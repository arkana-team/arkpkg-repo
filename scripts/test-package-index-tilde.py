#!/usr/bin/env python3
"""Ensure the package index accepts Debian epoch and tilde versions."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import lz4.frame


SCRIPT = Path(__file__).resolve().with_name("build-index.py")
SPEC = importlib.util.spec_from_file_location("build_index", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load build-index.py")
build_index = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_index
SPEC.loader.exec_module(build_index)


class PackageIndexTildeVersionTests(unittest.TestCase):
    def test_site_indexes_epoch_and_tilde_version_archive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="arkpkg-index-version-") as temporary:
            root = Path(temporary)
            archive_path = root / "packages/hello-1:1.2.3~rc1-x86_64.ark"
            archive_path.parent.mkdir()
            with (
                archive_path.open("wb") as output,
                lz4.frame.open(output, "wb") as compressed,
                tarfile.open(fileobj=compressed, mode="w|") as archive,
            ):
                metadata = (
                    b"Name: hello\n"
                    b"Description: test package\n"
                    b"Version: 1:1.2.3~rc1\n"
                    b"Arch: x86_64\n"
                )
                metadata_entry = tarfile.TarInfo("ARKPKG")
                metadata_entry.size = len(metadata)
                archive.addfile(metadata_entry, io.BytesIO(metadata))
                package_tree = tarfile.TarInfo("package/")
                package_tree.type = tarfile.DIRTYPE
                archive.addfile(package_tree)

            site = root / "site"
            build_index.build_site(root, site)

            index = json.loads((site / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["packages"][0]["version"], "1:1.2.3~rc1")
            self.assertEqual(
                index["packages"][0]["sha256"],
                hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                (site / "packages" / archive_path.name).read_bytes(),
                archive_path.read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
