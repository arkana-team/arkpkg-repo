#!/usr/bin/env python3
"""Build the XOSView resource monitor against the arkanaOS sysroot."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath

import lz4.frame

NAME = "xosview"
VERSION = "2.3.5"
SOURCE_ROOT = "xosview2-2.3.5"
SHA256 = "690ba023dacbd0dd6a2f152b52fc0fec6936cf7bfe3c2ffe04d56520daaf4fc8"
URL = f"https://downloads.sourceforge.net/project/xosview/{SOURCE_ROOT}.tar.gz"


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def source_archive(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive_path = cache / f"{SOURCE_ROOT}.tar.gz"
    if not archive_path.is_file():
        request = urllib.request.Request(
            URL, headers={"User-Agent": "arkpkg-repo-xosview-builder/1"}
        )
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            archive_path.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != SHA256:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {digest}")
    roots = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in archive: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
    if roots != {SOURCE_ROOT}:
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
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument(
        "--build-dependency-archive", type=Path, action="append", default=[]
    )
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-xosview-sources")
    )
    parser.add_argument(
        "--output-staging", type=Path, default=Path("/tmp/arkpkg-xosview-staging")
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--jobs", type=int, default=2, choices=(1, 2))
    args = parser.parse_args()

    sysroot = args.sysroot.resolve()
    arpkg = args.arkpkg.resolve()
    source = source_archive(args.source_cache.resolve())
    for binary in ("gcc", "g++", "make", "pkg-config", "tar", "strip"):
        if not (sysroot / "usr/bin" / binary).exists():
            raise FileNotFoundError(f"target build root lacks /usr/bin/{binary}")

    output_staging = args.output_staging.resolve()
    if output_staging.exists():
        shutil.rmtree(output_staging)
    output_staging.mkdir(parents=True)
    logs = Path("/tmp") / f"arkpkg-xosview-{uuid.uuid4().hex}.log"
    work_rel = f"/tmp/arkpkg-xosview-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    temporary_dependencies: list[str] = []
    target_devnull = sysroot / "dev/null"
    devnull_mounted = False
    try:
        installed = installed_packages(arpkg, sysroot)
        for archive in args.build_dependency_archive:
            dependency = package_name(archive.resolve())
            if dependency in installed:
                continue
            privileged(
                [
                    str(arpkg),
                    "--root",
                    str(sysroot),
                    "install",
                    str(archive.resolve()),
                    "-y",
                ]
            )
            installed.add(dependency)
            temporary_dependencies.append(dependency)

        privileged(["install", "-d", "-m", "755", str(work_host)])
        privileged(["install", "-m", "644", str(source), str(work_host / source.name)])
        payload_rel = f"{work_rel}/payload"
        shell = "\n".join(
            (
                "set -eu",
                f"cd {work_rel}",
                f"/usr/bin/tar -xf {source.name}",
                f"cd {SOURCE_ROOT}",
                "./configure --prefix=/usr --with-app-defaults=/usr/share/X11/app-defaults",
                f"/usr/bin/make -j{args.jobs}",
                f"/usr/bin/make DESTDIR={payload_rel} install",
                f"/usr/bin/chmod -R a+rX {payload_rel}",
                f"/usr/bin/strip --strip-unneeded {payload_rel}/usr/bin/xosview2",
            )
        )
        privileged(["mount", "--bind", "/dev/null", str(target_devnull)])
        devnull_mounted = True
        try:
            privileged(
                [
                    "chroot",
                    str(sysroot),
                    "/usr/bin/env",
                    "LC_ALL=C",
                    "LANG=C",
                    "PATH=/usr/bin:/bin",
                    "SHELL=/usr/bin/bash",
                    "CONFIG_SHELL=/usr/bin/bash",
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
        if not (payload_root / "usr/bin/xosview2").is_file():
            raise FileNotFoundError("xosview build did not install /usr/bin/xosview2")
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

        binary_alias = output_staging / "usr/bin/xosview"
        binary_alias.symlink_to("xosview2")
        desktop = output_staging / "usr/share/applications/xosview.desktop"
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(
            "[Desktop Entry]\n"
            "Name=XOSView\n"
            "Comment=Monitor system resource usage\n"
            "Exec=xosview\n"
            "Terminal=false\n"
            "Type=Application\n"
            "Categories=System;Monitor;\n",
            encoding="utf-8",
        )
        license_dir = output_staging / "usr/share/licenses/xosview"
        license_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(source, "r:gz") as archive:
            stream = archive.extractfile(f"{SOURCE_ROOT}/COPYING.GPL")
            if stream is None:
                raise ValueError("xosview source archive has no COPYING.GPL license")
            (license_dir / "COPYING.GPL").write_bytes(stream.read())

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
        if devnull_mounted:
            privileged(["umount", str(target_devnull)])
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                str(work_host),
            ]
        )
        logs.unlink(missing_ok=True)
        for dependency in reversed(temporary_dependencies):
            privileged([str(arpkg), "--root", str(sysroot), "remove", dependency, "-y"])


if __name__ == "__main__":
    main()
