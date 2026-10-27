#!/usr/bin/env python3
"""Build the FOX toolkit and Xfe suite in a disposable arkanaOS sysroot."""

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

FOX_NAME = "fox1.6-1.6.57"
FOX_VERSION = "1.6.57"
FOX_ARCHIVE = "fox1.6_1.6.57.orig.tar.gz"
FOX_URL = "https://deb.debian.org/debian/pool/main/f/fox1.6/fox1.6_1.6.57.orig.tar.gz"
FOX_SHA256 = "812066cd54f120eff5bacf61b5c5a1e9269bb2505e819c9d16610006a860d235"
XFE_NAME = "xfe"
XFE_VERSION = "2.1.12"
XFE_ARCHIVE = "xfe-2.1.12.tar.xz"
XFE_URL = "https://downloads.sourceforge.net/project/xfe/xfe/2.1.12/xfe-2.1.12.tar.xz"
XFE_SHA256 = "d40898da751637f026b75ab079f444380eaef86f9bc67f8989e8d9a0c554ebc2"
FOX_PACKAGES = ("fox-libs", "fox-devel", "fox-tools")


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


def source_archive(
    cache: Path,
    snapshots: Path,
    filename: str,
    url: str,
    expected_sha256: str,
    expected_root: str,
) -> Path:
    archive_path = source_snapshot(
        cache,
        snapshots,
        filename,
        url,
        expected_sha256,
        user_agent="arkpkg-repo-xfe-builder/1",
        timeout=120,
    )
    with tarfile.open(archive_path, "r:*") as source:
        roots = set()
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in {filename}: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
        if roots != {expected_root}:
            raise ValueError(f"unexpected source root in {filename}: {sorted(roots)}")
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


def run_build(
    sysroot: Path,
    source_archive_path: Path,
    source_root: str,
    configure: str,
    jobs: int,
    log: Path,
    work_hosts: list[Path],
) -> Path:
    work_rel = f"/tmp/arkpkg-{source_root}-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    privileged(["install", "-d", "-m", "755", str(work_host)])
    work_hosts.append(work_host)
    archive_in_root = work_host / source_archive_path.name
    privileged(["install", "-m", "644", str(source_archive_path), str(archive_in_root)])
    payload_rel = f"{work_rel}/payload"
    shell = "\n".join(
        (
            "set -eu",
            f"cd {work_rel}",
            f"/usr/bin/tar -xf {source_archive_path.name}",
            f"cd {source_root}",
            configure,
            f"/usr/bin/make -j{jobs}",
            f"/usr/bin/make DESTDIR={payload_rel} install",
            f"/usr/bin/chmod -R a+rX {payload_rel}",
        )
    )
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
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
        print("\n".join(tail), file=sys.stderr)
        raise
    return sysroot / payload_rel.lstrip("/")


