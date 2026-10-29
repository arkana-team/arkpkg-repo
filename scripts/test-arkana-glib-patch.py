#!/usr/bin/env python3
"""Keep the GLib compatibility patch inside the copied package payload."""

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "arkana_package_builder", Path(__file__).with_name("build-arkana-packages.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the arkanaOS package builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

UTILS = Path("usr/share/glib-2.0/codegen/utils.py")
SOURCE = (
    "import distutils.version\n\n"
    "def version_cmp_key(key):\n"
    "    v = key[0]\n"
    "    return (distutils.version.LooseVersion(v), key[1])\n"
)


class GlibPatchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.staging = self.root / "staging"
        self.payload = self.root / "package"
        self.payload.mkdir()
        for relative in BUILDER.STAGED_PACKAGE_FILES["glib-tools"]:
            if relative.startswith("usr/bin/"):
                path = self.staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
        self.source = self.staging / UTILS
        self.source.parent.mkdir(parents=True)
        self.source.write_text(SOURCE, encoding="utf-8")
        self.source.chmod(0o640)

    def copy_payload(self) -> Path:
        BUILDER.add_staged_payload("glib-tools", self.payload, self.staging)
        return self.payload / UTILS

    def test_regular_copy_is_patched_and_staging_is_unchanged(self) -> None:
        target = self.copy_payload()
        BUILDER.patch_glib_tools_python313(self.payload)
        patched = target.read_text(encoding="utf-8")
        self.assertNotIn("distutils", patched)
        spec = importlib.util.spec_from_file_location("patched_glib_utils", target)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        versions = [("2.10", "b"), ("2.9", "c"), ("2.10", "a")]
        self.assertEqual(
            sorted(versions, key=module.version_cmp_key),
            [("2.9", "c"), ("2.10", "a"), ("2.10", "b")],
        )
        self.assertEqual(self.source.read_text(encoding="utf-8"), SOURCE)
        for path in (self.source, target):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_preserved_staging_symlinks_cannot_modify_host_files(self) -> None:
        for component in ("glib-2.0", "codegen", "utils.py"):
            for kind in ("absolute", "relative"):
                with self.subTest(component=component, kind=kind):
                    case = self.root / component / kind
                    staging = case / "staging"
                    payload = case / "package"
                    payload.mkdir(parents=True)
                    bin_dir = staging / "usr/bin"
                    bin_dir.mkdir(parents=True)
                    for executable in (self.staging / "usr/bin").iterdir():
                        (bin_dir / executable.name).write_bytes(executable.read_bytes())
                    index = UTILS.parts.index(component)
                    relative = Path(*UTILS.parts[:index + 1])
                    victim = case / "host" / Path(*UTILS.parts[index + 1:])
                    victim.parent.mkdir(parents=True, exist_ok=True)
                    victim.write_text(SOURCE, encoding="utf-8")
                    victim.chmod(0o640)
                    link = staging / relative
                    link.parent.mkdir(parents=True, exist_ok=True)
                    destination = case / "host"
                    target = (str(destination) if kind == "absolute" else
                              os.path.relpath(destination, (payload / relative).parent))
                    link.symlink_to(target)
                    BUILDER.add_staged_payload("glib-tools", payload, staging)
                    self.assertTrue((payload / relative).is_symlink())
                    before = victim.stat()
                    with self.assertRaises(OSError):
                        BUILDER.patch_glib_tools_python313(payload)
                    self.assertEqual(victim.read_text(encoding="utf-8"), SOURCE)
                    self.assertEqual(victim.stat().st_mtime_ns, before.st_mtime_ns)
                    self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o640)
                    self.assertEqual((payload / relative).readlink(), Path(target))

    def test_earlier_payload_ancestor_links_are_rejected(self) -> None:
        self.copy_payload()
        for relative in (Path("usr/share"), Path("usr"), Path(".")):
            with self.subTest(relative=relative):
                path = self.payload / relative
                outside = self.root / "outside"
                path.rename(outside)
                path.symlink_to(outside, target_is_directory=True)
                try:
                    with self.assertRaises(OSError):
                        BUILDER.patch_glib_tools_python313(self.payload)
                    self.assertEqual((self.payload / UTILS).read_text(encoding="utf-8"), SOURCE)
                finally:
                    path.unlink()
                    outside.rename(path)

    def test_contained_and_dangling_final_links_are_rejected(self) -> None:
        target = self.copy_payload()
        saved = target.with_name("saved.py")
        target.rename(saved)
        for destination in (saved.name, "missing.py"):
            with self.subTest(destination=destination):
                target.symlink_to(destination)
                with self.assertRaises(OSError):
                    BUILDER.patch_glib_tools_python313(self.payload)
                self.assertEqual(saved.read_text(encoding="utf-8"), SOURCE)
                self.assertFalse(target.with_name("missing.py").exists())
                target.unlink()

    def test_nonregular_targets_are_rejected(self) -> None:
        target = self.copy_payload()
        target.unlink()
        for kind in ("directory", "fifo"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    target.mkdir()
                else:
                    os.mkfifo(target)
                with self.assertRaises((OSError, ValueError)):
                    BUILDER.patch_glib_tools_python313(self.payload)
                if kind == "directory":
                    target.rmdir()
                else:
                    target.unlink()

    def test_unexpected_source_is_unchanged(self) -> None:
        target = self.copy_payload()
        target.write_text("unexpected code\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected GLib"):
            BUILDER.patch_glib_tools_python313(self.payload)
        self.assertEqual(target.read_text(encoding="utf-8"), "unexpected code\n")

    def test_replacing_path_after_open_cannot_redirect_the_write(self) -> None:
        target = self.copy_payload()
        victim = self.root / "victim.py"
        victim.write_text(SOURCE, encoding="utf-8")
        saved = target.with_name("opened.py")
        fdopen = os.fdopen

        def replace_after_open(descriptor, *args, **kwargs):
            target.rename(saved)
            target.symlink_to(victim)
            return fdopen(descriptor, *args, **kwargs)

        with mock.patch.object(BUILDER.os, "fdopen", side_effect=replace_after_open):
            BUILDER.patch_glib_tools_python313(self.payload)
        self.assertEqual(victim.read_text(encoding="utf-8"), SOURCE)
        self.assertNotIn("distutils", saved.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
