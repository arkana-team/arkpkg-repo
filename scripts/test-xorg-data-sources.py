#!/usr/bin/env python3
"""Exercise verified X.Org data downloads and cache substitution attacks."""

import argparse
import hashlib
import importlib.util
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("build-xorg-data.py")
SPEC = importlib.util.spec_from_file_location("xorg_data", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the X.Org data builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class DataSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.archive = self.cache / f"{BUILDER.NAME}-{BUILDER.VERSION}.tar.xz"
        self.payload = b"pinned source archive"
        self.enterContext(
            patch.object(BUILDER, "SHA256", hashlib.sha256(self.payload).hexdigest())
        )

    def obtain(self) -> bytes:
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            source = BUILDER.obtain_source(self.cache, Path(temporary))
            self.assertEqual(source.parent, Path(temporary))
            return source.read_bytes()

    def test_download_and_reuse_verified_cache(self) -> None:
        with patch(
            "source_cache.urllib.request.urlopen", return_value=io.BytesIO(self.payload)
        ) as download:
            self.assertEqual(self.obtain(), self.payload)
        download.assert_called_once()
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        self.assertEqual(self.archive.read_bytes(), self.payload)
        with patch("source_cache.urllib.request.urlopen") as download:
            self.assertEqual(self.obtain(), self.payload)
        download.assert_not_called()

    def test_download_never_overwrites_link_target(self) -> None:
        for kind in ("symlink", "hardlink", "dangling"):
            with self.subTest(kind=kind):
                self.cache = self.root / kind
                self.cache.mkdir(mode=0o700)
                self.archive = self.cache / self.archive.name
                victim = self.root / f"victim-{kind}"
                if kind != "dangling":
                    victim.write_bytes(b"unrelated user data")

                def respond(*args, kind=kind, victim=victim, **kwargs):
                    if kind == "hardlink":
                        os.link(victim, self.archive)
                    elif kind == "symlink":
                        self.archive.symlink_to(victim)
                    return io.BytesIO(self.payload)

                if kind == "dangling":
                    self.archive.symlink_to(victim)
                with patch("source_cache.urllib.request.urlopen", side_effect=respond):
                    self.assertEqual(self.obtain(), self.payload)
                if kind == "dangling":
                    self.assertFalse(victim.exists())
                else:
                    self.assertEqual(victim.read_bytes(), b"unrelated user data")
                self.assertFalse(self.archive.is_symlink())
                self.assertEqual(list(self.cache.iterdir()), [self.archive])
                self.assertEqual(self.archive.read_bytes(), self.payload)

    def test_failed_download_is_not_published(self) -> None:
        class InterruptedResponse(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError("interrupted download")
                return super().read(size)

        for response, error, message in (
            (io.BytesIO(b"bad source"), ValueError, "SHA-256 mismatch"),
            (InterruptedResponse(b"partial source"), OSError, "interrupted download"),
        ):
            with (
                self.subTest(message=message),
                patch("source_cache.urllib.request.urlopen", return_value=response),
                self.assertRaisesRegex(error, message),
            ):
                self.obtain()
            self.assertEqual(list(self.cache.iterdir()), [])

    def test_corrupt_cache_is_rejected_without_download(self) -> None:
        self.cache.mkdir(mode=0o700)
        self.archive.write_bytes(b"bad source")
        with (
            patch("source_cache.urllib.request.urlopen") as download,
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.obtain()
        download.assert_not_called()
        self.assertEqual(self.archive.read_bytes(), b"bad source")

    def test_unsafe_cache_is_rejected_before_network(self) -> None:
        self.cache.mkdir(mode=0o700)
        original_lstat = Path.lstat

        def foreign_owner(path):
            info = original_lstat(path)
            if path == self.cache:
                fields = list(info)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return info

        for mode in (0o770, 0o777, 0o1777):
            self.cache.chmod(mode)
            with (
                self.subTest(mode=mode),
                patch("source_cache.urllib.request.urlopen") as download,
            ):
                with self.assertRaisesRegex(ValueError, "writable by others"):
                    self.obtain()
                download.assert_not_called()
        self.cache.chmod(0o700)
        with (
            patch.object(Path, "lstat", autospec=True, side_effect=foreign_owner),
            patch("source_cache.urllib.request.urlopen") as download,
        ):
            with self.assertRaisesRegex(ValueError, "unsafe source cache directory"):
                self.obtain()
            download.assert_not_called()

    def test_build_consumes_private_snapshot_and_cleans_it_on_failure(self) -> None:
        self.cache.mkdir(mode=0o700)
        args = argparse.Namespace(
            source_cache=self.cache,
            arkpkg_build=self.root / "arkpkg-build",
            output_dir=None,
            staging=None,
        )
        for fail in (False, True):
            self.archive.write_bytes(self.payload)
            observed = []

            def stage(source, staging, *, observed=observed, fail=fail):
                observed.append(source)
                self.assertNotEqual(source, self.archive)
                self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o700)
                self.archive.write_bytes(b"cache replaced after verification")
                self.assertEqual(source.read_bytes(), self.payload)
                if fail:
                    raise OSError("staging failed")
                return 1

            with (
                self.subTest(fail=fail),
                patch.object(BUILDER, "stage_package", side_effect=stage),
                patch.object(BUILDER.subprocess, "run") as package,
                redirect_stdout(io.StringIO()),
            ):
                if fail:
                    with self.assertRaisesRegex(OSError, "staging failed"):
                        BUILDER.build(args, self.root / "staging")
                    package.assert_not_called()
                else:
                    BUILDER.build(args, self.root / "staging")
                    package.assert_called_once()
            self.assertEqual(len(observed), 1)
            self.assertFalse(observed[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
