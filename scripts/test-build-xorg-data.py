#!/usr/bin/env python3
"""Test X.Org data downloads and staging across the source cache boundary."""

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

BUILDER_PATH = Path(__file__).with_name("build-xorg-data.py")
SPEC = importlib.util.spec_from_file_location("xorg_data_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the X.Org data builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class XorgDataSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arkpkg-xorg-data-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.snapshots = self.root / "snapshots"
        self.archive = self.cache / f"{BUILDER.NAME}-{BUILDER.VERSION}.tar.xz"
        output = io.BytesIO()
        self.files = {
            "Makefile.am": b"dist_bitmap_DATA = \\\n sample.xbm\n\n",
            "xbitmaps.pc.in": b"prefix=@prefix@\nVersion: @PACKAGE_VERSION@\n",
            "sample.xbm": b"#define sample_width 1\n",
            "COPYING": b"bitmap license",
        }
        with tarfile.open(fileobj=output, mode="w:xz") as archive:
            for name, data in self.files.items():
                member = tarfile.TarInfo(f"{BUILDER.NAME}-{BUILDER.VERSION}/{name}")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.payload = output.getvalue()
        digest = mock.patch.object(
            BUILDER, "SHA256", hashlib.sha256(self.payload).hexdigest()
        )
        digest.start()
        self.addCleanup(digest.stop)

    def obtain(self) -> Path:
        return BUILDER.obtain_source(self.cache, self.snapshots)

    def test_download_creates_private_cache_and_reuses_verified_source(self) -> None:
        with mock.patch(
            "source_cache.urllib.request.urlopen", return_value=io.BytesIO(self.payload)
        ) as download:
            snapshot = self.obtain()
        download.assert_called_once()
        request = download.call_args.args[0]
        self.assertEqual(request.full_url, BUILDER.URL)
        self.assertEqual(
            request.get_header("User-agent"), "arkpkg-repo-xorg-data-builder/1"
        )
        self.assertEqual(download.call_args.kwargs, {"timeout": 90})
        self.assertEqual(snapshot.read_bytes(), self.payload)
        self.assertEqual(self.archive.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        snapshot.unlink()
        with mock.patch("source_cache.urllib.request.urlopen") as download:
            self.assertEqual(self.obtain().read_bytes(), self.payload)
        download.assert_not_called()

    def test_bad_download_is_not_cached(self) -> None:
        with (
            mock.patch(
                "source_cache.urllib.request.urlopen", return_value=io.BytesIO(b"bad")
            ),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.obtain()
        self.assertFalse(self.archive.exists())
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_dangling_archive_symlink_does_not_create_target(self) -> None:
        self.cache.mkdir(mode=0o700)
        victim = self.root / "absent"
        self.archive.symlink_to(victim)
        with mock.patch(
            "source_cache.urllib.request.urlopen", return_value=io.BytesIO(self.payload)
        ):
            snapshot = self.obtain()
        self.assertFalse(victim.exists())
        self.assertFalse(self.archive.is_symlink())
        self.assertEqual(snapshot.read_bytes(), self.payload)
        self.assertEqual(self.archive.read_bytes(), self.payload)

    def test_archive_replaced_during_download_does_not_overwrite_target(self) -> None:
        victim = self.root / "unrelated"
        victim.write_bytes(b"keep this data")
        for link in ("symlink", "hardlink"):
            with self.subTest(link=link):

                def response(*args, link=link, **kwargs):
                    if link == "symlink":
                        self.archive.symlink_to(victim)
                    else:
                        os.link(victim, self.archive)
                    return io.BytesIO(self.payload)

                with mock.patch(
                    "source_cache.urllib.request.urlopen", side_effect=response
                ):
                    snapshot = self.obtain()
                self.assertEqual(victim.read_bytes(), b"keep this data")
                self.assertEqual(snapshot.read_bytes(), self.payload)
                self.assertEqual(self.archive.read_bytes(), self.payload)
                self.assertEqual(list(self.cache.iterdir()), [self.archive])
                self.archive.unlink()
                snapshot.unlink()

    def test_rejects_writable_cache_and_replaceable_parent(self) -> None:
        self.cache.mkdir(mode=0o700)
        for mode in (0o770, 0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with (
                    mock.patch("source_cache.urllib.request.urlopen") as download,
                    self.assertRaisesRegex(ValueError, "writable by others"),
                ):
                    self.obtain()
                download.assert_not_called()
        self.cache.chmod(0o700)
        self.root.chmod(0o777)
        try:
            with (
                mock.patch("source_cache.urllib.request.urlopen") as download,
                self.assertRaisesRegex(ValueError, "writable by others"),
            ):
                self.obtain()
            download.assert_not_called()
        finally:
            self.root.chmod(0o700)

    def test_rejects_foreign_cache_even_through_symlink(self) -> None:
        self.cache.mkdir(mode=0o700)
        original_lstat = Path.lstat

        def foreign_owner(path):
            info = original_lstat(path)
            if path == self.cache:
                fields = list(info)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return info

        alias = self.root / "cache-link"
        alias.symlink_to(self.cache, target_is_directory=True)
        for cache in (self.cache, alias):
            with (
                self.subTest(cache=cache),
                mock.patch.object(
                    Path, "lstat", autospec=True, side_effect=foreign_owner
                ),
                mock.patch("source_cache.urllib.request.urlopen") as download,
                self.assertRaisesRegex(ValueError, "unsafe source cache directory"),
            ):
                BUILDER.obtain_source(cache, self.snapshots)
            download.assert_not_called()

    def build_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            source_cache=self.cache,
            arkpkg_build=self.root / "arkpkg-build",
            output_dir=None,
            staging=None,
        )

    def test_build_stages_verified_snapshot_after_cache_replacement(self) -> None:
        self.cache.mkdir(mode=0o700)
        self.archive.write_bytes(self.payload)
        staging = self.root / "staging"
        staged = []
        original_stage = BUILDER.stage_package

        def stage(source, destination):
            self.assertNotEqual(source, self.archive)
            self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o700)
            self.archive.write_bytes(b"substituted cache")
            staged.append(source)
            return original_stage(source, destination)

        with (
            mock.patch.object(BUILDER, "stage_package", side_effect=stage),
            mock.patch.object(BUILDER.subprocess, "run") as package,
        ):
            BUILDER.build(self.build_args(), staging)
        package.assert_called_once()
        self.assertEqual(
            (staging / "usr/include/X11/bitmaps/sample.xbm").read_bytes(),
            self.files["sample.xbm"],
        )
        self.assertEqual(
            (staging / "usr/share/licenses/xbitmaps/COPYING").read_bytes(),
            self.files["COPYING"],
        )
        self.assertIn(
            f"Version: {BUILDER.VERSION}",
            (staging / "usr/lib/pkgconfig/xbitmaps.pc").read_text(),
        )
        self.assertFalse(staged[0].parent.exists())

    def test_corrupt_cache_stops_before_staging_and_packaging(self) -> None:
        self.cache.mkdir(mode=0o700)
        self.archive.write_bytes(b"bad")
        with (
            mock.patch.object(BUILDER, "stage_package") as stage,
            mock.patch.object(BUILDER.subprocess, "run") as package,
            mock.patch("source_cache.urllib.request.urlopen") as download,
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.build(self.build_args(), self.root / "staging")
        stage.assert_not_called()
        package.assert_not_called()
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
