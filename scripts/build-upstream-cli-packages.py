#!/usr/bin/env python3
"""Build checksum-pinned upstream CLI tools as arkpkg packages."""

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
import zipfile
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
    "atuin": {
        "version": "18.24.2",
        "arch": "x86_64",
        "url": "https://github.com/atuinsh/atuin",
        "archive": "https://github.com/atuinsh/atuin/releases/download/v18.24.2/atuin-x86_64-unknown-linux-musl.tar.gz",
        "archive_sha256": "311cd1ba4b8be27de7071dbcae1c95ff4c2b38aeec82d557707b6b832bfceddf",
        "root": "atuin-x86_64-unknown-linux-musl",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/atuin/copyright",
        "binary_members": {
            "atuin-x86_64-unknown-linux-musl/atuin": "usr/bin/atuin",
        },
        "binary_member_sha256": {
            "atuin-x86_64-unknown-linux-musl/atuin": "3a80d8d27a6c4317843611a08c60ad7044d8e5b845bdcf896f8564f0ed9da383",
        },
        "files": {
            "README.md": "usr/share/doc/atuin/README.md",
            "CHANGELOG.md": "usr/share/doc/atuin/CHANGELOG.md",
        },
        "description": "Searchable shell history manager with optional sync",
        "license": "MIT",
    },
    "yazi": {
        "version": "26.9.1",
        "arch": "x86_64",
        "url": "https://github.com/sxyazi/yazi",
        "archive": "https://codeload.github.com/sxyazi/yazi/tar.gz/refs/tags/v26.9.1",
        "archive_sha256": "66857f1b670469daf258edd0bb2ea51d9ad3e2cab4eea9684028c80059fd6862",
        "root": "yazi-26.9.1",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/yazi/copyright",
        "binary_archive": "https://github.com/sxyazi/yazi/releases/download/v26.9.1/yazi-x86_64-unknown-linux-musl.zip",
        "binary_archive_sha256": "9b9c39decccf8cb0ff53a7d637d38f8a79d93bbd0099f4ea9c619ef6bb392f5d",
        "binary_members": {
            "yazi-x86_64-unknown-linux-musl/yazi": "usr/bin/yazi",
            "yazi-x86_64-unknown-linux-musl/ya": "usr/bin/ya",
        },
        "binary_member_sha256": {
            "yazi-x86_64-unknown-linux-musl/yazi": "a780480f4de6991f0ef3925a06af7835163a22efe52d73b5bc1fb827aa76ed11",
            "yazi-x86_64-unknown-linux-musl/ya": "d2d8c74cd1434864a873bf645a9dd211d8ede8dd877861d622c9a2ecd7deb38d",
        },
        "binary_files": {
            "yazi-x86_64-unknown-linux-musl/completions/yazi.bash": "usr/share/bash-completion/completions/yazi",
            "yazi-x86_64-unknown-linux-musl/completions/ya.bash": "usr/share/bash-completion/completions/ya",
            "yazi-x86_64-unknown-linux-musl/completions/yazi.fish": "usr/share/fish/vendor_completions.d/yazi.fish",
            "yazi-x86_64-unknown-linux-musl/completions/ya.fish": "usr/share/fish/vendor_completions.d/ya.fish",
            "yazi-x86_64-unknown-linux-musl/completions/_yazi": "usr/share/zsh/site-functions/_yazi",
            "yazi-x86_64-unknown-linux-musl/completions/_ya": "usr/share/zsh/site-functions/_ya",
        },
        "files": {
            "README.md": "usr/share/doc/yazi/README.md",
            "LICENSE-ICONS": "usr/share/doc/yazi/LICENSE-ICONS",
        },
        "provides": ["yazi=/usr/bin/yazi", "ya=/usr/bin/ya"],
        "description": "Fast terminal file manager with shell completions",
        "license": "MIT",
    },
    "zellij": {
        "version": "0.45.1",
        "arch": "x86_64",
        "url": "https://github.com/zellij-org/zellij",
        "archive": "https://codeload.github.com/zellij-org/zellij/tar.gz/refs/tags/v0.45.1",
        "archive_sha256": "5cbe711437d2a61afd9287165f6aca0bcccb9ab1473633665a5b11ed55467852",
        "root": "zellij-0.45.1",
        "license_source": "LICENSE.md",
        "license_path": "usr/share/doc/zellij/copyright",
        "binary_archive": "https://github.com/zellij-org/zellij/releases/download/v0.45.1/zellij-no-web-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "d7bda1e18c30a688833ae7627f1d6a253bbba5349a4bc48e4f0ec008aaf75ed1",
        "binary_members": {"zellij": "usr/bin/zellij"},
        "binary_member_sha256": {
            "zellij": "0ec6ef07b63c6355c02ce18343d40ef5ef5af19e25313ea9009c8fceda29e94f",
        },
        "files": {
            "README.md": "usr/share/doc/zellij/README.md",
            "CHANGELOG.md": "usr/share/doc/zellij/CHANGELOG.md",
        },
        "description": "Terminal workspace manager and multiplexer without the web client",
        "license": "MIT",
    },
    "btm": {
        "version": "0.14.9",
        "arch": "x86_64",
        "url": "https://github.com/ClementTsang/bottom",
        "archive": "https://codeload.github.com/ClementTsang/bottom/tar.gz/refs/tags/0.14.9",
        "archive_sha256": "1dbb940c763fb583b7e1c7dfa165b73ed9a0ba712e72cc97311c5b1c098d5b72",
        "root": "bottom-0.14.9",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/btm/copyright",
        "binary_archive": "https://github.com/ClementTsang/bottom/releases/download/0.14.9/bottom_x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "b4bee5b193e7d3f6e090ac14f0ca15acec2e7fe4ef64988e0bfc492e16c28c9a",
        "binary_members": {"btm": "usr/bin/btm"},
        "binary_member_sha256": {
            "btm": "5bffbcd391b9b9964e8429549f1c404f25d7606a5031a4242cad787ac6028bf3",
        },
        "binary_files": {
            "completion/btm.bash": "usr/share/bash-completion/completions/btm",
            "completion/btm.fish": "usr/share/fish/vendor_completions.d/btm.fish",
            "completion/_btm": "usr/share/zsh/site-functions/_btm",
        },
        "files": {"README.md": "usr/share/doc/btm/README.md"},
        "description": "Interactive terminal process and system monitor",
        "license": "MIT",
    },
    "watchexec": {
        "version": "2.8.0",
        "arch": "x86_64",
        "url": "https://github.com/watchexec/watchexec",
        "archive": "https://codeload.github.com/watchexec/watchexec/tar.gz/refs/tags/v2.8.0",
        "archive_sha256": "861496cd1e7d8546f770551893077e9b898fef1a6623d5e2207cf28c932e7738",
        "root": "watchexec-2.8.0",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/watchexec/copyright",
        "binary_archive": "https://github.com/watchexec/watchexec/releases/download/v2.8.0/watchexec-2.8.0-x86_64-unknown-linux-musl.tar.xz",
        "binary_archive_sha256": "35ea667cbe4bc96c23ba2a1f46c94c46bc385b67165e3bf621c0e7e4959aa99b",
        "binary_members": {
            "watchexec-2.8.0-x86_64-unknown-linux-musl/watchexec": "usr/bin/watchexec",
        },
        "binary_member_sha256": {
            "watchexec-2.8.0-x86_64-unknown-linux-musl/watchexec": "74c1004a62052b3785ab1267a2270671185d16f33e60d092c4e1c4f41678e0c2",
        },
        "binary_files": {
            "watchexec-2.8.0-x86_64-unknown-linux-musl/watchexec.1": "usr/share/man/man1/watchexec.1",
            "watchexec-2.8.0-x86_64-unknown-linux-musl/completions/bash": "usr/share/bash-completion/completions/watchexec",
            "watchexec-2.8.0-x86_64-unknown-linux-musl/completions/fish": "usr/share/fish/vendor_completions.d/watchexec.fish",
            "watchexec-2.8.0-x86_64-unknown-linux-musl/completions/zsh": "usr/share/zsh/site-functions/_watchexec",
        },
        "files": {"README.md": "usr/share/doc/watchexec/README.md"},
        "description": "Run commands when files change with shell completions and a manual",
        "license": "Apache-2.0",
    },
    "git-delta": {
        "version": "0.20.1",
        "arch": "x86_64",
        "url": "https://github.com/dandavison/delta",
        "archive": "https://codeload.github.com/dandavison/delta/tar.gz/refs/tags/0.20.1",
        "archive_sha256": "d9d502396e3595ee8fd926f1ed2e54ac9935baabd3569a3753efab36304f90fa",
        "root": "delta-0.20.1",
        "license_source": "LICENSE",
        "license_path": "usr/share/doc/git-delta/copyright",
        "binary_archive": "https://github.com/dandavison/delta/releases/download/0.20.1/delta-0.20.1-x86_64-unknown-linux-musl.tar.gz",
        "binary_archive_sha256": "69dcb4043f55c29118f408c6efd30115e0a4e0a6709253343b8c8fa814ab4a31",
        "binary_members": {
            "delta-0.20.1-x86_64-unknown-linux-musl/delta": "usr/bin/delta",
        },
        "binary_member_sha256": {
            "delta-0.20.1-x86_64-unknown-linux-musl/delta": "35d5a370dd8f554c0465a37acbd1016b8781efa10e04291f8fffc59cbc9e83c4",
        },
        "files": {
            "README.md": "usr/share/doc/git-delta/README.md",
            "themes.gitconfig": "usr/share/git-delta/themes.gitconfig",
        },
        "provides": ["delta=/usr/bin/delta"],
        "description": "Syntax-highlighting pager for git, diff, grep, and blame output",
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
MAX_ARCHIVE_BYTES = 90_000_000
MAX_UPSTREAM_MEMBER_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_METADATA_BYTES = 64 * 1024


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


def read_archive_file(archive_path: Path, member_name: str) -> bytes:
    """Read one regular archive member without extracting it to disk."""
    member_path = PurePosixPath(member_name)
    if (
        not member_name
        or member_path.is_absolute()
        or "\\" in member_name
        or ".." in member_path.parts
    ):
        raise ValueError(f"unsafe upstream archive member: {member_name!r}")

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            try:
                member = archive.getinfo(member_name)
            except KeyError as error:
                raise ValueError(
                    f"{archive_path.name}: missing {member_name}"
                ) from error
            mode = (member.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            if member.is_dir() or (file_type and file_type != stat.S_IFREG):
                raise ValueError(
                    f"{archive_path.name}: {member_name} is not a regular file"
                )
            if not 0 <= member.file_size <= MAX_UPSTREAM_MEMBER_BYTES:
                raise ValueError(
                    f"{archive_path.name}: {member_name} exceeds "
                    f"{MAX_UPSTREAM_MEMBER_BYTES} bytes"
                )
            with archive.open(member) as source:
                data = source.read(MAX_UPSTREAM_MEMBER_BYTES + 1)
    else:
        with tarfile.open(archive_path, "r:*") as archive:
            try:
                member = archive.getmember(member_name)
            except KeyError as error:
                raise ValueError(
                    f"{archive_path.name}: missing {member_name}"
                ) from error
            if not member.isfile() or member.issparse():
                raise ValueError(
                    f"{archive_path.name}: {member_name} is not a regular file"
                )
            if not 0 <= member.size <= MAX_UPSTREAM_MEMBER_BYTES:
                raise ValueError(
                    f"{archive_path.name}: {member_name} exceeds "
                    f"{MAX_UPSTREAM_MEMBER_BYTES} bytes"
                )
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"{archive_path.name}: could not read {member_name}")
            with source:
                data = source.read(MAX_UPSTREAM_MEMBER_BYTES + 1)

    if len(data) > MAX_UPSTREAM_MEMBER_BYTES:
        raise ValueError(
            f"{archive_path.name}: {member_name} exceeds "
            f"{MAX_UPSTREAM_MEMBER_BYTES} bytes"
        )
    return data


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


def archive_owners(repo_root: Path, ignored_packages: set[str]) -> set[str]:
    """Find payload paths owned by packages that are not being rebuilt."""
    owned = set()
    for archive_path in (repo_root / "packages").glob("*.ark"):
        package_name = None
        archive_owned = set()
        with (
            lz4.frame.open(archive_path, "rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            for member in archive:
                if member.name == "ARKPKG" and member.isfile():
                    if not 0 <= member.size <= MAX_PACKAGE_METADATA_BYTES:
                        raise ValueError(
                            f"{archive_path.name}: package metadata exceeds "
                            f"{MAX_PACKAGE_METADATA_BYTES} bytes"
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError(
                            f"{archive_path.name}: could not read package metadata"
                        )
                    metadata = source.read(MAX_PACKAGE_METADATA_BYTES + 1)
                    if len(metadata) > MAX_PACKAGE_METADATA_BYTES:
                        raise ValueError(
                            f"{archive_path.name}: package metadata exceeds "
                            f"{MAX_PACKAGE_METADATA_BYTES} bytes"
                        )
                    for line in metadata.decode("utf-8").splitlines():
                        key, separator, value = line.partition(":")
                        if separator and key.strip() == "Name":
                            package_name = value.strip()
                            break
                if not (member.isfile() or member.issym() or member.islnk()):
                    continue
                path = PurePosixPath(member.name)
                if path.parts[:1] == ("package",) and len(path.parts) > 1:
                    archive_owned.add("/".join(path.parts[1:]))
        if package_name not in ignored_packages:
            owned.update(archive_owned)
    return owned


def recipe_text(name: str, package: dict[str, object]) -> str:
    """Render the package metadata file for an upstream release."""
    executable = "nitch" if name == "nitch" else name
    provides = package.get("provides") or [
        f"{executable}=/usr/bin/{executable}"
    ]
    return "\n".join(
        (
            f"Name: {name}",
            f"Description: {package['description']}",
            f"Version: {package['version']}",
            f"Arch: {package['arch']}",
            f"URL: {package['url']}",
            f"License: {package['license']}",
            "Maintainer: arkanaOS Team",
            f"Provides: {', '.join(str(item) for item in provides)}",
            "",
        )
    )


def write_payload_file(target: Path, data: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    target.chmod(mode)


def build(args: argparse.Namespace) -> None:
    """Build selected upstream CLI packages from checksum-pinned sources."""
    repo_root = args.repo_root.resolve()
    cache = prepare_cache(args.source_cache)
    staging = args.staging.resolve()
    selected = args.packages or sorted(PACKAGES)
    unknown = set(selected).difference(PACKAGES)
    if unknown:
        raise ValueError(f"unknown packages: {', '.join(sorted(unknown))}")
    owned = archive_owners(repo_root, set(selected))

    for index, name in enumerate(selected, 1):
        package = PACKAGES[name]
        with tempfile.TemporaryDirectory(prefix=f"arkpkg-{name}-") as temporary:
            work = Path(temporary) / "sources"
            work.mkdir()
            source_archive = download(
                str(package["archive"]), str(package["archive_sha256"]), cache, work
            )
            binary_archive = source_archive
            if package.get("binary_archive"):
                binary_archive = download(
                    str(package["binary_archive"]),
                    str(package["binary_archive_sha256"]),
                    cache,
                    work,
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

            member_hashes = package.get("binary_member_sha256", {})
            for source_name, target_name in package.get("binary_members", {}).items():
                data = read_archive_file(binary_archive, str(source_name))
                expected = member_hashes.get(source_name)
                if expected and hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError(f"{name}: checksum mismatch for {source_name}")
                target_relative = PurePosixPath(str(target_name))
                if target_relative.is_absolute() or ".." in target_relative.parts:
                    raise ValueError(f"{name}: unsafe package path {target_name!r}")
                target = payload.joinpath(*target_relative.parts)
                write_payload_file(target, data, 0o755)
                if data[:4] == b"\x7fELF":
                    validate_elf(target, staging)
                elif not data[:160].startswith(b"#!"):
                    raise ValueError(
                        f"{name}: {source_name} is not an ELF binary or script"
                    )

            for source_name, target_name in package.get("binary_files", {}).items():
                data = read_archive_file(binary_archive, str(source_name))
                target_relative = PurePosixPath(str(target_name))
                if target_relative.is_absolute() or ".." in target_relative.parts:
                    raise ValueError(f"{name}: unsafe package path {target_name!r}")
                write_payload_file(payload.joinpath(*target_relative.parts), data, 0o644)

            for source_name, target_name in package.get("files", {}).items():
                data = read_archive_file(
                    source_archive, f"{package['root']}/{source_name}"
                )
                target_relative = PurePosixPath(str(target_name))
                if target_relative.is_absolute() or ".." in target_relative.parts:
                    raise ValueError(f"{name}: unsafe package path {target_name!r}")
                mode = 0o755 if target_relative.parts[:2] == ("usr", "bin") else 0o644
                write_payload_file(payload.joinpath(*target_relative.parts), data, mode)

            license_target = payload / str(package["license_path"])
            write_payload_file(
                license_target,
                read_archive_file(
                    source_archive,
                    f"{package['root']}/{package['license_source']}",
                ),
                0o644,
            )

            for directory in (payload, *payload.rglob("*")):
                if directory.is_dir() and not directory.is_symlink():
                    directory.chmod(0o755)

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
            temporary_archive = archive_path.with_name(f".{archive_path.name}.tmp")
            try:
                subprocess.run(
                    [
                        str(args.arkpkg_build.resolve()),
                        str(package_dir),
                        "--output",
                        str(temporary_archive),
                    ],
                    check=True,
                )
                archive_size = temporary_archive.stat().st_size
                if archive_size > MAX_ARCHIVE_BYTES:
                    raise ValueError(
                        f"{archive_path.name}: archive is {archive_size} bytes; "
                        f"maximum supported size is {MAX_ARCHIVE_BYTES} bytes"
                    )
                os.replace(temporary_archive, archive_path)
            finally:
                temporary_archive.unlink(missing_ok=True)
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
