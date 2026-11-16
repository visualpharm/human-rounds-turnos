# Local study validation

Recorded: 2026-10-14T18:19:24-03:00. Branch `task/usability-study` remains uncommitted and unpushed in the dedicated worktree.

## Integration results

- All six file contents are conflict-free and retain both the upstream AI-register/backup intent and study intent. Study decision remains last. Git still reports unmerged index entries: `git add` was denied because the worktree index is outside the writable sandbox. No index, commit, branch or remote was changed.
- Alembic has exactly one head: `0020_real_study`, following `0019_ai_register_changes`. Migration filename, head pin, frozen bridge table classification, schema comments and study instructions agree.
- Data map covers 61 application tables, including the AI register and four anonymous study tables. Ordered route regeneration adds only the eleven study routes; every prior route and middleware retains its order. Census: composition 392, native 659, unique native 473.
- Requested Python groups: 348 tests, 279 passed, 69 database-dependent checks skipped; no failures or errors. AI-register groups include the exact inference-call-site inventory and pass with no study registration.
- Credential-marker candidate check: 1 passed. The mock session handle was changed to a visibly synthetic constructed value without weakening the detector. Public-tree hygiene: passed after removing personal attribution from two inherited browser-test comments.
- Cache and rendered-admin checks include current-byte/immutable and stale-hash/no-store proofs for both study modules. Closed shared asset cohorts stay unchanged. Only the four English account hashes move: the study catalog enters their existing generic bootstrap. Removing only `study.*` restores every previous hash; Spanish pins and shared-manifest asset bytes match. Evidence: `.claude/artifacts/study-integration/account-render-proof.json`.
- Full `node --test test/*.test.mjs`: 2417 tests, 2274 passed and 143 failed. Every failure block contains browser launch denied by the macOS sandbox; no other failure category occurred. Focused `node --test test/study-runner*.test.mjs`: 7 passed.
- No remaining old study revision references or conflict markers. `git diff --check` passes. `app.py` remains unchanged at 99 lines. Logs and machine-readable summaries are under `.claude/artifacts/study-integration/` inside this worktree.

## Unverified acceptance

Read-only connection probes to both loopback Postgres lanes (5432 and 5433)
fail with `PermissionError: Operation not permitted`. Real schema upgrade,
store round trips, retention and concurrency remain unverified. Browser
launch is denied, so rendered phone/desktop behavior and screenshots remain
unverified. No deployment or external calls were performed.

The study documents and coordinator message contain no personal names or
private hostnames. The broader literal name-removal criterion is still
unmet: 52 pre-existing tracked files outside this study retain the
coordinator's first-name literal, including historical documentation,
generated locale catalogs and unrelated synthetic-person fixtures. These
were not mass-rewritten as part of the integration.

The implementation reuses the schema's no-FK convention: all participant
writes lock the session, and retention locks eligible sessions before
transactionally deleting events, results and sessions. This keeps the
Alembic baseline-shape parser intact rather than weakening its checks.

## Exact feature routes

```text
GET /study
POST /api/study/session
GET /study/s/{token}
POST /api/study/s/{token}/event
POST /api/study/s/{token}/result
POST /api/study/s/{token}/finish
GET /admin/usability
GET /api/admin/usability
POST /api/admin/usability/analyze
POST /api/admin/usability/findings
POST /api/admin/usability/findings/{finding_id}
```

Existing `/admin/demo-access` and `/admin/demo-doctor-access` are reused,
not new routes. Feature flags also hide the study sidebar destination.

## Proposed e2e profile

`e2e-study-asgi`, a reviewed fixed-argv profile in
`scripts/turnos_managed_exec.py`, running `test/e2e/study-lifecycle.e2e.mjs`
against a disposable database and minimally seeded fictional demo accounts.
It would fix ASGI, study/demo and fake-provider flags, block outbound sockets
and third-party browser requests, and use separate browser contexts for the
owner and participants. Do not use the demo's real-AI seeding path.

Cover ES/EN and all three roles at 360, 390, 768, 834, 1024, 1280 and 1440 px:
no session/event before consent; single and role fallback links; passwordless
demo entries; no real credentials; task outcomes/ease/comments; opt-out;
refresh and browser Back recovery; delayed/hung module load; failed/late save;
F1–F3; gate-off 404s; role denials; demo read-only and owner writes; findings
analysis/dedup/status preservation; status/role filters and bulk changes;
copy link; privacy payload/database inspection; migration from 0019_ai_register_changes;
90-day purge; purge/write contention; overflow and phone/desktop screenshots.
