#!/usr/bin/env python3
"""Exercise source verification and cache substitution at the build boundary."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from source_cache import source_snapshot, trusted_cache

SCRIPTS = Path(__file__).resolve().parent


def load_builder(filename: str):
    spec = importlib.util.spec_from_file_location(filename, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDERS = (
    (load_builder("build-xfe.py"), "fox1.6-1.6.57", "fox1.6_1.6.57.orig.tar.gz"),
    (load_builder("build-xcb-util.py"), "xcb-util-0.4.1", "xcb-util-0.4.1.tar.xz"),
    (
        load_builder("build-xcursor-themes.py"),
        "xcursor-themes-1.0.7",
        "xcursor-themes-1.0.7.tar.xz",
    ),
)


def archive_bytes(name: str, payload: bytes = b"verified source") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


class BuildStopped(Exception):
    pass


class SourceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arkpkg-source-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.snapshots = self.root / "snapshots"
        self.good = archive_bytes("upstream/configure")
        self.digest = hashlib.sha256(self.good).hexdigest()

    def snapshot(self, digest: str | None = None) -> Path:
        return source_snapshot(
            self.cache,
            self.snapshots,
            "source.tar.xz",
            "https://example.invalid/source",
            self.digest if digest is None else digest,
            user_agent="test",
            timeout=1,
        )

    def test_download_is_verified_and_cached_for_reuse(self) -> None:
        with mock.patch(
            "source_cache.urllib.request.urlopen", return_value=io.BytesIO(self.good)
        ) as download:
            snapshot = self.snapshot()
        download.assert_called_once()
        self.assertEqual(snapshot.read_bytes(), self.good)
        self.assertEqual((self.cache / snapshot.name).read_bytes(), self.good)
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        snapshot.unlink()
        with mock.patch("source_cache.urllib.request.urlopen") as download:
            self.assertEqual(self.snapshot().read_bytes(), self.good)
        download.assert_not_called()

    def test_bad_download_is_not_published(self) -> None:
        with (
            mock.patch(
                "source_cache.urllib.request.urlopen", return_value=io.BytesIO(b"bad")
            ),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.snapshot()
        self.assertFalse((self.cache / "source.tar.xz").exists())

    def test_cached_content_changes_during_copy_fail_verification(self) -> None:
        trusted_cache(self.cache)
        candidate = self.cache / "source.tar.xz"
        candidate.write_bytes(self.good)
        original_copy = shutil.copyfileobj

        def change_then_copy(source, output):
            candidate.write_bytes(b"changed while open")
            original_copy(source, output)

        with (
            mock.patch("source_cache.shutil.copyfileobj", side_effect=change_then_copy),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.snapshot()

    def test_rejects_writable_cache_and_replaceable_parent(self) -> None:
        for mode in (0o770, 0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                self.cache.mkdir(exist_ok=True)
                self.cache.chmod(mode)
                with self.assertRaisesRegex(ValueError, "writable by others"):
                    self.snapshot()
        self.cache.chmod(0o700)
        self.root.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "writable by others"):
            self.snapshot()
        self.root.chmod(0o700)

    def test_rejects_cache_owned_by_unrelated_user(self) -> None:
        trusted_cache(self.cache)
        original_lstat = Path.lstat

        def foreign_owner(path):
            info = original_lstat(path)
            if path == self.cache:
                fields = list(info)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return info

        with (
            mock.patch.object(Path, "lstat", autospec=True, side_effect=foreign_owner),
            self.assertRaisesRegex(ValueError, "unsafe source cache directory"),
        ):
            self.snapshot()

    def test_preserves_archive_path_and_root_checks(self) -> None:
        for builder, root, filename in BUILDERS:
            for member in (
                "/absolute/path",
                f"{root}/../escape",
                "unexpected/configure",
            ):
                with self.subTest(builder=filename, member=member):
                    trusted_cache(self.cache)
                    content = archive_bytes(member)
                    (self.cache / filename).write_bytes(content)
                    with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                        digest = hashlib.sha256(content).hexdigest()
                        with (
                            mock.patch.multiple(builder, **self.pins(builder, digest)),
                            self.assertRaisesRegex(
                                ValueError, "unsafe source path|unexpected source root"
                            ),
                        ):
                            if hasattr(builder, "FOX_NAME"):
                                builder.source_archive(
                                    self.cache,
                                    Path(temporary),
                                    filename,
                                    builder.FOX_URL,
                                    digest,
                                    root,
                                )
                            else:
                                builder.source_archive(self.cache, Path(temporary))

    @staticmethod
    def pins(builder, digest: str) -> dict[str, str]:
        if hasattr(builder, "FOX_NAME"):
            return {"FOX_SHA256": digest, "XFE_SHA256": digest}
        return {"SHA256" if hasattr(builder, "NAME") else "SOURCE_SHA256": digest}

    def test_all_builders_copy_verified_snapshot_after_cache_substitution(self) -> None:
        for builder, root, filename in BUILDERS:
            with self.subTest(builder=filename):
                self.check_builder_copy(builder, root, filename)

    def check_builder_copy(self, builder, root: str, filename: str) -> None:
        trusted_cache(self.cache)
        good = archive_bytes(f"{root}/configure")
        candidate = self.cache / filename
        candidate.write_bytes(good)
        if hasattr(builder, "FOX_NAME"):
            xfe = archive_bytes(f"{builder.XFE_NAME}-{builder.XFE_VERSION}/configure")
            (self.cache / builder.XFE_ARCHIVE).write_bytes(xfe)
        sysroot = self.root / root
        binaries = sysroot / "usr/bin"
        binaries.mkdir(parents=True)
        for binary in ("bash", "gcc", "make", "pkg-config", "tar", "strip"):
            (binaries / binary).touch()
        args = argparse.Namespace(
            sysroot=sysroot,
            source_cache=self.cache,
            output_staging=self.root / f"staging-{filename}",
            arkpkg=self.root / "arkpkg",
            arkpkg_build=self.root / "arkpkg-build",
            build_dependency_archive=[],
            output_dir=self.root / "packages",
            jobs=1,
            xorg_util_macros_archive=self.root / "macros.ark",
        )
        copied = []

        def privileged(command, **kwargs):
            if command[0] == "install":
                if "-d" not in command:
                    source = Path(command[-2])
                    self.assertNotEqual(source, candidate)
                    self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o700)
                    self.assertEqual(source.stat().st_uid, os.geteuid())
                    candidate.write_bytes(b"attacker replacement")
                    copied.append(source)
                subprocess.run(command, check=True)
            elif command[0] == "chroot":
                installed = list((sysroot / "tmp").glob(f"*/{filename}"))
                self.assertEqual(len(installed), 1)
                self.assertEqual(installed[0].read_bytes(), good)
                raise BuildStopped

        pins = self.pins(builder, hashlib.sha256(good).hexdigest())
        if hasattr(builder, "FOX_NAME"):
            pins["XFE_SHA256"] = hashlib.sha256(xfe).hexdigest()
        with (
            mock.patch.multiple(builder, **pins),
            mock.patch.object(builder, "privileged", side_effect=privileged),
            mock.patch.object(builder, "installed_packages", return_value={"macros"}),
            mock.patch.object(builder, "package_name", return_value="macros"),
            self.assertRaises(BuildStopped),
        ):
            if hasattr(builder, "main"):
                with mock.patch.object(
                    builder.argparse.ArgumentParser,
                    "parse_args",
                    return_value=args,
                ):
                    builder.main()
            else:
                builder.build(args)
        self.assertEqual(len(copied), 1)
        self.assertFalse(
            copied[0].parent.exists(), "snapshot must be cleaned on failure"
        )


if __name__ == "__main__":
    unittest.main()
