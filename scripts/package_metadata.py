"""Bound logical ARKPKG metadata reads before decoding dependency archives."""

import tarfile

MAX_METADATA_SIZE = 64 * 1024  # Includes sparse TAR holes.


def read_metadata_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo
) -> str | None:
    if not 0 <= member.size <= MAX_METADATA_SIZE:
        raise ValueError(f"ARKPKG metadata exceeds {MAX_METADATA_SIZE} bytes")
    stream = archive.extractfile(member)
    if stream is None:
        return None
    with stream:
        metadata = stream.read(MAX_METADATA_SIZE + 1)
    if len(metadata) > MAX_METADATA_SIZE:
        raise ValueError(f"ARKPKG metadata exceeds {MAX_METADATA_SIZE} bytes")
    return metadata.decode("utf-8")
