#!/usr/bin/env python3
"""Build optional FLTK packages and the Dillo browser in an arkanaOS sysroot."""

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

from package_metadata import read_metadata_member

FLTK_ARCHIVE = "fltk-1.4.5-source.tar.gz"
FLTK_ROOT = "fltk-1.4.5"
FLTK_URL = f"https://github.com/fltk/fltk/releases/download/release-1.4.5/{FLTK_ARCHIVE}"
FLTK_SHA256 = "eede1fb2b8e9c2e581e77082e15252145855c79aad30070ee3b24aabe2f926f1"
DILLO_ARCHIVE = "dillo-3.2.0.tar.bz2"
DILLO_ROOT = "dillo-3.2.0"
DILLO_URL = f"https://dillo-browser.github.io/release/3.2.0/{DILLO_ARCHIVE}"
DILLO_SHA256 = "1066ed42ea7fe0ce19e79becd029c651c15689922de8408e13e70bb5701931bf"
FLTK_PACKAGES = ("fltk-libs", "fltk-devel", "fltk-tools")
MAX_ARCHIVE_BYTES = 1024**3
MAX_TAR_EXTENSION_SIZE = 64 * 1024
MAX_TAR_EXTENSION_BYTES = 1024 * 1024
MAX_TAR_EXTENSION_DEPTH = 32


class BudgetReader:
    def __init__(self, source, limit: int, label: str):
        self.source = source
        self.remaining = limit
        self.label = label

    def read(self, size: int) -> bytes:
        if size < 0:
            raise ValueError(f"{self.label} requires bounded reads")
        data = self.source.read(min(size, self.remaining + 1))
        if len(data) > self.remaining:
            raise ValueError(f"{self.label} exceeds inspection limit")
        self.remaining -= len(data)
        return data

    def tell(self) -> int:
        return self.source.tell()


class MetadataTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # Extension bodies and sparse maps are parsed before members are yielded.
        depth = getattr(archive, "_metadata_parser_depth", 0)
        if self.type in {
            tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
            tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
        }:
            if depth >= MAX_TAR_EXTENSION_DEPTH:
                raise ValueError("TAR extension nesting exceeds inspection limit")
            if not 0 <= self.size <= MAX_TAR_EXTENSION_SIZE:
                raise ValueError("TAR extension header exceeds inspection limit")
            total = getattr(archive, "_metadata_extension_bytes", 0) + self._block(self.size)
            if total > MAX_TAR_EXTENSION_BYTES:
                raise ValueError("TAR extension data exceeds inspection limit")
            archive._metadata_extension_bytes = total
        source = archive.fileobj
        archive.fileobj = BudgetReader(source, MAX_TAR_EXTENSION_BYTES, "TAR parser data")
        archive._metadata_parser_depth = depth + 1
        try:
            return super()._proc_member(archive)
        finally:
            archive.fileobj = source
            archive._metadata_parser_depth = depth


def privileged(command: list[str], *, log: Path | None = None) -> None:
    full = command if os.geteuid() == 0 else ["sudo", *command]
    if log is None:
        subprocess.run(full, check=True)
    else:
        with log.open("w", encoding="utf-8") as output:
            subprocess.run(full, stdout=output, stderr=subprocess.STDOUT, check=True)


