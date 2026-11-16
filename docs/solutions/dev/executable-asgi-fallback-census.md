---
title: Route ownership is not implementation retirement
scope: repo
area: dev
tags: [asgi, routing, strangler, verification]
created: 2026-08-19
refs: [docs/roadmap/asgi-proxy-retirement-plan.md, test/test_transport_shapes.py, test/test_route_probe.py]
status: active
---

> **Update 2026-10-28.** Both census scripts named below are gone. The retirement
> gate is now `test/test_transport_shapes.py` (source shapes and the composed app's
> live routes; no call-graph walk, because the modules it searched for are deleted)
> and the outer route-ownership and health proof is `test/test_route_probe.py` (the
> inventory and probe live in `test/_probe_rows.py`). The lesson below stands.

## Problem

The ASGI fallback census reported every route as native even though most
FastAPI endpoints still executed pre-refactor `handle(handler, method)` bodies.
It also called terminal 404 rows native and silently swallowed handler crashes.

## Root cause

The census measured which Starlette route matched the URL, not which code the
endpoint executed. Its exception probe tested only for outbound sockets; every
other exception was intentionally ignored. Terminal rows returned `native`
despite a dormant `terminal-compatibility` category.

## Solution

Keep `legacy_fallback_census.py` as an outer route-ownership and health proof,
with terminal rows classified honestly and unexpected 5xx responses fatal.
Use `scripts/refactor_architecture_census.py` as the binding retirement gate:
it follows repository-local call graphs and FastAPI dependencies, then reports
`native-independent`, `adapted-legacy`, and `rollback-only`. Arbitrary dead
source stays outside this executable gate and requires separate lifecycle
evidence rather than a decorative zero category. A renamed adapter helper is
still adapter-backed. The strict source pass must also reject legacy response
writers, imports, references, and fabricated request shapes even when no live
route reaches them. Retire FakeHandler tests with the transport; rewrite them
against value-result boundaries plus real ASGI requests. A historical gate
passed371/371 native-independent with zero strict source findings; its row
total is not a requirement to retain obsolete routes.

## How to apply

Run both censuses after every ASGI route change. A matched FastAPI route proves
transport ownership only. Retirement requires the architecture census to pass
`--require-complete`, production-source grep/shape evidence, and stale-test
removal. Do not keep a historical route total by retaining retired aliases.
On 2026-09-05, Interviews retirement removed GET /admin-entrevistas.js and
its slash twin; the module .js pair became .mjs, giving no further delta.
Composition became 363; native acceptance became 370/370, with zero strict
source findings. Explain the actual registered method/path delta, not the
number of closed URLs. The subsequent appointment creation migration
removed only the Spanish alias pair. Its English static alias had no separate
route object. The same day, dormant-client retirement closed GET
/rubricador.js, GET /rubricador.js/, GET /stz-ui.js and GET /stz-ui.js/
after proving ui-core.js and specialty-browser.js had no live consumers.
Its isolated822f-based composition became359 and architecture366/366. Combined
with appointment retirement, composition is357 and architecture364/364, with
zero strict source findings. Prove exact method/path set changes, then pin retired URL
responses through TestClient (404 basename, 403 slash, both no-store), not
just smaller counts. Keep the probe offline; it owns its scrubbed environment and resets
already-loaded configuration and authentication-mode caches so a preceding
test cannot leak a provider choice into it. Allow only the exact identity-
pinned database-unavailable 503 responses and fail on all other server errors.
