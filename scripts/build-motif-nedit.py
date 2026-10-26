#!/usr/bin/env python3
"""Build optional Motif, NEdit, and XNEdit packages in a disposable arkanaOS sysroot."""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import uuid
from pathlib import Path, PurePosixPath

MOTIF = (
    "motif-2.3.8.tar.gz",
    "https://downloads.sourceforge.net/project/motif/Motif%202.3.8%20Source%20Code/motif-2.3.8.tar.gz",
    "859b723666eeac7df018209d66045c9853b50b4218cecadb794e2359619ebce7",
    "motif-2.3.8",
)
NEDIT = (
    "nedit-5.7-src.tar.gz",
    "https://downloads.sourceforge.net/project/nedit/nedit-source/nedit-5.7-src.tar.gz",
    "add9ac79ff973528ad36c86858238bac4f59896c27dbf285cbe6a4d425fca17a",
    "nedit-5.7",
)
XNEDIT = (
    "xnedit-1.6.3.tar.gz",
    "https://downloads.sourceforge.net/project/xnedit/xnedit-1.6.3.tar.gz",
    "8ba69f792412fd86a037b77ab36e706773d541b96aea0ecfd0ee080968076355",
    "xnedit-1.6.3",
)
MOTIF_PACKAGES = ("motif-libs", "motif-devel", "motif-tools")


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def get_source(cache: Path, spec: tuple[str, str, str, str], private_work: Path) -> Path:
    """Copy cached bytes into private work before validating or using them."""
    filename, url, expected, root = spec
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = cache.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
        raise ValueError(f"source cache must be owned by the current user and not writable by others: {cache}")
    cached_path = cache / filename
    if not cached_path.is_file():
        request = urllib.request.Request(url, headers={"User-Agent": "arkpkg-repo-builder/1"})
        with urllib.request.urlopen(request, timeout=90) as response, cached_path.open("xb") as output:
            shutil.copyfileobj(response, output)
    path = private_work / filename
    shutil.copyfile(cached_path, path)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {filename}: {actual}")
    with tarfile.open(path, "r:*") as archive:
        roots = set()
        for member in archive:
            item = PurePosixPath(member.name)
            if item.is_absolute() or ".." in item.parts:
                raise ValueError(f"unsafe path in {filename}: {member.name!r}")
            if item.parts:
                roots.add(item.parts[0])
        if roots != {root}:
            raise ValueError(f"unexpected source root in {filename}: {sorted(roots)}")
    return path


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


def copy_license(archive_path: Path, member_name: str, destination: Path) -> None:
    with tarfile.open(archive_path, "r:*") as archive:
        member = archive.extractfile(member_name)
        if member is None:
            raise ValueError(f"{archive_path.name} does not contain {member_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(member.read())


def run_target_build(
    *, sysroot: Path, archive: Path, root: str, commands: list[str], log: Path
) -> tuple[Path, Path]:
    work_rel = f"/tmp/arkpkg-{root}-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    privileged(["install", "-d", "-m", "755", str(work_host)])
    privileged(["install", "-m", "644", str(archive), str(work_host / archive.name)])
    payload_rel = f"{work_rel}/payload"
    shell = "\n".join(
        (
            "set -eu",
            f"cd {work_rel}",
            f"/usr/bin/tar -xf {archive.name}",
            f"cd {root}",
            *[command.replace("@PAYLOAD@", payload_rel) for command in commands],
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
                "LD_LIBRARY_PATH=/usr/lib",
                "/usr/bin/bash",
                "-c",
                shell,
            ],
            log=log,
        )
    except subprocess.CalledProcessError:
        print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]), file=sys.stderr)
        privileged(
            ["/usr/bin/python3", "-c", "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)", str(work_host)]
        )
        raise
    return sysroot / payload_rel.lstrip("/"), work_host


def installed_packages(arkpkg: Path, sysroot: Path) -> set[str]:
    command = [str(arkpkg), "--root", str(sysroot), "list"]
    full = command if os.geteuid() == 0 else ["sudo", *command]
    result = subprocess.run(full, capture_output=True, text=True, check=True)
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def install_package(arkpkg: Path, sysroot: Path, archive: Path) -> str:
    import lz4.frame

    with lz4.frame.open(archive, "rb") as compressed, tarfile.open(fileobj=compressed, mode="r|") as package:
        for member in package:
            if member.name != "ARKPKG" or not member.isfile():
                continue
            stream = package.extractfile(member)
            if stream is None:
                break
            for line in stream.read().decode("utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "Name":
                    name = value.strip()
                    break
            else:
                continue
            break
        else:
            raise ValueError(f"package archive has no Name field: {archive}")
    privileged([str(arkpkg), "--root", str(sysroot), "install", str(archive), "-y"])
    return name


def build_archives(repo_root: Path, staging: Path, output: Path, builder: Path, names: tuple[str, ...]) -> None:
    subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/build-arkana-packages.py"),
            "--staging",
            str(staging),
            "--arkpkg-build",
            str(builder),
            "--output-dir",
            str(output),
            "--packages",
            *names,
        ],
        check=True,
    )


