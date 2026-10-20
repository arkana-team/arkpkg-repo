"""Check that the Pages index stays small and points to verified package files."""

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

spec = importlib.util.spec_from_file_location(
    "build_index", Path(__file__).resolve().with_name("build-index.py")
)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load build-index.py")
build_index = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build_index
spec.loader.exec_module(build_index)
build_site = build_index.build_site
validate_download_base_url = build_index.validate_download_base_url


class BuildIndexTests(unittest.TestCase):
    def test_site_publishes_epoch_and_tilde_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "packages" / "hello-1:1.2.3~rc1-x86_64.ark"
            archive_path.parent.mkdir()
            with (
                archive_path.open("wb") as output,
                lz4.frame.open(output, "wb") as compressed,
                tarfile.open(fileobj=compressed, mode="w|") as archive,
            ):
                metadata = (
                    b"Name: hello\n"
                    b"Description: sample package\n"
                    b"Version: 1:1.2.3~rc1\n"
                    b"Arch: x86_64\n"
                )
                metadata_info = tarfile.TarInfo("ARKPKG")
                metadata_info.size = len(metadata)
                archive.addfile(metadata_info, io.BytesIO(metadata))
                package_info = tarfile.TarInfo("package/")
                package_info.type = tarfile.DIRTYPE
                archive.addfile(package_info)

            site = root / "site"
            build_site(root, site)

            index = json.loads((site / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["schema_version"], 1)
            self.assertEqual(len(index["packages"]), 1)
            entry = index["packages"][0]
            self.assertEqual(
                entry["download_url"],
                "https://raw.githubusercontent.com/arkana-team/arkpkg-repo/main/"
                "packages/hello-1%3A1.2.3~rc1-x86_64.ark",
            )
            self.assertEqual(entry["version"], "1:1.2.3~rc1")
            self.assertEqual(
                entry["sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest()
            )
            self.assertFalse((site / "packages").exists())
            self.assertTrue((site / "index.html").is_file())

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
                validate_download_base_url(value)


if __name__ == "__main__":
    unittest.main()
