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


def extension_data(size: int) -> bytes:
    """Return a valid PAX comment record of exactly size bytes."""
    prefix = f"{size} comment=".encode()
    return prefix + b"x" * (size - len(prefix) - 1) + b"\n"


def add_extensions(
    path: Path, extensions: list[tuple[bytes, bytes]], after_metadata: bool = False
) -> None:
    write_archive(path, METADATA)
    data = lz4.frame.decompress(path.read_bytes())
    records = []
    for kind, content in extensions:
        member = tarfile.TarInfo("extension")
        member.type = kind
        member.size = len(content)
        records.append(member.tobuf(format=tarfile.USTAR_FORMAT))
        records.append(content)
        records.append(b"\0" * (-len(content) % tarfile.BLOCKSIZE))
    offset = 2 * tarfile.BLOCKSIZE if after_metadata else 0
    path.write_bytes(
        lz4.frame.compress(data[:offset] + b"".join(records) + data[offset:])
    )


class ArchiveExtensionLimitTests(unittest.TestCase):
    def test_rejects_compressed_extensions_before_buffering(self) -> None:
        limit = package_audit.MAX_TAR_EXTENSION_BYTES
        original_read = tarfile._Stream.read

        def bounded_read(stream, size):
            if size > limit:
                raise AssertionError(f"extension buffered before validation: {size}")
            return original_read(stream, size)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            for kind in (
                tarfile.XHDTYPE,
                tarfile.XGLTYPE,
                tarfile.SOLARIS_XHDTYPE,
                tarfile.GNUTYPE_LONGNAME,
                tarfile.GNUTYPE_LONGLINK,
            ):
                for after_metadata in (False, True):
                    add_extensions(
                        path, [(kind, extension_data(limit + 1))], after_metadata
                    )
                    self.assertLess(path.stat().st_size, 8192)
                    with (
                        self.subTest(kind=kind, after_metadata=after_metadata),
                        patch.object(tarfile._Stream, "read", bounded_read),
                        self.assertRaisesRegex(ValueError, "TAR extension data"),
                    ):
                        package_audit.inspect_archive(path)

    def test_accepts_extensions_at_limit_and_resets_budget_per_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            add_extensions(
                path,
                [(tarfile.XHDTYPE, extension_data(package_audit.MAX_TAR_EXTENSION_BYTES))],
            )
            for _ in range(2):
                self.assertEqual(package_audit.inspect_archive(path)[0]["Name"], "demo")

    def test_rejects_cumulative_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            half = extension_data(package_audit.MAX_TAR_EXTENSION_BYTES // 2)
            add_extensions(path, [(tarfile.XGLTYPE, half)] * 3)
            data = lz4.frame.decompress(path.read_bytes())
            # Separate global headers with members, so they are not all nested.
            record_size = tarfile.BLOCKSIZE + len(half)
            member = tarfile.TarInfo("package/empty")
            member.type = tarfile.DIRTYPE
            path.write_bytes(
                lz4.frame.compress(
                    data[:record_size] + member.tobuf()
                    + data[record_size:2 * record_size] + member.tobuf()
                    + data[2 * record_size:]
                )
            )
            with self.assertRaisesRegex(ValueError, "TAR extension data"):
                package_audit.inspect_archive(path)

    def test_limits_nested_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            extensions = [(tarfile.XHDTYPE, extension_data(32))]
            add_extensions(path, extensions * package_audit.MAX_TAR_EXTENSION_DEPTH)
            self.assertEqual(package_audit.inspect_archive(path)[0]["Name"], "demo")
            add_extensions(path, extensions * (package_audit.MAX_TAR_EXTENSION_DEPTH + 1))
            with self.assertRaisesRegex(ValueError, "TAR extension nesting"):
                package_audit.inspect_archive(path)

    def test_accepts_gnu_long_names_and_links_and_large_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            add_extensions(
                path,
                [
                    (tarfile.GNUTYPE_LONGNAME, b"package/" + b"x" * 200 + b"\0"),
                    (tarfile.GNUTYPE_LONGLINK, b"target/" + b"y" * 200 + b"\0"),
                ],
                after_metadata=True,
            )
            data = lz4.frame.decompress(path.read_bytes())
            payload = tarfile.TarInfo("package/large")
            payload.size = 2 * package_audit.MAX_TAR_EXTENSION_BYTES
            # Insert a regular file after ARKPKG and before the GNU extensions.
            offset = 2 * tarfile.BLOCKSIZE
            path.write_bytes(
                lz4.frame.compress(
                    data[:offset] + payload.tobuf() + b"x" * payload.size + data[offset:]
                )
            )
            self.assertEqual(package_audit.inspect_archive(path)[0]["Name"], "demo")

    def test_ownership_pass_uses_extension_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "packages").mkdir()
            path = root / "packages/demo.ark"
            add_extensions(
                path,
                [(tarfile.XHDTYPE, extension_data(package_audit.MAX_TAR_EXTENSION_BYTES + 1))],
            )
            # Bypass the first pass to exercise the independent ownership reader.
            with (
                patch.object(
                    package_audit, "inspect_archive",
                    return_value=({"Name": "demo", "Version": "1", "Arch": "any"}, [], []),
                ),
                self.assertRaisesRegex(ValueError, "TAR extension data"),
            ):
                package_audit.test_repository(root, Path("/unused/arkpkg"), audit_only=True)


if __name__ == "__main__":
    unittest.main()
