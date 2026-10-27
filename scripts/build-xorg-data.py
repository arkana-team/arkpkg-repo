#!/usr/bin/env python3
"""Build small data-only packages from verified X.Org source releases."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from source_cache import source_snapshot

NAME = "xbitmaps"
VERSION = "1.1.4"
SHA256 = "895722f136e21e728c52f2d99fd2dae95018b9ddad1bac1f29d61bcd6593721d"
URL = f"https://xorg.freedesktop.org/archive/individual/data/{NAME}-{VERSION}.tar.xz"


def obtain_source(cache: Path, snapshots: Path) -> Path:
    return source_snapshot(
        cache,
        snapshots,
        f"{NAME}-{VERSION}.tar.xz",
        URL,
        SHA256,
        user_agent="arkpkg-repo-xorg-data-builder/1",
        timeout=90,
    )


def stage_package(archive_path: Path, staging: Path) -> int:
    root = staging / "usr/include/X11/bitmaps"
    licenses = staging / f"usr/share/licenses/{NAME}"
    root.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:xz") as archive:
        prefix = f"{NAME}-{VERSION}/"
        makefile = archive.extractfile(f"{prefix}Makefile.am")
        pkgconfig = archive.extractfile(f"{prefix}xbitmaps.pc.in")
        license_file = archive.extractfile(f"{prefix}COPYING")
        if makefile is None or pkgconfig is None or license_file is None:
            raise ValueError("upstream archive is missing its manifest or license")
        text = makefile.read().decode("utf-8")
        match = re.search(r"(?ms)^dist_bitmap_DATA\s*=\s*\\\n(.*?)(?=\n\s*\n|\Z)", text)
        if match is None:
            raise ValueError("upstream Makefile.am has no dist_bitmap_DATA list")
        names = [token for token in re.sub(r"\\\s*\n", " ", match.group(1)).split()]
        if not names or len(names) != len(set(names)):
            raise ValueError("upstream bitmap list is empty or has duplicate names")
        for name in names:
            member_name = f"{prefix}{name}"
            member = archive.getmember(member_name)
            if not member.isfile():
                raise ValueError(f"upstream bitmap is not a regular file: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"could not read upstream bitmap: {name}")
            (root / name).write_bytes(stream.read())
        (licenses / "COPYING").write_bytes(license_file.read())
        pc = pkgconfig.read().decode("utf-8")
        for token, value in (
            ("@prefix@", "/usr"),
            ("@exec_prefix@", "${prefix}"),
            ("@includedir@", "${prefix}/include"),
            ("@PACKAGE_VERSION@", VERSION),
        ):
            pc = pc.replace(token, value)
        if "@" in pc:
            raise ValueError("unexpanded variable remains in xbitmaps.pc")
        pkgconfig_dir = staging / "usr/lib/pkgconfig"
        pkgconfig_dir.mkdir(parents=True, exist_ok=True)
        (pkgconfig_dir / "xbitmaps.pc").write_text(pc, encoding="utf-8")
    return len(names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-xorg-sources")
    )
    parser.add_argument(
        "--staging",
        type=Path,
        help="new staging directory (default: private temporary directory)",
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    if args.staging is None:
        with tempfile.TemporaryDirectory(prefix="arkpkg-xorg-data-staging-") as temporary:
            build(args, Path(temporary))
    else:
        staging = args.staging.absolute()
        staging.mkdir(mode=0o700, parents=True)
        build(args, staging)


def build(args: argparse.Namespace, staging: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="arkpkg-xorg-data-source-") as temporary:
        source = obtain_source(args.source_cache, Path(temporary))
        count = stage_package(source, staging)
    command = [
        sys.executable,
        str(repo_root / "scripts/build-arkana-packages.py"),
        "--staging",
        str(staging),
        "--arkpkg-build",
        str(args.arkpkg_build.resolve()),
        "--packages",
        NAME,
    ]
    if args.output_dir is not None:
        command.extend(("--output-dir", str(args.output_dir.resolve())))
    subprocess.run(command, check=True)
    print(f"Staged {count} upstream X.Org bitmaps in {NAME}")
    if args.staging is not None:
        print(f"Package staging preserved at {staging}")


if __name__ == "__main__":
    main()
