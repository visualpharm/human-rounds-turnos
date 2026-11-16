# Slice 7 implementation report — 2026-10-14

## Targeted follow-up fixes — 2026-10-14

Executed the five-item `codex-slice7-fix.md` task. All changes remain
uncommitted; no push, deployment or real cloud request was performed.

1. **Checkout-env ownership:** backup inventory now delegates filename
   classification to `core.config.is_env_file_name`, which uses the existing
   `env_file_path()` accessor for the effective basename. Only core.config
   names the checkout basename. Conventional env copies, custom effective
   basenames and inode/path aliases still have recovery keys removed.
   Checkout-reader contract: 5/5 passed; archive tests: 9/9 passed;
   config tests: 20/20 passed.
2. **Synthetic SigV4 vectors:** replaced both example secrets and the
   marker-shaped access ID with explicitly synthetic fixtures. IAM signing
   key and complete S3 GET signature expectations were recomputed with a
   literal canonical request and independently unrolled HMAC stages, without
   importing the implementation. Vectors now run without a socket. Derivation:
   `docs/solutions/security/slice7-sigv4-proof.json`. S3 tests: 9 passed,
   5 loopback cases skipped; current-repo credential-marker scan: 1/1 passed.
3. **Data inventory:** added `backup_runs` to EXPECTED_DOMAIN_TABLES.
   DATA_MAP already lists it and the module manifest already classifies it
   as platform/operations state, consistently with docs/modules.md.
   Data-map tests: 6/6 passed; module-manifest tests: 36/36 passed.
4. **Closed runtime cohort:** Settings imports `platform/backup-key.mjs`
   after a successful key-generation response, resolving the URL through
   `window.SANA_STATIC`. Added that asset to both exact pinned sets.
   Cache-header tests: 60/60 passed; admin fingerprint tests: 15/15 passed.
5. **Account byte pins:** the initial English comparison identified an
   unintended backup-namespace addition to patient locale payloads. Kept
   operator backup copy outside the default patient catalog and explicitly
   included it in Settings/System; regression tests cover both boundaries.
   All nine ES/EN/anonymous responses now differ only by the single new
   backup-key manifest entry. Removing exactly that entry restores every
   original hash and raw baseline render; unrelated-byte mutation controls
   reject. All 38 prior client-loaded assets remain exact baseline bytes.
   Regenerated pins with `test/regen_accounts_pins.py`, marked
   `Regenerated 2026-10-14 (slice 7)`. Proof:
   `docs/solutions/security/slice7-render-proof.json`.
   Accounts: 20/20 passed; i18n: 49/49 passed; backup UI: 8/8 passed;
   Settings/System screen tests: 6/6 each passed.

Combined focused run: **284 discovered, 278 passed, 6 skipped, 0 failed**.
All six skips are sandbox-denied loopback binds; in-process S3, encryption,
archive and full-backup pipeline coverage ran. Other public-cut preflight
environment failures were deliberately excluded as instructed.
Log: `artifacts/backup-slice7/fix/relevant-tests.log`; exact render bytes,
reproducible proof script and regeneration output are alongside it.
`git diff --check` passed. This follow-up is focused acceptance, not a
new backend-full or release claim. Earlier validation below is historical.
Admin divider checks also passed (2/2), and the admin simplification fixture
evaluation passed. No HTML/CSS or divider markup was changed in this follow-up.

Both task files were read in order; the amendment is authoritative. Changes
remain UNCOMMITTED. No push, deploy, real cloud request or browser e2e profile
was run. The B2 producer/recovery source files were not modified.

## Per-item delivery

