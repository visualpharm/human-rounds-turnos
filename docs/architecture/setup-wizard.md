# First-run installer wizard

Fase 5 del rework de open-sourcing (`~/.claude/plans/large-rewrok-before-open-robust-chipmunk.md`), 2026-07-30. Before this, a fresh checkout with an empty database had no guided path to a working install — someone had to hand-edit `.env`, hit `/admin`, and hope the defaults lined up. `setup.py` detects that exact state (nothing configured yet) and walks the deployer through it: location → data pack → language(s) → admin account → API keys, then closes itself permanently.

## Where it lives

- Backend + wizard UI: `setup.py` — one module, both the gate (`is_fresh_install()`, `handle()`) and the server-rendered steps. No new persistence, no new auth mechanism: it writes through `config.py`'s DB layer and creates the admin account via `db_operators.operator_create()`, the exact function `admin_users._create()` calls.
- Wired into `routing.py` as the FIRST module in every method's list (`GET`/`POST`/`PUT`/`DELETE`) — it has to see every request before anything else decides what to do with it.

## The gate: what counts as "fresh"

`is_fresh_install()` returns True only when the DB has **zero `operators` rows AND zero `catalog` rows of type `"config"`**. Both have to be empty — either one existing means some deployer already touched this installation.

Three deliberate exceptions, each with a reason:

- **DB down → not fresh.** `db.DbUnavailable` is caught and treated as "not fresh" rather than forcing every route into a wizard nobody can complete against the very storage layer that's unreachable. A real fresh install with the DB down just gets today's ordinary degraded behavior everywhere else.
- **The public demo (`demo_installation.enabled()`) → never fresh.** The demo mints admin sessions for a synthetic "demo" user with no real operator row, and runs in legacy `TURNOS_WEBDATA_DIR` mode (may have zero config rows too) — by the literal DB check it would look "fresh" and hijack every demo route into a self-install wizard. `TURNOS_DEMO_MODE=1` is itself the deployer's explicit configuration; the wizard has nothing to add.
- **One-way latch.** The moment either check comes back non-empty, the process remembers "configured" forever (`_LATCH`) and never queries the DB for this again — after the wizard finishes, this check costs nothing for the rest of the process's life. Before that point it re-checks the DB on every request (two cheap `SELECT`s), because a second browser tab or another process finishing the wizard first has to be picked up immediately.

## What "fresh" changes about every request

`setup.handle()` runs before every other route:

- `/setup` and `/setup/<step>` (GET/POST) — the wizard itself. Once the gate is closed, these always 404 (checked fresh on every request, not cached — "Once an operator exists, `/setup` 404s forever").
- `GET /` — a minimal "this site is being configured" holding page, rendered directly (not a redirect: patients hitting the bare domain shouldn't be bounced into a deployer-facing wizard entry point).
- Static assets (`/static/`, `/webdata/`, `/vendor/`, `/data/`, and known asset extensions) — pass through untouched, so the wizard's own `<link>`/`<img>` tags and the site's existing CSS keep working.
- Everything else (GET) — `303` redirect to `/setup`.
- Everything else (POST/PUT/DELETE) — `503 {"error": "instalacion_no_configurada", "setup_url": "/setup"}`. There's no real operator or config to act on yet, so a booking/login/admin-API call has nothing to do.

Once an operator exists, `handle()` returns `False` immediately for every non-`/setup` path (near-zero overhead) and lets the normal registry run.

## The wizard itself

Server-rendered, no framework — one `<form>` per step, per `wizard-design` (a step shows only its own task, text-only back links, no chrome beyond the process header) and `form-design` (content-width fields, inline errors, one primary action). Steps, in order:

1. **Welcome** — one line of context, one CTA.
2. **Location** — country / province / town. Country is pre-filled from the `CF-IPCountry` header when the deployment sits behind Cloudflare; otherwise a best-effort free geo-IP lookup (`ip-api.com`, no key, 1.5s timeout, wrapped so it never raises or blocks the page — `TURNOS_SETUP_SKIP_GEOIP=1` disables it outright). Either way it's a *proposal*: the deployer can always overwrite it.
3. **Data pack** — `_propose_chain()` matches the entered country/province/town against `data_packs.list_packs()` by exact (case-insensitive) display name at each level. A town-level match reuses that pack outright. No match → scaffold a new one (see below), display name defaulting to the entered town.
4. **Language(s)** — checkboxes over `i18n.available_locales()`, a default-language select. A single language selected forces the switcher off (nothing to switch between); two or more default to the `nav` switcher.
5. **Admin account** — username/password/confirm/email, TOTP now-or-later (mirrors `admin_users.py`'s own choice). The password is hashed with the shared Argon2 password library the moment it is submitted; only the **hash**, never the plaintext, travels in the wizard's cookie afterwards.
6. **API keys** — one field, OpenRouter, optional. This step's POST is also the commit — see below. Everything else (Mailgun, WhatsApp, HSI, B2, …) is deferred to Settings, which the Sistema page already nags about.
7. **Done** — success, the new username, the TOTP secret/otpauth URI shown exactly once if generated now, a link to `/admin`.

## State between steps: a signed cookie, never the DB

Each step's answers accumulate in `st_setup`, an HMAC-signed cookie (same shape as `admin_auth`'s session token: `<base64 payload>.<hex sig>`, HttpOnly, SameSite=Lax, `Secure` when the request came in over HTTPS). **Nothing is written to the DB until the final commit.** This is load-bearing, not a style choice: the fresh-install gate is `operators == 0 AND config_rows == 0`. If an earlier step wrote a config row (say, the pack chain) before an operator existed, the very next request would see `config_rows > 0` and the gate would slam `/setup` shut with a 404 mid-wizard — the deployer would be locked out before finishing.

The signing key is per-process (`TURNOS_SETUP_SECRET` env to pin it, otherwise a random key generated at import). There's no admin password yet to derive a key from — that's exactly what the wizard is about to create — and deriving from `DATABASE_URL` would leak a DB credential fragment into a cookie signature. The app runs one `uvicorn` process (`asgi_main.py`, see the repo's `Run` section), so a random per-process key is fine for the normal case; a restart mid-wizard just restarts the wizard, which is an acceptable cost for a one-time bootstrap flow.

## The commit (`POST /setup/apikeys`)

One atomic sequence:

1. Scaffold the new pack on disk if one was requested (below).
2. **Register three boot keys together** (`config.save_env_keys`) — writes `TURNOS_PRIVATE_PACKS_DIR=<install_packs_dir()>`, `TURNOS_REQUIRED_PACKS=<slug>`, and `PACK_CHAIN=<prefix>,<slug>` to the effective `.env`. These are boot keys (process/`.env` only, never the DB — see [data-packs.md](data-packs.md) and the [required-pack readiness note](../solutions/security/required-private-pack-readiness.md)), so they cannot go through `config.guardar`. The private root + required pack make the scaffolded town durable across rebuilds AND fail-closed if its volume is ever missing or corrupt; the chain is persisted ALONGSIDE them so a **locked-config restart** (`TURNOS_CONFIG_LOCK=1`, DB overrides ignored) still SERVES the town instead of falling through to the bundled public default — without it the boot gate would validate a private pack the chain never serves (validate-private, serve-public). The same chain is also written to the DB in step 3 for normal-operation admin edits; under the lock the boot-key copy wins. Skipped for a *matched* (pre-existing) pack — only a brand-new scaffolded town is the clinic's own install-owned data.
3. `config.guardar({...})` — `PACK_CHAIN`, `LANGUAGES`, `LANG_DEFAULT`, `LANG_SWITCHER_MODE`, and `OPENROUTER_API_KEY` if given. Same partial-merge write `settings_admin.py`'s own save handlers use.
4. `db_operators.operator_create(username, rol="admin", totp_secret=..., email=..., pass_hash=...)` — the same INSERT `admin_users._create()` drives. `operator_create()` gained an optional `pass_hash` parameter for this (see `db.py`): pass a pre-computed hash instead of a plaintext password, so the wizard never has to carry the plaintext past the step that received it.
5. `db_auth.admin_security_set_deadline(...)` — same 7-day TOTP enrollment grace period `admin_users.py` grants a fresh account without TOTP.
6. `setup.mark_configured()` — flips the in-process latch immediately, no DB round-trip needed to know the gate is now closed.

A username collision at this point (only reachable if a second browser tab wins a genuine race to finish first) surfaces as a plain 404 on the retry — the top-level gate re-evaluates before the wizard's own nicer in-page validation ever runs, since the moment the other tab commits, `is_fresh_install()` is already `False`. Documented rather than special-cased: the second tab's deployer just goes to `/admin` and signs in with whichever account won.

## Scaffolding a new town pack

`_scaffold_pack(display_name, province, parent_id)`:

- Slugifies the display name (ASCII-fold, lowercase, hyphens; collision-safe via `_unique_slug()`, which appends `-2`, `-3`, … against `data_packs.manifest()`).
- Writes **onto the persistent volume**, not the image tree: `data_packs.install_packs_dir()` is a configured `TURNOS_PRIVATE_PACKS_DIR`, otherwise `<data_dir>/packs` (`/app/data/packs` in prod — the volume, not the repo's `packs/` that a rebuild wipes). A pack scaffolded into the image tree would vanish on the next release; this is the durability bug the volume path exists to close. See [the volume note](../solutions/deploy/install-owned-town-packs-live-on-the-volume.md).
- Writes `<volume>/<slug>/pack.json` (`level: 4`, `parent` = whichever country/province pack matched, `name` = the entered display name) and `<volume>/<slug>/sitio.json` (city/province/hospital/network all set to the display name — a deliberately generic skeleton, not a copy of `example-town`'s services/doctors/centers content).
- Freezes `<volume>/<slug>/INVENTORY.json` (`data_packs.write_inventory`) so the reviewed required-pack readiness gate admits the pack on every later boot, and so a later byte-level change refuses boot instead of silently serving tampered data.
- Calls `data_packs.invalidate()` so the new pack resolves on the very next request — no restart needed.

This matches the plan's literal scope ("create the pack dir + pack.json + sitio.json skeleton") rather than cloning `example-town`'s fictional services/doctors: a scaffolded install boots with an empty catalog (gracefully — every consumer already degrades to `site_identity._DEFAULTS`/empty lists when a file is missing, that's the whole point of the pack-mode rework), and the deployer fills in real centers/services/doctors from the admin panel afterward.

## Verified end-to-end (not just mocked)

Beyond `tests/test_setup.py` (26 cases, in-memory `_FakeDB` + a temp `packs/` dir, same isolation pattern `test_data_packs.py` uses), this was driven for real once: a genuinely empty Postgres database, the real server process, `curl` walking all five POSTs with a real cookie jar. Confirmed: `/` served the holding page before setup, the wizard created a real `operators` row and a real `catalog` row, `packs/<slug>/{pack.json,sitio.json}` landed on disk with the right content, `/` afterward rendered the new town's name (the scaffolded pack resolves and boots), and `/setup` 404s from that point on. Torn down after (dropped the temp DB, removed the scaffolded pack dir) — nothing about that DB or that pack is part of this repo's history.

## Tests

`tests/test_setup.py`:
- `TestIsFreshInstall` — the gate's five conditions (empty, an operator exists, a config row exists, DB down, demo mode) and the one-way latch.
- `TestGateRouting` — holding page at `/`, redirect elsewhere, static passthrough, `503` on non-GET, `/setup` 200 while fresh, `404` once configured (including a `POST` to a sub-path), DB-down never forces setup mode.
- `TestCountryProposal` — `CF-IPCountry` honored, unknown codes shown raw, geo-IP failure/skip degrade without raising.
- `TestWizardEndToEnd` — the full walkthrough (operator + config + pack scaffold all land correctly, password never rides the cookie in plaintext, matched-pack path skips scaffolding, TOTP-now shows the secret once, password mismatch re-renders inline, the two-tabs-racing-to-finish case 404s cleanly).
- `TestConfiguredInstallDoesNotChangeConfig` — a configured install never calls `config.guardar` from this module.

## Encrypted backup step (2026-10-14)

The current order is location → pack → languages → admin → backups → API keys
(final commit). /setup/backups stages credentials and a generated random
recovery key in short-lived process memory; the signed cookie holds only its
opaque stage token. Nothing touches the DB/env until the final commit.
The key is shown once with copy/download; later views show its fingerprint.
Connection testing uses staged values. Explicit skip leaves an external-backup
warning. New installs set TURNOS_BACKUP_OFF=1 for the legacy plaintext B2 job.
An expired stage must be repeated; it cannot silently enable backup.
