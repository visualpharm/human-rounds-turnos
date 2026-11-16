# Slice 8 continuation — 2026-10-14
PARTIAL: real PostgreSQL acceptance and the full Node browser-launch gate remain unverified.
No commits, deployment, browser e2e profiles or external AI/provider calls.

## Spec items
1. **DONE:** 17-function source-of-truth inventory; live provider/model policy,
   conservative risk, PII/oversight/notices/switches, honest unknown dates and call-site drift test.
2. **PARTIAL:** admin-only manifest screen, table engine, JSON export and printable HTML
   pass ASGI fixture tests. Added browser-free real-ledger round-trip test; DB execution is blocked.
3. **PARTIAL:** atomic change ledger, Alembic 0019, schema/data-map/manifest classification,
   notification action and one Sistema row implemented; offline checks pass; DB proofs remain skipped.
4. **DONE:** ES/EN notices per surface. Fixed WhatsApp notice timing: disclose before first
   inference and every attachment read; failed disclosure stops processing and stores no receipt.
5. **DONE:** inherited cancellation/auto-approval/unreviewed-profile violations remain fixed.
   Eight oversight regressions pass, including actual interview-close and explicit clinician-save gates.
6. **DONE:** normative provenance map, 17 provisional risk files, municipality checklist,
   shared terms/privacy inventory and legal/register consistency checks. Institutional duties remain open.

