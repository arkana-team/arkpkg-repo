#!/usr/bin/env python3
"""Audit package archives and test a full install/verify/remove transaction."""

from __future__ import annotations

import argparse
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import lz4.frame

MAX_METADATA_BYTES = 1024 * 1024  # Logical size, including sparse TAR holes.
REQUIRED_FIELDS = {"Name", "Description", "Version", "Arch"}
PACKAGE_ATOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
VERSION = re.compile(r"(?:[0-9]+:)?[A-Za-z0-9][-A-Za-z0-9._+~]*\Z")
DEPENDENCY = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._+-]*)(?:\s*(?:>=|<=|=|>|<)\s*(\S+))?\Z"
)


def version_segments(version: str) -> tuple[int, list[tuple[str, int | str]]]:
    epoch = 0
    if ":" in version:
        raw_epoch, version = version.split(":", 1)
        epoch = int(raw_epoch)

    segments: list[tuple[str, int | str]] = []
    for index, part in enumerate(version.split("~")):
        if index:
            segments.append(("tilde", ""))
        for token in re.split(r"[._-]", part):
            if not token:
                continue
            if token.isdigit():
                segments.append(("number", int(token)))
            else:
                segments.append(("string", token))
    return epoch, segments


def compare_versions(left: str, right: str) -> int:
    left_epoch, left_segments = version_segments(left)
    right_epoch, right_segments = version_segments(right)
    if left_epoch != right_epoch:
        return (left_epoch > right_epoch) - (left_epoch < right_epoch)

    for index in range(max(len(left_segments), len(right_segments))):
        left_segment = left_segments[index] if index < len(left_segments) else None
        right_segment = right_segments[index] if index < len(right_segments) else None
        if left_segment == right_segment:
            continue
        if left_segment is None:
            if right_segment[0] == "tilde":
                return 1
            if right_segment[0] == "number" and right_segment[1] == 0:
                continue
            return 1 if right_segment[0] == "string" else -1
        if right_segment is None:
            if left_segment[0] == "tilde":
                return -1
            if left_segment[0] == "number" and left_segment[1] == 0:
                continue
            return -1 if left_segment[0] == "string" else 1
        if left_segment[0] == "tilde" or right_segment[0] == "tilde":
            return -1 if left_segment[0] == "tilde" else 1
        if left_segment[0] == right_segment[0]:
            return (left_segment[1] > right_segment[1]) - (
                left_segment[1] < right_segment[1]
            )
        return 1 if left_segment[0] == "number" else -1
    return 0


