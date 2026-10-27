#!/usr/bin/env python3
"""Repack checksum-pinned Debian CLI binaries into arkpkg archives.

The source manifest records the exact Debian Trixie package files and SHA-256
digests. The packages are unpacked as data only; Debian maintainer scripts are
never executed. Existing arkpkg recipes supply the dependency and license
metadata used by the generated archives.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

ARCHIVE_BASE_URLS = {
    "main": "https://deb.debian.org/debian",
    "security": "https://security.debian.org/debian-security",
}
MANIFEST = Path("data/debian-cli-packages.json")
MAX_ARCHIVE_BYTES = 90_000_000
MAX_METADATA_SIZE = 64 * 1024
PACKAGE_ATOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
NEEDED = re.compile(r"Shared library: \[(.+?)\]")
SONAME = re.compile(r"Library soname: \[(.+?)\]")
LIBRARY_FILE = re.compile(r"lib[^/]+\.so(?:\.[A-Za-z0-9._+-]+)?\Z")
BITMAP_FONT_DIRECTORIES = {
    "xfonts-base": "usr/share/fonts/X11/misc",
    "xfonts-75dpi": "usr/share/fonts/X11/75dpi",
    "xfonts-100dpi": "usr/share/fonts/X11/100dpi",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def source_download_url(source: dict[str, str]) -> str:
    filename = PurePosixPath(source["filename"])
    if filename.is_absolute() or ".." in filename.parts:
        raise ValueError(f"unsafe Debian filename: {source['filename']!r}")
    archive = source.get("archive", "main")
    base_url = ARCHIVE_BASE_URLS.get(archive)
    if base_url is None:
        raise ValueError(f"unsupported Debian archive: {archive!r}")
    return f"{base_url}/{filename.as_posix()}"


def generate_bitmap_font_indexes(payload: Path, package_name: str) -> None:
    """Generate legacy X11 font indexes for Debian's bitmap font payloads."""
    relative_directory = BITMAP_FONT_DIRECTORIES.get(package_name)
    if relative_directory is None:
        return

    font_directory = payload / relative_directory
    if not font_directory.is_dir():
        raise FileNotFoundError(
            f"{package_name}: missing bitmap font directory {relative_directory}"
        )

    tools = {name: shutil.which(name) for name in ("mkfontscale", "mkfontdir")}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise FileNotFoundError(
            f"{package_name}: required font index tool(s) not found: "
            f"{', '.join(missing)}"
        )

    for name in ("mkfontscale", "mkfontdir"):
        tool = tools[name]
        assert tool is not None
        subprocess.run([tool], cwd=font_directory, check=True)

    for filename in ("fonts.scale", "fonts.dir"):
        index = font_directory / filename
        if not index.is_file() or index.is_symlink():
            raise RuntimeError(f"{package_name}: {filename} was not generated")


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(4) == b"\x7fELF"
    except OSError:
        return False


def read_recipe(path: Path) -> tuple[dict[str, str], list[str], list[str]]:
    fields: dict[str, str] = {}
    dependencies: list[str] = []
    provides: list[str] = []
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            section = key.strip()
            if section not in {"Dependencies", "Provides"}:
                fields[section] = value.strip()
            elif section == "Dependencies" and value.strip():
                dependencies.append(value.strip())
            elif section == "Provides" and value.strip():
                provides.extend(item.strip() for item in value.split(",") if item.strip())
        elif line.strip() and section == "Dependencies":
            dependencies.append(line.strip())
        elif line.strip() and section == "Provides":
            provides.extend(item.strip() for item in line.split(",") if item.strip())
    return fields, dependencies, provides


def provider_keys(entries: list[str]) -> list[str]:
    keys: list[str] = []
    for entry in entries:
        provided, separator, target = entry.partition("=")
        provided = provided.strip()
        if provided:
            keys.append(provided)
        target = target.strip()
        if separator and target.startswith("/"):
            filename = PurePosixPath(target).name
            if LIBRARY_FILE.fullmatch(filename):
                keys.append(filename)
    return keys


