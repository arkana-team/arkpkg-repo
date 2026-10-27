#!/usr/bin/env python3
"""Test checksum verification at the upstream builder's cache boundary."""

import argparse
import hashlib
import importlib.util
import io
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

            def download(url, *_args):
                return source_path if url.endswith("source.tar.gz") else release_path

            def build_package(command, **_kwargs):
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
                mock.patch.object(BUILDER, "download", side_effect=download),
                mock.patch.object(BUILDER.subprocess, "run", side_effect=build_package),
            ):
                BUILDER.build(args)

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
