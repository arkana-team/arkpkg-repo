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
from urllib.parse import quote, urlsplit

import lz4.frame

PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
PACKAGE_ATOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
PACKAGE_VERSION = re.compile(r"(?:[0-9]+:)?[A-Za-z0-9][-A-Za-z0-9._+~]*\Z")
DEFAULT_DOWNLOAD_BASE_URL = (
    "https://raw.githubusercontent.com/arkana-team/arkpkg-repo/main"
)

PAGE_STYLE = """
:root {
  color-scheme: light;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #202b3b;
  background: #d9dbde;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  --line: #9ea5aa;
  --muted: #647184;
  --accent: #3159b8;
  --panel: #cbd0d3;
}
* { box-sizing: border-box; }
body { margin: 0; min-width: 20rem; background: #d9dbde; line-height: 1.45; }
a { color: #3159b8; }
a:hover { color: #1d3e8a; }
button, input, select { font: inherit; }
button:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible {
  outline: 2px solid #3159b8;
  outline-offset: 2px;
}
.site-header { border-bottom: 2px solid #626a70; background: linear-gradient(180deg, #f2f3f4 0%, #c7cbce 100%); box-shadow: inset 0 1px 0 #fff, 0 3px 4px #42494f70; }
.masthead, .primary-nav, main, .site-footer { width: min(82rem, calc(100% - 2rem)); margin-inline: auto; }
.masthead { min-height: 4.5rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.brand { display: flex; align-items: baseline; gap: .8rem; color: #202b3b; text-decoration: none; }
.brand strong { color: #202b3b; font-size: 1.65rem; letter-spacing: -.055em; }
.brand span { color: var(--muted); font-size: .82rem; }
.utility-links { display: flex; gap: 1rem; font-size: .82rem; }
.primary-nav { display: flex; align-items: end; min-height: 2.5rem; gap: .3rem; border-bottom: 1px solid var(--line); }
.primary-nav a { min-width: 5rem; padding: .48rem .9rem; border: 2px solid; border-color: #fff #626a70 #626a70 #fff; border-bottom: 0; border-radius: 2px 2px 0 0; background: linear-gradient(180deg, #eef0f1 0%, #c9cdd0 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px 0 0 #92999f; color: #4a586b; font-size: .85rem; font-weight: 650; text-align: center; text-decoration: none; }
.primary-nav a:hover { border-color: #fff #535b61 #535b61 #fff; background: linear-gradient(180deg, #f7f8f8 0%, #d6d9dc 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px 0 0 #858d93; }
.primary-nav a[aria-current="page"] { border: 2px solid; border-color: #626a70 #fff #fff #626a70; border-bottom: 0; background: #d2d5d8; box-shadow: inset 2px 2px 3px #666e7480, inset -1px 0 0 #fff; color: var(--accent); }
main { padding: 1rem 0 2rem; min-height: 70vh; }
.breadcrumb { margin: 0 0 .8rem; color: var(--muted); font-size: .78rem; }
h1 { margin: .3rem 0 .25rem; font-size: 1.45rem; font-weight: 600; }
.intro { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .7rem 1.5rem; padding: .7rem; border: 2px solid; border-color: #fff #626a70 #626a70 #fff; border-radius: 2px; background: linear-gradient(180deg, #e8eaec 0%, #c9cdd0 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #969da3, 2px 2px 0 #abb1b5; }
.intro p { margin: .2rem 0; color: #515960; font-size: .9rem; }
.repo-command { padding: .38rem .6rem; border: 3px solid; border-color: #69727a #fff #fff #69727a; border-radius: 2px; background: #d4d7da; box-shadow: inset 2px 2px 3px #4e565f99, inset -1px -1px 0 #fff; color: #34445e; font-size: .8rem; overflow-wrap: anywhere; }
.stats-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .55rem; margin: .9rem 0; }
.stat { display: flex; align-items: baseline; gap: .5rem; padding: .5rem .7rem; border: 3px solid; border-color: #fff #596269 #596269 #fff; border-radius: 2px; background: linear-gradient(180deg, #f1f2f3 0%, #c6cace 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #858d93, 3px 3px 0 #aab0b4, 4px 5px 5px #42494f60; }
.stat strong { color: #263956; font-size: 1.05rem; }
.stat span { color: var(--muted); font-size: .78rem; }
.catalogue-layout { display: grid; grid-template-columns: 12.5rem minmax(0, 1fr); gap: .9rem; align-items: start; }
.category-panel, .catalogue-panel, .details-panel { border: 3px solid; border-color: #fff #4f585f #4f585f #fff; border-radius: 2px; background: var(--panel); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #737b82, 0 0 0 1px #92999f, 4px 4px 0 #aeb4b9, 5px 6px 7px #343a4055; overflow: hidden; }
.panel-heading { margin: 0; padding: .65rem .75rem; border-bottom: 2px solid #626a70; background: linear-gradient(180deg, #eceeef 0%, #c2c7ca 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #969da3; color: #3e4853; font-size: .83rem; font-weight: 700; }
.category-list { display: grid; gap: .2rem; margin: 0; padding: .4rem; list-style: none; }
.category-list button { display: flex; width: 100%; justify-content: space-between; gap: .5rem; padding: .28rem .4rem; border: 3px solid; border-color: #fff #555e66 #555e66 #fff; border-radius: 2px; background: linear-gradient(180deg, #f2f3f4 0%, #c4c8cb 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #858d93, 1px 1px 0 #9ba2a8, 2px 3px 2px #42494f60; color: #26323a; text-align: left; cursor: pointer; font-size: .82rem; }
.category-list button:hover { border-color: #fff #454e54 #454e54 #fff; background: linear-gradient(180deg, #fafafa 0%, #d5d8da 100%); }
.category-list button:active, .category-list button[aria-pressed="true"] { transform: translate(1px, 1px); border-color: #555e66 #fff #fff #555e66; background: #c2c6c9; color: #24499d; font-weight: 700; box-shadow: inset 2px 2px 3px #596168a0, inset -1px -1px 0 #fff, inset 3px 0 var(--accent); }
.category-count { color: #687078; font-variant-numeric: tabular-nums; }
.catalogue-tools { display: flex; align-items: end; justify-content: space-between; gap: .75rem; padding: .65rem; border-bottom: 2px solid #626a70; background: linear-gradient(180deg, #e7e9eb 0%, #c2c7ca 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px 0 0 #90979d; }
.search-field { flex: 1; min-width: 12rem; }
.search-field label { display: block; margin-bottom: .22rem; color: #4c545b; font-size: .76rem; font-weight: 700; }
.search-field input, .mobile-category { width: 100%; min-height: 2.1rem; padding: .38rem .55rem; border: 3px solid; border-color: #596269 #fff #fff #596269; border-radius: 2px; background: #fafafa; box-shadow: inset 3px 3px 4px #42494f80, inset -1px -1px 0 #fff; }
.result-count { margin: 0; color: #5c646b; font-size: .78rem; white-space: nowrap; }
.mobile-category { display: none; }
.mobile-category-label { display: none; color: #4c545b; font-size: .76rem; font-weight: 700; }
.table-wrap { overflow: auto; max-height: 34rem; border: 2px solid; border-color: #596269 #fff #fff #596269; background: #e4e6e8; box-shadow: inset 1px 1px 2px #555e6670, inset -1px -1px 0 #fff; }
table { width: 100%; border-collapse: collapse; font-size: .78rem; }
thead { position: sticky; top: 0; z-index: 1; }
th { padding: .42rem .5rem; border-right: 1px solid #a3aaaf; border-bottom: 2px solid #626a70; background: linear-gradient(180deg, #eceeef 0%, #c7cbce 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #92999f; color: #34445e; text-align: left; white-space: nowrap; }
td { padding: .35rem .5rem; border-bottom: 1px solid #e0e2e3; vertical-align: top; }
tbody tr:nth-child(even) { background: #e9ebed; }
tbody tr:hover { background: #e3e8ef; }
tbody tr.is-selected { background: #dce4f5; }
th button { padding: .16rem .38rem; border: 2px solid; border-color: #fff #69727a #69727a #fff; border-radius: 2px; background: linear-gradient(180deg, #f4f5f6 0%, #c9cdd0 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #a2a8ad, 1px 1px 0 #858d93; color: inherit; cursor: pointer; font: inherit; font-weight: 700; }
th button:hover { background: linear-gradient(180deg, #fff 0%, #d7dadd 100%); }
th button:active, th[aria-sort="ascending"] button, th[aria-sort="descending"] button { transform: translate(1px, 1px); border-color: #69727a #fff #fff #69727a; background: linear-gradient(180deg, #c3c7ca 0%, #d7dadd 100%); box-shadow: inset 2px 2px 2px #50586090, inset -1px -1px 0 #fff; }
th button span { color: #687078; }
.package-select { display: block; padding: 0; border: 0; background: none; color: #3159b8; text-align: left; cursor: pointer; font-weight: 700; }
.package-select:hover { text-decoration: underline; }
.package-summary { display: block; max-width: 29rem; margin-top: .12rem; color: #5b6369; font-size: .73rem; font-weight: 400; line-height: 1.35; }
.repo-type { display: block; margin-top: .1rem; color: #687078; font-size: .68rem; }
.download-link { display: inline-block; padding: .18rem .42rem; border: 3px solid; border-color: #fff #555e66 #555e66 #fff; border-radius: 2px; background: linear-gradient(180deg, #f2f3f4 0%, #c4c8cb 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #858d93, 1px 1px 0 #9ba2a8, 2px 3px 2px #42494f60; color: #284b98; text-decoration: none; white-space: nowrap; }
.download-link:hover { border-color: #fff #454e54 #454e54 #fff; background: linear-gradient(180deg, #fafafa 0%, #d5d8da 100%); }
.download-link:active { transform: translate(1px, 1px); border-color: #555e66 #fff #fff #555e66; background: #c2c6c9; box-shadow: inset 2px 2px 3px #596168a0, inset -1px -1px 0 #fff; }
.details-panel { margin-top: .8rem; background: #e9ebed; }
.details-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .6rem .75rem; border-bottom: 2px solid #737b82; background: linear-gradient(180deg, #e5e7e9 0%, #c9cdd1 100%); box-shadow: inset 1px 1px 0 #fff, inset -1px -1px 0 #aeb4b9; }
.details-header h2 { margin: 0; font-size: .9rem; }
.details-panel > p { margin: .7rem; color: #4f585f; font-size: .84rem; }
.details-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0; margin: 0; border-top: 1px solid #e0e2e3; }
.details-grid > div { min-width: 0; padding: .5rem .65rem; border-right: 1px solid #e0e2e3; border-bottom: 1px solid #e0e2e3; }
.details-grid dt { margin-bottom: .16rem; color: #6a7278; font-size: .7rem; font-weight: 700; text-transform: uppercase; }
.details-grid dd { margin: 0; font-size: .78rem; overflow-wrap: anywhere; }
.details-grid ul { margin: 0; padding-left: 1rem; }
.details-grid code { font-size: .7rem; }
.site-footer { padding: .7rem 0 1.5rem; color: #687078; font-size: .75rem; }
[hidden] { display: none !important; }
@media (max-width: 760px) {
  .masthead, .primary-nav, main, .site-footer { width: min(100% - 1rem, 82rem); }
  .masthead { min-height: 4rem; }
  .brand { display: block; }
  .brand span { display: block; }
  .utility-links { gap: .55rem; }
  main { padding: .8rem; }
  .catalogue-layout { grid-template-columns: minmax(0, 1fr); }
  .category-panel { display: none; }
  .mobile-category-label { display: block; }
  .mobile-category { display: block; }
  .catalogue-tools { align-items: stretch; flex-direction: column; }
  .result-count { white-space: normal; }
  .stats-strip { grid-template-columns: 1fr; }
  .details-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .table-wrap { max-height: 29rem; }
}
@media (max-width: 420px) {
  .utility-links { flex-direction: column; gap: .1rem; text-align: right; }
  .primary-nav a { min-width: 0; flex: 1; padding-inline: .5rem; }
  .details-grid { grid-template-columns: 1fr; }
}
"""

PAGE_SCRIPT = """
(() => {
  const search = document.querySelector("#package-search");
  const table = document.querySelector("#package-table");
  const body = table.querySelector("tbody");
  const count = document.querySelector("#package-count");
  const headers = [...table.querySelectorAll("th[data-sort-key]")];
  const rows = [...body.querySelectorAll("tr[data-package-row]")];
  const noResults = body.querySelector("[data-no-results]");
  const categoryButtons = [...document.querySelectorAll("[data-category-filter]")];
  const mobileCategory = document.querySelector("#mobile-category");
  let activeCategory = "all";
  let sortKey = "name";
  let sortDirection = 1;

  function updateResults() {
    const query = search.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {
      const matchesCategory = activeCategory === "all"
        || row.dataset.category === activeCategory;
      row.hidden = !matchesCategory
        || !row.dataset.search.toLocaleLowerCase().includes(query);
      if (!row.hidden) visible += 1;
    }
    noResults.hidden = rows.length === 0 || visible > 0;
    count.textContent = rows.length === 0
      ? "0 packages available"
      : visible + " of " + rows.length + " packages shown";
  }

  function selectCategory(category) {
    activeCategory = category;
    for (const button of categoryButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.categoryFilter === category));
    }
    mobileCategory.value = category;
    updateResults();
  }

  function renderLink(container, url, label) {
    container.replaceChildren();
    if (!url) {
      container.textContent = "not listed";
      return;
    }
    const link = document.createElement("a");
    link.href = url;
    link.rel = "noopener noreferrer";
    link.textContent = label;
    container.append(link);
  }

  function showDetails(row) {
    for (const item of rows) item.classList.toggle("is-selected", item === row);
    document.querySelector("#details-title").textContent = row.dataset.name;
    document.querySelector("#details-summary").textContent = row.dataset.description;
    document.querySelector("#details-version").textContent = row.dataset.version;
    document.querySelector("#details-architecture").textContent = row.dataset.arch;
    document.querySelector("#details-author").textContent = row.dataset.author || "not listed";
    document.querySelector("#details-repo-type").textContent = row.dataset.gitRepoType;
    document.querySelector("#details-checksum").textContent = row.dataset.sha256;
    renderLink(document.querySelector("#details-repository"), row.dataset.gitRepo,
      row.dataset.gitRepo);
    renderLink(document.querySelector("#details-author-link"), row.dataset.gitAuthorUrl,
      row.dataset.gitAuthorUrl);
    renderLink(document.querySelector("#details-download"), row.dataset.downloadUrl,
      "download .ark");

    const packagers = document.querySelector("#details-packagers");
    packagers.replaceChildren();
    const list = document.createElement("ul");
    for (const packager of JSON.parse(row.dataset.packagers)) {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.href = packager.url;
      link.rel = "noopener noreferrer";
      link.textContent = packager.name;
      item.append(link);
      list.append(item);
    }
    packagers.append(list);
  }

  function sortBy(key) {
    if (key === sortKey) {
      sortDirection *= -1;
    } else {
      sortKey = key;
      sortDirection = 1;
    }
    const column = headers.findIndex((header) => header.dataset.sortKey === key);
    rows.sort((left, right) => {
      const a = left.cells[column].textContent.trim();
      const b = right.cells[column].textContent.trim();
      return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" })
        * sortDirection;
    });
    body.append(...rows, noResults);
    for (const header of headers) {
      const active = header.dataset.sortKey === sortKey;
      header.setAttribute("aria-sort", active
        ? (sortDirection === 1 ? "ascending" : "descending")
        : "none");
      header.querySelector("span").textContent = active
        ? (sortDirection === 1 ? "↑" : "↓")
        : "↕";
    }
    updateResults();
  }

  search.addEventListener("input", updateResults);
  for (const button of categoryButtons) {
    button.addEventListener("click", () => selectCategory(button.dataset.categoryFilter));
  }
  mobileCategory.addEventListener("change", () => selectCategory(mobileCategory.value));
  for (const button of table.querySelectorAll("button[data-sort-key]")) {
    button.addEventListener("click", () => sortBy(button.dataset.sortKey));
  }
  for (const button of table.querySelectorAll("button[data-select-package]")) {
    button.addEventListener("click", () => showDetails(button.closest("tr")));
  }
  document.querySelector("#search-tab").addEventListener("click", () => search.focus());
  updateResults();
})();
"""

