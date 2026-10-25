#!/usr/bin/env python3
"""Check that XOSView builds consume only verified private source archives."""

import hashlib
import importlib.util
import io
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "xosview_builder", Path(__file__).with_name("build-xosview.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the XOSView builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def archive_bytes(name: str, content: bytes = b"upstream license\n") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class SourceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.cached = self.cache / f"{BUILDER.SOURCE_ROOT}.tar.gz"
        self.good = archive_bytes(f"{BUILDER.SOURCE_ROOT}/COPYING.GPL")
        self.cached.write_bytes(self.good)
        digest = patch.object(BUILDER, "SHA256", hashlib.sha256(self.good).hexdigest())
        digest.start()
        self.addCleanup(digest.stop)

    def test_cached_archive_is_an_independent_copy(self) -> None:
        source = BUILDER.source_archive(self.cache, self.private)
        self.assertEqual(source, self.private / self.cached.name)
        self.assertFalse(source.samefile(self.cached))
        self.cached.write_bytes(b"cache modified in place")
        self.assertEqual(source.read_bytes(), self.good)

    def test_downloaded_archive_is_copied_and_verified(self) -> None:
        self.cached.unlink()
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.good)
        ) as download:
            source = BUILDER.source_archive(self.cache, self.private)
        download.assert_called_once()
        self.assertEqual(self.cached.read_bytes(), self.good)
        self.assertEqual(source.read_bytes(), self.good)
        self.assertFalse(source.samefile(self.cached))

    def test_cache_changed_during_copy_fails_verification(self) -> None:
        copyfile = shutil.copyfile

        def replace_then_copy(source: Path, destination: Path) -> None:
            self.cached.unlink()
            self.cached.write_bytes(b"replacement before snapshot")
            copyfile(source, destination)

        with (
            patch.object(BUILDER.shutil, "copyfile", side_effect=replace_then_copy),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.source_archive(self.cache, self.private)

    def test_rejects_checksum_mismatch_before_privileged_operations(self) -> None:
        self.cached.write_bytes(b"untrusted archive")
        with (
            patch.object(BUILDER, "privileged") as privileged,
            patch.object(sys, "argv", self.arguments()),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.main()
        privileged.assert_not_called()

    def test_preserves_source_layout_checks(self) -> None:
        for name, error in (
            ("/absolute/path", "unsafe source path"),
            (f"{BUILDER.SOURCE_ROOT}/../escape", "unsafe source path"),
            ("unexpected-root/COPYING.GPL", "unexpected source roots"),
        ):
            with self.subTest(name=name):
                content = archive_bytes(name)
                self.cached.write_bytes(content)
                with (
                    patch.object(BUILDER, "SHA256", hashlib.sha256(content).hexdigest()),
                    self.assertRaisesRegex(ValueError, error),
                ):
                    BUILDER.source_archive(self.cache, self.private)

    def arguments(self) -> list[str]:
        return [
            "build-xosview.py",
            "--sysroot", str(self.root / "sysroot"),
            "--arkpkg", str(self.root / "arkpkg"),
            "--arkpkg-build", str(self.root / "arkpkg-build"),
            "--source-cache", str(self.cache),
            "--output-staging", str(self.root / "staging"),
        ]

    def run_build_with_cache_replacement(self, *, fail_install: bool) -> None:
        sysroot = self.root / "sysroot"
        (sysroot / "usr/bin").mkdir(parents=True)
        for binary in ("gcc", "g++", "make", "pkg-config", "tar", "strip"):
            (sysroot / "usr/bin" / binary).touch()
        replacement = self.root / "replacement.tar.gz"
        replacement.write_bytes(
            archive_bytes(f"{BUILDER.SOURCE_ROOT}/COPYING.GPL", b"attacker license")
        )
        sources: list[Path] = []
        destinations: list[Path] = []

        def replace_cache(*args: object) -> set[str]:
            self.cached.unlink()
            self.cached.symlink_to(replacement)
            return set()

        def privileged(command: list[str], **kwargs: object) -> None:
            if command[:2] == ["install", "-d"]:
                Path(command[-1]).mkdir(parents=True)
            elif command[:3] == ["install", "-m", "644"]:
                source, destination = map(Path, command[3:])
                sources.append(source)
                destinations.append(destination)
                self.assertEqual(source.read_bytes(), self.good)
                self.assertFalse(source.samefile(self.cached))
                self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o700)
                if fail_install:
                    raise OSError("simulated install failure")
                shutil.copyfile(source, destination)
            elif command[0] == "chroot":
                self.assertEqual(destinations[0].read_bytes(), self.good)
                binary = destinations[0].parent / "payload/usr/bin/xosview2"
                binary.parent.mkdir(parents=True)
                binary.touch()
            elif command[0] == "/usr/bin/python3":
                if "copytree" in command[2]:
                    shutil.copytree(command[3], command[4], dirs_exist_ok=True)
                else:
                    shutil.rmtree(command[3], ignore_errors=True)

        def package(*args: object, **kwargs: object) -> None:
            license_path = self.root / "staging/usr/share/licenses/xosview/COPYING.GPL"
            self.assertEqual(license_path.read_bytes(), b"upstream license\n")
            self.assertTrue(sources[0].is_file())

        with (
            patch.object(sys, "argv", self.arguments()),
            patch.object(BUILDER, "installed_packages", side_effect=replace_cache),
            patch.object(BUILDER, "privileged", side_effect=privileged),
            patch.object(BUILDER.subprocess, "run", side_effect=package) as packaging,
        ):
            if fail_install:
                with self.assertRaisesRegex(OSError, "simulated install failure"):
                    BUILDER.main()
                packaging.assert_not_called()
            else:
                BUILDER.main()
                packaging.assert_called_once()
        self.assertEqual(len(sources), 1)
        self.assertFalse(sources[0].parent.exists())
        self.assertTrue(self.cached.is_symlink())

    def test_build_and_license_use_snapshot_after_cache_replacement(self) -> None:
        self.run_build_with_cache_replacement(fail_install=False)

    def test_private_snapshot_is_removed_after_build_failure(self) -> None:
        self.run_build_with_cache_replacement(fail_install=True)


if __name__ == "__main__":
    unittest.main()
