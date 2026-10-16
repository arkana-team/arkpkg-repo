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
