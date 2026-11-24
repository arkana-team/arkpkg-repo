#!/usr/bin/env python3
"""Repack published archives that shipped broken payload entries.

The archive audit in ``scripts/test-packages.py`` rejects three classes of
defect that earlier builder runs produced:

* files under a bin directory that are not executable, so the installed
  command cannot be run;
* pkg-config files whose ``prefix`` still points at the build staging root
  instead of the installed system;
* payload entries owned by a non-root build uid, which leaks the builder's
  account onto the target system.

This helper rewrites only the members it must change, preserves every other
member exactly, and refuses to publish an archive that still fails the
repository audit. Run it from the repository root::

    python3 scripts/repack-defective-archives.py --repo-root .
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import re
import sys
import tarfile
import tempfile
from pathlib import Path

import lz4.frame

# Build roots that must never appear inside installed metadata. The staging
# directory is the sysroot itself, so "…/staging/usr" is installed "/usr"
# and the build prefix has to be removed entirely.
BUILD_PREFIX = re.compile(
    r"(?:/(?:build/[A-Za-z0-9._-]+|tmp/[A-Za-z0-9._-]*arkana[A-Za-z0-9._-]*)"
    r")?/staging(?=/|$)"
)
BIN_DIRECTORIES = ("usr/bin/", "usr/sbin/", "usr/games/", "bin/", "sbin/")
TEXT_METADATA_SUFFIXES = (".pc", ".cmake", ".la")
MAX_TEXT_METADATA_BYTES = 256 * 1024


def normalize_member(member: tarfile.TarInfo, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    """Return the member with safe ownership and repaired content."""
    if member.uid != 0 or member.gid != 0:
        member.uid = 0
        member.gid = 0
        member.uname = "root"
        member.gname = "root"

    name = member.name
    relative = name.removeprefix("package/")
    if (
        relative.startswith(BIN_DIRECTORIES)
        and not member.mode & 0o111
        and member.isfile()
    ):
        # Grant an executable bit wherever the matching read bit is set, so an
        # owner-only script stays owner-only but can still be run.
        member.mode |= (member.mode & 0o444) >> 2

    if (
        member.isfile()
        and relative.endswith(TEXT_METADATA_SUFFIXES)
        and member.size <= MAX_TEXT_METADATA_BYTES
    ):
        text = data.decode("utf-8")
        repaired = BUILD_PREFIX.sub("", text)
        if repaired != text:
            data = repaired.encode("utf-8")
            member.size = len(data)

    return member, data


def repack(archive_path: Path, destination: Path) -> None:
    with (
        lz4.frame.open(archive_path, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r|") as archive,
        lz4.frame.open(destination, "wb") as output,
        tarfile.open(
            fileobj=output, mode="w|", format=tarfile.PAX_FORMAT
        ) as rebuilt,
    ):
        for member in archive:
            data = b""
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"unreadable member: {member.name}")
                data = stream.read()
            member, data = normalize_member(member, data)
            if member.isfile():
                rebuilt.addfile(member, io.BytesIO(data))
            else:
                rebuilt.addfile(member)

    verify(destination)


def load_audit():
    """Import the repository archive audit as a module."""
    audit_path = Path(__file__).resolve().with_name("test-packages.py")
    spec = importlib.util.spec_from_file_location("arkpkg_package_audit", audit_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {audit_path}")
    audit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit
    spec.loader.exec_module(audit)
    return audit


def verify(archive: Path) -> None:
    """Fail if a rewritten archive still violates the repository audit."""
    load_audit().inspect_archive(archive)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--package",
        action="append",
        metavar="ARCHIVE",
        help="archive file name under packages/ to repack; repeat as needed",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    package_root = repo_root / "packages"
    targets = args.package or []
    if not targets:
        raise SystemExit("pass at least one --package ARCHIVE")

    for name in targets:
        archive_name = Path(name)
        if name in {".", ".."} or archive_name.name != name:
            raise SystemExit(
                f"--package expects an archive file name under packages/: {name!r}"
            )
        archive_path = package_root / archive_name
        if not archive_path.is_file() or archive_path.is_symlink():
            raise SystemExit(f"missing regular archive: {archive_path}")
        print(f"Repacking {name}", flush=True)
        # Keep the verified replacement on the archive's filesystem so the
        # final replace stays atomic even when /tmp is a separate mount.
        with tempfile.TemporaryDirectory(
            prefix=".arkpkg-repack-", dir=package_root
        ) as temporary:
            destination = Path(temporary) / name
            repack(archive_path, destination)
            destination.replace(archive_path)


if __name__ == "__main__":
    main()
