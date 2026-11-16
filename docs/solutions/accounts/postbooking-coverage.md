# Optional coverage after a booking

Local slice based on `78afa83a` (2026-10-21). Publication and production installation are separate decisions. No schema migration, outbound message, credential upload or external insurance lookup is added.

## Authorization and storage

`GET /api/requests/{id}/coverage` and `POST` at the same path resolve identity from the existing session. The booking must belong to that session's clinical patient AND have the same requester (legacy empty requester is accepted). The server ignores patient IDs in the body and rechecks this binding inside the coverage update SQL.

An unrelated or absent booking returns the same generic 404. A missing session returns 401. An authorized dependent booking returns only `{supported:false, reason:"dependent"}` on GET and refuses POST. It never returns the guardian's or child's coverage. Existing dependent links are declared relationships, and the dependent table has no independent coverage store. This slice does not turn those links into permission to overwrite another account. Accounts with a generated identity key remain unsupported, consistently with the existing account coverage form.

Only the existing `users.coverage` JSON changes. The booking, booking events, contact, preferences, profile, session, and clinical records are untouched. Existing doctor-day and admin projections join coverage by the clinical patient's DNI. Member numbers remain in the existing coverage object; this slice does not add a new staff credential display. The current HSI appointment adapter has no coverage field mapping, so storing coverage here does **not** claim it has been sent to HSI.

## Decisions and concurrency

- `choice:"coverage"` requires a selected insurer/prepaga and the shared picker's wire object; existing normalization preserves catalog type, permitted RNOS provenance and member numbers.
- `choice:"none"` stores `{declarada_sin_cobertura:true}`. It remains distinct from unknown/missing information. The account normalizer retains this decision when its empty picker is included in a later profile save. Existing primary-coverage and admin report serializers return no invented payer.
- “Prefer later” makes no coverage write and grants no messaging consent.
- GET returns an opaque content revision. POST requires it; both the initial check and SQL compare-and-swap reject a concurrent replacement. An identical retry after a lost response is acknowledged without another write.
- The account editor sends the revision too. Cached clients without it may only carry a normalized no-op, guarded by the same SQL comparison. Account updates omitting coverage and federated identity refreshes preserve the coverage column in SQL.
- A request changing both DNI and coverage refuses before either write. An ordinary DNI edit carrying unchanged coverage moves the identity and preserves the current coverage column without replaying a stale snapshot. This avoids a 409 after identity migration had already committed.

## Validation boundary

`test/test_postbooking_coverage.py` uses native FastAPI HTTP, real sessions and a run-scoped disposable PostgreSQL database. It covers self/foreign/unauthenticated/dependent ownership, body-ID spoofing, generated/inactive identities, legacy requester, save/reload, explicit no-coverage and replacement, identical retry, outage, stale revision, parallel CAS, account edit races, provider refresh, DNI migration and profile/preference preservation. Every case checks that no extra booking event appears. Email, WhatsApp OTP and external insurance lookup are forbidden test seams; only the local insurance cache may be consulted.

Related existing tests cover account/store contracts, account session projection, coverage normalization, PUCO provenance, RENAPER/portal identity, connector routing, reports and the route inventory. The inventory change is exactly one GET/POST path; the existing PUCO edit fixture now supplies the required client revision. Full-suite and browser evidence belong to the final combined slice receipt, not this backend-only note.

## Deliberately separate work

WhatsApp follow-up and credential photos are inactive design work in `docs/roadmap/coverage-followup-whatsapp.md`, with synthetic design scenarios. They require a purpose-specific opt-in, durable verified contact evidence, deduplication/uncertain-send handling, stop-on-reply, and an independent private media path before implementation. No new credentials, provider configuration, scheduled jobs or real reminders were introduced.

## Actual browser-to-database proof

After the component and backend were integrated, an ephemeral loopback fixture served the actual fingerprinted account module and native account/coverage endpoints against a fresh disposable PostgreSQL database. A browser selected Swiss Medical, entered member number `00042` and plan `SMG20`, saved and reloaded; then it selected no coverage, saved and reloaded again. `postbooking-coverage-native-proof.json` records the database-backed result at each stage: one booking, one creation event, all non-coverage profile fields unchanged, exactly two coverage POSTs, no booking POST and no browser errors. The fixture blocked unrelated writes, printed no session token, and was shut down with its owned database removed.

Frontend owner additionally verified the actual service receipt with fully intercepted synthetic responses at 320/390/1280 px, including pending/approved receipts, fresh booking completion, receipt recovery, dependent limitation, session/route teardown, conflict review and retry after a committed response was lost. Those browser lifecycle checks and the native persistence proof are separate evidence; they do not establish live municipal, HSI or WhatsApp integration. Final placement is immediately after the receipt summary, preserving the existing receipt actions below it.
