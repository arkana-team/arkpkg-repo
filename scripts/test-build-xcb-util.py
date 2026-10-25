#!/usr/bin/env python3
"""Check that XCB builds never reuse an untrusted staging destination."""

import importlib.util
import io
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BUILDER_PATH = Path(__file__).with_name("build-xcb-util.py")
SPEC = importlib.util.spec_from_file_location("xcb_util_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the XCB utility builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class StagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sysroot = self.root / "sysroot"
        binaries = self.sysroot / "usr/bin"
        binaries.mkdir(parents=True)
        for binary in ("gcc", "make", "pkg-config", "tar", "strip"):
            (binaries / binary).touch()
        self.source = self.root / "source.tar.xz"
        with tarfile.open(self.source, "w:xz") as archive:
            license_file = tarfile.TarInfo(f"{BUILDER.NAME}-{BUILDER.VERSION}/COPYING")
            license_file.size = len(b"license\n")
            archive.addfile(license_file, io.BytesIO(b"license\n"))
        self.argv = [
            str(BUILDER_PATH),
            "--sysroot", str(self.sysroot),
            "--arkpkg", str(self.root / "arkpkg"),
            "--arkpkg-build", str(self.root / "arkpkg-build"),
            "--xorg-util-macros-archive", str(self.root / "macros.ark"),
        ]
        self.enterContext(patch.object(BUILDER, "source_archive", return_value=self.source))
        self.enterContext(patch.object(BUILDER, "package_name", return_value="macros"))
        # Also check that rejected staging paths do not trigger dependency installation.
        self.enterContext(patch.object(BUILDER, "installed_packages", return_value=set()))
        self.privileged = self.enterContext(patch.object(BUILDER, "privileged"))
        self.run = self.enterContext(patch.object(BUILDER.subprocess, "run"))
        self.enterContext(patch.object(tempfile, "tempdir", str(self.root)))

    def build(self, staging: Path | None = None) -> Path:
        self.privileged.reset_mock()
        self.run.reset_mock()
        argv = self.argv + (["--output-staging", str(staging)] if staging else [])
        with patch.object(sys, "argv", argv):
            BUILDER.main()
        command = self.run.call_args.args[0]
        result = Path(command[command.index("--staging") + 1])
        self.assertEqual(
            (result / "usr/share/licenses/xcb-util/COPYING").read_bytes(), b"license\n"
        )
        copy = next(
            call.args[0] for call in self.privileged.call_args_list
            if call.args[0][0] == "/usr/bin/python3" and "copytree" in call.args[0][2]
        )
        self.assertEqual(Path(copy[-1]), result)
        return result

    def test_existing_destinations_are_untouched(self) -> None:
        for kind in ("symlink", "dangling symlink", "directory", "file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                victim = root / "victim"
                victim.mkdir()
                sentinel = victim / "keep"
                sentinel.write_text("keep", encoding="utf-8")
                staging = root / "staging"
                if kind == "directory":
                    staging.mkdir()
                elif kind == "file":
                    staging.write_text("keep", encoding="utf-8")
                else:
                    target = victim if kind == "symlink" else root / "missing"
                    staging.symlink_to(target, target_is_directory=True)
                with (
                    patch.object(sys, "argv", self.argv + ["--output-staging", str(staging)]),
                    self.assertRaises(FileExistsError),
                ):
                    BUILDER.main()
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
                self.assertTrue(staging.exists() or staging.is_symlink())
                self.privileged.assert_not_called()

    def test_default_staging_is_unique_and_private(self) -> None:
        first = self.build()
        second = self.build()
        self.assertNotEqual(first, second)
        for staging in (first, second):
            self.assertTrue(staging.is_dir())
            self.assertFalse(staging.is_symlink())
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)

    def test_explicit_new_staging_reaches_packager(self) -> None:
        staging = self.root / "new-parent/staging"
        self.assertEqual(self.build(staging), staging)
        self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