| Item | Files changed / added | Tests added / updated and decision |
|---|---|---|
| 1. Consistent complete archive | ops/backup_full.py, ops/backup_archive.py, manage.py; Dockerfile adds PostgreSQL clients and SOURCE_COMMIT injection | test_backup_archive.py, test_backup_inventory.py, test_backup_full.py: exported DB snapshot first, same-snapshot references/counts, data/private-pack/legacy-webdata/env inventory, file hashes, private originals, empty dirs, changes/skips, no overwrite. Commit/image + requirements/schema pin, no source bundle. |
| 2. Streaming encryption / operator key | core/backup_crypto.py, core/backup_settings.py, core/managed_secrets.py, core/config.py, static/platform/backup-key.mjs | test_backup_crypto.py, test_backup_settings.py, test_backup_full.py: roundtrip, wrong key/id, truncation/missing final/trailing data, tampered header, reordered/duplicate/cross-archive chunks, nonce counters, rewrapping, checksummed key, retained-key recovery, DB refusal and env redaction including aliases/staging. Immutable content header + mutable authenticated key envelope; no passphrase/KDF. |
| 3. R2/path-style S3 | core/backup_s3.py | test_backup_s3.py: AWS IAM signing-key and complete AWS S3 signature vectors, urllib protocol fake + separately defined loopback fake, retry, LIST pagination, PUT/HEAD/readback/delete, multipart ordering/interruption/abort, exact owned-ID cleanup, kill switch and double-gated seam. 100 MiB single PUT, 16 MiB parts above. |
| 4. Setup / Settings | core/backup_setup.py, ops/setup.py, ops/setup_router.py, admin_panel/settings_admin.py, static/platform/admin-settings.mjs, core/static_fingerprint.py, locales/es/backup.json, locales/en/backup.json, locales/{es,en}/admin.srv.json | test_backup_ui.py and existing setup/settings suites: opaque-cookie token, no DB/env writes before final commit, staged probe, explicit skip, masking/fingerprint and lock refusal. One-time response-only key with copy/download, retained-key rotation. No new route surface. Existing responsive form/card controls reused; no actual rendered/browser acceptance claimed. |
| 5. Worker / status / verification / retention | ops/backup_full_queue.py, worker.py, ops/web_runtime.py, core/backup_journal.py, core/db.py, alembic/versions/0018_backup_runs.py, migrations/__init__.py, admin_panel/system_status.py, static/platform/admin-system.mjs | test_backup_full.py: complete remote integrity, independent weekly scratch restore evidence, failed-verification invalidation, dry-run/applied signed ownership retention and tested floor, kill, missing pg_dump, explicit/live/nonempty target refusal. Worker registry/capability tests, migration head pin and platform-table classification updated. Every-size complete integrity; UTC retention buckets; daily default; same advisory lock across topologies. |
| 6. B2 / operator handoff | .env.example, docs/operations/backup-and-restore.md, backup-format.md, backup-acceptance.md, restore-rehearsal.md; DATA_MAP, architecture/settings/setup/deploy, docs/modules.md, docs/decisions.md, solutions/index | New installs default legacy B2 OFF; existing active B2 produces a plaintext warning in the ONE backup row. Migration retains old copies/code. Historical plaintext access, rotation and exact-version deletion remain operator work. Real R2/empty-host procedures explicitly NOT RUN. |

Technical decisions are recorded in docs/decisions/2026-10-w3a.md (four entries dated
2026-10-14). RPO/RTO are NOT measured; the runbook lists municipal dataset
size, capture/upload/download/restore and code-rebuild/login measurements.

## Executed validation

All Python commands used TURNOS_ENV_FILE=/dev/null and TURNOS_FAKE_AI=1.
They never read this checkout's actual canonical env/data bytes. Inventory
tests enumerate data top-level names, then reconstruct synthetic fixtures.

- Relevant Python files: **393 discovered, 323 passed, 70 skipped, 0 failed**
  (backup, setup/native router/secret generation, Settings, Sistema, managed
  secrets/config boundary, module manifest, Alembic cutover, legacy B2 producer
  and restore). Skips cover absent isolated DB configuration and forbidden
  loopback binding. Log: artifacts/backup-slice7/python-tests.log.
- Backup-specific tests: **59 discovered, 51 passed, 8 skipped, 0 failed**.
  Seven S3 wire cases and one whole-job loopback case could not bind sockets.
  The in-process protocol and complete synthetic pipeline tests did execute.
- Worker registry/composition: **26 discovered, 25 passed, 1 skipped**.
  The complete worker file was also attempted: 163 tests, six health-server
  errors from forbidden loopback binding, 82 DB-dependent skips, no remaining
  assertion failures. Logs: worker-focused.log and worker-tests.log.
