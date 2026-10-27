"""Check that the Pages index stays small and points to verified package files."""

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lz4.frame

spec = importlib.util.spec_from_file_location(
    "build_index", Path(__file__).resolve().with_name("build-index.py")
)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load build-index.py")
build_index = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build_index
spec.loader.exec_module(build_index)
build_site = build_index.build_site
validate_download_base_url = build_index.validate_download_base_url
validate_package_metadata = build_index.validate_package_metadata
package_category = build_index.package_category


class MetadataReadTests(unittest.TestCase):
    METADATA = (
        b"Name: hello\nDescription: sample package\nVersion: 1.0\nArch: any\n"
    )

    def write_archive(
        self, path: Path, member: tarfile.TarInfo, content: bytes
    ) -> None:
        with (
            lz4.frame.open(path, "wb") as compressed,
            tarfile.open(fileobj=compressed, mode="w|") as archive,
        ):
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
            package = tarfile.TarInfo("package/")
            package.type = tarfile.DIRTYPE
            archive.addfile(package)

    def test_metadata_at_size_limit_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            content = self.METADATA.ljust(build_index.MAX_METADATA_SIZE, b"\n")
            self.write_archive(path, tarfile.TarInfo("./ARKPKG"), content)
            self.assertEqual(build_index.read_metadata(path)["Name"], "hello")

    def test_oversized_metadata_is_rejected_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            content = self.METADATA.ljust(build_index.MAX_METADATA_SIZE + 1, b"\n")
            self.write_archive(path, tarfile.TarInfo("./ARKPKG"), content)
            with patch.object(tarfile.TarFile, "extractfile") as extract:
                with self.assertRaisesRegex(ValueError, "ARKPKG metadata exceeds"):
                    build_index.read_metadata(path)
                extract.assert_not_called()

    def test_sparse_metadata_is_rejected_before_extraction(self) -> None:
        for logical_size in (len(self.METADATA) + 1, 1 << 40):
            with (
                self.subTest(logical_size=logical_size),
                tempfile.TemporaryDirectory() as temporary,
            ):
                path = Path(temporary) / "hello.ark"
                member = tarfile.TarInfo("./ARKPKG")
                member.pax_headers = {
                    "GNU.sparse.map": f"0,{len(self.METADATA)}",
                    "GNU.sparse.size": str(logical_size),
                }
                self.write_archive(path, member, self.METADATA)
                with (
                    lz4.frame.open(path, "rb") as compressed,
                    tarfile.open(fileobj=compressed, mode="r|") as archive,
                ):
                    parsed = next(iter(archive))
                    self.assertTrue(parsed.isfile())
                    self.assertTrue(parsed.issparse())
                    self.assertEqual(parsed.size, logical_size)
                with patch.object(tarfile.TarFile, "extractfile") as extract:
                    with self.assertRaisesRegex(ValueError, "must not be sparse"):
                        build_index.read_metadata(path)
                    extract.assert_not_called()

    def test_metadata_read_is_bounded_and_checked_before_decoding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            self.write_archive(path, tarfile.TarInfo("ARKPKG"), self.METADATA)
            with patch.object(tarfile.ExFileObject, "read") as read:
                read.return_value = b"\xff" * (build_index.MAX_METADATA_SIZE + 1)
                with self.assertRaisesRegex(ValueError, "ARKPKG metadata exceeds"):
                    build_index.read_metadata(path)
                read.assert_called_once_with(build_index.MAX_METADATA_SIZE + 1)


