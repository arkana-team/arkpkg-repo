#!/usr/bin/env python3
"""Build ArkanaOS application packages from its staging root and official Firefox."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


FIREFOX_VERSION = "158.0"
FIREFOX_URL = (
    "https://archive.mozilla.org/pub/firefox/releases/158.0/linux-x86_64/en-US/"
    "firefox-158.0.tar.xz"
)
FIREFOX_SHA512 = (
    "da90b8c7c6f391935508f99a54d730e5141044c0ce0e915900ecb3f208e840f10a4cf8d38"
    "d76852335e6c436bbe2a16e1bf72945fe067340ad94d9ae98f41c59"
)
FIREFOX_ARCHIVE_BYTES = 90_552_004
FIREFOX_ICON = "firefox/browser/chrome/icons/default/default128.png"
PACKAGES = ("arkcfg", "arkpkg", "fastfetch", "firefox", "git", "vim")


class PackageTree:
    def __init__(self, staging: Path, package_root: Path) -> None:
        self.staging = staging
        self.package_root = package_root
        self.hardlinks: dict[tuple[int, int], Path] = {}

    def add(self, relative: str | Path, *, required: bool = True) -> None:
        relative_path = Path(relative)
        source = self.staging / relative_path
        destination = self.package_root / relative_path
        if not source.exists() and not source.is_symlink():
            if required:
                raise FileNotFoundError(f"missing staged package input: {source}")
            return
        if destination.exists() or destination.is_symlink():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)

        if source.is_symlink():
            destination.symlink_to(os.readlink(source))
        elif source.is_dir():
            destination.mkdir()
            for child in sorted(source.iterdir(), key=lambda item: item.name):
                self.add(child.relative_to(self.staging))
            shutil.copystat(source, destination, follow_symlinks=False)
        elif source.is_file():
            stat = source.stat()
            inode = (stat.st_dev, stat.st_ino)
            if stat.st_nlink > 1 and inode in self.hardlinks:
                target = os.path.relpath(self.hardlinks[inode], destination.parent)
                destination.symlink_to(target)
            else:
                shutil.copy2(source, destination)
                if stat.st_nlink > 1:
                    self.hardlinks[inode] = destination
        else:
            raise ValueError(f"unsupported staged package input: {source}")

    def add_glob(self, pattern: str) -> None:
        for source in sorted(self.staging.glob(pattern)):
            self.add(source.relative_to(self.staging), required=False)


def read_field(recipe: Path, name: str) -> str:
    for line in recipe.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == name:
            return value.strip()
    raise ValueError(f"{recipe} has no {name} field")


def sha512_file(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def obtain_firefox_archive(
    archive_path: Path | None, temporary_dir: Path
) -> Path:
    if archive_path is None:
        archive_path = temporary_dir / f"firefox-{FIREFOX_VERSION}.tar.xz"
        request = urllib.request.Request(
            FIREFOX_URL, headers={"User-Agent": "arkpkg-repo-package-builder/1"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            with archive_path.open("wb") as output:
