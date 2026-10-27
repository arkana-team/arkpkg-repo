#!/usr/bin/env python3
"""Verify X.Org consumers use protected bytes after cache substitution."""

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
from unittest.mock import patch

import source_cache


def load(script):
    spec = importlib.util.spec_from_file_location(script, Path(__file__).with_name(script))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DATA = load("build-xorg-data.py")
ENCODINGS = load("build-xorg-font-data.py")
APPS = load("build-xorg-apps.py")
FONTS = load("build-xorg-fonts.py")


def archive_bytes(root, files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(f"{root}/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


class BuildStopped(Exception):
    pass


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir(mode=0o700)
        self.enterContext(patch.object(tempfile, "tempdir", str(self.root)))

    def obtain(self, builder, content):
        snapshots = Path(tempfile.mkdtemp(dir=self.root))
        digest = hashlib.sha256(content).hexdigest()
        if builder in (DATA, ENCODINGS):
            with patch.object(builder, "SHA256", digest):
                obtain = DATA.obtain_source if builder is DATA else ENCODINGS.source_archive
                return obtain(self.cache, snapshots)
        if builder is APPS:
            return APPS.source_archive("fixture", "1", digest, self.cache, snapshots)
        return FONTS.obtain_source(
            self.cache, "fixture", "1", "https://example.invalid/source", digest, snapshots
        )

    @staticmethod
    def source_root(builder):
        return f"{builder.NAME}-{builder.VERSION}" if builder in (DATA, ENCODINGS) else "fixture-1"

    def test_cache_links_cannot_change_verified_snapshots(self):
        for builder in (DATA, ENCODINGS, APPS, FONTS):
            for link in ("symlink", "hardlink"):
                with self.subTest(builder=builder.__name__, link=link):
                    root = self.source_root(builder)
                    content = archive_bytes(root, {"configure": b"verified"})
                    target = self.root / "mutable"
                    target.write_bytes(content)
                    candidate = self.cache / f"{root}.tar.xz"
                    if link == "symlink":
                        candidate.symlink_to(target)
                    else:
                        os.link(target, candidate)
                    snapshot = self.obtain(builder, content)
                    target.write_bytes(b"attacker replacement")
                    self.assertNotEqual(snapshot, candidate)
                    self.assertEqual(stat.S_IMODE(snapshot.parent.stat().st_mode), 0o700)
                    self.assertEqual(snapshot.read_bytes(), content)
                    candidate.unlink()

    def test_bad_sources_and_unsafe_caches_are_rejected(self):
        for builder in (DATA, ENCODINGS, APPS, FONTS):
            with self.subTest(builder=builder.__name__):
                content = archive_bytes(self.source_root(builder), {"configure": b"verified"})
                with (
                    patch.object(source_cache.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")),
                    self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
                ):
                    self.obtain(builder, content)
                self.assertEqual(list(self.cache.iterdir()), [])
                self.cache.chmod(0o777)
                with (
                    patch.object(source_cache.urllib.request, "urlopen") as download,
                    self.assertRaisesRegex(ValueError, "source cache"),
                ):
                    self.obtain(builder, content)
                download.assert_not_called()
                self.cache.chmod(0o700)

    def test_archive_path_checks_are_preserved(self):
        for builder in (ENCODINGS, APPS):
            for root in ("/absolute", "../escape", "wrong-root"):
                with self.subTest(builder=builder.__name__, root=root):
                    content = archive_bytes(root, {"configure": b"verified"})
                    with (
                        patch.object(source_cache.urllib.request, "urlopen", return_value=io.BytesIO(content)),
                        self.assertRaisesRegex(ValueError, "unsafe source path|unexpected source root"),
                    ):
                        self.obtain(builder, content)
                    (self.cache / f"{self.source_root(builder)}.tar.xz").unlink()

    def test_privileged_copy_uses_snapshot_and_cleans_it_on_failure(self):
        for builder in (ENCODINGS, APPS):
            with self.subTest(builder=builder.__name__):
                root = ENCODINGS.ROOT if builder is ENCODINGS else "xcalc-1.1.3"
                content = archive_bytes(root, {"configure": b"verified"})
                candidate = self.cache / f"{root}.tar.xz"
                candidate.write_bytes(content)
                sysroot = self.root / root
                binaries = sysroot / "usr/bin"
                binaries.mkdir(parents=True)
                for name in ("gcc", "make", "tar", "strip"):
                    (binaries / name).touch()
                args = argparse.Namespace(
                    sysroot=sysroot, source_cache=self.cache, arkpkg=self.root / "arkpkg",
                    arkpkg_build=self.root / "arkpkg-build", output_dir=self.root / "output",
                    staging=self.root / "staging", output_staging=self.root / "app-staging",
                    packages=["xcalc"], build_dependency_archive=[], build_pkg_config_file=[],
                    jobs=1,
                )
                copies = []

                def privileged(command, *, candidate=candidate, copies=copies, content=content, **kwargs):
                    if command[0] == "install" and "-d" not in command:
                        candidate.write_bytes(b"attacker replacement")
                        source = Path(command[-2])
                        copies.append(source)
                        self.assertEqual(source.read_bytes(), content)
                        self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o700)
                        raise BuildStopped

                digest = hashlib.sha256(content).hexdigest()
                pins = {"SHA256": digest} if builder is ENCODINGS else {
                    "APPS": {"xcalc": ("1.1.3", digest, "XCalc", "Calculator")}
                }
                with (
                    patch.multiple(builder, **pins),
                    patch.object(builder, "privileged", side_effect=privileged),
                    patch.object(builder, "installed_packages", return_value={"mkfontscale"}),
                    patch.object(APPS, "privileged_file_test", return_value=True),
                    self.assertRaises(BuildStopped),
                ):
                    builder.build(args)
                self.assertEqual(len(copies), 1)
                self.assertFalse(copies[0].exists())

    def bitmap_archive(self, name):
        return archive_bytes(f"{DATA.NAME}-{DATA.VERSION}", {
            "Makefile.am": f"dist_bitmap_DATA = \\\n {name}\n\n".encode(),
            "xbitmaps.pc.in": b"prefix=@prefix@\nVersion: @PACKAGE_VERSION@\n",
            "COPYING": b"license", name: b"verified bitmap",
        })

    def test_bitmap_staging_consumes_snapshot_after_cache_change(self):
        content = self.bitmap_archive("sample")
        candidate = self.cache / f"{DATA.NAME}-{DATA.VERSION}.tar.xz"
        candidate.write_bytes(content)
        stage = DATA.stage_package
        paths = []

        def stage_after_swap(source, staging):
            candidate.write_bytes(b"attacker replacement")
            paths.append(source)
            return stage(source, staging)

        args = argparse.Namespace(source_cache=self.cache, arkpkg_build=self.root / "builder",
                                  staging=self.root / "staging", output_dir=None)
        with (
            patch.object(DATA, "SHA256", hashlib.sha256(content).hexdigest()),
            patch.object(DATA, "stage_package", side_effect=stage_after_swap),
            patch.object(DATA.subprocess, "run"),
        ):
            DATA.build(args, args.staging)
        self.assertEqual((args.staging / "usr/include/X11/bitmaps/sample").read_bytes(), b"verified bitmap")
        self.assertFalse(paths[0].exists())

    def test_bitmap_manifest_cannot_select_paths(self):
        victim = self.root / "victim"
        victim.write_bytes(b"keep")
        for name in (str(victim), "../../../../victim", "sub/file", ".", "..", "sub\\file"):
            with self.subTest(name=name):
                source = self.root / "bitmap.tar.xz"
                source.write_bytes(self.bitmap_archive(name))
                with self.assertRaisesRegex(ValueError, "unsafe upstream bitmap name"):
                    DATA.stage_package(source, self.root / "staging")
                self.assertEqual(victim.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
