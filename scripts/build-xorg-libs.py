#!/usr/bin/env python3
"""Build selected X.Org libraries in an arkanaOS sysroot."""

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

from package_metadata import read_metadata_member

BASE_URL = "https://xorg.freedesktop.org/archive/individual/lib"
LIBRARIES = {
    "libXmu": (
        "1.3.1",
        "81a99e94c4501e81c427cbaa4a11748b584933e94b7a156830c3621256857bc4",
        ("libxmu", "libxmu-devel"),
        (
            "usr/lib/pkgconfig/xmu.pc",
            "usr/lib/pkgconfig/xmuu.pc",
            "usr/lib/libXmu.so.6*",
            "usr/lib/libXmuu.so.1*",
        ),
    ),
    "libXres": (
        "1.2.3",
        "d2de8f5401d6c86a8992791654547eb8def585dfdc0c08cc16e24ef6aeeb69dc",
        ("libxres", "libxres-devel"),
        (
            "usr/lib/pkgconfig/xres.pc",
            "usr/lib/libXRes.so.1*",
            "usr/lib/libXRes.so",
            "usr/include/X11/extensions/XRes.h",
        ),
    ),
    "libXv": (
        "1.0.13",
        "7d34910958e1c1f8d193d828fea1b7da192297280a35437af0692f003ba03755",
        ("libxv", "libxv-devel"),
        (
            "usr/lib/pkgconfig/xv.pc",
            "usr/lib/libXv.so.1*",
            "usr/lib/libXv.so",
            "usr/include/X11/extensions/Xvlib.h",
        ),
    ),
    "libXcursor": (
        "1.2.3",
        "fde9402dd4cfe79da71e2d96bb980afc5e6ff4f8a7d74c159e1966afb2b2c2c0",
        ("libxcursor",),
        (
            "usr/lib/pkgconfig/xcursor.pc",
            "usr/lib/libXcursor.so.1*",
            "usr/include/X11/Xcursor/Xcursor.h",
        ),
    ),
    "libXinerama": (
        "1.1.6",
        "d00fc1599c303dc5cbc122b8068bdc7405d6fcb19060f4597fc51bd3a8be51d7",
        ("libxinerama",),
        (
            "usr/lib/pkgconfig/xinerama.pc",
            "usr/lib/libXinerama.so.1*",
            "usr/include/X11/extensions/Xinerama.h",
        ),
    ),
    "libXfixes": (
        "6.0.2",
        "39f115d72d9c5f8111e4684164d3d68cc1fd21f9b27ff2401b08fddfc0f409ba",
        ("libxfixes",),
        (
            "usr/lib/pkgconfig/xfixes.pc",
            "usr/lib/libXfixes.so.3*",
            "usr/include/X11/extensions/Xfixes.h",
        ),
    ),
    "libXrandr": (
        "1.5.5",
        "72b922c2e765434e9e9f0960148070bd4504b288263e2868a4ccce1b7cf2767a",
        ("libxrandr",),
        (
            "usr/lib/pkgconfig/xrandr.pc",
            "usr/lib/libXrandr.so.2*",
            "usr/include/X11/extensions/Xrandr.h",
        ),
    ),
    "libXrender": (
        "0.9.13",
        "3fe88c609df6782e1e7af2e4de36b2563d70cd70ff7dcb1cda561ad64912bb64",
        ("libxrender",),
        (
            "usr/lib/pkgconfig/xrender.pc",
            "usr/lib/libXrender.so.1*",
            "usr/include/X11/extensions/Xrender.h",
        ),
    ),
    "libXcomposite": (
        "0.4.7",
        "8bdf310967f484503fa51714cf97bff0723d9b673e0eecbf92b3f97c060c8ccb",
        ("libxcomposite",),
        (
            "usr/lib/pkgconfig/xcomposite.pc",
            "usr/lib/libXcomposite.so.1*",
            "usr/include/X11/extensions/Xcomposite.h",
        ),
    ),
    "libXdamage": (
        "1.1.7",
        "127067f521d3ee467b97bcb145aeba1078e2454d448e8748eb984d5b397bde24",
        ("libxdamage",),
        (
            "usr/lib/pkgconfig/xdamage.pc",
            "usr/lib/libXdamage.so.1*",
            "usr/include/X11/extensions/Xdamage.h",
        ),
    ),
    "libXft": (
        "2.3.9",
        "60a25b78945ed6932635b3bb1899a517d31df7456e69867ffba27f89ff3976f5",
        ("libxft",),
        (
            "usr/lib/pkgconfig/xft.pc",
            "usr/lib/libXft.so.2*",
            "usr/include/X11/Xft/Xft.h",
        ),
    ),
}


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


def source_archive(name: str, version: str, digest: str, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive_path = cache / f"{name}-{version}.tar.xz"
    if not archive_path.is_file():
        request = urllib.request.Request(
            f"{BASE_URL}/{archive_path.name}",
            headers={"User-Agent": "arkpkg-repo-xorg-library-builder/1"},
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            archive_path.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if actual != digest:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {actual}")
    expected_root = f"{name}-{version}"
    with tarfile.open(archive_path, "r:xz") as source:
        roots = set()
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in {archive_path.name}: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
        if roots != {expected_root}:
            raise ValueError(
                f"unexpected source root in {archive_path.name}: {sorted(roots)}"
            )
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
    sysroot = args.sysroot.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    output_staging = args.output_staging.resolve()
    source_cache = args.source_cache.resolve()
    package_builder = repo_root / "scripts/build-arkana-packages.py"

    for binary in ("gcc", "make", "pkg-config", "tar"):
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
    logs = Path(tempfile.mkdtemp(prefix="arkpkg-xorg-library-logs-"))
    try:
        for upstream_name in args.libraries or list(LIBRARIES):
            version, digest, package_names, required_patterns = LIBRARIES[upstream_name]
            archive = source_archive(upstream_name, version, digest, source_cache)
            work_rel = f"/tmp/arkpkg-xorg-library-{uuid.uuid4().hex}"
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
                    f"cd {upstream_name}-{version}",
                    (
                        "LIBS=-lm ./configure --prefix=/usr --libdir=/usr/lib "
                        "--sysconfdir=/etc --localstatedir=/var"
                    ),
                    f"/usr/bin/make -j{args.jobs}",
                    f"/usr/bin/make DESTDIR={payload_rel} install",
                    f"/usr/bin/chmod -R a+rX {payload_rel}",
                )
            )
            log = logs / f"{upstream_name}.log"
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
            for pattern in required_patterns:
                if not list(payload.glob(pattern)):
                    raise FileNotFoundError(
                        f"{upstream_name} build did not install {pattern}"
                    )

            with tarfile.open(archive, "r:xz") as source:
                license_member = source.extractfile(f"{upstream_name}-{version}/COPYING")
                if license_member is None:
                    raise ValueError(f"{archive.name} has no COPYING file")
                license_data = license_member.read()
            for package in package_names:
                package_staging = output_staging / package
                package_staging.mkdir(parents=True)
                copy_tree(payload, package_staging)
                license_path = package_staging / f"usr/share/licenses/{package}/COPYING"
                license_path.parent.mkdir(parents=True, exist_ok=True)
                license_path.write_bytes(license_data)
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
                        package,
                    ],
                    check=True,
                )
            privileged(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                    str(work_host),
                ]
            )
    finally:
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
        default=Path(tempfile.gettempdir()) / "arkpkg-xorg-library-source-cache",
    )
    parser.add_argument(
        "--libraries",
        nargs="+",
        choices=tuple(LIBRARIES),
        help="build selected X.Org libraries (default: all libraries)",
    )
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
