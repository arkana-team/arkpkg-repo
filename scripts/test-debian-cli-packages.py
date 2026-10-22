#!/usr/bin/env python3
"""Test license paths shared through package dependency symlinks."""

import importlib.util
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

BUILDER_PATH = Path(__file__).with_name("build-debian-cli-packages.py")
SPEC = importlib.util.spec_from_file_location("debian_cli_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the Debian package builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
UPSTREAM_BUILDER_PATH = Path(__file__).with_name("build-upstream-cli-packages.py")
UPSTREAM_SPEC = importlib.util.spec_from_file_location(
    "upstream_cli_builder", UPSTREAM_BUILDER_PATH
)
if UPSTREAM_SPEC is None or UPSTREAM_SPEC.loader is None:
    raise RuntimeError("could not load the upstream package builder")
UPSTREAM_BUILDER = importlib.util.module_from_spec(UPSTREAM_SPEC)
UPSTREAM_SPEC.loader.exec_module(UPSTREAM_BUILDER)


class DebianArchivePermissionTests(unittest.TestCase):
    def test_extract_data_archive_preserves_modes_despite_restrictive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deb = root / "fixture.deb"
            work = root / "work"
            work.mkdir()

            control_archive = root / "control.tar.xz"
            with tarfile.open(control_archive, "w:xz"):
                pass
            data_archive = root / "data.tar.xz"
            with tarfile.open(data_archive, "w:xz") as archive:
                for name, mode, content in (
                    ("usr/bin/tool", 0o755, b"#!/bin/sh\n"),
                    ("usr/share/doc/tool/copyright", 0o644, b"license\n"),
                ):
                    member = tarfile.TarInfo(name)
                    member.mode = mode
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))

            (root / "debian-binary").write_text("2.0\n", encoding="ascii")
            subprocess.run(
                ["ar", "rc", str(deb), "debian-binary", "control.tar.xz", "data.tar.xz"],
                cwd=root,
                check=True,
            )

            previous_umask = os.umask(0o027)
            try:
                payload = BUILDER.extract_data_archive(deb, work)
            finally:
                os.umask(previous_umask)

            self.assertEqual(
                stat.S_IMODE((payload / "usr/bin/tool").stat().st_mode), 0o755
            )
            self.assertEqual(
                stat.S_IMODE(
                    (payload / "usr/share/doc/tool/copyright").stat().st_mode
                ),
                0o644,
            )


class UpstreamPayloadPermissionTests(unittest.TestCase):
    def test_written_files_have_explicit_modes_despite_restrictive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "usr/bin/tool"
            documentation = root / "usr/share/doc/tool/copyright"
            previous_umask = os.umask(0o027)
            try:
                UPSTREAM_BUILDER.write_payload_file(executable, b"script\n", 0o755)
                UPSTREAM_BUILDER.write_payload_file(documentation, b"license\n", 0o644)
            finally:
                os.umask(previous_umask)

            self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(documentation.stat().st_mode), 0o644)


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


class DebianPythonPathTests(unittest.TestCase):
    def test_maps_debian_dist_packages_to_target_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            staging = root / "staging"
            source_dir = payload / "usr/lib/python3/dist-packages"
            source_dir.mkdir(parents=True)
            staging_site = staging / "usr/lib/python3.13/site-packages"
            staging_site.mkdir(parents=True)
            module = source_dir / "sample_module.py"
            module.write_text("value = 7\n", encoding="utf-8")

            BUILDER.relocate_debian_python_packages(payload, staging)

            self.assertEqual(
                (payload / "usr/lib/python3.13/site-packages/sample_module.py")
                .read_text(encoding="utf-8"),
                "value = 7\n",
            )
            self.assertFalse(source_dir.exists())

    def test_python_packages_require_a_target_sysroot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload"
            (payload / "usr/lib/python3/dist-packages").mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "--staging is required"):
                BUILDER.relocate_debian_python_packages(payload, None)

    def test_native_packages_do_not_require_python_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            BUILDER.relocate_debian_python_packages(Path(temporary), None)


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


class OptionalCliUtilitiesBatchTests(unittest.TestCase):
    def test_batch_07_has_pinned_hardware_and_shell_utilities(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-07.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {"libtinfo6", "lshw"}
        packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), expected)
        for name, package in batch_packages.items():
            self.assertEqual(packages[name], package)
            for source_name in package["debian_packages"]:
                source = batch["sources"][source_name]
                self.assertEqual(manifest["sources"][source_name], source)
                self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$", source_name)
            package_metadata = metadata[name]
            self.assertTrue(package_metadata["author"], name)
            self.assertTrue(package_metadata["git_repo"].startswith("https://"), name)
            self.assertTrue(package_metadata["git_author_url"].startswith("https://"), name)
            self.assertEqual(
                {item["name"] for item in package_metadata["packagers"]},
                {"arkana-team", "debarkak"},
            )

        self.assertEqual(packages["libtinfo6"]["version"], "6.5+20250216-2")


