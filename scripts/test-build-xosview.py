"""Check that XOSView staging never replaces a preexisting destination."""

import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "build_xosview", Path(__file__).resolve().with_name("build-xosview.py")
)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load build-xosview.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class OutputStagingTests(unittest.TestCase):
    def test_default_is_unique_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim"
            victim.mkdir()
            marker = victim / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            legacy = root / "arkpkg-xosview-staging"
            legacy.symlink_to(victim, target_is_directory=True)
            with mock.patch.object(tempfile, "tempdir", directory):
                first = builder.create_output_staging(None)
                second = builder.create_output_staging(None)
            self.assertNotEqual(first, second)
            for staging in (first, second):
                self.assertEqual(staging.parent, root)
                self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
                self.assertFalse(staging.is_symlink())
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_explicit_staging_creates_private_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "nested/staging"
            self.assertEqual(builder.create_output_staging(staging), staging)
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)

    def test_existing_directory_and_file_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            marker = staging / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            for destination in (staging, marker):
                with self.subTest(destination=destination):
                    with self.assertRaises(FileExistsError):
                        builder.create_output_staging(destination)
                    self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_symlinks_are_rejected_without_touching_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim"
            victim.mkdir()
            marker = victim / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            missing = root / "missing"
            for target in (victim, missing):
                with self.subTest(target=target):
                    staging = root / "staging"
                    staging.symlink_to(target, target_is_directory=True)
                    with self.assertRaises(FileExistsError):
                        builder.create_output_staging(staging)
                    self.assertTrue(staging.is_symlink())
                    self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
                    self.assertFalse(missing.exists())
                    staging.unlink()

    def test_main_selects_default_and_explicit_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sysroot = root / "sysroot"
            binaries = sysroot / "usr/bin"
            binaries.mkdir(parents=True)
            for binary in ("gcc", "g++", "make", "pkg-config", "tar", "strip"):
                (binaries / binary).touch()

            for explicit in (False, True):
                with self.subTest(explicit=explicit):
                    argv = [
                        "build-xosview.py",
                        "--sysroot",
                        str(sysroot),
                        "--arkpkg",
                        str(root / "arkpkg"),
                        "--arkpkg-build",
                        str(root / "arkpkg-build"),
                    ]
                    if explicit:
                        argv.extend(("--output-staging", str(root / "explicit")))
                    with (
                        mock.patch.object(sys, "argv", argv),
                        mock.patch.object(tempfile, "tempdir", directory),
                        mock.patch.object(
                            builder,
                            "source_archive",
                            return_value=root / "source.tar.gz",
                        ),
                        mock.patch.object(
                            builder,
                            "installed_packages",
                            side_effect=RuntimeError("stop after staging"),
                        ),
                        mock.patch.object(builder, "privileged") as privileged,
                        mock.patch.object(
                            builder,
                            "create_output_staging",
                            wraps=builder.create_output_staging,
                        ) as create,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "stop after staging"):
                            builder.main()
                        create.assert_called_once_with(
                            root / "explicit" if explicit else None
                        )
                        # Only the build-workspace cleanup runs after an early failure.
                        self.assertEqual(privileged.call_count, 1)


if __name__ == "__main__":
    unittest.main()
