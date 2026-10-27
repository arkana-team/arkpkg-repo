"""Copy cached build sources into private snapshots before verification and use."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import urllib.request
from pathlib import Path


def trusted_cache(cache: Path) -> Path:
    # Check the original entries: resolving first would hide untrusted symlinks.
    cache = cache.absolute()
    for directory in (*reversed(cache.parents), cache):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()):
            raise ValueError(f"unsafe source cache directory: {directory}")
        # Sticky ancestors such as /tmp protect entries owned by the builder.
        if info.st_mode & 0o022 and not (
            directory != cache and info.st_mode & stat.S_ISVTX
        ):
            raise ValueError(
                f"source cache directory is writable by others: {directory}"
            )
    return cache


def source_snapshot(
    cache: Path,
    snapshots: Path,
    filename: str,
    url: str,
    expected_sha256: str,
    *,
    user_agent: str,
    timeout: int,
) -> Path:
    cache = trusted_cache(cache)
    snapshots = trusted_cache(snapshots)
    archive_path = cache / filename
    snapshot = snapshots / filename
    downloaded = False
    with snapshot.open("xb") as output:
        try:
            source = archive_path.open("rb")
        except FileNotFoundError:
            request = urllib.request.Request(url, headers={"User-Agent": user_agent})
            source = urllib.request.urlopen(request, timeout=timeout)
            downloaded = True
        with source:
            shutil.copyfileobj(source, output)
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {filename}: {digest}")

    if downloaded:
        # Publish without following a pre-existing symlink or truncating a hardlink.
        descriptor, temporary = tempfile.mkstemp(prefix=".source-", dir=cache)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as output, snapshot.open("rb") as source:
                shutil.copyfileobj(source, output)
            temporary_path.replace(archive_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return snapshot
