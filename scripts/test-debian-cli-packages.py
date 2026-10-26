#!/usr/bin/env python3
"""Test license paths shared through package dependency symlinks."""

import argparse
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
from unittest.mock import patch
from urllib.parse import urlparse

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
    def test_batch_catalogue_uses_homepage_git_repositories(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        batch = json.loads(
            (repo_root / "data/debian-workstation-packages-batch-07.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        git_hosts = {
            "github.com",
            "gitlab.com",
            "gitlab.freedesktop.org",
            "codeberg.org",
            "git.sr.ht",
        }

        for package in batch["packages"]:
            homepage = package.get("url", "")
            if urlparse(homepage).hostname not in git_hosts:
                continue
            with self.subTest(package=package["name"]):
                self.assertEqual(
                    metadata[package["name"]]["git_repo"], homepage.rstrip("/")
                )
                self.assertEqual(
                    metadata[package["name"]]["git_repo_type"], "upstream"
                )

    def test_workstation_packages_are_checksum_pinned(self) -> None:
        """Validate workstation package pins, metadata, and dependency order."""
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "data/debian-workstation-packages.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        batch_manifest_path = (
            Path(__file__).resolve().parents[1]
            / "data/debian-workstation-packages-batch-07.json"
        )
        batch_names = {
            package["name"]
            for package in json.loads(
                batch_manifest_path.read_text(encoding="utf-8")
            )["packages"]
        }

        self.assertEqual(manifest["suite"], "trixie")
        self.assertEqual(manifest["architecture"], "amd64")
        packages = manifest["packages"]
        names = {package["name"] for package in packages}
        self.assertEqual(
            names,
            {
                "astyle",
                "alsamixergui",
                "atool",
                "bmake",
                "boxes",
                "cscope",
                "ffmpeg",
                "dhex",
                "hexcurse",
                "hexer",
                "freesweep",
                "global",
                "greed",
                "id3v2",
                "jove",
                "libxaw7",
                "libid3-3.8.3v5",
                "libavc1394-0",
                "libxcb-cursor0",
                "libavdevice61",
                "libdc1394-25",
                "libdisplay-info2",
                "libglew2.2",
                "libarchive13t64",
                "libiec61883-0",
                "liblua5.2-0",
                "libm17n-0",
                "libotf1",
                "libslopy7.6",
                "libraw1394-11",
                "libsixel1",
                "libva-wayland2",
                "libxpresent1",
                "m17n-db",
                "moon-buggy",
                "mpv",
                "nudoku",
                "gopher",
                "pcal",
                "rename",
                "rofi",
                "robotfindskitten",
                "sc",
                "suckless-tools",
                "slop",
                "maim",
                "nsxiv",
                "xbindkeys",
                "sxhkd",
                "tty-solitaire",
                "unrar-free",
                "vitetris",
                "x11vnc",
                "xmbmon",
                "x2x",
                "xrootconsole",
                "tigervnc-viewer",
                "xfonts-100dpi",
                "xfonts-75dpi",
                "xfonts-base",
                "xfonts-encodings",
                "xfonts-utils",
                "xmahjongg",
                "vbindiff",
                "w3m-img",
                "xcolors",
            }
            | batch_names,
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
        self.assertEqual(package_by_name["atool"]["dependencies"], ["perl"])
        self.assertEqual(package_by_name["rename"]["dependencies"], ["perl"])
        expected_font_alias_symlinks = {
            "xfonts-base": [
                [
                    "usr/share/fonts/X11/misc/fonts.alias",
                    "../../../../../etc/X11/fonts/misc/xfonts-base.alias",
                ]
            ],
            "xfonts-75dpi": [
                [
                    "usr/share/fonts/X11/75dpi/fonts.alias",
                    "../../../../../etc/X11/fonts/75dpi/xfonts-75dpi.alias",
                ]
            ],
            "xfonts-100dpi": [
                [
                    "usr/share/fonts/X11/100dpi/fonts.alias",
                    "../../../../../etc/X11/fonts/100dpi/xfonts-100dpi.alias",
                ]
            ],
        }
        for name, symlinks in expected_font_alias_symlinks.items():
            self.assertEqual(package_by_name[name]["symlinks"], symlinks)
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
        expected_author_profiles = {
            "9menu": "https://github.com/arnoldrobbins",
            "autorandr": "https://github.com/phillipberndt",
            "xdo": "https://github.com/baskerville",
            "ansiweather": "https://github.com/fcambus",
            "ddgr": "https://github.com/jarun",
            "dfc": "https://github.com/rolinh",
            "git-extras": "https://github.com/tj",
            "jqp": "https://github.com/noahgorstein",
            "2048": "https://github.com/mevdschee",
            "nbsdgames": "https://github.com/abakh",
            "cbonsai": "https://gitlab.com/jallbrit",
            "libfile-next-perl": "https://github.com/petdance",
        }
        for name, profile_url in expected_author_profiles.items():
            self.assertEqual(metadata[name]["git_author_url"], profile_url)

        nullable_author_profiles = {
            "ffmpeg",
            "alsamixergui",
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
            "jove",
            "libxaw7",
            "pcal",
            "sc",
            "w3m-img",
            "xcolors",
            "xmbmon",
            "x2x",
            "xrootconsole",
        } | (batch_names - set(expected_author_profiles))
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
        """Validate pinned archives and upstream credits for hex-tool packages."""
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

        self.assertEqual(manifest_packages["w3m-img"]["dependencies"], ["w3m"])
        self.assertEqual(manifest_packages["xcolors"]["dependencies"], ["libxaw7"])

    def test_classic_workstation_batch_has_pinned_sources_and_credits(self) -> None:
        """Validate pinned workstation sources and their upstream credits."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-workstation-packages.json").read_text(
                encoding="utf-8"
            )
        )
        batch = json.loads(
            (repo_root / "data/debian-workstation-packages-batch-03.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "jove": (
                "4.17.5.4-1",
                "c55c28891cb409b358b527dbcea03c0d58c4cee7e67c680670c90540f5544854",
                "Jonathan Payne",
                "https://github.com/jonmacs/jove",
                "upstream",
            ),
            "sc": (
                "7.16-1.1.2-1",
                "d2771111a9da871b402683cd5483b9e4b784fd477cbd57657c54cb288355f157",
                "James Gosling and Mark Weiser, with contributions by Robert Bond and others",
                "https://github.com/n-t-roff/sc",
                "upstream",
            ),
            "libxaw7": (
                "2:1.0.16-1",
                "e79f3ab51ecbbfd3414a0568a8947ac59a29c30d631e6c7b6e07ea46e688c047",
                "The X Consortium and X.Org contributors",
                "https://gitlab.freedesktop.org/xorg/lib/libxaw",
                "upstream",
            ),
            "pcal": (
                "4.11.0-4+b1",
                "c25973bea3c67469dbdb49bda2d34ee0a90e3eb3967831edf7be105e3e5e09a0",
                "Patrick Wood, Jef Poskanzer, Craig Leres, and contributors",
                "https://salsa.debian.org/debian/pcal",
                "packaging",
            ),
            "w3m-img": (
                "0.5.3+git20230121-2.1",
                "d80216f27aab5c81b601dd9ede59b8630222ba43f7f45dbbb814d0c3e8e96f09",
                "Akinori Ito, Hironori Sakamoto, and Fumitoshi Ukai",
                "https://salsa.debian.org/debian/w3m",
                "packaging",
            ),
            "xcolors": (
                "1.5a-10.1",
                "1f0efd04e4dbadcb696d05520a0afa9d033b2f3393446037f27f6f5cc1e176df",
                "Stephen Gildea",
                "https://salsa.debian.org/debian/xcolor",
                "packaging",
            ),
        }
        all_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected))

        for name, (version, digest, author, git_repo, repo_type) in expected.items():
            with self.subTest(package=name):
                package = batch_packages[name]
                self.assertEqual(package, all_packages[name])
                self.assertEqual(package["version"], version)
                self.assertEqual(batch["sources"][name], manifest["sources"][name])
                self.assertEqual(batch["sources"][name]["sha256"], digest)
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
                self.assertTrue(batch["sources"][name]["filename"].startswith("pool/"))
                self.assertEqual(package["debian_packages"], [name])
                self.assertTrue(package["license_path"].endswith("/copyright"))
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], repo_type)
                self.assertIsNone(metadata[name]["git_author_url"])
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )

        positions = {
            item["name"]: index for index, item in enumerate(manifest["packages"])
        }
        self.assertLess(positions["libxaw7"], positions["xcolors"])

    def test_hexyl_metadata_identifies_its_upstream_author(self) -> None:
        """Verify hexyl's upstream author name and links in the index."""
        repo_root = Path(__file__).resolve().parents[1]
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]["hexyl"]

        self.assertEqual(metadata["author"], "David Peter")
        self.assertEqual(metadata["git_repo"], "https://github.com/sharkdp/hexyl")
        self.assertEqual(metadata["git_author_url"], "https://github.com/sharkdp")

    def test_system_fetch_tools_identify_their_upstream_authors(self) -> None:
        """Keep upstream author names and repository links accurate."""
        repo_root = Path(__file__).resolve().parents[1]
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "fastfetch": (
                "Linus Dierheimer",
                "https://github.com/fastfetch-cli/fastfetch",
                "https://github.com/LinusDierheimer",
            ),
            "neofetch": (
                "Dylan Araps",
                "https://github.com/dylanaraps/neofetch",
                "https://github.com/dylanaraps",
            ),
            "neowofetch": (
                "Azalea Gui",
                "https://github.com/hykilpikonna/hyfetch",
                "https://github.com/hykilpikonna",
            ),
            "nitch": (
                "sfome",
                "https://github.com/ssleert/nitch",
                "https://github.com/ssleert",
            ),
            "pfetch": (
                "Dylan Araps",
                "https://github.com/dylanaraps/pfetch",
                "https://github.com/dylanaraps",
            ),
            "screenfetch": (
                "Brett Bohnenkamper",
                "https://github.com/KittyKatt/screenFetch",
                "https://github.com/KittyKatt",
            ),
        }

        for name, (author, git_repo, git_author_url) in expected.items():
            with self.subTest(package=name):
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], "upstream")
                self.assertEqual(metadata[name]["git_author_url"], git_author_url)
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )

    def test_classic_workstation_batch_04_is_pinned_and_credited(self) -> None:
        """Validate pinned archives and source credits for workstation batch 04."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-workstation-packages.json").read_text(
                encoding="utf-8"
            )
        )
        batch = json.loads(
            (repo_root / "data/debian-workstation-packages-batch-04.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "astyle": (
                "3.1-3+b3",
                "c9d64823182e577eb2c9cc75e8e76de7fe4a4407f3bf8f3a86d073c233698d1e",
            ),
            "atool": (
                "0.39.0-14",
                "84cc879bd99bf927fea54a652673d4ab65105d05318e4da4fb69c08e9aeefbba",
            ),
            "bmake": (
                "20200710-17",
                "7def59c505272cae2235b9b546e494e38ad422821cb4b13c6e7b9bd73c45fd31",
            ),
            "boxes": (
                "2.3.1-1",
                "3f5b05d39df74f3f1af4561dab0262a28211fb46e6f40b3f704741ae50985fa6",
            ),
            "cscope": (
                "15.9-2",
                "dfd2420cf5a8de16bf546c70b5eeb2f7d2825155ff0d1c54cd4d65c5c7e2c248",
            ),
            "freesweep": (
                "1.0.2-2",
                "d15fe209bb89583ee9d9b426d551e219a505c57817690d97f1d9161033195fb5",
            ),
            "greed": (
                "4.3-2",
                "be1e996e81589b2ea085f5c7399a03b5eaa65989172d3d19cdef332ffd0f3ad2",
            ),
            "global": (
                "6.6.13-1",
                "d2547b8d8fffaa4a3bcd56f57dbb04f85d1fd22f34a1f3d173e6e5d70cb91858",
            ),
            "id3v2": (
                "0.1.12+dfsg-7",
                "cb727feb74891d5f354a01ea68c7756bbf6846cb12bd865ecd1e40736e03e0a6",
            ),
            "libid3-3.8.3v5": (
                "3.8.3-18+b3",
                "6a1e7980c7788fce3d0754d21a26693d60c01444580e3c22c6f92b2cc9612e67",
            ),
            "moon-buggy": (
                "1:1.0.51-15",
                "7e46ff0d1d9a83423e42755c9ab079daba590d0dc21cf1347081163e2621e6ec",
            ),
            "nudoku": (
                "2.1.0-0.2",
                "5355f7045cc7388e2eb06582cfeb8364dc949879298263502f275d6a02351ac8",
            ),
            "gopher": (
                "3.0.18",
                "77ad5fe5fff12280da5687873454deea427cf0cbadb388738bd37f434876dc12",
            ),
            "rename": (
                "2.02-1",
                "b3d96a5da8e89e7fce6c9c144a1199c40c29b608bbd62ac72f7e779d088ea537",
            ),
            "robotfindskitten": (
                "2.8284271.702-1",
                "a4ade5ccc5552296df46f3db66b4582f4adada924c9ec7a11bd11ab2a42f1bb6",
            ),
            "tty-solitaire": (
                "1.3.1-1+b1",
                "8688db5b90c2d9ba32683747f966b4f15fc7203389c3172c8a244f78c2791bfc",
            ),
            "unrar-free": (
                "1:0.3.1-1",
                "3481b72c4b0ea60ceba920ed8e6c255084c8c1c0c85ffe2971117305715d0467",
            ),
            "vitetris": (
                "0.59.1-3",
                "5b1fb6539d518b9935c8e59801a793e4b6e493730dc605f8b43c57cceeca0bb4",
            ),
            "xmbmon": (
                "2.05-10",
                "b9e538f7c2cd6194169eaec7afed22cae95aea88477d198a3529c922dbcf5872",
            ),
            "xfonts-encodings": (
                "1:1.0.4-2.2",
                "56143f317d241b6dee0275aa1066df936a776810d3bb83fe58563965d9bbe3d3",
            ),
            "xfonts-utils": (
                "1:7.7+7",
                "36e77ba3fd25a42d4b807808de93eba87c0bd53981ee94d27da4cee502387f35",
            ),
            "xfonts-100dpi": (
                "1:1.0.5",
                "fcb97826afdd312bba1a4d13870be239767065880d523cddd91b882d2a1332ba",
            ),
            "xfonts-75dpi": (
                "1:1.0.5",
                "5f2ba56e3c57e494fab35c4907c4be6026aa6d0e94eecbd37f957c8a6b8746ed",
            ),
            "xfonts-base": (
                "1:1.0.5+nmu1",
                "24446137c63de94283dc808eef438918e243f518629844289a5af50a8e0aec23",
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected))

        for name, (version, digest) in expected.items():
            with self.subTest(package=name):
                package = batch_packages[name]
                self.assertEqual(package, manifest_packages[name])
                self.assertEqual(package["version"], version)
                self.assertEqual(batch["sources"][name], manifest["sources"][name])
                self.assertEqual(batch["sources"][name]["sha256"], digest)
                self.assertTrue(batch["sources"][name]["filename"].startswith("pool/"))
                self.assertTrue(package["license_path"].endswith("/copyright"))
                self.assertTrue(metadata[name]["author"])
                self.assertTrue(metadata[name]["git_repo"].startswith("https://"))
                self.assertIn(
                    metadata[name]["git_repo_type"],
                    {"upstream", "packaging", "arkpkg-recipe"},
                )
                if name == "xmbmon":
                    self.assertIsNone(metadata[name]["git_author_url"])
                else:
                    self.assertTrue(metadata[name]["git_author_url"].startswith("http"))
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )

        self.assertEqual(
            manifest_packages["id3v2"]["dependencies"], ["libid3-3.8.3v5"]
        )
        self.assertEqual(
            metadata["bmake"]["author"],
            "Adam de Boor, Simon J. Gerraty, and NetBSD contributors",
        )
        self.assertEqual(
            metadata["gopher"]["author"],
            "University of Minnesota, John Goerzen, and the Gopher team",
        )
        self.assertEqual(metadata["xmbmon"]["author"], "Yoshifumi R. Shimizu")
        self.assertEqual(
            metadata["xmbmon"]["git_repo"],
            "https://salsa.debian.org/salvage-team/xmbmon.git",
        )
        self.assertEqual(
            metadata["xmbmon"]["git_author_url"],
            None,
        )
        self.assertEqual(
            manifest_packages["xfonts-utils"]["dependencies"],
            ["fonttosfnt", "mkfontscale", "xfonts-encodings"],
        )
        self.assertEqual(
            set(manifest_packages["xfonts-utils"]["exclude_paths"]),
            {
                "usr/bin/bdftruncate",
                "usr/bin/fonttosfnt",
                "usr/bin/mkfontdir",
                "usr/bin/mkfontscale",
                "usr/bin/ucs2any",
            },
        )

    def test_remote_workstation_batch_05_is_pinned_and_credited(self) -> None:
        """Validate batch 05 archive pins, package records, and source credits."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-workstation-packages.json").read_text(
                encoding="utf-8"
            )
        )
        batch = json.loads(
            (repo_root / "data/debian-workstation-packages-batch-05.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "tigervnc-viewer": (
                "1.15.0+dfsg-2.1~deb13u1",
                "4967ae5b5005f531548a73dea293abc7c73cf3f2f35711e3bc7369e84dd62ad7",
                "TigerVNC Team",
                "https://github.com/TigerVNC/tigervnc",
                "upstream",
                "https://github.com/TigerVNC",
            ),
            "x2x": (
                "1.30-12",
                "7ff27eb1a70c77d02d1eb98821ca829a6a5f46496a1e6ddba5e17f4cd592e092",
                "David Chaiken",
                "https://github.com/dottedmag/x2x",
                "upstream",
                None,
            ),
            "xrootconsole": (
                "1:0.6-5",
                "1d610974c5532d7b7dc9bfde11580a701982b9e2cbe1cbde05f7e6c4ca98f3dd",
                "Eric Youngblut",
                "https://salsa.debian.org/debian/xrootconsole.git",
                "packaging",
                None,
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected))

        for name, (version, digest, author, git_repo, repo_type, author_url) in (
            expected.items()
        ):
            with self.subTest(package=name):
                package = batch_packages[name]
                self.assertEqual(package, manifest_packages[name])
                self.assertEqual(package["version"], version)
                self.assertEqual(package["debian_packages"], [name])
                self.assertEqual(batch["sources"][name], manifest["sources"][name])
                self.assertEqual(batch["sources"][name]["sha256"], digest)
                self.assertTrue(batch["sources"][name]["filename"].startswith("pool/"))
                self.assertTrue(package["license_path"].endswith("/copyright"))
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], repo_type)
                self.assertEqual(metadata[name]["git_author_url"], author_url)
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )

        self.assertEqual(manifest_packages["x2x"]["dependencies"], [])
        self.assertEqual(manifest_packages["xrootconsole"]["dependencies"], [])
        self.assertEqual(manifest_packages["tigervnc-viewer"]["dependencies"], [])

    def test_remote_workstation_batch_06_is_pinned_and_credited(self) -> None:
        """Validate X11 utility pins, package records, and upstream credits."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-workstation-packages.json").read_text(
                encoding="utf-8"
            )
        )
        batch = json.loads(
            (repo_root / "data/debian-workstation-packages-batch-06.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "libglew2.2": (
                "2.2.0-4+b2",
                "0bd19a4e495a6e0dccd849c77e7d63117e151de8fcc046447e3fa3f6d70bb0c1",
                "Milan Ikits, Marcelo E. Magallon, and Lev Povalahev",
                "https://github.com/nigels-com/glew",
                "upstream",
                "https://github.com/nigels-com",
            ),
            "libslopy7.6": (
                "7.6-5",
                "a475f3beab332e7da7777c025afedd51e6f4559fb3f6185bcad4bd4fffda2893",
                "Dalton Nell and contributors",
                "https://github.com/naelstrof/slop",
                "upstream",
                "https://github.com/naelstrof",
            ),
            "maim": (
                "5.8.0-1",
                "d872ab3a3a3726067ca44dc02b87ca892a47f585be151d8d4641e5a137b27772",
                "Dalton Nell and contributors",
                "https://github.com/naelstrof/maim",
                "upstream",
                "https://github.com/naelstrof",
            ),
            "nsxiv": (
                "33-1",
                "213007b215de5b77aebac87a1463ad705e3a0ca828feec38fe38d34a307b9336",
                "Bert Münnich and nsxiv contributors",
                "https://codeberg.org/nsxiv/nsxiv",
                "upstream",
                "https://codeberg.org/nsxiv",
            ),
            "slop": (
                "7.6-5",
                "09621d32aa02e0b1ae0bc7d9c2faa5e095957d75d34672f1253781770b0b9f40",
                "Dalton Nell and contributors",
                "https://github.com/naelstrof/slop",
                "upstream",
                "https://github.com/naelstrof",
            ),
            "xbindkeys": (
                "1.8.7-2",
                "5b30ba7ae3f4c6c11fa216664f5f0588b34341c8ac1c6e31a862c045869e0dae",
                "Philippe Brochard and Marcello Mathias Herreshoff",
                "https://www.nongnu.org/xbindkeys/",
                "upstream",
                "https://hocwp.free.fr/",
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected))

        for name, (version, digest, author, git_repo, repo_type, author_url) in (
            expected.items()
        ):
            with self.subTest(package=name):
                package = batch_packages[name]
                self.assertEqual(package, manifest_packages[name])
                self.assertEqual(package["version"], version)
                self.assertEqual(package["debian_packages"], [name])
                self.assertEqual(batch["sources"][name], manifest["sources"][name])
                self.assertEqual(batch["sources"][name]["sha256"], digest)
                self.assertTrue(batch["sources"][name]["filename"].startswith("pool/"))
                self.assertTrue(package["license_path"].endswith("/copyright"))
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], repo_type)
                self.assertEqual(metadata[name]["git_author_url"], author_url)
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )

        self.assertIn("libglew2.2", manifest_packages["libslopy7.6"]["dependencies"])
        self.assertIn("libslopy7.6", manifest_packages["slop"]["dependencies"])
        self.assertIn("slop", manifest_packages["maim"]["dependencies"])
        self.assertNotIn("exclude_paths", manifest_packages["nsxiv"])
        self.assertIn("guile", manifest_packages["xbindkeys"]["dependencies"])

    def test_remote_workstation_batch_07_is_pinned_and_credited(self) -> None:
        """Validate the optional dockapp, X11, CLI, and game package batch."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-workstation-packages.json").read_text(
                encoding="utf-8"
            )
        )
        batch = json.loads(
            (repo_root / "data/debian-workstation-packages-batch-07.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        expected_batch_names = {
            "2048",
            "9menu",
            "9mount",
            "9wm",
            "ack",
            "acpitool",
            "agedu",
            "alttab",
            "ansiweather",
            "asciijump",
            "asclock",
            "asclock-themes",
            "asmon",
            "autocutsel",
            "autorandr",
            "bbpager",
            "bbtime",
            "bdfresize",
            "bubblefishymon",
            "cbonsai",
            "chroma-curses",
            "chroma-data",
            "cpulimit",
            "crawl",
            "crawl-common",
            "ddgr",
            "dfc",
            "fizmo-console",
            "gearhead",
            "gearhead-data",
            "gemdropx",
            "git-extras",
            "jqp",
            "libdockapp3",
            "libfile-next-perl",
            "liblua5.1-0",
            "libwraster6",
            "nbsdgames",
            "ninvaders",
            "nsnake",
            "omega-rpg",
            "sudoku",
            "swaks",
            "tint",
            "ttyrec",
            "wmacpi",
            "wmbattery",
            "wmbiff",
            "wmbubble",
            "wmbutton",
            "wmcalc",
            "wmcalclock",
            "wmcliphist",
            "wmclock",
            "wmclockmon",
            "wmcore",
            "wmcpu",
            "wmcpuload",
            "wmdiskmon",
            "wmdrawer",
            "wmfsm",
            "wmget",
            "wmgtemp",
            "wmhdplop",
            "wmifinfo",
            "wmifs",
            "wmitime",
            "wmload",
            "wmmemload",
            "wmmon",
            "wmmoonclock",
            "wmnd",
            "wmnut",
            "wmpinboard",
            "wmpuzzle",
            "wmressel",
            "wmstickynotes",
            "wmsun",
            "wmsysmon",
            "wmsystemtray",
            "wmtemp",
            "wmtime",
            "wmtop",
            "wmusic",
            "wmweather",
            "wmweather+",
            "wmxres",
            "xdo",
            "xsoldier",
        }
        self.assertEqual(set(batch_packages), expected_batch_names)
        self.assertEqual(len(batch_packages), 89)
        self.assertEqual(len(batch_packages), len(batch["packages"]))
        self.assertEqual(batch["suite"], "trixie")
        self.assertEqual(batch["architecture"], "amd64")
        self.assertTrue(
            {"asclock", "wmbattery", "autorandr", "ninvaders"}
            <= set(batch_packages)
        )
        self.assertTrue({"wmaker", "twm"}.isdisjoint(batch_packages))

        for name, package in batch_packages.items():
            with self.subTest(package=name):
                self.assertEqual(packages[name], package)
                self.assertEqual(package["debian_packages"], [name])
                self.assertIn(package["arch"], {"any", "x86_64"})
                archive = repo_root / "packages" / (
                    f"{name}-{package['version']}-{package['arch']}.ark"
                )
                self.assertTrue(archive.is_file(), archive)
                recipe = repo_root / "packages/recipes" / name / "ARKPKG"
                self.assertTrue(recipe.is_file(), recipe)
                self.assertTrue(package["license_path"].endswith("/copyright"))
                source = batch["sources"][name]
                self.assertEqual(source, manifest["sources"][name])
                self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(source["filename"].startswith("pool/"))
                package_metadata = metadata[name]
                self.assertIn("author", package_metadata)
                self.assertTrue(package_metadata["author"])
                self.assertIn("git_author_url", package_metadata)
                self.assertTrue(package_metadata["git_repo"].startswith("https://"))
                self.assertIn(
                    package_metadata["git_repo_type"],
                    {"upstream", "packaging", "arkpkg-recipe"},
                )
                self.assertEqual(
                    {item["name"] for item in package_metadata["packagers"]},
                    {"arkana-team", "debarkak"},
                )

        for name, dependencies in {
            "ack": {"libfile-next-perl", "perl"},
            "asclock": {"asclock-themes"},
            "autorandr": {"xrandr"},
            "ansiweather": {"bc", "curl-cli", "jq"},
            "crawl": {"crawl-common", "liblua5.1-0"},
            "gearhead": {"gearhead-data"},
            "libwraster6": {
                "giflib",
                "libjpeg62-turbo",
                "libmagickwand-7.q16-10",
                "libpng16-16t64",
                "libtiff6",
                "libwebp7",
                "libxmu",
            },
            "wmbattery": {"upower"},
            "wmget": {"curl-cli", "perl"},
            "wmcore": {"libdockapp3"},
            "wmmon": {"libdockapp3"},
            "wmacpi": {"libdockapp3"},
            "wmsysmon": {"libdockapp3"},
            "wmweather+": {"libcurl3t64-gnutls", "libwraster6", "pcre2-libs"},
        }.items():
            self.assertTrue(dependencies <= set(packages[name]["dependencies"]))

        self.assertEqual(
            set(packages["autorandr"]["base_dependencies"]),
            {"python3", "libinput-tools"},
        )
        self.assertNotIn("python3", packages["autorandr"]["dependencies"])
        self.assertNotIn("libinput-tools", packages["autorandr"]["dependencies"])

        self.assertEqual(packages["crawl"]["license_source_package"], "crawl-common")
        self.assertEqual(
            packages["crawl"]["license_source_path"],
            "usr/share/doc/crawl-common/copyright",
        )


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
        for name, (
            architecture,
            filename,
            checksum,
            version,
        ) in expected_sources.items():
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

    def test_batch_18_pins_classic_shell_and_terminal_tools(self) -> None:
        """Keep the fuzzy selector, Plan 9 shell, and terminal tools reproducible."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-18.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected_sources = {
            "fzy": (
                "amd64",
                "pool/main/f/fzy/fzy_1.0-1_amd64.deb",
                "235db1e46f06b632ebb722c67a0d9dfdc829c2fbb517eac9e35449d90b82ad61",
                "1.0-1",
            ),
            "pipes-sh": (
                "all",
                "pool/main/p/pipes.sh/pipes-sh_1.3.0-2_all.deb",
                "aed5c00fab306e4e8223159a12f3d97ef62f4b08c555837695246dd7063d7450",
                "1.3.0-2",
            ),
            "rc": (
                "amd64",
                "pool/main/r/rc/rc_1.7.4+97.gceb59bb-5+b1_amd64.deb",
                "74ef45c671b9c2579c88370fc610ba4d5a68b55aa39c22e085a8e3497dd9e63f",
                "1.7.4+97.gceb59bb-5+b1",
            ),
            "task-spooler": (
                "amd64",
                "pool/main/t/task-spooler/task-spooler_1.0.3+dfsg1-1_amd64.deb",
                "2df72be9e428a6b25eaf4e23c1dbdc1037ba539260e7cce1ea7c97bd85351207",
                "1.0.3+dfsg1-1",
            ),
        }
        expected_repositories = {
            "fzy": ("https://github.com/jhawthorn/fzy", "upstream"),
            "pipes-sh": ("https://github.com/pipeseroni/pipes.sh", "upstream"),
            "rc": ("https://github.com/rakitzis/rc", "upstream"),
            "task-spooler": (
                "https://salsa.debian.org/debian/task-spooler",
                "packaging",
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected_sources))

        for name, (architecture, filename, checksum, version) in expected_sources.items():
            with self.subTest(package=name):
                source = {
                    "architecture": architecture,
                    "filename": filename,
                    "sha256": checksum,
                    "version": version,
                }
                self.assertEqual(batch["sources"][name], source)
                self.assertEqual(manifest["sources"][name], source)
                self.assertEqual(batch_packages[name], manifest_packages[name])
                self.assertEqual(batch_packages[name]["debian_version"], version)
                self.assertIsNone(metadata[name]["author"])
                self.assertIsNone(metadata[name]["git_author_url"])
                self.assertEqual(
                    (metadata[name]["git_repo"], metadata[name]["git_repo_type"]),
                    expected_repositories[name],
                )
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, batch_packages[name]),
                    f"missing matching recipe or archive for {name}",
                )

        self.assertEqual(
            batch_packages["rc"]["dependencies"], ["readline-libs", "ncurses-libs"]
        )
        self.assertEqual(
            batch_packages["rc"]["symlinks"], [["usr/bin/rc", "rc.byron"]]
        )

    def test_batch_19_pins_unix_shells_and_terminal_recording(self) -> None:
        """Keep the Korn shell refresh and terminal tools reproducible."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-19.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "asciinema": (
                "asciinema",
                "all",
                "pool/main/a/asciinema/asciinema_2.4.0-1_all.deb",
                "7f069e3dcf93c41c13835aa2423a220688c95a91ac0a972c4335dad3c43afb8e",
                "2.4.0-1",
                "https://github.com/asciinema/asciinema",
                "Marcin Kulik",
                "https://github.com/ku1ik",
            ),
            "ksh93": (
                "ksh93u+m",
                "amd64",
                "pool/main/k/ksh93u+m/ksh93u+m_1.0.10-2_amd64.deb",
                "ed896af6823f4c2e81cabc9d864b205d6f841434466613388703b9d0d928278d",
                "1.0.10-2",
                "https://github.com/ksh93/ksh",
                "David G. Korn / AT&T Research",
                "https://github.com/ksh93/ksh",
            ),
            "posh": (
                "posh",
                "amd64",
                "pool/main/p/posh/posh_0.14.1_amd64.deb",
                "1b5d164d880aa57c6808fec6185b0d07e0c15bee7c0c518bdd5252d17a24ef90",
                "0.14.1",
                "https://salsa.debian.org/clint/posh",
                "Clint Adams",
                "https://salsa.debian.org/clint",
            ),
            "python3-autocommand": (
                "python3-autocommand",
                "all",
                "pool/main/p/python-autocommand/python3-autocommand_2.2.2-3_all.deb",
                "1e62ac22ff83466228c051c6c2402a40f521646e15b5aa54cf2391206a848bad",
                "2.2.2-3",
                "https://github.com/Lucretiel/autocommand",
                "Nathan West",
                "https://github.com/Lucretiel",
            ),
            "python3-inflect": (
                "python3-inflect",
                "all",
                "pool/main/p/python-inflect/python3-inflect_7.3.1-2_all.deb",
                "6c10dca4d4aa0eb0b4e16e8b62b74a073a8d22be8809c3f61c56b06fdd1451f8",
                "7.3.1-2",
                "https://github.com/jaraco/inflect",
                "Paul Dyson",
                "https://github.com/pwdyson",
            ),
            "python3-jaraco.context": (
                "python3-jaraco.context",
                "all",
                "pool/main/j/jaraco.context/python3-jaraco.context_6.0.1-1+deb13u1_all.deb",
                "f909ec7b00dd96a1905322acba7cdd2df09747ceddeef6e426a97b33ab227f80",
                "6.0.1-1+deb13u1",
                "https://github.com/jaraco/jaraco.context",
                "Jason R. Coombs",
                "https://github.com/jaraco",
            ),
            "python3-jaraco.functools": (
                "python3-jaraco.functools",
                "all",
                "pool/main/p/python-jaraco.functools/python3-jaraco.functools_4.1.0-1_all.deb",
                "f5ac3e5f79b6394191d76ea7cde9255d8bae98f639886cb981444128f6545796",
                "4.1.0-1",
                "https://github.com/jaraco/jaraco.functools",
                "Jason R. Coombs",
                "https://github.com/jaraco",
            ),
            "python3-more-itertools": (
                "python3-more-itertools",
                "all",
                "pool/main/m/more-itertools/python3-more-itertools_10.7.0-1_all.deb",
                "385d785fa84261838732ec97627b4e95ec7399fc449d8a2aaaef66fb5f961c25",
                "10.7.0-1",
                "https://github.com/more-itertools/more-itertools",
                "Erik Rose",
                "https://github.com/erikrose",
            ),
            "python3-typeguard": (
                "python3-typeguard",
                "all",
                "pool/main/p/python-typeguard/python3-typeguard_4.4.2-1_all.deb",
                "81e512101d7ab94e73646b9a8e4fb9b948836e3172dc5719ad3acbcde65bca01",
                "4.4.2-1",
                "https://github.com/agronholm/typeguard",
                "Alex Grönholm",
                "https://github.com/agronholm",
            ),
            "python3-pkg-resources": (
                "python3-pkg-resources",
                "all",
                "pool/main/s/setuptools/python3-pkg-resources_78.1.1-0.1_all.deb",
                "cf8749251172abc945216cf878367f614cf91d3d9a1043ba3bacc5a74e8cc1a8",
                "78.1.1-0.1",
                "https://github.com/pypa/setuptools",
                "Python Packaging Authority",
                "https://github.com/pypa",
            ),
        }
        batch_packages = {item["name"]: item for item in batch["packages"]}
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        self.assertEqual(set(batch_packages), set(expected))

        for name, (
            source_name,
            architecture,
            filename,
            checksum,
            version,
            repository,
            author,
            author_url,
        ) in expected.items():
            with self.subTest(package=name):
                source = {
                    "architecture": architecture,
                    "filename": filename,
                    "sha256": checksum,
                    "version": version,
                }
                self.assertEqual(batch["sources"][source_name], source)
                self.assertEqual(manifest["sources"][source_name], source)
                self.assertEqual(batch_packages[name], manifest_packages[name])
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, batch_packages[name]),
                    f"missing matching recipe or archive for {name}",
                )
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_author_url"], author_url)
                self.assertEqual(metadata[name]["git_repo"], repository)
                self.assertEqual(
                    metadata[name]["git_repo_type"],
                    "packaging" if name == "posh" else "upstream",
                )
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )

        self.assertEqual(batch_packages["asciinema"]["dependencies"], ["python3-pkg-resources"])
        self.assertEqual(
            batch_packages["python3-pkg-resources"]["dependencies"],
            [
                "python3-autocommand",
                "python3-inflect",
                "python3-jaraco.context",
                "python3-jaraco.functools",
                "python3-more-itertools",
            ],
        )
        self.assertEqual(
            batch_packages["python3-inflect"]["dependencies"],
            [
                "python3-more-itertools",
                "python3-typeguard",
                "python3-typing-extensions",
            ],
        )
        self.assertEqual(
            batch_packages["python3-jaraco.functools"]["dependencies"],
            ["python3-more-itertools"],
        )
        self.assertEqual(
            batch_packages["python3-typeguard"]["dependencies"],
            ["python3-typing-extensions"],
        )
        self.assertEqual(
            metadata["python3-typing-extensions"]["author"],
            "Python Typing contributors",
        )
        self.assertEqual(
            metadata["python3-typing-extensions"]["git_repo"],
            "https://github.com/python/typing_extensions",
        )
        self.assertEqual(
            metadata["python3-typing-extensions"]["git_author_url"],
            "https://github.com/python",
        )
        self.assertEqual(
            metadata["python3-typing-extensions"]["git_repo_type"], "upstream"
        )
        self.assertEqual(batch_packages["ksh93"]["debian_version"], "1.0.10-2")
        self.assertEqual(
            batch_packages["ksh93"]["symlinks"], [["usr/bin/ksh", "ksh93"]]
        )

    def test_cli_and_workstation_catalogs_match_recipes_and_archives(self) -> None:
        """Catch version drift between package manifests and published archives."""
        repo_root = Path(__file__).resolve().parents[1]
        for manifest_name in (
            "data/debian-cli-packages.json",
            "data/debian-workstation-packages.json",
        ):
            manifest = json.loads(
                (repo_root / manifest_name).read_text(encoding="utf-8")
            )
            for package in manifest["packages"]:
                with self.subTest(manifest=manifest_name, package=package["name"]):
                    self.assertTrue(
                        BUILDER.repository_has_package(repo_root, package),
                        f"{manifest_name}: missing matching recipe or archive for "
                        f"{package['name']} {package['version']}",
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


class DebianDownloadCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.content = b"pinned Debian package bytes"
        self.source = {
            "filename": "pool/main/f/fixture/fixture.deb",
            "sha256": hashlib.sha256(self.content).hexdigest(),
        }

    def test_download_creates_private_cache_and_reuses_verified_archive(self) -> None:
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.content)
        ) as download:
            archive = BUILDER.obtain_deb(self.source, self.cache)
            self.assertEqual(BUILDER.obtain_deb(self.source, self.cache), archive)
        download.assert_called_once()
        self.assertEqual(archive.read_bytes(), self.content)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertEqual(list(self.cache.iterdir()), [archive])

    def test_predictable_download_symlink_cannot_overwrite_another_file(self) -> None:
        self.cache.mkdir(mode=0o700)
        victim = self.root / "unrelated-file"
        victim.write_bytes(b"keep me")
        trap = self.cache / "fixture.deb.download"
        trap.symlink_to(victim)
        archive = self.cache / "fixture.deb"
        archive.symlink_to(victim)
        with patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.content)
        ):
            self.assertEqual(BUILDER.obtain_deb(self.source, self.cache), archive)
        self.assertEqual(victim.read_bytes(), b"keep me")
        self.assertTrue(trap.is_symlink())
        self.assertFalse(archive.is_symlink())
        self.assertEqual(archive.read_bytes(), self.content)

    def test_checksum_failure_removes_download_without_publishing_archive(self) -> None:
        with (
            patch.object(BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.obtain_deb(self.source, self.cache)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_network_retries_clean_up_temporary_files(self) -> None:
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                responses = [BUILDER.urllib.error.URLError("interrupted")] * 4
                if succeeds:
                    responses[-1] = io.BytesIO(self.content)
                with (
                    patch.object(BUILDER.urllib.request, "urlopen", side_effect=responses),
                    patch.object(BUILDER.time, "sleep"),
                ):
                    if succeeds:
                        archive = BUILDER.obtain_deb(self.source, self.cache)
                        self.assertEqual(archive.read_bytes(), self.content)
                        archive.unlink()
                    else:
                        with self.assertRaises(BUILDER.urllib.error.URLError):
                            BUILDER.obtain_deb(self.source, self.cache)
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_non_private_caches_are_rejected_before_network_access(self) -> None:
        self.cache.mkdir(mode=0o700)
        for mode in (0o755, 0o770, 0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with (
                    patch.object(BUILDER.urllib.request, "urlopen") as download,
                    self.assertRaisesRegex(ValueError, "source cache"),
                ):
                    BUILDER.obtain_deb(self.source, self.cache)
                download.assert_not_called()

    def test_cache_owned_by_another_user_is_rejected(self) -> None:
        self.cache.mkdir(mode=0o700)
        original_lstat = Path.lstat

        def lstat(path: Path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            if path == self.cache:
                values = list(info)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return info

        with (
            patch.object(Path, "lstat", autospec=True, side_effect=lstat),
            patch.object(BUILDER.urllib.request, "urlopen") as download,
            self.assertRaisesRegex(ValueError, "source cache"),
        ):
            BUILDER.obtain_deb(self.source, self.cache)
        download.assert_not_called()

    def test_replaceable_parent_is_rejected_before_creating_cache(self) -> None:
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "unsafe source cache directory"):
            BUILDER.obtain_deb(self.source, self.cache)
        self.assertFalse(self.cache.exists())

    def test_symlink_cache_and_parent_are_rejected(self) -> None:
        target = self.root / "target"
        target.mkdir(mode=0o700)
        self.cache.symlink_to(target, target_is_directory=True)
        for cache in (self.cache, self.cache / "nested"):
            with self.subTest(cache=cache), self.assertRaisesRegex(ValueError, "source cache"):
                BUILDER.obtain_deb(self.source, cache)
        self.assertEqual(list(target.iterdir()), [])

    def test_build_does_not_resolve_away_cache_symlinks(self) -> None:
        self.cache.symlink_to(self.root, target_is_directory=True)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"sources": {}, "packages": []}), encoding="utf-8")
        args = argparse.Namespace(
            repo_root=self.root, manifest=manifest, packages=[], source_cache=self.cache
        )
        with self.assertRaisesRegex(ValueError, "source cache"):
            BUILDER.build(args)