class ArchiveBudgetTests(unittest.TestCase):
    EXTENSION_TYPES = (
        tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
        tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
    )

    @staticmethod
    def member(name, data=b"", kind=tarfile.REGTYPE):
        info = tarfile.TarInfo(name)
        info.type = kind
        info.size = len(data)
        return (
            info.tobuf(format=tarfile.USTAR_FORMAT)
            + data + b"\0" * (-len(data) % tarfile.BLOCKSIZE)
        )

    @staticmethod
    def pax_record(key, value):
        record = f" {key}={value}\n".encode()
        length = len(record) + 1
        while len(str(length)) + len(record) != length:
            length = len(str(length)) + len(record)
        return str(length).encode() + record

    def write_archive(self, path, before=b"", after=b"", payload=b"x"):
        data = (
            before + self.member("ARKPKG", MetadataReadTests.METADATA)
            + after + self.member("package/test", payload) + b"\0" * 1024
        )
        path.write_bytes(lz4.frame.compress(data))
        return len(data)

    def test_oversized_extensions_are_rejected_before_body_parsing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            for kind in self.EXTENSION_TYPES:
                extension = self.member(
                    "extension", b"\0" * (build_index.MAX_TAR_EXTENSION_SIZE + 1), kind
                )
                for position in ("before", "after"):
                    self.write_archive(path, **{position: extension})
                    with (
                        self.subTest(kind=kind, position=position),
                        patch.object(tarfile.TarInfo, "_proc_pax") as pax,
                        patch.object(tarfile.TarInfo, "_proc_gnulong") as gnu,
                        self.assertRaisesRegex(ValueError, "TAR extension header"),
                    ):
                        try:
                            build_index.read_metadata(path)
                        finally:
                            pax.assert_not_called()
                            gnu.assert_not_called()

    def test_extension_size_boundary_is_accepted(self):
        limit = build_index.MAX_TAR_EXTENSION_SIZE
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            for kind in self.EXTENSION_TYPES:
                if kind in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
                    data = b"ARKPKG\0".ljust(limit, b"\0")
                else:
                    # One valid PAX record whose total length equals the limit.
                    data = self.pax_record("comment", "x" * (limit - 15))
                    self.assertEqual(len(data), limit)
                self.write_archive(path, before=self.member("extension", data, kind))
                with self.subTest(kind=kind):
                    self.assertEqual(build_index.read_metadata(path)["Name"], "hello")

    def test_cumulative_extensions_are_bounded_across_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            extension = self.member(
                "extension", self.pax_record("comment", "test"), tarfile.XGLTYPE
            ) + self.member("package/", kind=tarfile.DIRTYPE)
            self.write_archive(path, before=extension * 3)
            with (
                patch.object(build_index, "MAX_TAR_EXTENSION_BYTES", 2 * 512),
                self.assertRaisesRegex(ValueError, "TAR extension data"),
            ):
                build_index.read_metadata(path)

    def test_member_count_is_bounded_before_processing(self):
        entries = self.member("package/empty", kind=tarfile.DIRTYPE) * 2
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            for position in ("before", "after"):
                self.write_archive(path, **{position: entries})
                with (
                    self.subTest(position=position, boundary="exact"),
                    patch.object(build_index, "MAX_TAR_MEMBERS", 4),
                ):
                    self.assertEqual(build_index.read_metadata(path)["Name"], "hello")
                original = tarfile.TarInfo._proc_member
                with (
                    self.subTest(position=position, boundary="over"),
                    patch.object(build_index, "MAX_TAR_MEMBERS", 3),
                    patch.object(tarfile.TarInfo, "_proc_member", autospec=True,
                                 side_effect=original) as process,
                    self.assertRaisesRegex(ValueError, "TAR member count"),
                ):
                    try:
                        build_index.read_metadata(path)
                    finally:
                        self.assertEqual(process.call_count, 3)

    def test_inherited_pax_work_is_bounded_before_processing(self):
        global_header = self.member(
            "global", self.pax_record("one", "x") + self.pax_record("two", "y"),
            tarfile.XGLTYPE,
        )
        entries = self.member("package/empty", kind=tarfile.DIRTYPE) * 2
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            for position, inherited_members in (("before", 4), ("after", 3)):
                self.write_archive(path, **{position: global_header + entries})
                with (
                    self.subTest(position=position, boundary="exact"),
                    patch.object(build_index, "MAX_TAR_PAX_ITEMS", 2 * inherited_members),
                ):
                    self.assertEqual(build_index.read_metadata(path)["Name"], "hello")
                original = tarfile.TarInfo._proc_member
                with (
                    self.subTest(position=position, boundary="over"),
                    patch.object(build_index, "MAX_TAR_PAX_ITEMS",
                                 2 * inherited_members - 1),
                    patch.object(tarfile.TarInfo, "_proc_member", autospec=True,
                                 side_effect=original) as process,
                    self.assertRaisesRegex(ValueError, "inherited PAX metadata"),
                ):
                    try:
                        build_index.read_metadata(path)
                    finally:
                        # The final payload must be rejected before tarfile can
                        # copy the inherited dictionary into it.
                        self.assertNotIn("package/test", [
                            call.args[0].name for call in process.call_args_list
                        ])

    def test_members_with_global_pax_metadata_are_not_retained(self):
        pax = b"".join(self.pax_record(f"key{i}", "x") for i in range(1000))
        before = self.member("global", pax, tarfile.XGLTYPE)
        after = self.member("package/empty", kind=tarfile.DIRTYPE) * 256
        cached_counts = []
        original = tarfile.TarInfo._proc_builtin

        def observe(member, archive):
            cached_counts.append(len(archive.members))
            return original(member, archive)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            self.write_archive(path, before=before, after=after)
            with patch.object(tarfile.TarInfo, "_proc_builtin", observe):
                self.assertEqual(build_index.read_metadata(path)["Name"], "hello")
        self.assertEqual(len(cached_counts), 258)
        self.assertEqual(max(cached_counts), 0)

    def test_pax_paths_are_still_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            for kind in (tarfile.XGLTYPE, tarfile.XHDTYPE):
                before = self.member(
                    "extension", self.pax_record("path", "../ARKPKG"), kind
                )
                self.write_archive(path, before=before)
                with (
                    self.subTest(kind=kind),
                    self.assertRaisesRegex(ValueError, "unsafe path in archive"),
                ):
                    build_index.read_metadata(path)

    def test_sparse_map_parsing_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            pax = (
                self.pax_record("GNU.sparse.major", "1")
                + self.pax_record("GNU.sparse.minor", "0")
            )
            sparse = self.member("extension", pax, tarfile.XHDTYPE) + self.member(
                "package/sparse", b"1000000\n" + b"0\n1\n" * 4096
            )
            self.write_archive(path, after=sparse)
            with (
                patch.object(build_index, "MAX_TAR_EXTENSION_BYTES", 4096),
                self.assertRaisesRegex(ValueError, "TAR parser data"),
            ):
                build_index.read_metadata(path)

    def test_expanded_budget_includes_payload_skipped_after_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hello.ark"
            expanded = self.write_archive(path, payload=b"\0" * (32 * 1024))
            self.assertLess(path.stat().st_size, 1024)
            with (
                patch.object(build_index, "MAX_ARCHIVE_BYTES", expanded),
                self.subTest(boundary="exact"),
            ):
                self.assertEqual(build_index.read_metadata(path)["Name"], "hello")
            with (
                patch.object(build_index, "MAX_ARCHIVE_BYTES", 16 * 1024),
                self.assertRaisesRegex(ValueError, "expanded archive"),
            ):
                build_index.read_metadata(path)

    def test_budget_reader_caps_requests_before_reading(self):
        source = io.BytesIO(b"x" * 1024)
        with patch.object(source, "read", wraps=source.read) as read:
            reader = build_index.BudgetReader(source, 512, "test")
            with self.assertRaisesRegex(ValueError, "inspection limit"):
                reader.read(1 << 40)
            read.assert_called_once_with(513)