def source_archive(
    cache: Path, name: str, url: str, digest: str, root: str, *, sources: Path
) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    cache_stat = cache.stat()
    if cache_stat.st_uid != os.geteuid() or cache_stat.st_mode & 0o022:
        raise PermissionError(
            f"source cache must be owned by the current user and not writable by others: {cache}"
        )
    cached_path = cache / name
    archive_path = sources / name
    # Validate private bytes; no later consumer may reopen the cache entry.
    try:
        shutil.copyfile(cached_path, archive_path)
        downloaded = False
    except FileNotFoundError:
        request = urllib.request.Request(
            url, headers={"User-Agent": "arkpkg-repo-gui-package-builder/1"}
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            archive_path.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        downloaded = True
    actual = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if actual != digest:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {actual}")
    with tarfile.open(archive_path, "r:*") as source:
        roots = set()
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe source path in {archive_path.name}: {member.name!r}")
            if path.parts:
                roots.add(path.parts[0])
        if roots != {root}:
            raise ValueError(f"unexpected source root in {archive_path.name}: {sorted(roots)}")
    if downloaded:
        try:
            # Never follow or truncate an entry created while downloading.
            with cached_path.open("xb") as output, archive_path.open("rb") as source:
                shutil.copyfileobj(source, output)
        except FileExistsError:
            pass
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


def copy_license(archive_path: Path, member_name: str, destination: Path) -> None:
    with tarfile.open(archive_path, "r:*") as source:
        member = source.extractfile(member_name)
        if member is None:
            raise ValueError(f"{archive_path.name} has no {member_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(member.read())


def run_target_build(
    *,
    sysroot: Path,
    archive: Path,
    source_root: str,
    commands: list[str],
    log: Path,
) -> tuple[Path, Path]:
    work_rel = f"/tmp/arkpkg-{source_root}-{uuid.uuid4().hex}"
    work_host = sysroot / work_rel.lstrip("/")
    privileged(["install", "-d", "-m", "755", str(work_host)])
    archive_in_root = work_host / archive.name
    privileged(["install", "-m", "644", str(archive), str(archive_in_root)])
    payload_rel = f"{work_rel}/payload"
    commands = [command.replace("@PAYLOAD@", payload_rel) for command in commands]
    shell = "\n".join(
        (
            "set -eu",
            f"cd {work_rel}",
            f"/usr/bin/tar -xf {archive.name}",
            f"cd {source_root}",
            *commands,
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
        privileged(
            ["/usr/bin/python3", "-c", "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)", str(work_host)]
        )
        raise
    return sysroot / payload_rel.lstrip("/"), work_host


def build_archives(
    repo_root: Path,
    staging: Path,
    output_dir: Path,
    arkpkg_build: Path,
    names: tuple[str, ...],
) -> None:
    subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/build-arkana-packages.py"),
            "--staging",
            str(staging),
            "--arkpkg-build",
            str(arkpkg_build),
            "--output-dir",
            str(output_dir),
            "--packages",
            *names,
        ],
        check=True,
    )


def installed_packages(arkpkg: Path, sysroot: Path) -> set[str]:
    command = [str(arkpkg), "--root", str(sysroot), "list"]
    full = command if os.geteuid() == 0 else ["sudo", *command]
    result = subprocess.run(full, capture_output=True, text=True, check=True)
    return {line.split()[0] for line in result.stdout.splitlines() if line.split()}


def install_package(arkpkg: Path, sysroot: Path, archive: Path) -> str:
    import lz4.frame

    name = None
    with (
        lz4.frame.open(archive, "rb") as compressed,
        tarfile.open(
            fileobj=BudgetReader(compressed, MAX_ARCHIVE_BYTES, "expanded archive"),
            mode="r|",
            tarinfo=MetadataTarInfo,
        ) as package,
    ):
        for member in package:
            if member.name != "ARKPKG" or not member.isfile():
                continue
            if member.issparse():
                raise ValueError("ARKPKG metadata must not be sparse")
            metadata = read_metadata_member(package, member)
            if metadata is None:
                break
            for line in metadata.splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "Name":
                    name = value.strip()
                    break
            else:
                continue
            break
    if name is None:
        raise ValueError(f"package archive has no Name field: {archive}")
    command = [str(arkpkg), "--root", str(sysroot), "install", str(archive), "-y"]
    privileged(command)
    return name


def build(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory(prefix="arkpkg-fltk-dillo-sources-") as temporary:
        build_from_sources(args, Path(temporary))


def build_from_sources(args: argparse.Namespace, sources: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sysroot = args.sysroot.resolve()
    output_dir = args.output_dir.resolve()
    output_staging = args.output_staging.resolve()
    source_cache = args.source_cache.resolve()
    arkpkg = args.arkpkg.resolve()
    arkpkg_build = args.arkpkg_build.resolve()

    for binary in ("bash", "cmake", "make", "pkg-config", "tar"):
        if not (sysroot / "usr/bin" / binary).exists():
            raise FileNotFoundError(f"target build root lacks /usr/bin/{binary}")
    fltk_source = None
    if not args.reuse_fltk:
        fltk_source = source_archive(
            source_cache, FLTK_ARCHIVE, FLTK_URL, FLTK_SHA256, FLTK_ROOT, sources=sources
        )
    dillo_source = source_archive(
        source_cache, DILLO_ARCHIVE, DILLO_URL, DILLO_SHA256, DILLO_ROOT, sources=sources
    )
    if output_staging.exists():
        shutil.rmtree(output_staging)
    output_staging.mkdir(parents=True)
    logs = Path(tempfile.mkdtemp(prefix="arkpkg-fltk-dillo-logs-"))
    installed_build_dependencies: list[str] = []
    try:
        if args.reuse_fltk:
            for package in FLTK_PACKAGES:
                if len(list(output_dir.glob(f"{package}-1.4.5-*.ark"))) != 1:
                    raise FileNotFoundError(f"--reuse-fltk needs one {package} 1.4.5 archive")
        else:
            assert fltk_source is not None
            fltk_commands = [
                "/usr/bin/cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib -DFLTK_BACKEND_X11=ON -DFLTK_BACKEND_WAYLAND=OFF -DFLTK_BUILD_SHARED_LIBS=ON -DFLTK_BUILD_TEST=OFF -DFLTK_BUILD_EXAMPLES=OFF -DFLTK_BUILD_FLUID=ON -DFLTK_BUILD_FLTK_OPTIONS=ON -DFLTK_BUILD_FORMS=ON -DFLTK_BUILD_GL=OFF -DFLTK_BUILD_HTML_DOCS=OFF -DFLTK_BUILD_PDF_DOCS=OFF",
                f"/usr/bin/make -C build -j{args.jobs}",
                "DESTDIR=@PAYLOAD@ /usr/bin/cmake --install build",
            ]
            fltk_payload, fltk_work = run_target_build(
                sysroot=sysroot,
                archive=fltk_source,
                source_root=FLTK_ROOT,
                commands=fltk_commands,
                log=logs / "fltk.log",
            )
            try:
                required = (
                    "usr/lib/libfltk.so.1.4",
                    "usr/bin/fltk-config",
                    "usr/bin/fluid",
                )
                for relative in required:
                    if not (fltk_payload / relative).exists():
                        raise FileNotFoundError(f"FLTK build did not install {relative}")
                fltk_staging = output_staging / "fltk"
                copy_tree(fltk_payload, fltk_staging)
                for package in FLTK_PACKAGES:
                    copy_license(
                        fltk_source,
                        f"{FLTK_ROOT}/COPYING",
                        fltk_staging / f"usr/share/licenses/{package}/COPYING",
                    )
                build_archives(
                    repo_root,
                    fltk_staging,
                    output_dir,
                    arkpkg_build,
                    FLTK_PACKAGES,
                )
            finally:
                privileged(
                    [
                        "/usr/bin/python3",
                        "-c",
                        "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                        str(fltk_work),
                    ]
                )

        already_installed = installed_packages(arkpkg, sysroot)
        for package in FLTK_PACKAGES[:2]:
            if package in already_installed:
                continue
            archive = next(output_dir.glob(f"{package}-1.4.5-*.ark"))
            name = install_package(arkpkg, sysroot, archive)
            installed_build_dependencies.append(name)
            already_installed.add(name)

        dillo_patch = """from pathlib import Path
path = Path('configure')
source = path.read_text()
old = '  1.3.*)'
assert source.count(old) == 1, 'unexpected FLTK version check in configure'
path.write_text(source.replace(old, '  1.3.*|1.4.*)', 1))"""
        dillo_commands = [
            f"/usr/bin/python3 -c {shlex.quote(dillo_patch)}",
            "./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var --enable-tls --enable-openssl --disable-mbedtls --enable-png --enable-jpeg --enable-webp --enable-svg --enable-gif --enable-ipv6 --with-ca-certs-file=/etc/ssl/ca-bundle.crt",
            f"/usr/bin/make -j{args.jobs}",
            "DESTDIR=@PAYLOAD@ /usr/bin/make install",
            "if test -f @PAYLOAD@/usr/share/applications/dillo.desktop; then /usr/bin/sed -i 's/^Name=Dillo$/Name=dillo/; s/^GenericName=Web Browser$/GenericName=web browser/; s/^Comment=Fast and small graphical web browser$/Comment=fast and small graphical web browser/' @PAYLOAD@/usr/share/applications/dillo.desktop; fi",
        ]
        dillo_payload, dillo_work = run_target_build(
            sysroot=sysroot,
            archive=dillo_source,
            source_root=DILLO_ROOT,
            commands=dillo_commands,
            log=logs / "dillo.log",
        )
        try:
            for relative in (
                "usr/bin/dillo",
                "usr/bin/dpid",
                "usr/bin/dillo-install-hyphenation",
            ):
                if not (dillo_payload / relative).is_file():
                    raise FileNotFoundError(f"Dillo build did not install {relative}")
            dillo_staging = output_staging / "dillo"
            copy_tree(dillo_payload, dillo_staging)
            copy_license(
                dillo_source,
                f"{DILLO_ROOT}/COPYING",
                dillo_staging / "usr/share/licenses/dillo/COPYING",
            )
            build_archives(
                repo_root,
                dillo_staging,
                output_dir,
                arkpkg_build,
                ("dillo",),
            )
        finally:
            privileged(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import shutil,sys; shutil.rmtree(sys.argv[1],ignore_errors=True)",
                    str(dillo_work),
                ]
            )
    finally:
        for name in reversed(installed_build_dependencies):
            privileged([str(arkpkg), "--root", str(sysroot), "remove", name, "-y"])
        shutil.rmtree(logs, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--arkpkg", type=Path, required=True)
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "packages"
    )
    parser.add_argument("--output-staging", type=Path, required=True)
    parser.add_argument(
        "--source-cache", type=Path, default=Path(tempfile.gettempdir()) / "arkpkg-gui-sources"
    )
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--reuse-fltk",
        action="store_true",
        help="reuse already-built FLTK 1.4.5 archives and only compile Dillo",
    )
    args = parser.parse_args()
    if args.jobs not in (1, 2):
        parser.error("--jobs must be 1 or 2 to keep the target build memory bounded")
    return args


if __name__ == "__main__":
    build(parse_args())
