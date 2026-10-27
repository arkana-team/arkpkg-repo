#!/usr/bin/env python3
"""Check executable permissions without changing staged symlink targets."""

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "arkana_package_builder", Path(__file__).with_name("build-arkana-packages.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the arkanaOS package builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

PACKAGES = ("arkcfg", "fastfetch", "vim")


class StagedExecutableTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def fixture(self, name: str, case: str) -> tuple[Path, Path]:
        root = self.root / name / case
        staging = root / "staging"
        payload = root / "package"
        (staging / "usr/bin").mkdir(parents=True)
        payload.mkdir()
        if name in {"fastfetch", "vim"}:
            (staging / "usr/share" / name).mkdir(parents=True)
        return staging, payload

    def test_regular_executables_and_vim_aliases(self) -> None:
        for name in PACKAGES:
            with self.subTest(name=name):
                staging, payload = self.fixture(name, "regular")
                source = staging / "usr/bin" / name
                source.write_bytes(b"#!/bin/sh\nexit 0\n")
                source.chmod(0o640)
                if name == "vim":
                    (staging / "usr/bin/vimdiff").symlink_to("vim")
                    os.link(source, staging / "usr/bin/view")

                BUILDER.add_staged_payload(name, payload, staging)

                executable = payload / "usr/bin" / name
                self.assertFalse(executable.is_symlink())
                self.assertEqual(executable.read_bytes(), source.read_bytes())
                self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o640)
                if name == "vim":
                    for alias in ("vi", "vimdiff", "view"):
                        self.assertEqual((payload / "usr/bin" / alias).readlink(), Path("vim"))

    def test_external_symlink_targets_are_unchanged(self) -> None:
        for name in PACKAGES:
            for kind in ("absolute", "relative"):
                with self.subTest(name=name, kind=kind):
                    staging, payload = self.fixture(name, kind)
                    victim = payload.parent / "victim"
                    victim.write_bytes(b"unrelated host file\n")
                    victim.chmod(0o600)
                    target = (
                        str(victim) if kind == "absolute"
                        else os.path.relpath(victim, payload / "usr/bin")
                    )
                    (staging / "usr/bin" / name).symlink_to(target)

                    BUILDER.add_staged_payload(name, payload, staging)

                    self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o600)
                    self.assertEqual(victim.read_bytes(), b"unrelated host file\n")
                    self.assertEqual((payload / "usr/bin" / name).readlink(), Path(target))

    def test_dangling_symlinks_are_preserved(self) -> None:
        for name in PACKAGES:
            with self.subTest(name=name):
                staging, payload = self.fixture(name, "dangling")
                missing = payload.parent / "missing"
                (staging / "usr/bin" / name).symlink_to(missing)

                BUILDER.add_staged_payload(name, payload, staging)

                self.assertEqual((payload / "usr/bin" / name).readlink(), missing)
                self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