def inspect_archive(path: Path) -> tuple[dict[str, str], list[str], list[str]]:
    metadata: dict[str, str] | None = None
    dependencies: list[str] = []
    provides: list[str] = []
    payload_paths: set[str] = set()
    has_package_tree = False

    with (
        lz4.frame.open(path, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r|") as archive,
    ):
        for member in archive:
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"{path.name}: unsafe archive path {member.name!r}")
            normalized = "/".join(
                part for part in member_path.parts if part not in {"", "."}
            )
            if normalized == "package" or normalized.startswith("package/"):
                has_package_tree = True
            if normalized == "ARKPKG" and member.isfile():
                if metadata is not None:
                    raise ValueError(f"{path.name}: multiple root ARKPKG files")
                if not 0 <= member.size <= MAX_METADATA_BYTES:
                    raise ValueError(f"{path.name}: ARKPKG metadata size exceeds limit")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"{path.name}: unreadable ARKPKG metadata")
                data = stream.read(MAX_METADATA_BYTES + 1)
                if len(data) > MAX_METADATA_BYTES:
                    raise ValueError(f"{path.name}: ARKPKG metadata size exceeds limit")
                fields: dict[str, str] = {}
                current_section = ""
                for line in data.decode("utf-8").splitlines():
                    key, separator, value = line.partition(":")
                    if separator:
                        current_section = key.strip()
                        if current_section in REQUIRED_FIELDS:
                            fields[current_section] = value.strip()
                        elif current_section == "Dependencies" and value.strip():
                            dependencies.append(value.strip())
                        elif current_section == "Provides" and value.strip():
                            provides.append(value.strip())
                    elif line.strip() and current_section == "Dependencies":
                        dependencies.append(line.strip())
                    elif line.strip() and current_section == "Provides":
                        provides.append(line.strip())
                metadata = fields
            if normalized.startswith("package/") and (
                member.isfile() or member.issym() or member.islnk()
            ):
                relative = normalized.removeprefix("package/")
                if relative in payload_paths:
                    raise ValueError(f"{path.name}: duplicate payload path {relative}")
                payload_paths.add(relative)

    if metadata is None:
        raise ValueError(f"{path.name}: missing root ARKPKG metadata")
    missing = REQUIRED_FIELDS.difference(metadata)
    if missing:
        raise ValueError(f"{path.name}: missing fields {', '.join(sorted(missing))}")
    if not has_package_tree or not payload_paths:
        raise ValueError(f"{path.name}: package payload is empty")
    if not PACKAGE_ATOM.fullmatch(metadata["Name"]):
        raise ValueError(f"{path.name}: invalid package name {metadata['Name']!r}")
    if not VERSION.fullmatch(metadata["Version"]):
        raise ValueError(f"{path.name}: invalid version {metadata['Version']!r}")
    if not PACKAGE_ATOM.fullmatch(metadata["Arch"]):
        raise ValueError(f"{path.name}: invalid architecture {metadata['Arch']!r}")
    return metadata, dependencies, provides


def package_dependencies(
    packages: dict[str, tuple[Path, str, list[str], list[str]]],
) -> dict[str, set[str]]:
    provider_map: dict[str, str] = {name: name for name in packages}
    for name, (_, _, _, provides) in packages.items():
        for item in provides:
            provided_name, separator, _ = item.partition("=")
            if separator:
                provider_map.setdefault(provided_name.strip(), name)

    dependencies: dict[str, set[str]] = {name: set() for name in packages}
    for name, (_, _, requirements, _) in packages.items():
        for requirement in requirements:
            match = DEPENDENCY.fullmatch(requirement)
            if match is None:
                raise ValueError(f"{name}: invalid dependency {requirement!r}")
            target = provider_map.get(match.group(1))
            if target is None:
                raise ValueError(f"{name}: unresolved dependency {requirement!r}")
            if target != name:
                dependencies[name].add(target)

    return dependencies


def package_order(
    dependencies: dict[str, set[str]],
) -> list[str]:
    pending = {name: set(values) for name, values in dependencies.items()}
    order: list[str] = []
    while pending:
        ready = sorted(name for name, values in pending.items() if not values)
        if not ready:
            raise ValueError(f"package dependency cycle: {pending}")
        order.extend(ready)
        for name in ready:
            pending.pop(name)
        for values in pending.values():
            values.difference_update(ready)
    return order


def transaction_order(
    order: list[str], dependencies: dict[str, set[str]], requested: list[str] | None
) -> list[str]:
    if requested is None:
        return order
    missing = sorted(set(requested).difference(dependencies))
    if missing:
        raise ValueError(f"unknown transaction packages: {', '.join(missing)}")
    selected: set[str] = set()
    pending = list(requested)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        pending.extend(dependencies[name])
    return [name for name in order if name in selected]


def run_package_manager(
    tool: Path,
    root: Path,
    action: str,
    package: str,
    archive: Path | None = None,
) -> None:
    command = [str(tool), "--root", str(root), action]
    command.append(str(archive) if archive is not None else package)
    if action in {"install", "remove"}:
        command.append("-y")
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        details = result.stderr[-3000:]
        raise RuntimeError(f"arkpkg {action} failed for {package}:\n{details}")


