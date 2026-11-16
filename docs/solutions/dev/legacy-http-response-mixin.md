# Keep legacy response behavior out of route dispatch

## Problem

The shrinking stdlib server still owned cache policy, branded errors, JSON and
body helpers, MIME overrides, access-log rotation, and request logging inside
the same `Handler` class that owns legacy route order. These are transport
contracts, but their size obscured the remaining dispatch boundary and made a
route edit look coupled to caching and logging.

## Resolution

`services/legacy_http.py` now provides a cooperative `LegacyHttpMixin` above
`SimpleHTTPRequestHandler`. `server.Handler` keeps only construction and
method dispatch. The mixin retains the same `super()` chain, status tracking,
cache precedence, branded 404 fallback, gzip JSON helper, body parsing, MIME
override, rotating logger, and access-line format.

The transport layer does not import the patient application. The composition
root injects `patient_web.error_pages.not_found_body` as the branded-document
renderer, while the mixin retains a byte fallback if presentation fails.

The ownership test asserts every moved method is absent from
`server.Handler.__dict__` and present on the mixin. This prevents a later copy
from silently shadowing the boundary while ordinary HTTP tests remain green.

## Verification

Run the access-log, cache-header, real socket HTTP, static-hardening, graceful
shutdown, and route-order suites. The managed legacy and ASGI ladders remain
the release gate because both topologies execute this exact handler.
