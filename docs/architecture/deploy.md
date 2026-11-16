# Deploy — the generic contract

Extracted from CLAUDE.md 2026-07-29, split from Pinamar-specific ops into
`docs/pinamar/deploy-coolify.md` on 2026-07-30 (Phase 8 of the open-source
rework), then moved out of this (public) repo entirely on 2026-08-05 (audit
item 0.2) into the private ops repo `humanrounds-pinamar`
(`<workspace>/human-rounds/humanrounds-pinamar/docs/deploy-coolify.md`). This
file is what's true for **any** installation, whatever host or CI you
deploy it with. The concrete Coolify setup behind the Pinamar reference
deployment now lives in that private repo, not here. A followable, generic
Coolify walkthrough (source-build, placeholder values, no private-repo
dependency) → [deploy/coolify-reference.md](../deploy/coolify-reference.md);
the abstract contract below is what any install path has to satisfy.
Upgrading an existing installation (backups, migrations, rollback) →
[UPGRADING.md](../UPGRADING.md).

- **Cache busting = content-hash fingerprinting** (Tier 4 slice 6,
  [tier4-replatform-design.md](../roadmap/tier4-replatform-design.md#static-fingerprinting-slice-6),
  `app/core/static_fingerprint.py`). Every first-party JS/CSS/MJS asset listed in
  `static_fingerprint.MIGRATED` is served at `/static/<name>.<hash8>.<ext>`,
  `public, max-age=31536000, immutable` — the URL itself changes when the
  content does, so a browser cache or any CDN TTL (there is none in front of
  the reference deployment today; see `CLAUDE.md`) never serves a stale
  bundle, and a changed file needs no manual bump anywhere. Only the exact
  CURRENT hash is served (`immutable`); a stale or forged hash falls through
  to an ordinary 404, which is `no-store`
  ([cached-401-poisons-an-asset.md](../solutions/deploy/cached-401-poisons-an-asset.md)).
  Three ways a page names a fingerprinted URL, one per HTML pipeline:
  `static_fingerprint.url(name)` in the f-string shells, `{{static.<name>}}`
  in the legacy regex pipeline (admin.html), `{{ static('<name>') }}` in the
  Jinja templates.
  **Client manifest (slice 6, batch 3 — 2026-08-20).** A handful of assets
  are loaded by URL at runtime by ANOTHER static JS file (`lab-results.js`
  → `listing.js`; `my-requests.js` → `platform/directory.mjs`; `my-requests.js`/
  `chat-widget.js` → `account-form.js`; `platform/account-form.mjs`/`platform/admin-create-appointment.mjs`/
  `dni-barcode.mjs` → `dni-barcode.mjs`, `dni-scan.mjs`), and a served file
  can't compute a hash. Those names live in `static_fingerprint.CLIENT_LOADED`
  (a subset of `MIGRATED`); every page that emits a loader emits
  `static_fingerprint.manifest_script()` once, in `<head>` — an inline
  `<script>window.SANA_STATIC={...}</script>` with their current URLs (the
  f-string shell, the reserved `{{static.manifest}}` token in admin.html, the
  Jinja global `{{ static_manifest() }}`) — and each loader resolves its
  target through that map, fail-closed: a missing entry yields `null`/a
  rejected `import()` plus a console error, never a hardcoded fallback URL, so
  the page degrades exactly as a failed network load would. A page that emits
  no loader may skip the manifest. `tests/test_static_manifest.py` pins
  loader ⇒ manifest page by page; `tests/static-manifest-loaders.test.mjs`
  pins the loaders. `scripts/smoke_prod.py`'s fingerprint loop verifies the
  manifest URLs on the live home page exactly like the `<script src>` ones.
  **Behind the municipal portal gateway (salud) a page left open past the
  portal session loses its lazy modules:** the gateway answers every request
  without a session — `/static` included — with its own 401
  `{"error":"unauthenticated","login":"<portal sign-in>"}`, the app never sees
  it (Sentry TURNOS-SALUD-4, 29.10). `manifest_script()` therefore also emits
  `recover_script()` (`app/core/static_recover.js`, inline — a static file would be
  behind the same 401): on a failed dynamic import / module script it probes
  one static file and, on exactly that 401 shape, goes ONCE to the sign-in with
  `return=<this page>` (sessionStorage guard: one trip per 5 min per tab, no
  loop); any other failure (404, offline, the test sites' Basic Auth, no
  gateway) stays the page's own error. `admin-shell.js` `sanaImportStatic`
  asks it first and shows no error line while the page is leaving. Proof:
  `tests/static-recover.test.mjs`, `tests/test_cache_headers.py`.
  First-party JS/CSS has no manual `?v=` cache-busting path. Platform files
  register in `MIGRATED`; module files register through their module manifest
  and render with `module_url()` / `module_static()`. The recursive invariant
  in `tests/test_listado_consistencia.py` rejects a new first-party JS/CSS
  manual-version URL.
- **The process entrypoint is `asgi_main.py`** (the Dockerfile's `CMD`).
  `TURNOS_ASGI=1` is the only supported serving mode: one Uvicorn listener
  serves the native `app.py` composition and its lifespan owns web-side
  background services. The legacy server and its fallback were deleted.
  Clearing or changing this flag causes a boot error; it is not a rollback.
  Roll back to a previously verified image while retaining `TURNOS_ASGI=1`
  and following [the upgrade and rollback procedure](../UPGRADING.md),
  including its schema-compatibility and retained-data checks. The admin-only
  System panel carries non-sensitive demo-refresh completion evidence;
  `/health` remains process liveness only, not refresh proof. See
  [the retirement plan](../roadmap/asgi-proxy-retirement-plan.md).
- **The queue worker is its own process, chosen by `TURNOS_QUEUE_WORKER`** (Tier 4 worker topology, 2026-08-08 — the design doc's "Worker topology" section). Unset (the code default): no worker runs, the daemon threads sweep as always. Installed values (checked on the host, 2026-10-28): pinamar.humanrounds.org and the demo run `=1`, each as one container; the value on salud.pinamar.gob.ar (Railway, managed by the municipality's side) is not visible to us — by the code, unset there means the threads run inside the web process. `=1`: `asgi_main.py` spawns and supervises `python3 worker.py` as a child process in the same container — restart with backoff, SIGTERM forwarded on shutdown, and the web app's `/health` NEVER depends on it. (A second-container mode, `=external`, existed until 2026-10-30; no installation ran it and it was removed — any value other than `1` is off.) Worker liveness: HTTP `/health`+`/ready` on `TURNOS_WORKER_HEALTH_PORT` (default 7710), plus the `procrastinate_workers` heartbeat row queryable with plain SQL. Queue-migrated so far: waitlist, reminders, the batch-2 housekeeping table-hygiene and referral-retention sweeps, and the configured data-volume backup sweep. All use their thread twin's OFF/cadence contract. Batch-2 boundaries remain: queued housekeeping is **purges-only, DB-only**; the disk check with its advisory email stays on the WEB process's legacy schedule. Filesystem jobs are registry-declared (currently referrals and configured data backups); the worker is always a child of the web process, so it sees the same data volume by construction. More worker-runtime contracts from the gate rounds: the worker only runs under an explicit mode (`1`, anything else exits 2), it runs its own scoped stalled-job recovery (a job whose worker was SIGKILLed mid-run is requeued at the next worker start and every 5 minutes; threshold `TURNOS_WORKER_STALLED_S`, floor 30 s enforced, non-numeric/NaN/inf rejected at startup), and the web-side disk alert dedups through a durable at-most-once claim in Postgres (`alert_claims`, claimed BEFORE the send — two processes or a mid-send crash can't duplicate it; at-most-once can mean ZERO on a crashed/blocked send, said loudly in the log; same-day marks from the legacy JSON state are migrated into claims on upgrade day, and old claim rows are purged by housekeeping itself).

- **Data-volume backup sweep joins the worker topology only when B2 is configured** (Tier 4 backup-worker slice). `app/ops/data_backups.py` keeps the legacy web daemon when `TURNOS_QUEUE_WORKER` is unset; with `=1` it delegates entirely to `app/ops/data_backups_queue.py`. The queue task is self-rescheduling, not cron-based: it preserves arbitrary `TURNOS_BACKUP_INTERVAL_MIN` values and the legacy durable 60-second first-run grace. It declares `filesystem=True`, `outbound=True`, and `exclusive=True`, and runs in the web container, on the same `data/` volume. A PostgreSQL **session** advisory lock spans the whole snapshot → B2 acceptance → journal-commit sweep and uses that same checked-out connection for journal reads/writes, so two workers or temporary web/worker topology skew cannot overlap even with a one-connection pool; process death releases the lock and an unjournaled accepted upload is safely retried. Each new upload now validates and records B2's immutable exact `(fileId, fileName, contentLength, contentSha1)` receipt; older journal rows lack `remote_file_id` and remain non-prunable. `app/ops/data_backups_restore.py` supplies **operator-only** paginated catalog/reconciliation, exact-id verified restore, and retention planning: it has no worker registration, route, boot hook, or activation flag. Retention is dry-run by default and keeps current plus newest prior verified receipts; it globally refuses to plan deletion when legacy/missing/orphan/hide/unknown/prefix-mismatch evidence exists. The exact `(fileName, fileId)` delete primitive serves a separately reviewed manual plan and, since schema review №11 (2026-10-25), ONE automatic purpose: the referral-file retention journals each file it deletes in `purged_files` (before deleting, so no record = no deletion), and the same sweep then deletes every B2 version of those paths by exact file id (tombstoned, retried until all are gone), never uploads a journaled path again, and does the same for `ordenes/pending/*` journal rows whose local file is gone (a 48 h scratch area; skipped when `data/ordenes` is missing so an unmounted volume cannot look like «all expired»). A file that merely vanishes from the volume is never a reason to delete its backup. `restore_version` refuses a journaled path, and the next retention pass removes a journaled file that reappeared (`booking_referrals.reapply_purged_files`). `TURNOS_BACKUP_PURGE_REMOTE=0` turns the remote deletion off; there is no automatic version pruning. **On the municipal installation (a `*.gob.ar` `TURNOS_PUBLIC_ORIGIN`, `core.config.government_installation`) the remote deletion is always off** (`ops/purge_guard.remote_purge_enabled`; the municipality runs the purge of remote copies itself, product owner 2026-10-26): the sweep gets no delete primitives and the «Sistema» block shows no purge lines. If `TURNOS_BACKUP_PURGE_REMOTE` asks for the purge there (anything but `0` or empty) the lock still holds and the service still starts, but it says so: an `ERROR` line at the start of the web process and of the worker job and in every backup pass, and a `warn` «Purga remota — Ignorada en esta instalación» in the B2 block (`purge_guard.remote_purge_ignored`); `/ready` is not affected. Keep `=0` in that installation's environment in every delivery; the test stands keep the purge on. Held by `tests/test_salud_guards.py`, which also holds the test-portal refusal on `*.gob.ar` and with `TURNOS_STAFF_PORTAL_LOGIN=1`. **There the application deletes or overwrites no backup by any path** (the product owner's hub 2026-10-26 19:48, `app/core/backup_lock.py`, held by `tests/test_backup_lock.py`): `_req_json` lets only B2 reads, lists and uploads out, and an upload only under `<prefix><path>~<sha256 of the bytes>` (a changed file goes to a new name; names uploaded before keep their plain form, `restore_version` strips the suffix); the S3 transport of the full backups sends no DELETE and a new object only after a HEAD 404, again before every retry (so its key must be allowed to read and list objects — a 403 on that HEAD fails the backup with that reason); the full-backup rotation, multipart aborts and `cleanup_incomplete` are skipped, the probe keeps its test object, Settings and the setup refuse `TURNOS_BACKUP_RETENTION_APPLY=1` (a value from the environment is ignored with an `ERROR` at start and in every full backup and a `warn` in «Sistema»). If B2 refuses the deletion for lack of permission (the application key has no `deleteFiles`: HTTP 401/403 from `b2_delete_file_version`), the pass stops at the first refusal, the paths stay pending, the sweep logs one `ERROR` line with their count and emails the owner at most once a day (`alert_claims` key `b2_delete_denied`); `ops.data_backups.last_purge()` reports `closed`/`denied`/`failed` for the last pass, and the next sweep with a key that can delete picks the pending paths up. `/ready` is not affected. Each pass also leaves its result in the one-row table `backup_purge_state` (Alembic 0034: `last_pass_at`, `last_success_at` — the last pass with no refusal and no failure —, `closed`, `denied`, `failed`), and the «Sistema» block of Backblaze B2 (`system_status._b2_purge_items`) shows three things from it: the files still waiting for the remote deletion (`purged_files` rows without `remote_done`), the last clean pass, and, as a `warn`, «Falta deleteFiles» with the count when the last pass was refused. The block shows nothing extra when `TURNOS_BACKUP_PURGE_REMOTE=0` or the database does not answer. Not covered: `cuenta-fotos/` (account photos) and the encrypted `backup_full` archives, which age out under their own `RETAIN_*` schedule. `TURNOS_OUTBOUND_KILL=1` rejects all operator B2 actions before authorization, transport, restore output, or tombstone mutation.
- **The worker has a hard, fail-closed outbound kill switch: `TURNOS_OUTBOUND_KILL=1`** (`app/core/outbound_guard.py`, 2026-08-08). When set, the worker process default-denies every Python-level `socket.connect`/`connect_ex`/`sendto`/`sendmsg` except AF_UNIX, the `DATABASE_URL` endpoint, and loopback ONLY on the DB's own port(s) — never loopback-wide, because WAHA listens on loopback ports in real deployments of this repo. Postgres keeps working (libpq connects below Python's socket layer), while Mailgun/WhatsApp/webhook connects are refused with a `[outbound_guard] BLOQUEADO` log line per attempt. Honest boundary: DNS name resolution (libc `getaddrinfo`) is below the patch, so a blocked send may still have looked up the destination's name — no message content can leave through that, but "blocked" is not "zero packets of any kind"; C extensions other than libpq and subprocesses are also outside it (the worker uses neither). Fail-closed twice over: unknown destinations are blocked by default, and a worker that requests the switch but cannot arm it exits 2 (permanent config, no supervisor retry) rather than run unprotected. Env-only (never a DB-stored setting — the guard arms before the first DB touch). This is a PROOF/SOAK tool for running the real business sweeps against fictional data without side effects — it is not how a production deployment turns notifications off (that's `MAIL_ENABLED`/provider config). Proof: `tests/test_business_queue_proof.py`.
- **Capacity settings (2026-10-25): `TURNOS_DB_POOL` (15), `TURNOS_DB_POOL_TIMEOUT` (3 s), `TURNOS_THREADPOOL_SIZE` (40).** Per process; the process prints the effective values at boot. Measured behaviour, why the defaults stay, and what a second web process would change → [capacity.md](capacity.md).
- **Email provider = the `email_transport` connector (2026-10-20).** Every email (patient notices, staff invitation/reset, disk alert) leaves through `notifications.mail.send()`. Nothing set → **Mailgun**, exactly as before: `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, optional `MAILGUN_API_BASE` (empty = US; an EU domain needs `https://api.eu.mailgun.net`). `CONNECTOR_EMAIL_TRANSPORT=mailjet` → **Mailjet** Send API v3.1: `MAILJET_API_KEY` + `MAILJET_API_SECRET` (the account's or a sub-account's key pair) and a `MAIL_FROM` whose address or domain is a validated sender in that account (SPF `include:spf.mailjet.com` + the `mailjet._domainkey` DKIM record on the sending domain); open/click tracking is disabled per message. Shared by both: `MAIL_FROM` (`Name <address>`), `MAIL_REPLY_TO`, `MAIL_ENABLED=0` (hard off; the demo pins it). Missing keys = a logged silent skip; a booking, an approval or an account creation never depends on the send. Settings «Email» → «Probar» and «Sistema» check the selected provider. No schema change. Tests: `tests/test_email_transport.py` (+ the recorded request `tests/cassettes/mailjet/send_ok.json`).
- **`/app/data` is the persistent volume.** It covers (shadows) whatever
  is baked into the image's `data/` directory at build time — anything the
  app writes at runtime (uploaded referrals, generated files) needs to
  live under `/app/data` or it disappears on the next rebuild. Data-pack
  content (site data, service/center/doctor info) is a different thing
  entirely and goes only through `packs/` — never a hand-built
  `os.path.join(..., "webdata", ...)` path; always `sitedata.path(fname)`.
  See [data-packs.md](data-packs.md). **The one exception is a clinic's
  own scaffolded town pack** (Tier 6): the first-run installer writes it
  to `/app/data/packs/<slug>` ON THE VOLUME (`data_packs.install_packs_dir()`),
  never the image-tree `packs/`, and registers it as the installation's
  REQUIRED private pack via **three `.env` boot keys that move together**:
  `TURNOS_PRIVATE_PACKS_DIR=<install_packs_dir()>` (the resolver finds the
  town on the volume), `TURNOS_REQUIRED_PACKS=<slug>` (the boot gate
  validates it before the ASGI application accepts traffic), and
  `PACK_CHAIN=<prefix>,<slug>` (the chain that NAMES it). The first two are
  strictly process/`.env`-only (never the DB); `PACK_CHAIN` is also mirrored
  into DB config (`config.guardar`) for normal unlocked admin edits, and a
  **locked-config restart** (`TURNOS_CONFIG_LOCK=1`, DB overrides ignored)
  serves the boot-key copy instead. The third key is load-bearing: the
  required-pack gate VALIDATES the private town from its boot keys, but
  which chain is SERVED is resolved separately, so writing the first two
  with `PACK_CHAIN` in the DB alone (the pre-fix state) validated a private
  pack the chain never served under the lock — a locked-config restart then
  fell through to the bundled public default. Persisting `PACK_CHAIN`
  alongside the private root and required pack in `.env` (all three written
  by the one bootstrap-key writer `config.save_env_keys`) makes validate
  and serve agree under every restart mode. So a rebuild that wipes the image
  cannot delete a clinic's data, and a missing/corrupt volume refuses boot
  instead of silently serving nothing. Detail → [install-owned-town-packs-live-on-the-volume.md](../solutions/deploy/install-owned-town-packs-live-on-the-volume.md).
