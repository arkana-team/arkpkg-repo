#!/usr/bin/env python3
"""Build checksum-pinned upstream system-information tools as arkpkg packages."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

import lz4.frame

PACKAGES = {
    "neofetch": {
        "version": "7.1.0",
        "arch": "any",
        "url": "https://github.com/dylanaraps/neofetch",
        "archive": "https://codeload.github.com/dylanaraps/neofetch/tar.gz/refs/tags/7.1.0",
        "archive_sha256": "58a95e6b714e41efc804eca389a223309169b2def35e57fa934482a6b47c27e7",
        "root": "neofetch-7.1.0",
        "license_source": "LICENSE.md",
        "license_path": "usr/share/doc/neofetch/copyright",
        "files": {
            "neofetch": "usr/bin/neofetch",
            "neofetch.1": "usr/share/man/man1/neofetch.1",
        },
        "description": "System information and distribution logo tool",
        "license": "MIT",
    },
    "pfetch": {
        "version": "0.6.0",
        "arch": "any",
        "url": "https://github.com/dylanaraps/pfetch",
        "archive": "https://codeload.github.com/dylanaraps/pfetch/tar.gz/refs/tags/0.6.0",
        "archive_sha256": "d1f611e61c1f8ae55bd14f8f6054d06fcb9a2d973095367c1626842db66b3182",
        "root": "pfetch-0.6.0",
        "license_source": "LICENSE.md",
        "license_path": "usr/share/doc/pfetch/copyright",
        "files": {"pfetch": "usr/bin/pfetch"},
        "description": "Small POSIX shell system information tool",
        "license": "MIT",
    },
    "nitch": {
        "version": "0.1.6",
        "arch": "x86_64",
        "url": "https://github.com/ssleert/nitch",
        "archive": "https://codeload.github.com/ssleert/nitch/tar.gz/refs/tags/0.1.6",
        "archive_sha256": "118654cf34b9b5d0f0babccad623360438b6c1e9d619770f590d192de2183350",
        "root": "nitch-0.1.6",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/nitch/copyright",
        "binary": "https://github.com/ssleert/nitch/releases/download/0.1.6/nitchNoNerd",
        "binary_sha256": "0f88f0dd30341476e62f2265bf81a67182a6a29e89003e0a19a15c666ba4f295",
        "description": "Fast system information tool written in Nim",
        "license": "MIT",
    },
    "yt-dlp": {
        "version": "2026.08.19",
        "arch": "any",
        "url": "https://github.com/yt-dlp/yt-dlp",
        "archive": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.tar.gz",
        "archive_sha256": "072aad4f2a7604e92155f61a275a4752dc64046c8f6d90df3710525d94cd37c1",
        "root": "yt-dlp",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/yt-dlp/copyright",
        "binary": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp",
        "binary_sha256": "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6",
        "binary_path": "usr/bin/yt-dlp",
        "files": {
            "yt-dlp.1": "usr/share/man/man1/yt-dlp.1",
            "README.md": "usr/share/doc/yt-dlp/README.md",
            "completions/bash/yt-dlp": "usr/share/bash-completion/completions/yt-dlp",
            "completions/fish/yt-dlp.fish": "usr/share/fish/vendor_completions.d/yt-dlp.fish",
            "completions/zsh/_yt-dlp": "usr/share/zsh/site-functions/_yt-dlp",
        },
        "description": "Feature-rich command-line audio and video downloader",
        "license": "Unlicense; bundled zipimport code also includes MIT and ISC components",
    },
}
INTERPRETER = re.compile(r"Requesting program interpreter: (.+?)\]")
NEEDED = re.compile(r"Shared library: \[(.+?)\]")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_cache(cache: Path) -> Path:
    cache = cache.absolute()
    # Check parents too: another user must not be able to replace the cache.
    for directory in reversed((cache, *cache.parents)):
        directory.mkdir(mode=0o700, exist_ok=True)
        metadata = directory.lstat()
        allowed_owners = {os.geteuid()} if directory == cache else {0, os.geteuid()}
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in allowed_owners:
            raise ValueError(f"unsafe source cache directory: {directory}")
        # A trusted sticky parent, such as /tmp, protects its owned children.
        if metadata.st_mode & 0o022 and (
            directory == cache or not metadata.st_mode & stat.S_ISVTX
        ):
            raise ValueError(
                f"unsafe source cache directory is writable by others: {directory}"
            )
    return cache


def download(url: str, expected: str, cache: Path, work: Path) -> Path:
    """Return verified bytes from a private snapshot, never from the shared cache."""
    cache = prepare_cache(cache)
    target = cache / url.rsplit("/", 1)[-1]
    snapshot = work / target.name
    if not target.is_symlink() and target.is_file():
        shutil.copyfile(target, snapshot)
        if sha256(snapshot) == expected:
            return snapshot
    request = urllib.request.Request(
        url, headers={"User-Agent": "arkpkg-repo-upstream-package-builder/1"}
    )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=cache, prefix=f".{target.name}.", suffix=".download", delete=False
        ) as output:
            temporary = Path(output.name)
            for attempt in range(4):
                try:
                    output.seek(0)
                    output.truncate()
                    with urllib.request.urlopen(request, timeout=120) as response:
                        shutil.copyfileobj(response, output)
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    if attempt == 3:
                        raise
                    time.sleep(2**attempt)
        shutil.copyfile(temporary, snapshot)
        actual = sha256(snapshot)
        if actual != expected:
            snapshot.unlink(missing_ok=True)
            raise ValueError(
                f"SHA-256 mismatch for {target.name}: expected {expected}, got {actual}"
            )
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return snapshot


def read_tar_file(archive_path: Path, member_name: str) -> bytes:
    with tarfile.open(archive_path, "r:gz") as archive:
        try:
            member = archive.getmember(member_name)
        except KeyError as error:
            raise ValueError(f"{archive_path.name}: missing {member_name}") from error
        if not member.isfile():
            raise ValueError(f"{archive_path.name}: {member_name} is not a regular file")
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"{archive_path.name}: could not read {member_name}")
        return source.read()


def stage_has_library(staging: Path, soname: str) -> bool:
    directories = (
        "lib",
        "lib64",
        "lib/x86_64-linux-gnu",
        "usr/lib",
        "usr/lib64",
        "usr/lib/x86_64-linux-gnu",
    )
    return any(
        (staging / directory / soname).exists()
        or (staging / directory / soname).is_symlink()
        for directory in directories
    )


def validate_elf(binary: Path, staging: Path) -> None:
    dynamic = subprocess.run(
        ["readelf", "-d", str(binary)], capture_output=True, text=True, check=True
    ).stdout
    for line in dynamic.splitlines():
        match = NEEDED.search(line)
        if match and not stage_has_library(staging, match.group(1)):
            raise ValueError(
                f"{binary.name}: {match.group(1)} is absent from the ArkanaOS staging root"
            )
    program_headers = subprocess.run(
        ["readelf", "-l", str(binary)], capture_output=True, text=True, check=True
    ).stdout
    match = INTERPRETER.search(program_headers)
    if match:
        interpreter = PurePosixPath(match.group(1))
        staged_interpreter = staging.joinpath(*interpreter.parts[1:])
        if not staged_interpreter.exists() and not staged_interpreter.is_symlink():
            raise ValueError(
                f"{binary.name}: ELF interpreter {interpreter} is absent from staging"
            )


def archive_owners(repo_root: Path, ignored_archives: set[str]) -> set[str]:
    owned = set()
    for archive_path in (repo_root / "packages").glob("*.ark"):
        if archive_path.name in ignored_archives:
            continue
        with (
            lz4.frame.open(archive_path, "rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            for member in archive:
                if not (member.isfile() or member.issym() or member.islnk()):
                    continue
                path = PurePosixPath(member.name)
                if path.parts[:1] == ("package",) and len(path.parts) > 1:
                    owned.add("/".join(path.parts[1:]))
    return owned


def recipe_text(name: str, package: dict[str, object]) -> str:
    executable = "nitch" if name == "nitch" else name
    return "\n".join(
        (
            f"Name: {name}",
            f"Description: {package['description']}",
            f"Version: {package['version']}",
            f"Arch: {package['arch']}",
            f"URL: {package['url']}",
            f"License: {package['license']}",
            "Maintainer: arkanaOS Team",
            f"Provides: {executable}=/usr/bin/{executable}",
            "",
        )
    )


def write_payload_file(target: Path, data: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    target.chmod(mode)


def build(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    cache = prepare_cache(args.source_cache)
    staging = args.staging.resolve()
    selected = args.packages or sorted(PACKAGES)
    unknown = set(selected).difference(PACKAGES)
    if unknown:
        raise ValueError(f"unknown packages: {', '.join(sorted(unknown))}")
    ignored_archives = {
        f"{name}-{PACKAGES[name]['version']}-{PACKAGES[name]['arch']}.ark"
        for name in selected
    }
    owned = archive_owners(repo_root, ignored_archives)

    for index, name in enumerate(selected, 1):
        package = PACKAGES[name]
        with tempfile.TemporaryDirectory(prefix=f"arkpkg-{name}-") as temporary:
            work = Path(temporary) / "sources"
            work.mkdir()
            source_archive = download(
                str(package["archive"]), str(package["archive_sha256"]), cache, work
            )
            package_dir = Path(temporary) / name
            payload = package_dir / "package"
            payload.mkdir(parents=True)

            if package.get("binary"):
                binary = download(
                    str(package["binary"]), str(package["binary_sha256"]), cache, work
                )
                target = payload / str(
                    package.get("binary_path", f"usr/bin/{name}")
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(binary, target)
                target.chmod(0o755)
                header = target.read_bytes()[:4]
                if header == b"\x7fELF":
                    validate_elf(target, staging)
                elif not target.read_bytes()[:160].startswith(b"#!"):
                    raise ValueError(
                        f"{name}: upstream binary is neither ELF nor an executable script"
                    )

            for source_name, target_name in package.get("files", {}).items():
                data = read_tar_file(
                    source_archive, f"{package['root']}/{source_name}"
                )
                target = payload / str(target_name)
                mode = 0o755 if str(target_name).startswith("usr/bin/") else 0o644
                write_payload_file(target, data, mode)

            license_target = payload / str(package["license_path"])
            write_payload_file(
                license_target,
                read_tar_file(
                    source_archive,
                    f"{package['root']}/{package['license_source']}",
                ),
                0o644,
            )

            paths = {
                path.relative_to(payload).as_posix()
                for path in payload.rglob("*")
                if not path.is_dir() or path.is_symlink()
            }
            collisions = sorted(paths.intersection(owned))
            if collisions:
                raise ValueError(
                    f"{name}: paths already owned by arkpkg-repo: "
                    + ", ".join(collisions[:20])
                )

            recipe = recipe_text(name, package)
            recipe_path = repo_root / "packages/recipes" / name / "ARKPKG"
            recipe_path.parent.mkdir(parents=True, exist_ok=True)
            if recipe_path.exists() and recipe_path.read_text(encoding="utf-8") != recipe:
                raise ValueError(f"existing recipe differs from pinned metadata: {recipe_path}")
            recipe_path.write_text(recipe, encoding="utf-8")
            shutil.copy2(recipe_path, package_dir / "ARKPKG")

            archive_path = repo_root / "packages" / (
                f"{name}-{package['version']}-{package['arch']}.ark"
            )
            subprocess.run(
                [
                    str(args.arkpkg_build.resolve()),
                    str(package_dir),
                    "--output",
                    str(archive_path),
                ],
                check=True,
            )
            owned.update(paths)
            print(
                f"Built {index}/{len(selected)} {archive_path.name} "
                f"({archive_path.stat().st_size} bytes; {len(paths)} payload paths)",
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-upstream-cli-cache")
    )
    parser.add_argument("--packages", nargs="+", choices=sorted(PACKAGES))
    return parser.parse_args()


if __name__ == "__main__":
    try:
        build(parse_args())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
