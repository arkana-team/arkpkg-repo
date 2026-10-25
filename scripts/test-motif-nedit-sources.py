#!/usr/bin/env python3
"""Test the source archive boundary used by the privileged Motif/NEdit builder."""

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

SPEC = importlib.util.spec_from_file_location(
    "motif_nedit_builder", Path(__file__).with_name("build-motif-nedit.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the Motif/NEdit builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def archive_bytes(root: str, license_name: str = "COPYING") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in (
            (f"{root}/configure", b"trusted build instructions\n"),
            (f"{root}/{license_name}", b"trusted license\n"),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="arkpkg-source-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir(mode=0o700)
        self.private = Path(tempfile.mkdtemp(dir=self.root))
        self.content = archive_bytes("source-1")
        self.spec = (
            "source-1.tar.gz", "https://example.invalid/source-1.tar.gz",
            hashlib.sha256(self.content).hexdigest(), "source-1",
        )
        self.cached = self.cache / self.spec[0]
        self.cached.write_bytes(self.content)

    def test_cache_changes_after_validation_do_not_reach_privileged_copy(self) -> None:
        for replace in (False, True):
            with self.subTest(replace=replace):
                self.cached.write_bytes(self.content)
                snapshot = BUILDER.get_source(self.cache, self.spec, self.private)
                if replace:
                    replacement = self.cache / "replacement"
                    replacement.write_bytes(b"unvalidated replacement")
                    replacement.replace(self.cached)
                else:
                    self.cached.write_bytes(b"unvalidated in-place change")

                def privileged(command, *, log=None):
                    if command[0] == "install":
                        # Exercise the real copy into a synthetic sysroot, without sudo.
                        subprocess.run(command, check=True)
                    else:
                        self.assertEqual(command[0], "chroot")

                with mock.patch.object(BUILDER, "privileged", side_effect=privileged):
                    _, work = BUILDER.run_target_build(
                        sysroot=self.root / "sysroot", archive=snapshot,
                        root=self.spec[3], commands=["./configure"],
                        log=self.private / "build.log",
                    )
                self.assertEqual((work / snapshot.name).read_bytes(), self.content)
                license_path = self.root / "license"
                BUILDER.copy_license(snapshot, "source-1/COPYING", license_path)
                self.assertEqual(license_path.read_bytes(), b"trusted license\n")

    def test_validates_snapshot_after_cache_changes_during_copy(self) -> None:
        copyfile = shutil.copyfile

        def copy_then_replace(source, destination):
            copyfile(source, destination)
            self.cached.write_bytes(b"changed after snapshot")

        with mock.patch.object(BUILDER.shutil, "copyfile", side_effect=copy_then_replace):
            snapshot = BUILDER.get_source(self.cache, self.spec, self.private)
        self.assertEqual(snapshot.read_bytes(), self.content)

    def test_rejects_tampering_during_snapshot_creation(self) -> None:
        copyfile = shutil.copyfile

        def replace_then_copy(source, destination):
            self.cached.write_bytes(b"changed before snapshot")
            copyfile(source, destination)

        with (
            mock.patch.object(BUILDER.shutil, "copyfile", side_effect=replace_then_copy),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            BUILDER.get_source(self.cache, self.spec, self.private)

    def test_preserves_archive_path_and_root_checks(self) -> None:
        for root, message in (
            ("../outside", "unsafe path"),
            ("/absolute", "unsafe path"),
            ("unexpected-1", "unexpected source root"),
        ):
            with self.subTest(root=root):
                content = archive_bytes(root)
                self.cached.write_bytes(content)
                spec = (*self.spec[:2], hashlib.sha256(content).hexdigest(), self.spec[3])
                with self.assertRaisesRegex(ValueError, message):
                    BUILDER.get_source(self.cache, spec, self.private)

    def test_rejects_cache_writable_by_other_users(self) -> None:
        for mode in (0o770, 0o707):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with self.assertRaisesRegex(ValueError, "source cache must be owned"):
                    BUILDER.get_source(self.cache, self.spec, self.private)

    def test_rejects_cache_owned_by_another_user(self) -> None:
        with (
            mock.patch.object(BUILDER.os, "geteuid", return_value=os.geteuid() + 1),
            self.assertRaisesRegex(ValueError, "source cache must be owned"),
        ):
            BUILDER.get_source(self.cache, self.spec, self.private)

    def test_downloads_into_new_private_cache_and_validates_snapshot(self) -> None:
        cache = self.root / "new-cache"
        with mock.patch.object(BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.content)):
            snapshot = BUILDER.get_source(cache, self.spec, self.private)
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)
        self.assertEqual(snapshot.read_bytes(), self.content)
        self.assertEqual((cache / self.spec[0]).read_bytes(), self.content)

    def test_download_does_not_follow_dangling_cache_symlink(self) -> None:
        self.cached.unlink()
        target = self.root / "must-not-be-created"
        self.cached.symlink_to(target)
        with (
            mock.patch.object(BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.content)),
            self.assertRaises(FileExistsError),
        ):
            BUILDER.get_source(self.cache, self.spec, self.private)
        self.assertFalse(target.exists())

    def test_build_uses_private_sources_for_both_packages_and_removes_them(self) -> None:
        specs = {}
        contents = {}
        for name, license_name in (("motif", "COPYING"), ("nedit", "COPYRIGHT")):
            content = archive_bytes(f"{name}-1", license_name)
            spec = (f"{name}-1.tar.gz", self.spec[1], hashlib.sha256(content).hexdigest(), f"{name}-1")
            (self.cache / spec[0]).write_bytes(content)
            specs[name] = spec
            contents[spec[0]] = content
        sources = []

        def target_build(*, sysroot, archive, root, commands, log):
            sources.append(archive)
            self.assertNotEqual(archive.parent, self.cache)
            self.assertEqual(stat.S_IMODE(archive.parent.stat().st_mode), 0o700)
            (self.cache / archive.name).write_bytes(b"replaced before build")
            self.assertEqual(archive.read_bytes(), contents[archive.name])
            payload = self.root / root
            for relative in ("usr/include/Xm", "usr/bin/mwm", "usr/bin/uil", "usr/bin/nedit", "usr/bin/nedit-client"):
                path = payload / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            return payload, self.root / f"work-{root}"

        args = argparse.Namespace(
            sysroot=self.root / "sysroot", output_dir=self.root / "output",
            staging=self.root / "staging", source_cache=self.cache,
            arkpkg=self.root / "arkpkg", arkpkg_build=self.root / "arkpkg-build",
            jobs=1, reuse_motif=False,
        )
        with (
            mock.patch.object(BUILDER, "MOTIF", specs["motif"]),
            mock.patch.object(BUILDER, "NEDIT", specs["nedit"]),
            mock.patch.object(BUILDER, "run_target_build", side_effect=target_build),
            mock.patch.object(BUILDER, "copy_tree", side_effect=shutil.copytree),
            mock.patch.object(BUILDER, "installed_packages", return_value=set(BUILDER.MOTIF_PACKAGES)),
            mock.patch.object(BUILDER, "build_archives"),
            mock.patch.object(BUILDER, "privileged"),
        ):
            BUILDER.build(args)
        self.assertEqual(len(sources), 2)
        self.assertEqual((args.staging / "nedit/usr/share/licenses/nedit/COPYRIGHT").read_bytes(), b"trusted license\n")
        self.assertEqual((args.staging / "motif/usr/share/licenses/motif-libs/COPYING").read_bytes(), b"trusted license\n")
        self.assertTrue(all(not source.parent.exists() for source in sources))


if __name__ == "__main__":
    unittest.main()
