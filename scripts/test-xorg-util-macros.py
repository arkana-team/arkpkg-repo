#!/usr/bin/env python3
"""Check that the X.Org macros builder executes only verified source bytes."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
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
                    builder.urllib.request, "urlopen", return_value=io.BytesIO(trusted)
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


if __name__ == "__main__":
    unittest.main()