class Batch20PackageTests(unittest.TestCase):
    def test_batch_20_pins_optional_unix_workstation_tools(self) -> None:
        """Verify batch 20 source pins, upstream attribution, and built archives."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-20.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]

        expected = {
            "dtach": (
                "amd64",
                "pool/main/d/dtach/dtach_0.9-7_amd64.deb",
                "1d73407ee097e34f6379a7721aad7af4b6cb034ad10f5a6eb0e4626502990d5e",
                "0.9-7",
                "Ned T. Crigler",
                "https://github.com/crigler/dtach",
                "https://github.com/crigler",
            ),
            "dvtm": (
                "amd64",
                "pool/main/d/dvtm/dvtm_0.15+40.g311a8c0-2_amd64.deb",
                "e177b030d5b27e00a8332d24acc0d3b184769d50fc6505a0473184b907661c91",
                "0.15+40.g311a8c0-2",
                "Markus Teich",
                "https://github.com/martanne/dvtm",
                "https://github.com/martanne",
            ),
            "fossil": (
                "amd64",
                "pool/main/f/fossil/fossil_2.26-2_amd64.deb",
                "fe2cb4da1441655e319ad6601eb99d013a814d7088731072cc7b45866f8fe195",
                "1:2.26-2",
                "D. Richard Hipp",
                "https://github.com/drhsqlite/fossil-mirror",
                "https://github.com/drhsqlite",
            ),
            "ncal": (
                "amd64",
                "pool/main/b/bsdmainutils/ncal_12.1.8_amd64.deb",
                "d2de213d7e86ae56d1d44318dc8307e1efe5ea6ede0dbef6ae3c7ed68e4be618",
                "12.1.8",
                "Wolfgang Helbig",
                "https://github.com/freebsd/freebsd-src",
                "https://docs.freebsd.org/en/articles/contributors/",
            ),
            "tokei": (
                "amd64",
                "pool/main/r/rust-tokei/tokei_12.1.2-6+b1_amd64.deb",
                "2daab456bb9cff63f6561430e788b3f914d65f68987616592f613cc238d1b697",
                "12.1.2-6+b1",
                "XAMPPRocky and contributors",
                "https://github.com/XAMPPRocky/tokei",
                "https://github.com/XAMPPRocky",
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected))
        self.assertEqual(batch["suite"], "trixie")
        self.assertEqual(batch["architecture"], "amd64")
        self.assertEqual(
            batch_packages["ncal"]["exclude_paths"],
            ["usr/bin/cal", "usr/share/man/man1/cal.1.gz"],
        )

        for name, (
            architecture,
            filename,
            checksum,
            version,
            author,
            git_repo,
            git_author_url,
        ) in expected.items():
            with self.subTest(package=name):
                source = {
                    "architecture": architecture,
                    "filename": filename,
                    "sha256": checksum,
                    "version": version,
                }
                self.assertEqual(batch["sources"][name], source)
                self.assertEqual(manifest["sources"][name], source)
                self.assertEqual(batch_packages[name], manifest_packages[name])
                self.assertEqual(batch_packages[name]["debian_version"], version)
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], "upstream")
                self.assertEqual(metadata[name]["git_author_url"], git_author_url)
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, batch_packages[name]),
                    f"missing matching recipe or archive for {name}",
                )


class Batch21PackageTests(unittest.TestCase):
    def test_batch_21_pins_hardware_diagnostics_and_topology_tools(self) -> None:
        """Verify hardware package pins, authors, dependencies, and pruning."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-21.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "cpuid": (
                "amd64",
                "pool/main/c/cpuid/cpuid_20250316-1_amd64.deb",
                "5eb4dd791712865897c1b7ce95a18dcea83f87350d0af4b1eaa227ca991d3d5d",
                "20250316-1",
                "Todd Allen",
                "https://salsa.debian.org/debian/cpuid.git",
                "https://www.etallen.com/cpuid.html",
                "packaging",
            ),
            "hwloc-nox": (
                "amd64",
                "pool/main/h/hwloc/hwloc-nox_2.12.0-4_amd64.deb",
                "f302d4e690d94f556ec30874633b5e1152abea963b890532aed4abaa88a16cf7",
                "2.12.0-4",
                "Cédric Augonnet, Jérôme Clet-Ortega, Ludovic Courtès, Brice Goglin, Nathalie Furmento, Samuel Thibault, and Jeff Squyres",
                "https://github.com/open-mpi/hwloc",
                "https://github.com/open-mpi/hwloc/graphs/contributors",
                "upstream",
            ),
            "libsysfs2": (
                "amd64",
                "pool/main/s/sysfsutils/libsysfs2_2.1.1-7_amd64.deb",
                "58b1277a9f5cf4ac23ccebb4633e182badb81defaf33d97838880afc1420cd9a",
                "2.1.1-7",
                "Ananth Mavinakayanahalli, Daniel Stekloff, Mohan Kumar, and Nitin Vashisth",
                "https://github.com/linux-ras/sysfsutils",
                "https://github.com/linux-ras/sysfsutils/graphs/contributors",
                "upstream",
            ),
            "memtester": (
                "amd64",
                "pool/main/m/memtester/memtester_4.7.1-1_amd64.deb",
                "399f82d122bb163ece0b670a195e99a1883f725c6cd8588181fe31a3bc595cb7",
                "4.7.1-1",
                "Charles Cazabon and Simon Kirby",
                "https://github.com/jnavila/memtester",
                "https://pyropus.ca/software/memtester/",
                "upstream",
            ),
            "pci.ids": (
                "all",
                "pool/main/p/pci.ids/pci.ids_0.0~2025.06.09-1_all.deb",
                "132bc7520473e53939e277cbf4512261b8d73ae244d77c277f674ecaf174598c",
                "0.0~2025.06.09-1",
                "Martin Mareš and Albert Pool",
                "https://github.com/pciutils/pciids",
                "https://pci-ids.ucw.cz/",
                "upstream",
            ),
            "sysfsutils": (
                "amd64",
                "pool/main/s/sysfsutils/sysfsutils_2.1.1-7_amd64.deb",
                "8f6c69ed795ccfb7fbde9e28f8cd969de460d6293cac6709b57d391290338f8a",
                "2.1.1-7",
                "Ananth Mavinakayanahalli, Daniel Stekloff, Mohan Kumar, and Nitin Vashisth",
                "https://github.com/linux-ras/sysfsutils",
                "https://github.com/linux-ras/sysfsutils/graphs/contributors",
                "upstream",
            ),
        }
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        batch_packages = {item["name"]: item for item in batch["packages"]}
        self.assertEqual(set(batch_packages), set(expected))
        self.assertEqual(batch["suite"], "trixie")
        self.assertEqual(batch["architecture"], "amd64")
        self.assertEqual(
            batch_packages["sysfsutils"]["exclude_paths"],
            ["etc/init.d/sysfsutils", "usr/lib/systemd/system/sysfsutils.service"],
        )
        self.assertEqual(batch_packages["cpuid"]["dependencies"], ["perl"])
        self.assertEqual(
            batch_packages["hwloc-nox"]["dependencies"], ["libhwloc15", "libtinfo6"]
        )
        self.assertEqual(
            batch_packages["sysfsutils"]["dependencies"], ["libsysfs2", "pci.ids"]
        )

        for name, (
            architecture,
            filename,
            checksum,
            version,
            author,
            git_repo,
            git_author_url,
            git_repo_type,
        ) in expected.items():
            with self.subTest(package=name):
                source = {
                    "architecture": architecture,
                    "filename": filename,
                    "sha256": checksum,
                    "version": version,
                }
                self.assertEqual(batch["sources"][name], source)
                self.assertEqual(manifest["sources"][name], source)
                self.assertEqual(batch_packages[name], manifest_packages[name])
                self.assertEqual(batch_packages[name]["debian_version"], version)
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], git_repo_type)
                self.assertEqual(metadata[name]["git_author_url"], git_author_url)
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, batch_packages[name]),
                    f"missing matching recipe or archive for {name}",
                )


    def test_sysfsutils_archive_omits_init_system_hooks(self) -> None:
        """Keep the optional sysfs tool package free of boot service hooks."""
        archive_path = (
            Path(__file__).resolve().parents[1]
            / "packages/sysfsutils-2.1.1-7-x86_64.ark"
        )
        with (
            BUILDER.lz4.frame.open(archive_path, "rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            paths = {member.name for member in archive}
        self.assertIn("package/usr/bin/systool", paths)
        self.assertIn("package/etc/sysfs.conf", paths)
        self.assertIn("package/usr/share/doc/sysfsutils/copyright", paths)
        self.assertNotIn("package/etc/init.d/sysfsutils", paths)
        self.assertNotIn("package/usr/lib/systemd/system/sysfsutils.service", paths)


class BitmapFontIndexTests(unittest.TestCase):
    def test_debian_bitmap_font_packages_generate_legacy_indexes(self) -> None:
        """Ensure Debian bitmap fonts receive generated legacy index files."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "tool-calls.log"

            mkfontscale = fake_bin / "mkfontscale"
            mkfontscale.write_text(
                "#!/bin/sh\n"
                "printf 'mkfontscale %s\\n' \"$PWD\" >> \"$FONT_INDEX_LOG\"\n"
                "printf '1\\nfixture.pcf.gz test-xlfd\\n' > fonts.scale\n",
                encoding="utf-8",
            )
            mkfontscale.chmod(0o755)
            mkfontdir = fake_bin / "mkfontdir"
            mkfontdir.write_text(
                "#!/bin/sh\n"
                "printf 'mkfontdir %s\\n' \"$PWD\" >> \"$FONT_INDEX_LOG\"\n"
                "printf '1\\nfixture.pcf.gz test-xlfd\\n' > fonts.dir\n",
                encoding="utf-8",
            )
            mkfontdir.chmod(0o755)

            with patch.dict(
                os.environ,
                {"PATH": str(fake_bin), "FONT_INDEX_LOG": str(log)},
            ):
                for package, relative_directory in BUILDER.BITMAP_FONT_DIRECTORIES.items():
                    font_directory = payload / relative_directory
                    font_directory.mkdir(parents=True)
                    BUILDER.generate_bitmap_font_indexes(payload, package)
                    for index_name in ("fonts.scale", "fonts.dir"):
                        self.assertEqual(
                            (font_directory / index_name).read_text(encoding="ascii"),
                            "1\nfixture.pcf.gz test-xlfd\n",
                        )

            expected = []
            for relative_directory in BUILDER.BITMAP_FONT_DIRECTORIES.values():
                font_directory = payload / relative_directory
                expected.extend(
                    f"{tool} {font_directory}"
                    for tool in ("mkfontscale", "mkfontdir")
                )
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), expected)

    def test_non_font_packages_do_not_require_font_tools(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(BUILDER.shutil, "which", return_value=None),
        ):
            BUILDER.generate_bitmap_font_indexes(Path(temporary), "atool")

    def test_missing_font_index_tools_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary)
            font_directory = payload / BUILDER.BITMAP_FONT_DIRECTORIES["xfonts-base"]
            font_directory.mkdir(parents=True)
            with (
                patch.object(BUILDER.shutil, "which", return_value=None),
                self.assertRaisesRegex(FileNotFoundError, "mkfontscale, mkfontdir"),
            ):
                BUILDER.generate_bitmap_font_indexes(payload, "xfonts-base")


class Batch22PackageTests(unittest.TestCase):
    def test_batch_22_pins_optional_job_time_and_remote_filesystem_tools(self) -> None:
        """Keep batch 22 pins, metadata, dependencies, and archives aligned."""
        repo_root = Path(__file__).resolve().parents[1]
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(encoding="utf-8")
        )
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-22.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "gnu-parallel": (
                "20240222+ds-2",
                "eb31b7482e76d3aee62917d2f9a474663679390f29abc227ed59ee05b1edc675",
                "Ole Tange and the Free Software Foundation",
                "https://git.savannah.gnu.org/cgit/parallel.git",
                "https://savannah.gnu.org/users/ole.tange",
                "parallel-gnu",
                "parallel",
            ),
            "sshfs": (
                "3.7.3-1.2~deb13u1",
                "2e026e0dac3d1d209112eafac8fea76e242f94615b16f0ae3cefef6ae7f6b405",
                "Miklos Szeredi",
                "https://github.com/libfuse/sshfs",
                "https://github.com/libfuse/sshfs/graphs/contributors",
                "sshfs",
                "sshfs",
            ),
            "timewarrior": (
                "1.7.1+ds.1-2",
                "41b98a6dbff93c932ebd66213332e46cef72bfcb77e9b3f4d8870c6ae290ed03",
                "Paul Beckingham and Federico Hernandez",
                "https://github.com/GothenburgBitFactory/timewarrior",
                "https://github.com/GothenburgBitFactory/timewarrior/graphs/contributors",
                "timew",
                "timewarrior",
            ),
            "tty-clock": (
                "2.3+git.20211121.f2f847c-1",
                "a04fdc38aabfaffe972b61e78b19079eb95585ac167241d9d0d7efd2ce693961",
                "Martin Duquesnoy and tty-clock contributors",
                "https://github.com/xorg62/tty-clock",
                "https://github.com/xorg62",
                "tty-clock",
                "tty-clock",
            ),
        }
        batch_packages = {item["name"]: item for item in batch["packages"]}
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        self.assertEqual(set(batch_packages), set(expected))
        self.assertEqual(batch["suite"], "trixie")
        self.assertEqual(batch["architecture"], "amd64")

        expected_dependencies = {
            "gnu-parallel": {"procps-ng", "sysstat", "perl"},
            "sshfs": {"fuse3-tools", "fuse3-devel", "glib-devel", "openssh-client"},
            "timewarrior": {"gcc-libs"},
            "tty-clock": {"ncurses-libs", "libtinfo6"},
        }
        for name, (
            version,
            checksum,
            author,
            git_repo,
            author_url,
            command,
            source_name,
        ) in expected.items():
            package = batch_packages[name]
            with self.subTest(package=name):
                self.assertEqual(manifest_packages[name], package)
                self.assertEqual(package["version"], version)
                self.assertEqual(
                    package["dependencies"], sorted(expected_dependencies[name])
                )
                self.assertEqual(
                    batch["sources"][source_name], manifest["sources"][source_name]
                )
                self.assertEqual(batch["sources"][source_name]["sha256"], checksum)
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_author_url"], author_url)
                self.assertEqual(metadata[name]["git_repo_type"], "upstream")
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, package),
                    f"missing matching recipe or archive for {name}",
                )

                archive_path = repo_root / "packages" / (
                    f"{name}-{version}-{package['arch']}.ark"
                )
                archive_links = {}
                helper_contents = {}
                with (
                    BUILDER.lz4.frame.open(archive_path, "rb") as compressed,
                    tarfile.open(fileobj=compressed, mode="r|") as archive,
                ):
                    paths = set()
                    for member in archive:
                        paths.add(member.name)
                        if member.issym():
                            archive_links[member.name] = member.linkname
                        if member.name in {
                            "package/usr/bin/parallel-gnu",
                            "package/usr/bin/env_parallel.sh",
                            "package/usr/bin/parsort",
                            "package/usr/share/bash-completion/completions/parallel-gnu",
                            "package/usr/share/zsh/site-functions/_parallel-gnu",
                        }:
                            helper_contents[member.name] = archive.extractfile(
                                member
                            ).read()
                self.assertIn(f"package/usr/bin/{command}", paths)
                self.assertIn(f"package/{package['license_path']}", paths)

                if name == "gnu-parallel":
                    expected_remote_contexts = [
                        'sshwrapped($sshlogin,"parallel --number-of-sockets")',
                        'sshwrapped($sshlogin,"parallel --number-of-cores")',
                        'sshwrapped($sshlogin,"parallel --number-of-threads")',
                        'sshwrapped($sshlogin,\n\t\t\t"parallel --max-line-length-allowed")',
                    ]
                    self.assertEqual(
                        package["command_rewrites"][0]["preserve_contexts"],
                        expected_remote_contexts,
                    )
                    self.assertIn("package/usr/bin/sem", paths)
                    self.assertIn(
                        "package/usr/share/man/man1/parallel-gnu.1.gz", paths
                    )
                    self.assertIn(
                        "package/usr/share/zsh/site-functions/_parallel-gnu", paths
                    )
                    self.assertNotIn("package/usr/bin/parallel", paths)
                    self.assertNotIn("package/usr/bin/sem-gnu", paths)
                    self.assertEqual(
                        archive_links["package/usr/bin/sem"], "parallel-gnu"
                    )
                    self.assertIn(
                        b'parallel-gnu "$@"',
                        helper_contents["package/usr/bin/env_parallel.sh"],
                    )
                    self.assertIn(
                        b"qw(parallel-gnu -0 -j)",
                        helper_contents["package/usr/bin/parsort"],
                    )
                    self.assertIn(
                        b"qw(parallel-gnu)",
                        helper_contents["package/usr/bin/parsort"],
                    )
                    self.assertIn(
                        b"exec qw(parallel-gnu -0 --block",
                        helper_contents["package/usr/bin/parallel-gnu"],
                    )
                    program = helper_contents["package/usr/bin/parallel-gnu"]
                    for remote_context in expected_remote_contexts:
                        self.assertIn(remote_context.encode(), program)
                    self.assertIn(
                        b'$self->wrap("parallel-gnu --number-of-cores")',
                        program,
                    )
                    self.assertIn(
                        b"complete -F _comp_parallel parallel-gnu",
                        helper_contents[
                            "package/usr/share/bash-completion/completions/parallel-gnu"
                        ],
                    )
                    self.assertIn(
                        b"#compdef parallel-gnu",
                        helper_contents[
                            "package/usr/share/zsh/site-functions/_parallel-gnu"
                        ],
                    )


class PackagePathRenameTests(unittest.TestCase):
    def test_rename_preserves_modes_and_symlinks(self) -> None:
        """Preserve executable modes and relative symlink targets during renames."""
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "package"
            (payload / "usr/bin").mkdir(parents=True)
            binary = payload / "usr/bin/parallel"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            alias = payload / "usr/bin/sem"
            alias.symlink_to("parallel")

            BUILDER.rename_package_paths(
                payload,
                [
                    ["usr/bin/parallel", "usr/bin/parallel-gnu"],
                    ["usr/bin/sem", "usr/bin/sem-gnu"],
                ],
                "gnu-parallel",
            )

            renamed_binary = payload / "usr/bin/parallel-gnu"
            self.assertEqual(
                renamed_binary.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n"
            )
            self.assertEqual(stat.S_IMODE(renamed_binary.stat().st_mode), 0o755)
            self.assertEqual(
                (payload / "usr/bin/sem-gnu").readlink(), Path("parallel")
            )

    def test_rename_rejects_unsafe_paths_and_symlink_parents(self) -> None:
        """Reject unsafe paths and destinations that could escape the payload tree."""
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "package"
            (payload / "usr/bin").mkdir(parents=True)
            (payload / "usr/bin/tool").write_text("tool", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsafe renamed path"):
                BUILDER.rename_package_paths(
                    payload, [["usr/bin/tool", "../escape"]], "fixture"
                )

            with self.assertRaisesRegex(ValueError, "unsafe renamed path"):
                BUILDER.rename_package_paths(
                    payload, [["usr/bin/tool", "."]], "fixture"
                )

            (payload / "usr/bin/occupied").write_text("occupied", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "destination already exists"):
                BUILDER.rename_package_paths(
                    payload, [["usr/bin/tool", "usr/bin/occupied"]], "fixture"
                )

            outside = Path(temporary) / "outside"
            outside.mkdir()
            (outside / "tool").write_text("outside", encoding="utf-8")
            (payload / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "traverses a symlink"):
                BUILDER.rename_package_paths(
                    payload, [["linked/tool", "linked/renamed"]], "fixture"
                )

    def test_rewrite_updates_commands_without_changing_identifiers(self) -> None:
        """Repoint retained helper scripts while preserving project identifiers."""
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "package"
            (payload / "usr/bin").mkdir(parents=True)
            helper = payload / "usr/bin/env_parallel.sh"
            helper.write_text(
                'env_parallel parallel "$@"; qw(parallel) '
                'GNU parallel is the project name. '
                'https://example.test/software/parallel; $opt::parallel\n',
                encoding="utf-8",
            )
            zsh_completion = payload / "usr/share/zsh/site-functions/_parallel-gnu"
            zsh_completion.parent.mkdir(parents=True)
            zsh_completion.write_text(
                "#compdef parallel\n"
                'eval "$(parallel --shell-completion auto)"\n',
                encoding="utf-8",
            )

            BUILDER.rewrite_package_commands(
                payload,
                [
                    {
                        "from": "parallel",
                        "to": "parallel-gnu",
                        "paths": [
                            "usr/bin/env_parallel.sh",
                            "usr/share/zsh/site-functions/_parallel-gnu",
                        ],
                    }
                ],
                "fixture",
            )

            self.assertEqual(
                helper.read_text(encoding="utf-8"),
                'env_parallel parallel-gnu "$@"; qw(parallel-gnu) '
                'GNU parallel is the project name. '
                'https://example.test/software/parallel; $opt::parallel\n',
            )
            self.assertEqual(
                zsh_completion.read_text(encoding="utf-8"),
                "#compdef parallel-gnu\n"
                'eval "$(parallel-gnu --shell-completion auto)"\n',
            )

    def test_rewrite_preserves_remote_host_probes(self) -> None:
        """Keep stock GNU Parallel names in SSH probes while renaming local calls."""
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "package"
            (payload / "usr/bin").mkdir(parents=True)
            program = payload / "usr/bin/parallel-gnu"
            program.write_text(
                'sshwrapped($sshlogin,"parallel --number-of-sockets")\n'
                'sshwrapped($sshlogin,"parallel --number-of-cores")\n'
                'sshwrapped($sshlogin,"parallel --number-of-threads")\n'
                'sshwrapped($sshlogin,\n'
                '\t\t\t"parallel --max-line-length-allowed")\n'
                '$self->wrap("parallel --number-of-cores")\n',
                encoding="utf-8",
            )
            preserve_contexts = [
                'sshwrapped($sshlogin,"parallel --number-of-sockets")',
                'sshwrapped($sshlogin,"parallel --number-of-cores")',
                'sshwrapped($sshlogin,"parallel --number-of-threads")',
                'sshwrapped($sshlogin,\n\t\t\t"parallel --max-line-length-allowed")',
            ]

            BUILDER.rewrite_package_commands(
                payload,
                [
                    {
                        "from": "parallel",
                        "to": "parallel-gnu",
                        "paths": ["usr/bin/parallel-gnu"],
                        "preserve_contexts": preserve_contexts,
                    }
                ],
                "gnu-parallel",
            )

            self.assertEqual(
                program.read_text(encoding="utf-8"),
                'sshwrapped($sshlogin,"parallel --number-of-sockets")\n'
                'sshwrapped($sshlogin,"parallel --number-of-cores")\n'
                'sshwrapped($sshlogin,"parallel --number-of-threads")\n'
                'sshwrapped($sshlogin,\n'
                '\t\t\t"parallel --max-line-length-allowed")\n'
                '$self->wrap("parallel-gnu --number-of-cores")\n',
            )

    def test_rewrite_rejects_missing_preserved_command_context(self) -> None:
        """Do not silently lose an SSH command guard when upstream changes."""
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "package"
            (payload / "usr/bin").mkdir(parents=True)
            program = payload / "usr/bin/parallel-gnu"
            program.write_text("parallel --version\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "preserved command context"):
                BUILDER.rewrite_package_commands(
                    payload,
                    [
                        {
                            "from": "parallel",
                            "to": "parallel-gnu",
                            "paths": ["usr/bin/parallel-gnu"],
                            "preserve_contexts": [
                                'sshwrapped($sshlogin,"parallel --number-of-cores")',
                            ],
                        }
                    ],
                    "gnu-parallel",
                )

            self.assertEqual(
                program.read_text(encoding="utf-8"), "parallel --version\n"
            )


class Batch23PackageTests(unittest.TestCase):
    def test_batch_23_pins_terminal_monitor_and_benchmark_tools(self) -> None:
        """Keep batch 23 source pins, package credits, and archives aligned."""
        repo_root = Path(__file__).resolve().parents[1]
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-23.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        expected = {
            "btm": (
                "0.10.2+20250107-5",
                "8b658e2bf10c9fd7a37a280aee344aa79328a8f257ca101c946c906910dbe559",
                "Clement Tsang",
                "https://github.com/ClementTsang/bottom",
                "https://github.com/ClementTsang",
                "package/usr/bin/btm",
            ),
            "libaio1t64": (
                "0.3.113-8+b1",
                "490c8a2d116001a5f3b0a6d7a7aabf6a7b629366590dc5c3f75b078e931dd129",
                "Benjamin LaHaise and Jeff Moyer",
                "https://pagure.io/libaio.git",
                None,
                "package/usr/lib/x86_64-linux-gnu/libaio.so.1t64",
            ),
            "sysbench": (
                "1.0.20+ds-7",
                "153583af3e01787d4150c943d25acb741f4a7344dda29d241ed6d527424a920f",
                "Alexey Kopytov and contributors",
                "https://github.com/akopytov/sysbench",
                "https://github.com/akopytov",
                "package/usr/bin/sysbench",
            ),
        }
        batch_packages = {item["name"]: item for item in batch["packages"]}
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        self.assertEqual(set(batch_packages), set(expected))
        self.assertEqual(batch["suite"], "trixie")
        self.assertEqual(batch["architecture"], "amd64")

        expected_dependencies = {
            "btm": {"gcc-libs"},
            "libaio1t64": set(),
            "sysbench": {
                "libaio1t64",
                "libmariadb3",
                "neovim-runtime-libs",
                "libpq5",
            },
        }
        for name, (
            version,
            checksum,
            author,
            git_repo,
            author_url,
            expected_path,
        ) in expected.items():
            package = batch_packages[name]
            with self.subTest(package=name):
                self.assertEqual(package, manifest_packages[name])
                self.assertEqual(package["version"], version)
                self.assertEqual(
                    set(package["dependencies"]), expected_dependencies[name]
                )
                self.assertTrue(package["license_path"].endswith("/copyright"))
                self.assertEqual(
                    batch["sources"][package["debian_packages"][0]]["sha256"],
                    checksum,
                )
                for source_name in package["debian_packages"]:
                    self.assertEqual(
                        batch["sources"][source_name], manifest["sources"][source_name]
                    )
                self.assertEqual(metadata[name]["author"], author)
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], "upstream")
                self.assertEqual(metadata[name]["git_author_url"], author_url)
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, package),
                    f"missing matching recipe or archive for {name}",
                )

                archive_path = repo_root / "packages" / (
                    f"{name}-{version}-{package['arch']}.ark"
                )
                paths = set()
                links = {}
                with (
                    BUILDER.lz4.frame.open(archive_path, "rb") as compressed,
                    tarfile.open(fileobj=compressed, mode="r|") as archive,
                ):
                    for member in archive:
                        paths.add(member.name)
                        if member.issym():
                            links[member.name] = member.linkname
                self.assertIn(expected_path, paths)
                self.assertIn(f"package/{package['license_path']}", paths)
                if name == "libaio1t64":
                    self.assertEqual(
                        links[expected_path], "libaio.so.1t64.0.2"
                    )
                    _, _, provides = BUILDER.read_recipe(
                        repo_root / "packages/recipes/libaio1t64/ARKPKG"
                    )
                    self.assertTrue(
                        any(item.startswith("libaio.so.1t64=") for item in provides)
                    )
class Batch24PackageTests(unittest.TestCase):
    def test_batch_24_pins_storage_and_remote_hardware_tools(self) -> None:
        """Keep batch 24 source pins, package credits, and archives aligned."""
        repo_root = Path(__file__).resolve().parents[1]
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-24.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        package_details = {
            "ioping": (
                "1.3-1",
                "22ead2b91d92b070ba9f37eecc3ba7972b3f56ecf7b5bf2d82ee4e18712acbe6",
                set(),
                "https://salsa.debian.org/debian/ioping.git",
                "package/usr/bin/ioping",
                None,
            ),
            "libsgutils2-1.48": (
                "1.48-3~deb13u1",
                "b3547dc1fb6e8109fa9d7b16c4d7a54a9266d8f971f300cc534bb678a986736d",
                set(),
                "https://salsa.debian.org/linux-blocks-team/sg3-utils.git",
                "package/usr/lib/x86_64-linux-gnu/libsgutils2-1.48.so.2",
                "libsgutils2-1.48.so.2.0.0",
            ),
            "sg3-utils": (
                "1.48-3~deb13u1",
                "ba2e2ab287b372dc9646bc5210fb8497f0b06058f75f54a9c3b3f565d2263047",
                {"libsgutils2-1.48"},
                "https://salsa.debian.org/linux-blocks-team/sg3-utils.git",
                "package/usr/bin/sg_inq",
                None,
            ),
            "freeipmi-common": (
                "1.6.15-1",
                "dc54520d7ccb413715ec19d77f3232b1df31cda9d42c57702646c9a409ff74b2",
                set(),
                "https://salsa.debian.org/debian/freeipmi.git",
                "package/etc/freeipmi/freeipmi.conf",
                None,
            ),
            "libgpg-error0": (
                "1.51-4",
                "22b95570fd41c113ef6f5651563b4d748292844baab1278a46eb940c1dec2322",
                set(),
                "https://salsa.debian.org/debian/libgpg-error.git",
                "package/usr/lib/x86_64-linux-gnu/libgpg-error.so.0",
                "libgpg-error.so.0.38.0",
            ),
            "libgcrypt20": (
                "1.11.0-7+deb13u1",
                "f0eb41f8cb40896e0822e9ab65611e8383c12b721c76c54764f744abb4a8c247",
                {"libgpg-error0"},
                "https://salsa.debian.org/gnutls-team/libgcrypt.git",
                "package/usr/lib/x86_64-linux-gnu/libgcrypt.so.20",
                "libgcrypt.so.20.5.0",
            ),
            "libfreeipmi17": (
                "1.6.15-1",
                "2eb073cca8a5b1298dd79886f686a144f901a4fb0d3ef47900ff66b17aff3da0",
                {"freeipmi-common", "libgcrypt20"},
                "https://salsa.debian.org/debian/freeipmi.git",
                "package/usr/lib/x86_64-linux-gnu/libfreeipmi.so.17",
                "libfreeipmi.so.17.2.13",
            ),
            "ipmitool": (
                "1.8.19-9",
                "692752b073210398aadb7d3f3fffe875ccb9a02e5ee538b84479653ecccd9d36",
                {"libfreeipmi17", "openssl-libs", "readline-libs"},
                "https://git.jff.email/cgit/ipmitool.git",
                "package/usr/bin/ipmitool",
                None,
            ),
        }
        batch_packages = {item["name"]: item for item in batch["packages"]}
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        authors = {
            "ioping": ("Konstantin Khlebnikov", "https://github.com/koct9i"),
            "libsgutils2-1.48": (
                "Douglas Gilbert",
                "https://github.com/doug-gilbert",
            ),
            "sg3-utils": ("Douglas Gilbert", "https://github.com/doug-gilbert"),
            "freeipmi-common": ("Albert Chu", "https://github.com/chu11"),
            "libgpg-error0": (
                "g10 Code GmbH and contributors",
                "https://github.com/gpg/libgpg-error",
            ),
            "libgcrypt20": (
                "Werner Koch and contributors",
                "https://github.com/gpg/libgcrypt",
            ),
            "libfreeipmi17": ("Albert Chu", "https://github.com/chu11"),
            "ipmitool": (
                "Duncan Laurie and contributors",
                "https://codeberg.org/IPMITool/ipmitool",
            ),
        }
        self.assertEqual(set(batch_packages), set(package_details))
        self.assertEqual(batch["suite"], "trixie")
        self.assertEqual(batch["architecture"], "amd64")

        for name, (version, checksum, dependencies, git_repo, expected_path, link) in (
            package_details.items()
        ):
            package = batch_packages[name]
            with self.subTest(package=name):
                self.assertEqual(package, manifest_packages[name])
                self.assertEqual(package["version"], version)
                self.assertEqual(set(package["dependencies"]), dependencies)
                self.assertTrue(package["license_path"].endswith("/copyright"))
                for source_name in package["debian_packages"]:
                    self.assertEqual(
                        batch["sources"][source_name]["sha256"], checksum
                    )
                    self.assertEqual(
                        batch["sources"][source_name], manifest["sources"][source_name]
                    )
                self.assertEqual(metadata[name]["author"], authors[name][0])
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], "packaging")
                self.assertEqual(metadata[name]["git_author_url"], authors[name][1])
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, package),
                    f"missing matching recipe or archive for {name}",
                )

                archive_path = repo_root / "packages" / (
                    f"{name}-{version}-{package['arch']}.ark"
                )
                paths = set()
                links = {}
                with (
                    BUILDER.lz4.frame.open(archive_path, "rb") as compressed,
                    tarfile.open(fileobj=compressed, mode="r|") as archive,
                ):
                    for member in archive:
                        paths.add(member.name)
                        if member.issym():
                            links[member.name] = member.linkname
                self.assertIn(expected_path, paths)
                self.assertIn(f"package/{package['license_path']}", paths)
                if link is not None:
                    self.assertEqual(links[expected_path], link)
                    _, _, provides = BUILDER.read_recipe(
                        repo_root / "packages/recipes" / name / "ARKPKG"
                    )
                    self.assertTrue(
                        any(
                            item.startswith(f"{expected_path.rsplit('/', 1)[-1]}=")
                            for item in provides
                        )
                    )


class Batch25PackageTests(unittest.TestCase):
    def test_batch_25_pins_storage_recovery_and_media_tools(self) -> None:
        """Keep batch 25 source pins, credits, recipes, and archives aligned."""
        repo_root = Path(__file__).resolve().parents[1]
        batch = json.loads(
            (repo_root / "data/debian-cli-packages-batch-25.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (repo_root / "data/debian-cli-packages.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (repo_root / "data/package-metadata.json").read_text(encoding="utf-8")
        )["packages"]
        package_details = {
            "gddrescue": (
                "1.29-1",
                "c19d45f14e11d28f8e87f3ac2e22cef57b0995ffe354e260f26230695a420f03",
                {"gcc-libs"},
                "https://salsa.debian.org/mika/gddrescue",
                "packaging",
                "package/usr/bin/ddrescue",
            ),
            "exfatprogs": (
                "1.2.9-1+deb13u1",
                "b48c77643a13fa6e33d362b993a251c6528cfec158b772b1d115df59117be32f",
                set(),
                "https://github.com/exfatprogs/exfatprogs",
                "upstream",
                "package/usr/sbin/mkfs.exfat",
            ),
            "sdparm": (
                "1.12-2",
                "3d0983215f7ae29f1e7af407ad073caadace63b4f5fdd21ab8ce1c0d9074b4c0",
                set(),
                "https://github.com/doug-gilbert/sdparm",
                "upstream",
                "package/usr/bin/sdparm",
            ),
            "mmc-utils": (
                "0+git20240329.f757f413-1",
                "704313d2701cacca661878427e82556ba6a3bd5df388188094c480dc9fc9c02d",
                set(),
                "https://git.kernel.org/pub/scm/utils/mmc/mmc-utils.git",
                "upstream",
                "package/usr/bin/mmc",
            ),
            "btrfs-compsize": (
                "1.5-1.1",
                "e1c3e8fa9582c1e57175de137f857da173e892bf32d08d573ca7074f10e45d2a",
                set(),
                "https://github.com/kilobyte/compsize",
                "upstream",
                "package/usr/sbin/compsize",
            ),
        }
        authors = {
            "gddrescue": ("Antonio Diaz Diaz", None),
            "exfatprogs": (
                "Namjae Jeon and Hyunchul Lee",
                "https://github.com/namjaejeon",
            ),
            "sdparm": ("Douglas Gilbert", "https://github.com/doug-gilbert"),
            "mmc-utils": (
                "Chris Ball and SanDisk contributors",
                "https://sourceforge.net/u/thoric/profile/",
            ),
            "btrfs-compsize": (
                "Adam Borowski and Timofey Titovets",
                "https://github.com/kilobyte",
            ),
        }
        batch_packages = {item["name"]: item for item in batch["packages"]}
        manifest_packages = {item["name"]: item for item in manifest["packages"]}
        self.assertEqual(set(batch_packages), set(package_details))
        self.assertEqual(batch["suite"], "trixie")
        self.assertEqual(batch["architecture"], "amd64")

        for name, (
            version,
            checksum,
            dependencies,
            git_repo,
            git_repo_type,
            expected_path,
        ) in package_details.items():
            package = batch_packages[name]
            with self.subTest(package=name):
                self.assertEqual(package, manifest_packages[name])
                self.assertEqual(package["version"], version)
                self.assertEqual(set(package["dependencies"]), dependencies)
                self.assertTrue(package["license_path"].endswith("/copyright"))
                for source_name in package["debian_packages"]:
                    self.assertEqual(
                        batch["sources"][source_name]["sha256"], checksum
                    )
                    self.assertEqual(
                        batch["sources"][source_name], manifest["sources"][source_name]
                    )
                self.assertEqual(metadata[name]["author"], authors[name][0])
                self.assertEqual(metadata[name]["git_repo"], git_repo)
                self.assertEqual(metadata[name]["git_repo_type"], git_repo_type)
                self.assertEqual(metadata[name]["git_author_url"], authors[name][1])
                self.assertEqual(
                    {item["name"] for item in metadata[name]["packagers"]},
                    {"arkana-team", "debarkak"},
                )
                self.assertTrue(
                    BUILDER.repository_has_package(repo_root, package),
                    f"missing matching recipe or archive for {name}",
                )

                archive_path = repo_root / "packages" / (
                    f"{name}-{version}-{package['arch']}.ark"
                )
                paths = set()
                with (
                    BUILDER.lz4.frame.open(archive_path, "rb") as compressed,
                    tarfile.open(fileobj=compressed, mode="r|") as archive,
                ):
                    paths.update(member.name for member in archive)
                self.assertIn(expected_path, paths)
                self.assertIn(f"package/{package['license_path']}", paths)
                _, recipe_dependencies, provides = BUILDER.read_recipe(
                    repo_root / "packages/recipes" / name / "ARKPKG"
                )
                self.assertEqual(set(recipe_dependencies), dependencies)
                expected_provide = "/" + expected_path.removeprefix("package/")
                self.assertTrue(
                    any(item.endswith("=" + expected_provide) for item in provides)
                )


if __name__ == "__main__":
    unittest.main()