CATEGORIES = (
    ("meta", "meta packages"),
    ("applications", "applications"),
    ("desktop", "desktop"),
    ("development", "development"),
    ("drivers", "drivers"),
    ("networking", "networking"),
    ("system", "system"),
    ("libraries", "libraries"),
    ("web-services", "web services"),
)

DEVELOPMENT_PACKAGES = {
    "autoconf", "automake", "binutils", "bison", "cargo", "cmake", "gcc",
    "gdb", "git", "go", "make", "meson", "ninja", "rustc",
}
SYSTEM_PACKAGES = {
    "arkcfg", "arkpkg", "base-files", "bash", "btrfs-progs", "coreutils",
    "cryptsetup", "dash", "device-mapper", "diffutils", "dosfstools",
    "e2fsprogs", "efibootmgr", "efivar", "file", "findutils", "grub",
    "grep", "gzip", "initramfs-tools", "kmod", "linux", "login", "lvm2",
    "man-db", "mount", "passwd", "procps", "sed", "shadow", "sudo",
    "systemd", "systemd-sysv", "sysvinit-utils", "tar", "util-linux",
    "which", "xz-tools", "zstd-tools",
}


def package_category(name: str, description: str) -> str:
    name = name.lower()
    description = description.lower()
    text = name + " " + description
    if name.endswith("-meta") or "meta-package" in description or "metapackage" in description:
        return "meta"
    if (
        name.endswith(("-devel", "-dev", "-headers"))
        or name in DEVELOPMENT_PACKAGES
        or "development headers" in description
        or "development files" in description
    ):
        return "development"
    if (
        name.startswith("lib")
        or name.endswith(("-libs", "-runtime-libs", "-library"))
        or "shared librar" in description
        or "runtime library" in description
    ):
        return "libraries"
    if (
        name.startswith(("firmware-", "printer-driver-", "xserver-xorg-input-", "xserver-xorg-video-"))
        or name.endswith("-firmware")
        or re.search(r"\bdriver\b", text)
        and not any(
            excluded in text
            for excluded in ("compiler driver", "provider driver", "driver manager", "driver information")
        )
    ):
        return "drivers"
    if (
        re.search(r"\b(?:web|database|mail) server\b", description)
        or name.startswith(("apache", "caddy", "lighttpd", "mariadb", "nginx", "postgresql", "redis", "tomcat"))
    ):
        return "web-services"
    if (
        re.search(r"\b(?:network|networking|dns|ssh|ftp|http|https|wireless|vpn)\b", text)
        or name.startswith(("aria2", "curl", "dhcp", "iperf", "netcat", "openssh", "rsync", "socat", "wget"))
    ):
        return "networking"
    if (
        name in SYSTEM_PACKAGES
        or re.search(
            r"\b(?:bootloader|init system|filesystem tools|user account management|system (?:administration|manager|tools|utilities?))\b",
            text,
        )
    ):
        return "system"
    if (
        re.search(r"\b(?:desktop environment|window manager|display manager|x11|x window|bitmap font|icon theme)\b", text)
        or name.startswith(("dillo", "fvwm", "font-", "fltk", "gtk", "motif", "nscde", "qt", "xfce", "xorg", "xterm", "xpdf", "xosview", "xlock", "xkb", "xcursor"))
    ):
        return "desktop"
    return "applications"


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
    if not PACKAGE_VERSION.fullmatch(metadata["Version"]):
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


