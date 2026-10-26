#!/usr/bin/env python3
"""Test verified source bytes and safe staging for the X.Org macros builder."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().with_name("build-xorg-util-macros.py")
SPEC = importlib.util.spec_from_file_location("xorg_util_macros", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load build-xorg-util-macros.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def source_archive(marker: str, *, root: str | None = None) -> bytes:
    if root is None:
        root = f"{builder.NAME}-{builder.VERSION}"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        for name, data in (
            (
                "configure",
                f"#!/bin/sh\nprintf '%s\\n' '{marker}' > configured\n".encode(),
            ),
            ("COPYING", b"fixture license\n"),
        ):
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(data)
            member.mode = 0o755 if name == "configure" else 0o644
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


class XorgUtilMacrosSourceTests(unittest.TestCase):
    def test_cache_mutations_after_verification_do_not_change_executed_source(
        self,
    ) -> None:
        for mutation in ("replace", "overwrite"):
            with self.subTest(mutation=mutation):
                self.run_build(mutation=mutation)

    def test_downloaded_source_is_verified_and_used(self) -> None:
        self.run_build(download=True)

    def run_build(self, *, mutation: str | None = None, download: bool = False) -> None:
        trusted = source_archive("verified")
        untrusted = source_archive("unverified")
        obtain_source = builder.obtain_source
        run = subprocess.run
        with tempfile.TemporaryDirectory(prefix="arkpkg-macros-test-") as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            cached = cache / f"{builder.NAME}-{builder.VERSION}.tar.xz"
            staging = root / "staging"
            if not download:
                cached.write_bytes(trusted)

            def obtain_then_mutate(cache_path: Path):
                source = obtain_source(cache_path)
                if mutation == "replace":
                    replacement = root / "replacement.tar.xz"
                    replacement.write_bytes(untrusted)
                    replacement.replace(cached)
                elif mutation == "overwrite":
                    cached.write_bytes(untrusted)
                return source

            def run_configure(command, **kwargs):
                if command[0] == "./configure":
                    return run(command, **kwargs)
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(builder, "SHA256", hashlib.sha256(trusted).hexdigest()),
                patch.object(builder, "obtain_source", side_effect=obtain_then_mutate),
                patch.object(
                    urllib.request, "urlopen", return_value=io.BytesIO(trusted)
                ) as fetch,
                patch.object(
                    builder.subprocess, "run", side_effect=run_configure
                ) as commands,
                patch.object(
                    sys,
                    "argv",
                    [
                        str(SCRIPT),
                        "--source-cache",
                        str(cache),
                        "--staging",
                        str(staging),
                        "--arkpkg-build",
                        str(root / "arkpkg-build"),
                    ],
                ),
            ):
                builder.main()

            source_root = staging / "source" / f"{builder.NAME}-{builder.VERSION}"
            self.assertEqual((source_root / "configured").read_text(), "verified\n")
            self.assertEqual(commands.call_count, 3)
            self.assertEqual(fetch.call_count, int(download))
            license_file = (
                staging / "payload/usr/share/licenses/xorg-util-macros/COPYING"
            )
            self.assertEqual(license_file.read_bytes(), b"fixture license\n")
            if mutation:
                self.assertEqual(cached.read_bytes(), untrusted)

    def test_checksum_mismatch_stops_before_extraction_or_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="arkpkg-macros-test-") as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            (cache / f"{builder.NAME}-{builder.VERSION}.tar.xz").write_bytes(
                source_archive("unverified")
            )
            staging = root / "staging"
            with (
                patch.object(
                    builder,
                    "SHA256",
                    hashlib.sha256(source_archive("verified")).hexdigest(),
                ),
                patch.object(builder.subprocess, "run") as commands,
                patch.object(
                    sys,
                    "argv",
                    [
                        str(SCRIPT),
                        "--source-cache",
                        str(cache),
                        "--staging",
                        str(staging),
                        "--arkpkg-build",
                        str(root / "arkpkg-build"),
                    ],
                ),
                self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
            ):
                builder.main()
            commands.assert_not_called()
            self.assertFalse((staging / "source").exists())

    def test_source_path_validation_is_preserved(self) -> None:
        for archive_root in ("../escape", "/absolute", "unexpected-root"):
            with (
                self.subTest(root=archive_root),
                tempfile.TemporaryDirectory() as temporary,
            ):
                cache = Path(temporary)
                data = source_archive("unused", root=archive_root)
                (cache / f"{builder.NAME}-{builder.VERSION}.tar.xz").write_bytes(data)
                with (
                    patch.object(builder, "SHA256", hashlib.sha256(data).hexdigest()),
                    self.assertRaisesRegex(
                        ValueError, "unsafe source path|unexpected source root"
                    ),
                ):
                    builder.obtain_source(cache)


class MacrosCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="macros-cache-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.cached = self.cache / f"{builder.NAME}-{builder.VERSION}.tar.xz"
        self.trusted = source_archive("verified")
        digest_patch = patch.object(
            builder, "SHA256", hashlib.sha256(self.trusted).hexdigest()
        )
        digest_patch.start()
        self.addCleanup(digest_patch.stop)

    def test_download_substitution_preserves_symlink_and_hardlink_targets(self) -> None:
        victim = self.root / "victim"
        for link in ("symlink", "hardlink"):
            with self.subTest(link=link):
                self.cached.unlink(missing_ok=True)
                victim.write_bytes(b"unrelated user data")

                def respond(*args, link=link, **kwargs):
                    if link == "symlink":
                        self.cached.symlink_to(victim)
                    else:
                        os.link(victim, self.cached)
                    return io.BytesIO(self.trusted)

                with patch.object(urllib.request, "urlopen", side_effect=respond):
                    self.assertEqual(builder.obtain_source(self.cache), self.trusted)
                self.assertEqual(victim.read_bytes(), b"unrelated user data")
                self.assertFalse(self.cached.is_symlink())
                self.assertFalse(self.cached.samefile(victim))
                self.assertEqual(self.cached.read_bytes(), self.trusted)
                self.assertEqual(list(self.cache.iterdir()), [self.cached])
                self.cached.unlink()

    def test_dangling_symlink_target_is_not_created(self) -> None:
        self.cache.mkdir(mode=0o700)
        victim = self.root / "missing"
        self.cached.symlink_to(victim)
        with patch.object(
            urllib.request, "urlopen", return_value=io.BytesIO(self.trusted)
        ):
            self.assertEqual(builder.obtain_source(self.cache), self.trusted)
        self.assertFalse(victim.exists())
        self.assertFalse(self.cached.is_symlink())
        self.assertEqual(self.cached.read_bytes(), self.trusted)
        self.assertEqual(stat.S_IMODE(self.cached.stat().st_mode), 0o600)

    def test_unsafe_cache_permissions_and_parent_are_rejected(self) -> None:
        self.cache.mkdir(mode=0o700)
        for mode in (0o770, 0o777, 0o1777):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with (
                    patch.object(
                        urllib.request, "urlopen", return_value=io.BytesIO(self.trusted)
                    ) as download,
                    self.assertRaisesRegex(ValueError, "writable by others"),
                ):
                    builder.obtain_source(self.cache)
                download.assert_not_called()
        self.cache.chmod(0o700)
        self.root.chmod(0o777)
        try:
            with (
                patch.object(
                    urllib.request, "urlopen", return_value=io.BytesIO(self.trusted)
                ) as download,
                self.assertRaisesRegex(ValueError, "writable by others"),
            ):
                builder.obtain_source(self.cache)
            download.assert_not_called()
        finally:
            self.root.chmod(0o700)

    def test_cache_owned_by_another_user_is_rejected(self) -> None:
        self.cache.mkdir(mode=0o700)
        original_lstat = Path.lstat

        def foreign_owner(path):
            info = original_lstat(path)
            if path == self.cache:
                fields = list(info)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return info

        with (
            patch.object(Path, "lstat", autospec=True, side_effect=foreign_owner),
            patch.object(
                urllib.request, "urlopen", return_value=io.BytesIO(self.trusted)
            ) as download,
            self.assertRaisesRegex(ValueError, "unsafe source cache directory"),
        ):
            builder.obtain_source(self.cache)
        download.assert_not_called()

    def test_failed_downloads_are_not_published_and_snapshots_are_cleaned(self) -> None:
        class InterruptedResponse(io.BytesIO):
            def read(self, size=-1):
                if self.tell():
                    raise OSError("interrupted download")
                return super().read(size)

        for response, error in (
            (io.BytesIO(b"bad source"), ValueError),
            (InterruptedResponse(b"partial source"), OSError),
        ):
            with (
                self.subTest(error=error),
                patch.object(urllib.request, "urlopen", return_value=response),
                patch.object(tempfile, "tempdir", str(self.root)),
                self.assertRaises(error),
            ):
                builder.obtain_source(self.cache)
            self.assertEqual(list(self.cache.iterdir()), [])
            self.assertEqual(list(self.root.iterdir()), [self.cache])

    def test_new_cache_is_private_and_verified_download_is_reused(self) -> None:
        original_umask = os.umask(0o022)
        self.addCleanup(os.umask, original_umask)
        with patch.object(
            urllib.request, "urlopen", return_value=io.BytesIO(self.trusted)
        ) as download:
            self.assertEqual(builder.obtain_source(self.cache), self.trusted)
            download.assert_called_once()
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o700)
        with patch.object(urllib.request, "urlopen") as download:
            self.assertEqual(builder.obtain_source(self.cache), self.trusted)
            download.assert_not_called()


class MacrosStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="macros-staging-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        archive_path = self.cache / f"{builder.NAME}-{builder.VERSION}.tar.xz"
        with tarfile.open(archive_path, "w:xz") as archive:
            license_entry = tarfile.TarInfo(
                f"{builder.NAME}-{builder.VERSION}/COPYING"
            )
            license_entry.size = len(b"fixture license\n")
            archive.addfile(license_entry, io.BytesIO(b"fixture license\n"))
        digest_patch = patch.object(
            builder, "SHA256", hashlib.sha256(archive_path.read_bytes()).hexdigest()
        )
        digest_patch.start()
        self.addCleanup(digest_patch.stop)
        self.workspaces: list[Path] = []

    def run_command(self, command, *, cwd=None, check=False) -> None:
        self.assertTrue(check)
        if command[0] == "./configure":
            staging = cwd.parents[1]
            self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o700)
            self.workspaces.append(staging)
        elif command[0] == "make":
            payload = Path(command[1].removeprefix("DESTDIR="))
            for relative in (
                "usr/share/aclocal/xorg-macros.m4",
                "usr/share/pkgconfig/xorg-macros.pc",
            ):
                destination = payload / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("fixture payload\n")
        else:
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(Path(command[1]).name, "build-arkana-packages.py")
            payload = Path(command[command.index("--staging") + 1])
            self.assertTrue((payload / "usr/share/aclocal/xorg-macros.m4").is_file())
            self.assertTrue((payload / "usr/share/pkgconfig/xorg-macros.pc").is_file())
            self.assertEqual(
                (payload / "usr/share/licenses/xorg-util-macros/COPYING").read_bytes(),
                b"fixture license\n",
            )
            self.assertEqual(command[command.index("--packages") + 1], "xorg-util-macros")
            self.assertEqual(
                command[command.index("--output-dir") + 1], str(self.root / "output")
            )

    def invoke(self, *arguments: str, side_effect=None):
        argv = [
            "builder",
            "--source-cache", str(self.cache),
            "--arkpkg-build", str(self.root / "arkpkg-build"),
            "--output-dir", str(self.root / "output"),
            *arguments,
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(tempfile, "tempdir", str(self.root)),
            patch.object(
                builder.subprocess, "run", side_effect=side_effect or self.run_command
            ) as run,
            redirect_stdout(io.StringIO()),
        ):
            builder.main()
        return run

    def test_default_workspace_is_private_unique_and_cleaned(self) -> None:
        victim = self.root / "unrelated"
        victim.mkdir()
        marker = victim / "keep"
        marker.write_text("unrelated data")
        legacy = self.root / "arkpkg-xorg-macros-staging"
        legacy.symlink_to(victim, target_is_directory=True)

        for _ in range(2):
            self.assertEqual(self.invoke().call_count, 3)
        self.assertNotEqual(self.workspaces[0], self.workspaces[1])
        self.assertTrue(all(not path.exists() for path in self.workspaces))
        self.assertTrue(legacy.is_symlink())
        self.assertEqual(marker.read_text(), "unrelated data")

    def test_default_workspace_is_cleaned_after_subprocess_failures(self) -> None:
        for failing_call in (1, 2, 3):
            with self.subTest(failing_call=failing_call):
                calls = []

                def fail(command, *, calls=calls, failing_call=failing_call, **kwargs):
                    self.run_command(command, **kwargs)
                    calls.append(command)
                    if len(calls) == failing_call:
                        raise subprocess.CalledProcessError(1, command)

                with self.assertRaises(subprocess.CalledProcessError):
                    self.invoke(side_effect=fail)
                self.assertFalse(self.workspaces[-1].exists())

    def test_default_workspace_is_cleaned_after_checksum_failure(self) -> None:
        with (
            patch.object(builder, "SHA256", "0" * 64),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.invoke()
        self.assertEqual(list(self.root.glob("arkpkg-xorg-macros-staging-*")), [])

    def test_explicit_new_workspace_is_retained(self) -> None:
        staging = self.root / "new-parent" / "staging"
        self.assertEqual(self.invoke("--staging", os.path.relpath(staging)).call_count, 3)
        self.assertEqual(len(self.workspaces), 1)
        self.assertTrue(self.workspaces[0].samefile(staging))
        self.assertTrue((staging / "payload").is_dir())

    def test_existing_entries_are_rejected_without_modification(self) -> None:
        victim = self.root / "unrelated"
        victim.mkdir()
        marker = victim / "keep"
        marker.write_text("unrelated data")
        symlink = self.root / "symlink"
        symlink.symlink_to(victim, target_is_directory=True)
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "missing", target_is_directory=True)

        for staging in (victim, marker, symlink, dangling):
            with self.subTest(staging=staging):
                with patch.object(builder, "obtain_source") as obtain:
                    with self.assertRaises(FileExistsError):
                        self.invoke("--staging", str(staging))
                    obtain.assert_not_called()
                self.assertEqual(marker.read_text(), "unrelated data")
                self.assertEqual(symlink.readlink(), victim)
                self.assertEqual(dangling.readlink(), self.root / "missing")


if __name__ == "__main__":
    unittest.main()
