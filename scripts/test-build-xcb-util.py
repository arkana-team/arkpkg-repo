#!/usr/bin/env python3
"""Check source cache isolation, download publication, and archive validation."""

import hashlib
import importlib.util
import io
import os
import stat
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


def source_bytes(member_name: str) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        member = tarfile.TarInfo(member_name)
        member.size = len(b"fixture license\n")
        archive.addfile(member, io.BytesIO(b"fixture license\n"))
    return output.getvalue()


class SourceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / "cache"
        self.archive = self.cache / f"{BUILDER.NAME}-{BUILDER.VERSION}.tar.xz"
        self.payload = source_bytes(f"{BUILDER.NAME}-{BUILDER.VERSION}/COPYING")
        checksum = patch.object(
            BUILDER, "SHA256", hashlib.sha256(self.payload).hexdigest()
        )
        checksum.start()
        self.addCleanup(checksum.stop)
        http = patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)
        )
        self.http = http.start()
        self.addCleanup(http.stop)

    def test_download_is_verified_before_publication(self) -> None:
        verify = BUILDER.verify_source_archive

        def check_unpublished(path: Path) -> None:
            self.assertFalse(self.archive.exists())
            self.assertEqual(path.read_bytes(), self.payload)
            verify(path)

        with patch.object(BUILDER, "verify_source_archive", side_effect=check_unpublished):
            self.assertEqual(BUILDER.source_archive(self.cache), self.archive)
        self.assertEqual(self.archive.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        self.assertEqual(list(self.cache.iterdir()), [self.archive])

    def test_valid_cache_is_reused_without_network(self) -> None:
        self.cache.mkdir(mode=0o700)
        self.archive.write_bytes(self.payload)
        self.assertEqual(BUILDER.source_archive(self.cache), self.archive)
        self.http.assert_not_called()

    def test_rejects_nonprivate_cache(self) -> None:
        self.cache.mkdir()
        for mode in (0o755, 0o770, 0o777):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with self.assertRaises(ValueError):
                    BUILDER.source_archive(self.cache)
        self.http.assert_not_called()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_rejects_foreign_cache_owner(self) -> None:
        self.cache.mkdir(mode=0o700)
        original_lstat = Path.lstat

        def foreign_owner(path: Path):
            metadata = original_lstat(path)
            if path == self.cache:
                values = list(metadata)
                values[stat.ST_UID] = os.geteuid() + 1
                return os.stat_result(values)
            return metadata

        with patch.object(Path, "lstat", foreign_owner), self.assertRaises(ValueError):
            BUILDER.source_archive(self.cache)
        self.http.assert_not_called()

    def test_rejects_writable_parent_even_with_private_cache(self) -> None:
        shared = self.root / "shared"
        shared.mkdir(mode=0o777)
        shared.chmod(0o777)
        cache = shared / "cache"
        cache.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            BUILDER.source_archive(cache)
        self.http.assert_not_called()

    def test_rejects_cache_directory_symlink(self) -> None:
        target = self.root / "target"
        target.mkdir(mode=0o700)
        self.cache.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            BUILDER.source_archive(self.cache)
        self.http.assert_not_called()
        self.assertEqual(list(target.iterdir()), [])

    def test_rejects_existing_archive_symlinks(self) -> None:
        self.cache.mkdir(mode=0o700)
        target = self.root / "target"
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    target.write_bytes(self.payload)
                self.archive.symlink_to(target)
                with self.assertRaises(ValueError):
                    BUILDER.source_archive(self.cache)
                self.assertEqual(target.exists(), present)
                if present:
                    self.assertEqual(target.read_bytes(), self.payload)
                self.archive.unlink()
        self.http.assert_not_called()

    def test_substituted_archive_link_does_not_overwrite_target(self) -> None:
        self.cache.mkdir(mode=0o700)
        target = self.root / "target"
        target.write_bytes(b"original contents")
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink):
                def substitute_link(*args, hardlink=hardlink, **kwargs):
                    if hardlink:
                        self.archive.hardlink_to(target)
                    else:
                        self.archive.symlink_to(target)
                    return io.BytesIO(self.payload)

                self.http.side_effect = substitute_link
                self.assertEqual(BUILDER.source_archive(self.cache), self.archive)
                self.assertEqual(target.read_bytes(), b"original contents")
                self.assertFalse(self.archive.is_symlink())
                self.assertEqual(self.archive.read_bytes(), self.payload)
                self.archive.unlink()

    def test_bad_checksum_is_not_published(self) -> None:
        self.http.return_value = io.BytesIO(b"incorrect download")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            BUILDER.source_archive(self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_unsafe_archive_is_not_published(self) -> None:
        for name in ("/absolute", "../escape", "unexpected/COPYING"):
            with self.subTest(name=name):
                payload = source_bytes(name)
                self.http.return_value = io.BytesIO(payload)
                with (
                    patch.object(BUILDER, "SHA256", hashlib.sha256(payload).hexdigest()),
                    self.assertRaises(ValueError),
                ):
                    BUILDER.source_archive(self.cache)
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_interrupted_download_is_cleaned_up(self) -> None:
        with (
            patch.object(
                self.http.return_value, "read",
                side_effect=[b"partial", OSError("interrupted")],
            ),
            self.assertRaisesRegex(OSError, "interrupted"),
        ):
            BUILDER.source_archive(self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_corrupt_cache_is_rejected_without_rewriting(self) -> None:
        self.cache.mkdir(mode=0o700)
        self.archive.write_bytes(b"corrupt cache")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            BUILDER.source_archive(self.cache)
        self.http.assert_not_called()
        self.assertEqual(self.archive.read_bytes(), b"corrupt cache")


if __name__ == "__main__":
    unittest.main()
