"""Bound TAR parsing and logical metadata reads in dependency archives."""

import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MAX_METADATA_SIZE = 64 * 1024  # Includes sparse TAR holes.
MAX_TAR_EXTENSION_BYTES = 1024 * 1024  # Cumulative, including block padding.
MAX_TAR_EXTENSION_DEPTH = 32
MAX_TAR_PARSER_BYTES = 2 * MAX_TAR_EXTENSION_BYTES  # Also covers sparse maps/headers.


class _ParserReader:
    def __init__(self, source, archive: tarfile.TarFile):
        self.source = source
        self.archive = archive

    def read(self, size: int) -> bytes:
        remaining = getattr(
            self.archive, "_dependency_parser_bytes_remaining", MAX_TAR_PARSER_BYTES
        )
        if not 0 <= size <= remaining:
            raise ValueError("TAR parser data exceeds limit")
        data = self.source.read(size)
        self.archive._dependency_parser_bytes_remaining = remaining - len(data)
        return data

    def tell(self) -> int:
        return self.source.tell()


class _DependencyTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        extension = self.type in {
            tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
            tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
        }
        depth = getattr(archive, "_dependency_extension_depth", 0)
        if extension:
            # Validate raw sizes before tarfile buffers or decodes the body.
            remaining = getattr(
                archive, "_dependency_extension_bytes_remaining", MAX_TAR_EXTENSION_BYTES
            )
            if self.size < 0 or self._block(self.size) > remaining:
                raise ValueError("TAR extension data exceeds limit")
            if depth >= MAX_TAR_EXTENSION_DEPTH:
                raise ValueError("TAR extension nesting exceeds limit")
            archive._dependency_extension_bytes_remaining = remaining - self._block(self.size)
            archive._dependency_extension_depth = depth + 1

        # Sparse maps are parsed here too. Share one budget through recursive
        # extension processing; ordinary payload reads/skips happen outside it.
        source = archive.fileobj
        if not isinstance(source, _ParserReader):
            archive.fileobj = _ParserReader(source, archive)
        try:
            return super()._proc_member(archive)
        finally:
            archive.fileobj = source
            archive._dependency_extension_depth = depth


@contextmanager
def open_dependency_archive(archive_path: Path) -> Iterator[tarfile.TarFile]:
    import lz4.frame

    with (
        lz4.frame.open(archive_path, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r|", tarinfo=_DependencyTarInfo) as archive,
    ):
        yield archive


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
