#!/usr/bin/env python3
"""Test private staging and cleanup for the X.Org font builder."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("build-xorg-fonts.py")
SPEC = importlib.util.spec_from_file_location("xorg_font_builder", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the X.Org font builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class FontStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="arkpkg-font-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.victim = self.root / "unrelated"
        self.victim.mkdir()
        self.marker = self.victim / "keep"
        self.marker.write_text("keep me", encoding="ascii")
        self.legacy_staging = self.root / "arkpkg-font-staging"
        self.legacy_staging.symlink_to(self.victim, target_is_directory=True)
        original_resolve = Path.resolve

        def resolve(path: Path, *args, **kwargs) -> Path:
            # Keep regressions to the old hardcoded default inside this fixture.
            if path == Path("/tmp/arkpkg-font-staging"):
                path = self.legacy_staging
            return original_resolve(path, *args, **kwargs)

        self.enterContext(patch.object(Path, "resolve", resolve))

        name, version, _, directory, licenses = BUILDER.FONT_PACKAGES[0]
        self.font_name = name
        self.font_directory = directory
        self.font_data = b"STARTFONT 2.1\nFONT test-xlfd\nENDFONT\n"
        digest = self.write_archive(name, version, {
            "test.bdf": self.font_data,
            "COPYING": b"test font license\n",
        })
        self.enterContext(patch.object(BUILDER, "FONT_PACKAGES", (
            (name, version, digest, directory, licenses),
        )))
        alias_digest = self.write_archive(BUILDER.ALIAS_NAME, BUILDER.ALIAS_VERSION, {
            **{f"{item}/fonts.alias": b"test test-xlfd\n"
               for item in ("misc", "75dpi", "100dpi", "cyrillic")},
            "COPYING": b"test alias license\n",
        })
        self.enterContext(patch.object(BUILDER, "ALIAS_SHA256", alias_digest))
        self.enterContext(patch.object(tempfile, "tempdir", str(self.root)))

    def write_archive(self, name: str, version: str, files: dict[str, bytes]) -> str:
        path = self.cache / f"{name}-{version}.tar.xz"
        with tarfile.open(path, "w:xz") as archive:
            for filename, data in files.items():
                member = tarfile.TarInfo(f"{name}-{version}/{filename}")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def run_builder(self, *arguments: str) -> None:
        with patch.object(sys, "argv", [
            str(SCRIPT), "--arkpkg-build", "/unused/arkpkg-build",
            "--source-cache", str(self.cache), "--output-dir", str(self.root / "output"),
            *arguments,
        ]), redirect_stdout(io.StringIO()):
            BUILDER.main()

    def inspect_package(self, command: list[str], *, check: bool) -> Path:
        self.assertTrue(check)
        staging = Path(command[command.index("--staging") + 1])
        package = command[command.index("--packages") + 1]
        self.assertEqual(staging.name, package)
        self.assertEqual(Path(command[1]), SCRIPT.resolve().with_name("build-arkana-packages.py"))
        self.assertEqual(command[command.index("--output-dir") + 1], str(self.root / "output"))
        self.assertEqual(stat.S_IMODE(staging.parent.stat().st_mode), 0o700)
        self.assertTrue((staging / f"usr/share/licenses/{package}/COPYING").is_file())
        if package == BUILDER.ALIAS_NAME:
            self.assertEqual(len(list(staging.glob("usr/share/fonts/*/fonts.alias"))), 4)
        else:
            fonts = staging / f"usr/share/fonts/{self.font_directory}"
            self.assertEqual((fonts / "test.bdf").read_bytes(), self.font_data)
            self.assertEqual((fonts / "fonts.dir").read_text(), "1\ntest.bdf test-xlfd\n")
        return staging.parent

    def test_fonts_extra_sources_and_aliases_survive_cache_substitution(self) -> None:
        extra_digest = self.write_archive("extra", "1", {
            "extra.bdf": b"FONT extra-font\n", "COPYING": b"extra license",
        })
        obtain = BUILDER.obtain_source
        snapshots = []

        def obtain_then_mutate(*args):
            source = obtain(*args)
            (self.cache / source.name).write_bytes(b"attacker replacement")
            snapshots.append(source)
            return source

        with (
            patch.object(BUILDER, "FONT_EXTRA_SOURCES", {
                self.font_name: (("extra", "1", extra_digest),),
            }),
            patch.object(BUILDER, "obtain_source", side_effect=obtain_then_mutate),
            patch.object(BUILDER.subprocess, "run"),
        ):
            staging = self.root / "snapshot-staging"
            self.run_builder("--staging", str(staging))
        fonts = staging / self.font_name / f"usr/share/fonts/{self.font_directory}"
        self.assertEqual((fonts / "test.bdf").read_bytes(), self.font_data)
        self.assertEqual((fonts / "extra.bdf").read_bytes(), b"FONT extra-font\n")
        self.assertIn("extra.bdf extra-font\n", (fonts / "fonts.dir").read_text())
        aliases = staging / BUILDER.ALIAS_NAME / "usr/share/fonts"
        self.assertIn("test test-xlfd", (aliases / "misc/fonts.alias").read_text())
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(not source.exists() for source in snapshots))

    def test_default_staging_is_private_unique_and_cleaned(self) -> None:
        roots = []

        def inspect(command: list[str], *, check: bool) -> None:
            roots.append(self.inspect_package(command, check=check))

        for _ in range(2):
            with patch.object(BUILDER.subprocess, "run", side_effect=inspect) as run:
                self.run_builder()
            self.assertEqual(run.call_count, 2)
            self.assertEqual(roots[-2], roots[-1])
            self.assertEqual(roots[-1].parent, self.root)
            self.assertFalse(roots[-1].exists())
        self.assertNotEqual(roots[0], roots[2])
        self.assertTrue(self.legacy_staging.is_symlink())
        self.assertEqual(self.marker.read_text(), "keep me")

    def test_default_staging_is_cleaned_when_packaging_fails(self) -> None:
        roots = []

        def fail(command: list[str], *, check: bool) -> None:
            roots.append(self.inspect_package(command, check=check))
            raise subprocess.CalledProcessError(1, command)

        with (
            patch.object(BUILDER.subprocess, "run", side_effect=fail),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            self.run_builder()
        self.assertEqual(len(roots), 1)
        self.assertFalse(roots[0].exists())
        self.assertEqual(self.marker.read_text(), "keep me")

    def test_explicit_fresh_staging_is_retained(self) -> None:
        staging = self.root / "custom" / "staging"
        with patch.object(BUILDER.subprocess, "run", side_effect=self.inspect_package) as run:
            self.run_builder("--staging", str(staging))
        self.assertEqual(run.call_count, 2)
        self.assertTrue((staging / self.font_name).is_dir())
        self.assertTrue((staging / BUILDER.ALIAS_NAME).is_dir())

    def test_existing_staging_entries_are_rejected_without_modification(self) -> None:
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "missing", target_is_directory=True)
        for staging in (self.legacy_staging, dangling, self.victim, self.marker):
            with self.subTest(staging=staging):
                with (
                    patch.object(BUILDER, "obtain_source", side_effect=AssertionError(
                        "source accessed before rejecting staging"
                    )) as obtain,
                    self.assertRaises(FileExistsError),
                ):
                    self.run_builder("--staging", str(staging))
                obtain.assert_not_called()
                self.assertEqual(self.marker.read_text(), "keep me")
        self.assertTrue(self.legacy_staging.is_symlink())
        self.assertTrue(dangling.is_symlink())
        self.assertFalse((self.root / "missing").exists())

    def test_default_staging_is_cleaned_when_checksum_validation_fails(self) -> None:
        archive = next(self.cache.glob(f"{self.font_name}-*.tar.xz"))
        archive.write_bytes(b"invalid archive")
        with (
            patch.object(BUILDER.subprocess, "run") as run,
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.run_builder()
        run.assert_not_called()
        self.assertEqual(list(self.root.glob("arkpkg-font-staging-*")), [])
        self.assertEqual(self.marker.read_text(), "keep me")


if __name__ == "__main__":
    unittest.main()
