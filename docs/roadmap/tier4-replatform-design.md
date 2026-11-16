# Tier 4 — backend re-platform (design)

Status: BINDING design for Tier 4 (`open-source-readiness-plan.md`, "Tier 4 —
backend re-platform (Phase 3, strangler, route by route)"). Written 2026-08-05
against `origin/main` at `8ff59ac` (Tier 2 module contract + pilot and Tier 3 test
harness both DONE). Same contract as `module-contract-design.md` and
`tier3-harness-design.md`: what exists today and is NOT re-done, per-area design
with concrete mechanisms, explicit out-of-scope, sequenced slices each
independently landable with its own gate, then a risk register and the decisions
only the product owner can make. This doc merges into `docs/architecture/` once implemented;
until then it is what an implementation is reviewed against.

**This is an architecture document, not a build order.** No code in this tier is
written from this doc directly — each slice below gets its own short
implementation pass, gated on the previous slice's acceptance criteria and on
`scripts/ci_local.sh` staying green throughout.

## What exists today and is NOT re-done

- **The module contract (Tier 2)** — `app_modules.py`'s loader, `module.json` v2
  schema, `merge_routes()`'s `after`-anchor splice algorithm, the
  enabled/disabled/broken lifecycle, hooks, `GET /api/admin/modules` /
  `GET /api/nav`. Tier 4 EXTENDS this contract with a second route-registration
  shape (a FastAPI router alongside the existing bare-function shape) — it does
  not replace the manifest, the loader, or the enable/disable semantics.
- **The test harness (Tier 3)** — e2e Playwright tier, the routes tier
  (`test/_routes_fixture.py`'s worker-DB pool + truncate-based disposal), the
  cassette layer, the four-point run ladder. Every strangler slice runs through
  this unchanged; Tier 4 adds ONE new fixture flavor (FastAPI `TestClient`, see
  "Test harness extension" below), never a parallel harness.
- **Datetime/tz doctrine (`tz.py`)**, **identity-from-session**, **`pii.py`
  scrub/restore**, **the i18n request-binding `ContextVar`** — all four are
  transport-agnostic today (plain function calls, no `BaseHTTPRequestHandler`
  coupling). Ported as-is; see "Cross-cutting invariants" below for exactly
  where each lands in the new stack.
- **`admin_auth.py` (682 lines), `accounts.py` (2270 lines), `passkeys.py` (689
  lines)** — inventoried before writing this design (not re-derived from memory):
  session cookie is a stateless HMAC (`_mint_token`/`_verify_token`, no table,
  30-day sliding `Max-Age`); TOTP is hand-rolled HOTP/TOTP (`_hotp`,
  `_totp_valido`, RFC 6238, base32 secret, no `pyotp`); passkeys are hand-rolled
  WebAuthn — CBOR decoding, COSE-to-JSON key conversion, ES256/RS256 signature
  verification, all stdlib, zero dependencies (`passkeys.py:80-263`); Turnstile
  is a thin wrapper over Cloudflare's verify HTTP endpoint (`turnstile.py`, not
  audited as part of "auth" — see below); roles are `admin | operador | medico |
  admisionista`, gated centrally by `admin_auth.require_admin(handler,
  solo_admin=False, permitir_medico=False, permitir_reportes=…,
  permitir_marca=…)`; patient accounts (`accounts.py`) are DNI/photo sign-up +
  coverage lookup + an SSO reset flow, session-cookie-based, separate identity
  space from operator accounts; `usuarios.feed_token` (ICS feeds) is an
  opaque token (stored hashed since 2026-11-06), not a session at all.
- **Nine background daemon threads**, inventoried from `server.py:main()`
  (L3766-3906): `watcher` (owner's WebTurno poll, dormant outside legacy
  single-owner mode — RETIRED 2026-08-16 with the rest of server.py's WebTurno
  client, `ops/watcher.py` deleted; see
  `test/test_server_webturno_retirement.py`), `reminders` (day-before email sweep), `waitlist` (expired-
  offer sweep), `demo_refresh` (daily, demo-only), `chart_summary`
  (pre-generation), `translate_warmup` (every 6h, only with a 2nd language
  enabled), `hsi_resync`/`hsi_sync` (thread/manual exact-replay fallback vs.
  durable bidirectional journal — mutually exclusive, `hsi_sync` is the live
  one), `data_backups` (B2 off-box
  backup), `ai_health` (OpenRouter health probe + reprocess). Every single one
  is an **interval-loop sweep**, not a one-shot enqueue-and-process shape — this
  fact drives the queue-library pick below.
- **Historical/superseded `?v=` cache-busting** and its former advisory hook
  (`.claude/hooks/vbump-drift-guard.py`) — this recorded pre-fingerprint
  inventory no longer describes the current tree; all first-party assets now
  use immutable registry fingerprints and manual `?v=` is rejected.
- **Zero runtime pip deps beyond psycopg/Pillow/segno** — confirmed
  (`requirements.txt`, `Dockerfile`). No FastAPI/uvicorn/Jinja2/Alembic
  reference anywhere in the tree today (checked). Tier 4 is the first slice of
  this repo's life to add a real dependency surface.

## Strangler coexistence mechanism — the concrete answer

**Recommended: in-process reverse proxy, one public port, unchanged Coolify/
Traefik config.** The legacy `ThreadingHTTPServer` keeps running, verbatim,
bound to `127.0.0.1:7701` (internal-only) inside the SAME container. A new
uvicorn/FastAPI process becomes the public front door on `0.0.0.0:7700` — the
port Coolify already exposes, so **no infra change on any slice**. FastAPI
mounts real routers as routes migrate; a catch-all route at the very end
(lowest priority) reverse-proxies anything unmatched to the internal legacy
port via `httpx.AsyncClient`, streaming request and response bytes — including
headers, cookies, and the request body — through unchanged.

Rejected alternatives, with reasons:
- **Second public port + Traefik path-based routing.** Rejected: needs a
  Coolify/Traefik config change (in the private `humanrounds-pinamar` ops repo)
  on every slice that migrates a new path prefix, multiplies the surfaces that
  can drift between prod and demo, and fights CLAUDE.md's own "the webhook
  already works, don't touch it" invariant repeatedly instead of once.
- **A direct ASGI adapter that fakes `BaseHTTPRequestHandler`'s interface**
  (`self.rfile`/`self.wfile`, synchronous blocking I/O) so legacy handler
  functions can be called straight from an async request cycle, no reverse
  proxy. Rejected: `routing.dispatch()` and the ~40 handler modules it calls are
  built tightly around blocking-socket semantics — large referral-image/PDF
  uploads, ICS feed streaming — and faithfully emulating that surface inside an
  async ASGI cycle is itself a significant, bug-prone reimplementation with a
  wide blast radius if wrong. The reverse-proxy option requires **zero changes
  to any legacy code path** — legacy code runs literally unmodified in its own
  thread pool — so a strangler slice can never accidentally break an
  unmigrated route by construction.

**Migration mechanics, concretely:**
- `asgi_main.py` (new, <100 lines per the roadmap's own `app.py` budget) starts
  the legacy server on a background thread bound to `127.0.0.1:7701`
  (`server.ThreadingHTTPServer((HOST, PORT), Handler)` with `PORT` overridden),
  then hands control to uvicorn serving `app` (the FastAPI instance) on
  `0.0.0.0:7700`.
- `app.py` mounts `routers/` per migrated slice, in the SAME `after`-anchor
  order the legacy `merge_routes()` already encodes (Starlette matches routes
  in registration order for overlapping prefixes, same shape as the existing
  golden-order guarantee) — no second ordering scheme to maintain.
- The catch-all proxy route is the ONE thing every slice needs to keep
  correct: it must forward the exact method, headers (including `Cookie`,
  `X-Forwarded-*`), body (streamed, not buffered, for large uploads), and
  return the legacy response's status/headers/body unmodified. This is the
  single highest-scrutiny piece of code in the whole tier — see the risk
  register.
- **Dockerfile change: one line.** `CMD ["python3", "server.py"]` becomes
  `CMD ["python3", "asgi_main.py"]`. Rollback of the ENTIRE strangler mechanism,
  at any point, is reverting that one line — the legacy server is untouched and
  boots exactly as it does today.
- **Verification order, per CLAUDE.md's own doctrine**: land slice 0 (below) on
  `demo.humanrounds.org` first, run `scripts/smoke_demo.py` plus a manual pass
  over cookies/ICS-feed/file-upload paths, soak for a stated minimum period
  (see "Decisions for the product owner"), THEN cut prod over. This is not optional given
  "hospital users must not be logged out."

## Auth replacement (slice 1) — library picks and migration story

**Do not adopt an all-in-one auth framework** (`fastapi-users`, `authlib`).
Reasoned rejection: this repo's identity model is four staff roles + a separate
patient-account space + bare feed tokens + Turnstile + an IP-exempt bypass list
— nothing genuinely "auth-library shaped" about role gating that already reads
"is this session admin, or operador, or medico, or admisionista, with these
specific opt-ins" (`require_admin(handler, solo_admin=False,
permitir_medico=False, …)`). Forcing that into a generic library's user/role
model would fight the library harder than it would save code. Instead:
**adopt targeted libraries only for the pieces that are genuinely risky to
hand-roll** (cryptographic verification), and keep session/role dispatch as our
own FastAPI dependency, a direct structural port of `require_admin()`.

| Piece | Today | Tier 4 |
|---|---|---|
| Session cookie | Hand-rolled HMAC (`_mint_token`/`_verify_token`) | `itsdangerous.URLSafeTimedSerializer` — same algorithm class, byte-different format |
| TOTP | Hand-rolled HOTP/TOTP, RFC 6238, stdlib | `pyotp` — same RFC, same base32 secret format, **drop-in, no data migration** |
| Passkeys | Hand-rolled CBOR/COSE/ES256/RS256, stdlib | `webauthn` (PyPI) — see migration story below, **the one genuinely hard item** |
| Turnstile | Thin wrapper over Cloudflare's HTTP verify endpoint | **Unchanged.** Not "hand-rolled auth logic" in the sense this tier means — there's no meaningful Python Turnstile SDK to adopt, and `turnstile.py` is already a 15-line HTTP call. Ported as-is behind the new FastAPI dependency, not rewritten. |
| Role gating | `admin_auth.require_admin(handler, …)` | `deps.require_admin(solo_admin=False, permitir_medico=False, …)` — a `Depends`-returning factory, same parameter shape, same central-gate invariant |
| IP-exempt bypass | `ip_exempt.py`, socket-peer for localhost, header for proxied | Loopback-only since 2026-08-08 (verified peer via `proxy_trust.real_peer`; the header branch for remote IPs was spoofable and is retired — see the milestone section), called from the new dependency |
| Patient accounts | `accounts.py`, DNI/photo sign-up, own session cookie | Business logic, not "auth library" territory — ported behind the same session mechanism once its own slice lands (see "Out of scope") |

### Migration story for EXISTING sessions and credentials

This is the roadmap's explicit ask ("hospital users must not be logged out or
lose passkeys; if unavoidable, say so explicitly").

- **Session cookies — self-expiring dual-verify, LOW risk.** The new code
  mints with `itsdangerous` going forward; the old HMAC verifier stays live,
  read-only, as a fallback path for any cookie that fails the new verifier but
  passes the old one — for up to the cookie's remaining `Max-Age` (≤30 days by
  design). No forced logout, no data migration (the cookie is stateless — there
  is no session table to migrate). Once the old format's maximum lifetime has
  elapsed post-cutover, the fallback path is dead code and gets deleted.
- **TOTP secrets — zero-risk, no migration needed.** `pyotp.TOTP(secret)`
  computes the identical standard algorithm the hand-rolled `_hotp`/
  `_totp_valido` already implement against the SAME stored base32 secret. This
  is a pure code swap with a parity test (feed a fixed set of `(secret,
  timestamp)` pairs through both implementations, assert identical 6-digit
  codes) — no existing operator's enrolled secret changes meaning.
- **Passkeys — the genuinely hard item, HIGHEST risk in this whole tier.**
  `passkeys.py`'s `cose_a_json()` converts the raw COSE public key into a JSON
  shape for storage (`passkeys` table, `public_key jsonb`) — the RAW COSE bytes
  are NOT what's stored. A library swap therefore needs one of two paths, and
  **which one is a decision for the product owner** (see below), because it's a genuine
  engineering-risk trade-off, not a mechanical choice:
  1. **Adapter (recommended, pending a data-shape spike).** Write a thin
     function that reconstructs verifiable key material from the stored JSON
     COSE fields (`kty`/`crv`/`x`/`y` for EC keys, `n`/`e` for RSA) into
     whatever shape the `webauthn` library's verify call expects. If the
     stored fields are a faithful COSE-JSON projection (they should be, since
     `cose_a_json()` exists specifically to make the key inspectable/storable
     without losing verification-relevant fields), this needs zero data
     migration — new registrations go straight through the library, existing
     rows verify through the adapter, and the hand-rolled verifier is deleted
     the moment the adapter is proven against a captured real registration.
  2. **Grandfather.** Keep the hand-rolled `verificar_firma()` alive
     permanently for rows created before cutover; route only NEW registrations
     through the new library. Simpler to build, but means two verification
     code paths live in the codebase indefinitely (or until every existing
     passkey happens to be re-registered, which nobody can force — a lost
     phone is the only thing that naturally retires a row).
  A hard credential loss (neither path can rescue a genuinely incompatible
  stored format) forces the affected staff member back to password+TOTP, which
  still works — this is the acceptable-worst-case, not a full lockout, and it
  is stated here explicitly because the roadmap asked for it to be said
  explicitly if unavoidable.

## Module contract adapter (slice 3)

The Tier 2 acceptance property — **disable a module → its routes 404, its nav
entries disappear, zero central edits** — must survive route-by-route
migration to FastAPI. `module.json`'s `routes` entries grow a second shape
(the manifest schema is already superset-tolerant by design, per
`module-contract-design.md`):

```json
{"module": "modules.intake.routes_patient", "router_attr": "router", "after": "…"}
```

A route entry naming a `router_attr` is a FastAPI `APIRouter` object;
`app_modules`'s loader mounts it via `app.include_router()` at the SAME
`after`-anchor position `merge_routes()` already computes for the legacy list
(reusing the existing splice algorithm, not inventing a second ordering
scheme). A route entry with no `router_attr` (every not-yet-migrated route)
keeps flowing through the reverse-proxy fallback automatically — migrating a
module's routes to FastAPI is additive per-route, never a whole-module cutover
requiring simultaneous work.

**Enablement, since FastAPI can't cleanly un-mount a router at runtime**: every
migrated endpoint takes a `Depends(require_module_enabled("intake"))`
dependency that raises `HTTPException(404)` the moment `app_modules.is_enabled
(id)` is False — same observable behavior (404, indistinguishable from an
unknown path, per the product owner's 2026-08-05 decision) as today's `dispatch()` gating,
achieved at the dependency-injection layer instead of by removing routes. This
is a genuinely different mechanism from today's, so it gets its OWN pilot proof
(mirrors Tier 2's own pilot discipline): migrate `intake`'s routes first (same
module Tier 2 already proved the contract on, smallest blast radius), assert
disable/enable still drives 404s and the sidebar with zero central edits.

## i18n → Jinja2 + jinja2-fragments (slice 2)

**The resolution engine does not change.** `i18n.resolve_lang()`,
`enabled_languages()`, `default_lang()`, `switcher_mode()`, the cookie/
Accept-Language priority chain, `pack_l10n.py`, `translate.py`'s cache+gate —
all are already framework-agnostic (`config.get()`-driven, no
`BaseHTTPRequestHandler` coupling). What changes is ONLY the template
substitution mechanism: the `{{t.<dotted>}}` regex pipeline
(`i18n.render()`) is replaced by Jinja2 template functions
(`env.globals["t"] = i18n.t_or`, so a template calls `{{ t("about.hero.intro",
"Acerca de") }}` — same fallback contract, same canonical-Spanish-default rule
this repo's own invariant requires).

- **Request-scoped language survives unchanged as a mechanism, moves to
  middleware.** Today `routing.dispatch()` calls `i18n.bind(resolve_lang
  (handler))` once per request and resets in a `finally`. The FastAPI
  equivalent is a small ASGI middleware doing the identical bind/reset around
  `call_next()` — the `ContextVar` itself, and every downstream `i18n.active()`
  call site, is untouched.
- **`{{sitio.*}}` ordering invariant survives.** `site_identity.render_page()`
  today calls `i18n.render()` before `site_identity.render()` so a translated
  string introducing a fresh `{{sitio.*}}` token still resolves. Jinja2's
  template compilation makes this moot for NEW templates (both token families
  become plain function calls inside one render pass, no ordering to get
  wrong) — but the FOUR pages `site_identity.py` serves today keep rendering
  through the legacy `{{t.*}}`/`{{sitio.*}}` pipeline until they're the actual
  pilot (see below), so the ordering rule stays load-bearing until that slice
  lands, not deleted prematurely.
- **Pilot: the four `site_identity.py` pages** (`index.html`, `about.html`,
  `privacy.html`, `terms.html`) — chosen because they're already the most
  fully i18n-proven surface in the repo (`i18n.md`'s "Proven this slice, safe
  to build on") and because `test_golden_pages.py`'s byte-identity doctrine
  gives this migration a ready-made acceptance test: render both ways
  (legacy pipeline vs. new Jinja2 templates), assert byte-identical HTML for
  the canonical (Spanish, no-op i18n) case, and assert IDENTICAL resolved
  strings for a non-canonical language.
- **`jinja2-fragments`** — included per the roadmap's own wording, used where
  it earns its place: partial re-renders for interactive panel screens (a
  direct precursor to Tier 5's htmx/Preact-islands direction). Not a hard
  requirement for slice 2's pilot pages, which are static and don't need
  fragment rendering — its first REAL user is whichever admin screen slice 3's
  module-contract pilot migrates next.
- **JS-side i18n (`i18n.js`, `SanaI18n.t()`) is UNCHANGED** — it's already
  client-side and framework-agnostic; the server-side switch to Jinja2 doesn't
  touch `GET /api/i18n/<lang>.json` or the inline-catalog bootstrap mechanism.

## Alembic (slice 4)

Replaces `migrations/__init__.py`'s hand-rolled `apply_pending()` (which today
runs BEFORE `_SCHEMA_SQL` creates any table, and is guarded by
`pg_advisory_lock(hashtext('turnos:migrations'))` against concurrent-boot
races). **Alembic is scoped to schema-migration tooling only — it does NOT
bring SQLAlchemy ORM into the application.** `db.py`'s raw psycopg query
functions are entirely untouched; Alembic's `env.py` uses a
`sqlalchemy.create_engine()` connection purely as the execution vehicle for its
own migration scripts (SQLAlchemy Core, not the ORM), the same way this repo
already treats `migrations/` as pure DDL runner code with no relationship to
`db.py`'s query layer.

- **The one-time cutover risk**: production already has a fully-formed schema
  from `_SCHEMA_SQL`/existing numbered migrations. The FIRST Alembic migration
  must be a no-op "baseline" that matches the CURRENT schema exactly, and
  production gets `alembic stamp head` (a metadata-only write to Alembic's own
  `alembic_version` table) rather than `alembic upgrade head` — running the
  baseline migration for real against prod would attempt to re-create tables
  that already exist. Getting the stamp step wrong is the risk-register entry
  below.
- **The advisory-lock idiom is reused, not replaced.** Alembic has no built-in
  multi-instance startup-migration coordination; wrap `command.upgrade()` in
  the SAME `pg_advisory_lock(hashtext('turnos:migrations'))` call already in
  `db.py`, so a Coolify rolling redeploy that briefly runs two containers can't
  race two migration runs against each other — identical protection, new
  runner underneath.
- **`_SCHEMA_SQL`'s "new column on an existing table goes straight into
  `_SCHEMA_SQL` as `ADD COLUMN IF NOT EXISTS`, never a migration" rule is
  superseded by Alembic's own norms once this lands** — every schema change
  becomes a real Alembic revision, including additive columns. This is a
  genuine workflow change for future contributors, called out here so it
  doesn't get silently violated the first time someone adds a column out of
  habit.

## Background daemon threads → Postgres-backed queue (slice 5)

**Recommendation: `procrastinate`, not `pgqueuer` — reasoned pick.** Every one
of the nine existing daemons is an INTERVAL-LOOP SWEEP (retry every N minutes,
run once a day, run every 6 hours), never a one-shot "enqueue a task, process
it ASAP" shape. `procrastinate` ships a first-class periodic-task decorator
(`@app.periodic(cron=…)`) that maps 1:1 onto every existing daemon's actual
behavior — migrating a daemon becomes "wrap the existing sweep function body in
a decorated task," not a redesign. `pgqueuer` is a leaner, more minimal
durable-queue-plus-worker library with no comparable native periodic-scheduling
primitive as far as this design could establish — adopting it would mean
hand-rolling the "run this every N minutes" scheduling logic ON TOP of the
queue, which reintroduces exactly the kind of custom timing code this tier
exists to retire. `procrastinate` also has a SYNC connector (not async-only),
which matters because `db.py`'s query functions are synchronous psycopg
calls — a daemon's task body can call them directly with no async rewrite of
business logic, keeping each daemon's migration to its scheduling wrapper, not
its internals. Both are explicitly "no Redis" per the roadmap; neither
introduces one.

- **procrastinate's own schema stays OUT of the Alembic chain.** It ships its
  own versioned SQL migration files, applied via its own CLI
  (`procrastinate schema --apply`), independent of this app's migration
  numbering — coupling procrastinate's upstream schema evolution to this app's
  Alembic revision history would be a maintenance trap for no benefit. This is
  a deliberate simplicity choice, recorded so it isn't "fixed" into one system
  later without re-deriving the reasoning.
- **Pilot: `waitlist.py`** — chosen for the smallest, most isolated blast
  radius (mirrors the Tier 2 hook migration's own precedent of picking
  `reminders.py`'s intake hook for the same reason). Acceptance: the periodic
  task fires at the identical cadence and produces the identical observable
  side effect (an `appointment_offers` row / a queued notification) as the
  thread-based sweep it replaces, proven by a parity test over a fixed time
  window, plus the existing `cancel-waitlist.e2e.mjs` Tier-3 test staying green
  unmodified (it doesn't care which mechanism produced the promotion).
- **The remaining eight daemons migrate module-by-module, open-ended** — same
  "coverage expansion, no fixed target count" pattern Tier 3 used for its own
  routes tier. Explicitly out of this design's exhaustive scope; the pilot
  proves the mechanism, not a migration order for the rest.

## Static fingerprinting (slice 6)

> **Current implementation note (2026-08-21):** the transitional `?v=`
> doctrine and hook described in historical paragraphs below are retired.
> Platform assets use `MIGRATED`; module-owned assets use exact v2 manifest
> entries and `module_url()`. The first-party source invariant permits no
> manual JS/CSS version query.

Kills manual `?v=` bumps and `.claude/hooks/vbump-drift-guard.py`'s reason to
exist. Content-hash the bytes of every served JS/CSS file once (at process
startup — this repo's Coolify deploy model already rebuilds the container on
every push, so "at startup" and "at build" are the same moment in practice,
and this matches the existing convention of computing `i18n.py`'s mtime cache
at first-use rather than adding a separate build step); expose a template
helper (`{{ static('nav.js') }}` → `/static/nav.<hash8>.js`) and serve the
fingerprinted tree with `Cache-Control: public, max-age=31536000, immutable` —
since the URL itself changes on content change, Cloudflare's 4-hour TTL stops
mattering entirely, no bump needed ever again for a migrated asset.

- **Partial-migration state is safe by construction**: fingerprinted URLs and
  legacy `?v=`-bumped URLs are just different query/path shapes serving the
  same underlying files — both mechanisms coexist without conflict for as long
  as some assets are migrated and others aren't. `vbump-drift-guard.py` is
  deleted only once every asset it currently watches has moved to a
  fingerprinted URL, not as part of this slice.

## `/health` + `/ready` + graceful shutdown + optional Sentry (slices 0 and 7)

- **`/health` ships in slice 0** (minimal — process-up liveness only, no
  dependency checks) purely because slice 0's own deploy verification needs
  SOMETHING to smoke-test the new topology against.
- **`/ready` (slice 7) checks a cheap `SELECT 1` and no-pending-migrations**,
  matching `estado_sistema.py`'s own philosophy (timeout-guarded external
  probes that degrade to warn/error, never hang or 500) — reuse
  `estado_sistema.py`'s probe helpers rather than writing a second
  implementation of "check Postgres is reachable."
- **Graceful shutdown (slice 7) is a genuine correctness fix, not just a
  port.** Today's `server.py:main()` only catches `KeyboardInterrupt` in its
  `finally` block around `app_modules.shutdown_all()` — a Docker/Coolify
  `SIGTERM` on redeploy is not guaranteed to route through that same path
  cleanly. FastAPI/uvicorn's lifespan `shutdown` event is signal-driven
  properly; it becomes the ONE place `app_modules.shutdown_all()` is called
  from, replacing today's `finally` block outright.
- **Sentry (slice 7, OPTIONAL per the roadmap) — off by default, and treated
  with the SAME caution as `pii.py`'s external-AI-provider rule.** An error
  payload's stack trace or breadcrumb can trivially capture a DNI, a name, or
  a referral filename from local variables in scope at the point of failure —
  this is exactly the "nothing that identifies a person leaves for an external
  provider unless it has to" invariant, applied to an error tracker instead of
  an AI call. No DSN configured = fully inert (matching every other optional
  integration in this repo — Mailgun, OpenRouter, Turnstile). Turning it ON at
  all needs an explicit scrubbing review BEFORE any DSN is set, not after — see
  "Decisions for the product owner."

## Deps, lockfile, and Docker story

New runtime dependencies this tier introduces: `fastapi`, `uvicorn[standard]`,
`httpx` (the strangler reverse proxy), `jinja2`, `jinja2-fragments`, `alembic`,
`sqlalchemy` (Alembic's execution engine only, no ORM usage), `pyotp`,
`webauthn`, `procrastinate`, `itsdangerous`, `python-multipart` (FastAPI file
uploads — the referral-image path needs this the moment its route migrates),
and optionally `sentry-sdk` (slice 7, inert without a DSN).

- **No new dependency-management tool.** `requirements.txt` stays
  hand-pinned-exact-version, the same convention `psycopg[binary]==3.3.4` /
  `Pillow==12.2.0` / `segno==1.6.6` already use — introducing a NEW
  dependency-management paradigm (poetry, pip-tools) in the SAME tier that
  introduces a new web framework would multiply this tier's risk for no
  benefit at this repo's size. Revisit if the dependency count grows enough to
  make manual pinning genuinely painful — not a Tier 4 decision.
- **Dockerfile**: same `RUN pip install --no-cache-dir -r requirements.txt`
  shape. Verify before slice 1 lands that `webauthn`'s CBOR/crypto dependencies
  are pure-Python (no new `apt-get` system library needed) — if not, the
  Dockerfile's existing `apt-get install poppler-utils ffmpeg` line grows one
  more package, not a new mechanism.
- **`CMD` changes once, in slice 0** (see the strangler section above) — no
  further Dockerfile churn expected from slices 1-7; they add Python
  dependencies and application code, not new processes or ports.

## Cross-cutting invariants — where each one lands

| Invariant | Today | Tier 4 |
|---|---|---|
| Datetimes always converted to install TZ | `tz.py`, zoneinfo-based | Unchanged — pure function, called identically from a FastAPI handler or a Jinja template |
| Identity always from session, never request body | `require_admin()` reads the cookie, handler signatures never accept identity fields | `Depends(require_admin(…))` — structurally enforced the same way: a migrated route's Pydantic body model must never declare `usuario`/`dni`/identity fields |
| `pii.py` scrub before any external AI call | Called at each AI call site (`ai_orden.py`, chat modules) | Unchanged call sites; the referral-image upload endpoint's migrated FastAPI handler must call the identical scrub functions — verified per-route in that slice's parity check |
| i18n request-scoped language | `routing.dispatch()` binds/resets a `ContextVar` | A FastAPI middleware binds/resets the SAME `ContextVar` around `call_next()` — see the i18n section above |
| The module contract's enable→404 property | `dispatch()` gates on `app_modules.route_enabled()` | `Depends(require_module_enabled(id))` — see the module contract section above |
| Manual first-party JS/CSS version query | Historical `vbump-drift-guard.py` and manual bump | Retired: platform and module registries fingerprint every first-party asset; the source invariant rejects `?v=` |

## Test harness extension (Tier 3 survives, one new fixture flavor)

- **The e2e Playwright tier is the strongest regression net during this whole
  tier and needs ZERO changes.** It drives a real browser against a real port
  (7700) — transparently, it cannot tell whether a given URL is served by the
  legacy handler or a migrated FastAPI router, so every existing `*.e2e.mjs`
  file is a live, continuous proof that the strangler cutover hasn't broken
  anything, slice after slice, with no test-file changes required.
- **The routes tier gets ONE new fixture flavor**, additive per Tier 3's own
  "extends, never replaces" doctrine: `test/routes/_fastapi_fixture.py`, built
  on FastAPI's `TestClient` (in-process, synchronous, no socket needed — a
  genuine simplification over the existing worker-DB-pool socket dance for
  routes that have migrated) but reusing the SAME per-test-TRUNCATE disposal
  and advisory-lock-guarded worker-DB-pool bootstrap as `_routes_fixture.py`,
  not a second database-lifecycle scheme.
- **Golden request/response parity is the per-route migration acceptance
  test, run once, not forever.** For each route being migrated: a one-time
  audit script (`scripts/route_parity_check.py <path>`) sends the same request
  to the legacy handler (direct function call) and the new FastAPI endpoint
  (`TestClient`), diffs status/body/headers (excluding genuinely volatile ones
  — `Date`, a fresh `Set-Cookie` nonce), and the empty diff is committed as the
  slice's PR evidence. This does NOT become a permanently-running test suite
  entry for every migrated route (that would double the test count forever for
  a temporary migration proof) — EXCEPT for response shapes where an ongoing
  regression risk clearly outweighs that cost (the ICS feed's exact byte
  format, any JSON a client parses by key order) — those get a permanent
  parity test, judged case by case at migration time, not pre-decided here.

## Out of scope for this design

- **Tier 5's frontend re-platform** (ES modules, `admin.html` decomposition
  into `/admin/<screen>`, Preact islands) — this design's job is the BACKEND
  strangler; the admin shell keeps being served as today's single HTML file
  with inline routing until Tier 5 starts, whether the routes behind it are
  legacy or FastAPI.
- **`accounts.py`'s patient-facing sign-up/coverage flow as a "auth library"
  concern** — it's business logic (DNI photo capture, RENAPER/PUCO lookups),
  ported behind the new session mechanism when ITS OWN slice migrates, not
  bundled into slice 1's auth-library work.
- **Rewiring the remaining 8 background daemons beyond the `waitlist.py`
  pilot** — open-ended follow-up, same as Tier 3's routes-tier coverage.
- **Any physical file move of the 100+ root `turnos`-module files into
  `modules/turnos/`** — that's Tier 2's own still-open item, orthogonal to
  this tier; a file can be re-platformed to FastAPI from its current
  repo-root location exactly as easily as from a `modules/turnos/` location.
- **A lockfile/dependency-management tool swap** — see "Deps" above.
- **Turning Sentry ON** — the integration ships inert; enabling it for real is
  a follow-up decision gated on the scrubbing review named above.
- **Migrating `admin.html`'s inline `{{t.*}}`-equivalent strings or any
  patient-site page beyond the slice-2 pilot four** — i18n's own "Remaining"
  list (`i18n.md`) is untouched by this tier; slice 2 proves the Jinja2
  mechanism, it doesn't re-run the whole extraction sweep.

## Execution slices (sequenced, each independently landable, one gate each)

0. **Coexistence scaffold.** `asgi_main.py`, legacy server rebinds to
   `127.0.0.1:7701`, uvicorn/FastAPI on `0.0.0.0:7700` with the catch-all
   reverse proxy, minimal `/health`. Zero routes migrated. Gate: full existing
   backend+frontend+e2e suite green unchanged; `scripts/smoke_demo.py` green
   against the new topology; a manual pass over cookies, the ICS feed, and a
   referral-image upload (the streaming-body cases most likely to break in a
   naive proxy) on the demo; soak period on demo before prod cutover (see
   "Decisions for the product owner").

   ### Slice 0 — implemented, LAND-DARK (deviation from this doc's own prose)

   Implemented 2026-08-05 with one constraint added on top of everything
   above: it ships **dark**. `TURNOS_ASGI` (env, default unset/off) selects
   the serving mode at process start — off: `asgi_main.py` calls
   `server.main()` with no arguments, which is byte-for-byte what
   `python3 server.py` always did (no uvicorn process, no internal proxy, no
   behavioral delta); on: the coexistence scaffold this doc describes runs
   for real. The Dockerfile `CMD` still moves to `asgi_main.py` in this
   slice (matching "Migration mechanics" above) — that's safe specifically
   *because* the default is dark, so the CMD change is itself a no-op until
   someone sets the flag. **Historical note (2026-08-05):** no Coolify
   environment set `TURNOS_ASGI` when this slice landed. That changed on
   2026-08-08 when the demo began its proxy-mode soak; production remains
   dark with the flag unset. Flipping production remains a separate, later
   action, not part of this land. Where this doc's prose assumed the proxy is
   always live, land-dark wins per this slice's explicit brief.

   **Four implementation findings not anticipated by the design prose
   above**, found by actually building and manually diffing proxied vs.
   direct responses (`app.py`'s module docstring carries the same detail
   inline) — **item 0 was a real security bug, caught by the e2e gate
   itself, not by inspection**:

   0. **The shared `httpx.AsyncClient` was leaking one session's cookie
      into every OTHER concurrent request through the proxy.** First
      surfaced as 3 flaky-looking `admin.e2e.mjs` failures (login-adjacent
      tests timing out on a login gate that never appeared) that only
      happened with the flag on — proven to be real, not flaky, by firing
      10 authenticated + 10 anonymous concurrent requests at
      `/api/admin/whoami` by hand: **all 20 came back authenticated as
      admin**, including the ones that sent no cookie at all. Root cause:
      `client.build_request()` unconditionally merges the client's own
      persistent cookie jar (`self.cookies`) into every outgoing request,
      and `client.send()` unconditionally re-populates that SAME jar from
      every response's `Set-Cookie` (`httpx/_client.py`,
      `self.cookies.extract_cookies(response)`) — normal, desirable
      behavior for an httpx client acting as ONE user's browser, actively
      wrong for a client proxying on behalf of many different real
      sessions at once. Fix: build the outgoing request as a bare
      `httpx.Request(...)` and send it directly, never through
      `client.build_request()` — the real `Cookie` header (or its absence)
      already travels correctly in the forwarded headers, which is the
      only source of truth the proxy should ever consult. Confirmed fixed
      with the same concurrent-request script (0/20 wrong afterward) and
      by the previously-flaky e2e cases going green. This is exactly the
      class of bug the design's own risk register worried about in the
      abstract ("mishandles a streaming body... a real request breaks")
      but didn't specifically name — recorded here so a later slice
      touching `app.py`'s proxy core doesn't reintroduce it by going back
      to `build_request()` for convenience.
   1. **The request body must be buffered, not streamed.** `server.py`
      reads the legacy request body via
      `int(self.headers.get("Content-Length") or 0)` then
      `self.rfile.read(length)` — it never parses
      `Transfer-Encoding: chunked`. Forwarding `request.stream()` as-is
      (an async generator of unknown length) forces httpx into chunked
      encoding for HTTP/1.1, which legacy can't read: the request hangs.
      Fix: `app.py`'s proxy route awaits `request.body()` and forwards
      `bytes`, so httpx computes a real `Content-Length` — the one legacy
      already expects. The RESPONSE direction is unaffected and streams
      unbuffered (`httpx.Response.aiter_raw()`) — legacy always answers
      with an explicit `Content-Length` itself (grep confirms every
      `send_header("Content-Length", …)` call site is fully-buffered
      already), so there was never a chunked-response case to worry about,
      and this direction gets a real streaming benefit for free.
   2. **uvicorn injects its own `Server`/`Date` response headers by
      default**, even when the proxied response already carries both from
      legacy — verified by hand (`curl -sD -` showed each header twice
      before the fix). `uvicorn.run(..., server_header=False,
      date_header=False)` in `asgi_main.py` turns this off; confirmed by
      diff that a proxied `GET /` response is now byte-identical to the
      direct-legacy response (`server: SimpleHTTP/0.6`, one `date`, gzip
      `content-encoding` intact, identical body).
   3. **The `Host` header must pass through unmodified** (not be
      overwritten with the internal `127.0.0.1:7701` httpx would default
      to) — verified httpx respects an explicitly-supplied `Host` header
      rather than deriving one from `base_url`. This matters because
      `turnstile.host_de_dev()` treats a loopback/private-IP `Host` as "dev
      host, skip Turnstile" — proxying with the wrong `Host` would silently
      disable Turnstile for every real visitor the moment the flag is on.

   **A NEW risk this slice's own construction surfaces, not previously in
   the risk register below — added as its own row there**: several legacy
   checks (`ip_exempt.exento()`, `accounts._stub_desplegado`-adjacent code,
   `turnstile.py`'s loopback branch) trust `handler.client_address` — the
   TCP peer — to prove a request is genuinely local (Turnstile bypass,
   RENAPER-stub bypass). Once the proxy is live, the TCP peer legacy sees
   for **every** request, including ones from the public internet, is the
   FastAPI proxy's own loopback connection — `127.0.0.1`. This is a correct
   consequence of "reverse proxy on the same box," not a proxy bug, but it
   silently defeats every peer-based local-only check the moment
   `TURNOS_ASGI=1` is set anywhere reachable off-box. **This must be
   resolved (or explicitly accepted with a mitigation) before the flag is
   ever turned on outside a developer's own machine** — flagged here so
   slice 1 (which already touches `ip_exempt`-adjacent auth code) inherits
   it rather than it being rediscovered at demo-soak time.

   **Test harness change needed to actually exercise the flag-on path**:
   this doc's "e2e needs ZERO changes" claim (Test harness extension,
   above) assumed the deployed process is unconditionally `asgi_main.py`.
   Under land-dark, `test/e2e/lib/harness.mjs` had to gain one small
   conditional (`TURNOS_ASGI=1` in the harness's own environment spawns
   `asgi_main.py` instead of `server.py`) so the SAME e2e suite can prove
   both modes — no test file changed, only the harness's process spawn.
   Safe under `npm run e2e`'s existing `--test-concurrency=1`, which
   guarantees only one spawned server (and therefore one internal
   `127.0.0.1:7701` bind) is ever alive at a time.
1. **Auth.** `deps.require_admin`, `pyotp` swap (parity-tested), `webauthn`
   swap with the adapter-or-grandfather migration story resolved (see
   "Decisions for the product owner"), `itsdangerous` cookie minting with the dual-verify
   window. `/api/admin/login`, `/api/admin/passkeys/*`, `whoami` mounted as
   FastAPI routers at their EXISTING URL paths. Gate: `route_parity_check.py`
   empty diff on every migrated auth endpoint; existing-shaped test sessions/
   passkeys (synthetic, never real PII) continue to verify; `admin.e2e.mjs`
   green unchanged (URL-transparent, per the harness section above).

   ### Slice 1 — implemented (2026-08-05/06), one implementation choice not
   ### anticipated by the prose above, one residual risk not yet closeable
   ### in this environment

   The three transport-adapter files (`legacy_shim.py`, `deps.py`,
   `admin_auth_router.py`/`passkeys_router.py`) are NOT a second
   implementation of login/passkeys/whoami — they are a thin
   `BaseHTTPRequestHandler`-shaped shim that lets the EXISTING
   `admin_auth.py`/`passkeys.py` functions run, unmodified, underneath a
   real FastAPI route. This is a deliberate departure from "structural
   port" read as "rewrite the logic FastAPI-natively": rewriting 600+
   lines of proven Turnstile/TOTP/lockout/session logic a second time
   would have doubled the surface that could silently drift from the
   legacy behavior it must match byte-for-byte. `route_parity_check.py`'s
   empty diff across all 6 migrated endpoints (config/login/logout/whoami/
   passkeys-list/passkeys-login-options), including a full login→whoami→
   passkeys-list→logout round trip driven through `TestClient`, is the
   proof this holds at the transport layer, not just "it's obviously the
   same code."

   **The passkey adapter is proven against synthetic fixtures
   (`test/test_passkeys_webauthn_adapter.py` — real ES256/EC-P256 and
   RS256/RSA-2048 keypairs generated with `cryptography`, stored in the
   exact `cose_a_json()` JSON shape, verified through both the adapter and
   the hand-rolled path with identical results on every case, including
   negative ones) — NOT against a real captured hospital passkey
   registration**, which the risk register names as the actual cutover
   bar ("Adapter approach validated against a REAL captured registration
   before cutover, not just synthetic data"). This environment has no
   access to Pinamar's production database or a physical authenticator to
   produce one. `passkeys.verificar_firma()` therefore calls the adapter
   FIRST and falls back to the untouched hand-rolled verifier on ANY
   exception (`passkeys.py`, `_verificar_firma_hand_rolled`) — so the
   worst case of an adapter bug the synthetic fixtures didn't anticipate
   is a spurious "firma_invalida" on one login attempt (recoverable via
   password+TOTP, the design's own stated acceptable-worst-case), never a
   credential silently accepted or invalidated. Before the demo soak
   named in "Decisions for the product owner," a real staff passkey should be
   registered and exercised once against this code path.

   **The loopback proxy-trust risk inherited from slice 0** (`ip_exempt.
   exento()`'s Turnstile bypass, `turnstile.client_ip()`'s last-resort
   fallback, `accounts._test_auth_enabled()`'s RENAPER-stub dev bypass —
   all three trusted the raw TCP peer) is closed by `proxy_trust.py`: the
   ASGI proxy signs the real peer it saw (`request.client.host`) with an
   HMAC only the two co-located processes can produce, legacy verifies the
   signature before trusting the header over the socket peer, and the
   header is unconditionally stripped from any inbound request before
   forwarding so a public client can never supply its own. Covered by
   `test/test_proxy_trust.py` (10 cases: bare socket peer unchanged with
   the flag off, a spoofed/forged header ignored, a correctly-signed
   header wins, both real call sites verified end to end). This fix is
   general — it protects every current and future `client_address` read,
   not only the auth endpoints this slice migrated.
2. **i18n/Jinja2 pilot.** Jinja2 env with `t`/`t_or` globals, the request-
   binding middleware, the four `site_identity.py` pages migrated. Gate:
   `test_golden_pages.py`-style byte-identity for canonical language, resolved-
   string identity for a non-canonical one.

   ### Slice 2 — implemented (2026-08-06), three implementation findings
   ### not anticipated by the prose above

   `jinja_env.py` (the `Environment` + three globals), `templates/*.html`
   (the four pages, mechanically converted from `index.html`/`about.html`/
   `privacy.html`/`terms.html`'s `{{t.*}}`/`{{sitio.*}}`/`{{legal.*}}`
   tokens), `i18n_middleware.py` (the bind/reset middleware this doc's prose
   already named), and `site_identity_router.py` (mounted in `app.py`
   before the catch-all, same pattern as slice 1's auth routers) — `site_identity.py`
   itself is untouched, so the legacy path (`TURNOS_ASGI` off) renders the
   exact same bytes it always did, unmodified.

   1. **A translated/legal string embedding its own fresh `{{sitio.*}}`
      token — the exact case this doc's "`{{sitio.*}}` ordering invariant
      survives" paragraph flagged as load-bearing "until this slice
      lands" — turned out to be real, not hypothetical.** `about.hero.lede`,
      `home.hero.lede`, `footer.mission`, every `*.meta.title`/
      `*.meta.description` key, and the legal document bodies
      (`legal.body_html()`, "the installation name is NEVER written
      literal") all carry a `{{sitio.brand}}`/`{{sitio.city}}` INSIDE the
      catalog/document value, not just in the raw template. Since Jinja2
      compiles the template source once and never re-scans a value a
      global function returns at runtime, a naive `t()`/`legal_doc()`
      global would have left that inner token unresolved, literal, in the
      served HTML. Fix: `jinja_env._t()`/`_legal_doc()` each call
      `site_identity.render()` on their OWN resolved fragment before
      returning it — a per-value "second pass" with the exact same
      function the legacy per-document second pass already used, proven
      byte-identical (not just equivalent) by `test_jinja_pilot.py` and
      `route_parity_check.py`'s `ESCENARIOS_PAGINAS` against real catalog
      content in both languages, not synthetic fixtures.
   2. **`autoescape=False`, deliberately, against Jinja2's own convention
      for HTML templates.** Confirmed by hand in this checkout:
      `markupsafe.escape()` (Jinja2's native auto-escaper) renders a quote
      as `&#34;`/`&#39;`; `html.escape(quote=True)` (what
      `i18n.render()`/`site_identity.render()` already use) renders
      `&quot;`/`&#x27;`. Leaving auto-escape on would have made every
      non-canonical page (and any canonical string with a quote in it)
      diverge from legacy by ENTITY STYLE, not content — a real but
      pointless difference the parity gate would have had to learn to
      tolerate. The three Jinja globals (`t`/`sitio`/`legal_doc`) do the
      exact same `html.escape(val, quote=True)` legacy already does, so
      Jinja's own auto-escape layer is switched off entirely rather than
      run a second, differently-behaved escape pass on top.
   3. **`theme.inject()` resolves THREE bare tokens, not one** —
      `{{tema}}`, `{{favicon}}`, AND `{{fuentes}}` (`theme.py:230`), not
      just the `{{tema}}` this doc's "`{{sitio.*}}` ordering invariant
      survives" paragraph implied by naming only `{{lang}}`/`{{tema}}`/
      `{{i18n_bootstrap}}`. All five bare tokens (`{{lang}}`, `{{tema}}`,
      `{{favicon}}`, `{{fuentes}}`, `{{i18n_bootstrap}}`) are protected in
      the new templates the same way: `{{ '{{token}}' }}` — a Jinja string
      literal that prints itself, so the token survives Jinja's render
      pass as literal text for `i18n.inject_html_lang()`/
      `i18n.inject_bootstrap()`/`theme.inject()` to resolve exactly as
      they already do for the legacy pipeline, unmodified, in the same
      order `site_identity.render_page()` already runs them in (see
      `site_identity_router.py`'s `_render()`).

   **A mechanism not named by this doc's prose at all**: `?lang=<code>`
   language switching (`i18n._maybe_switch_lang`, mounted ahead of
   `site_identity` in the legacy `GET_PLATFORM` list) would never fire for
   these four paths once FastAPI claims them ahead of the catch-all — the
   redirect-and-set-cookie mechanism is a DIFFERENT module (`i18n.py`) than
   the one this slice migrated (`site_identity.py`), and the design's own
   i18n section never mentions it. Fixed the same low-risk way slice 1
   handled Turnstile/TOTP: `site_identity_router.py` runs the literal
   `i18n.handle()` function through the SAME `legacy_shim.LegacyHandlerShim`
   `admin_auth_router.py` already uses, before rendering — zero
   reimplementation of the redirect/cookie logic, so a future change to
   switching behavior in `i18n.py` is inherited automatically.

   **Gate**: `scripts/route_parity_check.py`'s `ESCENARIOS_PAGINAS` (8
   scenarios — 4 pages × es/en, legacy direct call vs. FastAPI `TestClient`,
   full-byte comparison) and `test/test_jinja_pilot.py` (the same comparison
   without needing FastAPI/TestClient at all, so it stays in the plain
   backend suite) both clear byte-identity in BOTH languages, not just
   "resolved-string identity" for the non-canonical one — stronger than
   this doc's own stated bar, achievable because every Jinja global reads
   the exact same lookup functions (`i18n.t_or`, `site_identity.get`,
   `legal.body_html`) the legacy pipeline already calls, never a
   reimplementation of the lookup itself.
3. **Module contract adapter + `intake` pilot.** `router_attr` manifest field,
   `require_module_enabled()` dependency, `intake`'s routes migrated
   end-to-end. Gate: disable/enable still drives 404s and the sidebar with
   zero central edits outside `modules/intake/` (mirrors Tier 2's own pilot
   acceptance, proven against the NEW mechanism).

   ### Slice 3 — implemented (2026-08-06), one deliberate scope narrowing
   ### against this doc's own prose, two implementation findings

   `app_modules.app_router_entries()`/`merge_app_routers()`/
   `mount_module_routers()` (mirrors `route_modules()`/`merge_routes()`
   exactly, one level up — routers instead of route modules),
   `deps.require_module_enabled(id)` (a `Depends`-factory, same shape as
   slice 1's `deps.require_admin(...)`), `module_router.route_via_handle
   (module, attr_name="handle")` (the one adapter every intake endpoint is
   built from — see its own docstring for why it re-resolves the attribute
   per-call instead of closing over a bound function), and `intake`'s four
   NEW sibling `*_router.py` files (`interview_patient_router.py`,
   `consult_admin_router.py`, `intake_admin_router.py`,
   `printable_router.py`) — the original four handler files
   (`interview_patient.py`/`consult_admin.py`/`intake_admin.py`/
   `printable.py`) are UNTOUCHED beyond gaining a sibling, so the legacy
   path (`TURNOS_ASGI` off) renders exactly the bytes it always did.

   **A deliberate narrowing of this doc's own "same after-anchor position
   `merge_routes()` already computes" phrase (module contract adapter
   section, above).** Once a route is mounted as a real FastAPI router it
   claims its path directly, ahead of the reverse-proxy catch-all — so its
   position relative to `routing.py`'s own PLATFORM_LIST (what a legacy
   `after` anchor is a position IN) is moot, that list is never consulted
   for a path FastAPI already owns. The only ordering question that
   survives the migration for real is app-module router vs. app-module
   router (two different modules' routers both wanting an overlapping
   path) — `merge_app_routers()` resolves `after` against ANOTHER APP
   MODULE's id, using the identical splice algorithm `merge_routes()`
   already uses, not a second ordering scheme, but a narrower anchor
   namespace than "any platform-core route module name." `intake`'s own
   four router entries all use `after: null` (tail) — none of its twelve
   paths overlap anything else FastAPI has mounted, so this pilot doesn't
   exercise a real anchor collision; the splice algorithm itself is proven
   directly, independent of intake, by
   `test/test_module_contract_asgi.py`'s `TestMergeAppRoutersSpliceContract`
   (synthetic modules, an anchored splice, and an unresolved-anchor
   degrade-to-tail case — same "never crash, never silently drop" contract
   `merge_routes()` already has).

   **Finding 1 — a `router_attr` module must be a SEPARATE file from the
   route module the legacy registry imports, never the same one.**
   `routing.py` (and therefore dark mode, `TURNOS_ASGI` unset — the default
   everywhere today) imports `app_modules.merge_routes()`'s registry
   UNCONDITIONALLY at process start, which imports every manifest's plain
   route modules via `_validate_routes()`. Had `router = APIRouter()` lived
   directly inside `interview_patient.py` (this doc's own module-contract
   example names one dotted module for both roles), `import fastapi` would
   have become an unconditional cost of dark mode the instant `intake` is
   enabled — exactly the "zero behavioral/cost delta in dark mode" property
   slice 0 promised, silently broken. Fix: each `router_attr` entry names a
   NEW sibling module (e.g. `modules.intake.interview_patient_router`,
   importing the ORIGINAL `interview_patient` module and forwarding to its
   unmodified `handle()`); `app_modules._validate_routes()`/
   `_base_app_routes()` (the legacy loader) explicitly SKIP any route entry
   declaring `router_attr`, so they never import the sibling file at all.
   Verified by hand: `import routing` alone, then inspecting
   `sys.modules`, shows zero `fastapi`/`starlette` modules loaded — dark
   mode's import graph is unchanged by this slice, `intake` enabled or not.
   `test_module_manifest.py`'s new `TestRouterAttrEntriesResolve` also
   asserts the two dotted names (plain route vs. `router_attr` route) never
   coincide within one manifest, so this can't regress silently.

   **Finding 2 — `legacy_shim.py` needed two additions intake exercises that slice
   1's auth endpoints never did — one of them a genuine, if latent, parity
   bug in the shared shim, not new to this slice.** `admin_auth.py`/
   `passkeys.py` have their own low-level `_json()` built directly on
   `send_response`/`send_header`/`wfile.write`; `intake`'s four route
   modules instead call `handler._send_json(obj, status=...)` — the
   convenience method `server.Handler` itself defines (server.py:3018,
   delegating to `gzip_bridge.send()`) that the shim never had to
   implement before. Added `LegacyHandlerShim._send_json()` as a thin call
   into the SAME `gzip_bridge.send()` server.py's real one uses (that
   function only touches `.headers.get()`/`.send_response()`/
   `.send_header()`/`.end_headers()`/`.wfile.write()`, all of which the
   shim already provided) — zero reimplementation. Separately,
   `_read_json()` raised `ValueError` for ANY empty body; the real
   `server.Handler._read_json()` (server.py:3025) returns `{}` for an empty
   body and only raises on genuinely malformed JSON. Slice 1's own
   `route_parity_check.py` scenarios never exercised this (their login/
   passkeys calls always send a real body), so the divergence shipped
   unnoticed; `interview_patient._message`'s own `except Exception as e:
   ...f"JSON inválido: {e}"` would have surfaced it as a wrong ERROR BODY
   (both sides still 400) the moment a genuinely empty POST reached it.
   Fixed to match `server.Handler`'s real semantics — a strict correctness
   fix, not a behavior change for either slice's already-proven scenarios
   (neither exercises a truly empty body).

   **Gate**: `scripts/route_parity_check.py`'s new `ESCENARIOS_INTAKE` (the
   twelve migrated paths, legacy `handle()` call vs. the real FastAPI
   router via `TestClient`) — unlike the slice 1/2 blocks, none of these
   assert a PARTICULAR status; the property proven is that legacy and
   FastAPI reach the IDENTICAL response (status + body + header keys)
   because both call the literal same `handle()`. Run twice, once against
   a reachable Postgres and once with `DATABASE_URL=""` (no DB at all) —
   33/33 scenarios empty diff both times (the 14 slice-1/2 scenarios plus
   these 12, all unaffected). `test/test_module_contract_asgi.py` is the
   ASGI-path sibling of `test_module_runtime.py`'s `TestIntakeModuleGating`
   (disabled → 404 + handler never called, enabled → the real `handle()`
   runs, live `enable()`/`disable()` flips it with no remount) plus the two
   router-adapter-specific classes named above (splice-order contract,
   broken-router-module boot survival — an unimportable `router_attr`
   module or one missing the named attribute marks that module `broken`
   and mounts every OTHER module's routers regardless, mirroring
   `test_module_runtime.py`'s own `TestBrokenModuleBootSurvival` for the
   plain-route case).

4. **Alembic cutover.** Baseline migration, `alembic stamp head` on
   already-migrated environments, advisory-lock-wrapped `command.upgrade()` at
   boot replacing `migrations.apply_pending()`. Gate: a fresh install applies
   the baseline for real and reaches the identical schema `_SCHEMA_SQL`
   produces today (schema-diff test); a stamped "already migrated" environment
   runs zero DDL on boot.

   ### Slice 4 — implemented (2026-08-06), one narrowing against this
   ### doc's own wording, one non-obvious detection-timing bug found

   `alembic/` (`env.py`, `script.py.mako`, `versions/0001_baseline.py`),
   `alembic.ini`, and four new functions in `db.py`
   (`_sqlalchemy_url`/`_alembic_config`/`_pre_alembic_schema_present`/
   `_alembic_version_present`/`_run_alembic_upgrade`) — `migrations/__init__.py`
   and every numbered `migrations/00N_*.py` file are byte-for-byte untouched,
   `_ensure_schema` still calls `migrations.apply_pending(conn)` exactly as
   before, on the same connection, under the same
   `pg_advisory_lock(hashtext('turnos:migrations'))`. `0001_baseline.upgrade()`
   replays `db._SCHEMA_SQL` (imported, never re-typed) via
   `op.get_bind().exec_driver_sql(...)` — SQLAlchemy Core's raw-driver-SQL
   primitive, load-bearing because `_SCHEMA_SQL` contains a literal `%s`
   inside a SQL comment that a parameterized execute path would choke on.

   **A narrowing of "advisory-lock-wrapped `command.upgrade()`" from this
   doc's own prose.** Alembic runs on its OWN SQLAlchemy engine/connection,
   never the `conn` that holds the outer `pg_advisory_lock` — `migrations
   .apply_pending()` already takes that SAME lock key reentrantly on `conn`'s
   own session (safe, proven since the Tier 1.4 fix); having
   `0001_baseline.upgrade()` call `apply_pending()` AGAIN from a DIFFERENT
   session (Alembic's own connection) while `conn`'s session still holds the
   lock would self-deadlock — a second session requesting the same key blocks
   until the first releases, which here can't happen until the (blocked) call
   returns. Fix: the baseline revision never touches `migrations.apply_pending`
   at all, and the outer advisory lock still fully protects Alembic's separate
   connection anyway — every OTHER process trying to reach this point blocks
   on `pg_advisory_lock` itself, before Alembic ever opens its own connection,
   so cross-process serialization holds without the two connections needing
   to be the same one.

   **Detection-timing bug found while writing `test_alembic_cutover.py`'s
   `TestLegacyDuplicateBackfillMigration`-adjacent case**: `_pre_alembic_schema_present(conn)`
   (schema_migrations already has every discovered legacy migration id +
   `booking_requests` exists → "this installation predates Alembic, stamp
   don't upgrade") MUST be called BEFORE `migrations.apply_pending(conn)`
   runs for this boot, not after — `apply_pending()` itself fully populates
   `schema_migrations` on every boot, including a genuinely fresh database's
   very first one, so checking afterward can no longer tell a brand-new
   install apart from a real pre-Alembic production database (both show
   "all 5 ids present"). `test/test_integrity_races.py::TestLegacyDuplicateBackfillMigration`
   (a dirty legacy `booking_requests` table pre-created by hand, no
   `schema_migrations` yet) caught this the moment it was ported to the new
   boot path — it got mis-stamped instead of upgraded until the check moved
   earlier, one line, in `_ensure_schema`.

   **Gate**: `test/test_alembic_cutover.py` — `TestFreshInstallSchemaDiff`
   (a database built via `migrations.apply_pending()` + `_SCHEMA_SQL` +
   `_apply_trigrams()` directly, vs. one built via `db._connect()`'s new
   Alembic path, `pg_dump --schema-only` normalized-equal) and
   `TestCutoverStampRunsZeroDDL` (same fixture, stamped instead — proven
   with a real Postgres `ddl_command_end` event trigger logging every DDL
   object actually created during the call, not inferred from a before/after
   diff; the only objects allowed are `alembic_version` and its implicit PK,
   both tagged `CREATE TABLE` by the ONE statement `command.stamp()` issues).
   `test/test_integrity_races.py::TestConcurrentBootstrap` (the Tier 1.4
   two-processes-boot-a-truly-empty-database race) passes unmodified against
   the new path — proof the advisory-lock property survived the narrowing
   above.
5. **Background queue pilot.** `procrastinate` scaffold, `waitlist.py`
   migrated. Gate: parity test over a fixed time window (same promotions,
   same cadence) as the thread-based sweep; `cancel-waitlist.e2e.mjs` green
   unmodified.

   ### Slice 5 — implemented (2026-08-06), one topology decision this doc's
   ### own prose left open, one connector-class finding

   `waitlist_queue.py` (new sibling module — `procrastinate` App +
   `SyncPsycopgConnector`-backed schema apply + `PsycopgConnector`-backed
   worker + the periodic task wrapping `waitlist.tick()`, unmodified) and a
   small refactor of `waitlist.start()` into `_start_thread()`/
   `_start_queue()`, branching on ONE new flag, `TURNOS_WAITLIST_QUEUE`
   (default unset — every deployment at the time of this slice,
   byte-for-byte unchanged; the demo has since moved to
   TURNOS_QUEUE_WORKER=1, which supersedes this pilot flag there).
   `waitlist.py`'s own `tick()`/`release_slot()`/`_create_offer()` are
   completely untouched.

   **Topology decision this doc's own prose never made explicit** (the
   task brief that produced this slice named it as open): **in-process
   worker**, running in its own daemon thread from `waitlist.start()` —
   the exact same shape every one of the nine existing daemons already
   uses, not a second deployable/Coolify app. Reasoning: nothing about
   this pilot's own acceptance bar ("same cadence, same observable side
   effect as the thread it replaces") asks for out-of-process isolation,
   and a second process would need its own command/health/redeploy story
   that slice 0 deliberately avoided introducing for the exact same
   "no infra change on any slice" reason. Recorded here so a later slice
   migrating one of the other eight daemons doesn't have to re-derive this.

   **Connector-class finding, found by actually running the worker, not by
   reading procrastinate's docs**: `procrastinate.SyncPsycopgConnector`
   cannot run a worker at all — `App.run_worker()`'s "synchronous"
   convenience wrapper still calls `open_async()` internally (it's
   `asyncio.run()` around an async worker loop underneath), and a
   sync-only connector raises `SyncConnectorConfigurationError` the moment
   that happens (confirmed by hand: a `SyncPsycopgConnector`-backed App's
   `run_worker()` fails immediately). The WORKER app therefore uses
   `procrastinate.PsycopgConnector` (async, but ALSO transparently
   supports every sync call shape — `.defer()`, `get_sync_connector()` —
   via a lazily-created internal sync sub-connector), while the one-shot
   schema-apply path (`waitlist_queue._apply_schema_standalone()`, never
   touches the worker) keeps the pure `SyncPsycopgConnector` since it never
   needs an event loop. This does NOT reintroduce an async rewrite of
   `waitlist.py`'s business logic: the task body stays a plain sync
   function, and procrastinate itself bridges it onto the worker's event
   loop through a thread executor (`utils.sync_to_async`, confirmed by
   reading `worker.py`'s job-execution path) — exactly the "no async
   rewrite" property this doc's own "Recommendation" paragraph promised,
   just located one level lower (the connector, not the task) than that
   paragraph implied.

   **Schema handling — the "STOP and report" condition did NOT trigger.**
   procrastinate's bundled `schema.sql` is genuinely non-idempotent DDL
   (bare `CREATE TABLE`/`CREATE TYPE`, no `IF NOT EXISTS` guard — confirmed
   by reading it), so it cannot run on every boot regardless of the flag —
   nor does it: `db._connect()`'s boot path (Alembic, slice 4) is entirely
   unmodified by this slice and never references `waitlist_queue.py` at
   all (verified directly: `test/test_waitlist_queue.py::
   TestSchemaLifecycle::test_schema_absent_by_default_normal_boot_never_touches_it`
   boots a fresh database through the normal path and asserts
   `procrastinate_jobs` was never created). The schema is applied lazily,
   exactly once, ONLY from `_start_queue()` — the same call site the
   mutual-exclusion property already gates — guarded against a
   concurrent-boot race with the SAME keyed
   `pg_advisory_xact_lock(hashtext(...))` idiom `db.create_booking_request`
   already uses (db.py:3749), reusing the "two connections, one advisory
   lock" reasoning slice 4's own Alembic cutover already established
   (`_ensure_schema`'s docstring) rather than inventing a second one.
   Existing Alembic zero-DDL proof (`test_alembic_cutover.py::
   TestCutoverStampRunsZeroDDL`) needed NO changes — it only ever exercised
   `db._connect()`, which this slice never touches.

   **Mutual exclusion, proven by construction, not just by convention.**
   `waitlist.start()` is the ONE function deciding the mechanism: an
   `if`/`else` reaching `_start_thread()` XOR `_start_queue()`, never both,
   never neither (net of the pre-existing `TURNOS_WAITLIST_OFF` kill
   switch, checked first, unconditionally). `test/test_waitlist_queue.py::
   TestMutualExclusion` asserts exactly this for all three flag
   combinations that matter — proof, not inference from reading the code.

   **Gate**: `test/test_waitlist_queue.py` (10 cases: cron-cadence parity
   against `croniter` for every divisor of 60 this repo has ever configured,
   mutual exclusion, schema-absent-by-default, schema-applies-once, and
   sweep parity proven twice — a manually-deferred job drained
   deterministically, and the REAL periodic deferrer firing on its own
   cron within a bounded async window — both reaching the identical
   `expirada`/`ofrecida`/one-notification state `waitlist.tick()` already
   produces under the thread), run 3x green with no flakiness;
   `test/test_integrity_races.py::TestWaitlistOfferRace` (Tier 1.4's own
   race proof, calls `waitlist.release_slot()` directly) and
   `test/test_waitlist.py` (the pure-engine suite) both green unmodified,
   run 3x; `test_alembic_cutover.py` green unmodified; full
   `scripts/ci_local.sh` green with `TURNOS_ASGI` unset AND `=1`.

   ### Worker topology (2026-08-08) — the slice 5 in-process decision
   ### REVERSED, deliberately

   Slice 5's recorded topology choice ("in-process worker, daemon thread,
   not a second deployable") was correct for that pilot's acceptance bar
   and is now deliberately superseded — not silently contradicted. The
   trigger is the maintainer's infrastructure review of the open-source plan: the
   plan promised a modern Postgres-backed **queue boundary**, and a queue
   library embedded inside the web monolith is not a boundary. What
   changed, all feature-gated behind ONE new flag, `TURNOS_QUEUE_WORKER`
   (default unset — every deployment at the time of this slice,
   byte-for-byte unchanged; the demo has since adopted `=1` as its soak,
   production remains unset):

   - **`worker.py` (repo root) is the worker's own entrypoint** — a real
     OS process, never a thread in the web process. One procrastinate App;
     each migrated job module contributes its task via `register(app, …)`
     (`waitlist_queue.register`, `reminders_queue.register` — the generic
     schema/cron/connector machinery moved verbatim to
     `core/queue_infra.py`, `waitlist_queue` keeps thin aliases so the
     slice 5 pilot and its tests are untouched). Lifecycle: SIGTERM/SIGINT
     drain in-flight jobs up to `TURNOS_WORKER_GRACEFUL_TIMEOUT` (default
     20 s) then exit 0; a worker that cannot work (no `DATABASE_URL`,
     unreachable Postgres) exits 1 LOUDLY — the opposite of the web
     process's degradation contract, on purpose: the supervisor owns
     restart policy.
   - **Health has two independent signals**: HTTP `/health` + `/ready` on
     `TURNOS_WORKER_HEALTH_PORT` (default 7710; `/ready` = worker loop up
     AND Postgres reachable right now), and procrastinate 3.x's own
     `procrastinate_workers` heartbeat row (named worker, ~10 s beat) —
     the census an operator reads with plain SQL, no network path to the
     worker. The web app's `/health` consults NEITHER: a down worker
     never makes the web app unhealthy (sweeps degrade to "delayed",
     the same failure shape a dead daemon thread always had).
   - **`TURNOS_QUEUE_WORKER=1`** — asgi_main.py (the process entrypoint)
     spawns `worker.py` as a child process and supervises it: restart
     with exponential backoff (1 s → 60 s cap), SIGTERM forwarded at
     shutdown via atexit (both serving modes already own the process
     signals — dark mode's server.main() and proxy mode's uvicorn — so
     the supervisor deliberately hooks exit, not signals).
     Single-container topology, no new Coolify app.
     **`=external`** — nothing is spawned; worker.py runs as its own
     deployable (second Coolify app on the same image, CMD
     `python3 worker.py`, shared `DATABASE_URL`). Same code either way;
     only who starts the process changes.
   - **Delegation, mutually exclusive by construction**: with the flag
     set, `waitlist.start()` and `reminders.start()` start NOTHING (no
     thread, no in-process pilot worker) — one if/elif chain per module,
     delegation checked first, so old thread / slice-5 in-process / new
     external paths can never coexist
     (`test_queue_worker.py::TestDelegationMatrix`, including
     flag+`TURNOS_WAITLIST_QUEUE` both set).
   - **Second job migrated**: reminders (`notifications/reminders_queue.py`
     — ONE periodic task running `tick()` then `tick_interview()`, the
     thread loop's exact body, claim-CAS duplicate-send guards unchanged).

   **Proven** (`test/test_queue_worker.py`, 20 cases, real subprocesses +
   real Postgres): entrypoint lifecycle incl. graceful SIGTERM exit 0 and
   fail-loud startup; `/health` `/ready`; heartbeat row; a job deferred
   with NO worker running waits in `procrastinate_jobs` and executes
   exactly ONCE when a worker appears; five jobs under TWO concurrent
   worker processes each execute exactly once (`procrastinate_events`
   started-count = 1); the same periodic tick deferred twice yields one
   job (`procrastinate_periodic_defers` uniqueness); supervisor
   spawn/no-spawn matrix with shutdown; the suite fails itself if any
   worker.py process survives it.

   **Independent Fable review round (2026-08-08)** — no blockers; both
   MAJORs fixed the same day: (1) `TURNOS_QUEUE_WORKER` was parsed
   differently by asgi_main (stripped) and the web modules (exact match),
   so a value with whitespace ran threads AND worker concurrently — now
   ONE parser, `queue_infra.worker_mode()`, used by all three call sites,
   with non-canonical values proven inert; (2) `queues=colas or None` made
   a jobs-disabled worker consume EVERY queue and poison unknown jobs
   (TaskNotFound → failed, no retry) — the consumed-queue list is now
   always explicit and structurally non-empty. Also from that round,
   tightened per the product owner's own re-verification the same day (he reproduced
   the first janitor draft calling the ASYNC `delete_old_jobs` without
   await — an unawaited coroutine that deleted nothing and reported
   success): a daily ledger janitor that is an async task, genuinely
   awaited, scoped to ONLY the queues this worker consumes
   (succeeded/cancelled rows older than 72 h deleted daily at 04:30
   container clock; failed rows and foreign queues kept — proven row by
   row in `TestJanitorRetention`, suite runs with
   `-W error::RuntimeWarning`); exact hour-crons for intervals that ARE
   representable (60→hourly, 120→`*/2` hours, 1440→daily) and a loud
   `ValueError` — never a faster-than-configured approximation — for any
   that aren't (90, 2880); the supervisor's spawn-vs-atexit orphan race
   closed with a real lock (either the stop flag wins before spawn, or
   the just-born child is seen and terminated — no window); its child
   wait derived from `TURNOS_WORKER_GRACEFUL_TIMEOUT` + margin; true 1 s
   first backoff.

   **Codex round (2026-08-08, pre-deploy)** — two more correctness gaps
   closed before any flag was set anywhere: (1) cron derivation is now
   exact-or-reject across the WHOLE range — sub-60 non-divisors of 60
   (7, 45, 59) raise too (`*/45` restarts at the hour boundary: a 15-min
   gap, the same faster-than-configured class), accepted values proven
   with croniter; (2) worker exit codes are a contract the supervisor
   honors — 1 = transient (unreachable Postgres, loop error) →
   backoff-restart, 2 = permanent invalid config (no DATABASE_URL,
   non-expressible interval) → logged loudly and NOT retried, because a
   restart can't fix configuration. Also made explicit: the janitor keeps
   aborted rows forever, same diagnosis-trail choice as failed
   (`include_aborted` deliberately not passed).

   **Demo evidence caveat (2026-08-08) — what the soak does and does NOT
   prove.** The demo deployment (TURNOS_QUEUE_WORKER=1) proves the PROCESS
   topology: web + worker as separate PIDs, /health-/ready 200, fresh
   `procrastinate_workers` heartbeat, kill-the-worker → supervisor
   relaunch in 1 s, exactly one worker process, and live durable execution
   of ONE maintenance-queue marker job (todo → succeeded, started-events
   = 1). It does NOT prove the BUSINESS queues live: the demo has always
   run `TURNOS_WAITLIST_OFF=1` + `TURNOS_REMINDERS_OFF=1` (no emails from
   the fictional site), so its worker consumes only `maintenance`; a
   synthetic waitlist job deferred there correctly stayed `todo` (the OFF
   matrix working as designed) and was removed. Waitlist/reminders
   queue-path behavior is proven ONLY by the local disposable-Postgres
   suite (`test_queue_worker.py` durability/contention/parity).
   **Activating the business queues in ANY deployment remains gated** on
   a separate safe activation proof (its own card) — do not read the demo
   soak as "waitlist/reminders run through the queue in production-like
   conditions"; nothing does yet.

   **Safe business-queue EXECUTION proof (2026-08-08 evening, the
   follow-up card) — CLOSED; activation itself still OFF everywhere.**
   The missing half above was proven without turning anything on:
   (a) `core/outbound_guard.py` — a fail-closed outbound kill switch
   (`TURNOS_OUTBOUND_KILL=1`, env-only): socket-level default-deny
   (connect/connect_ex/sendto/sendmsg) allowing only AF_UNIX, the
   `DATABASE_URL` endpoint and loopback strictly on the DB's port(s)
   (never loopback-wide — WAHA lives on loopback ports in real
   deployments); a worker that requests the switch but cannot arm it
   exits 2 and never runs. Honest boundary: the guard neither observes
   nor controls native libpq or libc DNS (`getaddrinfo`) — allowed DB
   traffic flows through libpq as it must, and DNS resolution can still
   occur. The no-outbound claim is scoped to the REVIEWED Python
   transport paths (urllib/http.client and every socket-level sender:
   connect/connect_ex/sendto/sendmsg); it holds for the business
   handlers because they have no subprocess and no native external-send
   path other than libpq-to-Postgres.
   (b) `test/test_business_queue_proof.py` (12 tests, disposable
   Postgres, the REAL `python3 worker.py` subprocess): both business
   sweeps consumed exactly once (`started`=1), durable transitions
   (`reminder_at` claim-CAS; expired offer → `expirada` + next
   candidate's `ofrecida`), SIGKILL+replacement without duplicates,
   two workers one job one execution, zero synthetic rows left; both
   transports carry DUMMY credentials so the sends are genuinely
   ATTEMPTED and cut by the guard (`send FAILED` + `Meta WhatsApp
   FAILED` + ≥2 `BLOQUEADO` required in the worker's own stderr).
   Gates on final SHA `d5d086ce`: independent adversarial review — its
   three MAJOR findings (unpatched `sendmsg`, WhatsApp never attempted,
   loopback-wide allowance) each reproduced, fixed, and re-verified by
   the reviewer's own probes, final verdict "no blocker/major findings
   remain"; full ladder green (backend 3399, frontend 935, routes,
   cassettes) and BOTH e2e modes (legacy 14/17+3 ASGI-only skips,
   ASGI 17/17). Live evidence on the demo HOST, not the demo app: the
   exact deployed image run as a one-shot worker against a disposable
   Postgres with a hermetic explicit env (`TURNOS_ENV_FILE=/dev/null`,
   `TURNOS_CONFIG_LOCK=1`, dummy Mailgun/Meta creds, no
   `TURNOS_DEMO_MODE`) — all 8 evidence items PASS (guard armed, 4×
   BLOQUEADO across both transports incl. IPv6, exactly-once, durable
   transitions, standing demo still maintenance-only, prod running no
   worker at all, full teardown verified). What this does NOT close:
   no deployment runs waitlist/reminders through the queue — demo keeps
   `TURNOS_WAITLIST_OFF=1`/`TURNOS_REMINDERS_OFF=1`, prod keeps the
   flag unset entirely. Turning a business queue ON anywhere is now an
   operational decision with its proof in hand, not a proof gap — but
   it is a decision that has NOT been taken.

   **Periodic-daemon batch 2 (2026-08-08): housekeeping + referral-file
   retention migrated.** The full remaining inventory was audited from
   `server.py:main()` (which by now starts MORE than the nine daemons the
   original design counted — the referral-retention sweep was added by
   the 2026-08-06 audit after that inventory was written). Selected: the
   table-hygiene sweep (`ops/housekeeping_queue.py`, task
   `ops.housekeeping.sweep`, queue `housekeeping`) and the referral-file
   retention sweep (`modules/turnos/referrals_queue.py`, task
   `modules.turnos.referrals.retention_sweep`, queue `referrals`) — the
   two remaining sweeps whose bodies are bounded (LIMIT-capped),
   idempotent per item (a re-run finds nothing left to do), DB-DRIVEN
   and free of AI/HSI/backup coupling. The queued housekeeping job is
   PURGES-ONLY and genuinely DB-only (round-8 narrowing, see below);
   referrals is the batch's one mixed-I/O contract — DB-driven deletes
   of real files on the data volume — proven on its own terms. Why each of
   the deferred daemons was NOT grouped in: `cache_warmup` and the
   avatar warm-up refill IN-PROCESS memory caches — they cannot leave
   the web process by definition; `demo_refresh` rewrites the demo's
   whole dataset (files + DB) and exists only on demo installations;
   `chart_summary` + `translate_warmup` + `ai_health` call external AI
   providers; `hsi_sync`/`hsi_resync` are the HSI outbound journal with
   their own conflict/retry semantics; `data_backups` ships the data
   volume off-box to B2; `watcher` drove the legacy Webturno upstream
   (retired 2026-08-16 with server.py's WebTurno client).
   `data_backups` now has a Postgres producer journal: its path row records
   the SHA-256/size identity of bytes accepted by B2, and a failed journal
   commit retries rather than reporting success. The backup sweep now also
   has a queue bridge (`ops/data_backups_queue.py`): unlike cron jobs, it
   self-reschedules from completion, preserving arbitrary legacy intervals
   and the 60-second initial grace as durable job-row scheduling. It is a
   `filesystem=True`, `outbound=True`, `exclusive=True` worker job, so an
   external worker fails before any DB/filesystem work without the existing
   shared-data attestation. Its separate session advisory lock spans the
   complete snapshot/upload/journal outcome and uses the same connection for
   journal I/O, preventing a pool-size-one self-deadlock. Batch 5c adds the
   independently-gated recovery side: every *new* accepted upload has an
   immutable B2 version receipt (`fileId`, exact name, size, SHA-1 and local
   SHA-256) in `backup_versions`, while pre-ledger rows stay legacy and are
   permanently non-prunable. `ops/data_backups_restore.py` is intentionally
   **operator-only** — no worker registration, HTTP route, startup hook, or
   automatic retention job. It paginates B2 versions with both cursors,
   reconciles current/legacy/missing/orphan/hide/unknown evidence, restores
   one exact id into a disjoint scratch directory only after byte and metadata
   verification, and produces a dry-run retention plan. That plan keeps the
   current receipt and newest prior verified receipt and refuses all deletion
   whenever any ambiguous evidence exists. An exact `(fileName, fileId)` B2
   delete primitive plus durable tombstone is available only for a separately
   reviewed manual apply; there is no production auto-delete flag. The worker
   outbound kill switch also refuses direct operator B2 actions before any
   transport or local mutation.
   Mechanics, identical to the first two jobs: sibling `*_queue.py`
   modules registered by `worker.py._register_jobs()` honoring the SAME
   OFF flags (`TURNOS_HOUSEKEEPING_OFF`, `TURNOS_REFERRAL_SWEEP_OFF`,
   plus retention<=0 as the same explicit opt-out the thread applies)
   and the SAME cadence resolution via the modules' own helpers
   (`housekeeping._interval_h()`,
   `booking_requests._referral_sweep_interval_h()`, hours × 60 through
   the exact-or-reject cron derivation — default 24 h → `0 0 * * *`);
   delegation in `housekeeping.start()` /
   `booking_requests.start_referral_sweep()` via the one
   `queue_infra.worker_mode()` parser, mutually exclusive by
   construction. Topology caveat, ENFORCED in code (Codex gate — a
   docs-only MUST is fail-open): filesystem jobs (referral retention
   and configured data-volume backups) share the data volume by
   construction under `=1` (same container); an `=external` worker
   that may consume either EXITS 2 at startup unless
   the shared mount is positively attested with
   TURNOS_WORKER_SHARED_DATA=1 (an explicit OPERATOR attestation — the
   process cannot verify mount identity, only refuse to run without
   the statement) — there is no per-job thread fallback for the queued
   work, delegation is deliberately all-or-nothing
   (TestExternalTopologyGate, branch by branch). The disk alert's
   once-a-day dedup is a durable AT-MOST-ONCE claim in Postgres
   (`db.claim_daily_alert`, table `alert_claims`, claimed BEFORE the
   send — the earlier JSON check-send-save raced under two processes or
   a SIGKILL after provider acceptance; a failed send now consumes the
   day's claim on purpose: zero-or-one email, never two; proven at the
   SQL level, under an 8-thread race, and at the post-acceptance kill
   boundary), and since round 8 the alert itself runs ONLY web-side —
   see the round-8 narrowing below.

   **Stalled-job recovery (same gate round) — a pre-existing
   worker-topology hole batch 2 exposed, now closed for all queues.**
   procrastinate 3.9 fetches only status='todo'; a worker SIGKILLed
   mid-task left its job 'doing' FOREVER (stalled-WORKER pruning only
   nulls worker_id — nothing requeues the JOB), so "queued jobs survive
   the worker dying" was false mid-task. worker.py now runs
   `_recover_stalled_jobs`, scoped strictly to ITS OWN queues: once at
   startup (a replacement requeues its predecessor's in-flight jobs as
   part of booting) and every 5 minutes as a maintenance-queue periodic
   task. Concurrency-safe via procrastinate_retry_job_v2's own row lock
   (two recoverers serialize; the loser's raise is caught per job).
   What the recovery guarantees is precise (round-7 correction — the
   first phrasing over-claimed a blanket "same business effect"): a
   stuck job re-runs, and the re-run never DUPLICATES a business
   mutation; delivery of outbound NOTIFICATIONS stays per-queue and is
   audited honestly per job (since the registry-hardening round, in each
   `_JOBS` entry's `rerun` field — it lived in the recovery function's
   docstring when this round shipped) — idempotent
   purges/deletes (housekeeping, referrals, maintenance; since round 8
   the batch-2 queue jobs carry no outbound at all — the disk alert is
   web-side work outside this recovery's scope entirely), and for the
   not-activated business queues the pre-existing
   claim-before-send exposure is now RECORDED rather than papered
   over: a kill between the waitlist offer insert and its notify, or
   between a reminder's claim-CAS and its send, loses that one
   notification (the offer's own expiry advances the queue; the thread
   path always had the same window) — an input to the still-untaken
   activation decision, not a regression of this batch. A new queue
   must state its re-run safety before joining (since the
   registry-hardening round: the `rerun` field of its `_JOBS` entry,
   enforced by TestJobRegistry). The
   staleness threshold TURNOS_WORKER_STALLED_S has an enforced 30 s
   floor (≈3 heartbeats — below it a live-but-slow worker's jobs would
   run twice); the floor lifts only under the test-only gates. Proven
   by genuine MID-task SIGKILL for each batch-2 task
   (TestBatch2CrashRecovery: job observably stuck 'doing', replacement
   requeues and completes it, started-events = 2, and the batch-2
   business effect converges to one).

   **Round-8 narrowing — the disk alert leaves the queue batch, the
   card stays literal.** The disk check's advisory email is
   claim-before-send at-most-once, which can legitimately deliver ZERO
   after a crash — and a missed daily alert IS skipped work under the
   card's literal "no skipped or duplicate work" bar (tomorrow's
   re-alert is a different day's event, not a recovery of today's).
   Rather than relax the acceptance gate or build a durable-delivery
   outbox for one advisory email, the queued housekeeping job was
   narrowed to `tick_purges()` — the five LIMIT-capped table DELETEs,
   no filesystem, no outbound — whose no-skip/no-dup behavior under
   kill/restart is fully provable and proven. The disk check
   (measurement + email, still hardened by the durable `alert_claims`
   at-most-once claim, the rollout-day JSON bridge and the self-purge)
   runs on the WEB process's legacy schedule in EVERY topology: the
   full legacy `tick()` when no worker is configured, and a reduced
   disk-only thread (`_disk_loop`, same cadence) when the purges are
   delegated — one mechanism per concern, delegation-matrix-proven.
   Consequence: the worker's shared-data attestation gate covers every
   registry-declared filesystem job, and the queued housekeeping job is proven to
   perform ZERO outbound and touch zero alert claims even under
   alert-triggering conditions with the kill switch armed as sentinel
   (TestQueueJobExcludesDiskAlert). The alert's own crash-safety proofs
   (claim atomicity under an 8-thread race, the post-acceptance
   SIGKILL kill-boundary with no second acceptance, the master-gated
   test seams) moved with it to the web-side path unchanged in
   substance.

   Proven (`test_queue_worker.py`, now 63 cases): delegation
   matrix for both new jobs (worker modes, OFF-wins, retention-0-wins,
   unset-keeps-thread byte-identical); composition (each OFF flag
   excludes exactly its queue, all-off still yields the explicit
   non-empty maintenance list, unrepresentable hour intervals — 5 h,
   7 h — reject at registration into the permanent-config exit);
   cadence derived from the modules' own defaults; and real-subprocess
   exactly-once EFFECT proofs against disposable Postgres — a
   housekeeping job queued while the worker is down purges the
   400-day-old `searches` row and keeps the recent one, a referrals job
   deletes the 200-day terminal file AND nulls its pointer while the
   recent terminal keeps both, each with `started`-events = 1.
   Activation state after this batch: production keeps
   `TURNOS_QUEUE_WORKER` unset (threads, byte-identical); the demo —
   which already runs `=1` with the business queues OFF — now runs
   these two hygiene sweeps through its worker instead of web threads.
   That is the same soak-on-demo pattern every Tier 4 slice used, and
   deliberately does NOT touch the separate, still-untaken decision of
   activating the waitlist/reminders BUSINESS queues anywhere.

   **What this closes / what remains — do not over-claim**: this closes
   "the queue lives behind a process boundary with a production-legible
   worker topology" for the four migrated jobs (waitlist, reminders,
   housekeeping, referral retention). It does NOT close Tier 4's
   "background daemon threads → queue" item: the remaining daemons
   still run as threads (demo_refresh, cache_warmup + the
   avatar warm-up — the two in-process cache refills that can never
   leave the web process — data_backups, hsi_resync, hsi_sync,
   ai_health; each needs its own persistence/safety/rollback proof
   before migrating through the same `register(app, …)` shape;
   chart_summary + translate_warmup moved in batch 3, below),
   production stays on the thread path
   (`TURNOS_QUEUE_WORKER` unset) until the demo soak and an explicit
   flag/topology decision for prod, and the `=external` two-container
   shape is available but not yet exercised by any deployment.

   ### Worker-registry hardening (2026-08-08) — the runtime-contract
   ### gate BEFORE daemon batch 3

   Batch 2 left four contract gaps that would compound with every
   further migrated daemon; all four closed in one round, with NO
   activation or topology change (prod stays flag-unset threads, the
   demo stays `=1` — a pure code/proof slice, rollback = revert the
   commit, no schema, no flag, no env change):

   1. **Jobs and capabilities are declared, not narrated.** `worker.py`
      now holds a declarative `_JOBS` registry — one `_JobSpec` per
      queue-migrated job carrying its queue/task identity, its ON/OFF
      truth (the same helpers the thread twins use), its cadence
      source, and its DECLARED capabilities: `filesystem` (drives the
      `=external` attestation gate), `outbound`, and an audited `rerun`
      contract (the per-queue re-run-safety audit that previously
      lived as prose in `_recover_stalled_jobs`' docstring). Everything
      that used to be kept aligned by discipline now DERIVES from the
      registry: registration order and startup logs
      (`_register_jobs`), the external-topology gate's filesystem list
      (`_fs_jobs_enabled` — previously a hand-maintained tuple in
      `main()`), and a machine-readable audit: `python3 worker.py
      --jobs` prints the registry as JSON, English in keys AND values
      (labels, off reasons, cadence, rerun contracts — pinned ASCII by
      a language-contract test; legacy Spanish stderr log prose stays).
      The audit is READ-ONLY and honest about its resolution: flags go
      through `core.config.get` exactly like the running worker (DB
      override > env > .env), it works with NO database configured,
      and with one it closes the psycopg pool explicitly before exit —
      the Codex round reproduced a PythonFinalizationError on stderr
      under `-W error::ResourceWarning` when the pool was left to the
      GC, now pinned by a real-env clean-stderr regression test.
      `/health` now also reports the consumed queues (`queues`), so
      registration is observable on a LIVE worker. TestJobRegistry
      pins the capability sets exactly (including filesystem = {referrals,
      data_backups}), refuses a spec with an empty
      re-run contract, pins task/queue identity UNIQUENESS across the
      registry + maintenance, and proves — by injecting a fake spec —
      that a batch-3 job declaring `filesystem=True` hits the
      attestation gate with zero changes to `main()`.
   2. **Test-only registration duplication eliminated.** The four
      `*_queue.py` modules each carried the same decorator stack, the
      same `cron` test-seam plumbing, and (two of them) an identical
      private `_test_hold` copy. All of it now lives once in
      `queue_infra.register_periodic_sweep` (+ `test_hold` /
      `test_seams_enabled`, moved from worker.py so the master gate has
      one implementation); a queue module owns exactly its sweep body.
      Side effect that enabled gap 3: EVERY sweep body now opens with
      the double-gated hold, so any queue can be killed genuinely
      mid-task by the crash suites.
   3. **Re-run proofs now cover every registered queue.** Batch 2's
      mid-task-SIGKILL-and-replace proof was extracted into
      `_CrashRecoveryMixin` and applied to the business queues:
      waitlist (a stale offer seeded 'ofrecida' past its window
      converges to 'expirada' exactly once, no duplicate offer row,
      started-events = 2 — TestWaitlistCrashRecovery) and reminders
      (queue-level convergence to 'succeeded' with exactly one
      re-execution — TestRemindersCrashRecovery; the mutation guard
      behind its `rerun` contract, claim-CAS before send, is
      race-proven in test_integrity_races.py item 5 and deliberately
      not re-proven there because the sweep body is wall-clock gated
      09:00–21:00 ART). The Codex round added the missing half — the
      crash seam holds BEFORE the body, so those suites prove recovery
      but not post-effect idempotency: TestRerunContractsRunTwice now
      runs each sweep body TWICE against seeded state (waitlist: one
      'expirada', second tick all-zeros, no duplicate row; reminders:
      second claim loses; housekeeping: second purge pass deletes 0;
      referrals: second sweep 0, pointer stays NULL) — deterministic
      run-twice evidence for every declared rerun contract.
      `test_queue_worker.py` is 81 cases after this round.
   4. **Bootstrap double-open removed.** Startup recovery used to run
      under its own `asyncio.run` + `open_async` (pool built and torn
      down) before `run_worker()` built a SECOND pool on a second
      event loop. `main()` now runs ONE async body: open the connector
      once, run the startup recovery (still best-effort), then
      `run_worker_async` — same signal handling, same exit contract,
      one connector lifecycle.
   5. **Recovery scoped by declared TASK, not only by queue** (Codex
      round). Queue scoping alone would let a foreign or
      not-yet-declared task sitting in an OWNED queue be requeued and
      re-executed by a worker that never declared its rerun contract.
      `_recover_stalled_jobs` now derives an allowed task-name set from
      the ENABLED registry plus the two maintenance tasks
      (`_allowed_task_names`) and skips — loudly — any stalled job
      whose task is not in it; the job stays 'doing' for the worker
      that knows it. Pinned by TestRecoveryTaskScoping: a seeded
      foreign task in the waitlist queue survives a recovery pass
      untouched while the declared task alongside it is requeued, and
      a DISABLED job's task leaves the allowed set.
   ### Periodic-daemon batch 3 (2026-08-08) — the AI-cache warmups

   The first two OUTBOUND-capable jobs join the registry: chart-summary
   pre-generation (`ai/chart_summary_queue.py`, queue `chart_summary`,
   task `ai.chart_summary.pregen_sweep`) and the translation-cache
   warmup (`ops/translate_warmup_queue.py`, queue `translate_warmup`,
   task `ops.translate_warmup.sweep`). Both were deferred from batch 2
   for exactly one reason — their sweeps call the external AI provider —
   so both registry entries declare `outbound=True` (filesystem=False:
   they write only Postgres cache tables) and the batch ships with its
   own egress proof. Mechanics identical to batch 2 (sibling `*_queue.py`
   modules, `queue_infra.register_periodic_sweep`, delegation in the
   twins' `start()` via the one `worker_mode()` parser, cadence from the
   twins' own helpers: `chart_summary._tick_min()` default 60 →
   `0 * * * *`, `translate_warmup._interval_min()` fixed 360 →
   `0 */6 * * *`, exact-or-reject). No activation change: prod stays
   flag-unset threads, the demo's `=1` soak simply picks the two jobs
   up. Three contracts specific to this batch:

   1. **First-run parity via durable startup sweeps, serialized by a
      per-task execution lock.** The thread twins tick at every process
      boot (chart_summary immediately, translate_warmup after a 30 s
      boot grace); a cron-only queue would leave a cold install waiting
      up to 60 min / 6 h for the first boundary — a user-visible
      regression. The registry gained declared `startup` +
      `startup_delay_s` capabilities: `worker._defer_startup_jobs`
      defers ONE startup sweep per enabled `startup=True` job at worker
      boot, preserving each twin's grace verbatim (chart_summary
      `startup_delay_s=0`; translate_warmup `startup_delay_s=30` =
      FIRST_RUN_DELAY_S, carried durably in the job row's scheduled_at
      via `schedule_in`, never a sleep). Durable: a Postgres job row —
      a worker dying after the defer still gets it run, and a mid-run
      SIGKILL is recovered like any declared task. Two locks with two
      distinct jobs: the QUEUEING lock dedups queued rows only
      (procrastinate's unique index covers status='todo' — the
      supervisor-restart-loop case defers one row, not many), and
      EXECUTION overlap is PREVENTED outright by procrastinate's
      NATIVE per-job `lock`, registered as each exclusive task's
      default (`queue_infra.task_lock_name`, `exclusive=True` at
      registration) and therefore inherited by every invocation path —
      cron tick, startup defer (Task.configure falls back to the
      task-level lock when given only queueing_lock/schedule_in), and
      crash-recovery re-runs (retry_job requeues the original row,
      lock column included). The library's contract is literal: "no
      two jobs with the same lock string can run simultaneously" —
      enforced at fetch, FIFO among same-lock rows — so a concurrent
      second job WAITS ('todo') and runs after, never alongside — no
      lost tick, and the overlap half of the double-spend path is gone;
      the serialized follow-up then no-ops on unchanged sources via the
      run-twice-proven fingerprint/hash guards, leaving as the ONLY
      residual provider cost the audited crash window in each rerun
      contract (a death between the provider call and the upsert
      re-buys that one call). Known bound, documented:
      a SIGKILLed 'doing' row holds the lock until stalled recovery
      requeues it; same-lock jobs wait it out. All of it exposed by
      `--jobs` (`startup`, `startup_delay_s`, `exclusive`). The
      batch-2 jobs stay `startup=False`: their boundary-first cadence
      was accepted at their own gate. Proven in TestStartupSweeps
      (boot runs the immediate sweep; the 30 s grace lands in
      scheduled_at; queued-row dedup) and TestAiCacheExecutionLock
      (real two-worker races: startup-vs-startup with live processes,
      and two independent jobs of one task — the cron-vs-startup
      shape — never two 'doing' at once, single business effect).
   2. **Per-tick language eligibility, never boot-frozen.** The
      translate_warmup registry `enabled()` is the OFF flag alone; the
      sweep body re-resolves `pending_languages()` every tick, so
      enabling a second language from the panel reaches the next sweep
      without a worker restart, and with no second language the tick is
      a true no-op (the thread's exact behavior, which re-reads it each
      pass). The web-side `start()` keeps its historical boot-time
      no-op when no second language is enabled.
   3. **Zero-egress / zero-spend proof with the provider CONFIGURED.**
      test_ai_cache_queue_proof.py boots the real worker with the
      outbound kill switch armed AND a dummy OpenRouter key present, so
      the provider call is genuinely attempted: the guard's socket-level
      deny is observed (BLOQUEADO line), chart_summary's row lands with
      `ai_model='fallback'` (the attempted-and-failed marker — 'fake' /
      'sin_clave_api' / 'ai_no_disponible' would each be a vacuous
      pass), translate_warmup writes zero rows (TranslationUnavailable
      swallowed per batch, no fake markers), and both jobs still
      SUCCEED — degradation, never a failed job. Every other suite runs
      under TURNOS_FAKE_AI=1, so no test anywhere pays a provider.

   Re-run contracts, audited in the registry like every job:
   chart_summary is a fingerprint-guarded upsert per (dni, doctor) —
   run-twice second tick generates 0, byte-stable row
   (TestRerunContractsRunTwice); a death between the provider call and
   the upsert re-buys that one call, never a duplicate row or message.
   translate_warmup filters by source hash — run-twice second sweep
   translates 0, row count stable; a death mid-batch leaves a partial
   cache the re-run completes. Mid-task SIGKILL-and-replace proven for
   both in TestAiCacheCrashRecovery (one summary row / no duplicate
   (key, lang) rows, started-events = 2). Rollback = revert the commit:
   no schema change, no flag change, no env change; with the flag unset
   the thread path is unchanged by construction (TestDelegationMatrix
   pins the flag-unset branches) with ONE reviewed exception:
   chart_summary._tick_min() now falls back to 60 on a malformed
   TURNOS_SUMMARY_TICK_MIN instead of crashing start() — the sibling
   parsers' pre-existing convention, adopted in the batch-3 review
   round.

   ### Periodic-daemon batch 4 (2026-08-09) — the durable HSI journal,
   ### narrowed and redesigned across the same review round

   The last two thread-based HSI daemons: `hsi_sync` (the durable
   bidirectional journal — backfill, outbox replay, inbound reconcile,
   resolver auto-close) and `hsi_resync` (its legacy one-way
   predecessor, dormant unless an operator sets `TURNOS_HSI_SYNC_OFF=1`).
   The batch shipped in two passes the same day: an initial migration
   that put BOTH on the queue registry with a cron-based schedule, then
   a same-day adversarial review that narrowed the scope and replaced
   the scheduling mechanism entirely. Both passes are recorded here
   because the reasoning that killed the first design is the actual
   argument for the second, not incidental history.

   **Scope remains narrow: only `hsi_sync` is queue-migrated.**
   `hsi_resync` stays purely on-thread in every topology —
   `TURNOS_QUEUE_WORKER` never affects it, only its own
   `TURNOS_HSI_RESYNC_OFF` does. This preserves a single queue owner for
   inbound reconciliation while retaining the fallback's operator-controlled
   cadence and stop-first policy. It is intentionally not a second queue
   migration.

   **Follow-up (2026-08-10): the fallback remains outside the registry, but
   its exact outbound row path is now hardened.** It still owns only its
   thread/manual cadence and stop-first policy; it does not call a full
   `hsi_sync.tick()` or join queue scheduling. Instead, each eligible row uses
   the durable journal's shared row-to-intent mapping and exact-item replay,
   retaining the original operation for linked cancel/reassign rows. The
   remote-accepted/local-uncommitted exposure described above is therefore
   superseded for that fallback route. See `docs/architecture/hsi.md` and
   `docs/solutions/hsi/legacy-fallback-reuses-durable-journal.md`.

   **Cadence redesigned: self-rescheduling, not periodic cron.** The
   thread loop is `while True: tick(); wait(interval_s)` — interval_s
   AFTER each completion, a rolling cadence anchored to completion time,
   not a wall-clock grid. Every OTHER queue-migrated job here uses
   `queue_infra.register_periodic_sweep`'s fixed `@app.periodic(cron=...)`
   grid safely, because their sweeps are cheap DB-only no-ops when
   nothing changed — a batch-3 AI-cache job's occasional early or late
   tick costs nothing. `hsi_sync.reconcile()` makes an UNCONDITIONAL real
   HSI network read every tick regardless of local state, so a cadence
   deviation here has an actual external cost, which is why the card
   asked for cadence PRESERVATION, not approximation. A first cut kept
   the cron grid and bolted on a "skip if a prior run succeeded within
   ~90% of the interval" guard; adversarial review rejected it as
   structurally incapable of being exact — a tick's completion time and
   the cron grid's boundaries are two independent clocks, so a completion
   landing even slightly behind a grid boundary causes the NEXT boundary
   to be skipped by the guard (too soon) and the tick doesn't fire until
   the boundary after that, silently stretching the gap toward ~2x the
   configured interval; no threshold choice fixes both the early-fire and
   the stretch failure at once. The fix: `hsi_sync` is registered as a
   plain `@app.task` (no `@app.periodic` at all) — each tick, AT
   COMPLETION, durably defers its own next invocation `schedule_in`
   seconds out, reproducing the thread's exact wait-then-act spacing
   because it uses the SAME mechanism (a real elapsed wait from actual
   completion), not a fixed grid.

   **Cold-start vs. warm-restart, one reused lock.** The existing
   `startup=True` boot-time kick (`worker._defer_startup_jobs`, unchanged
   mechanism) still fires the FIRST tick immediately, matching the
   thread's own immediate first tick. It and the chain's own
   self-reschedule share the identical queueing-lock string
   (`startup:<task_name>`) — procrastinate's own unique-index-based
   dedup on 'todo' rows then means a worker booting while a chain is
   already pending or executing sees `AlreadyEnqueued`/its own
   `chain_alive()` check and does nothing; only a genuine cold start (or
   a chain broken by a swallowed reschedule failure) actually kicks a
   fresh one.

   **Three review gates closed the same day, each a genuine gap in the
   first redesign, not a hypothetical:**
   1. *Rolling-restart race*: worker B booting while worker A's row is
      genuinely 'doing' (A alive, not crashed) isn't blocked by the
      queueing lock alone — that only covers 'todo' rows. B would defer a
      job that waits on the execution lock and fires the INSTANT A's tick
      finishes: a back-to-back HSI read via a different door than the
      one just closed. Fixed by `hsi_sync_queue.chain_alive()` — checked
      at boot AND folded directly into `rekick()` (not a separate step a
      caller could get right once and wrong elsewhere), so it's current
      at the exact instant the schedule time is computed.
   2. *Swallowed failure could stall the chain indefinitely*: the
      self-reschedule's own defer is wrapped in try/except so a
      scheduling hiccup never loses the completing tick's real result —
      but the first cut's ONLY rescue was the next worker boot, which
      never happens on a long-lived process. Closed with an always-on
      steady-state watchdog (`worker._register_hsi_sync_watchdog`, every
      5 min — the same cadence the stalled-job recovery pass already
      uses) that calls the SAME `rekick()`.
   3. *A kick "now" is the SAME bug one row later*: an early cut of the
      fix reasoned that `chain_alive()` alone made scheduling "now" safe
      for any external kicker. It doesn't — `chain_alive()` only rules
      out a currently live chain; it says nothing about how RECENTLY it
      last ran, so a kick landing right after a chain that ran moments
      ago is exactly the back-to-back HSI read this design exists to
      prevent, just a same-row version. `rekick()` now schedules no
      earlier than the last successful completion (procrastinate's own
      durable event history) plus the full configured interval — never
      "now" except on a genuine, never-succeeded cold start.

   **Proof, all on real disposable Postgres with real subprocess
   workers** (`test/test_queue_worker.py`'s `TestHsiSyncCrashRecoveryAndContention`):
   two workers racing one task never both 'doing'; a genuinely-SIGKILLed
   worker's job recovers to exactly one execution; a REAL SIGKILL landing
   strictly inside the remote-accepted/local-uncommitted window (a
   from-scratch local fake HSI HTTP server implementing just enough of
   the real REST surface for `HsiClient.create_appointment`'s actual,
   unmodified dedup branch to run over a real HTTP round trip — not a
   scripted mock return value — so the post-crash retry is PROVEN to land
   on the same booking, never a duplicate); a rolling restart (worker B
   boots while A is genuinely still 'doing') never produces a second
   tick, checked against the EXACT expected `scheduled_at` (the anchor
   job's own durable 'succeeded' event timestamp plus the configured
   interval, compared in one SQL snapshot — not a loose "> N seconds
   remaining" bound, which would still let a real ~50s cadence error
   pass); and a forced, GENUINE self-reschedule failure (a one-shot test
   seam firing from inside the real `defer_async()` call the chain
   itself makes, not a simulated end-state like deleting the
   continuation row after the fact) heals via the watchdog while the
   SAME worker process stays up — the whole point being no restart —
   landing on that same exact schedule. Zero-egress proven under the
   armed kill switch with a configured dummy `HSI_URL`, for BOTH
   `hsi_sync.tick()` and `hsi_resync.tick()` — the latter's outbound path
   stays real, reachable code (the thread can run it any time) even
   though it isn't queue-scheduled, so its behavior under the kill switch
   stays worth proving (`test/test_hsi_queue_proof.py`).

   **What this does NOT claim**: the fallback is now a queue task or performs
   inbound reconciliation; it is neither. Its outbound crash window is covered
   by exact durable replay, but queue ownership remains exclusive to
   `hsi_sync`. `core.queue_infra.cron_for_interval` was separately hardened to reject non-integral and
   sub-1-minute intervals outright (it used to silently truncate/clamp
   them — the exact faster-than-configured defect class its own docstring
   already banned for non-divisor integers) rather than approximate them,
   though `hsi_sync` itself no longer routes through it at all.

6. **Static fingerprinting.** Fingerprint map + `static()` template helper,
   migrate one asset family (JS, then CSS). Gate: every migrated asset's
   fingerprinted URL round-trips through `smoke_prod.py`'s cache-busting
   checks; `?v=` mechanism stays live and correct for whatever hasn't migrated
   yet.

   ### Slice 6 — implemented (2026-08-06), one scope narrowing, no
   ### ASGI-specific code needed

   `static_fingerprint.py` (new, <180 lines): SHA-256-of-bytes, first 8 hex
   chars, mtime-cached the same way `i18n.py`'s `_load_catalog` caches its
   own catalog signature. Three callers: `url(name)` for Python f-string
   page builders (a direct expression, no new syntax); `render(text)` — the
   `{{static.<name>}}` sibling of `i18n.render()`'s `{{t.*}}` and
   `site_identity.render()`'s `{{sitio.*}}` — wired into every
   `i18n.render()` call site this repo has (`accounts.py`'s account/reset
   pages, `booking_requests.py`'s admin.html shell, `site_identity.py`'s
   `render_page()` for the four static pages); and `jinja_env.ENV.globals
   ["static"]`, bound straight to `url()`, giving the Jinja pilot pages the
   design doc's own literal `{{ static('nav.js') }}` syntax. Serving is
   `static_fingerprint.handle(handler, method)`, registered as a NEW
   platform-core entry in `routing.GET_PLATFORM` (right after
   `legacy_urls` — `test/test_route_order.py`'s golden list and
   `test/test_module_manifest.py`'s `PLATFORM_ROUTES` allowlist both updated
   to name it, same as the `avatar_sprite` precedent this slice reused).

   **No ASGI-specific serving code, by construction, not by extra work.**
   The design's own prose left open "whether fingerprinting applies in
   legacy mode too or only ASGI" — it applies to BOTH, and needed zero
   `app.py` changes to do it: `static_fingerprint.handle()` is reached
   through `routing.dispatch()`, which `server.py`'s legacy `do_GET` already
   calls regardless of `TURNOS_ASGI`. In proxy mode, a fingerprinted request
   isn't claimed by any FastAPI router, so it falls through `app.py`'s
   catch-all to the legacy process, which resolves it exactly as dark mode
   does — the same "one implementation, both modes" property
   `avatar_sprite.py`'s pre-existing `/avatar-sprite/<hash>.webp` route
   already had, unmodified by this slice. Verified live, not just by
   reading the proxy code: booted `asgi_main.py` with `TURNOS_ASGI=1` and
   curled `/static/nav.<hash8>.js` through the public port — `200`,
   `cache-control: public, max-age=31536000, immutable`, byte-identical to
   the source file — and the home page's own `<script>`/`<link>` tags
   already point at that exact URL.

   **Scope narrowing against "migrate one asset family (JS, then CSS)":**
   `MIGRATED = {"nav.js", "human-rounds.css"}` — one JS file and one CSS
   file, not literally every `.js`/`.css` this repo serves (61 JS + 1 more
   CSS file, `entrevista.css`, stay on `?v=`). Reasoning: `nav.js` and
   `human-rounds.css` are the two assets referenced from EVERY page-building
   call site in the repo (patient pages via `patient_site._shell()`,
   account/reset pages, admin.html, the setup wizard, the password-reset
   page, the pre-consult embed, the four `site_identity.py` static pages and
   their Jinja twins, the SEO-content pages built in `server.py`) — proving
   the mechanism against these two exercises every reference SHAPE this repo
   has (plain f-string interpolation, the `{{t.*}}`-sibling regex pipeline,
   and the real Jinja `{{ static(...) }}` global) at once, which a
   single-shape asset (say, one admin-only `.js`) would not have. Migrating
   the other 61 files is mechanical repetition of the same pattern per file
   — deliberately left to the "no fixed target count" follow-up slice 8
   already establishes as this tier's own precedent, rather than turning one
   slice into a 60-file diff with no new design decision in it.

   **Three genuinely-dead files were found and deliberately NOT touched**:
   `app.html`, `panel.html` still carry the literal `human-rounds.css?v=42`
   — both routes are unconditionally 302-redirected to `/` by
   `patient_site.handle()` (Pinamar's de-linking from the WebTurno fork,
   pre-dating this slice), so neither file is ever actually served; touching
   them would have meant inventing a render pipeline for pages nothing
   reaches. `directorio.html` is the same story (redirected; the live
   `/medicos` directory is server-rendered by `server.py:_render_directorio()`
   from a different code path entirely, unaffected).

   **Gate**: the new `TestCacheControlFingerprinted` class in
   `test/test_cache_headers.py` (current hash serves `immutable`; a
   stale/forged hash serves nothing, never `immutable`, falls through to the
   existing `code >= 400` → `no-store` branch already proven above; a
   non-migrated name is never accepted even with a real hash; non-GET is
   never handled) plus a standalone `static_fingerprint.py` round-trip
   (`url()` → `_resolve()` → same name, `render()` substitutes only
   migrated keys and leaves an unknown `{{static.*}}` token visibly intact).
   `scripts/smoke_prod.py` extended with a fingerprinted-asset loop
   (discovers `/static/<name>.<hash8>.<ext>` refs in prod's own home page,
   confirms the hash still matches the local checkout, confirms
   `Cache-Control` is exactly the immutable string, confirms the bytes match)
   — this is the deploy-time proof the numbered list's own gate names;
   nothing to run against a live URL from inside this repo, but exercised
   locally against `asgi_main.py` end to end. Full `scripts/ci_local.sh`
   green with `TURNOS_ASGI` unset AND `=1` (3101 backend + 878 frontend + 12
   e2e, all green both runs).

   ### Slice 6, batch 2 — the mechanical repetition, 20 assets (2026-08-08)

   The "follow-up slice" the scope-narrowing above deliberately left open.
   `MIGRATED` grew by every root-level patient-site/shared asset whose
   references are ALL server-rendered: `dates.js`, `chat-ui.js`,
   `chat-widget.js`, `i18n.js`, `hours.js`, `prep.js`, `service.js`,
   `photo-zoom.js`, `age.js`, `week.js`, `tiles.js`, `search.js`,
   `confirm.js`, `wz-upload.js`, `upload-order.js`, `turnstile.mjs`,
   `my-requests.js`, `lab-results.js`, `dependents.js`, `auth-test.js` —
   105 `?v=` references swapped across the three established shapes (root
   `{{t.*}}`-pipeline pages → `{{static.<name>}}`; `templates/` Jinja twins
   → `{{ static('<name>') }}`; Python f-string builders →
   `static_fingerprint.url()`, with `patient_site._shell()` binding
   `i18n_js`/`dates_js`/`chat_js` locals once per request, same as the
   existing `nav_js`). Asset-reference census by the literal
   `[name].js?v=N` grep: **189 → 84**.

   **What deliberately did NOT migrate, and why** (the registry comment in
   `static_fingerprint.MIGRATED` carries the same list): `listing.js`,
   `directory.js`, `account-form.js`, `dni-barcode.js` — each is loaded at
   runtime BY A STATIC JS FILE (`lab-results.js:129`, `my-requests.js`,
   `chat-widget.js:47`, `account-form.js:1371`), and a served file can't
   resolve a fingerprint; migrating them needs a client-side manifest (e.g.
   a `window.SANA_STATIC` map injected by the shell) — a design extension,
   not a per-asset swap. Also out: module-owned assets (`entrevista.js`/
   `entrevista.css`, the `admin-*.js` family — a later batch through the
   same recipe), `format.js`/`calendar-grid.js` (already `_PASSTHROUGH`-
   paired with their `.mjs` facades, adding them to `MIGRATED` would give
   one file three serving shapes), and `vendor/lottie/` (lives outside
   `static/vendor/`). Tests that asserted literal `?v=` URLs now assert
   `static_fingerprint.url(...)` (test_mobile_public_ui, test_preparation —
   including the prep.js-pairing guard's consumer detection —
   test_servicio_info, test_medico_page, admin-reports-summary.test.mjs,
   chat-widget-ui.test.mjs's load-order guard), so they never need a bump
   again either.

   ### Slice 6, batch 3 — the client manifest, last first-party holdouts (2026-08-20)

   **Reconciling the stale claims above first** (batches that landed between
   batch 2 and this one, recorded here so the section reads true end to
   end): `MIGRATED` is no longer `{nav.js, human-rounds.css}` nor "20 more";
   the 29-file `admin-*.js` family migrated as `ADMIN_ROOT_JAVASCRIPT`
   (2026-08-10) and `format.js`/`calendar-grid.js`/`islands.js`/`passkeys.js`
   as the historical `ADMIN_SHELL_UTILITIES` cohort (2026-08-19). Those four
   have since become canonical `.mjs` modules in `CLIENT_LOADED`; `format.js` and
   `calendar-grid.js` carry BOTH the fingerprinted URL and their `_PASSTHROUGH`
   twin for the `.mjs` facades' relative imports, the "three shapes" worry
   above turned out to be two URLs for one file, tested as such in
   `test_cache_headers.py`. The "189 → 84" census is historic: before this
   batch the live first-party JS/CSS `?v=` references were 16, in 8 files.

   **What this batch built** — the design extension batch 2 named: a
   client-side manifest. `static_fingerprint.CLIENT_LOADED` =
   `{listing.js, directory.js, account-form.js, dni-barcode.js, dni-scan.js}`
   (a subset of `MIGRATED`, which also gains `offer.js` — the waitlist offer
   page's own script, a plain `_shell()` server-rendered swap).
   `manifest()` is `{name: url(name)}`; `manifest_script()` the inline
   `<script>window.SANA_STATIC={...};</script>` a page emits ONCE in
   `<head>`, before any deferred loader can run — `patient_site._shell()`
   (every patient page: wizard, /my-requests, offer, interview),
   `templates/index|about|privacy|terms|account|account-reset.html` and
   `templates/admin/base.html` through the new Jinja global
   `{{ static_manifest() }}`, and `admin.html` through the reserved
   `{{static.manifest}}` token `render()` resolves before its URL pass (the
   placeholder regex demands an extension, so the two can never collide).
   Each of the six loaders (`lab-results.js`, `my-requests.js`,
   `chat-widget.js`, `account-form.js`, `admin-create-appointment.js`,
   `dni-barcode.js`) got a four-line `staticUrl()` resolver — the
   `islands.js` `resolveUrl()` shape: `window.SANA_STATIC[name]` or `null`
   + `console.error`, never a hardcoded fallback URL. On `null` the
   `<script>` loaders call their callback immediately (the path `onerror`
   already took) and the `import()` sites reject (the `.catch` paths that
   already existed: OCR tier 2 in `account-form.js`, "No se pudo leer el
   documento" at the desk). `dni-barcode.js`'s static
   `import { parsePayload } from '/dni-scan.js?v=1'` became a top-level
   dynamic import through the manifest, awaited inside `readDni()`. The
   rule: **a page that emits any loader emits the manifest; a page that
   emits no loader may skip it** (the `embed=1` interview document does).
   Every loader is itself `MIGRATED`, so editing one changes its own hash —
   nothing to bump anywhere, recursively closed.

   **No transport, route or census change**: `routers/native_terminal.py`,
   `services/legacy_static.py`, `services/legacy_http.py`, `core/routing.py`,
   `core/legacy_urls.py`, `scripts/legacy_fallback_census.py` and the
   composition inventory are untouched — the manifest is inline HTML, not an
   endpoint, so the query-string-blind, route-keyed census can't see it, by
   design. `TestDarkNativeTerminalParity` gained a client-loaded fingerprint
   and its stale twin; `smoke_prod.py`'s fingerprint loop verifies the
   manifest URLs on the live home page automatically (its regex matches them
   inside the JSON too).

   **The `?v=` doctrine becomes an exact, shrinking allowlist with written
   exit criteria**: `test/test_listado_consistencia.py::
   TestVersionesConsistentes` now scans production source RECURSIVELY
   (root + every package + `templates/`; docs/tests/stories/scripts/vendor
   and the three dormant redirected HTML files excluded) and asserts the
   set of `?v=` JS/CSS assets EQUALS `MANUAL_VERSIONED_ALLOWLIST =
   {entrevista.js, entrevista.css}` — a new `?v=` reference fails, a
   migrated holdout left in the list fails. `.claude/hooks/vbump-drift-guard.py`
   scans the same tree (it used to be root-only and blind to
   `modules/intake/interview_patient.py`, the only renderer still emitting
   `?v=`). When the allowlist is empty, that test class, the hook and the
   `?v=` paragraphs in `deploy.md`/`CLAUDE.md`/`CONTRIBUTING.md` retire
   together.

   **Deliberately deferred, with owners**: `entrevista.js`/`entrevista.css`
   (module-owned; the right shape is a per-module asset registry feeding
   fingerprints + `/api/admin/modules` emitting fingerprinted `js` URLs —
   the same item that would give `modules/intake/admin-entrevistas.js` a
   hash, a module-contract design item, not a platform-core `MIGRATED`
   entry); `vendor/lottie/` and the unversioned `vendor/zxing-wasm/` imports
   (outside `static/vendor/`, vendor hygiene; relocation touches the
   `/vendor/` census rows); the dormant `app.html`/`panel.html`/
   `directorio.html` (deletion is a cleanup decision, not fingerprinting);
   non-JS/CSS `?v=` (fonts, favicons, the photo sprite, icons — a different
   cache policy, `url()` would have to grow past `js|css|mjs`). Known and
   accepted: there is no old-hash retention window for fingerprinted assets
   (contrast `avatar_sprite.py`'s 6 h grace) — pre-existing for every
   migrated asset, and Coolify rebuilds the container per push so the
   overlap is seconds.

   **Gate**: `test_cache_headers.py` (`TestCacheControlFingerprintedClientLoaded`:
   the six batch-3 assets byte-identical + immutable, stale hash → no-store
   404, `manifest()` == CLIENT_LOADED through `url()`, `manifest_script()`
   exact shape, the render token), `test_static_manifest.py` (loader ⇒
   manifest, source and rendered levels), `test_admin_static_fingerprint_rendering.py`
   (the CLIENT_LOADED cohort, the legacy admin shell publishes the manifest
   once, the wizard too), `test_admin_screen_contract.py` (`/admin/solicitudes`
   carries it), `test_native_terminal.py`, `test_listado_consistencia.py`
   (the allowlist), `static-manifest-loaders.test.mjs` and
   `admin-crear-turno.test.mjs` (the loaders, fail-closed).
7. **`/ready`, graceful shutdown, optional Sentry.** Gate: a live `SIGTERM`
   during a request in flight completes that request before exiting (measured,
   not assumed); `/ready` correctly flips to unhealthy when Postgres is
   unreachable (fault-injection test); Sentry stays fully inert with no DSN
   configured (default state, verified by absence of any outbound call in a
   test run).
8. **Remaining route migration, open-ended.** Every other legacy route moves
   to a FastAPI router, module by module, each its own small PR gated by
   `route_parity_check.py` — no fixed target count or order prescribed here,
   same "coverage expansion" pattern as Tier 3's own routes tier. The
   reverse-proxy fallback keeps unmigrated routes fully functional throughout,
   so this slice can pause indefinitely without leaving the app in a broken
   state at any point.

   ### Slice 8 — first batch implemented (2026-08-06): patient-facing data
   ### APIs + core account surfaces

   Thirteen paths, chosen as the next coherent surface rather than a fixed
   count: four of `patient_site.py`'s read-only catalog data APIs
   (`/api/services`, `/api/doctors`, `/api/centers`, `/api/directory` — the
   home page's and the listing widgets' own data source) plus nine of
   `accounts.py`'s core register/login/logout/profile paths (`/account`,
   `/api/account/config`, `/me`, `/register`, `/login`, `/logout`,
   `/update`, `/dependents` GET+POST, `/dependents/delete`). No new
   mechanism — this batch is a straight application of slices 1 and 3's
   already-proven patterns to a wider surface, confirming both generalize
   past their pilots:

   - **`patient_site.py` is `turnos`-owned** (`modules/turnos/module.json`'s
     `routes`), so its four migrated paths follow slice 3's exact shape: a
     SEPARATE sibling file (`patient_site_router.py`, never inside
     `patient_site.py` itself, for the same "don't leak `fastapi` into dark
     mode's import path" reason `interview_patient_router.py` documents), a
     second `router_attr` manifest entry alongside the untouched legacy one,
     `Depends(deps.require_module_enabled("turnos"))` on the router, and
     `module_router.route_via_handle(patient_site)` as the one adapter —
     zero business logic re-written. Verified live: disabling `turnos`
     (`config.guardar({"ENABLED_MODULES": "intake"})`) turns `GET
     /api/services` into a plain 404 through the ASGI app while `GET
     /account` (platform-core, below) stays 200 — the module-contract
     property holds past its slice-3 pilot module.
   - **`accounts.py` is platform-core** (`docs/modules.md`'s explicit
     allowlist, confirmed by grep before writing a line of code — it is
     never one of any manifest's `routes`), so its nine paths follow slice
     1's shape instead: a router mounted directly in `app.py`
     (`app.include_router(accounts_router.router)`, same unconditional
     placement as `admin_auth_router.py`/`passkeys_router.py`), no
     `require_module_enabled` dependency, because there is nothing to
     disable it against.
   - **`module_router.route_via_handle()` (built for intake in slice 3)
     needed zero changes to serve a completely different pair of legacy
     files** — confirms it's a genuine general-purpose adapter, not an
     intake-specific helper that happened to get a generic name.
   - **`route_parity_check.py`'s `_run_legacy_intake()`/
     `_correr_escenarios_intake()` also needed zero new logic** — both were
     already generic (`(module, method, path, body)` in, same-response-
     assertion out); this batch only added a parameter (`escenarios=
     ESCENARIOS_INTAKE` default preserved) so `ESCENARIOS_PATIENT_SITE`/
     `ESCENARIOS_ACCOUNTS` could reuse the identical runner instead of a
     third near-duplicate loop. 13 new scenarios, all empty-diff (46 total
     in the script now). The `accounts` scenarios deliberately use
     synthetic, obviously-fake credentials/DNI (register/login/update all
     expected to fail validation, never actually create a row) so the
     script stays safe to run against any checkout's real database, same
     restraint `ESCENARIOS` already exercises for `/api/admin/login`.
   - **Left for the next slice-8 batch, same files, no new decision
     needed**: `patient_site.py`'s `/api/service-slots/<id>` and its
     non-API pages (`/doctors`, `/doctor/<id>`, `/service/<id>`, …);
     `accounts.py`'s `/account/reset`, `/auth/test`, `/auth/sso`, the
     synthetic test-identity endpoints, and the password-recovery/OTP
     family (`/recover`, `/reset`, `/reset_otp`, `/otp/send`,
     `/otp/verify`) — all secondary to the core path this batch covers.
   - **Gate**: `route_parity_check.py` — 46/46 scenarios empty diff. Full
     `scripts/ci_local.sh` green with `TURNOS_ASGI` unset (3132 backend +
     878 frontend + 12 e2e) AND `=1` (identical counts, same green) — two
     full runs, not one, per this tier's own doctrine that dark mode must
     show zero behavioral delta.

   ### Slice 8 — second batch implemented (2026-08-06): the booking flow
   ### itself

   Nineteen more paths, chosen as the next coherent surface — the actual
   patient booking flow the first batch's catalog APIs feed into: the
   service info + wizard "book" page, creating/listing/cancelling/
   rescheduling/waitlisting a request, the referral-AI pre-registration
   step, the slot soft-lock, and the two token-authenticated email-click
   flows (waitlist offer, appointment-reminder confirm). Same mechanism as
   the first batch, applied to four more files, plus four more paths added
   to the already-migrated `patient_site_router.py`:

   - **`patient_site_router.py` (existing file, no new manifest entry)**
     gains `/service/<id>` + `/service/<id>/book` (ONE FastAPI route,
     `{rest:path}`, not two — `handle()`'s own `path.startswith("/service/")`
     + `rest.endswith("/book")` branch already does the split, so a single
     template mirrors it exactly instead of hand-splitting the two cases
     into separate routes), `/api/service-slots/<id>`, and the two "Mis
     turnos" links (`/my-requests`, `/request/<id>` — both always a
     redirect, never DB-dependent). `/service/*` and `/request/*` use
     `{...:path}` converters, not a plain `{id}` — `handle()` strips/tests
     the WHOLE remainder after the prefix, matching legacy's
     `startswith()` + manual slicing; a single-segment converter would
     404 on any id containing a literal `/` legacy would have accepted.
   - **`booking_requests_router.py` (new)** — the PATIENT half only of
     `booking_requests.py`: `/api/requests` (list + create),
     `/api/requests/<id>` (detail), `/cancel`/`/reprogramar`/`/waitlist`,
     `/api/analyze-order` (referral-AI read, precedes registration) and
     `/api/orden-preview/<token>` (signed preview, token IS the
     capability). The file's `/api/admin/*` staff surface (config/login/
     theme/auth-mode/turnstile/the whole review queue/HSI sync/RENAPER)
     stays legacy-only — different gate (`admin_auth`), a later batch.
   - **`slot_holds_router.py`/`appointment_offer_router.py`/
     `appointment_confirm_router.py` (new)** — each file's full surface (2,
     3, and 2 paths respectively) migrates whole; none of the three has an
     admin half to split off.
   - Same `Depends(deps.require_module_enabled("turnos"))` gate on every
     new router (all five files are `turnos`-owned) — verified live again:
     `ENABLED_MODULES=intake` turns every new path into a 404 while
     `/account` (platform-core) stays 200, confirming the module-contract
     property holds past its now three migrated surfaces.
   - **First batch of scenarios with a genuine redirect** (`/service/<id>`,
     `/my-requests`, `/request/<id>` are always 302): `route_parity_check.
     py`'s shared `TestClient` defaults to `follow_redirects=True`
     (Starlette's own default), which nothing before this batch ever
     exercised. `_run_fastapi()` now passes `follow_redirects=False`
     explicitly so the comparison stays symmetric with `_run_legacy_intake
     ()`'s direct (never-redirect-following) `handle()` call — without
     this every redirect scenario false-positives a diff (302 vs. the
     followed page's 200).
   - **Orchestrator finding, fixed in the same batch though unrelated to
     route migration**: `app.py`'s `/health`/`/ready` were `@app.get(...)`
     only — `fastapi.routing.APIRoute` does NOT auto-add `HEAD` to a
     GET-only route the way `starlette.routing.Route` does, so a bare
     `HEAD /health` fell through to the reverse-proxy catch-all (which DOES
     declare `HEAD`) and 404'd against legacy, which has no such route.
     Fixed with explicit `methods=["GET", "HEAD"]` on both; three new tests
     in `test_health_ready.py`.
   - **Gate**: `route_parity_check.py` — 67/67 scenarios empty diff (46 from
     before + 21 new). Full `scripts/ci_local.sh` green with `TURNOS_ASGI`
     unset (3135 backend + 878 frontend + 12 e2e) AND `=1` (identical
     counts, same green) — same "two full runs" doctrine as the first
     batch. One finding along the way, NOT caused by this batch (reproduced
     on the pre-batch commit with none of these files present): the
     backend suite is intermittently red under `TURNOS_ASGI=1` alone, on
     an i18n assertion (`test_search_examples_i18n.py`, "the cache was just
     warmed for 'en' — pick_all_languages() should not treat it as cold")
     — `pack_l10n.localize()`'s 30 s TTL memo (docs/solutions/dev/
     i18n-content-translation-traps.md) racing against a ~110-140 s full
     suite run, apparently more likely to lose the race at whatever timing
     `TURNOS_ASGI=1`'s extra imports shift it to. Pre-existing, unrelated
     to booking-flow routing, and out of this batch's scope to fix — flagged
     here so it doesn't get mistaken for a regression the next time this
     gate goes red on an unrelated diff.
   - **Left for a later slice-8 batch**: `booking_requests.py`'s `/api/
     admin/*` staff surface; `patient_site.py`'s catalog/directory pages
     (`/doctors`, `/doctor/<id>`, `/d/<cid>`, `/study/<country>/<id>`,
     `/centers`, `/center/<slug>`, `/news`, `/news/pedidos`) and its
     de-linking redirects (`/app`, `/clinicas`, etc.); `accounts.py`'s
     `/account/reset`, `/auth/test`, `/auth/sso`, the synthetic
     test-identity endpoints, and the password-recovery/OTP family — same
     list the first batch already deferred, still untouched.

   ### Slice 8 — third batch implemented (2026-08-06): the request-review
   ### admin surface

   Nineteen more paths, all in the ONE file the second batch's own docstring
   had already flagged as "belongs with the rest of the admin surface's own
   migration" — `booking_requests.py`'s staff-facing half: `GET /admin` (the
   panel shell itself), the Settings triad (`theme`/`auth-mode`/`turnstile`,
   GET+POST each), the whole review queue (`/api/admin/requests` list plus
   `/api/admin/requests/<id>/{approve,assign,retry-hsi,reject,campos,
   attendance}`), HSI sync (`/api/admin/hsi-sync` + its two POST actions),
   the referral read (`/api/admin/order/<id>`) and the RENAPER lookup
   (`/api/admin/refeps`). No new file and no new manifest entry — every path
   is declared in the ALREADY-migrated `booking_requests_router.py` (second
   batch), appended to the same `router` with the same
   `Depends(deps.require_module_enabled("turnos"))` gate and the same
   `module_router.route_via_handle(booking_requests)` adapter the patient
   half already uses.

   - **`/api/admin/config`/`/whoami`/`/login`/`/logout` deliberately NOT
     re-declared here**, even though `booking_requests.handle()` also
     answers those four literal paths: `admin_auth_router.py` (slice 1,
     mounted unconditionally in `app.py` ahead of any turnos router)
     already claims them in both transports, calling the exact same
     `admin_auth.config/whoami/login/logout` functions — declaring them a
     second time here would just be a route Starlette never reaches
     (registration-order resolution), not a behavior change either way.
   - **No new auth mechanism.** Every admin endpoint's role gate
     (`admin_auth.require_admin(...)`, wrapped by this file's own
     `_check_admin`/`_request_staff` helpers) still runs from INSIDE
     `booking_requests.py`'s handler functions, unchanged —
     `route_via_handle()` forwards the whole request through
     `LegacyHandlerShim` exactly as slices 3/8(1st)/8(2nd) already proved,
     so `solo_admin`/`permitir_medico`/`permitir_admisionista`/
     `permitir_reportes` gate byte-identically in both transports. Verified
     live: `ENABLED_MODULES=intake` turns every path in this batch into a
     404 while `/api/admin/config` (platform-core, via
     `admin_auth_router.py`) stays reachable — the module-contract property
     holds past its now-largest migrated surface.
   - **`GET /admin` has no auth check of its own** (`_serve_admin()` — the
     gate is client-side, driven by `/api/admin/whoami` after the page
     loads), same "shell always 200, gate happens after" shape
     `patient_site`'s pages already have — so this is the first scenario in
     this batch that ISN'T a 401, and still passes the same "same response
     whatever it is" comparison (full HTML text, `_normalizar_body`'s
     non-JSON fallback path).
   - **`test_integrity_races.py` is unaffected by construction, not by
     luck**: its `TestRouteApproveRejectRace`/`TestRouteDoctorSlotCollision`
     etc. call the business functions (`_approve`/`_reject`/…) directly or
     through the legacy HTTP server, never through either FastAPI
     transport — this batch adds a SECOND way to reach the same functions,
     it doesn't touch the functions or their CAS guards. Ran green,
     17/17, both `TURNOS_ASGI` unset and `=1`, confirming the observation
     rather than just asserting it.
   - **Gate**: `route_parity_check.py` — 86/86 scenarios empty diff (67 from
     before + 19 new, all the new ones 401 except `GET /admin`'s 200). Full
     `scripts/ci_local.sh` green with `TURNOS_ASGI` unset (3135 backend +
     882 frontend + 12 e2e) AND `=1` (identical counts, same green) — same
     "two full runs" doctrine as the first two batches.
   - **Left for a later slice-8 batch, unchanged scope from before**:
     `admin_content.py` (catalog CRUD, ~6 paths), `admin_patients.py`
     (patient roster + dependents + clinical history, ~8 paths),
     `admin_appointments.py` (front-desk walk-in booking + overbooking
     slots, 2 paths), `doctor_appointments.py` (doctor's own agenda/stats/
     compatible-services, 7 paths), `doctor_patients.py` (doctor's patient
     roster, 1 path) — roughly 24 more paths across five files, deliberately
     NOT started this batch (19 admin-queue paths in one file was already a
     coherent, full-sized batch on its own); plus `patient_site.py`'s
     catalog/directory pages and `accounts.py`'s reset/OTP family, still
     deferred from the first two batches.

   ### Slice 8 — fourth batch implemented (2026-08-06): the admin-console
   ### surfaces (admin_content, admin_patients, admin_appointments,
   ### doctor_appointments, doctor_patients)

   Twenty-four more paths across the five files the third batch's own note
   listed as "left for a later batch, unchanged scope" — the last coherent
   surface of the admin console still legacy-only: catalog CRUD
   (`admin_content.py`), the patient roster + dependents + clinical-history
   import (`admin_patients.py`), the front-desk walk-in booking + slots
   (`admin_appointments.py`), and the doctor's own agenda and patient
   roster (`doctor_appointments.py`/`doctor_patients.py`). Same mechanism as
   every batch before it, applied to five new files, each with its own new
   sibling router and its own new `router_attr` manifest entry (unlike the
   third batch, which appended to an already-migrated file):

   - **Five new sibling files** (`admin_content_router.py`,
     `admin_patients_router.py`, `admin_appointments_router.py`,
     `doctor_appointments_router.py`, `doctor_patients_router.py`), each
     the same shape as every prior slice-8 router: `Depends(deps.
     require_module_enabled("turnos"))` on the router (all five files are
     `turnos`-owned per `modules/turnos/module.json`), `module_router.
     route_via_handle(<module>)` as the one adapter, zero business-logic
     rewritten.
   - **`admin_content.py`'s one genuine template-shape decision**: `POST
     /api/admin/content/knowledge/properties` is a LITERAL path `handle()`
     checks before its regexes (not a member of the `<tipo>/<id>` item
     shape), so it needs its own two-segment template
     (`/api/admin/content/{tipo}/{sub}`) distinct from the three-segment
     `/api/admin/content/{tipo}/{item_id}/restaurar` — Starlette
     distinguishes the two by segment count alone, so both can coexist as
     generic templates with zero literal-path validation duplicated into
     the router, same principle every earlier batch's `{...:path}`/plain
     `{id}` choices already established.
   - **`doctor_appointments.py`'s `{doctor}` param is a plain single-segment
     converter, not `{...:path}`**: both of that file's regexes
     (`_COMPAT_RE`, `_DOCTOR_APPOINTMENTS_RE`) are `[^/]+` — explicitly one
     segment, no slash — so a path converter would accept a shape the
     legacy regex never did; matching the regex's own strictness, not
     just copying the pattern from a file that needed something looser.
   - **No new auth mechanism**: every handler in all five files still calls
     `admin_auth.require_admin(...)` (with each file's own role
     combination — `permitir_medico`/`permitir_admisionista`/
     `permitir_reportes`/`solo_admin`) from INSIDE the legacy function,
     unchanged, so the role gate runs byte-identically in both transports.
     Verified live: `ENABLED_MODULES=intake` turns every path in this
     batch into a 404 while `/api/admin/config` (platform-core) stays
     reachable — the module-contract property holds past its now five
     newest surfaces.
   - **`test_integrity_races.py` unaffected by construction**: none of its
     17 cases call any of these five files' functions through either HTTP
     transport (they hit the business functions directly or via the
     legacy server), so this batch adds a second reachability path without
     touching a single guarded code path. Ran 17/17 green, both
     `TURNOS_ASGI` unset and `=1`.
   - **Gate**: `route_parity_check.py` — 110/110 scenarios empty diff (86
     from before + 24 new, every new one a 401 without a session cookie —
     all five files call `require_admin(...)` before touching Postgres, so
     no scenario here needed a database at all). Full `scripts/ci_local.sh`
     green with `TURNOS_ASGI` unset (3135 backend + 882 frontend + 12 e2e)
     AND `=1` (identical counts, same green) — same "two full runs"
     doctrine as every batch before it.

   ### Slice 8 — honest remaining-routes inventory (as of the fourth batch,
   ### 2026-08-06)

   Per this slice's own "no fixed target count or order" framing, closing
   it responsibly means naming what is STILL legacy-only and why, not just
   what moved. Compared the turnos manifest's `router_attr` entries and
   `app.py`'s direct mounts against every route-owning module in the repo:

   **Fully migrated by this point** (a `router_attr` twin or a direct
   `app.py` mount covers every path the file answers): `patient_site.py`'s
   booking-flow core (catalog/directory pages still pending, see below),
   `booking_requests.py` (both halves), `slot_holds.py`,
   `appointment_offer.py`, `appointment_confirm.py`, `admin_content.py`,
   `admin_patients.py`, `admin_appointments.py`, `doctor_appointments.py`,
   `doctor_patients.py`, `reports.py` (slice 9, see below), plus
   platform-core `admin_auth.py`, `passkeys.py`, `accounts.py`'s core
   paths, `site_identity.py`'s four real pages. Note the plain `handle()`
   manifest entries for `slot_holds`/`appointment_offer`/
   `appointment_confirm`/`reports` still sit in the manifest unchanged
   (dark mode's own registry) — this is by design, not drift: the
   `router_attr` entry only matters once `TURNOS_ASGI=1`.

   **Genuinely deferred functional surfaces — real future-batch work,
   nothing dormant about them**:
   - `patient_site.py`'s catalog/directory pages: `/doctors`, `/doctor/<id>`,
     `/d/<cid>`, `/study/<country>/<id>`, `/centers`, `/center/<slug>`,
     `/news`, `/news/pedidos`, plus the de-linking redirects (`/app`,
     `/clinicas`, …) — ~9 paths, deferred since the first batch.
   - `accounts.py`'s secondary paths: `/account/reset`, `/auth/test`,
     `/auth/sso`, the synthetic test-identity endpoints, and the whole
     password-recovery/OTP family (`/recover`, `/reset`, `/reset_otp`,
     `/otp/send`, `/otp/verify`) — ~9 paths, deferred since the first
     batch.
   - `site_identity.py`'s `/llms.txt` and `/robots.txt` — the four real
     pages migrated in slice 2; these two were never in `_ROUTES` and stay
     legacy-only.
   - `chart_summary.py` (Resumen del paciente AI chart summary),
     `lab_results.py` (patient lab-results view), `preparation.py`
     ("Subí tu orden" referral pre-check), `doctor_photos.py`/
     `avatar_sprite.py`/`account_photo.py` (photo upload+serve),
     `ai_costs.py`/`ai_health.py` (AI spend/health dashboards),
     `ocr_dni.py` (DNI-photo OCR), `prompts.py` (editable AI-prompt admin),
     `searches.py` (search-logging beacon), `sitemap.py` (`/sitemap.xml`,
     one trivial path), `connectors/puco/puco.py` (PUCO coverage lookup) —
     each its own small file, none started yet.
   - Platform-core admin/settings sections with no router file yet:
     `admin_users.py`, `admin_preferences.py`, `admin_password_reset.py`,
     `admin_account.py`, `settings_admin.py`, `panel_login.py`,
     `system_status.py`, `branding.py`, `legal.py`.

   **Different-transport surfaces — a plain REST router twin is possible
   but was deliberately not the priority**:
   - `whatsapp_chat.py` — `/api/whatsapp/webhook` (GET verify + POST
     ingest) for WAHA and Meta Cloud API: a genuinely third-party-initiated
     webhook, worth its own migration + verification pass (a bad diff here
     breaks WhatsApp silently) rather than folding into a generic sweep.
   - `mcp_api.py` — `/mcp` (stateless JSON-RPC 2.0, no SSE/session) plus
     `/api/account/mcp*`: a different protocol shape (MCP tool-calling)
     layered over HTTP, reasonable to treat as its own decision rather than
     a normal REST endpoint.
   - `chat_api.py` (on-site chat widget) and `voice.py` (transcription/
     voice-chat) — no protocol blocker, just genuinely plain JSON POST
     endpoints that haven't been reached yet; unlike the two above, these
     have no reason to be treated differently from the rest of this list
     once a batch gets to them.

   **Dormant/dead code — deliberately excluded from migration scope, not
   migration debt**: `app.html`, `panel.html`, `directorio.html` — all
   three still on disk but unconditionally 302-redirected by
   `patient_site.handle()` (Pinamar's de-linking from the WebTurno fork,
   pre-dating Tier 4 entirely); no live request ever reaches the file
   itself, so there is no route left to migrate. Re-confirmed accurate at
   this point in the tier — first documented by slice 6's own addendum
   above.

   ### Slice 9 implemented (2026-08-06): the Reportes batch

   The batch slice 8's own inventory named next — `reports.py`'s whole
   surface, thirteen paths, same mechanism as every batch before it:

   - **One new sibling file** (`reports_router.py`) and one new manifest
     entry (`reports_router`, `router_attr: "router"`, tail-append, no
     `after` — nothing else claims these paths), same shape as every
     slice-8/9 router: `Depends(deps.require_module_enabled("turnos"))`
     on the router (`reports` is `turnos`-owned per
     `modules/turnos/module.json`), `module_router.route_via_handle()` as
     the one adapter, zero business-logic rewritten.
   - **No path templates needed**: unlike every earlier batch, `reports.py`
     has no regex and no `{id}`-shaped path at all — all thirteen routes
     are `path == "literal string"` comparisons inside `handle()` — so the
     router is thirteen flat `router.add_api_route(literal, ...,
     methods=["GET"])` calls, the simplest file in this whole family.
   - **No new auth mechanism**: every handler still calls
     `admin_auth.require_admin(handler, solo_admin=True,
     permitir_reportes=True)` from INSIDE the legacy function, unchanged,
     so the role gate — `solo_admin` with the `reportes` role opted in —
     runs byte-identically in both transports.
   - **Gate**: `route_parity_check.py` — 123/123 scenarios empty diff (110
     from before + 13 new, every new one a 401 without a session cookie —
     `reports.py` calls `require_admin(...)` before touching Postgres, so
     no scenario here needed a database at all). Full `scripts/ci_local.sh`
     green with `TURNOS_ASGI` unset (3144 backend + 882 frontend + 12 e2e)
     AND `=1` (identical counts, same green) — same "two full runs"
     doctrine as every batch before it.

   ### Slice 8 CLOSING BATCH implemented (2026-08-06): everything the
   ### fourth batch's own inventory named, plus what it missed

   The fourth batch's own "honest remaining-routes inventory" above named
   three things left to do — `patient_site.py`'s catalog/directory pages,
   `accounts.py`'s reset/OTP family, and thirteen small untouched files —
   plus a separate "platform-core admin/settings sections with no router
   file yet" list of nine. This batch does ALL of it, in one commit, with
   the established patterns unchanged: sibling `*_router.py` + a
   `router_attr` manifest entry for every `turnos`-owned file, a sibling
   `*_router.py` mounted directly in `app.py` (no manifest entry, no
   `deps.require_module_enabled`) for every platform-core file — same
   split `accounts_router.py`/`admin_users_router.py` already draw.

   - **`patient_site_router.py`** grows to cover its catalog/directory
     pages (`/doctors`, `/doctor/<id>`, `/d/<cluster>`,
     `/study/<country>/<slug>`, `/centers`, `/center/<slug>`, `/news`,
     `/news/pedidos`) and the de-linking redirects at the top of
     `handle()` (`/app`, `/clinicas`, `/directorio`, `/obras-sociales`,
     `/panel`, `/clinica/*`, `/cobertura/*`) — no new manifest entry, same
     file/attr already listed.
   - **`accounts_router.py`** grows to cover `/account/reset`,
     `/auth/test`, `/auth/sso`, the synthetic test-identity endpoints, and
     the whole password-recovery/OTP family — same treatment.
   - **`site_identity_router.py`** grows to cover `/llms.txt` and
     `/robots.txt`, but via the generic `module_router.route_via_handle()`
     adapter (not the Jinja2 pipeline the other four pages use — neither
     file resolves `{{t.*}}`) — see that router's own docstring.
   - **Sixteen new `turnos`-owned sibling routers**, each its own new
     `router_attr` manifest entry, tail-append: `chart_summary_router`,
     `lab_results_router`, `preparation_router`, `doctor_photos_router`,
     `avatar_sprite_router`, `account_photo_router`, `ai_costs_router`,
     `ai_health_router`, `ocr_dni_router`, `prompts_router`,
     `searches_router`, `sitemap_router`, `connectors.puco.puco_router`,
     `chat_api_router`, `voice_router`, `calendar_feed_router`.
     `calendar_feed.py` is the one file even THIS inventory's own fourth
     batch missed naming — found auditing the manifest's plain routes for
     this batch, migrated anyway so "closes fully" stays honest.
   - **Nine new platform-core sibling routers**, mounted directly in
     `app.py` (the exact list the fourth batch's own inventory named):
     `admin_users_router`, `admin_preferences_router`,
     `admin_password_reset_router`, `admin_account_router`,
     `settings_admin_router`, `panel_login_router`, `system_status_router`,
     `branding_router`, `legal_router`.
   - **One genuine finding, documented at the point it applies rather than
     repeated here** (was two through this batch; `branding.py`'s logo routes
     moved off the list in slice 9, 2026-08-10 — see below):
     `chat_api.py`'s `POST /api/chat/stream` stays legacy-proxy-only — it is
     a real `text/event-stream` response that streams small `chunk`/`estado`
     events over 60-120s specifically so Cloudflare doesn't kill it as idle,
     and `legacy_shim.LegacyHandlerShim` buffers the WHOLE response into one
     `Response` object, which would silently turn the stream into one late
     blob (`chat_api_router.py`'s own docstring).
   - **Slice 9 (2026-08-10) — `branding.py`'s `GET /logo.png`/`GET /logo`
     moved OFF the reverse proxy and into `branding_router.py` natively.**
     The finding the closing batch documented — that `handle()` returning
     `False` means "no custom logo, fall through to the repo's static file,"
     a real three-way fallback chain `route_via_handle()`'s "`False` = 404"
     contract can't reproduce — is real but no longer blocks migration: the
     fallback chain is now modeled in the router itself
     (`branding.default_logo_path()`/`custom_logo_payload()`), not forwarded
     through `handle()`. Every contract the legacy chain gave those URLs is
     preserved: the custom-logo response carries the SAME CSP/nosniff/no-cache
     headers (`branding.CUSTOM_LOGO_HEADERS`, the one constant `_serve_logo`
     also uses), the shipped default is served with the SAME `image/png` +
     one-day cache + `Last-Modified` and the SAME conditional-GET contract
     `SimpleHTTPRequestHandler.send_head` honored, a missing default is a
     no-store 404, and `/logo` (no extension) has NO shipped default. The
     shipped default is a plain `Response`, NOT `FileResponse`: `FileResponse`
     would add an `etag` and `accept-ranges` the legacy
     `SimpleHTTPRequestHandler` never sent (Starlette computes both from the
     stat), so the plain-Response path reproduces the legacy response
     header-for-header. The conditional itself mirrors `send_head` line for
     line, not the HTTP RFC: `If-None-Match` on the request SUPPRESSES the
     `If-Modified-Since` check (-> 200); only an IMS that parses to a naive
     (read as UTC) or UTC datetime is compared, a non-UTC offset is left
     un-compared (-> 200), and an ill-formed value is ignored (-> 200); the
     304 carries the one-day `Cache-Control` but NOT `Last-Modified`
     (`send_head`'s NOT_MODIFIED branch sends only the status line).
     Pinned in
     `test/test_branding_logo_asgi.py` (all four custom formats, exact
     headers/length, the no-ETag/Accept-Ranges parity, full-app no-proxy
     reachability, the unreadable-custom fallback, and the send_head-exact
     304 contract: If-None-Match suppression, non-UTC offset -> 200, naive
     read as UTC -> 304, ill-formed -> 200, and no Last-Modified on the 304).
   - **Gate**: `route_parity_check.py` grew from 123 to 229 scenarios (all
     new ones empty diff) — building the new `ESCENARIOS_CHAT_API` family
     surfaced a genuine pre-existing gap in the harness itself, not in any
     migrated route: `_run_legacy_intake()`'s synthetic "not matched"
     response (`{"error":"no_encontrado"}`, no headers) had never actually
     been compared against `module_router.route_via_handle()`'s REAL
     not-matched response before (every earlier family's scenarios always
     got a genuine status back from `handle()`) — the real one is
     `HTTPException(status_code=404, detail={"error": "no_encontrado"})`,
     which FastAPI's own exception handler wraps as
     `{"detail": {"error": "no_encontrado"}}` with a `Content-Type`
     header. Fixed once, in the shared helper, not per-scenario. Full
     `scripts/ci_local.sh` green with `TURNOS_ASGI` unset (3145 backend +
     882 frontend + 12 e2e) AND `=1` (identical counts, same green).
   - **Unrelated live finding fixed in the same commit**: `server.py`'s
     `_PUBLIC_STATIC_EXT` predated `.mjs` — a root-path ES module
     (`/format.mjs`) 403'd while the SAME file fingerprinted under
     `/static/` served fine (that prefix bypasses the extension check
     entirely). Added `.mjs` to the whitelist; Tier 5's islands will serve
     `.mjs` files one at a time, not always through `/static/`.

   ### Tier 4 route migration and proxy retirement — CLOSED

   **Current status (2026-08-21; this supersedes the dated milestone text in
   this section).** `app.py` is a 97-line composition root, `server.py` is a
   33-line dark-mode entrypoint, and native terminal routing owns the final
   compatibility boundary. The executable census classifies all 565 source
   rows as native (382 unique routes), runs 287 concrete probes, and finds no
   fallback or protocol exception. WhatsApp webhook, MCP JSON-RPC, and chat
   SSE all have native transports. The current candidate has passed the
   complete legacy and ASGI managed gates, including backend, frontend, route,
   cassette-replay/audit, and E2E surfaces in each topology with the explicit
   expected-skip allowlist.

   The one-listener/no-proxy candidate was integrated and deployed to
   production and demo on 2026-08-20 after exact-revision dual-topology CI.
   Both labels run the native ASGI topology. The operational soak closed on
   2026-08-21; unsetting `TURNOS_ASGI` and redeploying the same revision remains
   an available dark-mode rollback.

   Alembic is the sole normal-boot migration authority. The legacy runner is
   restricted to the one-time bridge for a genuinely PARTIAL historical shape
   before Alembic convergence. Successful-auth parity and real stored passkey
   shapes are proven; only a soak with a physical hospital staff passkey is
   unavailable locally.

   The declarative worker owns eight jobs: waitlist, reminders, table hygiene,
   referral retention, data-volume backups, chart summaries, catalog
   translation, and HSI sync. `hsi_resync`, demo refresh, process-local
   cache/avatar warmups, and AI health retain web-process responsibilities by
   design or pending an independent persistence/safety proof.

   Remaining Tier 4 work is therefore bounded: perform the physical-passkey
   check, decide any further daemon moves on their own evidence, and finish
   the Jinja migration (82 current source `{{t.*}}` occurrences across 7 files
   outside documentation and tests, 68 in `admin.html`). The operational soak is
   closed on production and demo, and static fingerprinting is complete for
   the first-party tree: every first-party JS/CSS/MJS asset, including the
   intake module's interview assets, is content-hashed and no live manual
   version allowlist remains. Runtime-loaded assets resolve through
   `window.SANA_STATIC`. Outside the gates by design: Lottie
   (vendor hygiene) and the dormant `app.html`, `panel.html`, and
   `directorio.html` artifacts, which remain unreachable behind their
   intentional redirects; they are not transport debt.

   ### Security readiness for the ASGI cutover — milestone 2026-08-08

   **This is the security/dual-runtime readiness milestone, not Tier 4
   completion and not the cutover itself** (same discipline as the route
   milestone above). What is now PROVEN, with the full test ladder green
   in BOTH `TURNOS_ASGI` unset and `=1`:

   - **Proxy trust fails closed** (commit 58a3fe75): with the flag on, an
     absent, forged, or unverifiable peer marker resolves to "" (non-local),
     never to the proxy's loopback — the Turnstile loopback exemption, the
     RENAPER stub and `ip_exempt` stay shut. A marker is only trusted with
     a REAL signing secret configured.
   - **Non-loopback Turnstile IP exemptions retired** (this change):
     `ip_exempt.exento()` grants only a VERIFIED loopback peer; the branch
     that honored CF-Connecting-IP/X-Forwarded-For for listed remote IPs is
     gone (no CDN in front — those headers were client-suppliable end to
     end, in both topologies). Scripted/monitoring logins use the signed
     `X-Turnstile-Test-Bypass` header (`turnstile.bypass_activo`,
     constant-time, secret from env only). `scripts/turnstile_ip_refresh.py`
     deleted — its only job was maintaining the retired list.
   - **Deterministic evidence**: `test/test_turnstile.py` no longer depends
     on a persisted panel setting (gate pinned per test class);
     `test/test_passkeys_real_stored_shape.py` proves the ADAPTER path, not
     just a verdict; native-shim tests prove markerless peer checks fail
     closed instead of pinning `TURNOS_ASGI` off.
   - **Known bounded residual, deliberately NOT fixed**: RENAPER's
     rate-limit bucket key trusts the first `X-Forwarded-For` value
     (`cuentas/auth_renaper.py:_client_ip`). It gates an anti-abuse counter
     only — never identity — and keying it on the verified peer would
     collapse every patient behind one NAT/proxy into a single bucket. Real
     fix is trusted ingress; see the risk register row.

   The production cutover itself is closed. Physical staff-passkey validation
   remains follow-up hardening. The sequential
   [ASGI proxy retirement plan](asgi-proxy-retirement-plan.md) has now been
   executed and fully verified locally; exact-candidate integration,
   deployment, and soak remain before that retirement becomes live truth.

## Risk register

| Risk | Blast radius | Mitigation | Rollback |
|---|---|---|---|
| Reverse-proxy cutover (slice 0) mishandles a streaming body (large referral upload, ICS feed) | A real patient/staff-facing request breaks | Explicit manual pass over exactly these cases before prod cutover; demo-first soak | Unset `TURNOS_ASGI` and redeploy the same revision — `asgi_main._dark_mode()` calls the legacy entrypoint on the public port |
| **(found during slice 0 build; RESOLVED FAIL-CLOSED 2026-08-08)** Every peer-based "this request is genuinely local" check (`ip_exempt.exento()`'s loopback branch, `turnstile.py`, the RENAPER-stub dev bypass in `accounts.py`) sees the proxy's own loopback connection as the TCP peer for ALL traffic once `TURNOS_ASGI=1`, not just genuinely-local requests | Turnstile bypass / dev-only bypasses become reachable from the public internet the moment the flag is on anywhere off-box | RESOLVED (commit 58a3fe75 + this change): all three checks resolve the peer via `core/proxy_trust.real_peer()` — flag off: the socket peer, identical to always; flag on: only `app.py`'s HMAC-signed marker (which app.py strips from client input and re-signs), and an absent/forged/unverifiable marker or a missing real signing secret returns "" (non-local), never the loopback. Covered by `test/test_proxy_trust.py` + `test/test_ip_exempt.py` in both CI modes. The non-loopback `ip_exempt` header branch is retired outright (see the 2026-08-08 milestone section) | Unset `TURNOS_ASGI` for the same-revision dark-mode rollback; in ASGI, fail-closed means a missing or bad marker degrades to "not local", never to a bypass |
| RENAPER login rate-limit bucket keyed on client-suppliable first `X-Forwarded-For` value (`cuentas/auth_renaper.py:_client_ip`) — an abuser can rotate buckets | Anti-abuse throttle on the RENAPER OIDC start can be evaded (more requests/hour than intended); identity is NEVER affected — it comes from the signed OIDC state + provider round-trip | ACCEPTED as bounded residual (2026-08-08): keying on the verified peer would collapse all patients behind one NAT/proxy into a single 60/h bucket, worse than the abuse. Real fix is trusted ingress (CDN/proxy that overwrites the header) — same root cause as the retired `ip_exempt` branch | Tighten `TURNOS_RENAPER_PER_HOUR` or put trusted ingress in front; no code rollback needed |
| Session cookie dual-verify window has a bug | Staff logged out mid-shift | Parity-test both verifiers against a fixed cookie set before cutover | Revert to legacy-only minting/verification (single code path, no data loss — cookies are stateless) |
| Passkey library swap breaks an existing physical credential | Affected staff member loses passkey login, falls back to password+TOTP (still works — not a full lockout) | Adapter approach validated against a REAL captured registration before cutover, not just synthetic data | Grandfather path (keep the hand-rolled verifier alive for existing rows) if the adapter proves unreliable — see "Decisions for the product owner" |
| Alembic `stamp head` applied incorrectly against prod | Migration bookkeeping desyncs; a LATER real migration silently no-ops or double-applies | Schema-diff test proves the baseline migration matches prod's actual current schema byte-for-byte before any stamp runs | `alembic_version` is one metadata row — resettable; the actual schema is untouched by a bad stamp (blast radius is "next migration doesn't apply," caught by the schema-diff test, not silent data loss) |
| `procrastinate`'s schema competes for connections/locks under load | Degraded performance on the daemon(s) migrated so far | Land the pilot on demo first, watch for a stated soak period, matching this repo's own verification doctrine | Schema install is additive-only (new tables) — safe to leave in place even if the queue feature is disabled; per-daemon migration is independently reversible (old thread-based code stays available behind a flag until proven) |
| Sentry captures PII in a stack trace/breadcrumb once a DSN is set | A patient/staff identifier leaves the system to a third party, violating this repo's own PII doctrine | Explicit scrubbing review required BEFORE any DSN is configured — see "Decisions for the product owner" | DSN unset = fully inert; removing a configured DSN stops all outbound capture immediately |

## Decisions for the product owner

- **Q: Passkey migration — adapter (one library, reconstructed key material
  from stored JSON) or grandfather (keep the hand-rolled verifier alive
  permanently for pre-cutover rows)?**
  A (recommended): adapter, PENDING a short data-shape spike confirming the
  stored `cose_a_json()` fields are a faithful enough COSE-JSON projection to
  reconstruct verifiable key material — if that spike finds a gap, fall back
  to grandfather rather than force a data migration nobody can complete
  without physical device access.
- **Q: How long does the demo need to soak on the new strangler topology
  (slice 0) before prod cuts over?**
  A (recommended): a minimum of 3-7 days of the demo running clean (including
  its own daily `demo_refresh.py` cycle at least once) before touching prod,
  given the "hospital users must not be logged out" stakes — but this is a
  judgment call on acceptable delay vs. risk that only the product owner can weigh against
  the hospital relationship.
- **Q: `procrastinate` or `pgqueuer` for the background queue?**
  A (recommended): `procrastinate`, for the periodic-task fit reasoned above —
  flagged as a real decision, not a rubber stamp, since the product owner may weigh
  dependency footprint/maturity differently than this design did.
- **Q: Session dual-verify window — accept both old and new cookie formats
  for the full 30-day `TURNOS_ADMIN_SESSION_DAYS` default, meaning two
  verifier code paths live simultaneously for up to a month?**
  A (recommended): yes — it's what makes the cutover a zero-forced-logout
  event, and the alternative (a shorter window) trades a small amount of
  extra transition-code lifetime for forcing some staff to re-authenticate
  mid-window, which seems like the wrong trade for a hospital's front-desk and
  clinical staff.
- **Q: Should Sentry (or any Sentry-compatible tracker) ever actually be
  turned on for this project, given the PII sensitivity, or does the
  "optional" in the roadmap mean it ships wired-but-permanently-off unless a
  specific future need arises?**
  A (recommended): ship it wired-but-off (slice 7 builds the integration,
  inert by default) and treat "turn it on" as a SEPARATE future decision
  requiring its own scrubbing review — don't pre-commit to ever flipping it on
  as part of this tier.
