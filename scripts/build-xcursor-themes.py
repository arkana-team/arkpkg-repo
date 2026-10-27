#!/usr/bin/env python3
"""Build the classic X.Org Xcursor theme package in an arkanaOS sysroot."""

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

from package_metadata import read_metadata_member
from source_cache import source_snapshot

SOURCE_NAME = "xcursor-themes"
VERSION = "1.0.7"
SOURCE_SHA256 = "95bae8f48823d894a05bf42dfbf453674ab7dbdeb11e2bc079e8525ad47378c8"
SOURCE_URL = f"https://xorg.freedesktop.org/archive/individual/data/{SOURCE_NAME}-{VERSION}.tar.xz"


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def installed_packages(arkpkg: Path, sysroot: Path) -> set[str]:
    command = [str(arkpkg), "--root", str(sysroot), "list"]
    full = command if os.geteuid() == 0 else ["sudo", *command]
    result = subprocess.run(full, capture_output=True, text=True, check=True)
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def package_name(archive_path: Path) -> str:
    import lz4.frame

    with (
        lz4.frame.open(archive_path, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r|") as archive,
    ):
        for member in archive:
            if member.name != "ARKPKG" or not member.isfile():
                continue
            metadata = read_metadata_member(archive, member)
            if metadata is None:
                break
            for line in metadata.splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "Name":
                    return value.strip()
    raise ValueError(f"package archive has no Name field: {archive_path}")


def source_archive(cache: Path, snapshots: Path) -> Path:
    archive_path = source_snapshot(
        cache,
        snapshots,
        f"{SOURCE_NAME}-{VERSION}.tar.xz",
        SOURCE_URL,
        SOURCE_SHA256,
        user_agent="arkpkg-repo-xcursor-builder/1",
        timeout=90,
    )
    expected_root = f"{SOURCE_NAME}-{VERSION}"
    with tarfile.open(archive_path, "r:xz") as source:
        roots = set()
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in {archive_path.name}: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
        if roots != {expected_root}:
            raise ValueError(f"unexpected source root in {archive_path.name}: {sorted(roots)}")
    return archive_path


def copy_tree(source: Path, destination: Path) -> None:
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


def build(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="arkpkg-xcursor-sources-") as temporary:
        build_with_sources(args, Path(temporary))


def build_with_sources(args: argparse.Namespace, snapshots: Path) -> None:
    sysroot = args.sysroot.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    package_builder = repo_root / "scripts/build-arkana-packages.py"
    archive = source_archive(args.source_cache.resolve(), snapshots)
    output_staging = args.output_staging.resolve()

    for binary in ("bash", "make", "pkg-config", "tar"):
        if not (sysroot / "usr/bin" / binary).exists():
            raise FileNotFoundError(f"target build root lacks /usr/bin/{binary}")
    if args.arkpkg is None and args.build_dependency_archive:
        raise ValueError("--arkpkg is required with --build-dependency-archive")

    installed_build_dependencies: list[str] = []
    if args.build_dependency_archive:
        installed = installed_packages(args.arkpkg.resolve(), sysroot)
        for dependency_archive in args.build_dependency_archive:
            dependency_archive = dependency_archive.resolve()
            name = package_name(dependency_archive)
            if name in installed:
                continue
            privileged(
                [
                    str(args.arkpkg.resolve()),
                    "--root",
                    str(sysroot),
                    "install",
                    str(dependency_archive),
                    "-y",
                ]
            )
            installed_build_dependencies.append(name)
            installed.add(name)

    if output_staging.exists():
        shutil.rmtree(output_staging)
    output_staging.mkdir(parents=True)
    logs = Path(tempfile.mkdtemp(prefix="arkpkg-xcursor-logs-"))
    work_host: Path | None = None
    try:
        work_rel = f"/tmp/arkpkg-xcursor-{uuid.uuid4().hex}"
        work_host = sysroot / work_rel.lstrip("/")
        privileged(["install", "-d", "-m", "755", str(work_host)])
        archive_in_root = work_host / archive.name
        privileged(["install", "-m", "644", str(archive), str(archive_in_root)])
        payload_rel = f"{work_rel}/payload"
        shell = "\n".join(
            (
                "set -eu",
                f"cd {work_rel}",
                f"/usr/bin/tar -xf {archive.name}",
                f"cd {SOURCE_NAME}-{VERSION}",
                "./configure --prefix=/usr --with-cursordir=/usr/share/icons",
                f"/usr/bin/make -j{args.jobs}",
                f"/usr/bin/make DESTDIR={payload_rel} install",
                f"/usr/bin/chmod -R a+rX {payload_rel}",
            )
        )
        log = logs / "xcursor-themes.log"
        try:
            privileged(
                [
                    "chroot",
                    str(sysroot),
                    "/usr/bin/env",
                    "LC_ALL=C",
                    "LANG=C",
                    "PATH=/usr/bin:/bin",
                    "PKG_CONFIG_PATH=/usr/lib/pkgconfig:/usr/share/pkgconfig",
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
        expected = (
            "usr/share/icons/whiteglass/cursors/left_ptr",
            "usr/share/icons/redglass/cursors/left_ptr",
            "usr/share/icons/handhelds/cursors/left_ptr",
        )
        for relative in expected:
            if not (payload / relative).is_file():
                raise FileNotFoundError(f"cursor build did not install {relative}")

        package_staging = output_staging / SOURCE_NAME
        license_path = package_staging / "usr/share/licenses/xcursor-themes/COPYING"
        license_path.parent.mkdir(parents=True, exist_ok=True)
        copy_tree(payload / "usr/share/icons", package_staging / "usr/share/icons")
        with tarfile.open(archive, "r:xz") as source:
            license_member = source.extractfile(f"{SOURCE_NAME}-{VERSION}/COPYING")
            if license_member is None:
                raise ValueError(f"{archive.name} has no COPYING file")
            license_path.write_bytes(license_member.read())
        subprocess.run(
            [
                sys.executable,
                str(package_builder),
                "--staging",
                str(package_staging),
                "--arkpkg-build",
                str(args.arkpkg_build.resolve()),
                "--output-dir",
                str(args.output_dir.resolve()),
                "--packages",
                SOURCE_NAME,
            ],
            check=True,
        )
    finally:
        if work_host is not None:
            privileged(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                    str(work_host),
                ]
            )
        if args.arkpkg is not None:
            for name in reversed(installed_build_dependencies):
                privileged(
                    [
                        str(args.arkpkg.resolve()),
                        "--root",
                        str(sysroot),
                        "remove",
                        name,
                        "-y",
                    ]
                )
        shutil.rmtree(logs, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--arkpkg", type=Path)
    parser.add_argument(
        "--build-dependency-archive",
        type=Path,
        action="append",
        default=[],
        help="temporarily install a build dependency from an arkpkg archive",
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=repo_root / "packages")
    parser.add_argument("--output-staging", type=Path, required=True)
    parser.add_argument(
        "--source-cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "arkpkg-xcursor-source-cache",
    )
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
