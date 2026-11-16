# Releases — immutable image digest record

Every published container image is recorded here by its **immutable sha256
manifest digest**, not (only) its tag. A tag (`v1.0.0`, `latest`) is mutable —
it can be re-pointed; a digest cannot. So "what exactly did `v1.0.0` point at
when it shipped" stays answerable here even if the tag is later moved.

This file pairs with:

- [.github/workflows/release.yml](../../.github/workflows/release.yml) — the publish
  path whose "Record immutable digest" step emits each release's
  `image@sha256:<digest>` (to the run summary and a `release-manifest-*.tsv`
  artifact).
- [docker-compose.release.yml](../../docker-compose.release.yml) — the digest-pinned
  install path that hardcodes the repository and `@sha256:` prefix and
  consumes `SANA_IMAGE_DIGEST_HEX=<64-hex-digest>`. The structural hardening
  prevents ANY value from becoming a mutable tag reference (even a malformed
  value like "latest" becomes `@sha256:latest`, which cannot resolve to `:latest`
  or `@latest` and fails at runtime pull).

## How a real entry lands here

The release workflow runs with `permissions: contents: read` — it deliberately
does **not** auto-commit (auto-committing the manifest would require
`contents: write`, a guardrail that workflow keeps off). It also refuses to
publish at all unless the tagged commit is reachable from `main` (a SemVer tag
pushed from an unmerged branch is rejected before any build). So after each real
release a human copies the recorded line from the run summary / manifest
artifact and appends it to the table below. The "Record immutable digest" step
emits a paste-ready row in this table's EXACT column order — `| version tag |
immutable digest reference | date | notes |` — so it is one copy-paste, not a
reformat. One row per published version tag.

### Real-release ledger

| version tag | immutable digest reference | date | notes |
|---|---|---|---|

*(Empty — no image has ever been published from this repo. See status below.)*

### Format illustration (NOT a real release)

<!-- Example row format (SANA_IMAGE_DIGEST_HEX consumes the 64-hex digest payload ONLY;
the repository, @ separator, and sha256: prefix are fixed in docker-compose.release.yml).
Replace the placeholder digest with the real one emitted by the release workflow; never
reuse a digest across versions. A tag MAY repeat a digest (e.g. :latest aliasing
:v1.0.0) — a version tag never shares another version tag's digest. -->

| version tag | immutable digest reference | date | notes |
|---|---|---|---|
| `v0.0.0-example` | `ghcr.io/visualpharm/sana-turnos@sha256:0000000000000000000000000000000000000000000000000000000000000000` | UNPUBLISHED | SYNTHETIC FORMAT EXAMPLE — not a real release, not pullable, placeholder digest. Records nothing. |

## Status — nothing has been published

**No image has ever been published from this repo.** The release workflow
cannot run while GitHub Actions billing is frozen for this account (see the
workflow file's header), and no manual `docker build && docker push` has been
performed either. The real-release ledger above is therefore empty by truth,
not by oversight — it has zero rows because there have been zero releases.

The "Format illustration" section below the ledger carries a single
**synthetic format example**, not a real release. Its digest is a placeholder
(all-zero) and is explicitly NOT pullable. It exists so the row format and
the `SANA_IMAGE_DIGEST_HEX=<64-hex>` contract are concrete, not so anyone
mistakes it for a shipped artifact. Delete that section when the first real
row lands.
