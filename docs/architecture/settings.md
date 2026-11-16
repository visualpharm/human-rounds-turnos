# Settings (runtime configuration)

Settings has two presentation routes over the same APIs and storage:

- Native ASGI: `/admin/settings` for General, `?view=integrations` for
  Integrations, and `?field=<CONFIG_KEY>` for a field deep link.
- Legacy compatibility: `/admin#settings/general`,
  `/admin#settings/integrations`, and `/admin#settings/<CONFIG_KEY>`.

The native screen uses ordinary General and Integrations links plus browser
history. A valid field owns its view even when a conflicting `view` query is
present; unknown or malformed state canonicalizes safely to a valid view. The
field key is matched against rendered `data-campo` attributes and is never
interpolated into a CSS selector.

Fase 4 del rework de open-sourcing (`~/.claude/plans/large-rewrok-before-open-robust-chipmunk.md`), 2026-07-30. Before this, changing anything infra-shaped (a pack chain, an API key, which languages are on) meant editing `.env` and redeploying. Settings makes it admin-editable, live, no redeploy — the same DB row `config.py` already reads for everything else.

## Where it lives

- Backend: `settings_admin.py` — `GET/POST /api/admin/settings`, `POST /api/admin/settings/probar`, all `admin_auth.require_admin(solo_admin=True)`.
- Frontend: `admin-settings.js` — shared by the native `/admin/settings`
  screen and the legacy `#settings` section. The native shell is rendered by
  `app/admin_panel/admin_settings_screen.py` from
  `templates/admin/settings.html`; rendering it never reads settings or runs
  a live probe.
- Storage: `config.py`'s existing DB layer — `catalogo` table, `tipo="config"` `id="settings"`, the SAME row `config.get()` already reads for anything in the codebase. Settings doesn't invent a new storage — it's a form over what already exists.

## Precedence and the lock

Unchanged from `config.py`, restated here because Settings is where an admin will actually FEEL it:

```
DB (this Settings row) > os.environ > .env > default
```

**`TURNOS_CONFIG_LOCK=1`** (env only, never through .env or DB — that would be circular) inverts it: env/.env win over whatever's saved here. `config.locked()` is the public accessor; the Settings page shows a banner when it's on ("your changes save but won't apply until the lock comes off"). Meant for an operator (Coolify/credential vault) who needs to override a bad value an admin saved, without deleting the DB row.

## Process keys are not settings (`app/core/settings.py`, 2026-11-11)

What decides how a process runs before the database is open — `HOST`, `PORT`, `DATABASE_URL`, the pool and its waits, the web limits, the queue worker's topology and drain, the outbound kill switch, `TURNOS_CONFIG_LOCK` and `TURNOS_ENV_FILE` themselves, the timezone, the legacy data dir, Sentry, the public origin and the install-owned packs — is declared as typed pydantic-settings classes in `app/core/settings.py` and never appears on this screen: no database override can move it. One class per read policy:

