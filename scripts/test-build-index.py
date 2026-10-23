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
validate_package_metadata = build_index.validate_package_metadata
package_category = build_index.package_category


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
            build_site(root, site)

            index = json.loads((site / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["schema_version"], 1)
            self.assertEqual(len(index["packages"]), 1)
            entry = index["packages"][0]
            self.assertIsNone(entry["author"])
            self.assertEqual(entry["git_repo"], "https://github.com/example/hello")
            self.assertEqual(entry["git_repo_type"], "upstream")
            self.assertIsNone(entry["git_author_url"])
            self.assertEqual(
                entry["packagers"],
                [
                    {
                        "name": "arkana-team",
                        "url": "https://github.com/arkana-team",
                    },
                    {
                        "name": "debarkak",
                        "url": "https://github.com/debarkak",
                    },
                ],
            )
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
            self.assertTrue((site / "catalogue.css").is_file())
            self.assertTrue((site / "catalogue.js").is_file())
            page = (site / "index.html").read_text(encoding="utf-8")
            style = (site / "catalogue.css").read_text(encoding="utf-8")
            script = (site / "catalogue.js").read_text(encoding="utf-8")
            self.assertIn('data-sort-key="author"', page)
            self.assertIn('data-sort-key="git_repo"', page)
            self.assertIn('href="https://github.com/example/hello"', page)
            self.assertIn('data-packagers="[{&quot;name&quot;:&quot;arkana-team&quot;', page)
            self.assertIn('data-git-repo-type="upstream"', page)
            self.assertIn('data-category-filter="all"', page)
            self.assertIn('id="details-packagers"', page)
            self.assertIn('href="catalogue.css"', page)
            self.assertIn('src="catalogue.js"', page)
            self.assertIn('id="mobile-category"', page)
            self.assertIn("software catalogue for arkanaOS", page)
            self.assertNotIn("solaris", page.lower())
            self.assertNotIn("oracle", page.lower())
            self.assertIn(">not listed</td>", page)
            self.assertIn('<small class="repo-type">upstream</small>', page)
            self.assertIn(".catalogue-layout", style)
            self.assertIn("@media (max-width: 760px)", style)
            self.assertIn("--accent: #3159b8", style)
            self.assertIn("background: #dfe1e3", style)
            self.assertIn(
                "inset 1px 1px 0 #fff, inset -1px -1px 0 #b3b8be, "
                "0 2px 5px #28313d24",
                style,
            )
            self.assertIn("inset 2px 2px 3px #545d6624", style)
            self.assertIn('.category-list button[aria-pressed="true"]', style)
            self.assertIn(".download-link:active", style)
            self.assertNotIn("linear-gradient", style)
            self.assertIn("function selectCategory", script)
            self.assertIn("function showDetails", script)

    def test_package_categories_cover_common_workstation_software(self) -> None:
        cases = (
            ("libx11", "X11 shared libraries", "libraries"),
            ("rustc-runtime", "Rust compiler driver shared library", "libraries"),
            ("linux-firmware", "Firmware for network devices", "drivers"),
            ("printer-driver-brlaser", "Printer driver for Brother printers", "drivers"),
            ("nscde", "A desktop environment for X11", "desktop"),
            ("coreutils", "GNU core utilities", "system"),
            ("openssh-client", "Secure shell networking tools", "networking"),
            ("nginx", "A web server", "web-services"),
            ("hello", "A command line greeting program", "applications"),
        )
        for name, description, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(package_category(name, description), expected)

    def test_package_metadata_requires_complete_https_links(self) -> None:
        package = {
            "author": "Example upstream author",
            "git_repo": "https://github.com/example/hello",
            "git_repo_type": "upstream",
            "git_author_url": "https://github.com/example",
            "packagers": [
                {"name": "arkana-team", "url": "https://github.com/arkana-team"},
                {"name": "debarkak", "url": "https://github.com/debarkak"},
            ],
        }
        valid = {"schema_version": 1, "packages": {"hello": package}}
        self.assertEqual(
            validate_package_metadata(valid, {"hello"})["hello"], package
        )
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            validate_package_metadata(valid, {"hello", "world"})
        with self.assertRaisesRegex(ValueError, "git_repo for hello must be HTTPS"):
            validate_package_metadata(
                {
                    "schema_version": 1,
                    "packages": {
                        "hello": {**package, "git_repo": "http://example.com/repo"}
                    },
                },
                {"hello"},
            )
        with self.assertRaisesRegex(ValueError, "author profile requires"):
            validate_package_metadata(
                {
                    "schema_version": 1,
                    "packages": {
                        "hello": {**package, "author": None}
                    },
                },
                {"hello"},
            )
        with self.assertRaisesRegex(ValueError, "packager URL for hello must be HTTPS"):
            validate_package_metadata(
                {
                    "schema_version": 1,
                    "packages": {
                        "hello": {
                            **package,
                            "packagers": [
                                {"name": "arkana-team", "url": "http://example.com"}
                            ],
                        }
                    },
                },
                {"hello"},
            )
        query_url = {
            "schema_version": 1,
            "packages": {
                "hello": {**package, "git_repo": "https://git.example/?p=hello.git"}
            },
        }
        self.assertEqual(
            validate_package_metadata(query_url, {"hello"})["hello"]["git_repo"],
            "https://git.example/?p=hello.git",
        )

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
