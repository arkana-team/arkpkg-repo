#!/usr/bin/env python3
"""Check that Firefox payloads use only a verified private archive."""

import hashlib
import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "arkana_package_builder", Path(__file__).with_name("build-arkana-packages.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the arkanaOS package builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def firefox_archive(icon: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        member = tarfile.TarInfo(BUILDER.FIREFOX_ICON)
        member.size = len(icon)
        archive.addfile(member, io.BytesIO(icon))
    return output.getvalue()


class FirefoxArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / "private"
        self.work.mkdir(mode=0o700)
        self.source = self.root / "firefox.tar.xz"
        self.good = firefox_archive(b"verified icon")
        self.bad = firefox_archive(b"substituted icon")
        for name, value in (
            ("FIREFOX_ARCHIVE_BYTES", len(self.good)),
            ("FIREFOX_SHA512", hashlib.sha512(self.good).hexdigest()),
        ):
            patch = mock.patch.object(BUILDER, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def assert_payload(self, archive_path: Path, package_root: Path) -> None:
        staging = self.root / "staging"
        integration = staging / "usr/share/NsCDE/integration/firefox"
        integration.parent.mkdir(parents=True, exist_ok=True)
        integration.write_bytes(b"integration")
        BUILDER.add_firefox_payload(package_root, staging, archive_path)
        bundle = package_root / f"usr/lib/arkpkg/firefox-{BUILDER.FIREFOX_VERSION}.tar.xz"
        self.assertEqual(bundle.read_bytes(), self.good)
        icon = package_root / "usr/share/icons/hicolor/128x128/apps/firefox.png"
        self.assertEqual(icon.read_bytes(), b"verified icon")

    def test_source_changes_after_verification_do_not_change_payload(self) -> None:
        original_hash = BUILDER.sha512_file
        for change in ("overwrite", "replace", "retarget"):
            with self.subTest(change=change):
                self.source.unlink(missing_ok=True)
                self.source.write_bytes(self.good)
                if change == "retarget":
                    target = self.root / "target.tar.xz"
                    self.source.rename(target)
                    self.source.symlink_to(target)

                def hash_then_change(path: Path, change: str = change) -> str:
                    digest = original_hash(path)
                    if change == "overwrite":
                        self.source.write_bytes(self.bad)
                    else:
                        replacement = self.root / "replacement.tar.xz"
                        replacement.write_bytes(self.bad)
                        if change == "retarget":
                            self.source.unlink()
                            self.source.symlink_to(replacement)
                        else:
                            replacement.replace(self.source)
                    return digest

                with mock.patch.object(
                    BUILDER, "sha512_file", side_effect=hash_then_change
                ):
                    snapshot = BUILDER.obtain_firefox_archive(self.source, self.work)

                self.assert_payload(snapshot, self.root / change)
                self.assertEqual(snapshot.parent, self.work)
                self.assertFalse(snapshot.is_symlink())
                self.assertNotEqual(snapshot.stat().st_ino, self.source.stat().st_ino)

    def test_source_changes_during_copy_are_rejected(self) -> None:
        original_open = Path.open
        for content, error in (
            (bytes([self.good[0] ^ 1]) + self.good[1:], "SHA-512"),
            (self.good + b"x" * len(self.good), "unexpected size"),
        ):
            with self.subTest(error=error):
                self.source.write_bytes(self.good)

                def change_then_read(path, mode="r", *args, content=content, **kwargs):
                    stream = original_open(path, mode, *args, **kwargs)
                    if path == self.source and mode == "rb":
                        self.source.write_bytes(content)
                    return stream

                with (
                    mock.patch.object(
                        Path, "open", autospec=True, side_effect=change_then_read
                    ),
                    self.assertRaisesRegex(ValueError, error),
                ):
                    BUILDER.obtain_firefox_archive(self.source, self.work)
                snapshot = self.work / f"firefox-{BUILDER.FIREFOX_VERSION}.tar.xz"
                self.assertLessEqual(snapshot.stat().st_size, len(self.good))

    def test_oversized_source_is_rejected_before_copy(self) -> None:
        self.source.write_bytes(self.good + b"x")
        with (
            mock.patch.object(Path, "open", side_effect=AssertionError("must not copy")),
            self.assertRaisesRegex(ValueError, "unexpected size"),
        ):
            BUILDER.obtain_firefox_archive(self.source, self.work)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_invalid_local_and_downloaded_archives_are_rejected(self) -> None:
        for origin in ("local", "download"):
            for content, error in (
                (self.good[:-1], "unexpected size"),
                (bytes([self.good[0] ^ 1]) + self.good[1:], "SHA-512"),
            ):
                with self.subTest(origin=origin, error=error):
                    self.source.write_bytes(content)
                    with (
                        mock.patch.object(
                            BUILDER.urllib.request, "urlopen",
                            return_value=io.BytesIO(content),
                        ),
                        self.assertRaisesRegex(ValueError, error),
                    ):
                        BUILDER.obtain_firefox_archive(
                            self.source if origin == "local" else None, self.work
                        )

    def test_downloaded_archive_is_verified_and_used_for_payload(self) -> None:
        with mock.patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.good)
        ):
            snapshot = BUILDER.obtain_firefox_archive(None, self.work)
        self.assertEqual(snapshot.parent, self.work)
        self.assert_payload(snapshot, self.root / "download")


if __name__ == "__main__":
    unittest.main()
