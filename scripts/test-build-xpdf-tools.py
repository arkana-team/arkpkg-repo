#!/usr/bin/env python3
"""Test Xpdf source authentication and private staging behavior."""

import hashlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

BUILDER_PATH = Path(__file__).with_name("build-xpdf-tools.py")
SPEC = importlib.util.spec_from_file_location("xpdf_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the Xpdf builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
TEST_TMP = Path.home() / ".cache"
TEST_TMP.mkdir(mode=0o700, parents=True, exist_ok=True)


class SourceAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.cached = self.cache / f"{BUILDER.SOURCE_ROOT}.tar.gz"
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)
        self.good = self.archive(b"# trusted CMake source\n")
        self.bad = self.archive(b"# substituted CMake source\n")
        self.cached.write_bytes(self.good)
        checksum = patch.object(
            BUILDER, "SOURCE_SHA256", hashlib.sha256(self.good).hexdigest()
        )
        checksum.start()
        self.addCleanup(checksum.stop)

    @staticmethod
    def archive(content: bytes, name: str | None = None) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            member = tarfile.TarInfo(name or f"{BUILDER.SOURCE_ROOT}/CMakeLists.txt")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        return output.getvalue()

    def test_verified_copy_survives_cache_changes(self) -> None:
        source = BUILDER.obtain_source(self.cache, self.private)
        self.assertEqual(source.parent, self.private)
        self.assertFalse(source.samefile(self.cached))
        self.cached.write_bytes(self.bad)
        self.assertEqual(source.read_bytes(), self.good)
        self.cached.unlink()
        self.cached.symlink_to(self.root / "missing")
        self.assertEqual(source.read_bytes(), self.good)

    def test_download_is_copied_and_verified(self) -> None:
        self.cached.unlink()
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.good)
        ) as download:
            source = BUILDER.obtain_source(self.cache, self.private)
        self.assertEqual(source.read_bytes(), self.good)
        self.assertEqual(self.cached.read_bytes(), self.good)
        self.assertEqual(download.call_args.args[0].full_url, BUILDER.SOURCE_URL)

    def test_invalid_cached_checksum_is_rejected(self) -> None:
        self.cached.write_bytes(self.bad)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            BUILDER.obtain_source(self.cache, self.private)

    def test_bytes_changed_after_open_are_rejected(self) -> None:
        fdopen = os.fdopen

        def open_changed_bytes(descriptor: int, mode: str):
            self.cached.write_bytes(self.bad)
            return fdopen(descriptor, mode)

        with (
            patch.object(BUILDER.os, "fdopen", side_effect=open_changed_bytes),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.obtain_source(self.cache, self.private)

    def test_symlinks_are_rejected_without_download_or_target_write(self) -> None:
        for target in (self.root / "target", self.root / "missing", Path("/dev/zero")):
            with self.subTest(target=target):
                if target.name == "target":
                    target.write_bytes(self.good)
                self.cached.unlink()
                self.cached.symlink_to(target)
                with (
                    patch.object(BUILDER.urllib.request, "urlopen") as download,
                    self.assertRaises(OSError),
                ):
                    BUILDER.obtain_source(self.cache, self.private)
                download.assert_not_called()
                self.assertFalse((self.private / self.cached.name).exists())
                if target.name == "target":
                    self.assertEqual(target.read_bytes(), self.good)
                elif target.name == "missing":
                    self.assertFalse(target.exists())

    def test_fifo_is_rejected_without_blocking(self) -> None:
        self.cached.unlink()
        os.mkfifo(self.cached)
        original_open = os.open

        def open_fifo(path, flags):
            self.assertTrue(flags & os.O_NONBLOCK)
            return original_open(path, flags)

        with (
            patch.object(BUILDER.os, "open", side_effect=open_fifo),
            patch.object(BUILDER.urllib.request, "urlopen") as download,
            self.assertRaisesRegex(ValueError, "not a regular file"),
        ):
            BUILDER.obtain_source(self.cache, self.private)
        download.assert_not_called()

    def test_opened_device_is_rejected(self) -> None:
        # Exercise descriptor validation even if the pathname looked regular.
        descriptor = os.open("/dev/zero", os.O_RDONLY)
        with (
            patch.object(BUILDER.os, "open", return_value=descriptor),
            self.assertRaisesRegex(ValueError, "not a regular file"),
        ):
            BUILDER.obtain_source(self.cache, self.private)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertFalse((self.private / self.cached.name).exists())

    def test_writable_cache_is_rejected(self) -> None:
        self.cache.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "writable by others"):
            BUILDER.obtain_source(self.cache, self.private)

    def test_oversized_cached_archive_is_rejected_before_copy(self) -> None:
        with (
            patch.object(BUILDER, "MAX_SOURCE_BYTES", len(self.good) - 1),
            self.assertRaisesRegex(ValueError, "source archive exceeds"),
        ):
            BUILDER.obtain_source(self.cache, self.private)
        self.assertFalse((self.private / self.cached.name).exists())

    def test_copy_limit_still_applies_if_cached_file_grows_after_stat(self) -> None:
        initial = list(self.cached.stat())
        initial[6] = 0
        with (
            patch.object(BUILDER.os, "fstat", return_value=os.stat_result(initial)),
            patch.object(BUILDER, "MAX_SOURCE_BYTES", len(self.good) - 1),
            self.assertRaisesRegex(ValueError, "source archive exceeds"),
        ):
            BUILDER.obtain_source(self.cache, self.private)
        self.assertLessEqual(
            (self.private / self.cached.name).stat().st_size, len(self.good) - 1
        )

    def test_archive_exactly_at_limit_is_accepted(self) -> None:
        with patch.object(BUILDER, "MAX_SOURCE_BYTES", len(self.good)):
            self.assertEqual(
                BUILDER.obtain_source(self.cache, self.private).read_bytes(), self.good
            )

    def test_oversized_download_is_bounded_and_not_published(self) -> None:
        self.cached.unlink()
        with (
            patch.object(BUILDER, "MAX_SOURCE_BYTES", 1024),
            patch.object(
                BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * 2048)
            ),
            self.assertRaisesRegex(ValueError, "source archive exceeds"),
        ):
            BUILDER.obtain_source(self.cache, self.private)
        self.assertFalse(self.cached.exists())
        self.assertLessEqual((self.private / self.cached.name).stat().st_size, 1024)

    def test_bad_download_is_not_published(self) -> None:
        self.cached.unlink()
        with (
            patch.object(
                BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.bad)
            ),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.obtain_source(self.cache, self.private)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_download_publication_replaces_entry_without_following_it(self) -> None:
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink):
                self.cached.unlink()
                target = self.root / "publication-target"
                target.write_bytes(b"preserve target")

                def download(*args, hardlink=hardlink, target=target, **kwargs):
                    if hardlink:
                        self.cached.hardlink_to(target)
                    else:
                        self.cached.symlink_to(target)
                    return io.BytesIO(self.good)

                with patch.object(BUILDER.urllib.request, "urlopen", side_effect=download):
                    source = BUILDER.obtain_source(self.cache, self.private)
                self.assertEqual(source.read_bytes(), self.good)
                self.assertEqual(self.cached.read_bytes(), self.good)
                self.assertFalse(self.cached.samefile(target))
                self.assertEqual(target.read_bytes(), b"preserve target")
                self.assertEqual(list(self.cache.iterdir()), [self.cached])

    def test_cache_replaced_after_hash_does_not_change_archive_validation(self) -> None:
        sha256 = hashlib.sha256

        def replace_after_hash(content: bytes):
            digest = sha256(content)
            replacement = self.cache / "replacement"
            replacement.write_bytes(self.bad)
            replacement.replace(self.cached)
            return digest

        with patch.object(BUILDER.hashlib, "sha256", side_effect=replace_after_hash):
            source = BUILDER.obtain_source(self.cache, self.private)
        self.assertEqual(source.read_bytes(), self.good)

    def test_archive_path_and_root_checks_are_preserved(self) -> None:
        for name in (
            "/absolute",
            "../escape",
            f"{BUILDER.SOURCE_ROOT}/../escape",
            "unexpected-root/CMakeLists.txt",
        ):
            with self.subTest(name=name):
                archive = self.archive(b"content", name)
                self.cached.write_bytes(archive)
                with (
                    patch.object(
                        BUILDER, "SOURCE_SHA256", hashlib.sha256(archive).hexdigest()
                    ),
                    self.assertRaisesRegex(
                        ValueError, "unsafe source path|unexpected source roots"
                    ),
                ):
                    BUILDER.obtain_source(self.cache, self.private)

    def test_main_installs_verified_copy_and_cleans_up(self) -> None:
        self.check_main_flow(fail_install=False)

    def test_main_cleans_up_after_install_failure(self) -> None:
        self.check_main_flow(fail_install=True)

    def check_main_flow(self, *, fail_install: bool) -> None:
        sysroot = self.root / "sysroot"
        binaries = sysroot / "usr/bin"
        binaries.mkdir(parents=True)
        for name in ("cmake", "gcc", "g++", "make", "tar", "strip"):
            (binaries / name).touch()
        staging = self.root / "staging"

        self.cached.write_bytes(self.good)
        installed_sources = []
        work_dirs = []

        def privileged(command: list[str], *, log: Path | None = None) -> None:
            if command[:2] == ["install", "-d"]:
                # Swap the cache at the last boundary before privileged copying.
                replacement = self.cache / "replacement"
                replacement.write_bytes(self.bad)
                replacement.replace(self.cached)
                work = Path(command[-1])
                work.mkdir(parents=True)
                work_dirs.append(work)
            elif command[:2] == ["install", "-m"]:
                source = Path(command[-2])
                installed_sources.append(source)
                self.assertFalse(source.is_relative_to(self.cache))
                self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o700)
                self.assertEqual(source.read_bytes(), self.good)
                if fail_install:
                    raise subprocess.CalledProcessError(1, command)
                shutil.copyfile(source, command[-1])
            elif command[0] == "chroot":
                work = work_dirs[-1]
                installed = work / self.cached.name
                self.assertEqual(installed.read_bytes(), self.good)
                for tool in BUILDER.TOOLS:
                    payload = work / "payload/usr/bin" / tool
                    payload.parent.mkdir(parents=True, exist_ok=True)
                    payload.write_bytes(b"fixture")
            elif command[0] == "/usr/bin/python3":
                shutil.rmtree(command[-1], ignore_errors=True)
            else:
                self.fail(f"unexpected privileged command: {command}")

        with (
            patch.object(
                sys,
                "argv",
                [
                    str(BUILDER_PATH),
                    "--sysroot",
                    str(sysroot),
                    "--arkpkg-build",
                    str(self.root / "arkpkg-build"),
                    "--source-cache",
                    str(self.cache),
                    "--output-staging",
                    str(staging),
                ],
            ),
            patch.object(BUILDER, "privileged", side_effect=privileged),
            patch.object(BUILDER.tempfile, "tempdir", str(TEST_TMP)),
            patch.object(BUILDER.subprocess, "run") as package_build,
        ):
            if fail_install:
                with self.assertRaises(subprocess.CalledProcessError):
                    BUILDER.main()
                package_build.assert_not_called()
            else:
                BUILDER.main()
                package_build.assert_called_once()
                for tool in BUILDER.TOOLS:
                    self.assertEqual(
                        (staging / "usr/bin" / tool).read_bytes(), b"fixture"
                    )
        self.assertEqual(len(installed_sources), 1)
        self.assertFalse(installed_sources[0].parent.exists())
        self.assertFalse(work_dirs[0].exists())


class XpdfStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="xpdf-staging-test-", dir=TEST_TMP
        )
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
