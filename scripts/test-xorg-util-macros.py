"""Check macros builder staging isolation and the staged packaging flow."""

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
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "build_xorg_util_macros", Path(__file__).with_name("build-xorg-util-macros.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load build-xorg-util-macros.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


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
