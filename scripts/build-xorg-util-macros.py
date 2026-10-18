#!/usr/bin/env python3
"""Build the small, architecture-independent X.Org Autoconf macro package."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

NAME = "util-macros"
VERSION = "1.20.2"
SHA256 = "9ac269eba24f672d7d7b3574e4be5f333d13f04a7712303b1821b2a51ac82e8e"
URL = f"https://xorg.freedesktop.org/archive/individual/util/{NAME}-{VERSION}.tar.xz"


def obtain_source(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive_path = cache / f"{NAME}-{VERSION}.tar.xz"
    if not archive_path.is_file():
        request = urllib.request.Request(
            URL, headers={"User-Agent": "arkpkg-repo-xorg-macros-builder/1"}
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            archive_path.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != SHA256:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {digest}")
    with tarfile.open(archive_path, "r:xz") as archive:
        root = f"{NAME}-{VERSION}"
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in archive: {member.name!r}")
            if path.parts and path.parts[0] != root:
                raise ValueError(f"unexpected source root in archive: {member.name!r}")
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-xorg-sources")
    )
    parser.add_argument(
        "--staging", type=Path, default=Path("/tmp/arkpkg-xorg-macros-staging")
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    staging = args.staging.resolve()
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    source = obtain_source(args.source_cache.resolve())
    with tarfile.open(source, "r:xz") as archive:
        archive.extractall(staging / "source", filter="data")
    source_root = staging / "source" / f"{NAME}-{VERSION}"
    payload_root = staging / "payload"
    payload_root.mkdir()
    subprocess.run(
        ["./configure", "--prefix=/usr", "--datadir=/usr/share"],
        cwd=source_root,
        check=True,
    )
    subprocess.run(
        ["make", f"DESTDIR={payload_root}", "install"],
        cwd=source_root,
        check=True,
    )
    license_dir = payload_root / "usr/share/licenses/xorg-util-macros"
    license_dir.mkdir(parents=True)
    shutil.copyfile(source_root / "COPYING", license_dir / "COPYING")

    command = [
        sys.executable,
        str(repo_root / "scripts/build-arkana-packages.py"),
        "--staging",
        str(payload_root),
        "--arkpkg-build",
        str(args.arkpkg_build.resolve()),
        "--packages",
        "xorg-util-macros",
    ]
    if args.output_dir is not None:
        command.extend(("--output-dir", str(args.output_dir.resolve())))
    subprocess.run(command, check=True)
    print(f"Staged X.Org Autoconf macros {VERSION}")


if __name__ == "__main__":
    main()
