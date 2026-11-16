---
title: The ASGI cutover silently bypasses any per-request contract that lives only in server.py — host redirect was the first casualty
scope: repo
area: deploy
tags: [asgi, tier4, host-redirect, middleware, regression, coolify]
created: 2026-08-14
refs: [24073b9e]
status: active
---

## Problem

`salud.brieflysites.com/` answered 200 with the Pinamar homepage instead of
the contracted 308 to `pinamar.humanrounds.org/datos` — but only on `/`.
Every other path (`/cualquier-cosa`) still 308'd correctly, which made the
regression look like a bizarre root-path special case. The redirect had been
verified working on 2026-08-06; nothing in `core/host_redirect.py`,
`server.py`, or the Coolify domain config had changed.

## Root cause

Production was cut over to the ASGI front (`TURNOS_ASGI=1`, São Paulo
Coolify). In proxy mode, requests split into two families: routes served
NATIVELY by FastAPI (site_identity's `/`, everything in
`routers/platform.py`, module routers) and the legacy catch-all that
reverse-proxies to `server.py`. The host redirect ran only at the top of
`server.py`'s `do_*` methods — so exactly the natively-served paths skipped
it, while proxied paths kept redirecting. The same class of gap had already
been found and closed once for site auth (`auth/site_auth_middleware.py`'s
"parity gap" story); host redirect was the next contract sitting only in
legacy.

Diagnosis was also misled by infrastructure history: the Hetzner origin
(<host-A-ip>) 503s for this host because production moved to the São
Paulo box (<host-B-ip>) — always `dig`/probe the origin the São Paulo
way (see `wrong-coolify-host-ip-from-stale-note.md`) before blaming DNS.

## Solution

`core/host_redirect_middleware.py` — a BaseHTTPMiddleware running the SAME
`host_redirect.target()` decision, mounted as the OUTERMOST middleware in
`app.py` (last `add_middleware` call), before the site-auth gate, matching
`server.py`'s redirect-before-auth order. Wire shape matches legacy: 308,
`Location`, `Cache-Control: no-store`, empty body.
`test/test_host_redirect_middleware.py` pins it against the real app;
`tier4_composition_inventory.json` regenerated; the site-auth "outermost"
assertion now expects HostRedirect above SiteAuth. Commit 24073b9e.

## How to apply

When migrating any behavior that `server.py` applies to EVERY request
(redirects, auth, headers, quiet-hour gates), ask: does it run for the
NATIVE FastAPI family too, or only behind the legacy catch-all? If it lives
only in `server.py`, every newly-migrated native route silently drops it.
The fix pattern is always the same: extract the decision into a shared
module (it usually already is), add an ASGI middleware that calls it, and
pin the middleware ORDER in the composition fixture. Symptom signature to
recognize it live: a contract holds on obscure paths but fails on `/` or
another migrated route — that path split IS the native-vs-legacy split.
