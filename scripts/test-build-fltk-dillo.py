#!/usr/bin/env python3
"""Check that privileged FLTK/Dillo builds only consume verified private sources."""

import hashlib
import importlib.util
import io
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "fltk_dillo_builder", Path(__file__).with_name("build-fltk-dillo.py")
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the FLTK/Dillo builder")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def archive_bytes(member_name: str, content: bytes, mode: str = "w:gz") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode=mode) as archive:
        member = tarfile.TarInfo(member_name)
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class SourceArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.sources = self.root / "sources"
        self.sources.mkdir(mode=0o700)
        self.name = BUILDER.FLTK_ARCHIVE
        self.source_root = BUILDER.FLTK_ROOT
        self.good = archive_bytes(f"{self.source_root}/COPYING", b"upstream license")

    def obtain(self, content: bytes | None = None) -> Path:
        return BUILDER.source_archive(
            self.cache,
            self.name,
            "https://example.invalid/source.tar.gz",
            hashlib.sha256(self.good if content is None else content).hexdigest(),
            self.source_root,
            sources=self.sources,
        )

    def test_cache_changes_cannot_reach_privileged_build_or_license(self) -> None:
        for name, root, mode in (
            (BUILDER.FLTK_ARCHIVE, BUILDER.FLTK_ROOT, "w:gz"),
            (BUILDER.DILLO_ARCHIVE, BUILDER.DILLO_ROOT, "w:bz2"),
        ):
            for change in ("replace", "overwrite"):
                with self.subTest(archive=name, change=change):
                    self.name, self.source_root = name, root
                    self.good = archive_bytes(
                        f"{root}/COPYING", b"upstream license", mode
                    )
                    cached = self.cache / name
                    cached.write_bytes(self.good)
                    private = self.obtain()
                    self.assertEqual(private.parent, self.sources)
                    if change == "replace":
                        cached.unlink()
                    cached.write_bytes(
                        archive_bytes(f"{root}/COPYING", b"substituted", mode)
                    )
                    installed = []

                    def privileged(
                        command: list[str],
                        *,
                        installed: list[Path] = installed,
                        root: str = root,
                        **kwargs: object,
                    ) -> None:
                        if command[0] == "install":
                            subprocess.run(command, check=True)
                            if "-d" not in command:
                                installed.append(Path(command[-1]))
                        elif command[0] == "chroot":
                            self.assertEqual(installed[0].read_bytes(), self.good)
                            result = subprocess.run(
                                ["tar", "-xOf", str(installed[0]), f"{root}/COPYING"],
                                capture_output=True,
                                check=True,
                            )
                            self.assertEqual(result.stdout, b"upstream license")
                        else:
                            self.fail(f"unexpected privileged command: {command}")

                    with mock.patch.object(
                        BUILDER, "privileged", side_effect=privileged
                    ):
                        BUILDER.run_target_build(
                            sysroot=self.root / "sysroot",
                            archive=private,
                            source_root=root,
                            commands=["true"],
                            log=self.root / "build.log",
                        )
                    self.assertEqual(len(installed), 1)
                    license_path = self.root / "license"
                    BUILDER.copy_license(private, f"{root}/COPYING", license_path)
                    self.assertEqual(license_path.read_bytes(), b"upstream license")

    def test_copy_is_verified_before_use(self) -> None:
        cached = self.cache / self.name
        cached.write_bytes(self.good)
        copyfile = BUILDER.shutil.copyfile

        def swap_then_copy(source: Path, destination: Path) -> None:
            source.write_bytes(b"replaced before snapshot")
            copyfile(source, destination)

        with (
            mock.patch.object(BUILDER.shutil, "copyfile", side_effect=swap_then_copy),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.obtain()

    def test_preserves_archive_structure_checks(self) -> None:
        for member, message in (
            ("/absolute", "unsafe source path"),
            (f"{self.source_root}/../escape", "unsafe source path"),
            ("wrong-root/COPYING", "unexpected source root"),
        ):
            with self.subTest(member=member):
                content = archive_bytes(member, b"license")
                (self.cache / self.name).write_bytes(content)
                with self.assertRaisesRegex(ValueError, message):
                    self.obtain(content)

    def test_rejects_untrusted_cache(self) -> None:
        for mode in (0o775, 0o757):
            with self.subTest(mode=oct(mode)):
                self.cache.chmod(mode)
                with self.assertRaises(PermissionError):
                    self.obtain()
        self.cache.chmod(0o755)
        with (
            mock.patch.object(BUILDER.os, "geteuid", return_value=os.geteuid() + 1),
            self.assertRaises(PermissionError),
        ):
            self.obtain()

    def test_download_is_private_and_populates_cache(self) -> None:
        with mock.patch.object(
            BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(self.good)
        ):
            private = self.obtain()
        self.assertEqual(private.read_bytes(), self.good)
        self.assertEqual((self.cache / self.name).read_bytes(), self.good)

    def test_download_does_not_overwrite_a_cache_symlink_created_during_download(
        self,
    ) -> None:
        victim = self.root / "victim"
        victim.write_bytes(b"keep this file")

        def response(*args: object, **kwargs: object) -> io.BytesIO:
            (self.cache / self.name).symlink_to(victim)
            return io.BytesIO(self.good)

        with mock.patch.object(BUILDER.urllib.request, "urlopen", side_effect=response):
            private = self.obtain()
        self.assertEqual(private.read_bytes(), self.good)
        self.assertEqual(victim.read_bytes(), b"keep this file")

    def test_invalid_download_is_not_cached(self) -> None:
        with (
            mock.patch.object(
                BUILDER.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")
            ),
            self.assertRaisesRegex(ValueError, "SHA-256 mismatch"),
        ):
            self.obtain()
        self.assertFalse((self.cache / self.name).exists())

    def test_private_directory_lasts_until_build_finishes_and_is_cleaned_on_failure(
        self,
    ) -> None:
        for fail in (False, True):
            with self.subTest(fail=fail):
                seen = []

                def build(
                    args: object,
                    sources: Path,
                    *,
                    seen: list[Path] = seen,
                    fail: bool = fail,
                ) -> None:
                    seen.append(sources)
                    self.assertEqual(stat.S_IMODE(sources.stat().st_mode), 0o700)
                    if fail:
                        raise ValueError("build failed")

                with mock.patch.object(
                    BUILDER, "build_from_sources", side_effect=build
                ):
                    if fail:
                        with self.assertRaisesRegex(ValueError, "build failed"):
                            BUILDER.build(mock.Mock())
                    else:
                        BUILDER.build(mock.Mock())
                self.assertEqual(len(seen), 1)
                self.assertFalse(seen[0].exists())


if __name__ == "__main__":
    unittest.main()