def build_motif_archives(
    repo_root: Path,
    sysroot: Path,
    staging: Path,
    output: Path,
    motif_source: Path,
    builder: Path,
    jobs: int,
    log: Path,
) -> None:
    motif_patch = """from pathlib import Path
import re
string_calls = re.compile(
    r'\\b(?:strlen|strnlen|strdup|xpmstrdup|strcpy|strncpy|strcat|strncat|strcmp|strncmp|'
    r'strcasecmp|strncasecmp|memcpy|memmove|memset|memcmp)\\s*\\('
)
for path in Path('lib/Xm').glob('Xpm*.c'):
    source = path.read_text()
    if not string_calls.search(source) or '#include <string.h>' in source:
        continue
    lines = source.splitlines(keepends=True)
    include = next(
        (index for index, line in enumerate(lines) if line.lstrip().startswith('#include ')),
        None,
    )
    if include is None:
        raise ValueError(f'{path} uses string functions but has no include block')
    lines.insert(include + 1, '#include <string.h>\\n')
    path.write_text(''.join(lines))
"""
    motif_payload, motif_work = run_target_build(
        sysroot=sysroot,
        archive=motif_source,
        root=MOTIF[3],
        commands=[
            f"/usr/bin/python3 -c {shlex.quote(motif_patch)}",
            "CFLAGS='-std=gnu17' ./configure --prefix=/usr --libdir=/usr/lib --enable-shared --disable-static",
            f"/usr/bin/make -j{jobs} -C lib",
            f"/usr/bin/make -j{jobs} -C tools",
            f"/usr/bin/make -j{jobs} -C clients",
            "DESTDIR=@PAYLOAD@ /usr/bin/make -C lib install",
            "DESTDIR=@PAYLOAD@ /usr/bin/make -C tools install",
            "DESTDIR=@PAYLOAD@ /usr/bin/make -C clients install",
        ],
        log=log,
    )
    try:
        for relative in ("usr/include/Xm", "usr/bin/mwm", "usr/bin/uil"):
            if not (motif_payload / relative).exists():
                raise FileNotFoundError(f"Motif build did not install {relative}")
        motif_stage = staging / "motif"
        copy_tree(motif_payload, motif_stage)
        for name in MOTIF_PACKAGES:
            copy_license(
                motif_source,
                f"{MOTIF[3]}/COPYING",
                motif_stage / f"usr/share/licenses/{name}/COPYING",
            )
        build_archives(repo_root, motif_stage, output, builder, MOTIF_PACKAGES)
    finally:
        privileged(
            [
                "/usr/bin/python3",
                "-c",
                "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                str(motif_work),
            ]
        )


