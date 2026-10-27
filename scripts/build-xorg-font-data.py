#!/usr/bin/env python3
"""Build X.Org font encoding data for an arkanaOS package repository."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from source_cache import source_snapshot

NAME = "encodings"
VERSION = "1.1.0"
ARCHIVE = f"{NAME}-{VERSION}.tar.xz"
URL = f"https://xorg.freedesktop.org/archive/individual/font/{ARCHIVE}"
SHA256 = "9ff13c621756cfa12e95f32ba48a5b23839e8f577d0048beda66c67dab4de975"
ROOT = f"{NAME}-{VERSION}"


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def source_archive(cache: Path, snapshots: Path) -> Path:
    path = source_snapshot(
        cache, snapshots, ARCHIVE, URL, SHA256,
        user_agent="arkpkg-repo-font-builder/1", timeout=90,
    )
    with tarfile.open(path, "r:xz") as archive:
        roots = set()
        for member in archive:
            item = PurePosixPath(member.name)
            if item.is_absolute() or ".." in item.parts:
                raise ValueError(f"unsafe source path in {path.name}: {member.name!r}")
            if item.parts:
                roots.add(item.parts[0])
        if roots != {ROOT}:
            raise ValueError(f"unexpected source root in {path.name}: {sorted(roots)}")
    return path


def installed_packages(arkpkg: Path, sysroot: Path) -> set[str]:
    command = [str(arkpkg), "--root", str(sysroot), "list"]
    full = command if os.geteuid() == 0 else ["sudo", *command]
    result = subprocess.run(full, capture_output=True, text=True, check=True)
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def build(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="arkpkg-font-data-sources-") as temporary:
        archive = source_archive(args.source_cache, Path(temporary))
        build_from_source(args, archive)


def build_from_source(args: argparse.Namespace, archive: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sysroot = args.sysroot.resolve()
    package_manager = args.arkpkg.resolve()
    package_builder = args.arkpkg_build.resolve()
    output_dir = args.output_dir.resolve()
    staging = args.staging.resolve()
    dependency_archive = next((repo_root / "packages").glob("mkfontscale-*.ark"))

    installed = installed_packages(package_manager, sysroot)
    added_dependency = "mkfontscale" not in installed
    if added_dependency:
        privileged(
            [str(package_manager), "--root", str(sysroot), "install", str(dependency_archive), "-y"]
        )

    if staging.exists():
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                str(staging),
            ]
        )
    staging.mkdir(parents=True)
    logs = Path(tempfile.mkdtemp(prefix="arkpkg-font-data-logs-"))
    work_rel = f"/tmp/arkpkg-encodings-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    try:
        privileged(["install", "-d", "-m", "755", str(work_host)])
        privileged(["install", "-m", "644", str(archive), str(work_host / ARCHIVE)])
        payload_rel = f"{work_rel}/payload"
        shell = "\n".join(
            (
                "set -eu",
                f"cd {work_rel}",
                f"/usr/bin/tar -xf {ARCHIVE}",
                f"cd {ROOT}",
                "./configure --prefix=/usr --with-fontrootdir=/usr/share/fonts --with-encodingsdir=/usr/share/fonts/encodings --disable-gzip-small-encodings --disable-gzip-large-encodings",
                f"/usr/bin/make -j{args.jobs}",
                f"DESTDIR={payload_rel} /usr/bin/make install",
                f"/usr/bin/chmod -R a+rX {payload_rel}",
            )
        )
        log = logs / "encodings.log"
        try:
            privileged(
                [
                    "chroot",
                    str(sysroot),
                    "/usr/bin/env",
                    "LC_ALL=C",
                    "LANG=C",
                    "PATH=/usr/bin:/bin",
                    "/usr/bin/bash",
                    "-c",
                    shell,
                ],
                log=log,
            )
        except subprocess.CalledProcessError:
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            print("\n".join(tail), file=sys.stderr)
            raise

        payload = sysroot / payload_rel.lstrip("/")
        for relative in (
            "usr/share/fonts/encodings/encodings.dir",
            "usr/share/fonts/encodings/large/encodings.dir",
            "usr/share/fonts/encodings/adobe-standard.enc",
            "usr/share/fonts/encodings/large/gb18030-0.enc",
        ):
            if not (payload / relative).is_file():
                raise FileNotFoundError(f"encoding data build did not install {relative}")

        destination = staging / "usr/share/fonts/encodings"
        source = payload / "usr/share/fonts/encodings"
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True,symlinks=True)",
                str(source),
                str(destination),
            ]
        )
        privileged(["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(destination)])
        with tarfile.open(archive, "r:xz") as source_archive_file:
            license_file = source_archive_file.extractfile(f"{ROOT}/COPYING")
            if license_file is None:
                raise ValueError(f"{ARCHIVE} has no COPYING file")
            license_path = staging / "usr/share/licenses/xorg-font-encodings/COPYING"
            privileged(["install", "-d", "-m", "755", str(license_path.parent)])
            privileged(
                ["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(staging / "usr")]
            )
            license_path.write_bytes(license_file.read())

        subprocess.run(
            [
                sys.executable,
                str(repo_root / "scripts/build-arkana-packages.py"),
                "--staging",
                str(staging),
                "--arkpkg-build",
                str(package_builder),
                "--output-dir",
                str(output_dir),
                "--packages",
                "xorg-font-encodings",
            ],
            check=True,
        )
    finally:
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                str(work_host),
            ]
        )
        if added_dependency:
            privileged(
                [str(package_manager), "--root", str(sysroot), "remove", "mkfontscale", "-y"]
            )
        shutil.rmtree(logs, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--arkpkg", type=Path, required=True)
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "packages"
    )
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument(
        "--source-cache", type=Path, default=Path(tempfile.gettempdir()) / "arkpkg-font-data-sources"
    )
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