- **The container's canonical `.env` lives ON that same volume**
  (`/app/data/turnos.env`), so those three boot keys survive a rebuild too.
  The image sets `ENV TURNOS_ENV_FILE=/app/data/turnos.env` in the Dockerfile;
  `config._env_path()` reads exactly that one path — never an overlay, never a
  fallback to a second file — so `save_env_keys` writes the boot keys to the
  volume and the readers read them back from the volume. Before this, those
  keys were written to `config.ENV_PATH` (`/app/.env`) — which `.dockerignore`
  EXCLUDES from the image, so nothing was baked: `save_env_keys` created the
  file at RUNTIME in the ephemeral container filesystem, and the next build
  wiped it. The town pack was durable on the volume but UNREACHABLE
  (validate-private, resolve-public). Bootstrap keys (`DATABASE_URL`,
  `HOST`, `PORT`) are unaffected — they are process-env in every deploy
  (compose `environment:`, Coolify env), which sits above the file in the
  precedence, so a first boot with an empty `turnos.env` still resolves them.
  **Single effective path, three modes, all unchanged except the container
  default:**
  - *Container (compose/Coolify/`docker run`)* — the image default points at
    `/app/data/turnos.env` on the persistent volume. Requires the volume to be
    mounted at `/app/data`: `docker-compose.yml` does (`app_data:/app/data`);
    in Coolify, add a persistent storage path `/app/data` (the Pinamar
    reference deployment historically did NOT have one — see the ops repo
    `humanrounds-pinamar`; without it the config is no more durable than the
    ephemeral container filesystem).
  - *Explicit override* — setting `TURNOS_ENV_FILE` in the process
    (compose `environment:`, Coolify env, `docker run -e`) REPLACES the path
    entirely (it is not merged): that's the single-effective-path contract,
    the same one the hermetic E2E harness relies on.
  - *Direct local* (`TURNOS_ASGI=1 python3 asgi_main.py`, no image) — `TURNOS_ENV_FILE` is
    unset, so `_env_path()` falls back to `config.ENV_PATH` (the checkout
    `.env`) exactly as before; nothing about local development changes.
  Proof → `tests/test_install_owned_packs.py::TestReplacementContainerLifecycle`
  (a synthetic rebuild wipes the image tree and the ephemeral container
  filesystem; the town still resolves and validates from the untouched volume
  config — under `TURNOS_CONFIG_LOCK=1` with a conflicting DB `PACK_CHAIN` and
  the process triplet cleared; a missing or corrupt configured private pack,
  with the canonical config still present on the retained volume, still fails
  closed; and the contrast case exercising `config.ENV_PATH` — the
  runtime-created ephemeral file — goes red). Note the honest boundary of this
  same-volume design: a TOTAL `/app/data` loss takes the canonical config with
  it, so the gate no longer has the fact that any pack is required and boot
  degrades to bundled public packs — not detectable without storing the
  requirement-fact off-volume, a separate architecture this slice does not add.