def build(args: argparse.Namespace) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sysroot = args.sysroot.resolve()
    output = args.output_dir.resolve()
    staging = args.staging.resolve()
    cache = args.source_cache.resolve()
    arpkg = args.arkpkg.resolve()
    builder = args.arkpkg_build.resolve()
    # mkdtemp creates a private directory; retain the validated sources until all
    # privileged builds and license copies finish, then remove them in finally.
    private_work = Path(tempfile.mkdtemp(prefix="arkpkg-motif-nedit-"))
    installed: list[str] = []
    try:
        nedit_source = get_source(cache, NEDIT, private_work)
        xnedit_source = get_source(cache, XNEDIT, private_work)
        if staging.exists():
            privileged(["/usr/bin/python3", "-c", "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)", str(staging)])
        staging.mkdir(parents=True)
        if args.reuse_motif:
            for name in MOTIF_PACKAGES:
                matches = list(output.glob(f"{name}-2.3.8-*.ark"))
                if len(matches) != 1:
                    raise FileNotFoundError(
                        f"--reuse-motif needs exactly one {name} archive in {output}"
                    )
            print("Reusing the existing Motif archives")
        else:
            build_motif_archives(
                repo_root,
                sysroot,
                staging,
                output,
                get_source(cache, MOTIF, private_work),
                builder,
                args.jobs,
                private_work / "motif.log",
            )

        current = installed_packages(arpkg, sysroot)
        for name in MOTIF_PACKAGES[:2]:
            if name not in current:
                archive = next(output.glob(f"{name}-2.3.8-*.ark"))
                installed.append(install_package(arpkg, sysroot, archive))
                current.add(name)

        nedit_payload, nedit_work = run_target_build(
            sysroot=sysroot,
            archive=nedit_source,
            root=NEDIT[3],
            commands=[
                "/usr/bin/make -j1 linux CFLAGS='-O2 -std=gnu17 -I/usr/include -DUSE_DIRENT -DUSE_LPR_PRINT_CMD' LIBS='-L/usr/lib -lXm -lXt -lX11 -lm'",
                "/usr/bin/install -D -m 755 source/nedit @PAYLOAD@/usr/bin/nedit",
                "/usr/bin/install -D -m 755 source/nc @PAYLOAD@/usr/bin/nedit-client",
                "/usr/bin/install -D -m 644 README @PAYLOAD@/usr/share/doc/nedit/README",
                "/usr/bin/install -D -m 644 ReleaseNotes @PAYLOAD@/usr/share/doc/nedit/ReleaseNotes",
                "/usr/bin/make -C doc VERSION='NEdit 5.7' doc man",
                "/usr/bin/install -D -m 644 doc/nedit.doc @PAYLOAD@/usr/share/doc/nedit/nedit.doc",
                "/usr/bin/install -D -m 644 doc/nedit.html @PAYLOAD@/usr/share/doc/nedit/nedit.html",
                "/usr/bin/install -D -m 644 doc/html/nedit.html @PAYLOAD@/usr/share/doc/nedit/html/nedit.html",
                "/usr/bin/install -D -m 644 doc/nedit.man @PAYLOAD@/usr/share/man/man1/nedit.1",
                "/usr/bin/install -D -m 644 doc/nc.man @PAYLOAD@/usr/share/man/man1/nedit-client.1",
                "@PAYLOAD@/usr/bin/nedit -V",
            ],
            log=private_work / "nedit.log",
        )
        try:
            for relative in ("usr/bin/nedit", "usr/bin/nedit-client"):
                if not (nedit_payload / relative).is_file():
                    raise FileNotFoundError(f"NEdit build did not install {relative}")
            nedit_stage = staging / "nedit"
            copy_tree(nedit_payload, nedit_stage)
            copy_license(nedit_source, f"{NEDIT[3]}/COPYRIGHT", nedit_stage / "usr/share/licenses/nedit/COPYRIGHT")
            build_archives(repo_root, nedit_stage, output, builder, ("nedit",))
        finally:
            privileged(["/usr/bin/python3", "-c", "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)", str(nedit_work)])

        xnedit_payload, xnedit_work = run_target_build(
            sysroot=sysroot,
            archive=xnedit_source,
            root=XNEDIT[3],
            commands=[
                f"/usr/bin/make -j{args.jobs} linux C_OPT_FLAGS='-O2'",
                "/usr/bin/make install DESTDIR=@PAYLOAD@ PREFIX=/usr",
                "/usr/bin/install -D -m 644 README.md @PAYLOAD@/usr/share/doc/xnedit/README.md",
                "/usr/bin/install -D -m 644 ReleaseNotes @PAYLOAD@/usr/share/doc/xnedit/ReleaseNotes",
                "/usr/bin/install -D -m 644 doc/xnedit.txt @PAYLOAD@/usr/share/doc/xnedit/xnedit.txt",
                "/usr/bin/install -D -m 644 doc/xnedit.html @PAYLOAD@/usr/share/doc/xnedit/xnedit.html",
                "/usr/bin/install -D -m 644 doc/xnedit.man @PAYLOAD@/usr/share/man/man1/xnedit.1",
                "/usr/bin/install -D -m 644 doc/xnc.man @PAYLOAD@/usr/share/man/man1/xnc.1",
                "@PAYLOAD@/usr/bin/xnedit -V",
            ],
            log=private_work / "xnedit.log",
        )
        try:
            for relative in (
                "usr/bin/xnedit",
                "usr/bin/xnc",
                "usr/share/applications/xnedit.desktop",
                "usr/share/icons/xnedit.png",
                "usr/share/man/man1/xnedit.1",
                "usr/share/man/man1/xnc.1",
            ):
                if not (xnedit_payload / relative).is_file():
                    raise FileNotFoundError(f"XNEdit build did not install {relative}")
            xnedit_stage = staging / "xnedit"
            copy_tree(xnedit_payload, xnedit_stage)
            copy_license(
                xnedit_source,
                f"{XNEDIT[3]}/LICENSE",
                xnedit_stage / "usr/share/licenses/xnedit/LICENSE",
            )
            build_archives(repo_root, xnedit_stage, output, builder, ("xnedit",))
        finally:
            privileged(["/usr/bin/python3", "-c", "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)", str(xnedit_work)])
    finally:
        for name in reversed(installed):
            privileged([str(arpkg), "--root", str(sysroot), "remove", name, "-y"])
        shutil.rmtree(private_work, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--arkpkg", type=Path, required=True)
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "packages")
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--source-cache", type=Path, default=Path(tempfile.gettempdir()) / "arkpkg-motif-sources")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--reuse-motif", action="store_true", help="reuse existing Motif archives")
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
