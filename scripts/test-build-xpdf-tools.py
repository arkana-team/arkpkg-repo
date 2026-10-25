#!/usr/bin/env python3
"""Check Xpdf staging safety and the handoff to the package builder."""

import importlib.util
import stat
import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "xpdf_builder", Path(__file__).with_name("build-xpdf-tools.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the Xpdf builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class XpdfStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="xpdf-staging-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sysroot = self.root / "sysroot"
        (self.sysroot / "usr/bin").mkdir(parents=True)
        for binary in ("cmake", "gcc", "g++", "make", "tar", "strip"):
            (self.sysroot / "usr/bin" / binary).touch()
        self.tempdir = self.root / "tmp"
        self.tempdir.mkdir()

    def simulate_build(self, command: list[str], **kwargs: object) -> None:
        if command[0] == "chroot":
            work = next((self.sysroot / "tmp").glob("arkpkg-xpdf-*"))
            binaries = work / "payload/usr/bin"
            binaries.mkdir(parents=True)
            for tool in BUILDER.TOOLS:
                (binaries / tool).write_text("synthetic tool\n", encoding="utf-8")
        elif command[:2] == ["install", "-d"]:
            Path(command[-1]).mkdir(parents=True)
        elif command[0] == "/usr/bin/python3":
            BUILDER.shutil.rmtree(command[-1])

    def run_builder(self, staging: Path | None = None) -> Path:
        argv = [
            "build-xpdf-tools.py",
            "--sysroot",
            str(self.sysroot),
            "--arkpkg-build",
            str(self.root / "arkpkg-build"),
            "--output-dir",
            str(self.root / "packages"),
        ]
        if staging is not None:
            argv.extend(("--output-staging", str(staging)))
        with (
            patch.object(sys, "argv", argv),
            patch.object(tempfile, "tempdir", str(self.tempdir)),
            patch.object(
                BUILDER, "obtain_source", return_value=self.root / "source.tar.gz"
            ),
            patch.object(BUILDER, "privileged", side_effect=self.simulate_build),
            patch.object(BUILDER.subprocess, "run") as package,
        ):
            BUILDER.main()
        package.assert_called_once()
        command = package.call_args.args[0]
        self.assertEqual(
            command[:3],
            [
                sys.executable,
                str(Path(BUILDER.__file__).with_name("build-arkana-packages.py")),
                "--staging",
            ],
        )
        self.assertEqual(
            command[4:],
            [
                "--arkpkg-build",
                str(self.root / "arkpkg-build"),
                "--packages",
                "xpdf-tools",
                "--output-dir",
                str(self.root / "packages"),
            ],
        )
        output = Path(command[3])
        self.assertTrue(output.is_absolute())
        for tool in BUILDER.TOOLS:
            self.assertEqual(
                (output / "usr/bin" / tool).read_text(encoding="utf-8"),
                "synthetic tool\n",
            )
        return output

    def test_default_staging_is_private_unique_and_preserved(self) -> None:
        victim = self.root / "victim"
        victim.mkdir()
        marker = victim / "keep.txt"
        marker.write_text("keep\n", encoding="utf-8")
        legacy = self.tempdir / "arkpkg-xpdf-staging"
        legacy.symlink_to(victim, target_is_directory=True)

        first = self.run_builder()
        second = self.run_builder()

        self.assertNotEqual(first, second)
        for staging in (first, second):
            self.assertEqual(staging.parent, self.tempdir)
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
            self.assertTrue((staging / "usr/bin/pdfinfo").is_file())
        self.assertTrue(legacy.is_symlink())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")

    def test_existing_explicit_entries_are_never_overwritten(self) -> None:
        victim = self.root / "victim"
        victim.mkdir()
        marker = victim / "keep.txt"
        marker.write_text("keep\n", encoding="utf-8")
        regular_file = self.root / "file"
        regular_file.write_text("keep\n", encoding="utf-8")
        symlink = self.root / "symlink"
        symlink.symlink_to(victim, target_is_directory=True)
        dangling = self.root / "dangling"
        missing = self.root / "missing"
        dangling.symlink_to(missing, target_is_directory=True)

        for staging in (victim, regular_file, symlink, dangling):
            with self.subTest(staging=staging), self.assertRaises(FileExistsError):
                self.run_builder(staging)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
            self.assertEqual(regular_file.read_text(encoding="utf-8"), "keep\n")
            self.assertTrue(symlink.is_symlink())
            self.assertTrue(dangling.is_symlink())
            self.assertFalse(missing.exists())

    def test_new_explicit_staging_reaches_package_builder(self) -> None:
        staging = self.root / "new-parent/staging"
        self.assertEqual(self.run_builder(staging), staging)
        self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)

    def test_relative_staging_reaches_package_builder_as_absolute_path(self) -> None:
        with chdir(self.root):
            self.assertEqual(self.run_builder(Path("relative")), self.root / "relative")


if __name__ == "__main__":
    unittest.main()