class BuildIndexTests(unittest.TestCase):
    def test_site_publishes_epoch_and_tilde_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "packages" / "hello-1:1.2.3~rc1-x86_64.ark"
            archive_path.parent.mkdir()
            with (
                archive_path.open("wb") as output,
                lz4.frame.open(output, "wb") as compressed,
                tarfile.open(fileobj=compressed, mode="w|") as archive,
            ):
                metadata = (
                    b"Name: hello\n"
                    b"Description: sample package\n"
                    b"Version: 1:1.2.3~rc1\n"
                    b"Arch: x86_64\n"
                )
                metadata_info = tarfile.TarInfo("ARKPKG")
                metadata_info.size = len(metadata)
                archive.addfile(metadata_info, io.BytesIO(metadata))
                package_info = tarfile.TarInfo("package/")
                package_info.type = tarfile.DIRTYPE
                archive.addfile(package_info)

            metadata_dir = root / "data"
            metadata_dir.mkdir()
            (metadata_dir / "package-metadata.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "packages": {
                            "hello": {
                                "author": None,
                                "git_repo": "https://github.com/example/hello",
                                "git_repo_type": "upstream",
                                "git_author_url": None,
                                "packagers": [
                                    {
                                        "name": "arkana-team",
                                        "url": "https://github.com/arkana-team",
                                    },
                                    {
                                        "name": "debarkak",
                                        "url": "https://github.com/debarkak",
                                    },
                                ],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            site = root / "site"
            build_site(root, site)

            index = json.loads((site / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["schema_version"], 1)
            self.assertEqual(len(index["packages"]), 1)
            entry = index["packages"][0]
            self.assertIsNone(entry["author"])
            self.assertEqual(entry["git_repo"], "https://github.com/example/hello")
            self.assertEqual(entry["git_repo_type"], "upstream")
            self.assertIsNone(entry["git_author_url"])
            self.assertEqual(
                entry["packagers"],
                [
                    {
                        "name": "arkana-team",
                        "url": "https://github.com/arkana-team",
                    },
                    {
                        "name": "debarkak",
                        "url": "https://github.com/debarkak",
                    },
                ],
            )
            self.assertEqual(
                entry["download_url"],
                "https://raw.githubusercontent.com/arkana-team/arkpkg-repo/main/"
                "packages/hello-1%3A1.2.3~rc1-x86_64.ark",
            )
            self.assertEqual(entry["version"], "1:1.2.3~rc1")
            self.assertEqual(
                entry["sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest()
            )
            self.assertFalse((site / "packages").exists())
            self.assertTrue((site / "index.html").is_file())
            self.assertTrue((site / "catalogue.css").is_file())
            self.assertTrue((site / "catalogue.js").is_file())
            page = (site / "index.html").read_text(encoding="utf-8")
            style = (site / "catalogue.css").read_text(encoding="utf-8")
            script = (site / "catalogue.js").read_text(encoding="utf-8")
            self.assertIn('data-sort-key="author"', page)
            self.assertIn('data-sort-key="git_repo"', page)
            self.assertIn('href="https://github.com/example/hello"', page)
            self.assertIn('data-packagers="[{&quot;name&quot;:&quot;arkana-team&quot;', page)
            self.assertIn('data-git-repo-type="upstream"', page)
            self.assertIn('data-category-filter="all"', page)
            self.assertIn('id="details-packagers"', page)
            style_version = hashlib.sha256(style.encode("utf-8")).hexdigest()[:12]
            self.assertIn(f'href="catalogue.css?v={style_version}"', page)
            self.assertIn('src="catalogue.js"', page)
            self.assertIn('id="mobile-category"', page)
            self.assertIn("software catalogue for arkanaOS", page)
            self.assertNotIn("solaris", page.lower())
            self.assertNotIn("oracle", page.lower())
            self.assertIn(">not listed</td>", page)
            self.assertIn('<small class="repo-type">upstream</small>', page)
            self.assertIn(".catalogue-layout", style)
            self.assertIn("@media (max-width: 760px)", style)
            self.assertIn("--accent: #3159b8", style)
            self.assertIn(
                "background: linear-gradient(180deg, #f2f3f4 0%, #c7cbce 100%)",
                style,
            )
            self.assertIn(
                ".category-panel, .catalogue-panel, .details-panel { "
                "border: 3px solid; border-color: #fff #4f585f #4f585f #fff",
                style,
            )
            self.assertIn("4px 4px 0 #aeb4b9, 5px 6px 7px #343a4055", style)
            self.assertIn(
                ".intro { display: flex; flex-wrap: wrap; align-items: center; "
                "justify-content: space-between; gap: .7rem 1.5rem; padding: .7rem; "
                "border: 2px solid; border-color: #fff #626a70 #626a70 #fff",
                style,
            )
            self.assertIn(
                ".category-list button { display: flex; width: 100%; "
                "justify-content: space-between; gap: .5rem; padding: .28rem .4rem; "
                "border: 3px solid; border-color: #fff #555e66 #555e66 #fff; "
                "border-radius: 2px; background: linear-gradient(180deg, #f2f3f4 0%, #c4c8cb 100%)",
                style,
            )
            self.assertIn(
                ".search-field input, .mobile-category { width: 100%; "
                "min-height: 2.1rem; padding: .38rem .55rem; border: 3px solid; "
                "border-color: #596269 #fff #fff #596269",
                style,
            )
            self.assertIn("inset -1px -1px 0 #858d93", style)
            self.assertIn("3px 3px 0 #aab0b4, 4px 5px 5px #42494f60", style)
            self.assertIn("1px 1px 0 #9ba2a8, 2px 3px 2px #42494f60", style)
            self.assertIn("box-shadow: inset 2px 2px 3px #596168a0", style)
            self.assertIn("transform: translate(1px, 1px)", style)
            self.assertIn(
                "th button { padding: .16rem .38rem; border: 2px solid; "
                "border-color: #fff #69727a #69727a #fff",
                style,
            )
            self.assertIn(
                'th[aria-sort="ascending"] button, '
                'th[aria-sort="descending"] button',
                style,
            )
            self.assertIn(
                ".table-wrap { overflow: auto; max-height: 34rem; border: 2px solid; "
                "border-color: #596269 #fff #fff #596269",
                style,
            )
            self.assertIn('.category-list button[aria-pressed="true"]', style)
            self.assertIn(".download-link:active", style)
            self.assertIn("linear-gradient(180deg", style)
            self.assertIn("function selectCategory", script)
            self.assertIn("function showDetails", script)

    def test_package_categories_cover_common_workstation_software(self) -> None:
        cases = (
            ("libx11", "X11 shared libraries", "libraries"),
            ("rustc-runtime", "Rust compiler driver shared library", "libraries"),
            ("linux-firmware", "Firmware for network devices", "drivers"),
            ("printer-driver-brlaser", "Printer driver for Brother printers", "drivers"),
            ("nscde", "A desktop environment for X11", "desktop"),
            ("coreutils", "GNU core utilities", "system"),
            ("gddrescue", "GNU data recovery tool", "system"),
            ("exfatprogs", "exFAT file system utilities", "system"),
            ("sdparm", "Output and modify SCSI device parameters", "system"),
            ("mmc-utils", "Userspace tools for MMC and SD devices", "system"),
            (
                "btrfs-compsize",
                "Calculate the compression ratio of files on Btrfs",
                "system",
            ),
            ("openssh-client", "Secure shell networking tools", "networking"),
            ("nginx", "A web server", "web-services"),
            ("hello", "A command line greeting program", "applications"),
        )
        for name, description, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(package_category(name, description), expected)

    def test_package_metadata_requires_complete_https_links(self) -> None:
        package = {
            "author": "Example upstream author",
            "git_repo": "https://github.com/example/hello",
            "git_repo_type": "upstream",
            "git_author_url": "https://github.com/example",
            "packagers": [
                {"name": "arkana-team", "url": "https://github.com/arkana-team"},
                {"name": "debarkak", "url": "https://github.com/debarkak"},
            ],
        }
        valid = {"schema_version": 1, "packages": {"hello": package}}
        self.assertEqual(
            validate_package_metadata(valid, {"hello"})["hello"], package
        )
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            validate_package_metadata(valid, {"hello", "world"})
        with self.assertRaisesRegex(ValueError, "git_repo for hello must be HTTPS"):
            validate_package_metadata(
                {
                    "schema_version": 1,
                    "packages": {
                        "hello": {**package, "git_repo": "http://example.com/repo"}
                    },
                },
                {"hello"},
            )
        with self.assertRaisesRegex(ValueError, "author profile requires"):
            validate_package_metadata(
                {
                    "schema_version": 1,
                    "packages": {
                        "hello": {**package, "author": None}
                    },
                },
                {"hello"},
            )
        with self.assertRaisesRegex(ValueError, "packager URL for hello must be HTTPS"):
            validate_package_metadata(
                {
                    "schema_version": 1,
                    "packages": {
                        "hello": {
                            **package,
                            "packagers": [
                                {"name": "arkana-team", "url": "http://example.com"}
                            ],
                        }
                    },
                },
                {"hello"},
            )
        query_url = {
            "schema_version": 1,
            "packages": {
                "hello": {**package, "git_repo": "https://git.example/?p=hello.git"}
            },
        }
        self.assertEqual(
            validate_package_metadata(query_url, {"hello"})["hello"]["git_repo"],
            "https://git.example/?p=hello.git",
        )

    def test_download_base_url_requires_safe_https(self) -> None:
        for value in (
            "http://example.com/packages",
            "https://user:password@example.com/packages",
            "https://example.com/packages?token=secret",
            "https://example.com/packages#fragment",
            "https://example.com:0/packages",
            "https://example.com:wrong/packages",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_download_base_url(value)


if __name__ == "__main__":
    unittest.main()