def add_license(archive: Path, source_root: str, source_path: str, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as source:
        member = source.extractfile(f"{source_root}/{source_path}")
        if member is None:
            raise ValueError(f"{archive.name} is missing {source_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(member.read())


def build(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="arkpkg-xfe-sources-") as temporary:
        build_with_sources(args, Path(temporary))


def build_with_sources(args: argparse.Namespace, snapshots: Path) -> None:
    sysroot = args.sysroot.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    package_builder = repo_root / "scripts/build-arkana-packages.py"
    source_cache = args.source_cache
    fox_archive = source_archive(
        source_cache, snapshots, FOX_ARCHIVE, FOX_URL, FOX_SHA256, FOX_NAME
    )
    xfe_archive = source_archive(
        source_cache,
        snapshots,
        XFE_ARCHIVE,
        XFE_URL,
        XFE_SHA256,
        f"{XFE_NAME}-{XFE_VERSION}",
    )
    output_staging = args.output_staging.resolve()
    if output_staging.exists():
        shutil.rmtree(output_staging)
    output_staging.mkdir(parents=True)
    logs = Path(tempfile.mkdtemp(prefix="arkpkg-xfe-logs-"))
    work_hosts: list[Path] = []
    try:
        for binary in ("bash", "make", "pkg-config", "tar"):
            if not (sysroot / "usr/bin" / binary).exists():
                raise FileNotFoundError(f"target build root lacks /usr/bin/{binary}")

        fox_payload = run_build(
            sysroot,
            fox_archive,
            FOX_NAME,
            "./configure --prefix=/usr --libdir=/usr/lib --enable-release --with-xft --with-xim --with-opengl=no",
            args.jobs,
            logs / "fox.log",
            work_hosts,
        )
        for relative in (
            "usr/lib/libFOX-1.6.so.0",
            "usr/include/fox-1.6/fx.h",
            "usr/lib/pkgconfig/fox.pc",
            "usr/bin/fox-config",
        ):
            if not (fox_payload / relative).exists():
                raise FileNotFoundError(f"FOX build did not install {relative}")

        fox_stage = output_staging / "fox"
        copy_tree(fox_payload, fox_stage)
        for package in ("fox-libs", "fox-devel"):
            add_license(
                fox_archive,
                FOX_NAME,
                "LICENSE",
                fox_stage / f"usr/share/licenses/{package}/LICENSE",
            )
            add_license(
                fox_archive,
                FOX_NAME,
                "LICENSE_ADDENDUM",
                fox_stage / f"usr/share/licenses/{package}/LICENSE_ADDENDUM",
            )
        add_license(
            fox_archive,
            FOX_NAME,
            "adie/LICENSE",
            fox_stage / "usr/share/licenses/fox-tools/LICENSE",
        )
        subprocess.run(
            [
                sys.executable,
                str(package_builder),
                "--staging",
                str(fox_stage),
                "--arkpkg-build",
                str(args.arkpkg_build.resolve()),
                "--output-dir",
                str(args.output_dir.resolve()),
                "--packages",
                *FOX_PACKAGES,
            ],
            check=True,
        )

        installed = installed_packages(args.arkpkg.resolve(), sysroot)
        for package in FOX_PACKAGES[:2]:
            if package in installed:
                continue
            archive = next(args.output_dir.glob(f"{package}-{FOX_VERSION}-*.ark"))
            privileged(
                [
                    str(args.arkpkg.resolve()),
                    "--root",
                    str(sysroot),
                    "install",
                    str(archive.resolve()),
                    "-y",
                ]
            )
            installed.add(package)

        xfe_payload = run_build(
            sysroot,
            xfe_archive,
            f"{XFE_NAME}-{XFE_VERSION}",
            "./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var --enable-release --disable-sn",
            args.jobs,
            logs / "xfe.log",
            work_hosts,
        )
        for relative in ("usr/bin/xfe", "usr/bin/xfa", "usr/bin/xfi", "usr/bin/xfw"):
            if not (xfe_payload / relative).is_file():
                raise FileNotFoundError(f"Xfe build did not install {relative}")

        xfe_stage = output_staging / "xfe"
        copy_tree(xfe_payload, xfe_stage)
        add_license(
            xfe_archive,
            f"{XFE_NAME}-{XFE_VERSION}",
            "COPYING",
            xfe_stage / "usr/share/licenses/xfe/COPYING",
        )
        subprocess.run(
            [
                sys.executable,
                str(package_builder),
                "--staging",
                str(xfe_stage),
                "--arkpkg-build",
                str(args.arkpkg_build.resolve()),
                "--output-dir",
                str(args.output_dir.resolve()),
                "--packages",
                "xfe",
            ],
            check=True,
        )
    finally:
        for work_host in reversed(work_hosts):
            privileged(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                    str(work_host),
                ]
            )
        shutil.rmtree(logs, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--arkpkg", type=Path, required=True)
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=repo_root / "packages")
    parser.add_argument("--output-staging", type=Path, required=True)
    parser.add_argument(
        "--source-cache",
        type=Path,
        default=Path.home() / ".cache/arkpkg-xfe-sources",
        help="private source cache owned by the current user",
    )
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
