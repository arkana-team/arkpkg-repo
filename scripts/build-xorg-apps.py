#!/usr/bin/env python3
"""Build a small classic X11 application set in an arkanaOS sysroot.

Requires an arkanaOS build root with the C toolchain and Xorg runtime. If its
X11 development headers are absent, pass the xorg-devel archive and host-side
arkpkg executable; the helper installs that package temporarily and removes it
after the build. Package archives are then written using build-arkana-packages.py.
"""

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

import lz4.frame

BASE_URL = "https://xorg.freedesktop.org/archive/individual/app"
APPS = {
    "xcalc": (
        "1.1.3",
        "86e157b6174678675a4a912995dccd0ff4e2ce32b01bdd6f91bfe93333324980",
        "XCalc",
        "Scientific calculator",
    ),
    "xclock": (
        "1.2.1",
        "e326cac20f7ad2af5412b9c4b70b1c3d392e08cd3cc6246e0a77b7c3935a4851",
        "XClock",
        "Analog and digital clock",
    ),
    "xedit": (
        "1.2.5",
        "733460cbf20a9d88e2a482ff1b288f51a97150cff807c7ffb98d2430546c8867",
        "Xedit",
        "Athena text editor",
    ),
    "xeyes": (
        "1.3.1",
        "5608d76b7b1aac5ed7f22f1b6b5ad74ef98c8693220f32b4b87dccee4a956eaa",
        "XEyes",
        "Pointer-following X11 demo",
    ),
    "xload": (
        "1.2.2",
        "112a75e481783b52a20d37367f57983d60919f78059f0e107458dde236804131",
        "XLoad",
        "System load monitor",
    ),
    "xlogo": (
        "1.0.7",
        "8a3f67b20407a53286a7de1a04dbda12d272234d59ef35e68ecfaf7633d83624",
        "XLogo",
        "X.Org logo demo",
    ),
    "xmag": (
        "1.0.8",
        "326d3c583d795bb53ac609d144e7f7fb1499baba7eaec14b8e6cd232ea069532",
        "XMag",
        "Screen magnifier",
    ),
    "xman": (
        "1.2.0",
        "f18db80bd72a0c27cf38b2a7b75485ee48cd22aab10f2ff58de54d83e268b406",
        "Xman",
        "X11 manual page browser",
    ),
    "xmessage": (
        "1.0.7",
        "703fccb7a0b772d61d7e603c189b9739866aa97ba985c727275420f829a30356",
        "Xmessage",
        "X11 message dialog",
    ),
    "oclock": (
        "1.0.6",
        "3ee85e5e2d211eca7bdb8b2fe0e3baf31998829af48198b5fa9bbee2aa0d24be",
        "Clock",
        "Round X11 clock",
    ),
    "xbiff": (
        "1.0.6",
        "a85f34361c317ca216f42da9ee069a68b4beffd8dc0c973c9c339c8dd1d7d62a",
        "",
        "X11 mail indicator",
    ),
    "xclipboard": (
        "1.1.6",
        "d7c7a74ed0a4c8983b7dd47301a91c3da48e1027e9338705cad5be8d15c0d1f1",
        "XClipboard",
        "X11 clipboard manager",
    ),
    "xconsole": (
        "1.1.1",
        "d412abbea2423f2930443c43db1c83d7d0cf2b677419b86ed54aa5d47f072322",
        "XConsole",
        "X11 system console",
    ),
    "xditview": (
        "1.0.7",
        "039e2d447fa409d4bb25c4e87cf09b4d4b3eff2d3af5aee8e7e52165a3259fd7",
        "Xditview",
        "Ditroff document viewer",
    ),
    "xev": (
        "1.2.7",
        "95167895924de58e34b1013b2b0c8476e90d0888c6c39e7ae9bc35e3a19dba04",
        "",
        "X11 event viewer",
    ),
    "xfontsel": (
        "1.1.2",
        "78ff018757c7b22137cf5d3526ef5e9388bc0cdead1f00daf2c898e2e274553f",
        "XFontSel",
        "X11 font selector",
    ),
    "xgc": (
        "1.0.7",
        "d858258c05eaac2d1f48bb44820dc2dce0a68601a14fff97853c72b48d1b7d08",
        "Xgc",
        "X11 graphics demo",
    ),
    "xkill": (
        "1.0.7",
        "5ff264bc4eebc0459255a92036dc8dc84b6d48caef8509f88a783beaadea750b",
        "",
        "X11 window selector and terminator",
    ),
    "xmore": (
        "1.0.4",
        "7eb560dbc1de4e43c64fe491ad73907a29d734cca82a9ad82c7d3feb9cdb0a9a",
        "XMore",
        "X11 text pager",
    ),
    "xpr": (
        "1.2.1",
        "0a5b947cd5e3990c9fcd04fdf92603241de4a55a29b89d4e8a0e473320b778ba",
        "",
        "",
    ),
    "xsm": (
        "1.0.6",
        "b74cc774c6060c37592f68a90dbd31b0f2a62fb14e562769434f79be284a63ce",
        "XSm",
        "X11 session manager",
    ),
    "xwd": (
        "1.0.10",
        "4909eacc26c4cd5a9afcd1468aec26e7c646544639031bdad4f71173aa82f3c3",
        "",
        "",
    ),
    "xwud": (
        "1.0.8",
        "2e282648c34018fd870052b8d814ea4d35ad9fdb9bcfc8bdc5a06822c0f86e99",
        "",
        "",
    ),
    "xwininfo": (
        "1.1.7",
        "bee14d594cc86cc59aae1015c1b452a71bf60c304131e2716ca1cf0df733b4ac",
        "",
        "",
    ),
    "iceauth": (
        "1.0.11",
        "9d633cf0d4d1d98e3ef02d18660372958b60a67016e8a72cd04093a8d8f8d4e9",
        "",
        "",
    ),
    "xfd": (
        "1.1.6",
        "8f78ffcda2d58c66226daa957ad045839053ecdd44448124c20392ed9ff2f33b",
        "Xfd",
        "X11 font viewer",
    ),
    "xauth": (
        "1.1.5",
        "a4000e2f441facebf569026bedecc23ba262cc6927be52070abe0002625cfbe0",
        "",
        "",
    ),
    "ico": (
        "1.0.6",
        "dc59589044d71e3ef4dacf5a62a7b0f69b543386d2a12fb8b5558caee5b1e22f",
        "Ico",
        "X11 animation demo",
    ),
    "xbacklight": (
        "1.2.4",
        "d4c30b0e6f18c82f387585a737ee3b72d468c927892b08a898c41bc12248e8ee",
        "",
        "",
    ),
    "xgamma": (
        "1.0.8",
        "98f9f69e53a11c354a6637ea5c3d7699ceb5c5b1f8ad6f0a14d9931e5a10d079",
        "",
        "",
    ),
    "xhost": (
        "1.0.10",
        "a8afd70059479c712948b895e41c35a4a8bfcede3ba2d5a4b855c88bbb725be1",
        "",
        "",
    ),
    "xinput": (
        "1.6.4",
        "ad04d00d656884d133110eeddc34e9c69e626ebebbbab04dc95791c2907057c8",
        "",
        "",
    ),
    "xmodmap": (
        "1.0.12",
        "fc54b9b5bbf2ae58ba8f9d42bd051c41c7438377400c42c17d7496d19e1bb3ce",
        "",
        "",
    ),
    "xprop": (
        "1.2.8",
        "d689e2adb7ef7b439f6469b51cda8a7daefc83243854c2a3b8f84d0f029d67ee",
        "",
        "",
    ),
    "xrandr": (
        "1.5.4",
        "2cafccb2aaf2491a4068676117a0d4f90ab307724b96fffc54cd1da953779400",
        "",
        "",
    ),
    "xrefresh": (
        "1.1.1",
        "e947048130f6300e8be86573af8f225b486884fac14fcfe01bd913b41fa73bd3",
        "",
        "",
    ),
    "xset": (
        "1.2.6",
        "623837349ea887bc003f01ee2e4b6b8ddd9c2774f632c6d70eead1b56306b695",
        "",
        "",
    ),
    "xsetroot": (
        "1.1.4",
        "1315a3f7e9abe06357363b93461e272601f67225ce0bc075c430cce35073362b",
        "",
        "",
    ),
    "xinit": (
        "1.4.4",
        "40a47c7a164c7f981ce3787b4b37f7e411fb43231dcde543d70094075dacfef9",
        "",
        "",
    ),
    "xdpyinfo": (
        "1.4.0",
        "dc1de6e6e091ed46c4837b0ae9811e8182f7be0d2af638cab3e530ff081a48b6",
        "",
        "",
    ),
    "xlsatoms": (
        "1.1.5",
        "9ac9400328fbeffe15c9d94a90dedb90d09dcaa9d51482efabc55b33a736e317",
        "",
        "",
    ),
    "xlsclients": (
        "1.1.6",
        "909810a3fdbd01d84747907f2c0cc32ee732e77afc88cb310abb9d155b2a0807",
        "",
        "",
    ),
    "xscope": (
        "1.4.5",
        "5f125d4c94e19e4be48edc43691e46df0b82f0b6ead95453dc7ac775a0d70066",
        "",
        "",
    ),
    "xkbutils": (
        "1.0.7",
        "5f37be0ee31ada114415780c562638a1b4f03b385e56449927bf0d5665787a26",
        "",
        "",
    ),
    "appres": (
        "1.0.7",
        "1114b189239fd87a8d1db433edcb4486346d29912132b91eaeee5667f13b819f",
        "",
        "",
    ),
    "listres": (
        "1.0.7",
        "d7bdc7354a53f3786487399b6d5ea8169fac9ddac2c751c530383c777d1af139",
        "",
        "",
    ),
    "sessreg": (
        "1.1.4",
        "4e85b52f4f65a93449753bfb00ceb5d77f3317e24d07dc23147f2f116c785350",
        "",
        "",
    ),
    "setxkbmap": (
        "1.3.5",
        "360193cecc93f906d8383a8fb5c1f3a7eed35e6ced0e118a64ee56ae13c88cac",
        "",
        "",
    ),
    "transset": (
        "1.0.4",
        "81a9ab74af137733aa8cb6a37f8292948526fe7ef06b859fc0ff272c437c0b38",
        "",
        "",
    ),
    "xcmsdb": (
        "1.0.7",
        "5ec4068e488187b05ea92ee1362c96b78a90f19ccc7a1844c5921d70626bbc38",
        "",
        "",
    ),
    "xcompmgr": (
        "1.1.10",
        "7824f709c4f22432eaea7542ec93384e5dd48f6fcb85c12ff82d721423b0b98f",
        "",
        "",
    ),
    "xcursorgen": (
        "1.0.9",
        "0cc9e156ac84ca16ea902710af35e0faffa51d13797071e3b4b6cc7cbd493bbc",
        "",
        "",
    ),
    "xkbcomp": (
        "1.5.0",
        "2ac31f26600776db6d9cd79b3fcd272263faebac7eb85fb2f33c7141b8486060",
        "",
        "",
    ),
    "xkbevd": (
        "1.1.6",
        "d9b401f84cf8568342ff63b1ffc6aecd3877579256d89fe6408645e2741b073e",
        "",
        "",
    ),
    "xkbprint": (
        "1.0.8",
        "6dbb3dc79b1d1e17045a65f86e7581e149c75ee5aacae69a6cf685acf78fc31b",
        "",
        "",
    ),
    "xlsfonts": (
        "1.0.9",
        "a6a2e291801458d92510263b175d16f98024dc3816ebddbde79b5009f6c3b065",
        "",
        "",
    ),
    "xrdb": (
        "1.2.3",
        "c88f560243278c896ce4fc92ae5a45a2b505a316ffa427fe55b02e5d5914c4e4",
        "",
        "",
    ),
    "xstdcmap": (
        "1.0.6",
        "062a15722769799524eee542fc82ae778f5ac180b618295d913f34962b134ffe",
        "",
        "",
    ),
    "xvidtune": (
        "1.0.4",
        "0d4eecd54e440cc11f1bdaaa23180fcf890f003444343f533f639086b05b2cc5",
        "",
        "",
    ),
    "bitmap": (
        "1.1.2",
        "5c6d2637244c575a9cc65a6b1ee15ced480ef0184821aceeab29650daf323c12",
        "Bitmap",
        "X11 bitmap editor",
    ),
    "fonttosfnt": (
        "1.2.5",
        "2e251e66e8753c08aa280b26fd4a10d0d88e7d9bba5c8aac3d3fb36705e5918e",
        "",
        "",
    ),
    "mkfontscale": (
        "1.2.4",
        "a01492a17a9b6c0ee3f92ee578850e305315b9f298da5f006a1cd4b51db01a5e",
        "",
        "",
    ),
    "viewres": (
        "1.0.8",
        "4b221c2b103390b166633612b9abf8036cbbe90605dbd8a37cf2a377fa2b6cd2",
        "Viewres",
        "X11 resource hierarchy viewer",
    ),
}
APP_BINARIES = {
    "xkbutils": ("xkbbell", "xkbvleds", "xkbwatch"),
    "bitmap": ("bitmap", "bmtoa", "atobm"),
}
ARCHIVE_SUFFIXES = {"ico": ".tar.gz"}


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


