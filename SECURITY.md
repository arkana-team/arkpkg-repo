# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem. Use the repository's private
reporting flow instead:

1. Open <https://github.com/arkana-team/arkpkg-repo/security/advisories/new>
   (or the **Security** tab → **Report a vulnerability**) on the upstream
   repository.
2. Describe the affected package archive, recipe, builder script, or workflow,
   the arkpkg/arkanaOS version you tested, and the smallest reproduction you
   have.

If private reporting is unavailable, contact the arkanaOS maintainers through
<https://github.com/arkana-team> and say that you have a security report; do not
paste exploitation details into a public issue or pull request.

Please include the SHA-256 of any archive you tested. Every archive digest is
published in `index.json`, so a mismatch is easy to confirm.

## What this repository ships

- `packages/*.ark` — signed-out archives served from the raw file host. Each
  archive is verified by SHA-256 against `index.json` before installation.
- `data/package-metadata.json` — credits and source links rendered in the
  catalogue.
- `scripts/` — builders, the index generator, and the repository audit.

## Threat model notes for contributors

- **Staging paths in payloads.** A `.pc`, `.cmake`, or `.la` file that embeds a
  build root (for example `/build/arkana/staging/usr`) makes downstream builds
  link against paths that only existed on the build machine.
  `scripts/test-packages.py` rejects these.
- **Archive payload modes.** Bin entries that lose their executable bit and
  set-id files that are world writable are rejected by the same audit.
- **Source integrity.** Builders must pin an upstream version and a SHA-256 and
  must build from a verified private snapshot; see `scripts/source_cache.py`.
- **Untrusted archives.** The index builder and audit bound metadata size,
  extension headers, member counts, and expanded bytes before decoding, so a
  malicious `.ark` file cannot exhaust memory during CI.

## Supported versions

Only the current `main` branch and the archives currently published in
`index.json` are supported. Older archives that were replaced by a newer build
of the same package are not patched; report the issue against the current
version instead.
