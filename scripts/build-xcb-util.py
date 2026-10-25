#!/usr/bin/env python3
"""Build the XCB utility library against the arkanaOS sysroot."""

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

import lz4.frame
from source_cache import source_snapshot

NAME = "xcb-util"
VERSION = "0.4.1"
SHA256 = "5abe3bbbd8e54f0fa3ec945291b7e8fa8cfd3cccc43718f8758430f94126e512"
URL = f"https://xorg.freedesktop.org/archive/individual/lib/{NAME}-{VERSION}.tar.xz"


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def source_archive(cache: Path, snapshots: Path) -> Path:
    archive_path = source_snapshot(
        cache,
        snapshots,
        f"{NAME}-{VERSION}.tar.xz",
        URL,
        SHA256,
        user_agent="arkpkg-repo-xcb-util-builder/1",
        timeout=90,
    )
    roots = set()
    with tarfile.open(archive_path, "r:xz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in archive: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
    if roots != {f"{NAME}-{VERSION}"}:
        raise ValueError(f"unexpected source roots in archive: {sorted(roots)}")
    return archive_path


def package_name(archive_path: Path) -> str:
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
    raise ValueError(f"missing package name in {archive_path}")


def installed_packages(arkpkg: Path, sysroot: Path) -> set[str]:
    command = [str(arkpkg), "--root", str(sysroot), "list"]
    if os.geteuid() != 0:
        command.insert(0, "sudo")
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--arkpkg", type=Path, required=True)
    parser.add_argument("--xorg-util-macros-archive", type=Path, required=True)
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-xorg-sources")
    )
    parser.add_argument(
        "--output-staging", type=Path, default=Path("/tmp/arkpkg-xcb-util-staging")
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=2, choices=(1, 2))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="arkpkg-xcb-util-sources-") as temporary:
        build_with_sources(args, Path(temporary))


def build_with_sources(args: argparse.Namespace, snapshots: Path) -> None:
    sysroot = args.sysroot.resolve()
    arpkg = args.arkpkg.resolve()
    macros_archive = args.xorg_util_macros_archive.resolve()
    source = source_archive(args.source_cache.resolve(), snapshots)
    for binary in ("gcc", "make", "pkg-config", "tar", "strip"):
        if not (sysroot / "usr/bin" / binary).exists():
            raise FileNotFoundError(f"target build root lacks /usr/bin/{binary}")

    macros_name = package_name(macros_archive)
    installed = installed_packages(arpkg, sysroot)
    installed_macros_here = macros_name not in installed
    if installed_macros_here:
        privileged(
            [str(arpkg), "--root", str(sysroot), "install", str(macros_archive), "-y"]
        )

    work_rel = f"/tmp/arkpkg-xcb-util-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    output_staging = args.output_staging.resolve()
    if output_staging.exists():
        shutil.rmtree(output_staging)
    output_staging.mkdir(parents=True)
    logs = Path("/tmp") / f"arkpkg-xcb-util-{uuid.uuid4().hex}.log"
    try:
        privileged(["install", "-d", "-m", "755", str(work_host)])
        privileged(["install", "-m", "644", str(source), str(work_host / source.name)])
        payload_rel = f"{work_rel}/payload"
        shell = "\n".join(
            (
                "set -eu",
                f"cd {work_rel}",
                f"/usr/bin/tar -xf {source.name}",
                f"cd {NAME}-{VERSION}",
                "./configure --prefix=/usr --libdir=/usr/lib",
                f"/usr/bin/make -j{args.jobs}",
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
                log=logs,
            )
        except subprocess.CalledProcessError:
            print(
                "\n".join(
                    logs.read_text(encoding="utf-8", errors="replace").splitlines()[
                        -80:
                    ]
                ),
                file=sys.stderr,
            )
            raise

        payload_root = sysroot / payload_rel.lstrip("/")
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True,symlinks=True)",
                str(payload_root),
                str(output_staging),
            ]
        )
        privileged(["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(output_staging)])
        license_dir = output_staging / "usr/share/licenses/xcb-util"
        license_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source, "r:xz") as archive:
            stream = archive.extractfile(f"{NAME}-{VERSION}/COPYING")
            if stream is None:
                raise ValueError("xcb-util source archive has no COPYING file")
            (license_dir / "COPYING").write_bytes(stream.read())

        repo_root = Path(__file__).resolve().parents[1]
        command = [
            sys.executable,
            str(repo_root / "scripts/build-arkana-packages.py"),
            "--staging",
            str(output_staging),
            "--arkpkg-build",
            str(args.arkpkg_build.resolve()),
            "--packages",
            NAME,
        ]
        if args.output_dir is not None:
            command.extend(("--output-dir", str(args.output_dir.resolve())))
        subprocess.run(command, check=True)
    finally:
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)",
                str(work_host),
            ]
        )
        logs.unlink(missing_ok=True)
        if installed_macros_here:
            privileged(
                [str(arpkg), "--root", str(sysroot), "remove", macros_name, "-y"]
            )


if __name__ == "__main__":
    main()
