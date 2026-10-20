#!/usr/bin/env python3
"""Test license paths shared through package dependency symlinks."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

BUILDER_PATH = Path(__file__).with_name("build-debian-cli-packages.py")
SPEC = importlib.util.spec_from_file_location("debian_cli_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the Debian package builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


class LicensePathTests(unittest.TestCase):
    def test_dependency_license_resolves_through_shared_doc_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload"
            docs = payload / "usr/share/doc"
            docs.mkdir(parents=True)
            (docs / "cpp-13").symlink_to("gcc-13-base")
            dependency_license = docs / "gcc-13-base/copyright"
            dependency_license.parent.mkdir()
            dependency_license.write_text("license text\n", encoding="utf-8")

            self.assertTrue(
                BUILDER.license_path_resolves_to(
                    payload,
                    "usr/share/doc/cpp-13/copyright",
                    "usr/share/doc/gcc-13-base/copyright",
                )
            )

    def test_unrelated_license_path_does_not_resolve_to_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload"
            docs = payload / "usr/share/doc"
            docs.mkdir(parents=True)
            (docs / "cpp-13").symlink_to("gcc-13-base")

            self.assertFalse(
                BUILDER.license_path_resolves_to(
                    payload,
                    "usr/share/doc/cpp-13/copyright",
                    "usr/share/doc/libgcc-13/copyright",
                )
            )

    def test_requested_closure_reuses_an_exact_repository_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            recipe = repo_root / "packages/recipes/runtime/ARKPKG"
            archive = repo_root / "packages/runtime-1.0-x86_64.ark"
            recipe.parent.mkdir(parents=True)
            archive.parent.mkdir(parents=True, exist_ok=True)
            recipe.write_text(
                "Name: runtime\nVersion: 1.0\nArch: x86_64\n",
                encoding="utf-8",
            )
            archive.touch()
            packages = [
                {"name": "app", "dependencies": ["runtime"]},
                {"name": "runtime", "version": "1.0", "arch": "x86_64"},
            ]

            closure = BUILDER.requested_package_closure(
                repo_root, packages, {"app"}
            )

            self.assertEqual(closure, {"app"})


class RepositoryProviderTests(unittest.TestCase):
    def test_shared_library_path_is_indexed_as_runtime_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            recipe = repo_root / "packages/recipes/xcb-util/ARKPKG"
            recipe.parent.mkdir(parents=True)
            recipe.write_text(
                "Name: xcb-util\n"
                "Provides: xcb-util=/usr/lib/libxcb-util.so.1\n",
                encoding="utf-8",
            )

            _, providers = BUILDER.repository_owners(repo_root, set())

            self.assertEqual(providers["xcb-util"], "xcb-util")
            self.assertEqual(providers["libxcb-util.so.1"], "xcb-util")


class PackageExclusionTests(unittest.TestCase):
    def test_exclusions_remove_only_named_payload_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "package"
            documentation = payload / "usr/share/doc/restic/html"
            documentation.mkdir(parents=True)
            excluded = documentation / "080_examples.html"
            retained = documentation / "030_preparing_a_new_repo.html"
            excluded.write_text("example credentials\n", encoding="utf-8")
            retained.write_text("normal help\n", encoding="utf-8")

            BUILDER.exclude_package_paths(
                payload,
                ["usr/share/doc/restic/html/080_examples.html"],
                "restic",
            )

            self.assertFalse(excluded.exists())
            self.assertTrue(retained.is_file())

    def test_exclusions_reject_paths_outside_payload(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(ValueError, "unsafe excluded path"),
        ):
            BUILDER.exclude_package_paths(Path(temporary), ["../outside"], "restic")

    def test_exclusions_fail_if_pinned_file_is_missing(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(FileNotFoundError, "expected excluded file"),
        ):
            BUILDER.exclude_package_paths(
                Path(temporary),
                ["usr/share/doc/restic/html/080_examples.html"],
                "restic",
            )


class WorkstationPackageManifestTests(unittest.TestCase):
    def test_workstation_packages_have_pinned_sources_and_maintainer_metadata(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        new_batch = {
            "aerc",
            "age",
            "dmidecode",
            "dust",
            "elinks",
            "libev4t64",
            "libfsplib0t64",
            "libgmime-3.0-0t64",
            "libnotmuch5t64",
            "libsexp2",
            "libxdo3",
            "neomutt",
            "numlockx",
            "restic",
            "sd",
            "stterm",
            "sxiv",
            "wmctrl",
            "xautomation",
            "xbanish",
            "xclip",
            "xkbset",
            "xsel",
            "xvkbd",
            "xdotool",
        }
        expected = new_batch | {
            "abook",
            "irssi",
            "libgsasl18",
            "libgssglue1",
            "libntlm0",
            "libstfl0",
            "libtokyocabinet9t64",
            "minicom",
            "mutt",
            "newsboat",
            "picocom",
            "weechat",
        }
        packages = {package["name"]: package for package in manifest["packages"]}
        self.assertTrue(expected.issubset(packages))

        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-06.json").read_text(
                encoding="utf-8"
            )
        )
        batch_packages = {package["name"]: package for package in batch["packages"]}
        self.assertEqual(set(batch_packages), new_batch)
        for name, package in batch_packages.items():
            self.assertEqual(packages[name], package)
        for name, source in batch["sources"].items():
            self.assertEqual(manifest["sources"][name], source)

        for name in expected:
            package = packages[name]
            self.assertTrue(package["debian_packages"], name)
            self.assertTrue(package["license_path"], name)
            for debian_name in package["debian_packages"]:
                source = manifest["sources"][debian_name]
                self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$", debian_name)
                self.assertTrue(source["filename"].startswith("pool/"), debian_name)

            package_metadata = metadata[name]
            self.assertIn("author", package_metadata)
            self.assertIn("git_author_url", package_metadata)
            self.assertIn(
                package_metadata["git_repo_type"],
                {"upstream", "packaging", "arkpkg-recipe"},
            )
            self.assertTrue(package_metadata["git_repo"].startswith("https://"))
            packager_names = {
                packager["name"] for packager in package_metadata["packagers"]
            }
            self.assertEqual(packager_names, {"arkana-team", "debarkak"})

        self.assertEqual(
            packages["weechat"]["debian_packages"],
            ["weechat-core", "weechat-curses", "weechat-plugins"],
        )
        self.assertEqual(
            packages["restic"]["exclude_paths"],
            [
                "usr/share/doc/restic/html/080_examples.html",
                "usr/share/doc/restic/html/_sources/080_examples.rst.txt",
            ],
        )


if __name__ == "__main__":
    unittest.main()