class OptionalWorkstationUtilitiesBatchTests(unittest.TestCase):
    def test_batch_08_has_pinned_tools_and_python_runtime_dependencies(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-08.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_tools = {
            "bfs",
            "cava",
            "cmatrix",
            "cmus",
            "f3",
            "gallery-dl",
            "gping",
            "libcddb2",
            "libdiscid0",
            "libmediainfo0v5",
            "libopenmpt-modplug1",
            "libpoppler-cpp2",
            "libzen0t64",
            "libiniparser4",
            "mediainfo",
            "pdfgrep",
            "powertop",
            "s-tui",
            "wavemon",
            "yq",
        }
        expected_python = {
            "python3-argcomplete",
            "python3-certifi",
            "python3-chardet",
            "python3-charset-normalizer",
            "python3-idna",
            "python3-mutagen",
            "python3-psutil",
            "python3-pycryptodome",
            "python3-requests",
            "python3-tomlkit",
            "python3-typing-extensions",
            "python3-urllib3",
            "python3-urwid",
            "python3-websockets",
            "python3-wcwidth",
            "python3-xmltodict",
        }
        expected = expected_tools | expected_python
        packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), expected)

        for name, package in batch_packages.items():
            self.assertEqual(packages[name], package)
            self.assertTrue(package["debian_packages"], name)
            self.assertTrue(package["license_path"], name)
            for source_name in package["debian_packages"]:
                source = batch["sources"][source_name]
                self.assertEqual(manifest["sources"][source_name], source)
                self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$", source_name)

            package_metadata = metadata[name]
            self.assertIn("author", package_metadata)
            self.assertIn("git_author_url", package_metadata)
            self.assertTrue(package_metadata["git_repo"].startswith("https://"), name)
            self.assertIn(
                package_metadata["git_repo_type"],
                {"upstream", "packaging", "arkpkg-recipe"},
            )
            self.assertEqual(
                {item["name"] for item in package_metadata["packagers"]},
                {"arkana-team", "debarkak"},
            )

        self.assertEqual(
            set(packages["yq"]["dependencies"]),
            {
                "jq",
                "python3-argcomplete",
                "python3-tomlkit",
                "python3-xmltodict",
            },
        )
        self.assertEqual(packages["cava"]["dependencies"], ["libiniparser4"])
        self.assertEqual(packages["mediainfo"]["dependencies"], ["libmediainfo0v5"])
        self.assertEqual(packages["pdfgrep"]["dependencies"], ["libpoppler-cpp2"])
        self.assertEqual(
            set(packages["cmus"]["dependencies"]),
            {"libcddb2", "libdiscid0", "libopenmpt-modplug1"},
        )
        self.assertIn("python3-requests", packages["gallery-dl"]["dependencies"])
        self.assertEqual(
            set(packages["python3-requests"]["dependencies"]),
            {
                "python3-certifi",
                "python3-charset-normalizer",
                "python3-chardet",
                "python3-idna",
                "python3-urllib3",
            },
        )
        self.assertEqual(
            packages["python3-urwid"]["dependencies"],
            ["python3-typing-extensions", "python3-wcwidth"],
        )


class OptionalUnixUtilitiesBatchTests(unittest.TestCase):
    def test_batch_09_pins_history_markdown_and_copy_tools(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-09.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "glow": (
                "2.0.0-1+b5",
                "3e36a1b585d953429b429ec53a51b75ff1e182b7a76f547d5c60014899d08218",
                "Charmbracelet",
            ),
            "hstr": (
                "3.1+ds-1+b1",
                "0d7eb3c71a675bcfdd1cff196d3fac98e5c5cf3e6c5429ec425cac6363d14646",
                "Martin Dvorak",
            ),
            "progress": (
                "0.17-1",
                "c91bcc19a86ec1e340fa401514e98a6e38d9ff6367b63e4dcf85387cda7bfad8",
                "Julien LOLLIVIER",
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected))

        for name, (version, digest, author) in expected.items():
            package = batch_packages[name]
            self.assertEqual(package, manifest_packages[name])
            self.assertEqual(package["version"], version)
            self.assertEqual(package["debian_version"], version)
            source = batch["sources"][name]
            self.assertEqual(source, manifest["sources"][name])
            self.assertEqual(source["version"], version)
            self.assertEqual(source["sha256"], digest)
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(metadata[name]["author"], author)
            self.assertEqual(metadata[name]["git_repo_type"], "upstream")
            self.assertTrue(metadata[name]["git_repo"].startswith("https://"))
            self.assertTrue(metadata[name]["git_author_url"].startswith("https://"))
            self.assertEqual(
                {item["name"] for item in metadata[name]["packagers"]},
                {"arkana-team", "debarkak"},
            )


class UpstreamReleasePackageTests(unittest.TestCase):
    def test_yt_dlp_is_pinned_to_the_upstream_release_assets(self) -> None:
        package = UPSTREAM_BUILDER.PACKAGES["yt-dlp"]
        self.assertEqual(package["version"], "2026.08.19")
        self.assertEqual(
            package["archive_sha256"],
            "072aad4f2a7604e92155f61a275a4752dc64046c8f6d90df3710525d94cd37c1",
        )
        self.assertEqual(
            package["binary_sha256"],
            "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6",
        )
        self.assertEqual(package["binary_path"], "usr/bin/yt-dlp")
        self.assertIn("usr/share/man/man1/yt-dlp.1", package["files"].values())


if __name__ == "__main__":
    unittest.main()
