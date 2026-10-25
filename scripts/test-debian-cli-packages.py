#!/usr/bin/env python3
"""Test license paths shared through package dependency symlinks."""

import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class DebianSourceUrlTests(unittest.TestCase):
    def test_source_download_url_defaults_to_the_main_archive(self) -> None:
        source = {"filename": "pool/main/c/curl/curl_1.0_amd64.deb"}
        self.assertEqual(
            BUILDER.source_download_url(source),
            "https://deb.debian.org/debian/pool/main/c/curl/curl_1.0_amd64.deb",
        )

    def test_source_download_url_rejects_unsafe_paths(self) -> None:
        for source in (
            {"filename": "pool/main/../private.deb"},
            {"filename": "/tmp/package.deb"},
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                BUILDER.source_download_url(source)


class WorkstationManifestTests(unittest.TestCase):
    def test_workstation_packages_are_checksum_pinned(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "data/debian-workstation-packages.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["suite"], "trixie")
        self.assertEqual(manifest["architecture"], "amd64")
        packages = manifest["packages"]
        names = {package["name"] for package in packages}
        self.assertEqual(
            names,
            {
                "ffmpeg",
                "dhex",
                "hexcurse",
                "hexer",
                "libavc1394-0",
                "libxcb-cursor0",
                "libavdevice61",
                "libdc1394-25",
                "libdisplay-info2",
                "libarchive13t64",
                "libiec61883-0",
                "liblua5.2-0",
                "libm17n-0",
                "libotf1",
                "libraw1394-11",
                "libsixel1",
                "libva-wayland2",
                "libxpresent1",
                "m17n-db",
                "mpv",
                "rofi",
                "suckless-tools",
                "sxhkd",
                "x11vnc",
                "xmahjongg",
                "vbindiff",
            },
        )
        package_by_name = {package["name"]: package for package in packages}
        self.assertEqual(
            set(package_by_name["libavdevice61"]["dependencies"]),
            {
                "libavc1394-0",
                "libdc1394-25",
                "libiec61883-0",
                "libraw1394-11",
            },
        )
        self.assertIn(
            "libraw1394-11", package_by_name["libdc1394-25"]["dependencies"]
        )
        self.assertEqual(package_by_name["rofi"]["dependencies"], ["libxcb-cursor0"])
        self.assertTrue(
            {"libarchive13t64", "libdisplay-info2"}
            <= set(package_by_name["mpv"]["dependencies"])
        )
        self.assertEqual(
            set(package_by_name["libm17n-0"]["dependencies"]),
            {"libotf1", "m17n-db"},
        )

        sources = manifest["sources"]
        for package in packages:
            for source_name in package["debian_packages"]:
                with self.subTest(package=package["name"], source=source_name):
                    source = sources[source_name]
                    self.assertTrue(source["filename"].startswith("pool/"))
                    self.assertNotIn("..", source["filename"].split("/"))
                    self.assertRegex(source["sha256"], re.compile(r"^[0-9a-f]{64}$"))

        positions = {package["name"]: index for index, package in enumerate(packages)}
        for package in packages:
            for dependency in package.get("dependencies", []):
                if dependency in positions:
                    self.assertLess(positions[dependency], positions[package["name"]])

        metadata = json.loads(
            (Path(__file__).resolve().parents[1] / "data/package-metadata.json")
            .read_text(encoding="utf-8")
        )["packages"]
        nullable_author_profiles = {
            "ffmpeg",
            "libarchive13t64",
            "libavdevice61",
            "libdisplay-info2",
            "libiec61883-0",
            "libraw1394-11",
            "libva-wayland2",
            "libxpresent1",
            "mpv",
            "hexcurse",
            "hexer",
        }
        for package in packages:
            with self.subTest(credits=package["name"]):
                entry = metadata[package["name"]]
                self.assertTrue(entry["author"])
                self.assertTrue(entry["git_repo"].startswith("https://"))
                if package["name"] in nullable_author_profiles:
                    self.assertIsNone(entry["git_author_url"])
                else:
                    self.assertTrue(entry["git_author_url"].startswith("https://"))
                self.assertEqual(
                    {item["name"] for item in entry["packagers"]},
                    {"arkana-team", "debarkak"},
                )

    def test_hex_tools_batch_has_pinned_sources_and_upstream_credits(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-workstation-packages.json").read_text(
                encoding="utf-8"
            )
        )
        batch = json.loads(
            (repo_root / "data/debian-workstation-packages-batch-02.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {"dhex", "hexcurse", "hexer", "vbindiff"}
        expected_authors = {
            "dhex": "Thomas Dettbarn",
            "hexcurse": "James Stephenson and Lonny Gomes",
            "hexer": "Sascha Demetrio",
            "vbindiff": "Christopher J. Madsen",
        }
        expected_repositories = {
            "dhex": "https://salsa.debian.org/debian/dhex",
            "hexcurse": "https://github.com/LonnyGomes/hexcurse",
            "hexer": "https://gitlab.com/hexer/hexer",
            "vbindiff": "https://github.com/madsen/vbindiff",
        }
        expected_repository_types = {
            "dhex": "packaging",
            "hexcurse": "upstream",
            "hexer": "upstream",
            "vbindiff": "upstream",
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), expected)

        for name in expected:
            package = batch_packages[name]
            self.assertEqual(package, manifest_packages[name])
            self.assertEqual(batch["sources"][name], manifest["sources"][name])
            self.assertRegex(batch["sources"][name]["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(batch["sources"][name]["filename"].startswith("pool/"))
            self.assertEqual(package["debian_packages"], [name])
            self.assertTrue(package["license_path"].endswith("/copyright"))
            self.assertEqual(metadata[name]["author"], expected_authors[name])
            self.assertEqual(
                metadata[name]["git_repo"], expected_repositories[name]
            )
            self.assertEqual(
                metadata[name]["git_repo_type"], expected_repository_types[name]
            )
            self.assertEqual(
                {item["name"] for item in metadata[name]["packagers"]},
                {"arkana-team", "debarkak"},
            )

        self.assertEqual(
            metadata["dhex"]["git_author_url"], "https://www.dettus.net/"
        )

        self.assertEqual(
            set(manifest_packages["dhex"]["dependencies"]),
            {"ncurses-compat-libs", "ncurses-libs"},
        )
        self.assertEqual(
            set(manifest_packages["hexcurse"]["dependencies"]),
            {"ncurses-compat-libs", "ncurses-libs"},
        )
        self.assertEqual(manifest_packages["hexer"]["dependencies"], ["ncurses-libs"])
        self.assertEqual(
            set(manifest_packages["vbindiff"]["dependencies"]),
            {"gcc-libs", "ncurses-compat-libs", "ncurses-libs"},
        )

    def test_hexyl_metadata_identifies_its_upstream_author(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]["hexyl"]

        self.assertEqual(metadata["author"], "David Peter")
        self.assertEqual(metadata["git_repo"], "https://github.com/sharkdp/hexyl")
        self.assertEqual(metadata["git_author_url"], "https://github.com/sharkdp")


class UpstreamDownloadTests(unittest.TestCase):
    url = "https://example.invalid/release.tar.gz"
    content = b"pinned release\n"
    checksum = hashlib.sha256(content).hexdigest()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir(mode=0o700)
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        patcher = mock.patch.object(
            UPSTREAM_BUILDER.urllib.request,
            "urlopen",
            return_value=io.BytesIO(self.content),
        )
        self.urlopen = patcher.start()
        self.addCleanup(patcher.stop)

    def download(self, cache: Path | None = None) -> Path:
        return UPSTREAM_BUILDER.download(
            self.url, self.checksum, cache or self.cache, self.work
        )

    def test_legacy_temporary_symlink_cannot_overwrite_another_file(self) -> None:
        victim = self.root / "victim"
        victim.write_bytes(b"keep this file")
        (self.cache / "release.tar.gz.download").symlink_to(victim)

        snapshot = self.download()

        self.assertEqual(snapshot.read_bytes(), self.content)
        self.assertEqual(victim.read_bytes(), b"keep this file")

    def test_cache_is_created_private_and_verified_download_is_reused(self) -> None:
        self.cache.rmdir()
        previous_umask = os.umask(0)
        try:
            snapshot = self.download()
        finally:
            os.umask(previous_umask)

        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        target = self.cache / "release.tar.gz"
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(snapshot.read_bytes(), self.content)
        self.assertEqual(self.download(), snapshot)
        self.urlopen.assert_called_once()
        self.assertEqual(list(self.cache.iterdir()), [target])

    def test_final_cache_symlink_is_replaced_without_changing_its_target(self) -> None:
        victim = self.root / "victim"
        victim.write_bytes(self.content)
        target = self.cache / "release.tar.gz"
        target.symlink_to(victim)

        snapshot = self.download()
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_bytes(), self.content)
        self.assertEqual(snapshot.read_bytes(), self.content)
        self.assertEqual(victim.read_bytes(), self.content)
        self.urlopen.assert_called_once()

    def test_writable_cache_is_rejected_before_network_or_file_changes(self) -> None:
        target = self.cache / "release.tar.gz"
        target.write_bytes(self.content)
        for mode in (0o720, 0o702, 0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with self.assertRaises(ValueError):
                    self.download()
                self.assertEqual(target.read_bytes(), self.content)
        self.urlopen.assert_not_called()

    def test_foreign_owned_cache_is_rejected(self) -> None:
        lstat = Path.lstat

        def foreign_owner(path, *args, **kwargs):
            metadata = lstat(path, *args, **kwargs)
            if path == self.cache:
                return mock.Mock(st_mode=metadata.st_mode, st_uid=os.geteuid() + 1)
            return metadata

        with (
            mock.patch.object(Path, "lstat", foreign_owner),
            self.assertRaises(ValueError),
        ):
            self.download()
        self.urlopen.assert_not_called()

    def test_symlink_and_writable_parent_are_rejected(self) -> None:
        link = self.root / "cache-link"
        link.symlink_to(self.cache, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.download(link)
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            self.download()
        self.urlopen.assert_not_called()

    def test_checksum_mismatch_is_never_published_and_temporary_is_removed(self) -> None:
        self.urlopen.return_value = io.BytesIO(b"unverified data")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.download()
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_retry_discards_partial_response_before_publishing(self) -> None:
        class InterruptedResponse(io.BytesIO):
            def read(self, size=-1):
                chunk = super().read(size)
                if not chunk:
                    raise TimeoutError("interrupted download")
                return chunk

        self.urlopen.side_effect = [
            InterruptedResponse(b"partial response longer than the real release"),
            io.BytesIO(self.content),
        ]
        with mock.patch.object(UPSTREAM_BUILDER.time, "sleep"):
            snapshot = self.download()
        self.assertEqual(snapshot.read_bytes(), self.content)
        self.assertEqual((self.cache / "release.tar.gz").read_bytes(), self.content)
        self.assertEqual(self.urlopen.call_count, 2)
        self.assertEqual(list(self.cache.iterdir()), [self.cache / "release.tar.gz"])

    def test_exhausted_retries_remove_temporary_file(self) -> None:
        self.urlopen.side_effect = UPSTREAM_BUILDER.urllib.error.URLError("offline")
        with (
            mock.patch.object(UPSTREAM_BUILDER.time, "sleep"),
            self.assertRaises(UPSTREAM_BUILDER.urllib.error.URLError),
        ):
            self.download()
        self.assertEqual(self.urlopen.call_count, 4)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_disk_error_removes_temporary_file(self) -> None:
        with (
            mock.patch.object(
                UPSTREAM_BUILDER.shutil, "copyfileobj", side_effect=OSError("disk full")
            ),
            self.assertRaises(OSError),
        ):
            self.download()
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_build_rejects_cache_symlink_before_resolving_it(self) -> None:
        link = self.root / "cache-link"
        link.symlink_to(self.cache, target_is_directory=True)
        args = UPSTREAM_BUILDER.argparse.Namespace(
            repo_root=self.root,
            source_cache=link,
            staging=self.root,
            packages=["pfetch"],
        )
        with (
            mock.patch.object(UPSTREAM_BUILDER, "archive_owners", return_value=set()),
            self.assertRaises(ValueError),
        ):
            UPSTREAM_BUILDER.build(args)
        self.urlopen.assert_not_called()


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

    def test_openssh_server_packages_keep_the_client_dependency_chain(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        packages = {package["name"]: package for package in manifest["packages"]}
        server = packages["openssh-server"]
        sftp = packages["openssh-sftp-server"]
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-12.json").read_text(
                encoding="utf-8"
            )
        )
        batch_packages = {package["name"]: package for package in batch["packages"]}
        self.assertEqual(
            set(batch_packages),
            {"libwtmpdb0", "openssh-server", "openssh-sftp-server"},
        )
        for name, package in batch_packages.items():
            self.assertEqual(packages[name], package)
        for name, source in batch["sources"].items():
            self.assertEqual(manifest["sources"][name], source)

        self.assertIn("openssh-client", server["dependencies"])
        self.assertIn("openssh-sftp-server", server["dependencies"])
        self.assertIn("libwtmpdb0", server["dependencies"])
        self.assertIn("openssh-client", sftp["dependencies"])
        self.assertEqual(server["version"], sftp["version"])

        for name, package in (("openssh-server", server), ("openssh-sftp-server", sftp)):
            self.assertEqual(package["debian_version"], f"1:{package['version']}")
            self.assertEqual(package["license_source_package"], "openssh-client")
            self.assertEqual(
                package["license_source_path"],
                "usr/share/doc/openssh-client/copyright",
            )
            self.assertIn(package["license_source_package"], package["dependencies"])
            source = manifest["sources"][package["debian_packages"][0]]
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(package["version"], source["filename"])
            package_metadata = metadata[name]
            self.assertEqual(
                package_metadata["author"],
                "Tatu Ylonen, Markus Friedl, Theo de Raadt, Niels Provos, "
                "Dug Song, and OpenSSH contributors",
            )
            self.assertEqual(
                package_metadata["git_author_url"], "https://github.com/openssh"
            )
            self.assertEqual(package_metadata["git_repo_type"], "upstream")
            self.assertEqual(
                package_metadata["git_repo"],
                "https://github.com/openssh/openssh-portable",
            )
            self.assertEqual(
                {item["name"] for item in package_metadata["packagers"]},
                {"arkana-team", "debarkak"},
            )

        library = batch_packages["libwtmpdb0"]
        self.assertEqual(library["version"], "0.73.0-3+deb13u1")
        self.assertEqual(library["debian_version"], library["version"])
        library_source = manifest["sources"]["libwtmpdb0"]
        self.assertEqual(library_source["version"], library["version"])
        self.assertEqual(
            library_source["filename"],
            "pool/main/w/wtmpdb/libwtmpdb0_0.73.0-3+deb13u1_amd64.deb",
        )
        self.assertRegex(library_source["sha256"], r"^[0-9a-f]{64}$")
        library_metadata = metadata["libwtmpdb0"]
        self.assertEqual(library_metadata["author"], "Thorsten Kukuk")
        self.assertEqual(
            library_metadata["git_repo"], "https://github.com/thkukuk/wtmpdb"
        )
        self.assertEqual(
            library_metadata["git_author_url"], "https://github.com/thkukuk"
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


class ManualPageWorkstationPackageTests(unittest.TestCase):
    def test_batch_10_pins_man_db_and_credits_its_upstream_maintainer(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-10.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_source = {
            "architecture": "amd64",
            "filename": "pool/main/m/man-db/man-db_2.13.1-1_amd64.deb",
            "sha256": "d5a407221a0d59845484b5343a37ec6374102e949353e4b158eaa2cbaf27aa5a",
            "version": "2.13.1-1",
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}

        self.assertEqual(set(batch_packages), {"man-db"})
        self.assertEqual(batch_packages["man-db"], manifest_packages["man-db"])
        self.assertEqual(batch["sources"]["man-db"], expected_source)
        self.assertEqual(manifest["sources"]["man-db"], expected_source)
        self.assertEqual(
            metadata["man-db"]["author"],
            "Colin Watson and the man-db contributors",
        )
        self.assertEqual(
            metadata["man-db"]["git_repo"], "https://gitlab.com/man-db/man-db"
        )
        self.assertEqual(
            metadata["man-db"]["git_author_url"], "https://gitlab.com/cjwatson"
        )
        self.assertEqual(
            {item["name"] for item in metadata["man-db"]["packagers"]},
            {"arkana-team", "debarkak"},
        )


class ClassicUnixWorkstationPackageTests(unittest.TestCase):
    def test_batch_11_pins_fortune_units_and_lnav_with_source_credits(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-11.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_sources = {
            "fortune-mod": {
                "architecture": "amd64",
                "filename": "pool/main/f/fortune-mod/fortune-mod_1.99.1-9_amd64.deb",
                "sha256": "866b82c51f269bd5bdc29134ec5407564f597a3ba6d9a5dfb8d5a0e34b6f205e",
                "version": "1:1.99.1-9",
            },
            "fortunes-min": {
                "architecture": "all",
                "filename": "pool/main/f/fortune-mod/fortunes-min_1.99.1-9_all.deb",
                "sha256": "25d0560c014229f5736e377a4d343cb8e058a75466fc4ffee6be17ded67ea3e9",
                "version": "1:1.99.1-9",
            },
            "units": {
                "architecture": "amd64",
                "filename": "pool/main/u/units/units_2.24-1+deb13u1_amd64.deb",
                "sha256": "d7dc5caa0ed463f5ecc247c4067a4504088bac9eedba8ad2ff355aefde936406",
                "version": "2.24-1+deb13u1",
            },
            "lnav": {
                "architecture": "amd64",
                "filename": "pool/main/l/lnav/lnav_0.12.4-1+deb13u1_amd64.deb",
                "sha256": "49048278d87d28f1947eec96403635945d7c338493c8d419efd4f2e2ce488978",
                "version": "0.12.4-1+deb13u1",
            },
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}

        self.assertEqual(set(batch_packages), set(expected_sources))
        for name, source in expected_sources.items():
            self.assertEqual(batch_packages[name], manifest_packages[name])
            self.assertEqual(batch["sources"][name], source)
            self.assertEqual(manifest["sources"][name], source)
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                {item["name"] for item in metadata[name]["packagers"]},
                {"arkana-team", "debarkak"},
            )

        self.assertEqual(batch_packages["fortune-mod"]["dependencies"], ["fortunes-min"])
        self.assertEqual(
            batch_packages["fortune-mod"]["symlinks"],
            [["usr/bin/fortune", "../games/fortune"]],
        )
        self.assertEqual(
            metadata["fortune-mod"]["git_repo"],
            "https://github.com/shlomif/fortune-mod",
        )
        self.assertEqual(metadata["fortune-mod"]["git_author_url"], "https://github.com/shlomif")
        self.assertEqual(metadata["units"]["author"], "Adrian Mariano")
        self.assertEqual(metadata["units"]["git_repo_type"], "packaging")
        self.assertIsNone(metadata["units"]["git_author_url"])
        self.assertEqual(metadata["lnav"]["author"], "Tim Stack and lnav contributors")
        self.assertEqual(metadata["lnav"]["git_author_url"], "https://github.com/tstack")


class InfoPackageTests(unittest.TestCase):
    def test_batch_13_pins_gnu_info_packages(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-13.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_sources = {
            "install-info": {
                "architecture": "amd64",
                "filename": "pool/main/t/texinfo/install-info_7.1.1-1+b1_amd64.deb",
                "sha256": "96534736eea475b3be51d94d4969e29ab27d80fb80627d50ead9f10c24848e09",
                "version": "7.1.1-1+b1",
            },
            "info": {
                "architecture": "amd64",
                "filename": "pool/main/t/texinfo/info_7.1.1-1+b1_amd64.deb",
                "sha256": "3e71742dc4f2d32d93572f200a9125e6e625b88ca664729e587bfc46001fbdc6",
                "version": "7.1.1-1+b1",
            },
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}

        self.assertEqual(set(batch_packages), set(expected_sources))
        for name, source in expected_sources.items():
            self.assertEqual(batch_packages[name], manifest_packages[name])
            self.assertEqual(batch["sources"][name], source)
            self.assertEqual(manifest["sources"][name], source)
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                {item["name"] for item in metadata[name]["packagers"]},
                {"arkana-team", "debarkak"},
            )

        self.assertEqual(batch_packages["info"]["dependencies"], ["install-info"])
        for name in ("info", "install-info"):
            self.assertEqual(metadata[name]["author"], "GNU Texinfo contributors")
            self.assertEqual(metadata[name]["git_repo"], "https://gnu.googlesource.com/texinfo/")
            self.assertIsNone(metadata[name]["git_author_url"])


class MailClientPackageTests(unittest.TestCase):
    def test_batch_14_pins_alpine_and_s_nail_with_upstream_credits(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-14.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_sources = {
            "alpine": {
                "architecture": "amd64",
                "filename": "pool/main/a/alpine/alpine_2.26+dfsg-3_amd64.deb",
                "sha256": "7712f180873fb67a674ae0ff59ae06b49fdf03f06d533578f7c81b43109f498d",
                "version": "2.26+dfsg-3",
            },
            "s-nail": {
                "architecture": "amd64",
                "filename": "pool/main/s/s-nail/s-nail_14.9.25-1_amd64.deb",
                "sha256": "06f48c7dbc02bfcbb3d0bc83d52231f98d5619ff0b8b1c1bfb75afb172d771b5",
                "version": "14.9.25-1",
            },
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}

        self.assertEqual(set(batch_packages), set(expected_sources))
        for name, source in expected_sources.items():
            self.assertEqual(batch_packages[name], manifest_packages[name])
            self.assertEqual(batch["sources"][name], source)
            self.assertEqual(manifest["sources"][name], source)
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(metadata[name]["git_repo_type"], "upstream")
            self.assertTrue(metadata[name]["git_author_url"].startswith("https://"))
            self.assertEqual(
                {item["name"] for item in metadata[name]["packagers"]},
                {"arkana-team", "debarkak"},
            )

        self.assertEqual(metadata["alpine"]["author"], "Eduardo Chappa and Alpine contributors")
        self.assertEqual(metadata["alpine"]["git_repo"], "https://repo.or.cz/alpine.git")
        self.assertEqual(metadata["s-nail"]["author"], "Steffen Nurpmeso")
        self.assertEqual(metadata["s-nail"]["git_repo"], "https://git.sdaoden.eu/scm/s-nail.git")


class ClassicWorkstationPackagesBatchTests(unittest.TestCase):
    def test_batch_15_pins_mail_and_console_tools(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-15.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_sources = {
            "dialog": (
                "amd64",
                "pool/main/d/dialog/dialog_1.3-20250116-1_amd64.deb",
                "31a7870a8906b2bbe90b4aaf09cf2eaa18dbd4939941902ca26f78b799dfa4b9",
                "1.3-20250116-1",
            ),
            "gpm": (
                "amd64",
                "pool/main/g/gpm/gpm_1.20.7-11+b2_amd64.deb",
                "e6c5fd0e376597d2153166bc1b5701270f69596382ce78e36e11ba031d65e003",
                "1.20.7-11+b2",
            ),
            "isync": (
                "amd64",
                "pool/main/i/isync/isync_1.5.1-1_amd64.deb",
                "1d7eea9d7557a36054f1e221a4d448d6c4c3a203267cf08a1e503ed7fcd31fe7",
                "1.5.1-1",
            ),
            "libdialog15": (
                "amd64",
                "pool/main/d/dialog/libdialog15_1.3-20250116-1_amd64.deb",
                "62546547d12ee0170005204ac8df00233e3f4f58cde2ef059bff13c4d6a696cb",
                "1.3-20250116-1",
            ),
            "libsecret-1-0": (
                "amd64",
                "pool/main/libs/libsecret/libsecret-1-0_0.21.7-1_amd64.deb",
                "949c5428cd3427486ac6c4dfc6d9400ce786c504c02334acc903c873aa52d14f",
                "0.21.7-1",
            ),
            "libsecret-common": (
                "all",
                "pool/main/libs/libsecret/libsecret-common_0.21.7-1_all.deb",
                "ce336a4ea6d27bfe85c195b88ab9a8eb95ce197228648634b8ace35ddb9bd808",
                "0.21.7-1",
            ),
            "msmtp": (
                "amd64",
                "pool/main/m/msmtp/msmtp_1.8.28-3_amd64.deb",
                "403822d579c8be76065bb8aafd5934ecfd2faf97f7738bb3aebe801710b787d7",
                "1.8.28-3",
            ),
            "notmuch": (
                "amd64",
                "pool/main/n/notmuch/notmuch_0.39-1_amd64.deb",
                "f48553940896c3c408d43bf1305d6687cea0d4c91824206082584b4bebef2da3",
                "0.39-1",
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}

        self.assertEqual(set(batch_packages), set(expected_sources))
        self.assertNotIn("fd-find", batch_packages)
        self.assertNotIn("whiptail", batch_packages)
        self.assertIn("fd", manifest_packages)
        self.assertNotIn("fd-find", metadata)
        self.assertNotIn("whiptail", metadata)
        newt_recipe = (repo_root / "packages/recipes/newt-tools/ARKPKG").read_text(
            encoding="utf-8"
        )
        self.assertIn("Provides: whiptail=/usr/bin/whiptail", newt_recipe)
        for name, (architecture, filename, checksum, version) in expected_sources.items():
            expected = {
                "architecture": architecture,
                "filename": filename,
                "sha256": checksum,
                "version": version,
            }
            self.assertEqual(batch_packages[name], manifest_packages[name])
            self.assertEqual(batch["sources"][name], expected)
            self.assertEqual(manifest["sources"][name], expected)
            self.assertEqual(metadata[name]["packagers"][0]["name"], "arkana-team")
            self.assertEqual(metadata[name]["packagers"][1]["name"], "debarkak")
            self.assertTrue(metadata[name]["git_repo"].startswith("https://"))
            self.assertTrue(metadata[name]["git_author_url"].startswith("https://"))
        self.assertEqual(batch_packages["dialog"]["dependencies"], ["libdialog15"])
        self.assertEqual(batch_packages["libsecret-1-0"]["dependencies"], ["libsecret-common"])

    def test_batch_16_pins_optional_nscde_session_tools(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-16.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_sources = {
            "dunst": (
                "amd64",
                "pool/main/d/dunst/dunst_1.12.2-1_amd64.deb",
                "88cc8e6e31632bcdec59526df101aeaf294cde5673e2e5fd9506c3b28de7a461",
                "1.12.2-1",
            ),
            "libconfig11": (
                "amd64",
                "pool/main/libc/libconfig/libconfig11_1.7.3-2_amd64.deb",
                "0609351b0069ef47f9ade42625a760f8889aeb55bd7107339f61a4abf9a4068e",
                "1.7.3-2",
            ),
            "libxcb-image0": (
                "amd64",
                "pool/main/x/xcb-util-image/libxcb-image0_0.4.0-2+b2_amd64.deb",
                "81e2654e1b1da5cff9ea2bf46970a46eb5c81f1e35b1356691705e8cd6ab3ed6",
                "0.4.0-2+b2",
            ),
            "libxcb-render-util0": (
                "amd64",
                "pool/main/x/xcb-util-renderutil/libxcb-render-util0_0.3.10-1_amd64.deb",
                "9bdd6247e45cea5047e4da883dabf168366474fb1b00a37bb194359ca1f975ab",
                "0.3.10-1",
            ),
            "picom": (
                "amd64",
                "pool/main/p/picom/picom_12.5-1_amd64.deb",
                "4d5054faf9ef9af5e16b0720433a30968f17bfb6cae7bc64212492f8cbbc7b52",
                "12.5-1",
            ),
            "xsettingsd": (
                "amd64",
                "pool/main/x/xsettingsd/xsettingsd_1.0.2-1_amd64.deb",
                "4cc6d9a3e2871a28735f84e7ac68eecb7b89e03ee3edc23ded58948ceca72a48",
                "1.0.2-1",
            ),
        }
        expected_authors = {
            "dunst": ("Dunst contributors", "https://github.com/dunst-project/dunst"),
            "libconfig-libs": (
                "Mark A. Lindner",
                "https://github.com/hyperrealm/libconfig",
            ),
            "libxcb-image0": (
                "XCB contributors",
                "https://gitlab.freedesktop.org/xorg/lib/libxcb-image",
            ),
            "libxcb-render-util0": (
                "XCB contributors",
                "https://gitlab.freedesktop.org/xorg/lib/libxcb-render-util",
            ),
            "picom": ("Yuxuan Shui and contributors", "https://github.com/yshui/picom"),
            "xsettingsd": ("Daniel Erat", "https://codeberg.org/derat/xsettingsd"),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected_authors))

        for name, (architecture, filename, checksum, version) in expected_sources.items():
            expected = {
                "architecture": architecture,
                "filename": filename,
                "sha256": checksum,
                "version": version,
            }
            self.assertEqual(batch["sources"][name], expected)
            self.assertEqual(manifest["sources"][name], expected)

        for name, (author, git_repo) in expected_authors.items():
            self.assertEqual(batch_packages[name], manifest_packages[name])
            self.assertEqual(metadata[name]["author"], author)
            self.assertEqual(metadata[name]["git_repo"], git_repo)
            self.assertEqual(metadata[name]["git_repo_type"], "upstream")
            self.assertTrue(metadata[name]["git_author_url"].startswith("https://"))
            self.assertEqual(
                {item["name"] for item in metadata[name]["packagers"]},
                {"arkana-team", "debarkak"},
            )
            recipe = repo_root / "packages/recipes" / name / "ARKPKG"
            self.assertTrue(recipe.is_file(), f"missing recipe for {name}")
            self.assertIn(f"Name: {name}\n", recipe.read_text(encoding="utf-8"))

        self.assertEqual(
            batch_packages["picom"]["dependencies"],
            ["libconfig-libs", "libxcb-image0", "libxcb-render-util0"],
        )

    def test_batch_17_pins_optional_gpu_and_x11_session_tools(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-17.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_sources = {
            "libproc2-0": (
                "amd64",
                "pool/main/p/procps/libproc2-0_4.0.4-9_amd64.deb",
                "c9d61caab1b2ddfc3f17edb95be31b9809fcb2daf27d853dff615bc4ebd08b4b",
                "4.0.4-9",
            ),
            "intel-gpu-tools": (
                "amd64",
                "pool/main/i/intel-gpu-tools/intel-gpu-tools_2.0-1_amd64.deb",
                "419f9c9fe7978d01700d71a0ef1b4544b979daf9ee1aa72838169a21e3aa2fe1",
                "2.0-1",
            ),
            "nvtop": (
                "amd64",
                "pool/main/n/nvtop/nvtop_3.2.0-1_amd64.deb",
                "805038ff92ce444bfe3a0c15a8e08a17e565fba011297c0c482152d7e6640901",
                "3.2.0-1",
            ),
            "radeontop": (
                "amd64",
                "pool/main/r/radeontop/radeontop_1.4-2_amd64.deb",
                "218edd2934625b547ad1eb9a7bf229a629f36583f32880640473ac11515fe7f9",
                "1.4-2",
            ),
            "xss-lock": (
                "amd64",
                "pool/main/x/xss-lock/xss-lock_0.3.0+git20230128.0c562b-1+b2_amd64.deb",
                "47f81d5e5e708b27d5975687bef44636d3ed774e3903d3d6073b58156618b659",
                "0.3.0+git20230128.0c562b-1+b2",
            ),
        }
        expected_repositories = {
            "libproc2-0": "https://gitlab.com/procps-ng/procps",
            "intel-gpu-tools": "https://gitlab.freedesktop.org/drm/igt-gpu-tools",
            "nvtop": "https://github.com/Syllo/nvtop",
            "radeontop": "https://github.com/clbr/radeontop",
            "xss-lock": "https://github.com/wavexx/xss-lock",
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected_sources))

        for name, (architecture, filename, checksum, version) in expected_sources.items():
            source = {
                "architecture": architecture,
                "filename": filename,
                "sha256": checksum,
                "version": version,
            }
            self.assertEqual(batch["sources"][name], source)
            self.assertEqual(manifest["sources"][name], source)
            self.assertEqual(batch_packages[name], manifest_packages[name])
            self.assertIsNone(metadata[name]["author"])
            self.assertIsNone(metadata[name]["git_author_url"])
            self.assertEqual(metadata[name]["git_repo"], expected_repositories[name])
            self.assertEqual(metadata[name]["git_repo_type"], "upstream")
            self.assertEqual(
                {item["name"] for item in metadata[name]["packagers"]},
                {"arkana-team", "debarkak"},
            )
            archive = repo_root / "packages" / f"{name}-{batch_packages[name]['version']}-x86_64.ark"
            self.assertTrue(archive.is_file(), f"missing built archive for {name}")
            recipe = repo_root / "packages/recipes" / name / "ARKPKG"
            self.assertTrue(recipe.is_file(), f"missing recipe for {name}")
            self.assertIn(f"Name: {name}\n", recipe.read_text(encoding="utf-8"))

        self.assertEqual(batch_packages["intel-gpu-tools"]["dependencies"], ["libproc2-0"])
        self.assertEqual(batch_packages["libproc2-0"]["debian_version"], "2:4.0.4-9")
        self.assertEqual(batch_packages["libproc2-0"]["version"], "4.0.4-9")


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
