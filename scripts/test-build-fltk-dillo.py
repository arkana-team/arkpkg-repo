#!/usr/bin/env python3
"""Check source integrity and bounded dependency reads in the FLTK/Dillo builder."""

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

import lz4.frame
from package_metadata import MAX_METADATA_SIZE

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


class PackageMetadataTests(unittest.TestCase):
    METADATA = b"Name: fltk-libs\n"

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive = Path(temporary.name) / "fltk-libs-1.4.5-x86_64.ark"
        self.arkpkg = Path("/unused/arkpkg")
        self.sysroot = Path("/unused/sysroot")
        self.install = mock.patch.object(BUILDER, "privileged").start()
        self.addCleanup(mock.patch.stopall)

    @staticmethod
    def member(name, content=b"", kind=tarfile.REGTYPE):
        info = tarfile.TarInfo(name)
        info.type = kind
        info.size = len(content)
        return (
            info.tobuf(format=tarfile.USTAR_FORMAT)
            + content + b"\0" * (-len(content) % tarfile.BLOCKSIZE)
        )

    @staticmethod
    def pax_record(key, value):
        record = f" {key}={value}\n".encode()
        length = len(record) + 1
        while len(str(length)) + len(record) != length:
            length = len(str(length)) + len(record)
        return str(length).encode() + record

    def write_archive(self, content=METADATA, before=b"", sparse_size=None):
        if sparse_size is not None:
            before += self.member(
                "sparse", self.pax_record("GNU.sparse.map", f"0,{len(content)}")
                + self.pax_record("GNU.sparse.size", sparse_size), tarfile.XHDTYPE,
            )
        data = before + self.member("ARKPKG", content) + b"\0" * 1024
        self.archive.write_bytes(lz4.frame.compress(data))
        return len(data)

    def read_and_install(self):
        return BUILDER.install_package(self.arkpkg, self.sysroot, self.archive)

    def test_name_parsing_and_metadata_at_limit(self):
        for name in ("fltk-libs", "fltk-devel"):
            with self.subTest(name=name):
                metadata = f"Description: café\n Name : {name} \n".encode()
                self.write_archive(metadata.ljust(MAX_METADATA_SIZE, b"\n"))
                self.assertEqual(self.read_and_install(), name)
                self.install.assert_called_with([
                    str(self.arkpkg), "--root", str(self.sysroot), "install",
                    str(self.archive), "-y",
                ])
        self.assertEqual(self.install.call_count, 2)

    def test_oversized_and_sparse_metadata_rejected_before_extraction(self):
        for sparse_size in (None, MAX_METADATA_SIZE, 1 << 40):
            with self.subTest(sparse_size=sparse_size):
                self.write_archive(
                    self.METADATA.ljust(MAX_METADATA_SIZE + 1, b"\n")
                    if sparse_size is None else self.METADATA,
                    sparse_size=sparse_size,
                )
                self.assertLess(self.archive.stat().st_size, 1024)
                with (
                    mock.patch.object(tarfile.TarFile, "extractfile") as extract,
                    self.assertRaisesRegex(ValueError, "ARKPKG metadata"),
                ):
                    self.read_and_install()
                extract.assert_not_called()
        self.install.assert_not_called()

    def test_bounded_read_checked_before_decoding(self):
        self.write_archive()
        with (
            mock.patch.object(tarfile.ExFileObject, "read") as read,
            self.assertRaisesRegex(ValueError, "ARKPKG metadata exceeds"),
        ):
            read.return_value = b"\xff" * (MAX_METADATA_SIZE + 1)
            self.read_and_install()
        read.assert_called_once_with(MAX_METADATA_SIZE + 1)
        self.install.assert_not_called()

    def test_missing_name_and_invalid_utf8_do_not_install(self):
        for metadata, error in (
            (b"Description: missing name\n", ValueError),
            (b"Name: fltk-libs\nDescription: \xff\n", UnicodeDecodeError),
        ):
            with self.subTest(metadata=metadata):
                self.write_archive(metadata)
                with self.assertRaises(error):
                    self.read_and_install()
        self.install.assert_not_called()

    def test_oversized_extensions_rejected_before_body_parsing(self):
        for kind in (
            tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
            tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
        ):
            with self.subTest(kind=kind):
                self.write_archive(before=self.member(
                    "extension", b"\0" * (BUILDER.MAX_TAR_EXTENSION_SIZE + 1), kind,
                ))
                with (
                    mock.patch.object(tarfile.TarInfo, "_proc_pax") as pax,
                    mock.patch.object(tarfile.TarInfo, "_proc_gnulong") as gnu,
                    self.assertRaisesRegex(ValueError, "TAR extension header"),
                ):
                    self.read_and_install()
                pax.assert_not_called()
                gnu.assert_not_called()
        self.install.assert_not_called()

    def test_extension_boundary_and_budget_reset(self):
        limit = BUILDER.MAX_TAR_EXTENSION_SIZE
        record = self.pax_record("comment", "x" * (limit - 15))
        self.assertEqual(len(record), limit)
        self.write_archive(before=self.member("extension", record, tarfile.XHDTYPE))
        for _ in range(2):
            self.assertEqual(self.read_and_install(), "fltk-libs")

    def test_cumulative_extensions_and_nested_extensions_are_bounded(self):
        extension = self.member("extension", self.pax_record("comment", "ok"), tarfile.XHDTYPE)
        self.write_archive(before=(extension + self.member("package/empty")) * 3)
        with (
            mock.patch.object(BUILDER, "MAX_TAR_EXTENSION_BYTES", 2 * 512),
            self.assertRaisesRegex(ValueError, "TAR extension data"),
        ):
            self.read_and_install()
        self.write_archive(before=extension * (BUILDER.MAX_TAR_EXTENSION_DEPTH + 1))
        with self.assertRaisesRegex(ValueError, "TAR extension nesting"):
            self.read_and_install()
        self.install.assert_not_called()
        self.write_archive(before=extension * BUILDER.MAX_TAR_EXTENSION_DEPTH)
        self.assertEqual(self.read_and_install(), "fltk-libs")

    def test_sparse_map_parsing_is_bounded_before_metadata(self):
        pax = self.pax_record("GNU.sparse.major", "1") + self.pax_record("GNU.sparse.minor", "0")
        sparse = self.member("extension", pax, tarfile.XHDTYPE) + self.member(
            "package/sparse", b"1000000\n" + b"0\n1\n" * 4096,
        )
        self.write_archive(before=sparse)
        with (
            mock.patch.object(BUILDER, "MAX_TAR_EXTENSION_BYTES", 4096),
            self.assertRaisesRegex(ValueError, "TAR parser data"),
        ):
            self.read_and_install()
        self.install.assert_not_called()

    def test_member_count_is_bounded_before_processing(self):
        extension = self.member("extension", self.pax_record("comment", "ok"), tarfile.XHDTYPE)
        for before, count in (
            (self.member("package/empty") * 2, 3),
            (extension * 2, 3),
            ((extension + self.member("package/empty")) * 2, 5),
        ):
            self.write_archive(before=before)
            with mock.patch.object(BUILDER, "MAX_TAR_MEMBERS", count):
                for _ in range(2):
                    self.assertEqual(self.read_and_install(), "fltk-libs")
            self.install.reset_mock()
            original = tarfile.TarInfo._proc_member
            with (
                mock.patch.object(BUILDER, "MAX_TAR_MEMBERS", count - 1),
                mock.patch.object(tarfile.TarInfo, "_proc_member", autospec=True,
                                  side_effect=original) as process,
                self.assertRaisesRegex(ValueError, "TAR member count"),
            ):
                self.read_and_install()
            self.assertEqual(process.call_count, count - 1)
            self.install.assert_not_called()

    def test_inherited_pax_work_is_bounded_before_processing(self):
        global_header = self.member(
            "global", self.pax_record("one", "x") + self.pax_record("two", "y"),
            tarfile.XGLTYPE,
        )
        local_header = self.member("local", self.pax_record("comment", "ok"), tarfile.XHDTYPE)
        gnu_header = self.member("link", b"target\0", tarfile.GNUTYPE_LONGLINK)
        more_globals = self.member("global", self.pax_record("three", "z"), tarfile.XGLTYPE)
        for nested, items in ((b"", 6), (local_header, 8), (gnu_header, 8), (more_globals, 11)):
            self.write_archive(before=(
                global_header + nested + self.member("package/empty") * 2
            ))
            with mock.patch.object(BUILDER, "MAX_TAR_PAX_ITEMS", items):
                for _ in range(2):
                    self.assertEqual(self.read_and_install(), "fltk-libs")
            self.install.reset_mock()
            original = tarfile.TarInfo._proc_member
            with (
                mock.patch.object(BUILDER, "MAX_TAR_PAX_ITEMS", items - 1),
                mock.patch.object(tarfile.TarInfo, "_proc_member", autospec=True,
                                  side_effect=original) as process,
                self.assertRaisesRegex(ValueError, "inherited PAX metadata"),
            ):
                self.read_and_install()
            self.assertNotIn("ARKPKG", [call.args[0].name for call in process.call_args_list])
            self.install.assert_not_called()

    def test_members_with_global_pax_metadata_are_not_retained(self):
        pax = b"".join(self.pax_record(f"key{i}", "x") for i in range(1000))
        self.write_archive(before=(
            self.member("global", pax, tarfile.XGLTYPE)
            + self.member("package/empty") * 256
        ))
        cached_counts = []
        original = tarfile.TarInfo._proc_builtin

        def observe(member, archive):
            cached_counts.append(len(archive.members))
            return original(member, archive)

        with mock.patch.object(tarfile.TarInfo, "_proc_builtin", observe):
            self.assertEqual(self.read_and_install(), "fltk-libs")
        self.assertEqual(len(cached_counts), 257)
        self.assertEqual(max(cached_counts), 0)

    def test_expanded_budget_includes_payload_skipped_before_metadata(self):
        expanded = self.write_archive(before=self.member("package/large", b"\0" * (32 * 1024)))
        self.assertLess(self.archive.stat().st_size, 1024)
        with (
            mock.patch.object(BUILDER, "MAX_ARCHIVE_BYTES", 16 * 1024),
            self.assertRaisesRegex(ValueError, "expanded archive"),
        ):
            self.read_and_install()
        self.install.assert_not_called()
        with mock.patch.object(BUILDER, "MAX_ARCHIVE_BYTES", expanded):
            self.assertEqual(self.read_and_install(), "fltk-libs")

    def test_budget_reader_caps_requests_before_reading(self):
        source = io.BytesIO(b"x" * 1024)
        with mock.patch.object(source, "read", wraps=source.read) as read:
            reader = BUILDER.BudgetReader(source, 512, "test")
            with self.assertRaisesRegex(ValueError, "inspection limit"):
                reader.read(1 << 40)
            read.assert_called_once_with(513)


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
