#!/usr/bin/env python3
"""Build the public-domain X.Org misc bitmap-font package."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

FONT_PACKAGES = (
    (
        "font-misc-misc",
        "1.1.3",
        "79abe361f58bb21ade9f565898e486300ce1cc621d5285bec26e14b6a8618fed",
        "misc",
        ("COPYING",),
    ),
    (
        "font-bh-75dpi",
        "1.0.4",
        "6026d8c073563dd3cbb4878d0076eed970debabd21423b3b61dd90441b9e7cda",
        "75dpi",
        ("COPYING", "LU_LEGALNOTICE"),
    ),
    (
        "font-bh-100dpi",
        "1.0.4",
        "fd8f5efe8491faabdd2744808d3d4eafdae5c83e617017c7fddd2716d049ab1e",
        "100dpi",
        ("COPYING", "LU_LEGALNOTICE"),
    ),
)
FONT_EXTRA_SOURCES = {
    "font-bh-75dpi": (
        (
            "font-bh-lucidatypewriter-75dpi",
            "1.0.4",
            "864e2c39ac61f04f693fc2c8aaaed24b298c2cd40283cec12eee459c5635e8f5",
        ),
    ),
    "font-bh-100dpi": (
        (
            "font-bh-lucidatypewriter-100dpi",
            "1.0.4",
            "76ec09eda4094a29d47b91cf59c3eba229c8f7d1ca6bae2abbb3f925e33de8f2",
        ),
    ),
}
ALIAS_NAME = "font-alias"
ALIAS_VERSION = "1.0.6"
ALIAS_URL = f"https://xorg.freedesktop.org/archive/individual/font/{ALIAS_NAME}-{ALIAS_VERSION}.tar.xz"
ALIAS_SHA256 = "f6a5e968b616e728a981fd1fd9ed5fe2ca0fe18fbe072b1888cb3f71b701e4c0"
FONT_BASE_URL = "https://xorg.freedesktop.org/archive/individual/font"
FONT_NAME = re.compile(rb"^FONT\s+([^\r\n]+)", re.MULTILINE)


def obtain_source(
    cache: Path, name: str, version: str, url: str, expected_sha256: str
) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive_path = cache / f"{name}-{version}.tar.xz"
    if not archive_path.is_file():
        request = urllib.request.Request(
            url, headers={"User-Agent": "arkpkg-repo-font-builder/1"}
        )
        with (
            urllib.request.urlopen(request, timeout=90) as response,
            archive_path.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {archive_path.name}: {digest}")
    return archive_path


def stage_fonts(
    source: Path,
    staging: Path,
    name: str,
    version: str,
    font_directory: str,
    license_files: tuple[str, ...],
    extra_sources: tuple[tuple[Path, str, str], ...] = (),
) -> int:
    fonts = staging / f"usr/share/fonts/{font_directory}"
    licenses = staging / f"usr/share/licenses/{name}"
    fonts.mkdir(parents=True, exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, str]] = []
    copied = 0

    source_specs = [(source, name, version, False)] + [
        (path, extra_name, extra_version, True)
        for path, extra_name, extra_version in extra_sources
    ]
    for source_path, source_name, source_version, is_extra in source_specs:
        with tarfile.open(source_path, "r:xz") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or len(path.parts) != 2:
                    continue
                if (
                    path.parts[0] != f"{source_name}-{source_version}"
                    or not member.isfile()
                ):
                    continue
                if path.name.endswith(".bdf"):
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError(f"could not read font source {member.name}")
                    data = stream.read()
                    match = FONT_NAME.search(data)
                    if match is None:
                        raise ValueError(f"font has no XLFD name: {member.name}")
                    if (fonts / path.name).exists():
                        raise ValueError(
                            f"font filename collision in {name}: {path.name}"
                        )
                    (fonts / path.name).write_bytes(data)
                    entries.append((path.name, match.group(1).decode("ascii")))
                    copied += 1
                elif path.name in {"COPYING", "LU_LEGALNOTICE"}:
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError(
                            f"could not read upstream font license {path.name}"
                        )
                    license_name = (
                        f"{path.name}-{source_name}" if is_extra else path.name
                    )
                    (licenses / license_name).write_bytes(stream.read())

    if not entries or any(not (licenses / item).is_file() for item in license_files):
        raise ValueError(f"{name} archive is missing fonts or license files")
    entries.sort()
    (fonts / "fonts.dir").write_text(
        str(len(entries))
        + "\n"
        + "".join(f"{filename} {font_name}\n" for filename, font_name in entries),
        encoding="ascii",
    )
    return copied


def stage_aliases(source: Path, staging: Path) -> int:
    aliases_root = staging / "usr/share/fonts"
    licenses = staging / f"usr/share/licenses/{ALIAS_NAME}"
    licenses.mkdir(parents=True, exist_ok=True)
    overrides = {
        "fixed": "-Misc-Fixed-Medium-R-SemiCondensed--13-120-75-75-C-60-ISO10646-1",
        "variable": "-Misc-Fixed-Medium-R-Normal--13-120-75-75-C-80-ISO10646-1",
        "6x13": "-Misc-Fixed-Medium-R-SemiCondensed--13-120-75-75-C-60-ISO10646-1",
        "6x13bold": "-Misc-Fixed-Bold-R-SemiCondensed--13-120-75-75-C-60-ISO10646-1",
        "8x13": "-Misc-Fixed-Medium-R-Normal--13-120-75-75-C-80-ISO10646-1",
        "8x13bold": "-Misc-Fixed-Bold-R-Normal--13-120-75-75-C-80-ISO10646-1",
        "9x15": "-Misc-Fixed-Medium-R-Normal--15-140-75-75-C-90-ISO10646-1",
        "9x15bold": "-Misc-Fixed-Bold-R-Normal--15-140-75-75-C-90-ISO10646-1",
        "9x18": "-Misc-Fixed-Medium-R-Normal--18-120-100-100-C-90-ISO10646-1",
        "9x18bold": "-Misc-Fixed-Bold-R-Normal--18-120-100-100-C-90-ISO10646-1",
        "10x20": "-Misc-Fixed-Medium-R-Normal--20-200-75-75-C-100-ISO10646-1",
        "12x24": "-Misc-Fixed-Medium-R-Normal--20-200-75-75-C-100-ISO10646-1",
        "-*-helvetica-medium-r-*-*-*-120-*-*-*-*-*-*": "-B&H-Lucida-Medium-R-Normal-Sans-12-120-75-75-P-79-ISO10646-1",
        "-*-helvetica-bold-r-normal--*-120-*-*-*-*-iso8859-1": "-B&H-Lucida-Bold-R-Normal-Sans-12-120-75-75-P-79-ISO10646-1",
        "-*-courier-medium-r-normal--*-120-*-*-*-*-iso8859-1": "-B&H-LucidaTypewriter-Medium-R-Normal-Sans-12-120-75-75-M-70-ISO10646-1",
        "-*-courier-bold-r-normal--*-120-*-*-*-*-iso8859-1": "-B&H-LucidaTypewriter-Bold-R-Normal-Sans-12-120-75-75-M-70-ISO10646-1",
    }
    copied = 0
    with tarfile.open(source, "r:xz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or len(path.parts) != 3:
                continue
            if (
                path.parts[0] != f"{ALIAS_NAME}-{ALIAS_VERSION}"
                or path.name != "fonts.alias"
                or not member.isfile()
            ):
                continue
            directory = path.parts[1]
            if directory not in {"misc", "75dpi", "100dpi", "cyrillic"}:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"could not read alias file {member.name}")
            lines = stream.read().decode("utf-8").splitlines()
            destination = aliases_root / directory
            destination.mkdir(parents=True, exist_ok=True)
            if directory == "misc":
                names = set(overrides)
                lines = [
                    line
                    for line in lines
                    if not line.split() or line.split()[0] not in names
                ]
                lines = [f"{name} {font}" for name, font in overrides.items()] + lines
            (destination / "fonts.alias").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            copied += 1
        license_member = archive.getmember(f"{ALIAS_NAME}-{ALIAS_VERSION}/COPYING")
        license_stream = archive.extractfile(license_member)
        if license_stream is None:
            raise ValueError("could not read upstream font-alias license")
        (licenses / "COPYING").write_bytes(license_stream.read())
    if copied != 4:
        raise ValueError(f"expected four upstream alias files, found {copied}")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-cache", type=Path, default=Path("/tmp/arkpkg-xorg-sources")
    )
    parser.add_argument(
        "--staging", type=Path, default=Path("/tmp/arkpkg-font-staging")
    )
    parser.add_argument("--arkpkg-build", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    staging = args.staging.resolve()
    if staging.exists():
        shutil.rmtree(staging)
    alias_staging = staging / ALIAS_NAME
    font_stagings = []
    font_counts = {}
    cache = args.source_cache.resolve()
    for name, version, digest, font_directory, license_files in FONT_PACKAGES:
        source = obtain_source(
            cache,
            name,
            version,
            f"{FONT_BASE_URL}/{name}-{version}.tar.xz",
            digest,
        )
        package_staging = staging / name
        extra_sources = []
        for extra_name, extra_version, extra_digest in FONT_EXTRA_SOURCES.get(name, ()):
            extra_sources.append(
                (
                    obtain_source(
                        cache,
                        extra_name,
                        extra_version,
                        f"{FONT_BASE_URL}/{extra_name}-{extra_version}.tar.xz",
                        extra_digest,
                    ),
                    extra_name,
                    extra_version,
                )
            )
        count = stage_fonts(
            source,
            package_staging,
            name,
            version,
            font_directory,
            license_files,
            tuple(extra_sources),
        )
        font_stagings.append((name, package_staging))
        font_counts[name] = count
    alias_source = obtain_source(
        cache, ALIAS_NAME, ALIAS_VERSION, ALIAS_URL, ALIAS_SHA256
    )
    alias_count = stage_aliases(alias_source, alias_staging)
    for package_name, package_staging in ((ALIAS_NAME, alias_staging), *font_stagings):
        command = [
            sys.executable,
            str(repo_root / "scripts/build-arkana-packages.py"),
            "--staging",
            str(package_staging),
            "--arkpkg-build",
            str(args.arkpkg_build.resolve()),
            "--packages",
            package_name,
        ]
        if args.output_dir is not None:
            command.extend(("--output-dir", str(args.output_dir.resolve())))
        subprocess.run(command, check=True)
    for package_name, count in font_counts.items():
        print(f"Staged {count} upstream BDF fonts in {package_name}")
    print(f"Staged {alias_count} upstream X.Org font alias sets")


if __name__ == "__main__":
    main()
