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
