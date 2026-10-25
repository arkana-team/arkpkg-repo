#!/usr/bin/env python3
"""Test font source cache safety and checksum verification."""

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

SCRIPT = Path(__file__).with_name("build-xorg-fonts.py")
SPEC = importlib.util.spec_from_file_location("xorg_fonts", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the X.Org font builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class FontSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.archive = self.cache / "fixture-1.tar.xz"
        self.payload = b"pinned source archive"
        self.digest = hashlib.sha256(self.payload).hexdigest()

    def obtain(self) -> Path:
        return BUILDER.obtain_source(
            self.cache, "fixture", "1", "https://example.invalid/source", self.digest
        )

    def test_download_and_reuse_verified_cache(self) -> None:
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)
        ) as download:
            self.assertEqual(self.obtain(), self.archive)
            download.assert_called_once()
        self.assertEqual(self.archive.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        with patch.object(BUILDER.urllib.request, "urlopen") as download:
            self.assertEqual(self.obtain(), self.archive)
            download.assert_not_called()
        self.assertEqual(list(self.cache.iterdir()), [self.archive])

    def test_archive_symlink_swap_does_not_overwrite_target(self) -> None:
        victim = self.root / "victim"
        victim.write_bytes(b"user data")

        def respond(*args, **kwargs):
            self.archive.symlink_to(victim)
            return io.BytesIO(self.payload)

        with patch.object(BUILDER.urllib.request, "urlopen", side_effect=respond):
            self.obtain()
        self.assertEqual(victim.read_bytes(), b"user data")
        self.assertFalse(self.archive.is_symlink())
        self.assertEqual(self.archive.read_bytes(), self.payload)

    def test_dangling_symlink_does_not_create_target(self) -> None:
        self.cache.mkdir(mode=0o700)
        victim = self.root / "missing"
        self.archive.symlink_to(victim)
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)
        ):
            self.obtain()
        self.assertFalse(victim.exists())
        self.assertFalse(self.archive.is_symlink())
        self.assertEqual(self.archive.read_bytes(), self.payload)

    def test_hardlink_swap_does_not_overwrite_target(self) -> None:
        victim = self.root / "victim"
        victim.write_bytes(b"user data")

        def respond(*args, **kwargs):
            os.link(victim, self.archive)
            return io.BytesIO(self.payload)

        with patch.object(BUILDER.urllib.request, "urlopen", side_effect=respond):
            self.obtain()
        self.assertEqual(victim.read_bytes(), b"user data")
        self.assertEqual(self.archive.read_bytes(), self.payload)

    def test_failed_checksum_is_not_published(self) -> None:
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(b"bad source")
        ), self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.obtain()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_corrupt_cached_source_is_rejected_without_download(self) -> None:
        self.cache.mkdir(mode=0o700)
        self.archive.write_bytes(b"bad source")
        with patch.object(BUILDER.urllib.request, "urlopen") as download:
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                self.obtain()
            download.assert_not_called()
        self.assertEqual(self.archive.read_bytes(), b"bad source")

    def test_interrupted_download_is_not_published(self) -> None:
        class InterruptedResponse(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError("interrupted download")
                return super().read(size)

        with patch.object(
            BUILDER.urllib.request,
            "urlopen",
            return_value=InterruptedResponse(b"partial source"),
        ), self.assertRaisesRegex(OSError, "interrupted download"):
            self.obtain()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_cache_writable_by_other_users_is_rejected(self) -> None:
        self.cache.mkdir()
        for mode in (0o770, 0o707, 0o777):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with patch.object(BUILDER.urllib.request, "urlopen") as download:
                    with self.assertRaisesRegex(ValueError, "source cache must be"):
                        self.obtain()
                    download.assert_not_called()
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_cache_owned_by_another_user_is_rejected(self) -> None:
        self.cache.mkdir(mode=0o700)
        with (
            patch.object(BUILDER.os, "geteuid", return_value=os.geteuid() + 1),
            patch.object(BUILDER.urllib.request, "urlopen") as download,
        ):
            with self.assertRaisesRegex(ValueError, "source cache must be"):
                self.obtain()
            download.assert_not_called()

    def test_cache_symlink_is_rejected_through_cli(self) -> None:
        target = self.root / "target"
        target.mkdir(mode=0o700)
        self.cache.symlink_to(target, target_is_directory=True)
        with patch.object(
            BUILDER.sys,
            "argv",
            [
                str(SCRIPT),
                "--source-cache", str(self.cache),
                "--staging", str(self.root / "staging"),
                "--arkpkg-build", str(self.root / "unused-builder"),
                "--packages", BUILDER.FONT_PACKAGES[0][0],
            ],
        ), patch.object(BUILDER.urllib.request, "urlopen") as download:
            with self.assertRaisesRegex(ValueError, "source cache must be"):
                BUILDER.main()
            download.assert_not_called()
        self.assertEqual(list(target.iterdir()), [])

    def test_writable_parent_is_rejected_even_for_private_cache(self) -> None:
        parent = self.root / "shared"
        parent.mkdir()
        parent.chmod(0o777)
        self.cache = parent / "cache"
        with patch.object(BUILDER.urllib.request, "urlopen") as download:
            with self.assertRaisesRegex(ValueError, "unsafe source cache parent"):
                self.obtain()
            download.assert_not_called()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_existing_readable_cache_remains_usable(self) -> None:
        self.cache.mkdir()
        self.cache.chmod(0o755)
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)
        ):
            self.obtain()
        self.assertEqual(self.archive.read_bytes(), self.payload)

    def test_verified_source_still_stages_fonts_and_license(self) -> None:
        buffer = io.BytesIO()
        font = b"STARTFONT 2.1\nFONT -fixture-font\nENDFONT\n"
        license_text = b"fixture license\n"
        with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
            for name, data in (("fixture.bdf", font), ("COPYING", license_text)):
                member = tarfile.TarInfo(f"fixture-1/{name}")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.payload = buffer.getvalue()
        self.digest = hashlib.sha256(self.payload).hexdigest()
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.payload)
        ):
            source = self.obtain()
        staging = self.root / "staging"
        count = BUILDER.stage_fonts(source, staging, "fixture", "1", "misc", ("COPYING",))
        self.assertEqual(count, 1)
        self.assertEqual((staging / "usr/share/fonts/misc/fixture.bdf").read_bytes(), font)
        self.assertEqual(
            (staging / "usr/share/licenses/fixture/COPYING").read_bytes(), license_text
        )
        self.assertEqual(
            (staging / "usr/share/fonts/misc/fonts.dir").read_text(),
            "1\nfixture.bdf -fixture-font\n",
        )


if __name__ == "__main__":
    unittest.main()
