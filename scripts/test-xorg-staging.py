#!/usr/bin/env python3
"""Exercise X.Org staging without touching preexisting files or symlink targets."""

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


class XorgStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.builders = []
        for script in ("build-xorg-data.py", "build-xorg-fonts.py"):
            spec = importlib.util.spec_from_file_location(
                script, Path(__file__).with_name(script)
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(f"could not load {script}")
            builder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(builder)
            self.builders.append(builder)
        self.sources = {}
        for name, version, files in (
            (
                "xbitmaps", "1.1.4",
                {
                    "Makefile.am": b"dist_bitmap_DATA = \\\n sample.xbm\n\n",
                    "xbitmaps.pc.in": b"prefix=@prefix@\nVersion: @PACKAGE_VERSION@\n",
                    "sample.xbm": b"#define sample_width 1\n",
                    "COPYING": b"bitmap license",
                },
            ),
            (
                "font-misc-misc", "1.1.3",
                {"sample.bdf": b"FONT fixed\n", "COPYING": b"font license"},
            ),
            (
                "font-alias", "1.0.6",
                {
                    **{
                        f"{directory}/fonts.alias": b"sample fixed\n"
                        for directory in ("misc", "75dpi", "100dpi", "cyrillic")
                    },
                    "COPYING": b"alias license",
                },
            ),
        ):
            source = self.root / f"{name}.tar.xz"
            with tarfile.open(source, "w:xz") as archive:
                for filename, data in files.items():
                    member = tarfile.TarInfo(f"{name}-{version}/{filename}")
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            self.sources[name] = source

    def invoke(self, builder, staging=None, *, build_error=None):
        argv = [builder.__file__, "--arkpkg-build", str(self.root / "arkpkg-build")]
        is_font = hasattr(builder, "FONT_PACKAGES")
        if is_font:
            argv.extend(("--packages", "font-misc-misc", "font-alias"))
        if staging is not None:
            argv.extend(("--staging", str(staging)))

        def source(cache, name="xbitmaps", *args):
            return self.sources[name]

        observed = []

        def run(command, *, check):
            payload = Path(command[command.index("--staging") + 1])
            self.assertTrue((payload / "usr").is_dir())
            self.assertTrue(check)
            root = payload.parent if is_font else payload
            observed.append(
                (root, stat.S_IMODE(root.stat().st_mode))
            )
            if build_error is not None:
                raise build_error

        with (
            patch.object(sys, "argv", argv),
            patch.object(tempfile, "tempdir", str(self.root)),
            patch.object(builder, "obtain_source", side_effect=source),
            patch.object(builder.subprocess, "run", side_effect=run),
            redirect_stdout(io.StringIO()),
        ):
            builder.main()
        self.assertEqual(len(observed), 2 if is_font else 1)
        self.assertEqual(len({path for path, _ in observed}), 1)
        self.assertEqual(len({mode for _, mode in observed}), 1)
        return observed[0]

    def test_default_staging_is_private_and_unique(self) -> None:
        victim = self.root / "victim"
        victim.mkdir()
        sentinel = victim / "keep"
        sentinel.write_bytes(b"unrelated data")
        for name in ("arkpkg-xorg-data-staging", "arkpkg-font-staging"):
            (self.root / name).symlink_to(victim, target_is_directory=True)
        for builder in self.builders:
            with self.subTest(builder=builder.__file__):
                first, first_mode = self.invoke(builder)
                second, second_mode = self.invoke(builder)
                self.assertNotEqual(first, second)
                for staging, mode in ((first, first_mode), (second, second_mode)):
                    self.assertFalse(staging.is_symlink())
                    self.assertEqual(mode, 0o700)
                    self.assertFalse(staging.exists())
                self.assertEqual(sentinel.read_bytes(), b"unrelated data")

    def test_new_explicit_staging_builds_packages(self) -> None:
        for index, builder in enumerate(self.builders):
            with self.subTest(builder=builder.__file__):
                staging = self.root / str(index) / "new-staging"
                actual, mode = self.invoke(builder, staging)
                self.assertEqual(actual, staging)
                self.assertEqual(mode, 0o700)
                self.assertTrue(staging.exists())
                self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)

    def test_existing_destinations_are_preserved(self) -> None:
        directory = self.root / "existing"
        directory.mkdir()
        sentinel = directory / "keep"
        sentinel.write_bytes(b"unrelated data")
        existing_file = self.root / "file"
        existing_file.write_bytes(b"keep file")
        link = self.root / "link"
        link.symlink_to(directory, target_is_directory=True)
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "absent", target_is_directory=True)
        for builder in self.builders:
            for staging in (directory, existing_file, link, dangling):
                with (
                    self.subTest(builder=builder.__file__, staging=staging),
                    self.assertRaises(FileExistsError),
                ):
                    self.invoke(builder, staging)
                self.assertEqual(sentinel.read_bytes(), b"unrelated data")
                self.assertEqual(existing_file.read_bytes(), b"keep file")
                self.assertTrue(link.is_symlink())
                self.assertTrue(dangling.is_symlink())
                self.assertFalse((self.root / "absent").exists())

    def test_failed_build_preserves_staged_payload(self) -> None:
        for index, builder in enumerate(self.builders):
            staging = self.root / f"failed-{index}"
            with (
                self.subTest(builder=builder.__file__),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                self.invoke(
                    builder, staging,
                    build_error=subprocess.CalledProcessError(1, "arkpkg-build"),
                )
            self.assertTrue(list(staging.rglob("COPYING")))


if __name__ == "__main__":
    unittest.main()
