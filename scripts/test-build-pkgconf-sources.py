#!/usr/bin/env python3
"""Test source validation before the privileged pkgconf build."""

import argparse
import hashlib
import importlib.util
import io
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BUILDER_PATH = Path(__file__).with_name("build-pkgconf.py")
SPEC = importlib.util.spec_from_file_location("pkgconf_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the pkgconf builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def source_bytes(name: str | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        member = tarfile.TarInfo(name or f"pkgconf-{BUILDER.VERSION}/configure")
        content = b"#!/bin/sh\necho expected source\n"
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class SourceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir(mode=0o700)
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir(mode=0o700)
        self.archive = self.cache / f"pkgconf-{BUILDER.VERSION}.tar.xz"
        self.expected = source_bytes()
        self.archive.write_bytes(self.expected)
        digest = mock.patch.object(
            BUILDER, "SOURCE_SHA256", hashlib.sha256(self.expected).hexdigest()
        )
        digest.start()
        self.addCleanup(digest.stop)

    def test_privileged_install_uses_validated_snapshot_after_cache_changes(
        self,
    ) -> None:
        sysroot = self.root / "sysroot"
        binary_dir = sysroot / "usr/bin"
        binary_dir.mkdir(parents=True)
        for binary in ("gcc", "make", "tar", "strip", "libtool"):
            (binary_dir / binary).touch()
        args = argparse.Namespace(
            sysroot=sysroot,
            source_cache=self.cache,
            output_staging=self.root / "staging",
            arkpkg=None,
            build_dependency_archive=[],
            jobs=1,
        )
        for mutation in ("replace", "overwrite", "replace directory"):
            with self.subTest(mutation=mutation):
                self.archive.write_bytes(self.expected)
                installed_sources = []

                def privileged(
                    command: list[str],
                    *,
                    log: Path | None = None,
                    mutation: str = mutation,
                    installed_sources: list[Path] = installed_sources,
                ) -> None:
                    if command[:2] == ["install", "-d"]:
                        if mutation == "replace":
                            replacement = self.cache / "replacement"
                            replacement.write_bytes(b"substituted source")
                            replacement.replace(self.archive)
                        elif mutation == "overwrite":
                            self.archive.write_bytes(b"substituted source")
                        else:
                            self.cache.rename(self.root / "old-cache")
                            self.cache.mkdir(mode=0o700)
                            self.archive.write_bytes(b"substituted source")
                    elif command[:2] == ["install", "-m"]:
                        source = Path(command[3])
                        installed_sources.append(source)
                        self.assertNotEqual(source.parent, self.cache)
                        self.assertEqual(source.read_bytes(), self.expected)
                        self.assertEqual(
                            stat.S_IMODE(source.parent.stat().st_mode), 0o700
                        )
                    elif command[0] == "chroot":
                        raise RuntimeError("stop before privileged execution")

                with (
                    mock.patch.object(BUILDER, "privileged", side_effect=privileged),
                    self.assertRaisesRegex(
                        RuntimeError, "stop before privileged execution"
                    ),
                ):
                    BUILDER.build(args)
                self.assertEqual(len(installed_sources), 1)
                self.assertFalse(installed_sources[0].exists())

    def test_checksum_is_checked_after_snapshot_copy(self) -> None:
        copyfile = BUILDER.shutil.copyfile

        def replace_during_copy(source: Path, destination: Path) -> None:
            self.archive.write_bytes(b"substituted before snapshot")
            copyfile(source, destination)

        with (
            mock.patch.object(
                BUILDER.shutil, "copyfile", side_effect=replace_during_copy
            ),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.source_archive(self.cache, self.snapshot)

    def test_downloaded_source_is_snapshotted_and_validated(self) -> None:
        self.archive.unlink()
        self.cache.rmdir()
        with mock.patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.expected)
        ) as download:
            snapshot = BUILDER.source_archive(self.cache, self.snapshot)
        download.assert_called_once()
        self.assertEqual(snapshot.read_bytes(), self.expected)
        self.assertEqual(self.archive.read_bytes(), self.expected)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)

    def test_download_does_not_follow_dangling_cache_symlink(self) -> None:
        self.archive.unlink()
        target = self.root / "unrelated-file"
        self.archive.symlink_to(target)
        with (
            mock.patch.object(
                BUILDER.urllib.request,
                "urlopen",
                return_value=io.BytesIO(self.expected),
            ),
            self.assertRaises(FileExistsError),
        ):
            BUILDER.source_archive(self.cache, self.snapshot)
        self.assertFalse(target.exists())

    def test_rejects_cache_writable_by_others(self) -> None:
        for mode in (0o770, 0o707):
            with self.subTest(mode=mode):
                self.cache.chmod(mode)
                with self.assertRaisesRegex(ValueError, "not writable by others"):
                    BUILDER.source_archive(self.cache, self.snapshot)

    def test_rejects_cache_owned_by_another_user(self) -> None:
        with (
            mock.patch.object(BUILDER.os, "geteuid", return_value=os.geteuid() + 1),
            self.assertRaisesRegex(ValueError, "owned by the current user"),
        ):
            BUILDER.source_archive(self.cache, self.snapshot)

    def test_rejects_unsafe_members_even_with_matching_digest(self) -> None:
        for name in (
            "/absolute",
            "../outside",
            f"pkgconf-{BUILDER.VERSION}/../outside",
            "other/configure",
        ):
            with self.subTest(name=name):
                data = source_bytes(name)
                self.archive.write_bytes(data)
                with (
                    mock.patch.object(
                        BUILDER, "SOURCE_SHA256", hashlib.sha256(data).hexdigest()
                    ),
                    self.assertRaisesRegex(
                        ValueError, "unsafe source path|unexpected source roots"
                    ),
                ):
                    BUILDER.source_archive(self.cache, self.snapshot)

    def test_invalid_source_stops_before_privileged_operations(self) -> None:
        self.archive.write_bytes(b"invalid source")
        args = argparse.Namespace(source_cache=self.cache)
        with (
            mock.patch.object(BUILDER, "privileged") as privileged,
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.build(args)
        privileged.assert_not_called()


if __name__ == "__main__":
    unittest.main()