def repository_owners(
    repo_root: Path, ignored_packages: set[str]
) -> tuple[set[str], dict[str, str]]:
    owned: set[str] = set()
    providers: dict[str, str] = {}
    recipes = repo_root / "packages/recipes"
    for recipe in sorted(recipes.glob("*/ARKPKG")):
        fields, _, provides = read_recipe(recipe)
        name = fields.get("Name", recipe.parent.name)
        providers.setdefault(name, name)
        for key in provider_keys(provides):
            providers.setdefault(key, name)

    for archive_path in sorted((repo_root / "packages").glob("*.ark")):
        archive_name = None
        archive_owned: set[str] = set()
        with (
            lz4.frame.open(archive_path, "rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            for member in archive:
                member_path = PurePosixPath(member.name)
                normalized = "/".join(
                    part for part in member_path.parts if part not in {"", "."}
                )
                if normalized == "ARKPKG" and member.isfile():
                    if member.issparse():
                        raise ValueError(
                            f"{archive_path.name}: ARKPKG metadata must not be sparse"
                        )
                    if not 0 <= member.size <= MAX_METADATA_SIZE:
                        raise ValueError(
                            f"{archive_path.name}: ARKPKG metadata exceeds "
                            f"{MAX_METADATA_SIZE} bytes"
                        )
                    metadata = archive.extractfile(member)
                    if metadata is not None:
                        content = metadata.read(MAX_METADATA_SIZE + 1)
                        if len(content) > MAX_METADATA_SIZE:
                            raise ValueError(
                                f"{archive_path.name}: ARKPKG metadata exceeds "
                                f"{MAX_METADATA_SIZE} bytes"
                            )
                        for line in content.decode("utf-8").splitlines():
                            key, separator, value = line.partition(":")
                            if separator and key.strip() == "Name":
                                archive_name = value.strip()
                                break
                if not (member.isfile() or member.issym() or member.islnk()):
                    continue
                if member_path.parts[:1] == ("package",) and len(member_path.parts) > 1:
                    archive_owned.add("/".join(member_path.parts[1:]))
        if archive_name not in ignored_packages:
            owned.update(archive_owned)
    return owned, providers


def repository_has_package(repo_root: Path, package: dict[str, object]) -> bool:
    """Check a versioned archive while allowing the recipe to track newer builds."""
    name = str(package["name"])
    version = str(package["version"])
    arch = str(package["arch"])
    recipe = repo_root / "packages/recipes" / name / "ARKPKG"
    archive = repo_root / "packages" / f"{name}-{version}-{arch}.ark"
    if not recipe.is_file() or not archive.is_file():
        return False
    recipe_fields, _, _ = read_recipe(recipe)
    if recipe_fields.get("Name") != name or recipe_fields.get("Arch") != arch:
        return False

    archive_fields: dict[str, str] = {}
    with (
        lz4.frame.open(archive, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r|") as package_archive,
    ):
        for member in package_archive:
            if member.name != "ARKPKG" or not member.isfile():
                continue
            if member.issparse() or not 0 <= member.size <= MAX_METADATA_SIZE:
                raise ValueError(f"{archive.name}: invalid ARKPKG metadata size")
            source = package_archive.extractfile(member)
            if source is None:
                raise ValueError(f"{archive.name}: unreadable ARKPKG metadata")
            content = source.read(MAX_METADATA_SIZE + 1)
            if len(content) > MAX_METADATA_SIZE:
                raise ValueError(f"{archive.name}: ARKPKG metadata exceeds limit")
            for line in content.decode("utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    archive_fields[key.strip()] = value.strip()
            break

    return (
        archive_fields.get("Name") == name
        and archive_fields.get("Version") == version
        and archive_fields.get("Arch") == arch
    )


def requested_package_closure(
    repo_root: Path,
    packages: list[dict[str, object]],
    requested: set[str],
) -> set[str]:
    package_by_name = {str(item["name"]): item for item in packages}
    pending = list(requested)
    while pending:
        current = package_by_name[pending.pop()]
        for dependency in current.get("dependencies", []):
            if (
                dependency in package_by_name
                and dependency not in requested
                and not repository_has_package(repo_root, package_by_name[dependency])
            ):
                requested.add(dependency)
                pending.append(dependency)
    return requested


def prepare_source_cache(cache: Path) -> Path:
    """Create a private cache whose path cannot be replaced by another user."""
    cache = cache.absolute()
    owner = os.geteuid()
    for directory in (*reversed(cache.parents), cache):
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, owner}
            or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
        ):
            raise ValueError(f"unsafe source cache directory: {directory}")
        # A trusted sticky parent (such as /tmp) protects our directory entry.
        if directory == cache and (info.st_uid != owner or info.st_mode & 0o077):
            raise ValueError(
                f"source cache must be owned by uid {owner} with mode 0700: {cache}"
            )
    return cache


def obtain_deb(source: dict[str, str], cache: Path) -> Path:
    cache = prepare_source_cache(cache)
    filename = PurePosixPath(source["filename"])
    archive = cache / filename.name
    if (
        not archive.is_symlink()
        and archive.is_file()
        and sha256(archive) == source["sha256"]
    ):
        return archive
    archive.unlink(missing_ok=True)
    request = urllib.request.Request(
        source_download_url(source),
        headers={"User-Agent": "arkpkg-repo-cli-package-builder/1"},
    )
    temporary = None
    try:
        for attempt in range(4):
            try:
                with tempfile.NamedTemporaryFile(
                    dir=cache, prefix=f".{archive.name}.", suffix=".download", delete=False
                ) as output:
                    temporary = Path(output.name)
                    with urllib.request.urlopen(request, timeout=120) as response:
                        shutil.copyfileobj(response, output)
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                    temporary = None
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
        actual = sha256(temporary)
        if actual != source["sha256"]:
            raise ValueError(
                f"SHA-256 mismatch for {filename.name}: expected "
                f"{source['sha256']}, got {actual}"
            )
        temporary.replace(archive)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return archive


def extract_data_archive(deb: Path, work: Path) -> Path:
    members = subprocess.run(
        ["ar", "t", str(deb)], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    data_members = [name for name in members if name.startswith("data.tar")]
    if len(data_members) != 1:
        raise ValueError(f"{deb.name}: expected one data.tar member, got {data_members}")
    data_archive = work / f"{deb.stem}-{data_members[0]}"
    payload = work / f"{deb.stem}-payload"
    if payload.is_dir():
        return payload
    with data_archive.open("wb") as output:
        subprocess.run(["ar", "p", str(deb), data_members[0]], stdout=output, check=True)
    payload.mkdir()
    subprocess.run(
        [
            "tar",
            "--no-same-owner",
            "--same-permissions",
            "-xf",
            str(data_archive),
            "-C",
            str(payload),
        ],
        check=True,
    )
    return payload


def same_entry(left: Path, right: Path) -> bool:
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and left.readlink() == right.readlink()
    if not left.is_file() or not right.is_file():
        return False
    return sha256(left) == sha256(right) and (left.stat().st_mode & 0o7777) == (
        right.stat().st_mode & 0o7777
    )


def merge_payload(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir() and not path.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if not same_entry(path, target):
                raise ValueError(f"conflicting bundled Debian payload path: {relative}")
            continue
        if path.is_symlink():
            target.symlink_to(path.readlink())
        elif path.is_file():
            shutil.copy2(path, target)
        else:
            raise ValueError(f"unsupported Debian payload entry: {relative}")


def relocate_debian_python_packages(payload: Path, staging: Path | None) -> None:
    """Map Debian's dist-packages into the target Python's site-packages."""
    library_dir = payload / "usr/lib"
    source_dirs = sorted(library_dir.glob("python*/dist-packages"))
    if not source_dirs:
        return
    if staging is None:
        raise ValueError(
            "--staging is required to map Debian Python packages into arkanaOS"
        )

    site_dirs = sorted((staging / "usr/lib").glob("python*/site-packages"))
    if len(site_dirs) != 1 or not site_dirs[0].is_dir():
        raise ValueError(
            "expected exactly one target Python site-packages directory in --staging"
        )
    destination = payload / site_dirs[0].relative_to(staging)

    for source in source_dirs:
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"unsafe Debian Python package directory: {source}")
        merge_payload(source, destination)
        shutil.rmtree(source)


def exclude_package_paths(payload: Path, exclusions: list[str], package_name: str) -> None:
    seen: set[str] = set()
    for relative in exclusions:
        path = PurePosixPath(relative)
        if (
            not relative
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != relative
        ):
            raise ValueError(f"{package_name}: unsafe excluded path {relative!r}")
        if relative in seen:
            raise ValueError(f"{package_name}: duplicate excluded path {relative!r}")
        target = payload.joinpath(*path.parts)
        if not target.is_file() and not target.is_symlink():
            raise FileNotFoundError(
                f"{package_name}: expected excluded file is missing: {relative}"
            )
        target.unlink()
        seen.add(relative)


def resolve_payload_path(payload: Path, relative: str, package_name: str) -> Path:
    """Resolve a normalized payload path without following symlink parents."""
    path = PurePosixPath(relative)
    if (
        not relative
        or not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != relative
    ):
        raise ValueError(f"{package_name}: unsafe renamed path {relative!r}")
    target = payload.joinpath(*path.parts)
    if target == payload:
        raise ValueError(f"{package_name}: unsafe renamed path {relative!r}")
    parent = target.parent
    while parent != payload:
        if parent.is_symlink():
            raise ValueError(
                f"{package_name}: renamed path traverses a symlink: {relative}"
            )
        parent = parent.parent
    return target


def rename_package_paths(
    payload: Path, renames: list[list[str]], package_name: str
) -> None:
    """Move package files to nonconflicting names without following symlink parents."""
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()

    for pair in renames:
        if len(pair) != 2:
            raise ValueError(
                f"{package_name}: each renamed path needs a source and target"
            )
        source_name, destination_name = pair
        if source_name == destination_name:
            raise ValueError(f"{package_name}: renamed source and target are identical")
        if source_name in seen_sources or destination_name in seen_destinations:
            raise ValueError(f"{package_name}: duplicate renamed path")
        source = resolve_payload_path(payload, source_name, package_name)
        destination = resolve_payload_path(payload, destination_name, package_name)
        if not source.is_file() and not source.is_symlink():
            raise FileNotFoundError(
                f"{package_name}: expected renamed file is missing: {source_name}"
            )
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(
                f"{package_name}: renamed destination already exists: "
                f"{destination_name}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        seen_sources.add(source_name)
        seen_destinations.add(destination_name)


def rewrite_package_commands(
    payload: Path, rewrites: list[dict[str, object]], package_name: str
) -> None:
    """Update exact command references in selected text files after a rename."""
    for rewrite in rewrites:
        original = rewrite["from"]
        replacement = rewrite["to"]
        paths = rewrite["paths"]
        preserve_contexts = rewrite.get("preserve_contexts", [])
        if (
            not isinstance(original, str)
            or not isinstance(replacement, str)
            or not PACKAGE_ATOM.fullmatch(original)
            or not PACKAGE_ATOM.fullmatch(replacement)
            or not isinstance(paths, list)
            or not paths
            or not isinstance(preserve_contexts, list)
            or any(
                not isinstance(context, str) or not context
                for context in preserve_contexts
            )
            or len(preserve_contexts) != len(set(preserve_contexts))
        ):
            raise ValueError(f"{package_name}: invalid command rewrite")
        pattern = re.compile(
            rb"(?<![A-Za-z0-9_/:$@%])(?<!GNU )"
            + re.escape(original.encode("ascii"))
            + rb"(?![A-Za-z0-9_-])(?=[ \t]*(?:-|[\"'`$|;&><)]|\r?\n|$))"
        )
        preserved_context_counts = dict.fromkeys(preserve_contexts, 0)
        rewritten_files: list[tuple[Path, bytes]] = []
        for relative in paths:
            if not isinstance(relative, str):
                raise TypeError(
                    f"{package_name}: command rewrite path must be a string"
                )
            path = resolve_payload_path(payload, relative, package_name)
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError(
                    f"{package_name}: command rewrite file is missing: {relative}"
                )
            contents = path.read_bytes()
            protected_spans: list[tuple[int, int]] = []
            for context in preserve_contexts:
                encoded_context = context.encode("utf-8")
                occurrences = contents.count(encoded_context)
                if occurrences > 1:
                    raise ValueError(
                        f"{package_name}: preserved command context is ambiguous "
                        f"in {relative}"
                    )
                if occurrences == 1:
                    context_matches = list(pattern.finditer(encoded_context))
                    if len(context_matches) != 1:
                        raise ValueError(
                            f"{package_name}: preserved context must contain one "
                            f"{original} command reference"
                        )
                    start = contents.index(encoded_context)
                    protected_spans.append((start, start + len(encoded_context)))
                    preserved_context_counts[context] += 1

            rewritten_parts: list[bytes] = []
            cursor = 0
            count = 0
            for start, end in sorted(protected_spans):
                if start < cursor:
                    raise ValueError(
                        f"{package_name}: preserved command contexts overlap"
                    )
                rewritten, replacements = pattern.subn(
                    replacement.encode("ascii"), contents[cursor:start]
                )
                rewritten_parts.extend((rewritten, contents[start:end]))
                count += replacements
                cursor = end
            rewritten, replacements = pattern.subn(
                replacement.encode("ascii"), contents[cursor:]
            )
            rewritten_parts.append(rewritten)
            count += replacements
            rewritten = b"".join(rewritten_parts)
            if count == 0:
                raise ValueError(
                    f"{package_name}: command reference not found in {relative}"
                )
            rewritten_files.append((path, rewritten))

        missing_contexts = [
            context
            for context, count in preserved_context_counts.items()
            if count != 1
        ]
        if missing_contexts:
            raise ValueError(
                f"{package_name}: expected each preserved command context once; "
                f"found {len(missing_contexts)} missing or repeated"
            )
        for path, contents in rewritten_files:
            path.write_bytes(contents)


def payload_paths(payload: Path) -> list[str]:
    paths = []
    for path in payload.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(payload).as_posix()
        normalized = PurePosixPath(relative)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError(f"unsafe package payload path: {relative!r}")
        paths.append(relative)
    return paths


def staging_conflicts(payload: Path, paths: list[str], staging: Path) -> list[str]:
    conflicts = []
    for relative in paths:
        package_path = payload / relative
        installed_path = staging / relative
        if (installed_path.exists() or installed_path.is_symlink()) and not same_entry(
            package_path, installed_path
        ):
            conflicts.append(relative)
    return conflicts


def resolve_license_path(payload: Path, relative: str, package_name: str) -> Path:
    """Resolve a license file path while keeping symlinks inside its payload."""
    path = PurePosixPath(relative)
    if (
        not relative
        or not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != relative
    ):
        raise ValueError(f"{package_name}: unsafe license path {relative!r}")
    root = payload.resolve()
    target = (root / path).resolve()
    if target == root or not target.is_relative_to(root) or target.is_dir():
        raise ValueError(f"{package_name}: unsafe license path {relative!r}")
    return target


def license_path_resolves_to(
    payload: Path, license_path: str, source_path: str
) -> bool:
    try:
        target = (payload / license_path).resolve(strict=False).relative_to(
            payload.resolve()
        )
        source = (payload / source_path).resolve(strict=False).relative_to(
            payload.resolve()
        )
    except ValueError:
        return False
    return target == source


def stage_has_library(staging: Path, soname: str) -> bool:
    for directory in (
        "lib",
        "lib64",
        "lib/x86_64-linux-gnu",
        "usr/lib",
        "usr/lib64",
        "usr/lib/x86_64-linux-gnu",
    ):
        candidate = staging / directory / soname
        if candidate.exists() or candidate.is_symlink():
            return True
    return False


def runtime_dependencies(
    payload: Path,
    providers: dict[str, str],
    staging: Path | None,
    package_name: str,
) -> tuple[list[str], list[str]]:
    dependencies: set[str] = set()
    bundled_names = {
        path.name
        for path in payload.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    missing: set[str] = set()
    for binary in sorted(payload.rglob("*")):
        if binary.is_symlink() or not binary.is_file() or not is_elf(binary):
            continue
        result = subprocess.run(
            ["readelf", "-d", str(binary)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            continue
        for line in result.stdout.splitlines():
            if "(NEEDED)" not in line:
                continue
            match = NEEDED.search(line)
            if match is None:
                continue
            soname = match.group(1)
            if soname in bundled_names:
                continue
            provider = providers.get(soname)
            if provider and provider != package_name:
                dependencies.add(provider)
            elif staging is not None and not stage_has_library(staging, soname):
                missing.add(soname)
    return sorted(dependencies), sorted(missing)


def recipe_text(
    package: dict[str, object], dependencies: list[str], provides: list[str]
) -> str:
    lines = [
        f"Name: {package['name']}",
        f"Description: {package['description']}",
        f"Version: {package['version']}",
        f"Arch: {package['arch']}",
        f"URL: {package['url']}",
        f"License: {package['license']}",
        "Maintainer: arkanaOS Team",
    ]
    if dependencies:
        lines.append("Dependencies:")
        lines.extend(f"  {dependency}" for dependency in dependencies)
    if provides:
        lines.append("Provides: " + ", ".join(provides))
    return "\n".join(lines) + "\n"


def command_provides(payload: Path) -> list[str]:
    results: set[str] = set()
    for base in ("usr/bin", "usr/sbin", "bin", "sbin"):
        root = payload / base
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if (path.is_file() or path.is_symlink()) and (
                path.is_symlink() or path.stat().st_mode & 0o111
            ):
                results.add(f"{path.name}=/{base}/{path.name}")

    for path in sorted(payload.rglob("*")):
        if path.is_symlink() or not path.is_file() or not is_elf(path):
            continue
        result = subprocess.run(
            ["readelf", "-d", str(path)], capture_output=True, text=True, check=False
        )
        if result.returncode:
            continue
        for line in result.stdout.splitlines():
            if "(SONAME)" not in line:
                continue
            match = SONAME.search(line)
            if match:
                relative = path.relative_to(payload).as_posix()
                results.add(f"{match.group(1)}=/{relative}")
    return sorted(results)


def build(args: argparse.Namespace) -> None:
    """Build the requested checksum-pinned Debian packages as ark archives."""
    repo_root = args.repo_root.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = manifest["sources"]
    packages = manifest["packages"]
    requested = set(args.packages or [])
    unknown = requested.difference(item["name"] for item in packages)
    if unknown:
        raise ValueError(f"unknown package names: {', '.join(sorted(unknown))}")

    if requested:
        requested = requested_package_closure(repo_root, packages, requested)

    cache = prepare_source_cache(args.source_cache)
    staging = args.staging.resolve() if args.staging else None
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = [item for item in packages if not requested or item["name"] in requested]
    ignored_packages = {item["name"] for item in selected}
    owned, providers = repository_owners(repo_root, ignored_packages)

    with tempfile.TemporaryDirectory(prefix="arkpkg-debian-cli-") as temporary:
        work = Path(temporary)
        for index, package in enumerate(selected, 1):
            name = package["name"]
            recipe_path = repo_root / "packages/recipes" / name / "ARKPKG"
            package_dir = work / name
            payload = package_dir / "package"
            payload.mkdir(parents=True)

            for deb_name in package["debian_packages"]:
                if deb_name not in sources:
                    raise ValueError(f"{name}: missing source metadata for {deb_name}")
                deb = obtain_deb(sources[deb_name], cache)
                extracted = extract_data_archive(deb, work)
                merge_payload(extracted, payload)

            relocate_debian_python_packages(payload, staging)
            rename_package_paths(payload, package.get("renames", []), name)
            rewrite_package_commands(
                payload, package.get("command_rewrites", []), name
            )
            exclude_package_paths(payload, package.get("exclude_paths", []), name)
            generate_bitmap_font_indexes(payload, name)

            for link, target in package.get("symlinks", []):
                link_path = payload / link
                if link_path.exists() or link_path.is_symlink():
                    raise ValueError(f"{name}: refusing to replace payload path {link}")
                link_path.parent.mkdir(parents=True, exist_ok=True)
                link_path.symlink_to(target)

            license_path = package["license_path"]
            license_target = resolve_license_path(payload, license_path, name)
            license_provided_by_dependency = False
            if not license_target.is_file() and "license_source_package" in package:
                license_source_name = package["license_source_package"]
                if license_source_name not in sources:
                    raise ValueError(
                        f"{name}: missing source metadata for license package "
                        f"{license_source_name}"
                    )
                license_deb = obtain_deb(sources[license_source_name], cache)
                license_payload = extract_data_archive(license_deb, work)
                license_source_path = resolve_license_path(
                    license_payload, package["license_source_path"], name
                )
                if not license_source_path.is_file():
                    raise FileNotFoundError(
                        f"{name}: missing license source {package['license_source_path']} "
                        f"from {license_source_name}"
                    )
                if (
                    license_source_name in package.get("dependencies", [])
                    and license_path_resolves_to(
                        payload, license_path, package["license_source_path"]
                    )
                ):
                    license_provided_by_dependency = True
                else:
                    license_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(license_source_path, license_target)
            if not license_target.is_file() and not license_provided_by_dependency:
                raise FileNotFoundError(f"{name}: missing preserved license {license_path}")

            dependencies, missing = runtime_dependencies(payload, providers, staging, name)
            dependencies = sorted(set(dependencies) | set(package.get("dependencies", [])))
            if missing:
                raise ValueError(
                    f"{name}: runtime libraries absent from the ArkanaOS staging root: "
                    + ", ".join(missing)
                )

            paths = payload_paths(payload)
            collisions = sorted(set(paths).intersection(owned))
            if collisions:
                raise ValueError(
                    f"{name}: payload paths already owned by arkpkg-repo: "
                    + ", ".join(collisions[:20])
                )
            if staging is not None:
                base_collisions = staging_conflicts(payload, paths, staging)
                if base_collisions:
                    raise ValueError(
                        f"{name}: payload would replace files from the arkanaOS base: "
                        + ", ".join(base_collisions[:20])
                    )
            if not paths:
                raise ValueError(f"{name}: empty package payload")

            expected_recipe = recipe_text(
                package, dependencies, command_provides(payload)
            )
            if recipe_path.exists():
                current_recipe = recipe_path.read_text(encoding="utf-8")
                tracked = subprocess.run(
                    [
                        "git",
                        "ls-files",
                        "--error-unmatch",
                        str(recipe_path.relative_to(repo_root)),
                    ],
                    cwd=repo_root,
                    capture_output=True,
                    check=False,
                ).returncode == 0
                if (
                    current_recipe != expected_recipe
                    and tracked
                    and not args.allow_recipe_updates
                ):
                    raise ValueError(
                        f"{recipe_path}: refusing to replace a tracked recipe; "
                        "pass --allow-recipe-updates to make the change explicit"
                    )
                if current_recipe != expected_recipe and not args.write_recipes:
                    raise ValueError(
                        f"{recipe_path}: metadata differs from detected package contents; "
                        "review the recipe or rebuild with --write-recipes"
                    )
                if args.write_recipes and current_recipe != expected_recipe:
                    recipe_path.write_text(expected_recipe, encoding="utf-8")
            elif args.write_recipes:
                recipe_path.parent.mkdir(parents=True, exist_ok=True)
                recipe_path.write_text(expected_recipe, encoding="utf-8")
            else:
                raise FileNotFoundError(
                    f"missing {recipe_path}; rerun once with --write-recipes"
                )

            shutil.copy2(recipe_path, package_dir / "ARKPKG")

            version = package["version"]
            arch = package["arch"]
            archive = output_dir / f"{name}-{version}-{arch}.ark"
            temporary_archive = archive.with_name(f".{archive.name}.tmp")
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
                        f"{archive.name}: archive is {archive_size} bytes; "
                        f"GitHub-hosted package files must stay below "
                        f"{MAX_ARCHIVE_BYTES} bytes"
                    )
                os.replace(temporary_archive, archive)
            finally:
                temporary_archive.unlink(missing_ok=True)
            owned.update(paths)
            _, _, package_provides = read_recipe(recipe_path)
            providers.setdefault(name, name)
            for key in provider_keys(package_provides):
                providers.setdefault(key, name)
            print(
                f"Built {index}/{len(selected)} {archive.name} "
                f"({archive.stat().st_size} bytes; {len(paths)} payload paths)",
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST,
        help="package source manifest (relative paths are resolved from --repo-root)",
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--staging", type=Path)
    parser.add_argument("--source-cache", type=Path, default=Path("/tmp/arkpkg-debian-cli-cache"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--packages", nargs="+")
    parser.add_argument("--write-recipes", action="store_true")
    parser.add_argument(
        "--allow-recipe-updates",
        action="store_true",
        help="permit --write-recipes to update tracked package recipes",
    )
    args = parser.parse_args()
    if args.output_dir is None:
        args.output_dir = args.repo_root / "packages"
    return args


if __name__ == "__main__":
    try:
        build(parse_args())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