## Files
Inherited: core/ai_register.py, core/ai_policy.py, core/db.py, core/config.py;
admin_panel/ai_register_screen.py, settings_admin*, system_status.py; ai/* model/prompt paths;
alembic/versions/0019_ai_register_changes.py; modules/{chat,intake,turnos} oversight/notices;
patient_web/legal.py; templates/admin/*; static/platform/*; locales/{es,en}/ai.json;
docs/compliance/, docs/decisions.md, DATA_MAP.md; tests/test_ai_register*.py and notice suites.
Continuation: modules/chat/whatsapp_chat.py; tests/test_ai_register{,_changes,_db,_notices,_oversight}.py;
tests/test_whatsapp_chat.py, tests/test_accounts_jinja.py, tests/test_legacy_fallback_census.py,
tests/tier4_composition_inventory.json; compliance/checklist/risk/evidence docs; decisions and solution index.
Unrelated center-photo e2e/fixture files were preserved untouched.

## Verification
AI register: 35 tests, 29 passed, 6 PostgreSQL skips; notices and oversight regressions pass.
Required gates pass: Alembic cutover (110, 59 DB skips), data map (6), manifest (36),
system status (42), census (24), checkout env contract (5), dependency direction (6).
Composition (3), Account Jinja (20), credential boundary (11), managed executor (11) pass.
Additional AI/config/legal/intake/chat/MCP/WhatsApp suites: no failures; DB skips recorded in JSON logs.
Credential-marker check (1) and current-public-candidate checks (3) pass.
Full public-cut unittest suite exceeded the 180-second diagnostic bound; not claimed complete.
Full Node: 2406 tests, 2263 passed, 143 Chromium launch failures; focused suites pass 48/48.
Admin simplification fixture evaluator and git diff --check pass.
Evidence: docs/compliance/evidence/continue-* (logs, counters and raw-render proof).

## Decisions and remaining acceptance
Exactly five register routes added: composition 381, native 648, unique native 462.
Account pin updates reverse exactly reviewed asset URLs plus the required new English AI
catalog keys; eight prior ES/EN byte responses and anonymous Account are proven unchanged otherwise.
Managed DB credentials are unavailable here; socket/TCP access and disposable PostgreSQL shared
memory are refused by this sandbox. Chromium launch is refused by macOS bootstrap permissions.
No new human-oversight violation found beyond the three inherited, fixed incidents; late WhatsApp
disclosure was a transparency violation and is now covered by failing-first regressions.
First deployment, provider retention/routing and institutional approval/notification are not verified.
Run later (not run here): managed e2e-ai-register-asgi, e2e-system-asgi, e2e-account-asgi,
e2e-consultation-asgi and e2e-asgi; first run backend-focused tests/test_ai_register_db.py with its vault source.
Requested artifacts-folder report write was refused (Operation not permitted); this repo copy is the fallback.

## Review fixes — 2026-10-14

This section supersedes the original continuation's source-behavior claims.
No commits or deployment; the center-photo and schedule-ownership implementation
in admin_content.py/admin-content.mjs was left untouched.

1. **Fixed:** triage scrubs outgoing transcript/known identity with pii.for_dni
   and restores the provider response locally. The register and privacy text
   explicitly include triage pseudonymisation. Synthetic name + DNI payload test.
2. **Fixed:** institutional/public translation no longer uses the patient
   scrubber in the single/batch content, prompt or locale translation paths.
   Phone/email preservation is tested. Cache reads perform a guarded cleanup
   only when poisoned machine rows are encountered, preserving human overrides.
3. **Fixed:** health-profile extraction stores a separate interview proposal in
   non-context chat_messages metadata. Consulta displays “Propuesta de la IA,
   revisala”; the doctor explicitly applies it to the editor and confirms the
   reviewed profile. Unreviewed edits block consultation confirmation; replacing
   edits and leaving the page warn before discard. Only confirmed profile values
   feed a subsequent interview. No new table or schema migration is needed.
4. **Fixed in source:** explicit functionality versions replace AST/file hashes.
   Settings and voice-model writes update ledger snapshots in the same transaction
   as their attributed changes. Startup performs the explicit observation.
   Dossier/Sistema GETs only read; a missing baseline cannot claim zero pending.
   Offline transaction/source-read controls pass; real-DB deduplication and rollback
   tests have been added but remain unexecuted in this sandbox.
5. **Fixed:** WhatsApp disclosure is dedicated non-context metadata, looked up
   across the conversation. Earlier system markers are recognized but excluded
   from both direct and merged histories/compaction. Successful sends alone record
   delivery. The >200-message DB regression remains part of DB acceptance.
6. **Partially fixed:** web/WhatsApp patient confirmation is enforced before a
   model turn by an account/conversation-bound, single-use exact-slot proposal.
   Deterministic auto_confirmar and same-day booking work after confirmation;
   recovering an existing pending request never approves it. All nested MCP hrefs
   are made absolute. **Still missing:** MCP confirmation inside the conversation.
   The current stateless JSON-RPC transport has no client elicitation channel, so
   MCP receives a selected-slot web confirmation link. No model-provided boolean
   or token is accepted as proof of a patient reply. Extending the MCP transport
   requires a separate elicitation implementation and acceptance test.
7. **Fixed:** fake mode exposes the microphone without a Fal key and does not send
   audio to a provider; the global/voice disable controls still hide it.
8. **Fixed:** the register, print/export and risk docs expose retention_risk for
   :free image fallbacks. Provider retention/training terms remain unverified;
   routing was not changed.
9. **Fixed:** prompt evidence contains only source-instruction hashes/lengths,
   using Spanish when supplied and the direct English source otherwise. A cached
   unedited translation is never called a human “saved text” override.
10. **Fixed:** runtime-controls.md states the configuration cache TTL of 3 seconds,
    immediate invalidation in the writing process, other-process propagation,
    and the fact that an already dispatched provider request is not cancelled.

### Review verification

Final counters are recorded in evidence/review-results.json with complete logs.
The failing-first synthetic regressions and UI lifecycle test are retained.
The four English Account render pins were regenerated only after reversing the
six intended new locale keys and the chat-notice text restored every prior byte.
Spanish and anonymous pins were unchanged; proof and regeneration utility are in
[evidence/slice8-review-account-proof.json](evidence/slice8-review-account-proof.json).

Sandbox limits: both PostgreSQL socket and loopback TCP connections are refused
(Operation not permitted). Chromium dies at macOS bootstrap before a page opens.
No full DB/browser acceptance, rendered screenshots, release or live verification
is claimed. The safe MCP web fallback does not fulfill the requested conversation
confirmation. This review fix therefore remains **PARTIAL**.

Final review counters: required/touched Python group **740 tests: 527 passed,
213 DB skips**; fresh-process MCP group **63: 56 passed, 7 DB skips**. Together
these cover 803 cases with 583 passes and 220 DB skips. An initial combined
run leaked module-fixture state into MCP (404 responses); the isolated MCP run
is the acceptance evidence, not that combined run. Full Node **2408: 2265
passed, 143 failed at Chromium launch, zero other failures**. Focused UI
**38/38**, admin simplification and diff checks pass. No public locale/pack JSON
contains the leaked teléfono/email numeric-placeholder patterns. No unrelated
asset pins or composition inventory changed in this review.

## Verification fixes (2026-10-14)

The supplied external isolation logs are the failing-first evidence for this
pass. No commit, push, deployment, provider request or institutional submission
was made. The existing center-photo and doctor schedule-ownership implementation
was preserved; only private names in their test comments were neutralized.

1. **Schema convergence:** both `_SCHEMA_SQL` and revision 0019 now declare the
   same single-column status-membership CHECK. The multi-column attestation
   expression was removed rather than weakening the shape comparer. The table is
   declared in `FORWARD_REVISION_TABLES`, so an installation at the earlier
   baseline is stamped only for its already-proven shape and receives the table
   through the forward upgrade. The notification writer validates reference,
   date and nonempty human actor before changing all attestation fields together;
   ordinary event appends never accept attestation fields. An offline regression
   compares every modeled table-shape field between the revision SQL and baseline.
   Real CHECK canonicalization and stamp/bridge DB tests remain unverified.
2. **Patient NO:** reminders now call the canonical patient cancellation operation
   directly. Email links, WhatsApp and interview CTAs share `record`; the model
   tool still only offers a human confirmation link. The new offline proof drives
   the real cancellation operation and verifies the cancelled state, patient
   actor, waitlist opt-out and slot release. The existing AI-cannot-cancel test
   remains unchanged. Actual Postgres cancellation tests remain unverified.
3. **Navigation:** System and Settings already use `screen_nav.nav_items` and
   the shared base template. All 21 sentinel render paths pass with an isolated
   offline environment, so no duplicate builder or template change was needed.
   The 24-combination golden was deliberately updated by adding only the new
   AI-register destination to the four turnos-enabled admin rows. Every previous
   entry, ordering and label is retained; intake-only and non-admin rows stay
   unchanged. Isolation prevents inherited DB state from contaminating the gate.
4. **Chat storage boundary:** patient-name lookup now uses a signature-identical
   `chat_store.get_user` delegate. The store contract includes the two proposal
   metadata delegates introduced by slice 8 and this lookup, while preserving
   every previous pinned delegate signature and one-call body.
5. **Setup:** the supplied error came from the old in-memory fixture rejecting
   `catalog_upsert(connection=...)`, not a product wizard transition. Its fixture
   now supplies the atomic config transaction, locked current-row read and empty
   audit append; unexpected SQL or AI events fail. Native setup tests suppress
   unrelated background workers. All original walkthrough, config, operator and
   first-login TOTP assertions are retained; config writes remain atomic.
6. **Reports/System gate:** no new dependency or production permission change was
   present. The offline fixtures now explicitly isolate the DB and environment
   per test, preventing inherited accounts/deadlines from returning 403. Existing
   anonymous, doctor-denial, reports-role and admin assertions pass. The System
   count changed deliberately from 9 to 10 for the new AI-register cluster.
   Real DB-backed authorization/integrity acceptance remains unverified.
7. **Public hygiene:** removed the private staff name from the tracked regression
   comment and both proposed browser-test comments. Compliance documents identify
   institutional owners by role and municipality, with no individual or named
   deployment. Stored logs replace personal filesystem prefixes with generic
   placeholders, preserving assertions, stack locations and exit evidence.
   Both the tracked-tree audit and the scan including proposed untracked files
   pass.
8. **Inventory type:** `inventory()` returns an independent deep-copied list;
   adding a newly observed function no longer tries to concatenate tuple+list.
   Offline extensibility/isolation is proven; the notification DB proof is skipped.
9. **Register width:** the table engine now measures an unpadded wrapper instead
   of the padded page host. The table stays bounded to that content width; cell
   wrapping, header box sizing and bounded form inputs prevent intrinsic content
   from enlarging the document. No divider was introduced. The existing seven-width
   browser test was preserved, but Chromium is denied at macOS bootstrap, so no
   rendered screenshot or browser acceptance is claimed. The intermittent queue
   case was left untouched as requested.
10. **Final route set:** census, app composition, module manifest and data-map
    tests all pass without route-count or manifest-classification changes.

### Verification evidence and limits

`evidence/fix2-results.json` records the 19 requested/adjacent isolated Python
groups: **334 cases, 213 passes, 121 DB skips, zero failures**. The five new
offline incident proofs, 13 HSI-integrity checks, all 36 setup checks, all 26
config checks, and the full 38-case AI-register group also pass (the latter
includes nine DB skips and overlaps the isolated groups). Divider checks,
admin simplification and `git diff --check` pass. Full local CI and the managed
DB launcher refuse because their credential source is unavailable. A separate
nonsecret loopback connection probe confirms `Operation not permitted` before
any DB query. Direct browser acceptance fails at Chromium bootstrap permission
denial, before opening a page. These failures were retained rather than changed
into skips or successful acceptance.

**Status: PARTIAL.** Implemented fixes are covered by the available offline
proofs. Fresh-install/upgrade/stamp convergence, persisted slot release, pending
notifications and seven-width live browser/export/print acceptance still need
the existing real Postgres and browser lanes outside this restricted sandbox.
The prior MCP conversation-confirmation limitation is unchanged by this task.