- **Rollback of the canonical-config-path change is image-only and keeps the
  config durable.** Revert the Dockerfile `ENV` line (drop
  `TURNOS_ENV_FILE=/app/data/turnos.env`) and redeploy, BUT keep
  `TURNOS_ENV_FILE=/app/data/turnos.env` as an explicit runtime override
  (compose `environment:`, Coolify env, `docker run -e`) so the install
  keeps reading its boot keys from the volume — exactly the explicit-override
  mode above. Do NOT copy `turnos.env` into the ephemeral `/app/.env`: that
  path is not baked (`.dockerignore` excludes `.env`) and would be recreated
  in the ephemeral container filesystem, lost again on the next rebuild. The
  runtime override is what makes the rollback durable; reverting the image
  alone (override off) only returns direct-local-style behavior to the
  checkout-`.env` default and would lose the boot keys on the next build. No
  schema migration, no data move, no DB change. The `config._env_path()`
  reader, the `save_env_keys` writer, the setup wizard, the data-pack
  resolver, and the DB bootstrap are all untouched by this change; only the
  container default moved.
- **Secrets via runtime env, never baked in.** `.env` is not committed;
  `.env.example` documents every variable name and what it defaults to.
  Runtime config that doesn't need a redeploy (pack chain, languages, API
  keys) can also be set live from `/admin` → Settings once an admin
  account exists — see [settings.md](settings.md).
