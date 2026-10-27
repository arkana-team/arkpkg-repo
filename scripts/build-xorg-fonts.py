#!/usr/bin/env python3
"""Build selected X.Org bitmap-font packages from pinned upstream archives."""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from source_cache import source_snapshot

FONT_PACKAGES = (
    (
        "font-misc-misc",
        "1.1.3",
        "79abe361f58bb21ade9f565898e486300ce1cc621d5285bec26e14b6a8618fed",
        "misc",
        ("COPYING",),
    ),
    (
        "font-bh-75dpi",
        "1.0.4",
        "6026d8c073563dd3cbb4878d0076eed970debabd21423b3b61dd90441b9e7cda",
        "75dpi",
        ("COPYING", "LU_LEGALNOTICE"),
    ),
    (
        "font-bh-100dpi",
        "1.0.4",
        "fd8f5efe8491faabdd2744808d3d4eafdae5c83e617017c7fddd2716d049ab1e",
        "100dpi",
        ("COPYING", "LU_LEGALNOTICE"),
    ),
    (
        "font-sun-misc",
        "1.0.4",
        "dd84dd116d927affa4fa0fa29727b3ecfc0f064238817c0a1e552a0ac384db9f",
        "sun-misc",
        ("COPYING",),
    ),
    (
        "font-schumacher-misc",
        "1.1.3",
        "8b849f0cdb1e55a34cc3dd8b0fb37443fabbc224d5ba44085569581244a68070",
        "schumacher-misc",
        ("COPYING",),
    ),
    (
        "font-dec-misc",
        "1.0.4",
        "82d968201d8ff8bec0e51dccd781bb4d4ebf17e11004944279bdc0201e161af7",
        "dec-misc",
        ("COPYING",),
    ),
    (
        "font-sony-misc",
        "1.0.4",
        "e6b09f823fccb06e0bd0b2062283b6514153323bd8a7486e9c2e3f55ab84946b",
        "sony-misc",
        ("COPYING",),
    ),
    (
        "font-micro-misc",
        "1.0.4",
        "2ee0b9d6bd7ae849aff1bd82efab44a1b6b368fbb5e11d12ff7f015a3df6f943",
        "micro-misc",
        ("COPYING",),
    ),
    (
        "font-misc-cyrillic",
        "1.0.4",
        "76021a7f53064001914a57fd08efae57f76b68f0a24dca8ab1b245474ee8e993",
        "misc-cyrillic",
        ("COPYING",),
    ),
    (
        "font-daewoo-misc",
        "1.0.4",
        "f63c8b3dc8f30098cb868b7db2c2c0c8b5b3fd2cefd044035697a43d4c7a4f31",
        "daewoo-misc",
        ("COPYING",),
    ),
    (
        "font-arabic-misc",
        "1.0.4",
        "46ffe61b52c78a1d2dca70ff20a9f2d84d69744639cab9a085c7a7ee17663467",
        "arabic-misc",
        ("COPYING",),
    ),
    (
        "font-bitstream-75dpi",
        "1.0.4",
        "aaeb34d87424a9c2b0cf0e8590704c90cb5b42c6a3b6a0ef9e4676ef773bf826",
        "bitstream-75dpi",
        ("COPYING",),
    ),
    (
        "font-bitstream-100dpi",
        "1.0.4",
        "2d1cc682efe4f7ebdf5fbd88961d8ca32b2729968728633dea20a1627690c1a7",
        "bitstream-100dpi",
        ("COPYING",),
    ),
    (
        "font-cursor-misc",
        "1.0.4",
        "25d9c9595013cb8ca08420509993a6434c917e53ca1fec3f63acd45a19d4f982",
        "cursor-misc",
        ("COPYING",),
    ),
    (
        "font-isas-misc",
        "1.0.4",
        "47e595bbe6da444b9f6fcaa26539abc7ba1989e23afa6cdc49e22e484cc438fc",
        "isas-misc",
        ("COPYING",),
    ),
    (
        "font-jis-misc",
        "1.0.4",
        "78d1eff6c471f7aa6802a26d62cccf51d8e5185586406d9b6e1ee691b0bffad0",
        "jis-misc",
        ("COPYING",),
    ),
    (
        "font-mutt-misc",
        "1.0.4",
        "b12359f4e12c23bcfcb448b918297e975fa91bef5293d88d3c25343cc768bb24",
        "mutt-misc",
        ("COPYING",),
    ),
    (
        "font-screen-cyrillic",
        "1.0.5",
        "8f758bb8cd580c7e655487d1d0db69d319acae54d932b295d96d9d9b83fde5c0",
        "screen-cyrillic",
        ("COPYING",),
    ),
    (
        "font-misc-ethiopic",
        "1.0.5",
        "4749a7e6e1a1eef6c91fcc9a04e8b1c0ed027d40c1599e5a6c93270d8469b612",
        "misc-ethiopic",
        ("COPYING",),
    ),
    (
        "font-misc-meltho",
        "1.0.4",
        "63be5ec17078898f263c24096a68b43ae5b06b88852e42549afa03d124d65219",
        "misc-meltho",
        ("COPYING",),
    ),
    (
        "font-bh-ttf",
        "1.0.4",
        "85a5f90d00c48c2b06fd125ea8adbc8b8ee97429e3075081c8710926efec3a56",
        "bh-ttf",
        ("COPYING", "COPYRIGHT.BH"),
    ),
    (
        "font-bh-type1",
        "1.0.4",
        "19dec3ec06abde6bedd10094579e928be0f0fc3bdb4fbe93f4c69cce406d72a6",
        "bh-type1",
        ("COPYING", "COPYRIGHT.BH"),
    ),
    (
        "font-bitstream-type1",
        "1.0.4",
        "de2f238b4cd72db4228a0ba67829d76a2b7c039e22993d66a722ee385248c628",
        "bitstream-type1",
        ("COPYING", "Copyright"),
    ),
    (
        "font-ibm-type1",
        "1.0.4",
        "c4395e95ba46d40c4ad1737e91cac20c0ab75411329b60db5d99fed92b60ce7f",
        "ibm-type1",
        ("COPYING", "COPYRIGHT.IBM"),
    ),
    (
        "font-cronyx-cyrillic",
        "1.0.4",
        "dc0781ce0dcbffdbf6aae1a00173a13403f92b0de925bca5a9e117e4e2d6b789",
        "cronyx-cyrillic",
        ("COPYING", "COPYRIGHT"),
    ),
    (
        "font-adobe-75dpi",
        "1.0.4",
        "1281a62dbeded169e495cae1a5b487e1f336f2b4d971d92911c59c103999b911",
        "adobe-75dpi",
        ("COPYING",),
    ),
    (
        "font-adobe-100dpi",
        "1.0.4",
        "b67aff445e056328d53f9732d39884f55dd8d303fc25af3dbba33a8ba35a9ccf",
        "adobe-100dpi",
        ("COPYING",),
    ),
    (
        "font-adobe-utopia-75dpi",
        "1.0.5",
        "a726245932d0724fa0c538c992811d63d597e5f53928f4048e9caf5623797760",
        "adobe-utopia-75dpi",
        ("COPYING",),
    ),
    (
        "font-adobe-utopia-100dpi",
        "1.0.5",
        "fb84ec297a906973548ca59b7c6daeaad21244bec5d3fb1e7c93df5ef43b024b",
        "adobe-utopia-100dpi",
        ("COPYING",),
    ),
    (
        "font-adobe-utopia-type1",
        "1.0.5",
        "4cb280bc47693b07c5e00fd0e5ad5721aabebc0548c3f06774e5cc3cbcf75697",
        "adobe-utopia-type1",
        ("COPYING",),
    ),
    (
        "font-winitzki-cyrillic",
        "1.0.4",
        "3b6d82122dc14776e3afcd877833a7834e1f900c53fc1c7bb2d67c781cfa97a8",
        "winitzki-cyrillic",
        ("COPYING",),
    ),
)
FONT_EXTRA_SOURCES = {
    "font-bh-75dpi": (
        (
            "font-bh-lucidatypewriter-75dpi",
            "1.0.4",
            "864e2c39ac61f04f693fc2c8aaaed24b298c2cd40283cec12eee459c5635e8f5",
        ),
    ),
    "font-bh-100dpi": (
        (
            "font-bh-lucidatypewriter-100dpi",
            "1.0.4",
            "76ec09eda4094a29d47b91cf59c3eba229c8f7d1ca6bae2abbb3f925e33de8f2",
        ),
    ),
}
ALIAS_NAME = "font-alias"
ALIAS_VERSION = "1.0.6"
ALIAS_URL = f"https://xorg.freedesktop.org/archive/individual/font/{ALIAS_NAME}-{ALIAS_VERSION}.tar.xz"
ALIAS_SHA256 = "f6a5e968b616e728a981fd1fd9ed5fe2ca0fe18fbe072b1888cb3f71b701e4c0"
FONT_BASE_URL = "https://xorg.freedesktop.org/archive/individual/font"
FONT_NAME = re.compile(rb"^FONT\s+([^\r\n]+)", re.MULTILINE)


