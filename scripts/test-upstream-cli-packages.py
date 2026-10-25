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
from pathlib import Path
from unittest import mock

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


class UpstreamCacheTests(unittest.TestCase):
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

    def test_build_uses_verified_bytes_after_cache_replacement(self) -> None:
        for cache_hit in (False, True):
            for with_binary in (False, True):
                with self.subTest(cache_hit=cache_hit, with_binary=with_binary):
                    self.check_build_after_cache_replacement(cache_hit, with_binary)

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
