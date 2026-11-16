# REFEPS blips make `/api/admin/refeps` 502 on the first try — retry before failing

**Symptom (2026-07-15):** two real admin lookups (`MN 100482` Ana Robledo,
`MP 100537` Eduardo Larrea) returned 502 from `/api/admin/refeps`, then succeeded on
retry seconds/hours later with zero code changes (`logs/access.log`).

**Root cause:** `refeps.buscar_matricula()` (`refeps.py`) does a single GET (form page) +
single POST (search) against `argentina.gob.ar` with no retry. Any transient network/
Cloudflare blip against that third-party gov.ar site raises `RefepsError` immediately,
which `_admin_refeps` (`solicitudes.py`) turns straight into a 502 for the operator — unless
a stale `refeps_cache` row already exists for that matrícula (first-time queries have none).
Not a parsing/markup bug: the exact same call reproduced cleanly minutes later.

**Fix:** `buscar_matricula()` now retries the whole GET+POST sequence up to 2 extra times
(1.5s apart) before propagating `RefepsError` (`refeps.py:_RETRIES`/`_RETRY_DELAY`). Test:
`test/test_refeps_retry.py` (mocked, no network).

**Related but NOT the same bug:** the separate Playwright-driven REFEPS batch engine in the
sibling repo `<workspace>/salud-pinamar/scripts/refeps.py` (used for bulk matrícula
enrichment, not live in this product) is slow/fragile on individual queries — a single
lookup can legitimately take 60–280s under its own internal retry loop
(`goto` 45s + `wait_for_function` 25s × up to 4 attempts), which reads as "hung" when
polled with `sleep`+`tail` in a session that then ends, leaving orphaned Chromium
processes. **Worse: its `batch()` only `json.dump`s the `--out` file after the whole loop
finishes** — an interrupted run (hang, kill, session/context end) loses 100% of the
already-resolved items, not just the pending ones. This is why a 2026-07-15 run that
genuinely resolved 11/26 professionals left zero trace in `medicos.json`, and a 2026-07-16
re-run resolving 12/33 before being killed had to be rescued by re-parsing its stderr log
line-by-line instead of reading the (nonexistent) output JSON. That script is out of scope
for a code fix here (repo boundary); if it recurs, checkpoint `--out` every N items instead
of only at the end, and/or shrink `retries`/`timeout`.

**Also resolved same session:** "CISA" (the product owner's dictation) = SISA (`sisa.msal.gov.ar`,
Sistema Integrado de Información Sanitaria Argentino) — confirmed via SISA's own help docs
that **REFEPS IS SISA's federal professional registry module**, not a separate system.
`sisa.msal.gov.ar/sisa/` is a GWT login portal for provincial data-entry, not a public
search alternative to REFEPS. No second lookup source exists to add — `refeps.py` already
queries it. See `docs/sisa-lookup-diagnosis-2026-07-15.md` for the full writeup + the
colmed9-vs-REFEPS coverage comparison.
