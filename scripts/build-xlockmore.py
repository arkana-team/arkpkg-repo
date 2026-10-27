#!/usr/bin/env python3
"""Build the xlockmore X11 locker against an isolated arkanaOS sysroot."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath

from package_metadata import open_dependency_archive, read_metadata_member

NAME = "xlockmore"
VERSION = "5.89"
SOURCE_ROOT = f"{NAME}-{VERSION}"
SOURCE_URL = f"https://sillycycle.com/xlock/{SOURCE_ROOT}.tar.xz"
SOURCE_SHA256 = "525597aca0a06486798be925b6f04146bb595643248690f45807b6fa3a0ded5d"
SOURCE_BYTES = 2_090_056


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def privileged_text(command: list[str]) -> str:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    return subprocess.check_output(full, text=True)


def package_name(archive_path: Path) -> str:
    with open_dependency_archive(archive_path) as archive:
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
    raise ValueError(f"missing package name in {archive_path}")


def source_archive(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive_path = cache / f"{SOURCE_ROOT}.tar.xz"
    if not archive_path.is_file():
        request = urllib.request.Request(
            SOURCE_URL, headers={"User-Agent": "arkpkg-repo-xlockmore-builder/1"}
        )
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            archive_path.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)

    if archive_path.stat().st_size != SOURCE_BYTES:
        raise ValueError(
            f"unexpected {archive_path.name} size: {archive_path.stat().st_size}"
        )
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {digest}")

    roots: set[str] = set()
    with tarfile.open(archive_path, "r:xz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in archive: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
            if member.issym() or member.islnk():
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or ".." in target.parts:
                    raise ValueError(f"unsafe link in source archive: {member.name!r}")
    if roots != {SOURCE_ROOT}:
        raise ValueError(f"unexpected source roots in archive: {sorted(roots)}")
    return archive_path


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
        "--source-cache", type=Path, default=Path.home() / ".cache/arkpkg-xlockmore-sources"
    )
    parser.add_argument("--output-staging", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    sysroot = args.sysroot.resolve()
    arpkg = args.arkpkg.resolve()
    if not sysroot.is_dir():
        raise FileNotFoundError(f"build sysroot does not exist: {sysroot}")
    for binary in ("gcc", "make", "tar", "strip", "bash"):
        if not (sysroot / "usr/bin" / binary).exists():
            raise FileNotFoundError(f"build sysroot lacks /usr/bin/{binary}")
    if not (sysroot / "etc/pam.d/system-auth").is_file():
        raise FileNotFoundError("build sysroot lacks /etc/pam.d/system-auth")

    source = source_archive(args.source_cache.resolve())
    staging = args.output_staging
    if staging is None:
        staging = (
            Path.home()
            / ".cache"
            / f"arkpkg-xlockmore-staging-{uuid.uuid4().hex[:12]}"
        )
    staging = staging.resolve()
    if staging.exists():
        raise FileExistsError(f"refusing to overwrite staging directory: {staging}")
    staging.mkdir(parents=True)

    build_id = uuid.uuid4().hex[:12]
    work_rel = f"/tmp/arkpkg-xlockmore-{build_id}"
    work_host = sysroot / work_rel.lstrip("/")
    log = Path.home() / ".cache" / f"arkpkg-xlockmore-build-{build_id}.log"
    target_devnull = sysroot / "dev/null"

    installed = installed_packages(arpkg, sysroot)
    for dependency_archive in args.build_dependency_archive:
        dependency_archive = dependency_archive.resolve()
        dependency = package_name(dependency_archive)
        if dependency in installed:
            continue
        privileged(
            [
                str(arpkg),
                "--root",
                str(sysroot),
                "install",
                str(dependency_archive),
                "-y",
            ]
        )
        installed.add(dependency)

    required = {"xorg-devel", "libxmu-devel", "pam-devel"}
    missing = sorted(required - installed)
    if missing:
        raise RuntimeError(
            "build sysroot is missing packages: "
            + ", ".join(missing)
            + "; pass their .ark files with --build-dependency-archive"
        )

    privileged(["install", "-d", "-m", "755", str(work_host)])
    privileged(["install", "-m", "644", str(source), str(work_host / source.name)])
    privileged(["install", "-d", "-m", "755", str(sysroot / "usr/share/X11/app-defaults")])
    payload_rel = f"{work_rel}/payload"
    configure = (
        "./configure --prefix=/usr --exec-prefix=/usr --bindir=/usr/bin "
        "--libdir=/usr/lib --mandir=/usr/share/man "
        "--enable-appdefaultdir=/usr/share/X11/app-defaults "
        "--enable-pam --disable-setuid --disable-allow-root --disable-bomb "
        "--without-nas --without-editres --without-motif "
        "--without-opengl --without-freetype --without-gtk --without-gtk2"
    )
    install_xlock = (
        f"/usr/bin/make -C modes DESTDIR={payload_rel} "
        "install-program install-man install-ad"
    )
    install_xmlock = (
        f"/usr/bin/make -C xmlock DESTDIR={payload_rel} "
        "install-program install-ad"
    )
    shell = "\n".join(
        (
            "set -eu",
            f"cd {work_rel}",
            f"/usr/bin/tar -xf {source.name}",
            f"cd {SOURCE_ROOT}",
            configure,
            "/usr/bin/make -j1",
            install_xlock,
            install_xmlock,
            f"/usr/bin/chmod -R a+rX {payload_rel}",
            f"/usr/bin/chmod 0755 {payload_rel}/usr/bin/xlock",
            f"/usr/bin/chmod 0755 {payload_rel}/usr/bin/xmlock",
            f"/usr/bin/strip --strip-unneeded {payload_rel}/usr/bin/xlock",
            f"/usr/bin/strip --strip-unneeded {payload_rel}/usr/bin/xmlock",
        )
    )

    devnull_mounted = False
    try:
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
                log=log,
            )
        except subprocess.CalledProcessError:
            print(
                "\n".join(
                    log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
                ),
                file=sys.stderr,
            )
            raise
    finally:
        if devnull_mounted:
            privileged(["umount", str(target_devnull)])

    payload = sysroot / payload_rel.lstrip("/")
    binary = payload / "usr/bin/xlock"
    xmlock = payload / "usr/bin/xmlock"
    config = work_host / SOURCE_ROOT / "config.h"
    if not binary.is_file() or not xmlock.is_file():
        raise FileNotFoundError("xlockmore did not install xlock and xmlock")
    config_text = privileged_text(["cat", str(config)])
    if "#define USE_PAM 1" not in config_text:
        raise ValueError("xlockmore was built without PAM support")
    if "#define ALWAYS_ALLOW_ROOT" in config_text:
        raise ValueError("xlockmore build unexpectedly permits root to unlock")
    for executable in (binary, xmlock):
        if executable.stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise ValueError(f"refusing to package privileged executable: {executable}")

    privileged(
        [
            "/usr/bin/python3",
            "-c",
            "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True,symlinks=True)",
            str(payload),
            str(staging),
        ]
    )
    privileged(["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(staging)])

    pam_service = staging / "etc/pam.d/xlock"
    pam_service.parent.mkdir(parents=True, exist_ok=True)
    pam_service.parent.chmod(0o755)
    pam_service.write_text(
        "auth include system-auth\n"
        "account include system-auth\n",
        encoding="utf-8",
    )
    pam_service.chmod(0o644)
    desktop = staging / "usr/share/applications/xlockmore.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.chmod(0o755)
    desktop.write_text(
        "[Desktop Entry]\n"
        "Name=xlockmore\n"
        "Comment=lock the X11 session with classic animated patterns\n"
        "Exec=xlock -mode random\n"
        "Terminal=false\n"
        "Type=Application\n"
        "Categories=Utility;Security;Screensaver;\n",
        encoding="utf-8",
    )
    desktop.chmod(0o644)

    license_dir = staging / "usr/share/licenses/xlockmore"
    license_dir.mkdir(parents=True, exist_ok=True)
    license_dir.chmod(0o755)
    docs = staging / "usr/share/doc/xlockmore"
    docs.mkdir(parents=True, exist_ok=True)
    docs.chmod(0o755)
    privileged(
        [
            "install",
            "-m",
            "644",
            str(work_host / SOURCE_ROOT / "xlockmore.spec"),
            str(license_dir / "xlockmore.spec"),
        ]
    )
    privileged(
        [
            "install",
            "-m",
            "644",
            str(work_host / SOURCE_ROOT / "README"),
            str(docs / "README"),
        ]
    )

    repository = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(repository / "scripts/build-arkana-packages.py"),
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
    print(f"Build log preserved at {log}")
    print(f"Build workspace preserved at {work_host}")
    print(f"Package staging preserved at {staging}")


if __name__ == "__main__":
    main()
