# Keep server.py as a stable entrypoint, not a monolith

## Problem

After every cohesive behavior family had moved to its owning package,
`server.py` still defined the concrete compatibility `Handler` and carried a
large set of obsolete direct feature imports. The file was small, but its
physical ownership still made it look like the application monolith.

## Resolution

`routers/legacy_http_transport.py` now owns the concrete stdlib handler and
the exact remaining order: host redirect, launch gate, registered routes,
health-data tail, API 404, doctor/specialty tail, and static fallback. The
response and static mixins remain in `services/`; the process lifecycle
remains in `ops/legacy_web_server.py`.

`server.py` re-exports `Handler` and `ROOT` and delegates `main()` so dark mode,
ASGI proxy mode, tests, and operational tooling retain the same stable import
surface. It is 33 lines and contains no feature logic.

## Verification

The real socket, cache, static security, response/logging, graceful shutdown,
required-pack, route-order, dependency-direction, composition, and module
runtime suites pin both behavior and ownership. Both managed deployment
topologies remain the release gate.
