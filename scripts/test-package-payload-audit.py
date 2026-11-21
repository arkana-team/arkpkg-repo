#!/usr/bin/env python3
"""Check that the archive audit rejects unusable payload entries."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import lz4.frame

SCRIPT = Path(__file__).resolve().with_name("test-packages.py")
SPEC = importlib.util.spec_from_file_location("package_audit_payload", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load test-packages.py")
package_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package_audit
SPEC.loader.exec_module(package_audit)

METADATA = b"Name: demo\nDescription: payload audit\nVersion: 1.0\nArch: x86_64\n"


def write_archive(path: Path, entries: list[tuple[str, int, bytes]]) -> None:
    """Write an archive from (name, mode, content) payload entries."""
    with (
        path.open("wb") as output,
        lz4.frame.open(output, "wb") as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as archive,
    ):
        metadata = tarfile.TarInfo("ARKPKG")
        metadata.size = len(METADATA)
        archive.addfile(metadata, io.BytesIO(METADATA))
        for name, mode, content in entries:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = mode
            archive.addfile(member, io.BytesIO(content))


class PayloadAuditTests(unittest.TestCase):
    def test_executable_payloads_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            write_archive(
                path,
                [
                    ("package/usr/bin/demo", 0o755, b"#!/bin/sh\n"),
                    ("package/usr/share/doc/demo/README", 0o644, b"docs\n"),
                ],
            )
            metadata, _, _ = package_audit.inspect_archive(path)
            self.assertEqual(metadata["Name"], "demo")

    def test_bin_entries_without_an_executable_bit_are_rejected(self) -> None:
        for name, mode in (
            ("package/usr/bin/demo", 0o640),
            ("package/usr/sbin/demo", 0o644),
            ("package/usr/games/demo", 0o600),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "demo.ark"
                write_archive(path, [(name, mode, b"payload\n")])
                with self.assertRaisesRegex(ValueError, "not executable"):
                    package_audit.inspect_archive(path)

    def test_staging_paths_in_metadata_are_rejected(self) -> None:
        for name, content in (
            (
                "package/usr/lib/pkgconfig/demo.pc",
                b"prefix=/build/arkana/staging/usr\nlibdir=${prefix}/lib\n",
            ),
            (
                "package/usr/lib/cmake/demo/DemoConfig.cmake",
                b'set(DEMO_ROOT "/tmp/arkana-build/staging/usr")\n',
            ),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "demo.ark"
                write_archive(path, [(name, 0o644, content)])
                with self.assertRaisesRegex(ValueError, "build-time path"):
                    package_audit.inspect_archive(path)

    def test_installed_prefixes_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            write_archive(
                path,
                [
                    (
                        "package/usr/lib/pkgconfig/demo.pc",
                        0o644,
                        b"prefix=/usr\nlibdir=${exec_prefix}/lib\n",
                    ),
                    (
                        "package/usr/share/wayland-protocols/staging/demo.xml",
                        0o644,
                        b"<protocol name=\"staging-demo\"/>\n",
                    ),
                ],
            )
            package_audit.inspect_archive(path)

    def test_setid_world_writable_payloads_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            write_archive(path, [("package/usr/bin/demo", 0o4777, b"#!/bin/sh\n")])
            with self.assertRaisesRegex(ValueError, "set-id"):
                package_audit.inspect_archive(path)

    def test_large_binary_payloads_are_not_decoded_as_metadata(self) -> None:
        # Only small text metadata files are inspected for staging paths, so a
        # multi-megabyte binary is skipped instead of being read and decoded.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.ark"
            write_archive(
                path,
                [("package/usr/lib/libdemo.la", 0o644, b"\x00" * (300 * 1024))],
            )
            package_audit.inspect_archive(path)


if __name__ == "__main__":
    unittest.main()