| Class | Keys | Read from |
|---|---|---|
| `ServerSettings` | `HOST`, `PORT` | environment; a `PORT` that is not an integer stops the boot (`int()`'s own error) |
| `ProcessSettings` | topology, pools, limits, worker drain and health port, lock, env file, timezone, webdata dir, Sentry | environment; an unusable value is the default |
| `WorkerSettings` | `TURNOS_WORKER_STALLED_S` | environment; a value that is not a number is `float()`'s error, exit 2 in the worker |
| `DatabaseSettings` | `DATABASE_URL`, `TURNOS_DB_MIGRATION_URL` | environment, then the env file (whitespace-only = unset), stripped |
| `InstallationSettings` | `TURNOS_PUBLIC_ORIGIN`, `TURNOS_PRIVATE_PACKS_DIR`, `TURNOS_DATA_DIR`, `TURNOS_REQUIRED_PACKS` | environment, then the env file (empty = unset) |

`XSettings.current()` is lazy: it looks at the environment (and, when a field of the class is not set there, the env file through `config.dotenv_values()`) on every call and rebuilds the frozen instance only when one of its raw values changed, so a test or an operator changing a variable needs no reset. A parse that raises raises its old reader's own `ValueError`, not pydantic's wrapper. Every field keeps the parse its reader had before (each validator is that parse, held against a copy of the old reader in `tests/test_process_settings.py`, which also fails when product code reads a declared key from `os.environ` itself). A new process key is a field there and a row in `docs/env-registry.md` (`python3 scripts/env_registry.py --write`). `config.get()` with its database layer, cache and lock is unchanged and remains the reader of everything this screen edits.

## The three groups

### Location
- **Pack chain**: which `packs/<id>` directories are active, general-to-specific (`global,ar,ar-b,ar-b-pinamar`). Discovered from `data_packs.list_packs()`; validated against a slug shape (`^[a-z0-9][a-z0-9._-]*$` per id) before saving — no path traversal. Saving calls `data_packs.invalidate()` so the new chain takes effect on the next request, no restart.
- **Identity**: a CURATED subset of `sitio.json`'s top-level keys — `site_identity.OVERRIDABLE_KEYS` (`hospital`, `city`, `province`, `network`, `network_long`, `emergency_number`, `main_center`). `main_center` («Centro principal», 2026-10-23) is a select of the installation's centers (`location.centros`; an unknown slug is refused) — the center the patient's hints name when a problem needs someone in person (`site_centers.main_center_place()`: portal refusal, no-show block, booking-horizon rejection; see docs/decisions/2026-10-w4b.md). Each is stored as its own config key, `SITE_ID_<KEY>` (e.g. `SITE_ID_CITY`), checked in `site_identity.get()` BEFORE falling through to the data pack's `sitio.json` file. Deliberately not every dotted path in `sitio.json` — the rest (hero copy, section text) stays pack content, edited by shipping a new pack, not a form field.

### Languages
Straight passthrough to the three `i18n.py` config keys — see `docs/architecture/i18n.md` for what each does:
- `LANGUAGES` (comma-separated, validated against `i18n.available_locales()` — a typo gets rejected here instead of silently degrading later)
- `LANG_DEFAULT`
- `LANG_SWITCHER_MODE` (`off` | `nav` | `secret`)

### API keys
A declarative registry (`settings_admin.INTEGRATIONS`) — 8 integration cards, each a list of `(config_key, label, "text"|"secret"|"choice"|…)`: OpenRouter, Email, Turnstile, WhatsApp (Meta Cloud API production + WAHA loopback dev), SISA/PUCO, RENAPER, HSI, Backblaze B2. Adding a 9th integration or a field to an existing one is one entry in that list — the GET/POST/masking machinery is generic over it.

**Email card (2026-10-20).** One card for the `email_transport` connector: `CONNECTOR_EMAIL_TRANSPORT` (a `"choice"` field — empty = Mailgun, the default; `mailjet`), the shared `MAIL_FROM`/`MAIL_REPLY_TO`, Mailgun's key/domain/region (`MAILGUN_API_BASE`) and Mailjet's key/secret (both secrets masked like every other). A `"choice"` field carries a closed `opciones` list (`settings_admin._CHOICES`); the POST refuses anything outside it, and the default option stores NOTHING — so a Mailgun installation that saves the card never pins `mailgun` in the DB, where it would outrank a later `CONNECTOR_EMAIL_TRANSPORT=mailjet` in env (`config.get`: DB > env). `MAIL_REPLY_TO` and `MAILGUN_API_BASE` used to be read from `os.environ` only (the region even at import time); both go through `config.get()` at call time now.

**Secrets never round-trip.** A field marked `"secret"` in the registry:
- GET returns `{"configurado": bool, "last4": "1234"|null}` — never the value.
- POST only overwrites it if `valores` carries a non-empty string for that key — an empty/untouched input in the form is "leave it alone", not "clear it".
- Clearing one requires naming it explicitly in a separate `borrar: [key, ...]` list in the POST body, so a blank input can never be misread as "delete this."

`hsi_client.py` used to read `HSI_URL`/`HSI_EMAIL`/`HSI_PASS`/`HSI_INSTITUTION_ID` straight from `os.environ` — Settings couldn't have controlled them. Switched to `config.get()` (fase 4, 2026-07-30) so the HSI card here actually does something.

## AI inference profiles (Tier 6)

`AI_INFERENCE_PROFILE` selects one OpenAI-compatible chat-completions
transport:

- `cloud_shared` is the default and is byte-compatible with the existing
  OpenRouter destination. Its destination is fixed in code and cannot be
  edited in Settings.
- `hosted_jurisdiction` requires `AI_INFERENCE_BASE_URL` and uses HTTPS only.
- `self_hosted_vllm` requires `AI_INFERENCE_BASE_URL`. HTTPS is permitted;
  plain HTTP is permitted only for `localhost`, literal loopback, or literal
  RFC1918 IPv4 addresses. Hostnames are refused for plain HTTP so an internal
  DNS name cannot rebind to a public destination between validation and use.

The endpoint must be a complete non-root path (for example,
`https://provider.example/v1/chat/completions`) and cannot contain userinfo,
query parameters, or fragments. The client refuses every redirect response.
This keeps credentials on the configured destination.

`AI_INFERENCE_API_KEY` and `AI_INFERENCE_API_KEY_BACKUP` are dedicated,
provider-neutral credentials. `cloud_shared` falls back to
`OPENROUTER_API_KEY` and `OPENROUTER_API_KEY_BACKUP` only when the matching
dedicated field is blank, preserving existing installations. Non-cloud
profiles never read or send `OPENROUTER_*` credentials. Every AI caller uses
`ai_client.available()` rather than checking a provider-named key directly.

Settings validates the selected profile and destination before saving.
`/probar` runs the same profile-neutral availability and transport path; its
failure messages never echo a key or endpoint value.

## Backup-key failover (the product owner's item 6)

The active profile's backup key is retried only on 402/403 (the key's own
quota/auth, not a broader 429/5xx outage). Once the backup proves active it
stays sticky for 5 minutes so every call does not re-hit the dead primary
first. `ai_client.active_key_kind()` reports which one is live;
`ai_health.status()["key_activa"]` and the AI inference card's "Probar"
button both surface it. This remains byte-compatible for `cloud_shared`.

