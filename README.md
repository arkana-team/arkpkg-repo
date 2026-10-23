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
- `scripts/` contains package builders, index generation, and repository tests.
- `.github/workflows/pages.yml` publishes the package index and catalog through
  GitHub Pages when package files are merged into `main`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for package and contribution guidelines.
