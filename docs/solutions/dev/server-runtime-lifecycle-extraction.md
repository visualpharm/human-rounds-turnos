# Keep the legacy server entrypoint out of background-service ownership

## Problem

After route and renderer extraction, `server.py` still owned the ordered
startup of reminders, waitlist/referral sweeps, housekeeping, demo refresh,
cache warmups, HSI workers, backups, AI health, and app-module entrypoints.
That made the HTTP composition root the owner of unrelated operational
lifecycle code and kept the monolith artificially large.

## Decision

`ops/web_runtime.py` owns web-process background startup and app-module
shutdown. At this slice's boundary, `server.main()` retained the
safety-critical required-pack preflight, socket bind, signal handling, bounded
request drain, and one explicit call to the lifecycle boundary. The later
`ops/legacy_web_server.py` extraction now owns those stdlib runtime contracts
and composes `web_runtime`; `server.main()` remains only the stable entrypoint.

The extraction preserves starter order, feature flags, fail-open isolation,
and the existing operational log strings. Queue-owned jobs still decide
whether their own `start()` functions delegate to the worker topology, so the
new boundary does not duplicate business work.

## Proof

- `test/test_web_runtime.py` pins exact starter order, HSI mutual exclusion,
  fail-open continuation, module shutdown, and the thin `server.main()` seam.
- Existing graceful-shutdown, readiness, error-tracking, module-runtime,
  access-log, and dependency-direction suites remain green.
- `server.py` falls from 959 to 788 lines without changing a route or response.
