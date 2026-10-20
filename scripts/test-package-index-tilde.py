#!/usr/bin/env python3
"""Ensure the package index supports Debian versions and direct archive URLs."""

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
    def test_site_indexes_versions_without_copying_package_archives(self) -> None:
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

            metadata_dir = root / "data"
            metadata_dir.mkdir()
            (metadata_dir / "package-metadata.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "packages": {
                            "hello": {
                                "author": None,
                                "git_repo": "https://github.com/example/hello",
                                "git_repo_type": "upstream",
                                "git_author_url": None,
                                "packagers": [
                                    {
                                        "name": "arkana-team",
                                        "url": "https://github.com/arkana-team",
                                    },
                                    {
                                        "name": "debarkak",
                                        "url": "https://github.com/debarkak",
                                    },
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            site = root / "site"
            build_index.build_site(root, site)

            index = json.loads((site / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["packages"][0]["version"], "1:1.2.3~rc1")
            self.assertIsNone(index["packages"][0]["author"])
            self.assertIsNone(index["packages"][0]["git_author_url"])
            self.assertEqual(
                [p["name"] for p in index["packages"][0]["packagers"]],
                ["arkana-team", "debarkak"],
            )
            self.assertEqual(
                index["packages"][0]["sha256"],
                hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                index["packages"][0]["download_url"],
                "https://raw.githubusercontent.com/arkana-team/arkpkg-repo/main/"
                "packages/hello-1%3A1.2.3~rc1-x86_64.ark",
            )
            self.assertFalse((site / "packages").exists())

    def test_download_base_url_requires_safe_https(self) -> None:
        for value in (
            "http://example.com/packages",
            "https://user:password@example.com/packages",
            "https://example.com/packages?token=secret",
            "https://example.com/packages#fragment",
            "https://example.com:0/packages",
            "https://example.com:wrong/packages",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_index.validate_download_base_url(value)


if __name__ == "__main__":
    unittest.main()
