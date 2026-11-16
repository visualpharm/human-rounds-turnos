---
title: Server retirement slice 3 — the WebTurno client/owner API leaves server.py
scope: repo
area: dev
tags: [server-retirement, webturno, legacy, routing, ownership]
created: 2026-08-16
refs:
  - test/test_server_webturno_retirement.py
  - docs/architecture/routing-y-modulos.md
  - ops/demo_installation.py
status: active
---

## Problem

After the cobertura (round 1) and directorio-dispatch (round 2) slices,
server.py still carried the entire WebTurno patient/owner CLIENT: ~620 lines
serving `/app`/`/panel` plus a header-credential API (`X-WT-DNI`/`X-WT-Pass`)
— login, availability scrape, book/cancel, watch, vault, coverage overrides,
logs, Gmail confirmation — a boot-time owner login against the live WebTurno
site, and the `ops/watcher.py` polling daemon. No served page could reach any
of it (patient_web/site_*.py 302s `/app`, `/app.html`, `/panel`, `/panel.html` to `/`;
the demo hard-404s the whole API inventory), but on prod the endpoints were
still curl-reachable and would scrape a third-party system with credentials
taken from request headers — violating the "identity comes from the session,
never the request" invariant, with no owner in the module system.

## Root cause

The 2026-07-08 de-linking removed the WebTurno UI "from the UI, not from the
code": interceptors made the client unreachable through pages, but the raw
API surface, the boot login, and the vigilante daemon stayed live-but-unowned
in the monolith.

## Solution

Delete the surface, keep every current owner untouched:

- server.py loses the route blocks (GET/POST/DELETE), the seven `_handle_*`
  bodies, `_vault_proxy`, `_req_creds`/`_req_wt`/`_require_owner`,
  `is_owner`/`make_wt`/`availability_for`/`_save_db`, the `_AVAIL` cache,
  watch/coverage/vault load-save + paths, `_tail_log`, `WT`/`OWNER_*`/
  `REMOTE_URL`, the `webturno` import, and main()'s owner-login + watcher
  boot. `ops/watcher.py` and `notifications/gmail_confirm.py` (orphans only
  this surface fed) are deleted.
- The retired endpoints now fall to server.py's generic `/api/` JSON 404.
- Deliberately UNTOUCHED: `ops/demo_installation._blocks_legacy` (the demo
  keeps denying the same inventory — byte-identical demo behavior, and a
  rollback of server.py re-lands behind the same denial), the
  `connectors/webturno` connector (still the owned WebTurno integration;
  review follow-up same day aligned only its metadata/docstrings and its
  `_load_env()` with the retirement — the loader now reads just its two
  declared credentials; the owner-calendar module `modules/turnos/calendar_busy`
  and its `OWNER_CALENDAR_ICS` were removed on 2026-11-06 — nothing imported it),
  and
  the `data/availability.json` snapshot READ path (`_load_db` →
  `public_medicos`/`especialidades_tree`, live via `/api/specialties` and
  `/<esp-slug>/<name-slug>`). The snapshot is now read-only: the scraper that
  wrote it is gone.

Pinned by `test/test_server_webturno_retirement.py`: source/runtime absence,
dispatch-level unclaimed-ness (non-demo), pages still 302→/ inside the
dispatch chain, demo deny-list parity over the full retired inventory, the
surviving snapshot read path, and orphan/ownership checks.

## How to apply

When retiring a dead monolith surface: (1) prove every entry point is
intercepted before the monolith (or denied by an ungated platform handler) —
patient_web/site_*.py/demo/legacy_urls are the three interceptor layers here; (2) cut
along the write/read seam — kill the writer, keep the reader if anything
live consumes the artifact; (3) leave existing deny-lists in place as the
rollback-compatible record; (4) chase the second-order tests that pin the
removed internals (this slice: `test_access_log`'s `_tail_log` class, the
checkout-env contract's `REMOTE_URL`/`_OWNER_ENV` probes,
`test_module_manifest`'s config-prefix scan needing `connectors/` once
server.py stopped using `WEBTURNO_*`, `test_domain_dependency_direction`'s
per-package module counts).
