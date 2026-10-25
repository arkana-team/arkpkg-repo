#!/usr/bin/env python3
"""Build pkgconf and its shared library inside an arkanaOS sysroot."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath

VERSION = "3.0.8"
SOURCE_URL = f"https://distfiles.ariadne.space/pkgconf/pkgconf-{VERSION}.tar.xz"
SOURCE_SHA256 = "943f0bc88bcaeceb630b00262e2a8ad5ea4fac3fba72a4267d8b9ec6b35d1194"


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def privileged_file_test(path: Path, test: str = "-f") -> bool:
    command = ["test", test, str(path)]
    if os.geteuid() != 0:
        command.insert(0, "sudo")
    return subprocess.run(command, check=False).returncode == 0


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
            stream = archive.extractfile(member)
            if stream is None:
                break
            for line in stream.read().decode("utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "Name":
                    return value.strip()
    raise ValueError(f"package archive has no Name field: {archive_path}")


def source_archive(cache: Path, snapshot_dir: Path) -> Path:
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache_stat = cache.stat()
    if cache_stat.st_uid != os.geteuid() or cache_stat.st_mode & 0o022:
        raise ValueError(
            f"source cache must be owned by the current user and not writable by others: {cache}"
        )
    archive_path = cache / f"pkgconf-{VERSION}.tar.xz"
    if not archive_path.is_file():
        request = urllib.request.Request(
            SOURCE_URL, headers={"User-Agent": "arkpkg-repo-pkgconf-builder/1"}
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            archive_path.open("xb") as output,
        ):
            shutil.copyfileobj(response, output)
    # Validate and build from a private copy; the cache may change at any time.
    snapshot = snapshot_dir / archive_path.name
    shutil.copyfile(archive_path, snapshot)
    archive_path = snapshot
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {digest}")
    with tarfile.open(archive_path, "r:xz") as source:
        roots = set()
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
        if roots != {f"pkgconf-{VERSION}"}:
            raise ValueError(f"unexpected source roots in {archive_path}: {roots}")
    return archive_path


def build(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="arkpkg-pkgconf-source-") as temporary:
        archive = source_archive(args.source_cache.resolve(), Path(temporary))
        build_from_source(args, archive)


def build_from_source(args: argparse.Namespace, archive: Path) -> None:
    sysroot = args.sysroot.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    output_staging = args.output_staging.resolve()

    for binary in ("gcc", "make", "tar", "strip", "libtool"):
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

    work_rel = f"/tmp/arkpkg-pkgconf-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    logs = Path(tempfile.mkdtemp(prefix="arkpkg-pkgconf-build-logs-"))
    try:
        privileged(["install", "-d", "-m", "755", str(work_host)])
        archive_in_root = work_host / archive.name
        privileged(["install", "-m", "644", str(archive), str(archive_in_root)])
        payload_rel = f"{work_rel}/payload"
        shell = "\n".join(
            (
                "set -eu",
                f"cd {work_rel}",
                f"/usr/bin/tar -xf {archive.name}",
                f"cd pkgconf-{VERSION}",
                (
                    "./configure --prefix=/usr --libdir=/usr/lib --disable-static "
                    "--with-pkg-config-dir=/usr/lib/pkgconfig:/usr/share/pkgconfig "
                    "--with-system-libdir=/usr/lib --with-system-includedir=/usr/include"
                ),
                f"/usr/bin/make -j{args.jobs}",
                f"/usr/bin/make DESTDIR={payload_rel} install",
                f"/usr/bin/chmod -R a+rX {payload_rel}",
                (
                    f"if [ ! -e {payload_rel}/usr/bin/pkg-config ]; then "
                    f"/usr/bin/ln -s pkgconf {payload_rel}/usr/bin/pkg-config; fi"
                ),
                f"/usr/bin/strip --strip-unneeded {payload_rel}/usr/bin/pkgconf",
            )
        )
        log = logs / "pkgconf.log"
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
        required = (
            "usr/bin/pkgconf",
            "usr/bin/pkg-config",
            "usr/lib/libpkgconf.so.8",
            "usr/lib/pkgconfig/libpkgconf.pc",
            "usr/include/pkgconf/libpkgconf/libpkgconf.h",
        )
        for relative in required:
            if not privileged_file_test(payload / relative):
                raise FileNotFoundError(f"pkgconf build did not install {relative}")

        if output_staging.exists():
            shutil.rmtree(output_staging)
        output_staging.mkdir(parents=True)
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True,symlinks=True)",
                str(payload),
                str(output_staging),
            ]
        )
        privileged(["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(output_staging)])
    finally:
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

    subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/build-arkana-packages.py"),
            "--staging",
            str(output_staging),
            "--arkpkg-build",
            str(args.arkpkg_build.resolve()),
            "--output-dir",
            str(args.output_dir.resolve()),
            "--packages",
            "pkgconf",
        ],
        check=True,
    )


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
        default=Path(tempfile.gettempdir()) / "arkpkg-pkgconf-source-cache",
    )
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