def obtain_source(
    cache: Path, name: str, version: str, url: str, expected_sha256: str,
    snapshots: Path,
) -> Path:
    cache = cache.parent.resolve() / cache.name
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache_stat = cache.lstat()
    if (
        not stat.S_ISDIR(cache_stat.st_mode)
        or cache_stat.st_uid != os.geteuid()
        or cache_stat.st_mode & 0o022
    ):
        raise ValueError(f"source cache must be owned and writable only by this user: {cache}")
    # A writable parent could let another user replace the checked cache directory.
    for parent in cache.parents:
        parent_stat = parent.stat()
        if parent_stat.st_uid not in {0, os.geteuid()} or (
            parent_stat.st_mode & 0o022 and not parent_stat.st_mode & stat.S_ISVTX
        ):
            raise ValueError(f"unsafe source cache parent: {parent}")
    return source_snapshot(
        cache, snapshots, f"{name}-{version}.tar.xz", url, expected_sha256,
        user_agent="arkpkg-repo-font-builder/1", timeout=90,
    )


def stage_fonts(
    source: Path,
    staging: Path,
    name: str,
    version: str,
    font_directory: str,
    license_files: tuple[str, ...],
    extra_sources: tuple[tuple[Path, str, str], ...] = (),
) -> int:
    fonts = staging / f"usr/share/fonts/{font_directory}"
    licenses = staging / f"usr/share/licenses/{name}"
    fonts.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, str]] = []
    copied = 0

    source_specs = [(source, name, version, False)] + [
        (path, extra_name, extra_version, True)
        for path, extra_name, extra_version in extra_sources
    ]
    for source_path, source_name, source_version, is_extra in source_specs:
        with tarfile.open(source_path, "r:xz") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or len(path.parts) != 2:
                    continue
                if (
                    path.parts[0] != f"{source_name}-{source_version}"
                    or not member.isfile()
                ):
                    continue
                if path.name.endswith(".bdf"):
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError(f"could not read font source {member.name}")
                    data = stream.read()
                    match = FONT_NAME.search(data)
                    if match is None:
                        raise ValueError(f"font has no XLFD name: {member.name}")
                    if (fonts / path.name).exists():
                        raise ValueError(
                            f"font filename collision in {name}: {path.name}"
                        )
                    (fonts / path.name).write_bytes(data)
                    entries.append((path.name, match.group(1).decode("ascii")))
                    copied += 1
                elif path.suffix.lower() in {".otf", ".ttf", ".pfa", ".pfb"}:
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError(f"could not read font file {member.name}")
                    data = stream.read()
                    suffix = path.suffix.lower()
                    signatures = {
                        ".otf": {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"},
                        ".ttf": {b"\x00\x01\x00\x00", b"true"},
                        ".pfa": {b"%!"},
                        ".pfb": {b"\x80\x01"},
                    }
                    if not any(data.startswith(signature) for signature in signatures[suffix]):
                        raise ValueError(f"unrecognized font data: {member.name}")
                    if (fonts / path.name).exists():
                        raise ValueError(
                            f"font filename collision in {name}: {path.name}"
                        )
                    (fonts / path.name).write_bytes(data)
                    copied += 1
                elif path.name in {*license_files, "COPYING", "LU_LEGALNOTICE"}:
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError(
                            f"could not read upstream font license {path.name}"
                        )
                    license_name = (
                        f"{path.name}-{source_name}" if is_extra else path.name
                    )
                    (licenses / license_name).write_bytes(stream.read())

    if not copied or any(not (licenses / item).is_file() for item in license_files):
        raise ValueError(f"{name} archive is missing fonts or license files")
    if entries:
        entries.sort()
        (fonts / "fonts.dir").write_text(
            str(len(entries))
            + "\n"
            + "".join(f"{filename} {font_name}\n" for filename, font_name in entries),
            encoding="ascii",
        )
    return copied


def stage_aliases(source: Path, staging: Path) -> int:
    aliases_root = staging / "usr/share/fonts"
    licenses = staging / f"usr/share/licenses/{ALIAS_NAME}"
    licenses.mkdir(parents=True, exist_ok=True)
    overrides = {
        "fixed": "-Misc-Fixed-Medium-R-SemiCondensed--13-120-75-75-C-60-ISO10646-1",
        "variable": "-Misc-Fixed-Medium-R-Normal--13-120-75-75-C-80-ISO10646-1",
        "6x13": "-Misc-Fixed-Medium-R-SemiCondensed--13-120-75-75-C-60-ISO10646-1",
        "6x13bold": "-Misc-Fixed-Bold-R-SemiCondensed--13-120-75-75-C-60-ISO10646-1",
        "8x13": "-Misc-Fixed-Medium-R-Normal--13-120-75-75-C-80-ISO10646-1",
        "8x13bold": "-Misc-Fixed-Bold-R-Normal--13-120-75-75-C-80-ISO10646-1",
        "9x15": "-Misc-Fixed-Medium-R-Normal--15-140-75-75-C-90-ISO10646-1",
        "9x15bold": "-Misc-Fixed-Bold-R-Normal--15-140-75-75-C-90-ISO10646-1",
        "9x18": "-Misc-Fixed-Medium-R-Normal--18-120-100-100-C-90-ISO10646-1",
        "9x18bold": "-Misc-Fixed-Bold-R-Normal--18-120-100-100-C-90-ISO10646-1",
        "10x20": "-Misc-Fixed-Medium-R-Normal--20-200-75-75-C-100-ISO10646-1",
        "12x24": "-Misc-Fixed-Medium-R-Normal--20-200-75-75-C-100-ISO10646-1",
        "-*-helvetica-medium-r-*-*-*-120-*-*-*-*-*-*": "-B&H-Lucida-Medium-R-Normal-Sans-12-120-75-75-P-79-ISO10646-1",
        "-*-helvetica-bold-r-normal--*-120-*-*-*-*-iso8859-1": "-B&H-Lucida-Bold-R-Normal-Sans-12-120-75-75-P-79-ISO10646-1",
        "-*-courier-medium-r-normal--*-120-*-*-*-*-iso8859-1": "-B&H-LucidaTypewriter-Medium-R-Normal-Sans-12-120-75-75-M-70-ISO10646-1",
        "-*-courier-bold-r-normal--*-120-*-*-*-*-iso8859-1": "-B&H-LucidaTypewriter-Bold-R-Normal-Sans-12-120-75-75-M-70-ISO10646-1",
    }
    copied = 0
    with tarfile.open(source, "r:xz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or len(path.parts) != 3:
                continue
            if (
                path.parts[0] != f"{ALIAS_NAME}-{ALIAS_VERSION}"
                or path.name != "fonts.alias"
                or not member.isfile()
            ):
                continue
            directory = path.parts[1]
            if directory not in {"misc", "75dpi", "100dpi", "cyrillic"}:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"could not read alias file {member.name}")
            lines = stream.read().decode("utf-8").splitlines()
            destination = aliases_root / directory
            destination.mkdir(parents=True, exist_ok=True)
            if directory == "misc":
                names = set(overrides)
                lines = [
                    line
                    for line in lines
                    if not line.split() or line.split()[0] not in names
                ]
                lines = [f"{name} {font}" for name, font in overrides.items()] + lines
            (destination / "fonts.alias").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            copied += 1
        license_member = archive.getmember(f"{ALIAS_NAME}-{ALIAS_VERSION}/COPYING")
        license_stream = archive.extractfile(license_member)
        if license_stream is None:
            raise ValueError("could not read upstream font-alias license")
        (licenses / "COPYING").write_bytes(license_stream.read())
    if copied != 4:
        raise ValueError(f"expected four upstream alias files, found {copied}")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-xorg-sources")
    )
    parser.add_argument(
        "--staging", type=Path,
        help="create and retain a new staging directory (default: private temporary directory)",
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--packages",
        nargs="+",
        choices=(ALIAS_NAME, *(name for name, *_ in FONT_PACKAGES)),
        help="build only selected font packages (default: all packages)",
    )
    args = parser.parse_args()

    if args.staging is None:
        with tempfile.TemporaryDirectory(prefix="arkpkg-font-staging-") as temporary:
            build(args, Path(temporary))
    else:
        staging = args.staging.absolute()
        # Refuse pre-existing entries, including symlinks, without deleting them.
        staging.mkdir(mode=0o700, parents=True)
        build(args, staging)


