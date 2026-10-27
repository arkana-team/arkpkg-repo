#!/usr/bin/env python3
"""Build the small, architecture-independent X.Org Autoconf macro package."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

NAME = "util-macros"
VERSION = "1.20.2"
SHA256 = "9ac269eba24f672d7d7b3574e4be5f333d13f04a7712303b1821b2a51ac82e8e"
URL = f"https://xorg.freedesktop.org/archive/individual/util/{NAME}-{VERSION}.tar.xz"
# The pinned archive is 83,592 bytes; bound both cache and network reads.
MAX_SOURCE_SIZE = 1024 * 1024


def obtain_source(cache: Path) -> bytes:
    cache = cache.absolute()
    # Check from the root down before creating children; do not resolve symlinks.
    for directory in (*reversed(cache.parents), cache):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = directory.lstat()
        owners = {os.geteuid()} if directory == cache else {0, os.geteuid()}
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in owners
            or (
                info.st_mode & 0o022
                and not (directory != cache and info.st_mode & stat.S_ISVTX)
            )
        ):
            raise ValueError(f"unsafe source cache directory: {directory}")
    archive_path = cache / f"{NAME}-{VERSION}.tar.xz"
    downloaded = False
    try:
        descriptor = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        request = urllib.request.Request(
            URL, headers={"User-Agent": "arkpkg-repo-xorg-macros-builder/1"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            source = response.read(MAX_SOURCE_SIZE + 1)
        downloaded = True
    else:
        with os.fdopen(descriptor, "rb") as cached:
            info = os.fstat(cached.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
                or info.st_nlink != 1
            ):
                raise ValueError(f"unsafe cached source: {archive_path}")
            source = cached.read(MAX_SOURCE_SIZE + 1)
    if len(source) > MAX_SOURCE_SIZE:
        raise ValueError(f"source exceeds {MAX_SOURCE_SIZE} bytes: {archive_path.name}")
    # Keep the verified bytes even if the cache later changes.
    digest = hashlib.sha256(source).hexdigest()
    if digest != SHA256:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {digest}")
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:xz") as archive:
        root = f"{NAME}-{VERSION}"
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in archive: {member.name!r}")
            if path.parts and path.parts[0] != root:
                raise ValueError(f"unexpected source root in archive: {member.name!r}")
    if downloaded:
        # Publish verified bytes without following or truncating an existing entry.
        with tempfile.NamedTemporaryFile(dir=cache, prefix=".download-", delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(source)
                output.close()
                temporary.replace(archive_path)
            finally:
                temporary.unlink(missing_ok=True)
    return source


def build(args: argparse.Namespace, staging: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source = obtain_source(args.source_cache)
    with tarfile.open(fileobj=io.BytesIO(source), mode="r:xz") as archive:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-xorg-sources")
    )
    parser.add_argument(
        "--staging",
        type=Path,
        help="New staging directory to retain after building (must not exist)",
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    if args.staging is not None:
        staging = args.staging.absolute()
        # Claim a fresh directory; never resolve or remove an existing entry.
        staging.mkdir(mode=0o700, parents=True)
        build(args, staging)
    else:
        with tempfile.TemporaryDirectory(prefix="arkpkg-xorg-macros-staging-") as work:
            build(args, Path(work))


if __name__ == "__main__":
    main()
