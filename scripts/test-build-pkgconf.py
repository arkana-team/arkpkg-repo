#!/usr/bin/env python3
"""Check bounded dependency metadata reads in the pkgconf builder."""

from __future__ import annotations

import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import lz4.frame

SCRIPT = Path(__file__).resolve().with_name("build-pkgconf.py")
spec = importlib.util.spec_from_file_location("arkpkg_build_pkgconf", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"could not load {SCRIPT}")
build_pkgconf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_pkgconf)


class PackageNameTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive_path = Path(temporary.name) / "dependency.ark"

    def write_metadata(self, metadata: bytes, *, sparse_size: int | None = None) -> None:
        member = tarfile.TarInfo("ARKPKG")
        member.size = len(metadata)
        if sparse_size is not None:
            member.pax_headers = {
                "GNU.sparse.map": f"0,{len(metadata)}",
                "GNU.sparse.size": str(sparse_size),
            }
        with (
            lz4.frame.open(self.archive_path, "wb") as compressed,
            tarfile.open(fileobj=compressed, mode="w|") as archive,
        ):
            archive.addfile(member, io.BytesIO(metadata))

    def test_name_field_parsing(self) -> None:
        self.write_metadata("Description: café\n Name : dependency \n".encode())
        self.assertEqual(build_pkgconf.package_name(self.archive_path), "dependency")

    def test_metadata_at_size_limit(self) -> None:
        metadata = b"Name: dependency\n"
        self.write_metadata(metadata.ljust(build_pkgconf.MAX_METADATA_SIZE, b"\n"))
        self.assertEqual(build_pkgconf.package_name(self.archive_path), "dependency")

    def test_oversized_metadata_rejected_before_extraction(self) -> None:
        # A Name at the start must not let oversized metadata bypass the limit.
        self.write_metadata(
            b"Name: dependency\n".ljust(build_pkgconf.MAX_METADATA_SIZE + 1, b"\n")
        )
        with (
            mock.patch.object(tarfile.TarFile, "extractfile") as extract,
            self.assertRaisesRegex(ValueError, "ARKPKG metadata exceeds"),
        ):
            build_pkgconf.package_name(self.archive_path)
        extract.assert_not_called()

    def test_sparse_logical_size_rejected_before_extraction(self) -> None:
        logical_size = 1024**3
        self.write_metadata(b"Name: dependency\n", sparse_size=logical_size)
        self.assertLess(self.archive_path.stat().st_size, 1024)
        with (
            lz4.frame.open(self.archive_path, "rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            member = next(iter(archive))
            self.assertTrue(member.isfile())
            self.assertTrue(member.issparse())
            self.assertEqual(member.size, logical_size)
        with (
            mock.patch.object(tarfile.TarFile, "extractfile") as extract,
            self.assertRaisesRegex(ValueError, "ARKPKG metadata exceeds"),
        ):
            build_pkgconf.package_name(self.archive_path)
        extract.assert_not_called()

    def test_sparse_metadata_within_limit(self) -> None:
        self.write_metadata(
            b"Name: dependency\n", sparse_size=build_pkgconf.MAX_METADATA_SIZE
        )
        self.assertEqual(build_pkgconf.package_name(self.archive_path), "dependency")

    def test_bounded_read_rejects_excess_before_decoding(self) -> None:
        self.write_metadata(b"Name: dependency\n")
        stream = mock.Mock()
        stream.read.return_value = b"\xff" * (build_pkgconf.MAX_METADATA_SIZE + 1)
        with (
            mock.patch.object(tarfile.TarFile, "extractfile", return_value=stream),
            self.assertRaisesRegex(ValueError, "ARKPKG metadata exceeds"),
        ):
            build_pkgconf.package_name(self.archive_path)
        stream.read.assert_called_once_with(build_pkgconf.MAX_METADATA_SIZE + 1)

    def test_missing_name(self) -> None:
        self.write_metadata(b"Description: dependency\n")
        with self.assertRaisesRegex(ValueError, "no Name field"):
            build_pkgconf.package_name(self.archive_path)

    def test_invalid_utf8(self) -> None:
        self.write_metadata(b"Name: dependency\nDescription: \xff\n")
        with self.assertRaises(UnicodeDecodeError):
            build_pkgconf.package_name(self.archive_path)


if __name__ == "__main__":
    unittest.main()
