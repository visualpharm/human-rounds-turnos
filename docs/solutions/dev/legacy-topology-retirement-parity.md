# Retiring the legacy topology: the parity traps that survived every unit test

**Date:** 2026-08-27 · **Context:** native-migration final wave (delete
`server.py` + the whole dark dispatch chain; census `--require-complete`
goes 0→100% native-independent, both managed topologies green, deployed).

## The lesson in one line

When two transports must serve identical bytes, prove it **empirically
against the deployed reference** (spin both apps under TestClient, fire the
whole probe-row inventory, diff status/body/headers) — per-router "verified
against the live app" docstrings go stale the moment the reference moves.

## Traps found (each shipped green tests until the right gate ran)

1. **Decline vs domain 404 are different bytes.** A path FastAPI matched
   but the legacy inner regex would have DECLINED (non-numeric id, unknown
   entity literal, out-of-shape token, feature-not-configured) must serve
   the SHARED terminal 404 (`core.terminal_response.json_not_found_response`,
   `{"error": "not found"}`, no-store). A genuine domain miss (entity looked
   up and absent) keeps its localized flat body, and the module-disabled
   gate keeps `no_encontrado`. Several campaign routers pinned the
   detail-wrapped `{"detail": {"error": "no_encontrado"}}` shape because
   they were "verified against the live app" BEFORE the gate lane fixed
   `route_via_handle`'s fallback to the terminal bytes — the reference
   moved, the docstrings didn't. `TerminalNotFound` (HTTPException subclass
   + app-level handler) is the raise-capable form for dependencies.
2. **uvicorn vs stdlib transport differences break tests, not prod:**
   lowercase response-header names (fix at the fixture: title-case the
   dict, not 40 call sites), and SIGTERM exiting `-15` AFTER a complete
   graceful drain (uvicorn re-raises the signal by design — assert the
   completed-shutdown log line, not exit 0).
3. **Registration-time side effects die with the registry.** `core/routing.py`'s
   import applied `demo_installation.apply_safety_defaults()` and its
   rstrip-before-lookup gave every legacy 301 a free trailing-slash twin;
   `core.i18n.handle()` was the ONLY owner of `/api/i18n/<lang>.json`.
   Deleting the dispatcher silently dropped all three — found by the test
   migration, not by any route census (the census counts implementations,
   not import side effects).
4. **"OK" without a DB is not green.** The routes tier and dozens of
   DB-gated suites skip cleanly without `TEST_DATABASE_URL`; a fully
   "green" offline run hid every DB-lane break (passkeys duck missing
   `_read_body` since F1, ai_health breaker state persisting into
   unrelated tests, per-DNI analysis cache satisfying a seeding test from
   another test's rows). Only the managed `local-ci` profiles are the
   verdict.
5. **Probe inventories must outlive their driver.** `route_parity_check.py`
   (legacy-vs-native differ) died with the topology, but its ESCENARIOS
   row tables were the census's probe inventory — extracted verbatim to
   `scripts/route_inventory_scenarios.py` (pure literals, AST-read, never
   imported) so coverage survived the driver.

## Reusable move

`xdiff`: check out the reference ref in a scratch worktree, run BOTH apps
offline under TestClient over the full row inventory, dump
`(status, body-prefix, content-type, location, cache-control)` per row,
diff the JSONs. 325 shared rows took ~2 min and surfaced every remaining
shape drift; the surviving diffs were each attributable (deliberate F1
401-body unification, local generated-atlas artifact, random nonce).
