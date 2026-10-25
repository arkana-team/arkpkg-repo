#!/usr/bin/env python3
"""Check source authentication at the privileged Xpdf build boundary."""

import hashlib
import importlib.util
import io
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BUILDER_PATH = Path(__file__).with_name("build-xpdf-tools.py")
SPEC = importlib.util.spec_from_file_location("xpdf_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the Xpdf builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class SourceAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
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

    def test_bytes_changed_during_copy_are_rejected(self) -> None:
        def copy_changed_bytes(cached: Path, destination: Path) -> None:
            self.cached.write_bytes(self.bad)
            destination.write_bytes(cached.read_bytes())

        with (
            patch.object(BUILDER.shutil, "copyfile", side_effect=copy_changed_bytes),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.obtain_source(self.cache, self.private)

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
            patch.object(BUILDER.tempfile, "tempdir", str(self.cache)),
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


if __name__ == "__main__":
    unittest.main()