- **Verify a deploy actually landed before trusting it.** A single
  successful request against a freshly deployed app doesn't prove every
  request is hitting the new build — a stale container left running
  alongside the new one (common with rolling-deploy setups) can serve
  the old and new build on alternating requests. `scripts/smoke_prod.py`
  is the reusable check: security matrix, key pages, every versioned asset
  compared against the local checkout, and repeated cache-busted requests
  that must all agree. Run it after every deploy, whatever platform you're
  on; don't diagnose a data problem in production until it's green.
- **Migrations run at boot, not as a separate step, and Alembic is the
  schema authority.** `db.py` takes a cross-process advisory lock and runs
  Alembic's `command.upgrade("head")` before the app starts serving — see
  [UPGRADING.md](../UPGRADING.md) for the full mechanism, and
  [Alembic (slice 4)](../roadmap/tier4-replatform-design.md#alembic-slice-4)
  for the original cutover design. There is no manual migrate command to
  remember to run. A database with pre-Alembic tables but no
  `alembic_version` row (only an old dump can be one: every installation
  went through the one-time bridge, removed 2026-11-05) is refused before
  any DDL; one boot of the release tagged `bridge-last` converts it
  ([UPGRADING.md](../UPGRADING.md), "A database from before Alembic").
  **Every NEW schema change is a real Alembic revision under
  `alembic/versions/`, and nothing else** (since 2026-10-25). The text that
  used to be `db._SCHEMA_SQL` is the frozen first revision,
  `alembic/baseline/0001_schema.sql` (hash-pinned, never edited): a fresh
  install runs it and then every revision, an existing one only the
  revisions it lacks, so the two cannot drift. Write revision SQL
  idempotently (`IF NOT EXISTS`, guarded `ALTER`s); foreign keys, triggers
  and dollar-quoted bodies are fine. Regenerate `alembic/schema-head.txt`
  (`python3 scripts/schema_snapshot.py --write`) in the same commit — it
  puts the schema change in front of the reviewer and a test compares it
  with a fresh install.
- **Every response carries the browser security headers, and HEAD is
  answered like GET** (2026-10-11). `app/core/security_headers_middleware.py`,
  the outermost boundary, adds `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN` with `Content-Security-Policy:
  frame-ancestors 'self'`, `Referrer-Policy:
  strict-origin-when-cross-origin`, and — only when the request arrived over
  https (`X-Forwarded-Proto`, else the socket scheme) —
  `Strict-Transport-Security: max-age=31536000` for the host itself. A route
  that sets one of them keeps its own value. No proxy configuration is
  needed, and none should duplicate them. `app/core/head_as_get_middleware.py`,
  the innermost one, runs a HEAD as the GET of the route that owns the path
  and drops the body, so a monitor probing `HEAD /` sees what a browser
  sees. Proof: `tests/test_security_headers.py`.
- **`/health` (liveness) + `/ready` (readiness) + graceful shutdown**
  (Tier 4 slice 7, [tier4-replatform-design.md](../roadmap/tier4-replatform-design.md)):
  `/health` is process-up only, no DB, can never hang or 500; its body is
  `{"status": "ok", "version": "<git commit>"}` — `app/core/version.py` reads
  `SOURCE_COMMIT` (the Dockerfile build arg Coolify passes, also the image
  label `org.opencontainers.image.revision`), `COOLIFY_GIT_COMMIT_SHA` or
  Railway's `RAILWAY_GIT_COMMIT_SHA`, and answers `"unknown"` without one.
  Sentry tags events with the same commit as `release`. `/ready`
  reuses `system_status.probe_db()` (the same check the admin «Sistema»
  panel runs) — SELECT 1, the schema's base table, Alembic at `head` — and
  answers 503 the moment any of those isn't true. Both endpoints are served
  by `app/routers/health.py`, mounted by `app.py`, using `app/core/health.py`'s
  liveness/readiness functions. No legacy handler or alternate serving mode
  remains. Both endpoints
  both are **exempted from the pre-launch Basic Auth gate**
  (`site_auth.py`'s `_EXENTAS`) — the image's `HEALTHCHECK` (Dockerfile,
  `/health` through the image's own Python, since it carries no curl or
  wget; `scripts/healthcheck.py` probes the loopback of the family `HOST`
  binds — Coolify `0.0.0.0`, Railway `::`, where 127.0.0.1 is refused) can't be expected to carry the shared site credential. On `SIGTERM` (what
  Coolify/Docker send on redeploy), uvicorn finishes the in-flight
  requests before exiting instead of severing them mid-response, and the
  shutdown hook (`ops/web_runtime.stop_app_modules`) then runs
  `app_modules.shutdown_all()`; Docker's own `stop_grace_period` bounds the
  wait (the old drain-seconds knob went with the legacy
  server, 2026-08-30). `tests/test_graceful_shutdown.py` proves both.
- **Sentry-compatible error tracking ships wired but OFF** (`error_tracking.py`,
  same slice). No `TURNOS_SENTRY_DSN` = fully inert, `sentry_sdk` never even
  imported (lazy import inside `init()`, same "sibling-file" cost
  discipline the module-contract adapter uses for FastAPI). Turning it on
  for real needs an explicit PII-scrubbing review first — an error's stack
  trace can trivially carry a DNI or a patient's name — see that module's
  docstring; this is a deliberate, separate future decision, not something
  this slice pre-approves. **The scrubbing review is done (2026-10-19,
  asked for by the municipality before a Turnos-only Sentry project):**
  `init()` sets `send_default_pii=False`, `include_local_variables=False`,
  `max_request_body_size="never"`, no tracing/profiling, and
  `scrub_event`/`scrub_breadcrumb` rebuild every event from an allowlist
  (exception type + `pii.Scrubber`-swept message + frame locations; request
  method + masked path only). `tests/test_error_tracking.py` proves it end to
  end with the real SDK and a capturing transport. Setting the DSN on an
  installation stays its operator's call.
- **Launch signals — the installation tells its own Sentry what nobody can read from outside**
  (TASK-4, 2026-10-30; `app/core/launch_signals.py`, `app/ops/launch_digest.py`). Off unless
  `TURNOS_SENTRY_SIGNALS` lists `failures`, `digest`, `crons` (or `all`) AND `TURNOS_SENTRY_DSN` is set;
  then: closed failure codes for the places that swallow an error on purpose (the message of the event IS the
  code; the worker's client runs `signals_only`, it reports nothing but these), one daily digest event of integers (requests, HSI outbox, worker queue, backup ages, hourly counters
  of sign-ins, mail and WhatsApp; table `launch_signal_counters`) and Sentry Crons check-ins for the worker
  (`turnos-worker`), reminders, waitlist and both backups. The monitors are created by the first check-in
  (the schedule rides in it); alert on a missed or failed check-in of any of them. Every installation sets
  its own `TURNOS_SENTRY_ENV` (default `production`): `salud`, `pinamar-test`, `demo`. The two test sites
  send NOTHING since 2026-11-10 (no DSN on either app): the municipal project's alert rules have no
  environment filter and mail its people, so a test site may report only after those rules filter on `salud`
  (decision of that day). Nothing here takes free
  text: a new code goes in
  `launch_signals.FAILURE_CODES`, a new number in `DIGEST_KEYS`, and `test_launch_signals.py` pins both
  against their call sites and against `error_tracking.scrub_event`. Since 2026-11-10 the steps after an
  already-saved action report too: the patient notices (`notify_*_failed`), a new request's referral file,
  reading and slot hold (`booking_*_failed`), the 503 «database unavailable» (`db_unavailable`, at most once per
  15 minutes per process, off the event loop: every 503 of a database that does not answer goes through
  `deps.answer_db_unavailable`, which sends it — the handler for the `DbUnavailable` no route caught, every
  route that catches it and answers its own body, and the domain results that carry that 503; no other code
  answers 503 for the database, `tests/test_db_unavailable_answer.py` holds it; only `deps.DB_UNAVAILABLE_ERRORS`
  count as the database not answering — a connection lost under a statement among them, raised by the pooled
  `with` as `db.DbConnectionLost`, never a deadlock nor a protocol violation (08P01, a bug of ours); one lost
  under the commit itself is `db.DbCommitUnknown`, whose default answer says the outcome is unknown
  (`outcome: unknown`, no `Retry-After`) because the write may have landed; any other failure is the route's
  own error without the signal), the day-before reminder (`reminder_failed`: a failed send gives its claim back and a later sweep,
  within the day-before 09:00–21:00 window, tries again — 30 minutes, then 1 h, 2 h, 4 h later, given up for
  the day when the next try would fall after 21:00, its column left empty so it is not counted as sent; one
  with nothing to send — no address and no phone, or mail off — is no failure: it keeps its claim and is
  marked `reminder_skipped`, not counted either) and
  the pre-consult interview invitation (`interview_reminder_failed`, the same).
- **Container artifact integrity (the release path), authored but UNPROVEN.**
  Tier 6 slice 9. [.github/workflows/release.yml](../../.github/workflows/release.yml)
  implements a **main-ancestry-admitted, staging-first, scan-gated, exact-digest
  promotion** policy: (0) **Main-ancestry admission (fail-closed)** — the commit
  being released MUST be reachable from `main`
  (`git merge-base --is-ancestor <sha> origin/main`, run against a full
  `fetch-depth: 0` checkout). A SemVer tag pushed from a commit that has not
  landed on `main` is refused BEFORE any build, so `packages: write` is never
  reached for off-main work; the tag-push trigger otherwise fires for any `v*`
  tag regardless of branch. (1) Build ONCE to a **unique non-release staging ref** (`staging-run-<run_id>-<attempt>`)
  — NO version tag, NO `:latest` — with `provenance: true` and `sbom: true`
  (SLSA/SBOM attestations attached to the image in the registry). (2) Capture the
  **immutable manifest digest** (`build-push-action`'s `outputs.digest`) and
  emit a standalone SPDX JSON SBOM via `anchore/sbom-action` (syft). (3) **Bind
  every check to IMAGE@DIGEST** — the standalone SBOM, the vulnerability scan,
  and the promotion all reference the exact digest the build produced, never a
  mutable tag. (4) **Vulnerability scan as a fail-closed gate** —
  `anchore/scan-action` (grype) scans IMAGE@DIGEST with `fail-build: true`,
  `severity-cutoff: critical`, `only-fixed: true`. The resulting policy is
  precise: refuse the release when a CRITICAL-severity vulnerability WITH a
  known upstream fix is present; vulnerabilities below CRITICAL, and CRITICALs
  with no fix available, do NOT block. `continue-on-error: true` lets
  the SARIF upload and refusal step fire, but NEITHER the version tag NOR
  `:latest` is created on failure — only the disposable staging ref exists.
  (5) **Scan success promotes the EXACT scanned digest to BOTH tags in one
  step** — `docker buildx imagetools create -t "$IMAGE:$VERSION" -t
  "$IMAGE:latest" "$IMAGE@$DIGEST"` copies that exact digest (no rebuild, no
  mutable-tag lookup) so `:version` and `:latest` resolve to the SAME scanned
  digest and its attestations. (6) **Immutable digest record** — posted to the
  run summary as a paste-ready row in [RELEASES.md](../policies/RELEASES.md)'s exact
  ledger column order (`| version tag | immutable digest reference | date | notes |`)
  and a `release-manifest-*.tsv` artifact (version, full `@ref`, date),
  appended by a human to RELEASES.md (workflow has `contents: read`, does NOT
  auto-commit — that would need `contents: write`, a guardrail it keeps off).
  (7) **Digest-pinned Compose install** —
  [docker-compose.release.yml](../../docker-compose.release.yml) cancels the
  dev `build: .`, pins `ghcr.io/visualpharm/sana-turnos@sha256:<64-hex-digest>`,
  and requires `SANA_IMAGE_DIGEST_HEX` to be **nonempty** (the repository plus `@sha256:` are fixed; Compose's `:?` checks presence only — it does NOT
  regex-validate 64-hex at config time, so an unset/empty value fails at config
  but a malformed one parses and fails at runtime pull, never becoming a mutable
  tag). **Cryptographic signing is deferred:** keyless
  `cosign` via GitHub OIDC needs `id-token: write` AND a real Actions job,
  impossible while this account's Actions billing is frozen — so `id-token:
  write` is deliberately absent (the workflow explains why) and
  provenance/SBOM attestations (which do NOT need it) are the interim integrity
  evidence, explicitly weaker than a signature. **Honesty: none of this has run.**
  No hosted Actions run, no image publication, no `docker pull`, no scan result,
  and no real digest exists for this repo; RELEASES.md' real-release ledger is
  empty by truth (zero published images), carrying one clearly-marked SYNTHETIC
  format-example row outside the ledger. The workflow is correct on paper against
  pinned action SHAs and is verified ONLY by the static contract test
  [tests/release-artifact-integrity.test.mjs](../../tests/release-artifact-integrity.test.mjs)
  (structure, not execution). The existing strict guardrails are preserved:
  `contents: read` + `packages: write` only, the `release-ghcr` concurrency
  group, the SemVer tag-push/dispatch `if:` gate, the **main-ancestry admission
  check**, the lowercase-image
  transform, and every action pinned to a 40-hex SHA. The slice's own
  acceptance gate (a real `syft`/`grype` run, zero unresolved CRITICAL-with-fix
  CVEs before either tag, a seeded CRITICAL-fixed finding blocking BOTH tags,
  the digest in RELEASES.md, and a clean-machine `docker
  pull` + compose-up reaching `/setup`) remains Tier 7 execution work; the
  preparation lands the mechanisms and the static check so that gate is
  checkable the moment a real release is possible. Design →
  [tier6-delivery-design.md](../roadmap/tier6-delivery-design.md) Gap 4 / slice 9.

## Encrypted portable backup

The backup_full registry job and manage.py backup CLI capture an exported
PostgreSQL snapshot before files, encrypt locally and verify complete remote
readback. Cloudflare R2/path-style HTTPS S3 is installation-configurable;
no Coolify backup service is needed. Full operational and recovery contract:
[backup-and-restore](../operations/backup-and-restore.md). Legacy B2 code remains;
new setup installations disable it. Real R2 and empty-host acceptance is NOT RUN.

## Moved out of CLAUDE.md: the queue worker, the demo data and the persistent volume

Former `CLAUDE.md` wording, moved here on 2026-11-06 to keep that file under its size limit:

> Migrated jobs: waitlist, reminders, housekeeping (purges only — the disk check and advisory email stay web-side in every topology), referral-file retention, the chart-summary and translation-cache warmups, and `hsi_sync`.
>
> The demo's data regenerates itself daily (`demo_refresh.py`): today's queue is built from the doctors' real agendas for TODAY and left nearly full, so the demo never shows its deploy day.