## `/probar` — live tests

Not every integration has a cheap, safe live check. `_PROBES` in `settings_admin.py` currently covers:
- `openrouter` — `ai_health.probe()` (a 1-token completion), reports `key_activa`.
- `email` — the SELECTED provider (`notifications.mail.transport()`): first the keys it still lacks (`missing_config()`), then its live `probe()` — Mailgun: the domain's state on the configured region (`MAILGUN_API_BASE`; until 2026-10-20 this probe always asked the US endpoint, so an EU domain read as an error while mail was going out); Mailjet: whether `MAIL_FROM` (or `*@` its domain) is an active sender of the account. «Sistema»'s email cluster calls the same `probe()`, so the two never disagree.
- `hsi` — reachability only (reuses `system_status._reachable`).

Everything else (Turnstile, WhatsApp, SISA/PUCO, RENAPER, B2) returns `{"ok": false, "detalle": "sin prueba en vivo… mirá Sistema"}` rather than faking a check — the real signal for those lives in the Sistema page's clusters, which DO reach the live service (see below). Adding a real probe later is one entry in `_PROBES`, same shape as the three that exist.

## Sistema ↔ Settings: "Configurar →"

`system_status.py`'s clusters (`/api/admin/system`, the "Sistema" page) are the read side — richer status than a config form should try to be (OpenRouter spend, the email provider's sender state, HSI conflict counts). Fase 4 added three clusters that were missing entirely — **RENAPER** (optional login provider, `na` not `error` when unset — the site works fine without it), **PUCO/SISA** (`ok` by default since WS131 needs no key; `warn` only if the GWT-RPC public fallback was explicitly turned off), **Backblaze B2** (`warn` not `error` when unset — backups are best-effort, the site doesn't depend on them) — and wired a `"campo"` key onto any item whose fix IS a Settings field.

`admin-system.js` renders a **"Configure →"** link next to any non-`ok` item
that carries a `campo`. Direct System uses the encoded native URL
`/admin/settings?field=<CONFIG_KEY>`; legacy System retains
`#settings/<CONFIG_KEY>`. Settings selects the field's actual owning view,
scrolls its exact `.set-row[data-campo]` into view, focuses its input, select,
or textarea, and flashes a highlight for about 1.6 seconds. Every field
rendered by the shared `row()` helper carries `data-campo`, so new integration
fields inherit this behavior without extra routing code.

## Tests

- `tests/test_ai_client.py` — cloud compatibility, dedicated non-cloud keys,
  endpoint policy, redirect refusal, snapshot-safe failover, sticky expiry,
  and `active_key_kind()`.
- `tests/test_config.py` — `TURNOS_CONFIG_LOCK` precedence inversion.
- `tests/test_site_identity.py` — `SITE_ID_*` overrides win over the pack, non-overridable keys never touch config.
- `tests/test_settings_admin.py` — gate, secret masking (never echoed,
  empty=leave-alone, explicit borrar), per-group validation including AI
  profile/destination policy, and `/probar` dispatch.
- `tests/test_admin_settings_screen.py` — direct-screen role gate, no-store
  response, fingerprinted dependency order, shared navigation, and the
  shell-only no-settings/no-probe boundary.
- `tests/admin-settings-direct.test.mjs` and
  `tests/admin-settings-ui.test.mjs` — canonical query state, field-owned view,
  exact safe focus, browser history, responsive controls, and unchanged legacy
  hashes.
- `tests/e2e/admin-screen.spec.mjs` — real legacy and ASGI navigation, direct
  field deep links, Back/Forward, 360–834 px fit, and the legacy-mode direct
  404 contract without invoking a live integration probe.
- `tests/test_system_status.py` — the 3 new clusters' `ok`/`warn`/`na` states
  plus cloud/default and non-cloud AI inference status without value leakage.
- `tests/admin-sistema-ui.test.mjs`, `tests/admin-navigation.test.mjs` — nav ordering and `?v=` after the new link/section.

## Grupo «Turnos» (2026-08-01)

Un solo campo: **Máximo por paciente** (`MAX_ACTIVE_REQUESTS`, default 1) —
cuántos turnos sin atender puede tener a la vez una misma persona de un mismo
servicio. Es el piso de la cadena que resuelve `booking_limits.limit_for`
(médico → servicio → sitio); los overrides por servicio y por profesional se
editan en Contenido, no acá. Se guarda con el mismo `POST /api/admin/settings`
que el resto (`grupo: "turnos"`), se valida en `settings_admin._guardar_turnos`
(1…`booking_limits.MAX_LIMIT`) y respeta `TURNOS_CONFIG_LOCK` como cualquier
otra clave. La regla completa está en
[wizard-y-servicio.md](wizard-y-servicio.md#máximo-de-turnos-activos-por-paciente-booking_limitspy-2026-08-01).

## "Feriados" group (2026-10-19)

Site holidays (`app/modules/turnos/site_holidays.py`): one list of `{fecha, nombre}`
that closes those dates on EVERY agenda — all doctors, services and centers. It
is not a `config` key: it lives in its own catalog overlay row
`("config", "feriados")` and ignores `TURNOS_CONFIG_LOCK` (there is no env
equivalent). `grupo: "feriados"` with `valores.items` replaces the list;
`valores.nacionales: true` also adds the country pack's official dates from
today on (`feriados-nacionales.json`, `packs/ar/` — copied from
argentina.gob.ar, never computed; the button hides when the chain has no
country list, e.g. the bundled `global,example-town`). The payload counts the
pending/approved appointments already on each date (`db_slots.count_appointments_on_dates`,
ART day): they are never cancelled, the desk reschedules them. The single
read point for slot generation is `site_availability._day_exceptions`; see
[wizard-y-servicio.md](wizard-y-servicio.md). Tests: `tests/test_site_holidays.py`,
`tests/site-holidays.test.mjs`.

## "Seguridad" group (2026-08-03)

A single field: **Exempt IPs** (`TURNOS_ADMIN_IP_EXEMPT`, CSV of EXACT
IPs, never CIDR) — which IPs enter the admin login without solving
Turnstile. Since 2026-08-08 only a loopback IP VERIFIED against the real
connection can take effect (a remote IP added here exempts nobody — the
header-based branch was forgeable and was retired; scripted logins use the
signed `X-Turnstile-Test-Bypass` header instead). Full logic and history in
["IP-exempt list" in admin-panel-y-auth.md](admin-panel-y-auth.md). It is
saved through the same `POST /api/admin/settings` as everything else
(`grupo: "seguridad"`), validated in `settings_admin._guardar_seguridad`
(every line has to parse as an exact v4/v6 IP, `ip_exempt.parse_lista`)
and honors `TURNOS_CONFIG_LOCK` like any other key.

## Encrypted backup destination

The backup integration in Settings owns TURNOS_BACKUP_* policy/destination
fields, masked S3 credentials and a one-time generate/rotate recovery-key action.
The KEK is managed in the canonical env through core.managed_secrets, never
catalog; later GETs return its fingerprint only. TURNOS_CONFIG_LOCK refuses
these edits. Test performs PUT/GET/HEAD/DELETE without sending an email.
See [backup-and-restore](../operations/backup-and-restore.md).