def validate_download_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"invalid download base URL: {error}") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise ValueError(
            "download base URL must be HTTPS without credentials, query, or fragment"
        )
    return value.rstrip("/")


def validate_https_url(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"invalid {field}: {error}") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
    ):
        raise ValueError(f"{field} must be HTTPS without credentials")
    return value


def validate_package_metadata(
    metadata: object, package_names: set[str]
) -> dict[str, dict[str, object]]:
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise ValueError("package metadata must use schema version 1")
    packages = metadata.get("packages")
    if not isinstance(packages, dict):
        raise TypeError("package metadata must contain a packages object")
    missing = package_names.difference(packages)
    unknown = set(packages).difference(package_names)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing: {', '.join(sorted(missing)[:8])}")
        if unknown:
            details.append(f"unknown: {', '.join(sorted(unknown)[:8])}")
        raise ValueError("package metadata coverage mismatch (" + "; ".join(details) + ")")

    validated = {}
    expected_fields = {
        "author", "git_repo", "git_repo_type", "git_author_url", "packagers"
    }
    valid_repo_types = {"upstream", "packaging", "arkpkg-recipe"}
    for name, item in packages.items():
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise ValueError(f"invalid source metadata fields for package {name!r}")
        author = item["author"]
        repo_type = item["git_repo_type"]
        if author is not None and (not isinstance(author, str) or not author.strip()):
            raise ValueError(f"package author must be non-empty or null for {name!r}")
        if not isinstance(repo_type, str) or repo_type not in valid_repo_types:
            raise ValueError(f"invalid git repository type for package {name!r}")
        git_author_url = item["git_author_url"]
        if author is None and git_author_url is not None:
            raise ValueError(f"author profile requires a verified author for {name!r}")
        if git_author_url is not None:
            git_author_url = validate_https_url(
                git_author_url, f"git_author_url for {name}"
            )
        packagers = item["packagers"]
        if not isinstance(packagers, list) or not packagers:
            raise ValueError(f"package packagers must be a non-empty list for {name!r}")
        validated_packagers = []
        packager_names = set()
        for packager in packagers:
            if (
                not isinstance(packager, dict)
                or set(packager) != {"name", "url"}
                or not isinstance(packager["name"], str)
                or not packager["name"].strip()
            ):
                raise ValueError(f"invalid packager entry for package {name!r}")
            packager_name = packager["name"].strip()
            if packager_name in packager_names:
                raise ValueError(f"duplicate packager for package {name!r}")
            packager_names.add(packager_name)
            validated_packagers.append(
                {
                    "name": packager_name,
                    "url": validate_https_url(
                        packager["url"], f"packager URL for {name}"
                    ),
                }
            )
        validated[name] = {
            "author": author.strip() if isinstance(author, str) else None,
            "git_repo": validate_https_url(item["git_repo"], f"git_repo for {name}"),
            "git_repo_type": repo_type,
            "git_author_url": git_author_url,
            "packagers": validated_packagers,
        }
    return validated


