#!/usr/bin/env python3
"""Test package-version ordering and archives with multiple published versions."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import lz4.frame

SCRIPT = Path(__file__).resolve().with_name("test-packages.py")
SPEC = importlib.util.spec_from_file_location("package_audit", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load test-packages.py")
package_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package_audit
SPEC.loader.exec_module(package_audit)


def write_archive(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("wb") as output,
        lz4.frame.open(output, "wb") as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as archive,
    ):
        metadata = (
            f"Name: demo\nDescription: test package\nVersion: {version}\n"
            "Arch: x86_64\n"
        ).encode()
        metadata_entry = tarfile.TarInfo("ARKPKG")
        metadata_entry.size = len(metadata)
        archive.addfile(metadata_entry, io.BytesIO(metadata))
        payload = tarfile.TarInfo("package/usr/bin/demo")
        payload.mode = 0o755
        payload_data = version.encode()
        payload.size = len(payload_data)
        archive.addfile(payload, io.BytesIO(payload_data))


class PackageAuditVersionTests(unittest.TestCase):
    def test_version_order_handles_epochs_tildes_and_numeric_segments(self) -> None:
        compare = package_audit.compare_versions
        self.assertGreater(compare("1:1.10", "1:1.9"), 0)
        self.assertGreater(compare("1:1.0", "0:99"), 0)
        self.assertLess(compare("1.2~rc1", "1.2"), 0)

    def test_audit_accepts_multiple_versions_of_one_package(self) -> None:
        with tempfile.TemporaryDirectory(prefix="arkpkg-audit-versions-") as temporary:
            root = Path(temporary)
            write_archive(root / "packages/demo-1.9-x86_64.ark", "1.9")
            write_archive(root / "packages/demo-1.10-x86_64.ark", "1.10")
            output = io.StringIO()

            with redirect_stdout(output):
                package_audit.test_repository(
                    root, Path("/usr/bin/true"), audit_only=True
                )

            self.assertIn("Audited 2 archives for 1 packages", output.getvalue())
            self.assertIn("PASS: archive, payload ownership, and dependency audit", output.getvalue())


if __name__ == "__main__":
    unittest.main()
