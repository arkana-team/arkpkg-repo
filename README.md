![Banner](https://raw.githubusercontent.com/arkana-team/branding/refs/heads/main/arkana_banner.png)

<h1 align="center">arkpkg-repo</h1>
<p align="center">Optional packages for arkanaOS, with a searchable index on GitHub Pages.</p>

## Use the repository

The package index and searchable catalog are published at:

- Repository: <https://arkana-team.github.io/arkpkg-repo>
- Package index: <https://arkana-team.github.io/arkpkg-repo/index.json>

GitHub Pages serves the small index and catalog. Package archives are downloaded
from this repository's raw file host and verified against the SHA-256 digest in
the index.

The catalogue lets you search packages, browse by category, sort the list, and
inspect package details before downloading. Entries include upstream and
packager credits, source links, dependencies, and direct `.ark` archive links.

Add the repository once, then search for and install optional packages with
`arkpkg`:

```sh
sudo arkpkg repo add arkanaOS https://arkana-team.github.io/arkpkg-repo
arkpkg repo list
arkpkg search nedit
sudo arkpkg install nedit
```

Replace `nedit` with a package name such as `dillo`, `xfe`, or `firefox`.
The package manager resolves dependencies from configured repositories.

## What's here

- `packages/*.ark` contains the built package archives served from the raw file host.
- `packages/recipes/<name>/ARKPKG` records package metadata and dependencies.
- `data/package-metadata.json` records upstream authors, packagers, and Git links.
- `scripts/` contains package builders, index generation, and repository tests.
- `.github/workflows/pages.yml` publishes the package index and catalog through
  GitHub Pages when package files are merged into `main`.
- `.github/workflows/repository-tool-tests.yml` runs every `scripts/test-*.py`
  file on Python 3.11.

See [CONTRIBUTING.md](CONTRIBUTING.md) for package and contribution guidelines.

## The machine-readable index

`index.json` uses `schema_version: 1`. Its `packages` list has one entry per
published archive, not one per package name, so a package with two published
versions appears twice:

```json
{
  "name": "zstd-devel",
  "version": "1.5.7",
  "arch": "x86_64",
  "description": "Zstandard compression library development files",
  "file": "packages/zstd-devel-1.5.7-x86_64.ark",
  "download_url": "https://raw.githubusercontent.com/.../zstd-devel-1.5.7-x86_64.ark",
  "sha256": "…",
  "author": null,
  "git_repo": "https://github.com/facebook/zstd",
  "git_repo_type": "upstream",
  "git_author_url": null,
  "packagers": [
    {"name": "arkana-team", "url": "https://github.com/arkana-team"}
  ]
}
```

`version` values follow Debian ordering (epoch prefix, numeric segments, `~`
before the plain version), and entries for the same name are listed from oldest
to newest. `git_repo_type` is one of `upstream`, `packaging`, or
`arkpkg-recipe`. `author` and `git_author_url` are `null` when they cannot be
verified. Always verify a downloaded archive against `sha256` before installing
it.

## Licenses

The tooling in `scripts/` and this documentation are MIT licensed; see
[LICENSE](LICENSE). Packages are third-party software under their own upstream
licenses; each recipe's `License` field records the license, and the archive
payload keeps the upstream license or copyright files. This notice does not
cover the published packages.

## Reporting a security problem

Do not open a public issue. Follow [SECURITY.md](SECURITY.md) and use the
repository's private vulnerability reporting.
