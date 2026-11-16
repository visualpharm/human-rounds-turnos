# Human Rounds

**The open-source, AI-native medical platform. Humans do the rounds. AI does the rest.**

AI interviews patients before the visit, reads handwritten referral orders from
a photo, judges urgency, and writes to the health record. The care team walks in
prepared. Patients are met by people, not paperwork.

**Live reference deployment:** [pinamar.humanrounds.org](https://pinamar.humanrounds.org).
Not a demo. Hospital de Pinamar (Buenos Aires province, Argentina) and its 7 CAPS
community health centres run their public appointment booking on this exact code:
120 services, 8 centres, 93 professionals, real patients.

Owned and built by [Generated Media, Inc.](https://generated.photos), Human
Rounds Core is released under [AGPL-3.0](LICENSE) with no software licence fee.
Commercial integrations and managed services are separate from Core; see the
[licensing and commercial boundary](docs/policies/LICENSING.md).

## How it works

One Python web process, one Postgres database, no build step. The town itself
is data, not code.

- **`app/asgi_main.py` is the only process entrypoint** (`python3 -m app`). It boots the FastAPI app
  composed in `app/main.py`, which owns the complete public route surface;
  `TURNOS_ASGI=1` is mandatory, any other value refuses to boot.
- **App modules on a platform core.** Appointments (`app/modules/turnos/`), the
  pre-consult interview (`app/modules/intake/`) and booking chat (`app/modules/chat/`)
  each declare routes, admin screens, tables and config keys in a
  `module.json` manifest, and can be switched off at runtime independently.
- **State lives in Postgres** (`psycopg`, no ORM): appointments, accounts,
  the request queue, admin sessions. Alembic is the sole schema authority —
  the app classifies the database at boot and runs pending migrations
  itself, so a pull-and-restart is the whole upgrade path.
- **Site content lives in layered data packs** (`packs/`, `data_packs.py`)
  resolved through `PACK_CHAIN`, general to specific. Local admin edits are
  stored in the database as an overlay on the pack, so an upstream pack
  update never clobbers them.
- **Everything country-specific is a connector** (`app/connectors/`, capability
  interfaces): identity, insurance, license, EMR sync, third-party slots,
  message transport. Consumers ask for an adapter by capability; a
  capability with no connector configured degrades gracefully instead of
  crashing.
- **AI goes through OpenRouter** — referral-order reading, the pre-consult
  interview, the chart summary, translation — with prompts editable from
  the panel. `pii.py` scrubs identifying data before any external call; the
  referral image is the one deliberate exception.
- **Background jobs run in a queue worker** (`app/worker.py`, procrastinate on
  the same Postgres): waitlist, reminders, housekeeping, HSI sync. In
  production it is its own process, and a down worker never makes the web
  app unhealthy.
- **No build step, no CDN.** First-party JS/CSS ships content-hash
  fingerprinted; page speed is won by making fewer requests.

Operators: `./install.sh` (Docker) → [Install in 5 minutes](#install-in-5-minutes).
From a checkout:

```bash
PORT=7700 TURNOS_ASGI=1 python3 -m app   # → http://127.0.0.1:7700
```

Tests (no network, no API key — `TURNOS_FAKE_AI=1` stubs every AI call):

```bash
TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_*.py"
node --test tests/*.test.mjs
```

## The encounter is the point

Rebuilt with AI in mind from the start. The model does the repetitive work
before, during and after care. The clinical record stays authoritative.

| Stage | What the software does |
|---|---|
| **Before the visit** | Asks the patient what's wrong, follows up, and judges urgency, so the most urgent are seen first. |
| **During the visit** | No typing through the consultation. History, key facts and trends surface on sight. |
| **Around the visit** | One system runs registration, booking, arrivals, queues and coverage checks. |
| **After the visit** | The encounter is summarized automatically, with prescriptions and study orders generated alongside it. |

## Running in production

Everything below is live at the Pinamar reference installation.

**For patients**

- **Find a service by typing 3 letters.** Search with stemming across the
  hospital and 7 health centres, ranked by relevance, not alphabetically.
- **Book online in 2 clicks.** Real slots, nearest first, held while you decide.
- **Referral orders read by AI.** Upload a photo of the doctor's order, even
  handwritten. The model detects what's being requested, for whom, whether it
  matches the service, and tells the patient how to prepare for the study.
- **A rejection carries the next step.** Alternative slots or a re-upload link,
  never a dead end and never the homepage.
- **Sign up with a photo of your national ID.** The same identifier the public
  health system already uses. Parents book for their children.
- **Coverage detected automatically** from the national insurance registry, so
  nobody has to know their own plan number.
- **Reminders the day before** by email and WhatsApp. Reply YES or NO to confirm
  or cancel.
- **Cancellation feeds a waitlist.** A freed slot is re-offered automatically to
  the next patient in line.
- **Booking chat**, on the site and on WhatsApp, driven by the same tool
  registry the panel uses.

**For staff**

- **A request queue that reads itself.** The AI reads every referral overnight
  and proposes accept or reject with the reason attached. Operators decide.
- **Today's patients, doctor agendas, capacity.** One screen per job, not one
  spreadsheet per person.
- **Story-driven reports**, including billing recovery and public-health
  reporting, written as findings rather than a wall of charts.
- **Health-record handoff.** Approving pushes the appointment into the network's
  EMR with automatic retry. Nothing breaks if the record is down: the booking
  persists locally and a resync daemon catches up.
- **Real admin auth.** Passwords, TOTP, passkeys, roles, and a bot gate, because
  this queue holds health data.

**For the operator running the install**

- **Multi-site engine.** Layered data packs, multi-language, a first-run
  installer wizard, and pluggable per-country connectors.
- **Nothing breaks because of one dependency.** No AI key, no email provider, no
  EMR, no identity service: every layer degrades instead of failing.

## Also in the box

`app/modules/intake/` ships enabled and is the second app module: the Pre-Consult AI
interview.

- **Pre-consult interview.** After booking, or by scanning a QR poster on the
  consultation-room door, the patient answers one adaptive question at a time.
- **Triage colour and a one-line summary** for the doctor's list, on a
  deterministic red-flag floor: the model can escalate urgency, never downgrade
  it below what the rules found.
- **Clinical brief before the patient walks in**, so the visit starts informed.
- **Printable prescription and study orders** composed from what the doctor
  ticked, optionally redrafted by a second AI pass using instructions the
  hospital writes in the panel.

It never diagnoses, never rates severity to the patient, and never promises a
wait time. That is the doctor's job at the visit. Design and the full safety
contract → [docs/architecture/entrevista-previa.md](docs/architecture/entrevista-previa.md).

## Install in 5 minutes

Requirements: [Docker](https://docs.docker.com/get-docker/) with the
`docker compose` plugin.

```bash
git clone <repository-url>
cd sana-turnos
./install.sh
```

That builds the engine, boots Postgres next to it, waits for both to come
up healthy, and prints the setup URL. A fresh install has no operator and
no configuration yet, so it lands on `/setup`: a wizard for location, data
pack, language(s), an admin account, and (optionally) an AI key. Full
options (bare metal, systemd/launchd, environment variables) →
[docs/INSTALL.md](docs/INSTALL.md).

## The engine is generic. The town is data.

Everything that's specific to Pinamar, center names, service hours, doctors, the
hospital's own copy, lives in a **data pack**, not in code. Packs compose in
layers, general to specific:

```
packs/
├─ global/          L1  medical content that isn't tied to any country
│                        (specialty → cluster taxonomy)
├─ ar/               L2  country data (phone/DNI format, timezone,
│                        currency, official-source domains)
├─ ar-b/              L3  province/state data (regional integrations —
│                        e.g. Buenos Aires' Mi Salud Digital)
└─ example-town/      L4  a fictional starter pack ("Sampleton") —
                          copy this for a new town
```

An installation picks its active chain (`PACK_CHAIN=global,example-town` is
the public default) and each pack contributes what it has. A higher, more
specific level either **replaces** a lower level's file whole (a town's own
`sitio.json`, its actual centers and services) or **composes** with it
(a shared taxonomy a province wants to extend, not override). Full design,
the override-vs-merge rule, and how the setup wizard scaffolds a brand-new
pack → [docs/architecture/data-packs.md](docs/architecture/data-packs.md).

To bring your own town: copy `packs/example-town/`, replace each file with
your real data in the same shape, place it under the install-owned
`TURNOS_PRIVATE_PACKS_DIR`, and point `PACK_CHAIN` at it; or let the setup
wizard's Location step scaffold it for you. Real installation data does not
belong in this public repository.

## Modules

Appointments (`app/modules/turnos/`) and the pre-consult interview
(`app/modules/intake/`) are **modules** on top of a platform core: accounts, admin
auth, config, i18n, data packs, notifications. Each declares its own routes,
admin sections, database tables and config keys in a `module.json` manifest that
a test asserts against reality, and each can be switched off at runtime without
touching the others. Teleconsultation and a clinical-history summary are next.
What a module owns and how a new one plugs in → [docs/modules.md](docs/modules.md).

## Connectors

Every integration with an external, country-specific system, national ID
verification, insurance lookup, professional-license lookup, EMR sync,
third-party slot availability, WhatsApp/SMS delivery, is a **connector**:
one of six capability interfaces, swappable per deployment.

| Capability | Argentina's connector |
|---|---|
| Identity verification | RENAPER (national ID registry) |
| Insurance lookup | PUCO/SISA |
| Professional license lookup | REFEPS |
| EMR sync | HSI (Historia de Salud Integrada) |
| Third-party slot source | Mi Salud Digital (provincial) |
| OTP/message transport | WhatsApp via Meta Cloud API only in production (WAHA is loopback dev-only, explicit opt-in); OTP unsupported until an approved Authentication template ships |

Deploying in a different country means writing new connectors for the ones
Argentina's don't apply to. Everything else keeps working, and a
capability with no connector configured degrades gracefully instead of
crashing: no ID verification service and password login stays on, no EMR and
appointments still book locally. The India walkthrough (Aadhaar identity,
a state insurance scheme) and the full PR checklist →
[docs/connectors.md](docs/connectors.md).

## Languages

UI strings and pack content (service names, descriptions) are two separate
translation layers. UI strings are short and hand-translated; pack content
is long-tail and machine-translated with a cache, so nobody has to
hand-translate hundreds of service names into every enabled language. Off
by default: an installation opts in with `LANGUAGES=es,en` and a switcher
mode. Design and the resolution order (cookie → admin default →
`Accept-Language` → first enabled) → [docs/architecture/i18n.md](docs/architecture/i18n.md).

## Tech stack

Python with a deliberately thin web layer, and vanilla JavaScript on the front:

- **Backend**: FastAPI served by one `uvicorn` process (`app/asgi_main.py` is the
  only entrypoint; `app/main.py` composes every route). The `http.server`-based
  server was retired on 2026-08-27. Handlers are plain `async def` functions
  over the existing synchronous domain code (database work runs in a thread
  pool); JSON request bodies are declared as Pydantic models
  (`app/core/api_body.py`) and a body that does not match its model is answered
  400 before the domain runs, and the OpenAPI/Swagger pages (`/docs`,
  `/openapi.json`) are served to a signed-in admin only
  (`python3 scripts/export_openapi.py --out openapi.json` writes the same
  schema without a server). Postgres via
  `psycopg` and a connection pool; queries are SQLAlchemy Core statements
  over the schema in `app/core/schema_model.py`, never the ORM, and Alembic runs
  the schema migrations. Background jobs run in
  a procrastinate queue (`app/worker.py`) or in daemon threads, depending on
  `TURNOS_QUEUE_WORKER`.
- **Frontend**: plain HTML/CSS/JS, no build step, no bundler, no npm
  install for the app itself. `node --test` runs the JS test suite; that's
  the only place Node enters the picture.
- **AI**: OpenRouter (model-agnostic, the reference deployment uses Gemini
  Flash) for referral reading, the pre-consult interview and translation, with a
  backup-key failover.

**SQLAlchemy Core on the psycopg cursor — why there is no `Engine` or
`Session`.** Queries are written as SQLAlchemy Core statements, but they run on
the pooled psycopg connection that `app/core/db.py` already hands out:
`app/core/sqlcore.py` compiles a statement for the psycopg dialect and executes it on
the caller's cursor (`sqlcore.run(cur, select(...))`). That keeps one pool, one
place for the health check, the timeouts and the «database unavailable» answer,
and one transaction per `with db._connect()` block shared by every statement in
it; rows stay the plain dicts the rest of the code reads. A second pool owned by
an `Engine` would duplicate all of that, and the ORM `Session` would change where
transactions begin and end in a code base that does not need identity-mapped
objects. The cost is that the bridge calls three private SQLAlchemy members, so
SQLAlchemy is pinned exactly and `tests/test_dependency_pins.py` fails as soon as
an upgrade moves any of them. Alembic is the one place an engine exists, for the
migrations only.

The point isn't nostalgia. It's that a public-health IT department with
limited resources can read, deploy, and maintain every line without a
frontend build pipeline or a framework-version treadmill.

## Testing

```bash
TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_*.py"
node --test tests/*.test.mjs
```

`-W error::ResourceWarning` turns an unclosed file/socket into a hard test
failure instead of a silent leak that only shows up as GC noise later.

`TURNOS_FAKE_AI=1` stubs every AI call so the suite runs with no network
and no API key. A golden-page test renders the home, service, and center
pages two ways (pack-chain mode vs. a reconstructed pre-pack snapshot) and
asserts byte-identical output: the safety net for every data-layout
change. Full contributor guide → [CONTRIBUTING.md](CONTRIBUTING.md).

## Upgrading

Code and data packs are upstream (this repo); your Postgres database and
the `/app/data` volume are yours. `git pull && docker compose up -d
--build` pulls new code and runs any pending schema migration at boot.
No separate migrate step, and a local admin edit (a corrected service
name, a config value) never gets clobbered by an upstream pack update,
because it lives in the database, not in the file that changed. Full
contract, backup/rollback steps, and release conventions →
[docs/UPGRADING.md](docs/UPGRADING.md). What shipped so far →
[CHANGELOG.md](CHANGELOG.md).

## Roadmap

- **Teleconsultation** and a **clinical-history summary**, each as its own
  module against the same core. Design docs live in `docs/roadmap/`; neither is
  started.
- **More regional connectors**: identity, coverage and record systems beyond
  Argentina.
- **Voice intake** and **scan-to-register** (national ID plus insurance
  credential), prototyped in a sibling project, entering the platform after
  review.

## Status

The Pinamar reference deployment is **live and in real use** by the
hospital and its CAPS network. The engine itself is mid-rework toward
general reusability: the layered data-pack model, i18n, runtime settings,
the module and connector seams, this packaging, and the update model
(schema migrations plus the catalog-overlay contract) are all shipped. The
JSON-key rename from Spanish to English (`localidad` → `city`, and so on)
is **in progress**, file by file, see [docs/rename-map.md](docs/rename-map.md)
for what's done and what's pending. None of it blocks a deployment:
every layer degrades gracefully, and the golden-page tests hold the
reference deployment's actual output constant while the rename proceeds
underneath it.

Currently tracked as `v0.9.0-pre`, no tagged release yet. See
[CHANGELOG.md](CHANGELOG.md) for the honest rundown of what's done and
what's still a known gap.

## Three ways in

- **Health organizations, run it.** Free to deploy, no licence fee. The Pinamar
  installation is live proof. Grant-funded public pilots can launch without a
  software licence fee.
- **Developers, extend it.** Fork the repo and open a pull request with a local
  module: your region's identity, coverage and health-record connectors.
  Start at [CONTRIBUTING.md](CONTRIBUTING.md).
- **Investors and grant organizations, fund it.** Money goes to hardening,
  independent validation and the next public pilots. Contact via
  [humanrounds.org](https://humanrounds.org).

## Ownership and licensing

Copyright © 2026 [Generated Media, Inc.](https://generated.photos)

Licensed under [AGPL-3.0](LICENSE). If you deploy a modified version,
including as a network service, your modifications are open source too.
The AGPL Core, Enterprise-services and managed-cloud boundary is documented in
[LICENSING.md](docs/policies/LICENSING.md).
Security policy → [SECURITY.md](SECURITY.md).
