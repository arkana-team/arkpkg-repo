#!/usr/bin/env python3
"""Build the static index served by GitHub Pages from packages/*.ark files."""

import argparse
import hashlib
import html
import json
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath

import lz4.frame

PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
PACKAGE_ATOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")


def read_metadata(archive_path: Path) -> dict[str, str]:
    metadata = None
    has_package_tree = False
    with (
        lz4.frame.open(archive_path, "rb") as compressed,
        tarfile.open(fileobj=compressed, mode="r|") as archive,
    ):
        for member in archive:
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"unsafe path in archive: {member.name!r}")
            normalized_name = "/".join(
                part for part in member_path.parts if part not in {"", "."}
            )
            if normalized_name == "package" or normalized_name.startswith("package/"):
                has_package_tree = True
            if normalized_name == "ARKPKG" and member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    break
                values: dict[str, str] = {}
                for line in stream.read().decode("utf-8").splitlines():
                    key, separator, value = line.partition(":")
                    if separator and key in {"Name", "Description", "Version", "Arch"}:
                        if key in values:
                            raise ValueError(f"duplicate {key} field")
                        values[key] = value.strip()
                required = {"Name", "Description", "Version", "Arch"}
                missing = required.difference(values)
                if missing:
                    raise ValueError(
                        f"missing metadata fields: {', '.join(sorted(missing))}"
                    )
                if metadata is not None:
                    raise ValueError("archive contains multiple root ARKPKG files")
                metadata = values
    if metadata is None:
        raise ValueError("archive does not contain a root ARKPKG metadata file")
    if not has_package_tree:
        raise ValueError("archive does not contain the required package/ tree")
    if not PACKAGE_NAME.fullmatch(metadata["Name"]):
        raise ValueError(f"invalid package name: {metadata['Name']!r}")
    if not PACKAGE_ATOM.fullmatch(metadata["Version"]):
        raise ValueError(f"invalid package version: {metadata['Version']!r}")
    if not PACKAGE_ATOM.fullmatch(metadata["Arch"]):
        raise ValueError(f"invalid package architecture: {metadata['Arch']!r}")
    return metadata


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_site(repo_root: Path, site_dir: Path) -> None:
    package_root = repo_root / "packages"
    if package_root.is_symlink():
        raise ValueError("packages/ must not be a symbolic link")
    site_packages = site_dir / "packages"
    site_dir.mkdir(parents=True, exist_ok=True)
    if site_packages.exists():
        shutil.rmtree(site_packages)
    site_packages.mkdir(parents=True)

    entries = []
    seen = set()
    for archive_path in (
        sorted(package_root.rglob("*.ark")) if package_root.exists() else []
    ):
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError(f"package archive must be a regular file: {archive_path}")
        metadata = read_metadata(archive_path)
        package_key = (metadata["Name"], metadata["Version"], metadata["Arch"])
        if package_key in seen:
            raise ValueError(
                f"duplicate package version and architecture: {package_key}"
            )
        seen.add(package_key)
        relative_path = archive_path.relative_to(repo_root)
        destination = site_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive_path, destination)

        digest = sha256_file(archive_path)
        entries.append(
            {
                "name": metadata["Name"],
                "version": metadata["Version"],
                "arch": metadata["Arch"],
                "description": metadata["Description"],
                "file": relative_path.as_posix(),
                "sha256": digest,
            }
        )

    entries.sort(key=lambda item: (item["name"].lower(), item["version"], item["arch"]))
    (site_dir / "index.json").write_text(
        json.dumps({"schema_version": 1, "packages": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    package_rows = "\n".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(item["name"]),
            html.escape(item["version"]),
            html.escape(item["arch"]),
            html.escape(item["description"]),
        )
        for item in entries
    )
    if not package_rows:
        package_rows = '<tr><td colspan="4">no packages published yet.</td></tr>'
    (site_dir / "index.html").write_text(
        "<!doctype html>\n"
        '<html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>arkpkg package repository</title>"
        "<body><main><h1>arkpkg package repository</h1>"
        "<p>static package repository for arkanaOS. configure it with:</p>"
        "<pre>arkpkg repo add arkanaOS https://arkana-team.github.io/arkpkg-repo</pre>"
        '<p><a href="index.json">package index (json)</a></p>'
        "<table><thead><tr><th>name</th><th>version</th><th>architecture</th>"
        f"<th>description</th></tr></thead><tbody>{package_rows}</tbody></table>"
        "</main></body></html>\n",
        encoding="utf-8",
    )
    (site_dir / ".nojekyll").touch()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    build_site(args.repo_root.resolve(), args.site_dir.resolve())


if __name__ == "__main__":
    main()