def build(args: argparse.Namespace, staging: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="arkpkg-font-sources-") as temporary:
        build_with_sources(args, staging, Path(temporary))


def build_with_sources(args: argparse.Namespace, staging: Path, snapshots: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    alias_staging = staging / ALIAS_NAME
    font_stagings = []
    font_counts = {}
    package_names = args.packages or [
        ALIAS_NAME,
        *(name for name, *_ in FONT_PACKAGES),
    ]
    cache = args.source_cache.absolute()
    for name, version, digest, font_directory, license_files in FONT_PACKAGES:
        if name not in package_names:
            continue
        source = obtain_source(
            cache,
            name,
            version,
            f"{FONT_BASE_URL}/{name}-{version}.tar.xz",
            digest,
            snapshots,
        )
        package_staging = staging / name
        extra_sources = []
        for extra_name, extra_version, extra_digest in FONT_EXTRA_SOURCES.get(name, ()):
            extra_sources.append(
                (
                    obtain_source(
                        cache,
                        extra_name,
                        extra_version,
                        f"{FONT_BASE_URL}/{extra_name}-{extra_version}.tar.xz",
                        extra_digest,
                        snapshots,
                    ),
                    extra_name,
                    extra_version,
                )
            )
        count = stage_fonts(
            source,
            package_staging,
            name,
            version,
            font_directory,
            license_files,
            tuple(extra_sources),
        )
        font_stagings.append((name, package_staging))
        font_counts[name] = count
    if ALIAS_NAME in package_names:
        alias_source = obtain_source(
            cache, ALIAS_NAME, ALIAS_VERSION, ALIAS_URL, ALIAS_SHA256, snapshots
        )
        alias_count = stage_aliases(alias_source, alias_staging)
    packages_to_build = (
        ([(ALIAS_NAME, alias_staging)] if ALIAS_NAME in package_names else [])
        + font_stagings
    )
    for package_name, package_staging in packages_to_build:
        command = [
            sys.executable,
            str(repo_root / "scripts/build-arkana-packages.py"),
            "--staging",
            str(package_staging),
            "--arkpkg-build",
            str(args.arkpkg_build.resolve()),
            "--packages",
            package_name,
        ]
        if args.output_dir is not None:
            command.extend(("--output-dir", str(args.output_dir.resolve())))
        subprocess.run(command, check=True)
    for package_name, count in font_counts.items():
        print(f"Staged {count} upstream font files in {package_name}")
    if ALIAS_NAME in package_names:
        print(f"Staged {alias_count} upstream X.Org font alias sets")
    if args.staging is not None:
        print(f"Package staging preserved at {staging}")


if __name__ == "__main__":
    main()
