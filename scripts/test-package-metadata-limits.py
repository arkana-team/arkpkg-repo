#!/usr/bin/env python3
"""Check metadata size limits in the archive audit and site builder."""

import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lz4.frame


def load_script(filename: str):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_index = load_script("build-index.py")
package_audit = load_script("test-packages.py")
READERS = (
    (build_index.read_metadata, build_index.MAX_METADATA_SIZE),
    (package_audit.inspect_archive, package_audit.MAX_METADATA_BYTES),
)
METADATA = b"Name: demo\nDescription: test\nVersion: 1\nArch: any\n"


def write_archive(path: Path, metadata: bytes, logical_size: int | None = None) -> None:
    with (
        lz4.frame.open(path, "wb") as compressed,
        tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive,
    ):
        member = tarfile.TarInfo("./ARKPKG")
        member.size = len(metadata)
        if logical_size is not None:
            member.pax_headers = {
                "GNU.sparse.map": f"0,{len(metadata)}",
                "GNU.sparse.size": str(logical_size),
            }
        archive.addfile(member, io.BytesIO(metadata))
        payload = tarfile.TarInfo("package/test")
        payload.size = 1
        archive.addfile(payload, io.BytesIO(b"x"))


class MetadataLimitTests(unittest.TestCase):
    def test_accepts_metadata_at_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            for reader, limit in READERS:
                with self.subTest(reader=reader.__name__):
                    write_archive(path, METADATA + b"#" * (limit - len(METADATA)))
                    result = reader(path)
                    fields = result[0] if isinstance(result, tuple) else result
                    self.assertEqual(fields["Name"], "demo")

    def test_rejects_oversized_metadata_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            for sparse in (False, True):
                if sparse:
                    # A tiny physical member advertises four GiB of logical data.
                    write_archive(path, METADATA, logical_size=4 * 1024**3)
                    self.assertLess(path.stat().st_size, 1024)
                for reader, limit in READERS:
                    if not sparse:
                        write_archive(
                            path, METADATA + b"#" * (limit + 1 - len(METADATA))
                        )
                    with (
                        self.subTest(sparse=sparse, reader=reader.__name__),
                        patch.object(tarfile.TarFile, "extractfile") as extract,
                        self.assertRaisesRegex(ValueError, "ARKPKG metadata"),
                    ):
                        try:
                            reader(path)
                        finally:
                            extract.assert_not_called()

    def test_read_is_bounded_and_overflow_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            write_archive(path, METADATA)
            for reader, limit in READERS:

                class MetadataStream(io.BytesIO):
                    def read(self, size=-1, expected_size=limit + 1):
                        if size != expected_size:
                            raise AssertionError(f"unbounded metadata read: {size}")
                        return super().read(size)

                with (
                    self.subTest(reader=reader.__name__),
                    patch.object(
                        tarfile.TarFile,
                        "extractfile",
                        return_value=MetadataStream(b"\xff" * (limit + 2)),
                    ),
                    self.assertRaisesRegex(ValueError, "ARKPKG metadata"),
                ):
                    reader(path)


if __name__ == "__main__":
    unittest.main()
