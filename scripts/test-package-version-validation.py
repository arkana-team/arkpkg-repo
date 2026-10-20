#!/usr/bin/env python3
"""Check that index and archive audits accept Debian epoch and tilde versions."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import lz4.frame


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ROOT = Path(__file__).resolve().parent
build_index = load_script("arkpkg_build_index", ROOT / "build-index.py")
test_packages = load_script("arkpkg_test_packages", ROOT / "test-packages.py")


class PackageVersionValidationTests(unittest.TestCase):
    def test_index_and_archive_audit_accept_debian_versions(self) -> None:
        for version in ("1:7.10.1+dfsg-1", "1.2.3~rc1", "1.0.0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                archive_path = Path(directory) / "hello.ark"
                with (
                    archive_path.open("wb") as output,
                    lz4.frame.open(output, "wb") as compressed,
                    tarfile.open(fileobj=compressed, mode="w|") as archive,
                ):
                    metadata = (
                        "Name: hello\n"
                        "Description: version validation test\n"
                        f"Version: {version}\n"
                        "Arch: any\n"
                    ).encode()
                    metadata_entry = tarfile.TarInfo("ARKPKG")
                    metadata_entry.size = len(metadata)
                    archive.addfile(metadata_entry, io.BytesIO(metadata))
                    payload = b"test package payload\n"
                    payload_entry = tarfile.TarInfo("package/usr/share/hello")
                    payload_entry.size = len(payload)
                    archive.addfile(payload_entry, io.BytesIO(payload))

                self.assertEqual(build_index.read_metadata(archive_path)["Version"], version)
                self.assertEqual(
                    test_packages.inspect_archive(archive_path)[0]["Version"], version
                )


if __name__ == "__main__":
    unittest.main()