def package_name(archive_path: Path) -> str:
    with (
        lz4.frame.open(archive_path, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r|") as archive,
    ):
        for member in archive:
            if member.name == "ARKPKG" and member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    break
                for line in stream.read().decode("utf-8").splitlines():
                    key, separator, value = line.partition(":")
                    if separator and key.strip() == "Name":
                        return value.strip()
    raise ValueError(f"package archive has no Name field: {archive_path}")


def installed_packages(arkpkg: Path, sysroot: Path) -> set[str]:
    command = [str(arkpkg), "--root", str(sysroot), "list"]
    full = command if os.geteuid() == 0 else ["sudo", *command]
    result = subprocess.run(full, capture_output=True, text=True, check=True)
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def source_archive(name: str, version: str, expected_sha256: str, cache: Path) -> Path:
    archive = cache / f"{name}-{version}{ARCHIVE_SUFFIXES.get(name, '.tar.xz')}"
    if not archive.is_file():
        request = urllib.request.Request(
            f"{BASE_URL}/{archive.name}",
            headers={"User-Agent": "arkpkg-repo-xorg-builder/1"},
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            archive.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {archive.name}: {digest}")
    with tarfile.open(archive, "r:*") as source:
        roots = set()
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    f"unsafe source path in {archive.name}: {member.name!r}"
                )
            if path.parts:
                roots.add(path.parts[0])
        expected_root = f"{name}-{version}"
        if roots != {expected_root}:
            raise ValueError(
                f"unexpected source root in {archive.name}: {sorted(roots)}"
            )
    return archive


def desktop_entry(name: str, label: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={label}\n"
        f"Exec=/usr/bin/{name}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
    )


def build(args: argparse.Namespace) -> None:
    sysroot = args.sysroot.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    source_cache = args.source_cache.resolve()
    output_staging = args.output_staging.resolve()
    source_cache.mkdir(parents=True, exist_ok=True)
    output_staging.mkdir(parents=True, exist_ok=True)
    package_names = args.packages or list(APPS)

    for binary in ("gcc", "make", "pkg-config", "tar", "strip"):
        if not (sysroot / "usr/bin" / binary).exists():
            raise FileNotFoundError(f"target build root lacks /usr/bin/{binary}")

    xaw_header = sysroot / "usr/include/X11/Xaw/Command.h"
    installed_xorg_devel_here = False
    installed_build_dependencies_here: list[str] = []
    if args.build_dependency_archive:
        if args.arkpkg is None:
            raise ValueError("--arkpkg is required with --build-dependency-archive")
        installed = installed_packages(args.arkpkg.resolve(), sysroot)
        for archive in args.build_dependency_archive:
            archive = archive.resolve()
            name = package_name(archive)
            if name in installed:
                continue
            privileged(
                [
                    str(args.arkpkg.resolve()),
                    "--root",
                    str(sysroot),
                    "install",
                    str(archive),
                    "-y",
                ]
            )
            installed_build_dependencies_here.append(name)
            installed.add(name)

    if not privileged_file_test(xaw_header):
        if args.arkpkg is None or args.xorg_devel_archive is None:
            raise FileNotFoundError(
                "X11 development headers are absent; pass --arkpkg and --xorg-devel-archive"
            )
        privileged(
            [
                str(args.arkpkg.resolve()),
                "--root",
                str(sysroot),
                "install",
                str(args.xorg_devel_archive.resolve()),
                "-y",
            ]
        )
        installed_xorg_devel_here = True

    work_rel = f"/tmp/arkpkg-xorg-apps-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    privileged(["install", "-d", "-m", "755", str(work_host)])
    pkgconfig_host = work_host / "pkgconfig"
    pkgconfig_rel = f"{work_rel}/pkgconfig"
    if args.build_pkg_config_file:
        privileged(["install", "-d", "-m", "755", str(pkgconfig_host)])
        for path in args.build_pkg_config_file:
            source_pc = path.resolve()
            if source_pc.suffix != ".pc" or not source_pc.is_file():
                raise ValueError(f"not a pkg-config file: {source_pc}")
            privileged(
                [
                    "install",
                    "-m",
                    "644",
                    str(source_pc),
                    str(pkgconfig_host / source_pc.name),
                ]
            )
    logs = Path(tempfile.mkdtemp(prefix="arkpkg-xorg-build-logs-"))
    try:
        for name in package_names:
            version, digest, _app_class, label = APPS[name]
            print(f"Building {name}-{version}", flush=True)
            archive = source_archive(name, version, digest, source_cache)
            archive_in_root = work_host / archive.name
            privileged(["install", "-m", "644", str(archive), str(archive_in_root)])
            payload_rel = f"{work_rel}/payload/{name}"
            configure = "LIBS=-lm ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var"
            if name == "xclock":
                # Keep the classic bitmap-font build and avoid an unnecessary
                # Xft development dependency in the optional package.
                configure += " --with-xft=no"
            shell = "\n".join(
                (
                    "set -eu",
                    f"cd {work_rel}",
                    f"/usr/bin/tar -xf {archive.name}",
                    f"cd {name}-{version}",
                    configure,
                    f"/usr/bin/make -j{args.jobs}",
                    f"/usr/bin/make DESTDIR={payload_rel} install",
                    f"/usr/bin/chmod -R a+rX {payload_rel}",
                    *(
                        f"/usr/bin/strip --strip-unneeded {payload_rel}/usr/bin/{binary}"
                        for binary in APP_BINARIES.get(name, (name,))
                    ),
                )
            )
            log = logs / f"{name}.log"
            try:
                privileged(
                    [
                        "chroot",
                        str(sysroot),
                        "/usr/bin/env",
                        "LC_ALL=C",
                        "LANG=C",
                        "PATH=/usr/bin:/bin",
                        f"PKG_CONFIG_PATH={pkgconfig_rel}:/usr/lib/pkgconfig:/usr/share/pkgconfig",
                        "/usr/bin/bash",
                        "-c",
                        shell,
                    ],
                    log=log,
                )
            except subprocess.CalledProcessError:
                tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[
                    -80:
                ]
                print("\n".join(tail), file=sys.stderr)
                raise

            installed_root = sysroot / payload_rel.lstrip("/")
            for binary in APP_BINARIES.get(name, (name,)):
                binary_path = installed_root / "usr/bin" / binary
                if not privileged_file_test(binary_path, "-x"):
                    raise FileNotFoundError(f"build did not install /usr/bin/{binary}")
            privileged(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True,symlinks=True)",
                    str(installed_root),
                    str(output_staging),
                ]
            )
            privileged(
                ["chown", "-R", f"{os.getuid()}:{os.getgid()}", str(output_staging)]
            )
            if label:
                desktop_path = (
                    output_staging / "usr/share/applications" / f"{name}.desktop"
                )
                desktop_path.parent.mkdir(parents=True, exist_ok=True)
                desktop_path.write_text(desktop_entry(name, label), encoding="utf-8")

            for child in (
                sysroot / work_rel.lstrip("/") / f"{name}-{version}",
                installed_root,
            ):
                privileged(
                    [
                        "/usr/bin/python3",
                        "-c",
                        "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)",
                        str(child),
                    ]
                )
            privileged(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).unlink(missing_ok=True)",
                    str(archive_in_root),
                ]
            )

        command = [
            sys.executable,
            str(repo_root / "scripts/build-arkana-packages.py"),
            "--staging",
            str(output_staging),
            "--arkpkg-build",
            str(args.arkpkg_build.resolve()),
            "--output-dir",
            str(args.output_dir.resolve()),
            "--packages",
            *package_names,
        ]
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
        if installed_xorg_devel_here and args.arkpkg is not None:
            privileged(
                [
                    str(args.arkpkg.resolve()),
                    "--root",
                    str(sysroot),
                    "remove",
                    "xorg-devel",
                    "-y",
                ]
            )
        if args.arkpkg is not None:
            for name in reversed(installed_build_dependencies_here):
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
    parser.add_argument("--xorg-devel-archive", type=Path)
    parser.add_argument(
        "--build-dependency-archive",
        type=Path,
        action="append",
        default=[],
        help="temporarily install a build dependency from an arkpkg archive",
    )
    parser.add_argument(
        "--build-pkg-config-file",
        type=Path,
        action="append",
        default=[],
        help="temporarily add a .pc file to the target build environment",
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=repo_root / "packages")
    parser.add_argument("--output-staging", type=Path, required=True)
    parser.add_argument(
        "--source-cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "arkpkg-xorg-source-cache",
    )
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument(
        "--packages",
        nargs="+",
        choices=tuple(APPS),
        help="Build only the selected Xorg applications (default: all)",
    )
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