def build_site(
    repo_root: Path,
    site_dir: Path,
    download_base_url: str = DEFAULT_DOWNLOAD_BASE_URL,
) -> None:
    download_base_url = validate_download_base_url(download_base_url)
    package_root = repo_root / "packages"
    if package_root.is_symlink():
        raise ValueError("packages/ must not be a symbolic link")
    site_packages = site_dir / "packages"
    site_dir.mkdir(parents=True, exist_ok=True)
    if site_packages.is_symlink():
        site_packages.unlink()
    elif site_packages.exists():
        shutil.rmtree(site_packages)

    entries = []
    package_names = set()
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
        package_names.add(metadata["Name"])
        relative_path = archive_path.relative_to(repo_root)
        digest = sha256_file(archive_path)
        entries.append(
            {
                "name": metadata["Name"],
                "version": metadata["Version"],
                "arch": metadata["Arch"],
                "description": metadata["Description"],
                "file": relative_path.as_posix(),
                "download_url": (
                    f"{download_base_url}/"
                    f"{quote(relative_path.as_posix(), safe='/._+-')}"
                ),
                "sha256": digest,
            }
        )

    metadata_path = repo_root / "data" / "package-metadata.json"
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise ValueError("data/package-metadata.json must be a regular file")
    package_metadata = validate_package_metadata(
        json.loads(metadata_path.read_text(encoding="utf-8")), package_names
    )
    for item in entries:
        item.update(package_metadata[item["name"]])

    entries.sort(key=lambda item: (item["name"].lower(), item["version"], item["arch"]))
    (site_dir / "index.json").write_text(
        json.dumps({"schema_version": 1, "packages": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    def link(url: str, label: str) -> str:
        return (
            f'<a href="{html.escape(url, quote=True)}" '
            f'rel="noopener noreferrer">{html.escape(label)}</a>'
        )

    def link_label(url: str) -> str:
        parsed = urlsplit(url)
        label = f"{parsed.hostname}{parsed.path}".rstrip("/")
        if parsed.query:
            label += f"?{parsed.query}"
        return label

    def repository_link(item: dict[str, object]) -> str:
        return (
            link(str(item["git_repo"]), link_label(str(item["git_repo"])))
            + f'<small class="repo-type">{html.escape(str(item["git_repo_type"]))}</small>'
        )

    category_counts = {"all": len(entries)}
    for item in entries:
        item["site_category"] = package_category(item["name"], item["description"])
        category = item["site_category"]
        category_counts[category] = category_counts.get(category, 0) + 1

    package_rows = []
    for item in entries:
        search_values = [
            str(item[key] or "")
            for key in (
                "name", "version", "arch", "author", "git_repo",
                "git_author_url", "description", "git_repo_type",
            )
        ] + [packager["name"] for packager in item["packagers"]]
        attributes = {
            "category": str(item["site_category"]),
            "search": " ".join(search_values),
            "name": str(item["name"]),
            "version": str(item["version"]),
            "arch": str(item["arch"]),
            "description": str(item["description"]),
            "author": str(item["author"] or "not listed"),
            "git-repo": str(item["git_repo"]),
            "git-repo-type": str(item["git_repo_type"]),
            "git-author-url": str(item["git_author_url"] or ""),
            "packagers": json.dumps(item["packagers"], separators=(",", ":")),
            "sha256": str(item["sha256"]),
            "download-url": str(item["download_url"]),
        }
        data_attributes = " ".join(
            f'data-{key}="{html.escape(value, quote=True)}"'
            for key, value in attributes.items()
        )
        package_rows.append(
            f"<tr data-package-row {data_attributes}>"
            "<td><button class=\"package-select\" type=\"button\" "
            f"data-select-package aria-label=\"show details for {html.escape(str(item['name']), quote=True)}\">"
            f"{html.escape(str(item['name']))}</button></td>"
            f"<td>{html.escape(str(item['version']))}</td>"
            f"<td>{html.escape(str(item['arch']))}</td>"
            f"<td>{html.escape(str(item['author'] or 'not listed'))}</td>"
            f"<td>{repository_link(item)}</td>"
            f"<td>{html.escape(str(item['description']))}</td>"
            f"<td><a class=\"download-link\" href=\"{html.escape(str(item['download_url']), quote=True)}\" "
            f"rel=\"noopener noreferrer\">download .ark</a></td></tr>"
        )
    if not package_rows:
        package_rows.append('<tr><td colspan="7">no packages published yet.</td></tr>')

    category_items = [("all", "all packages"), *CATEGORIES]
    category_items = [
        (key, label)
        for key, label in category_items
        if key == "all" or category_counts.get(key, 0) > 0
    ]
    category_buttons = "".join(
        f'<li><button type="button" data-category-filter="{key}" '
        f'aria-pressed="{"true" if key == "all" else "false"}">'
        f"<span>{html.escape(label)}</span><span class=\"category-count\">"
        f"{category_counts.get(key, 0)}</span></button></li>"
        for key, label in category_items
    )
    category_options = "".join(
        f'<option value="{key}">{html.escape(label)} '
        f'({category_counts.get(key, 0)})</option>'
        for key, label in category_items
    )
    columns = (
        ("name", "name"),
        ("version", "version"),
        ("arch", "architecture"),
        ("author", "author"),
        ("git_repo", "source"),
        ("description", "summary"),
    )
    table_headers = "".join(
        '<th data-sort-key="{0}" aria-sort="{2}"><button type="button" '
        'data-sort-key="{0}">{1} <span aria-hidden="true">{3}</span></button></th>'.format(
            key,
            label,
            "ascending" if key == "name" else "none",
            "↑" if key == "name" else "↕",
        )
        for key, label in columns
    ) + '<th scope="col">package</th>'

    package_count = len(entries)
    architecture_count = len({str(item["arch"]) for item in entries})
    source_count = len({str(item["git_repo"]) for item in entries})
    stylesheet_version = hashlib.sha256(PAGE_STYLE.encode("utf-8")).hexdigest()[:12]
    (site_dir / "catalogue.css").write_text(PAGE_STYLE, encoding="utf-8")
    (site_dir / "catalogue.js").write_text(PAGE_SCRIPT, encoding="utf-8")
    (site_dir / "index.html").write_text(
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="theme-color" content="#3159b8">'
        f'<link rel="stylesheet" href="catalogue.css?v={stylesheet_version}">'
        '<title>arkpkg software catalogue</title></head><body>'
        '<header class="site-header"><div class="masthead">'
        '<a class="brand" href="#packages"><strong>arkpkg</strong>'
        '<span>software catalogue for arkanaOS</span></a>'
        '<div class="utility-links"><a href="index.json">package index (json)</a>'
        '<a href="https://github.com/arkana-team/arkpkg-repo">source repository</a></div></div>'
        '<nav class="primary-nav" aria-label="main navigation">'
        '<a href="#packages" aria-current="page">packages</a>'
        '<a id="search-tab" href="#package-search">search</a>'
        '<a href="#statistics">statistics</a></nav></header>'
        '<main id="packages"><p class="breadcrumb">home &nbsp;›&nbsp; packages</p>'
        '<section class="intro"><div><h1>package catalogue</h1>'
        '<p>Browse the software available for arkanaOS and install packages with arkpkg.</p></div>'
        '<code class="repo-command">arkpkg repo add arkanaOS https://arkana-team.github.io/arkpkg-repo</code></section>'
        '<section id="statistics" class="stats-strip" aria-label="catalogue statistics">'
        f'<div class="stat"><strong>{package_count}</strong><span>packages</span></div>'
        f'<div class="stat"><strong>{architecture_count}</strong><span>architectures</span></div>'
        f'<div class="stat"><strong>{source_count}</strong><span>upstream projects</span></div></section>'
        '<div class="catalogue-layout"><aside class="category-panel" aria-label="package categories">'
        '<h2 class="panel-heading">categories</h2><ul class="category-list">'
        f'{category_buttons}</ul></aside><section class="catalogue-panel" aria-label="packages">'
        '<div class="catalogue-tools"><label class="mobile-category-label" for="mobile-category">category</label>'
        f'<select class="mobile-category" id="mobile-category">{category_options}</select>'
        '<div class="search-field"><label for="package-search">search packages</label>'
        '<input id="package-search" type="search" autocomplete="off" '
        'placeholder="name, version, author, source or description"></div>'
        f'<p id="package-count" class="result-count" aria-live="polite">{package_count} packages available</p></div>'
        '<div class="table-wrap"><table id="package-table"><thead><tr>'
        f'{table_headers}</tr></thead><tbody>'
        f'{"".join(package_rows)}'
        '<tr data-no-results hidden><td colspan="7">no packages match these filters.</td></tr>'
        '</tbody></table></div></section></div>'
        '<section class="details-panel" aria-labelledby="details-title">'
        '<div class="details-header"><h2 id="details-title">package details</h2>'
        '<span id="details-download"></span></div>'
        '<p id="details-summary">Select a package to view its source, author, packagers and checksum.</p>'
        '<dl class="details-grid">'
        '<div><dt>version</dt><dd id="details-version">—</dd></div>'
        '<div><dt>architecture</dt><dd id="details-architecture">—</dd></div>'
        '<div><dt>upstream author</dt><dd id="details-author">—</dd></div>'
        '<div><dt>source repository</dt><dd id="details-repository">—</dd></div>'
        '<div><dt>author profile</dt><dd id="details-author-link">—</dd></div>'
        '<div><dt>repository type</dt><dd id="details-repo-type">—</dd></div>'
        '<div><dt>arkpkg packagers</dt><dd id="details-packagers">—</dd></div>'
        '<div><dt>sha256</dt><dd><code id="details-checksum">—</code></dd></div>'
        '</dl></section></main>'
        '<footer class="site-footer">arkpkg package index · '
        '<a href="index.json">machine-readable catalogue</a></footer>'
        '<script src="catalogue.js" defer></script></body></html>\n',
        encoding="utf-8",
    )
    (site_dir / ".nojekyll").touch()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--download-base-url",
        default=DEFAULT_DOWNLOAD_BASE_URL,
        help="HTTPS root that serves immutable package archive paths",
    )
    args = parser.parse_args()
    build_site(
        args.repo_root.resolve(), args.site_dir.resolve(), args.download_base_url
    )


if __name__ == "__main__":
    main()
