# Keep the legacy stdlib lifecycle in ops

## Problem

After route, response, static-policy, and web-process lifecycle extraction,
`server.py` still owned the concrete `ThreadingHTTPServer`, concurrency
semaphore, signal watcher, request drain, required-pack gate, and background
module shutdown. Those are runtime orchestration contracts, not route
composition.

## Resolution

`ops/legacy_web_server.py` owns the bounded stdlib server and its full
lifecycle. `server.main()` remains the stable dark-mode and ASGI-proxy entry
point, but delegates with the composed `Handler` plus the selected bind
address. The runtime module never imports `server` or `app`, so dependency
direction stays one way.

The existing readiness, concurrency, graceful-shutdown, and subprocess gates
now patch the owning ops module. An ownership assertion prevents the runtime
types and constants from drifting back into `server.py`.

## Verification

Run the access-log/concurrency, graceful-shutdown, required-pack readiness,
error-tracking, real HTTP, route-order, static-policy, and dependency-direction
suites. Both managed deployment topologies remain the release gate because
dark mode and ASGI proxy mode call the same `server.main()` entry point.
