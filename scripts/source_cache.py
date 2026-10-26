"""Copy cached build sources into private snapshots before verification and use."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import urllib.request
from pathlib import Path

MAX_SOURCE_BYTES = 512 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024


class _BoundedDigestReader:
    def __init__(self, source, limit: int, digest) -> None:
        self.source = source
        self.limit = limit
        self.digest = digest
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = COPY_CHUNK_BYTES
        if size == 0:
            return b""
        remaining = self.limit - self.total
        data = self.source.read(min(size, remaining + 1))
        if len(data) > remaining:
            raise ValueError(f"source exceeds {self.limit} byte limit")
        self.total += len(data)
        self.digest.update(data)
        return data


def _open_cached(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"cached source is not a regular file: {path}")
        if info.st_size > MAX_SOURCE_BYTES:
            raise ValueError(f"source exceeds {MAX_SOURCE_BYTES} byte limit")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def trusted_cache(cache: Path) -> Path:
    cache = cache.resolve()
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
    created_snapshot = False
    try:
        digest = hashlib.sha256()
        with snapshot.open("xb") as output:
            created_snapshot = True
            try:
                source = _open_cached(archive_path)
            except FileNotFoundError:
                request = urllib.request.Request(
                    url, headers={"User-Agent": user_agent}
                )
                source = urllib.request.urlopen(request, timeout=timeout)
                downloaded = True
            with source:
                bounded = _BoundedDigestReader(source, MAX_SOURCE_BYTES, digest)
                shutil.copyfileobj(bounded, output, COPY_CHUNK_BYTES)
        checksum = digest.hexdigest()
        if checksum != expected_sha256:
            raise ValueError(f"SHA-256 mismatch for {filename}: {checksum}")

        if downloaded:
            # Publish without following a pre-existing symlink or truncating a hardlink.
            descriptor, temporary = tempfile.mkstemp(prefix=".source-", dir=cache)
            temporary_path = Path(temporary)
            try:
                with (
                    os.fdopen(descriptor, "wb") as output,
                    snapshot.open("rb") as source,
                ):
                    shutil.copyfileobj(source, output, COPY_CHUNK_BYTES)
                temporary_path.replace(archive_path)
            finally:
                temporary_path.unlink(missing_ok=True)
        return snapshot
    except BaseException:
        if created_snapshot:
            snapshot.unlink(missing_ok=True)
        raise
