# Legacy static policy belongs outside the HTTP shell

## Problem

`server.py` mixed the stdlib HTTP transport with the security policy that
decides which checkout files are public, which denied paths are real, and how
verified private-pack assets are opened without following swapped symlinks.
That made the shrinking legacy server look like the owner of data-pack and
filesystem security rather than a transport adapter.

## Resolution

`services/legacy_static.py` now owns the public extension and prefix allowlist,
NUL/path validation, 403-versus-404 classification, pack-aware root resolution,
and descriptor-relative private-file serving. The legacy handler delegates to
that boundary at the same points in its routing order. Its cooperative mixin
also owns the stable `/webdata/` and `/data/` asset transport plus the final
allowlisted checkout-file fallback. Tests patch the owning module directly,
assert the former private helpers no longer exist in `server.py`, and pin the
mixin ownership so route dispatch cannot silently reclaim the file policy.

## Verification

The pure static-policy matrix, real socket-bound HTTP suite, required-pack
readiness and swap-race suite, cache-header suite, route-order suite, and both
managed deployment topologies must remain green. This extraction changes no
URL, status, body, cache header, root, or private-pack trust decision.
