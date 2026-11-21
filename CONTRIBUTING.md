# Contributing to arkpkg-repo

Contributions of useful, optional packages and improvements to the repository
tools are welcome. Open a focused pull request against `main`; do not merge your
own contribution unless you have repository permission to do so.

## Repository layout

- `packages/*.ark` contains the archives clients download.
- `packages/recipes/<name>/ARKPKG` contains each package's metadata.
- `data/package-metadata.json` records verified upstream authorship, packagers,
  and Git links shown in the public index.
- `scripts/build-*.py` builds packages from pinned upstream sources or staged
  sysroot files.
- `scripts/build-index.py` creates the static Pages site and `index.json`.
- `scripts/test-build-index.py` checks catalogue rendering, metadata, and
  generated assets; `scripts/test-package-index-tilde.py` covers Debian version
  ordering in the package index.
- `scripts/test-packages.py` checks archive metadata, dependencies, file
  ownership, executable bits, build-time paths in metadata, and
  install/verify/remove behavior.
- `scripts/repack-defective-archives.py` rewrites an already-published archive
  after a builder fix and re-runs the audit before replacing it.
- `.github/workflows/repository-tool-tests.yml` runs every `scripts/test-*.py`
  file on Python 3.11.

## Adding or updating a package

Check existing package names and payload paths first. Add a recipe with accurate
`Name`, `Description`, `Version`, `Arch`, `URL`, `License`, and `Maintainer`
fields. Add `Dependencies` and `Provides` where applicable, with one value per
indented line. Use `any` for architecture-independent data packages and
`x86_64` for architecture-specific packages.

Build output must be a valid `.ark` archive with a root `ARKPKG` file and a
non-empty `package/` payload. Name the archive
`<name>-<version>-<arch>.ark`, matching its metadata. Use an existing helper
where possible; inspect its options with `python3 scripts/build-<helper>.py
--help`. For staged packages, the general builder accepts a sysroot and the
`arkpkg-build` executable, for example:

```sh
python3 scripts/build-arkana-packages.py \
  --staging /path/to/sysroot \
  --arkpkg-build /path/to/arkpkg-build \
  --packages dillo
```

Add or update the package's entry in `data/package-metadata.json` whenever its
archive is added or removed. `author` means the original upstream software
author, not the recipe maintainer; use `null` when the source does not verify an
author. `git_author_url` is that author's public Git profile and must also be
`null` when it cannot be verified. The `packagers` list credits the people or
teams who built and maintain the ArkPkg package; include their names and HTTPS
profile URLs. Use `git_repo` for the upstream Git repository when known, and set
`git_repo_type` to `upstream`, `packaging`, or `arkpkg-recipe` to make the link's
provenance clear. A packaging-repository link points to the source packaging
repository; an `arkpkg-recipe` link points to this repository's recipe when no
source Git repository is published.

Pin upstream versions and verify source checksums in build helpers. Preserve
upstream license files. Keep package payload ownership disjoint: files must not
be shipped by two packages. The `system-docs` archive is generated to exclude
files already owned by other packages.

When repacking Debian Python packages, pass `--staging`. The builder maps
Debian's `dist-packages` payload into the target sysroot's Python
`site-packages`; raw Debian paths are not on arkanaOS's default Python path.

Optional X11 and workstation applications use a checksum-pinned Debian Trixie
manifest. Build only the packages you need, for example:

```sh
python3 scripts/build-debian-cli-packages.py \
  --manifest data/debian-workstation-packages.json \
  --arkpkg-build /path/to/arkpkg-build \
  --staging /path/to/arkanaOS/staging \
  --packages mpv ffmpeg
```

Motif, NEdit, and XNEdit use checksum-pinned upstream sources. Build them in a
disposable target sysroot with a separate temporary package-staging directory;
the helper clears and recreates that staging directory. `--reuse-motif` skips
rebuilding Motif and uses the repository's existing Motif archives:

```sh
python3 scripts/build-motif-nedit.py \
  --sysroot /path/to/disposable-target-root \
  --arkpkg /path/to/arkpkg \
  --arkpkg-build /path/to/arkpkg-build \
  --staging /path/to/temporary-package-staging \
  --jobs 2 \
  --reuse-motif
```

If the system's `/tmp` is a small tmpfs, set `TMPDIR` to a writable persistent
directory before running archive and install tests.

Building `xfonts-base`, `xfonts-75dpi`, or `xfonts-100dpi` also requires
`mkfontscale` and `mkfontdir` on `PATH`; the builder generates each package's
`fonts.scale` and `fonts.dir` indexes before creating its archive.

## Validation

The tooling targets Python 3.11, which is the version the workflows pin. Newer
interpreter features such as `tarfile.extractall(filter=...)` are only
available from Python 3.12, so gate them behind a capability check or the tests
fail on the supported version.

Install the Python `lz4` module and have a built `arkpkg` executable available.
Run the repository audit and generate a preview of the Pages site:

```sh
ruff check scripts/*.py
python3 -m py_compile scripts/*.py
for test_file in scripts/test-*.py; do python3 "$test_file" -v || break; done
python3 scripts/test-packages.py --repo-root . --arkpkg /path/to/arkpkg
python3 scripts/build-index.py --repo-root . --site-dir /tmp/arkpkg-site
```

Every `scripts/test-*.py` file must be run by
`.github/workflows/repository-tool-tests.yml`; that workflow fails when a test
file is not referenced there. Add the step when you add a test file.

The full package test uses a temporary root and installs, verifies, and removes
every archive. Inspect the generated index and site before submitting. Do not
commit the generated `site/` directory; GitHub Actions publishes it after
package changes reach `main`.

The archive audit (`scripts/test-packages.py --audit-only`) also rejects payload
defects that used to reach the published repository:

- files under `usr/bin`, `usr/sbin`, `usr/games`, `bin`, or `sbin` that are not
  executable, because the installed command could not be run;
- `.pc`, `.cmake`, and `.la` files that embed a build root such as
  `/build/arkana/staging/usr` instead of the installed prefix;
- set-id payload entries that are world writable.

If a published archive already contains one of these defects, fix the builder
and then rewrite the archive with the helper instead of re-uploading by hand:

```sh
python3 scripts/repack-defective-archives.py \
  --package zstd-devel-1.5.7-x86_64.ark
```

The helper rewrites only the affected members, verifies the result with the same
audit, and replaces the archive in place. Commit the refreshed archive together
with the builder fix, and remember that its SHA-256 in `index.json` changes.

## Publishing several versions

`packages/` may hold more than one version of a package. The index keeps every
archive and lists them in Debian version order (epoch, then numeric segments,
with `~` sorting before the plain version) rather than in filename order. When
you bump a version, keep the older archive until clients have had a chance to
upgrade, and add a line for the same name in `data/package-metadata.json` only
once; metadata is keyed by package name, not by archive.

Keep the catalogue static and usable with keyboard and mobile layouts. Preserve
its search, category filters, sorting, and package details panel. Make styling
changes in `scripts/build-index.py`, keep depth and shadows restrained, and add
or update generated-page checks in `scripts/test-build-index.py`.

## Pull requests

Use a specific title such as `feat: add xfe package` or
`fix: correct pkgconf package metadata`. Describe the package, its upstream
source and license, dependencies, and the checks you ran. all lowercase would
be prefered as it matches with some of our text styling. Keep official project
spelling when referencing `arkanaOS`, `arkpkg`, `arkcfg`, and `arkpkg-repo`.