- Focused UI/controller/i18n/divider tests: **140/140 passed**.
  Admin simplification fixture evaluation passed. No new dividers were added;
  existing System row borders were reviewed against the divider contract.
- Exact requested command **node --test test/*.test.mjs**: **2397 total,
  2254 passed, 143 failed**. All 143 are Chromium launch failures
  (macOS MachPortRendezvous permission denial inside this sandbox), not
  assertion failures. This command includes fixture-browser unit files;
  no test/e2e profile was launched. The gate is explicitly RED, not waived.
  Logs: artifacts/backup-slice7/node-tests.log and node-summary.json.
- git diff --check and Python compilation passed. manage.py backup --help and
  worker.py --jobs ran without provider calls. app.py remains 99 lines.

## Not verified

Actual PostgreSQL pg_dump/scratch pg_restore and the Alembic forward upgrade
were not exercised against a running disposable DB: no isolated DB source was
available under this task's no-cloud constraint. The new migration has static
DDL/classification coverage; that is not execution evidence.

Actual loopback S3 wire tests could not bind; network retry/multipart/provider
semantics therefore have synthetic in-process coverage only. Real R2 and
generic S3, >100 MiB multipart against a provider, >2 GiB real-scale validation,
municipal fresh-host recovery and RPO/RTO remain NOT RUN. Unjournaled remote
objects/ambiguous upload IDs stay protected for operator reconciliation.

No browser screenshots, Claude Design round trip, phone/tablet/desktop rendered
fit, clipboard/download behavior in a real browser, or browser e2e is claimed.
The no-real-cloud instruction precluded the normal Claude Design service loop.
The optional GLM read-only review could not start because its global ledger
was outside writable roots; implementation/review continued in the parent.

## Browser profiles to run later (not run)

- e2e-settings-asgi
- e2e-system-asgi
- e2e-asgi (full suite)

There is currently no dedicated setup-wizard browser profile. The new
/setup/backups clean-install journey needs that browser coverage before setup
acceptance. Existing Python setup/native-router checks pass, but are not an
end-to-end browser substitute. Exact rehearsal journeys and the required
360/390/768/834/1024/1280/1440 widths are listed in backup-acceptance.md.

## Complete working-tree path inventory

- .env.example
- Dockerfile
- admin_panel/settings_admin.py
- admin_panel/system_status.py
- alembic/versions/0018_backup_runs.py
- core/backup_crypto.py
- core/backup_journal.py
- core/backup_s3.py
- core/backup_settings.py
- core/backup_setup.py
- core/config.py
- core/db.py
- core/managed_secrets.py
- core/static_fingerprint.py
- design-plans/slice7-backup-requirements.md
- docs/architecture/deploy.md
- docs/architecture/settings.md
- docs/architecture/setup-wizard.md
- docs/decisions.md
- docs/modules.md
- docs/operations/backup-acceptance.md
- docs/operations/backup-and-restore.md
- docs/operations/backup-format.md
- docs/operations/backup-slice7-report.md
- docs/operations/restore-rehearsal.md
- DATA_MAP.md
- docs/solutions/INDEX.md
- docs/solutions/deploy/encrypted-backup-key-envelope.md
- locales/en/admin.srv.json
- locales/en/backup.json
- locales/es/admin.srv.json
- locales/es/backup.json
- manage.py
- migrations/__init__.py
- ops/backup_archive.py
- ops/backup_full.py
- ops/backup_full_queue.py
- ops/setup.py
- ops/setup_router.py
- ops/web_runtime.py
- static/platform/admin-settings.mjs
- static/platform/admin-system.mjs
- static/platform/backup-key.mjs
- test/admin-system-direct.test.mjs
- test/test_alembic_cutover.py
- test/test_backup_archive.py
- test/test_backup_crypto.py
- test/test_backup_full.py
- test/test_backup_inventory.py
- test/test_backup_s3.py
- test/test_backup_settings.py
- test/test_backup_ui.py
- test/test_module_manifest.py
- test/test_queue_worker.py
- test/test_setup.py
- test/test_system_status.py
- worker.py