def test_repository(
    repo_root: Path,
    tool: Path,
    requested: list[str] | None = None,
    audit_only: bool = False,
) -> None:
    packages: dict[str, tuple[Path, str, list[str], list[str]]] = {}
    package_arches: dict[str, str] = {}
    owners: dict[str, str] = {}
    archives = sorted((repo_root / "packages").glob("*.ark"))
    if not archives:
        raise ValueError("packages/ contains no .ark archives")

    for path in archives:
        metadata, dependencies, provides = inspect_archive(path)
        name = metadata["Name"]
        if name in packages:
            if package_arches[name] != metadata["Arch"]:
                raise ValueError(f"multiple architectures for package name: {name}")
            existing_version = packages[name][1]
            version_order = compare_versions(metadata["Version"], existing_version)
            if version_order == 0:
                raise ValueError(
                    f"duplicate package version and architecture: "
                    f"{name}-{metadata['Version']}-{metadata['Arch']}"
                )
            if version_order > 0:
                packages[name] = (
                    path,
                    metadata["Version"],
                    dependencies,
                    provides,
                )
        else:
            packages[name] = (path, metadata["Version"], dependencies, provides)
            package_arches[name] = metadata["Arch"]
        with (
            lz4.frame.open(path, "rb") as compressed,
            tarfile.open(fileobj=compressed, mode="r|") as archive,
        ):
            for member in archive:
                normalized = "/".join(
                    part
                    for part in PurePosixPath(member.name).parts
                    if part not in {"", "."}
                )
                if not normalized.startswith("package/") or not (
                    member.isfile() or member.issym() or member.islnk()
                ):
                    continue
                relative = normalized.removeprefix("package/")
                previous = owners.setdefault(relative, name)
                if previous != name:
                    raise ValueError(
                        f"payload collision: {relative} belongs to {previous} and {name}"
                    )

    dependencies = package_dependencies(packages)
    order = package_order(dependencies)
    install_order = transaction_order(order, dependencies, requested)
    edge_count = sum(map(len, dependencies.values()))
    total_bytes = sum(path.stat().st_size for path in archives)
    print(
        f"Audited {len(archives)} archives for {len(packages)} packages, "
        f"{len(owners)} unique payload paths, "
        f"{edge_count} dependency edges, {total_bytes} archive bytes",
        flush=True,
    )
    if audit_only:
        print("PASS: archive, payload ownership, and dependency audit", flush=True)
        return
    print(
        f"Testing package transaction for {len(install_order)} packages",
        flush=True,
    )

    with tempfile.TemporaryDirectory(prefix="arkpkg-repo-test-") as temporary:
        root = Path(temporary) / "root"
        root.mkdir()
        for index, name in enumerate(install_order, 1):
            run_package_manager(tool, root, "install", name, packages[name][0])
            if index % 25 == 0 or index == len(install_order):
                print(f"Installed {index}/{len(install_order)} packages", flush=True)
        for index, name in enumerate(install_order, 1):
            run_package_manager(tool, root, "verify", name)
            if index % 50 == 0 or index == len(install_order):
                print(f"Verified {index}/{len(install_order)} packages", flush=True)
        for index, name in enumerate(reversed(install_order), 1):
            run_package_manager(tool, root, "remove", name)
            if index % 25 == 0 or index == len(install_order):
                print(f"Removed {index}/{len(install_order)} packages", flush=True)
        result = subprocess.run(
            [str(tool), "--root", str(root), "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode or result.stdout.strip():
            raise RuntimeError("package database is not empty after reverse removal")

    print(
        f"PASS: all {len(install_order)} selected packages installed, verified, and removed"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--arkpkg", type=Path, required=True)
    transaction = parser.add_mutually_exclusive_group()
    transaction.add_argument(
        "--transaction-packages",
        nargs="+",
        metavar="NAME",
        help="install, verify, and remove these packages and their dependencies",
    )
    transaction.add_argument(
        "--audit-only",
        action="store_true",
        help="audit every archive without running package transactions",
    )
    args = parser.parse_args()
    test_repository(
        args.repo_root.resolve(),
        args.arkpkg.resolve(),
        requested=args.transaction_packages,
        audit_only=args.audit_only,
    )


if __name__ == "__main__":
    main()
