#!/usr/bin/env python3
"""Test checksum verification at the upstream builder's cache boundary."""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import lz4.frame

BUILDER_PATH = Path(__file__).with_name("build-upstream-cli-packages.py")
SPEC = importlib.util.spec_from_file_location("upstream_cli_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the upstream package builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def source_archive(content: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name in ("tool", "LICENSE"):
            member = tarfile.TarInfo(f"fixture-1.0/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def zip_archive(files: dict[str, bytes]) -> bytes:
    """Create a small in-memory ZIP archive for reader tests."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def write_package_archive(
    path: Path, name: str, version: str, payloads: dict[str, bytes]
) -> None:
    """Write a minimal ark archive for package ownership tests."""
    metadata = f"Name: {name}\nVersion: {version}\nArch: x86_64\n".encode()
    with (
        lz4.frame.open(path, "wb") as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for member_name, data in (
            ("ARKPKG", metadata),
            *(
                (f"package/{relative}", content)
                for relative, content in payloads.items()
            ),
        ):
            member = tarfile.TarInfo(member_name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


class UpstreamCacheTests(unittest.TestCase):
    def test_http_and_source_metrics_tools_have_pinned_assets(self) -> None:
        """Keep Oha and scc tied to reviewed source and release hashes."""
        for name in ("oha", "scc"):
            with self.subTest(package=name):
                package = BUILDER.PACKAGES[name]
                self.assertEqual(package["arch"], "x86_64")
                digests = [package["archive_sha256"]]
                digests.extend(
                    package[key]
                    for key in ("binary_archive_sha256", "binary_sha256")
                    if key in package
                )
                digests.extend(package.get("binary_member_sha256", {}).values())
                self.assertTrue(
                    all(
                        len(digest) == 64
                        and all(char in "0123456789abcdef" for char in digest)
                        for digest in digests
                    )
                )
                self.assertTrue(package["license_source"])
        metadata = json.loads(
            (Path(__file__).resolve().parents[1] / "data/package-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["packages"]["oha"]["author"], "hatoo")
        self.assertEqual(metadata["packages"]["oha"]["git_author_url"], "https://github.com/hatoo")
        self.assertEqual(metadata["packages"]["scc"]["author"], "Ben Boyter")
        self.assertEqual(metadata["packages"]["scc"]["git_author_url"], "https://github.com/boyter")

    def test_added_terminal_tools_have_complete_release_pins(self) -> None:
        """Keep each new package's source, release, and binary hashes pinned."""
        for name in ("gum", "vivid", "ouch"):
            with self.subTest(package=name):
                package = BUILDER.PACKAGES[name]
                for key in ("archive_sha256", "binary_archive_sha256"):
                    digest = str(package[key])
                    self.assertEqual(len(digest), 64)
                    self.assertTrue(all(char in "0123456789abcdef" for char in digest))
                members = package["binary_members"]
                hashes = package["binary_member_sha256"]
                self.assertEqual(set(members), set(hashes))
                self.assertTrue(package["license_source"])

    def test_system_cli_tools_have_pinned_release_assets(self) -> None:
        """Keep each added system CLI's downloaded assets checksum-pinned."""
        for name in (
            "navi",
            "choose",
            "dysk",
            "doggo",
            "trippy",
            "nushell",
            "jujutsu",
            "mcfly",
            "curlie",
        ):
            with self.subTest(package=name):
                package = BUILDER.PACKAGES[name]
                digests = [package["archive_sha256"]]
                digests.extend(
                    package[key]
                    for key in ("binary_archive_sha256", "binary_sha256")
                    if key in package
                )
                digests.extend(package.get("binary_member_sha256", {}).values())
                self.assertGreaterEqual(len(digests), 2)
                self.assertTrue(
                    all(
                        len(str(digest)) == 64
                        and all(char in "0123456789abcdef" for char in str(digest))
                        for digest in digests
                    )
                )
                self.assertTrue(package["license_source"])
        nushell = BUILDER.PACKAGES["nushell"]
        self.assertEqual(nushell["provides"], ["nu=/usr/bin/nu"])
        self.assertIn(
            "Provides: nu=/usr/bin/nu",
            BUILDER.recipe_text("nushell", nushell),
        )
        jujutsu = BUILDER.PACKAGES["jujutsu"]
        self.assertEqual(jujutsu["provides"], ["jj=/usr/bin/jj"])
        self.assertEqual(jujutsu["dependencies"], ["git>=2.42.0"])
        self.assertIn(
            "Provides: jj=/usr/bin/jj",
            BUILDER.recipe_text("jujutsu", jujutsu),
        )
        self.assertIn(
            "Dependencies:\n  git>=2.42.0",
            BUILDER.recipe_text("jujutsu", jujutsu),
        )
        metadata = json.loads(
            (Path(__file__).resolve().parents[1] / "data/package-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            metadata["packages"]["jujutsu"]["author"], "Martin von Zweigbergk"
        )
        self.assertEqual(
            metadata["packages"]["jujutsu"]["git_author_url"],
            "https://github.com/martinvonz",
        )
        expected_authors = {
            "doggo": ("Karan Sharma", "https://github.com/mr-karan"),
            "mcfly": ("Andrew Cantino", "https://github.com/cantino"),
            "curlie": ("Olivier Poitrey", "https://github.com/rs"),
            "trippy": ("FujiApple", "https://github.com/fujiapple852"),
        }
        for name, (author, author_url) in expected_authors.items():
            with self.subTest(package=name):
                package_metadata = metadata["packages"][name]
                provides = BUILDER.PACKAGES[name].get("provides") or [
                    f"{name}=/usr/bin/{name}"
                ]
                self.assertEqual(package_metadata["author"], author)
                self.assertEqual(package_metadata["git_author_url"], author_url)
                self.assertEqual(package_metadata["git_repo_type"], "upstream")
                self.assertEqual(
                    package_metadata["git_repo"], BUILDER.PACKAGES[name]["url"]
                )
                self.assertIn(
                    f"Provides: {', '.join(provides)}",
                    BUILDER.recipe_text(name, BUILDER.PACKAGES[name]),
                )
        trippy = BUILDER.PACKAGES["trippy"]
        self.assertEqual(trippy["provides"], ["trip=/usr/bin/trip"])
        self.assertEqual(trippy["license"], "Apache-2.0")
        self.assertEqual(metadata["packages"]["trippy"]["git_repo"], trippy["url"])
        curlie = BUILDER.PACKAGES["curlie"]
        self.assertEqual(curlie["dependencies"], ["curl-cli"])
        self.assertIn(
            "Dependencies:\n  curl-cli",
            BUILDER.recipe_text("curlie", curlie),
        )

    def test_disk_and_git_tools_have_pinned_static_release_assets(self) -> None:
        """Keep dua and gitui tied to checksummed Linux release binaries."""
        for name in ("dua", "gitui"):
            with self.subTest(package=name):
                package = BUILDER.PACKAGES[name]
                self.assertEqual(package["arch"], "x86_64")
                self.assertEqual(
                    set(package["binary_members"]),
                    set(package["binary_member_sha256"]),
                )
                digests = (
                    package["archive_sha256"],
                    package["binary_archive_sha256"],
                    *package["binary_member_sha256"].values(),
                )
                for digest in digests:
                    self.assertEqual(len(digest), 64)
                    self.assertTrue(all(char in "0123456789abcdef" for char in digest))
                self.assertTrue(package["license_source"])
        gitui_recipe = BUILDER.recipe_text("gitui", BUILDER.PACKAGES["gitui"])
        self.assertIn("Dependencies:\n  git", gitui_recipe)

    def test_rust_developer_tools_have_pinned_musl_releases(self) -> None:
        """Keep Rust testing and audit tools pinned to static upstream assets."""
        expected_authors = {
            "cargo-nextest": ("Rain", "https://github.com/sunshowers"),
            "cargo-deny": ("Embark Studios and Jake Shadle", "https://github.com/Jake-Shadle"),
            "cargo-audit": ("Tony Arcieri", "https://github.com/Bascule"),
        }
        metadata = json.loads(
            (Path(__file__).resolve().parents[1] / "data/package-metadata.json").read_text(
                encoding="utf-8"
            )
        )
        for name, (author, author_url) in expected_authors.items():
            with self.subTest(package=name):
                package = BUILDER.PACKAGES[name]
                self.assertEqual(package["arch"], "x86_64")
                self.assertEqual(package["dependencies"][0], "cargo")
                self.assertEqual(
                    set(package["binary_members"]),
                    set(package["binary_member_sha256"]),
                )
                for digest in (
                    package["archive_sha256"],
                    package["binary_archive_sha256"],
                    *package["binary_member_sha256"].values(),
                ):
                    self.assertEqual(len(digest), 64)
                    self.assertTrue(all(char in "0123456789abcdef" for char in digest))
                package_metadata = metadata["packages"][name]
                self.assertEqual(package_metadata["author"], author)
                self.assertEqual(package_metadata["git_author_url"], author_url)
                self.assertEqual(package_metadata["git_repo"], package["url"])
                self.assertEqual(package_metadata["git_repo_type"], "upstream")
                recipe = BUILDER.recipe_text(name, package)
                self.assertIn(f"Provides: {name}=/usr/bin/{name}", recipe)
                self.assertIn("Dependencies:\n  cargo", recipe)
                if name == "cargo-audit":
                    self.assertIn("  git\nProvides:", recipe)

    def test_existing_dependency_recipes_use_multiline_format(self) -> None:
        """Keep previously committed recipes aligned with the current renderer."""
        for name in ("curlie", "gitui", "jujutsu", "navi"):
            with self.subTest(package=name):
                recipe = (
                    Path(__file__).resolve().parents[1]
                    / "packages/recipes"
                    / name
                    / "ARKPKG"
                ).read_text(encoding="utf-8")
                dependencies = BUILDER.PACKAGES[name]["dependencies"]
                lines = recipe.splitlines()
                index = lines.index("Dependencies:")
                actual = []
                for line in lines[index + 1 :]:
                    if not line.startswith("  "):
                        break
                    actual.append(line[2:])
                self.assertEqual(actual, dependencies)

    def test_recipe_text_records_optional_runtime_dependencies(self) -> None:
        """Write declared runtime dependencies into generated package metadata."""
        package = {
            "description": "fixture command-line tool",
            "version": "1.0",
            "arch": "x86_64",
            "url": "https://example.test/fixture",
            "license": "MIT",
            "dependencies": ["fzf", "git"],
        }

        recipe = BUILDER.recipe_text("fixture", package)

        self.assertIn("Dependencies:\n  fzf\n  git", recipe)
        self.assertLess(recipe.index("Dependencies:"), recipe.index("Provides:"))

    def test_archive_owner_filter_matches_exact_package_name(self) -> None:
        """Keep similarly named packages in the ownership collision set."""
        with tempfile.TemporaryDirectory() as temporary:
            packages = Path(temporary) / "packages"
            packages.mkdir()
            write_package_archive(
                packages / "fixture-tools-1.0-x86_64.ark",
                "fixture-tools",
                "1.0",
                {"usr/bin/tool": b"unrelated package"},
            )

            owned = BUILDER.archive_owners(Path(temporary), {"fixture"})

            self.assertIn("usr/bin/tool", owned)

    def test_new_cache_is_private_even_with_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            previous_umask = os.umask(0)
            try:
                BUILDER.prepare_cache(cache)
            finally:
                os.umask(previous_umask)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)

    def test_existing_owned_cache_can_be_readable_by_others(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            cache.mkdir()
            cache.chmod(0o755)
            BUILDER.prepare_cache(cache)
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o755)

    def test_cache_writable_by_another_user_is_rejected(self) -> None:
        for mode in (0o770, 0o702):
            with self.subTest(mode=oct(mode)), tempfile.TemporaryDirectory() as temporary:
                cache = Path(temporary) / "cache"
                cache.mkdir()
                cache.chmod(mode)
                with self.assertRaisesRegex(ValueError, "unsafe source cache"):
                    BUILDER.prepare_cache(cache)
                self.assertEqual(stat.S_IMODE(cache.stat().st_mode), mode)

    def test_cache_owned_by_another_user_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            cache.mkdir(mode=0o700)
            with (
                mock.patch.object(BUILDER.os, "geteuid", return_value=cache.stat().st_uid + 1),
                self.assertRaisesRegex(ValueError, "unsafe source cache"),
            ):
                BUILDER.prepare_cache(cache)

    def test_build_rejects_cache_symlink_before_reading_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.symlink_to(root, target_is_directory=True)
            args = argparse.Namespace(repo_root=root, source_cache=cache)
            with (
                mock.patch.object(BUILDER, "download") as download,
                self.assertRaisesRegex(ValueError, "unsafe source cache"),
            ):
                BUILDER.build(args)
            download.assert_not_called()

    def test_modified_cache_copy_is_reverified_and_redownloaded(self) -> None:
        trusted = b"verified archive"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            work = root / "work"
            cache.mkdir(mode=0o700)
            work.mkdir(mode=0o700)
            target = cache / "source.tar.gz"
            target.write_bytes(trusted)
            copyfile = BUILDER.shutil.copyfile
            cache_copy_seen = False

            def corrupt_copy(source, destination):
                nonlocal cache_copy_seen
                copyfile(source, destination)
                if source == target and not cache_copy_seen:
                    cache_copy_seen = True
                    Path(destination).write_bytes(b"changed during cache copy")

            with (
                mock.patch.object(BUILDER.shutil, "copyfile", side_effect=corrupt_copy),
                mock.patch.object(
                    BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(trusted)
                ) as network,
            ):
                snapshot = BUILDER.download(
                    "https://example.test/source.tar.gz",
                    hashlib.sha256(trusted).hexdigest(),
                    cache,
                    work,
                )
            network.assert_called_once()
            self.assertEqual(snapshot.parent, work)
            self.assertEqual(snapshot.read_bytes(), trusted)
            self.assertEqual(target.read_bytes(), trusted)

    def test_bad_checksum_is_rejected_without_publishing_to_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            victim = root / "victim"
            victim.write_bytes(b"unchanged")
            (cache / "source.tar.gz.download").symlink_to(victim)
            with (
                mock.patch.object(
                    BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")
                ),
                self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
            ):
                BUILDER.download(
                    "https://example.test/source.tar.gz",
                    hashlib.sha256(b"trusted").hexdigest(),
                    cache,
                    root,
                )
            self.assertFalse((cache / "source.tar.gz").exists())
            self.assertEqual(victim.read_bytes(), b"unchanged")
            self.assertEqual(
                sorted(path.name for path in cache.iterdir()), ["source.tar.gz.download"]
            )

    def test_archive_reader_supports_zip_members(self) -> None:
        """Read regular files from ZIP release assets."""
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "fixture.zip"
            archive_path.write_bytes(
                zip_archive({"fixture/LICENSE": b"MIT", "fixture/tool": b"tool"})
            )
            self.assertEqual(
                BUILDER.read_archive_file(archive_path, "fixture/tool"), b"tool"
            )

    def test_archive_reader_rejects_unsafe_paths_and_zip_symlinks(self) -> None:
        """Reject traversal requests and symlink members in ZIP assets."""
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "fixture.zip"
            symlink = zipfile.ZipInfo("fixture/link")
            symlink.create_system = 3
            symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
            output = io.BytesIO()
            with zipfile.ZipFile(output, mode="w") as archive:
                archive.writestr(symlink, "../../outside")
            archive_path.write_bytes(output.getvalue())
            with self.assertRaisesRegex(ValueError, "unsafe upstream archive member"):
                BUILDER.read_archive_file(archive_path, "../outside")
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                BUILDER.read_archive_file(archive_path, "fixture/link")

    def test_archive_reader_limits_expanded_member_size(self) -> None:
        """Reject archive members that exceed the expansion bound."""
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "fixture.zip"
            archive_path.write_bytes(zip_archive({"fixture/tool": b"12345"}))
            with (
                mock.patch.object(BUILDER, "MAX_UPSTREAM_MEMBER_BYTES", 4),
                self.assertRaisesRegex(ValueError, "exceeds 4 bytes"),
            ):
                BUILDER.read_archive_file(archive_path, "fixture/tool")

    def test_binary_member_limit_is_separate_and_still_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "release.zip"
            archive_path.write_bytes(zip_archive({"tool": b"binary-member"}))
            with (
                mock.patch.object(BUILDER, "MAX_UPSTREAM_MEMBER_BYTES", 8),
                mock.patch.object(BUILDER, "MAX_BINARY_MEMBER_BYTES", 16),
            ):
                with self.assertRaisesRegex(ValueError, "exceeds 8 bytes"):
                    BUILDER.read_archive_file(archive_path, "tool")
                self.assertEqual(
                    BUILDER.read_archive_file(
                        archive_path,
                        "tool",
                        max_bytes=BUILDER.MAX_BINARY_MEMBER_BYTES,
                    ),
                    b"binary-member",
                )

    def test_build_uses_verified_bytes_after_cache_replacement(self) -> None:
        for cache_hit in (False, True):
            for with_binary in (False, True):
                with self.subTest(cache_hit=cache_hit, with_binary=with_binary):
                    self.check_build_after_cache_replacement(cache_hit, with_binary)

    def test_build_reads_zip_binary_archive_and_emits_multiple_provides(self) -> None:
        """Build multiple executables from one verified ZIP release."""
        tool = b"#!/bin/sh\necho tool\n"
        helper = b"#!/bin/sh\necho helper\n"
        readme = b"fixture documentation\n"
        completion = b"complete -W tool tool\n"
        source = source_archive(b"fixture license\n")
        release = zip_archive(
            {
                "release/tool": tool,
                "release/helper": helper,
                "release/README.md": readme,
                "release/completions/tool.bash": completion,
            }
        )
        package = {
            "version": "1.0",
            "arch": "x86_64",
            "url": "https://example.test/fixture",
            "archive": "https://example.test/source.tar.gz",
            "archive_sha256": hashlib.sha256(source).hexdigest(),
            "root": "fixture-1.0",
            "license_source": "LICENSE",
            "license_path": "usr/share/doc/fixture/copyright",
            "files": {},
            "binary_archive": "https://example.test/release.zip",
            "binary_archive_sha256": hashlib.sha256(release).hexdigest(),
            "binary_members": {
                "release/tool": "usr/bin/tool",
                "release/helper": "usr/bin/helper",
            },
            "binary_member_sha256": {
                "release/tool": hashlib.sha256(tool).hexdigest(),
                "release/helper": hashlib.sha256(helper).hexdigest(),
            },
            "binary_files": {
                "release/README.md": "usr/share/doc/fixture/README.md",
                "release/completions/tool.bash": (
                    "usr/share/bash-completion/completions/tool"
                ),
            },
            "provides": ["tool=/usr/bin/tool", "helper=/usr/bin/helper"],
            "description": "Fixture command line tools",
            "license": "MIT",
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            repo = root / "repo"
            (repo / "packages").mkdir(parents=True)
            write_package_archive(
                repo / "packages/fixture-0.9-x86_64.ark",
                "fixture",
                "0.9",
                {"usr/bin/tool": b"older package version"},
            )
            source_path = root / "source.tar.gz"
            release_path = root / "release.zip"
            source_path.write_bytes(source)
            release_path.write_bytes(release)
            payloads = {}
            directory_modes = {}

            def download(url, *_args):
                return source_path if url.endswith("source.tar.gz") else release_path

            def build_package(command, **_kwargs):
                payload = Path(command[1]) / "package"
                directory_modes.update(
                    (
                        path.relative_to(payload).as_posix(),
                        stat.S_IMODE(path.stat().st_mode),
                    )
                    for path in (payload, *payload.rglob("*"))
                    if path.is_dir() and not path.is_symlink()
                )
                payloads.update(
                    (path.relative_to(payload).as_posix(), path.read_bytes())
                    for path in payload.rglob("*")
                    if path.is_file()
                )
                Path(command[-1]).write_bytes(b"built archive")

            args = argparse.Namespace(
                repo_root=repo,
                source_cache=cache,
                staging=root / "staging",
                packages=["fixture"],
                arkpkg_build=root / "arkpkg-build",
            )
            previous_umask = os.umask(0o027)
            try:
                with (
                    mock.patch.object(BUILDER, "PACKAGES", {"fixture": package}),
                    mock.patch.object(BUILDER, "download", side_effect=download),
                    mock.patch.object(
                        BUILDER.subprocess, "run", side_effect=build_package
                    ),
                ):
                    BUILDER.build(args)
            finally:
                os.umask(previous_umask)

            self.assertEqual(
                payloads,
                {
                    "usr/bin/tool": tool,
                    "usr/bin/helper": helper,
                    "usr/share/doc/fixture/README.md": readme,
                    "usr/share/doc/fixture/copyright": b"fixture license\n",
                    "usr/share/bash-completion/completions/tool": completion,
                },
            )
            self.assertTrue(directory_modes)
            self.assertEqual(set(directory_modes.values()), {0o755})
            recipe = (repo / "packages/recipes/fixture/ARKPKG").read_text()
            self.assertIn(
                "Provides: tool=/usr/bin/tool, helper=/usr/bin/helper", recipe
            )
            self.assertTrue((repo / "packages/fixture-1.0-x86_64.ark").is_file())

    def test_build_rejects_oversized_package_archive_atomically(self) -> None:
        """Do not publish or retain an oversized package archive."""
        source = source_archive(b"fixture license\n")
        package = {
            "version": "1.0",
            "arch": "any",
            "url": "https://example.test/fixture",
            "archive": "https://example.test/source.tar.gz",
            "archive_sha256": hashlib.sha256(source).hexdigest(),
            "root": "fixture-1.0",
            "license_source": "LICENSE",
            "license_path": "usr/share/doc/fixture/copyright",
            "files": {},
            "description": "Fixture",
            "license": "MIT",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            repo = root / "repo"
            packages = repo / "packages"
            packages.mkdir(parents=True)
            source_path = root / "source.tar.gz"
            source_path.write_bytes(source)
            args = argparse.Namespace(
                repo_root=repo,
                source_cache=cache,
                staging=root / "staging",
                packages=["fixture"],
                arkpkg_build=root / "arkpkg-build",
            )

            def write_oversized_archive(command, **_kwargs):
                Path(command[-1]).write_bytes(b"larger than the configured limit")

            with (
                mock.patch.object(BUILDER, "PACKAGES", {"fixture": package}),
                mock.patch.object(BUILDER, "download", return_value=source_path),
                mock.patch.object(
                    BUILDER.subprocess, "run", side_effect=write_oversized_archive
                ),
                mock.patch.object(BUILDER, "MAX_ARCHIVE_BYTES", 4),
                self.assertRaisesRegex(ValueError, "maximum supported size"),
            ):
                BUILDER.build(args)

            self.assertFalse((packages / "fixture-1.0-any.ark").exists())
            self.assertEqual(list(packages.glob("*.tmp")), [])

    def check_build_after_cache_replacement(
        self, cache_hit: bool, with_binary: bool
    ) -> None:
        trusted = b"#!/bin/sh\necho trusted\n"
        malicious = b"#!/bin/sh\necho injected\n"
        archive = source_archive(trusted)
        responses = {"source.tar.gz": archive, "fixture": trusted}
        replacements = {
            "source.tar.gz": source_archive(malicious),
            "fixture": malicious,
        }
        package = {
            "version": "1.0",
            "arch": "any",
            "url": "https://example.test/fixture",
            "archive": "https://example.test/source.tar.gz",
            "archive_sha256": hashlib.sha256(archive).hexdigest(),
            "root": "fixture-1.0",
            "license_source": "LICENSE",
            "license_path": "usr/share/doc/fixture/copyright",
            "files": {"tool": "usr/bin/fixture"},
            "description": "Fixture",
            "license": "MIT",
        }
        if with_binary:
            package.update(
                binary="https://example.test/fixture",
                binary_sha256=hashlib.sha256(trusted).hexdigest(),
                binary_path="usr/bin/fixture-bin",
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            repo = root / "repo"
            (repo / "packages").mkdir(parents=True)
            if cache_hit:
                for name, content in responses.items():
                    (cache / name).write_bytes(content)
            payloads = {}
            download = BUILDER.download

            def replace_after_verification(url, *args):
                verified = download(url, *args)
                name = url.rsplit("/", 1)[-1]
                (cache / name).write_bytes(replacements[name])
                return verified

            def urlopen(request, **kwargs):
                return io.BytesIO(responses[request.full_url.rsplit("/", 1)[-1]])

            def build_package(command, **kwargs):
                payload = Path(command[1]) / "package"
                payloads.update(
                    (path.relative_to(payload).as_posix(), path.read_bytes())
                    for path in payload.rglob("*")
                    if path.is_file()
                )
                Path(command[-1]).write_bytes(b"built archive")

            args = argparse.Namespace(
                repo_root=repo,
                source_cache=cache,
                staging=root / "staging",
                packages=["fixture"],
                arkpkg_build=root / "arkpkg-build",
            )
            with (
                mock.patch.object(BUILDER, "PACKAGES", {"fixture": package}),
                mock.patch.object(
                    BUILDER, "download", side_effect=replace_after_verification
                ),
                mock.patch.object(
                    BUILDER.urllib.request, "urlopen", side_effect=urlopen
                ) as network,
                mock.patch.object(
                    BUILDER.subprocess, "run", side_effect=build_package
                ),
            ):
                BUILDER.build(args)
            self.assertEqual(network.call_count, 0 if cache_hit else 1 + with_binary)
            expected = {
                "usr/bin/fixture": trusted,
                "usr/share/doc/fixture/copyright": trusted,
            }
            if with_binary:
                expected["usr/bin/fixture-bin"] = trusted
            self.assertEqual(payloads, expected)


if __name__ == "__main__":
    unittest.main()
