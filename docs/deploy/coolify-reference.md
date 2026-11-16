# Coolify reference — an additional source-build deployment path

This is a generic, followable recipe for running Human Rounds on
[Coolify](https://coolify.io/) (a self-hosted PaaS you run on your own
server). It is an **additional** install path: it builds the app **from the
repository's own `Dockerfile`** — the same file `docker compose up` builds —
so it needs no published image — only the repository source, reached as a Git
remote your operator account can access — your own fork or clone.
The primary documented install path is Docker Compose (plus bare metal) →
[../INSTALL.md](../INSTALL.md); reach for this Coolify path if you already run
Coolify and want the app managed by it. An operator with repository access who
has never touched the Pinamar reference deployment can follow it start to
finish; nothing here depends on the separate private ops repo or on
instance-specific secrets.

The abstract contract *any* install path has to satisfy (cache busting,
`/app/data`, secrets, migrations at boot, health, graceful shutdown) lives in
[../architecture/deploy.md](../architecture/deploy.md); read that for the
"why" behind the steps here.

## Read this honesty note before you start

These things are deliberately **not** claimed, because they are not yet built
or verified (they are designed as Tier 6 follow-on work — see
[../roadmap/tier6-delivery-design.md](../roadmap/tier6-delivery-design.md),
Area D):

- **Published-image delivery is not verified.** The repository contains a Helm
  chart and a digest-pinned release Compose path, but those files do not prove
  that a versioned registry image has been published. This recipe builds from
  the `Dockerfile`; it does not require an existing registry release.
- **Native ASGI is required.** Keep `TURNOS_ASGI=1`, as set by the image.
  The legacy server and same-revision fallback were deleted. Explicitly
  clearing or changing this value makes startup fail; it cannot select an
  older server. Production and demo use the native composition.
- **The queue worker is optional.** With `TURNOS_QUEUE_WORKER` unset (the
  default), background jobs run as in-process daemon threads in the web
  container. You can deploy, serve
  patients, and ignore the worker entirely. Scaling it out to its own process
  is covered at the end as an opt-in.
- **No real hosted run of this exact generic recipe has been independently
  verified.** Every command and contract below is grounded in the repo's own
  `Dockerfile`, `docker-compose.yml`, `.env.example`, health endpoints, and
  upgrade/backup docs — but Coolify versions and UI change, so treat the
  Coolify-specific clicks as a map, not a script. The verification step at the
  end is the proof a deploy actually landed.

## What you need

- A Coolify v4 instance on a server you control, with a build capacity and a
  working proxy (Coolify ships Traefik).
- A Git remote Coolify can reach and your operator account has access to — your
  own fork or clone of this repo. Point Coolify at a branch you control.
- A **Postgres 14+** database — Coolify's built-in managed Postgres, or any
  external Postgres you operate.
- A domain with DNS pointing at your Coolify server.
- Exactly one required secret: a Postgres connection string for `DATABASE_URL`.
  Everything else (email, WhatsApp, AI order-reading, HSI, identity lookups)
  degrades gracefully when unset — this repo's "nothing breaks because of email
  or HSI" invariant. See [../INSTALL.md](../INSTALL.md) "Environment variables".

## Step 1 — Create the app (build from the `Dockerfile`)

In Coolify: **New Resource → Dockerfile** (or *Public repository* / *Private
repository* pointed at your Git remote). Select your repo and branch. Coolify
detects the `Dockerfile` at the repo root and builds it — the same image
`docker compose up --build` produces.

Set Coolify's **port** to `7700`. The `Dockerfile` does `EXPOSE 7700` and the
process listens on `PORT=7700` by default (`HOST=0.0.0.0`, `PORT=7700` are
baked in as `ENV`). The image's `CMD` is `python3 asgi_main.py`, with
`TURNOS_ASGI=1` baked in. Do not override it with an empty value in Coolify.

> Do not change the build pack to "Docker Compose" here. This recipe builds
> the single app image the `Dockerfile` defines; the bundled Postgres in
> `docker-compose.yml` is a convenience for the Compose path only. Bring your
> own database (Step 2).

## Step 2 — Postgres (bring your own)

Add a database: a Coolify Postgres resource, or an external managed Postgres.
Capture its connection string — you will set it as `DATABASE_URL` in Step 3.
The schema (and any pending migration, including the Alembic layer) creates
and applies itself automatically on the first boot, inside a cross-process
`pg_advisory_lock` so two containers booting at once cannot corrupt it. There
is **no manual migrate step** — see [../UPGRADING.md](../UPGRADING.md).

If Coolify exposes both an internal and an external connection string for the
database, prefer the **internal** one for the app container; keep the external
one for ad-hoc admin from your laptop. Either resolves, but the internal path
stays inside the Coolify network and avoids exposing Postgres publicly.

## Step 3 — Environment variables

Set these in Coolify's **Environment Variables** section (the equivalent of a
`.env` file). The single source of truth for every variable name and its
default is [`.env.example`](../../.env.example) — read it for the full list.
A minimal, production-sane starting set:

```bash
# REQUIRED credential — supply your database connection string.
DATABASE_URL=postgresql://USER:PASS@HOST:5432/DBNAME
# Required topology — matches the image default; never clear it.
TURNOS_ASGI=1

# Strongly recommended for a real deployment:
# Your installation timezone — datetimes are always converted to this zone
# before use (zoneinfo-based; read by core/tz.py, not yet listed in
# .env.example). Pin it so schedules and reminders match your clinic.
TURNOS_TIMEZONE=America/Argentina/Buenos_Aires
# Canonical origin used to build absolute links (emails, chat, HSI handoff).
# A fresh checkout falls back to loopback and never trusts the request Host;
# set the public origin explicitly in every deployment.
TURNOS_PUBLIC_ORIGIN=https://your-domain.example
# Expected final domain — drives the "Dominio" card in /admin Sistema.
TURNOS_DOMINIO=your-domain.example
# HMAC secret for admin session cookies. Auto-derived if unset (dev only) —
# set it so admin sessions survive a container restart.
TURNOS_ADMIN_SECRET=generate-a-long-random-string
```

Optional integrations — each one silently skips when its key is missing, so add
only what your clinic uses (all names and defaults in `.env.example`):

- **AI order-reading**: `OPENROUTER_API_KEY` (Gemini via OpenRouter). Without
  it, referral reading is skipped, never a 500.
- **Email**: Mailgun by default (`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`,
  `MAIL_FROM`, …) or Mailjet (`CONNECTOR_EMAIL_TRANSPORT=mailjet`,
  `MAILJET_API_KEY`, `MAILJET_API_SECRET`, `MAIL_FROM`).
- **WhatsApp**: `META_WA_*` (Meta Cloud API, recommended for real deployments)
  or `OTP_WAHA_*` (local dev). `TURNOS_WA_WEBHOOK_SECRET` unset = the inbound
  webhook route does not exist.
- **Turnstile** (captcha on login/signup): `TURNOS_TURNSTILE_SECRET` /
  `TURNOS_TURNSTILE_SITEKEY`.
- **Off-box backups**: `TURNOS_B2_KEY_ID` / `TURNOS_B2_APP_KEY` /
  `TURNOS_B2_BUCKET_ID` (see Backups below).

Runtime config that does not need a redeploy (pack chain, languages, API keys)
can also be set live from `/admin` → Settings once an admin account exists.

## Step 4 — Persistent volume at `/app/data`

Add a Coolify **persistent storage** entry mapped to `/app/data`. Uploaded
referral photos and generated files live there at runtime; without a mounted
volume they are lost on every redeploy. This mirrors the `app_data:/app/data`
volume in `docker-compose.yml`. **Note which kind you create — a named Docker
volume or a host bind mount — because the backup command in "Backups" depends
on it.**

This volume is also where the installation's durable, install-owned state
lives — all of it survives a `docker compose up --build` / a Coolify redeploy
only because `/app/data` is mounted:

- **Install-owned packs** — a town pack scaffolded by the first-run wizard
  (Step 6) is written to `/app/data/packs`, not the image-tree `packs/`.
- **The canonical install config** — the image sets
  `ENV TURNOS_ENV_FILE=/app/data/turnos.env`, so the boot keys the wizard
  writes (`TURNOS_PRIVATE_PACKS_DIR` / `TURNOS_REQUIRED_PACKS` /
  `PACK_CHAIN`, via `config.save_env_keys`) live on this volume too. Without
  it, those keys would be recreated in the ephemeral container filesystem
  (`/app/.env`, which `.dockerignore` excludes from the image — nothing is
  baked) and wiped on the next build, leaving the durable pack unreachable.

So a missing or empty `/app/data` after a redeploy is not just lost uploads —
it is a first-run config that has to be redone. Mount the volume before you
finish the wizard. Detail →
[../architecture/deploy.md](../architecture/deploy.md) ("The container's
canonical `.env` lives ON that same volume"). The data **pack** (your clinic's
catalog) is a different thing and ships inside the image under `packs/` — see
Step 8 and [../architecture/data-packs.md](../architecture/data-packs.md).

## Step 5 — Domain and TLS

Set your domain in Coolify. Coolify's proxy terminates TLS and forwards to the
app on port 7700. The app itself speaks **plain HTTP** on `PORT` — never
expose port 7700 directly to the internet; let the proxy own TLS.

## Step 6 — Deploy and run the first-run wizard

Deploy. On a fresh install (zero operator accounts, zero config rows) the app
serves `/setup` instead of the patient site — the first-run wizard walks you
through location, data pack, language(s), an admin account, and (optionally)
an OpenRouter key. Nothing the wizard collects touches the database until the
final commit. Visit `https://your-domain.example/setup`.

## Step 7 — Auto-deploy webhook (optional)

Configure Coolify's Git webhook (or "push to deploy") so a push to your branch
triggers a rebuild. Coolify generates the webhook URL and secret — paste the
URL into your Git host's webhook settings. The secret is Coolify's, not
something this repo prescribes.

## Step 8 — Your clinic's data pack (durable, access-controlled)

The engine ships `packs/example-town/` (a fictional "Sampleton") for demos and
first-run exploration only — it is **not** a production data home. A real
clinic's catalog (real service names, real centers, real professional
identifiers) is identifying data, and it must live somewhere **durable and
access-controlled**, outside the public engine tree. Treat
it with the same care as the database — a pack that exists only in an
ephemeral container or a throwaway checkout is lost on the next rebuild. How
the pack chain resolves, and how to build one, is in
[../architecture/data-packs.md](../architecture/data-packs.md).

Two ways to bring a pack, both covered there:

- **An operator-managed private pack on a persistent mount.** Configure
  `TURNOS_PRIVATE_PACKS_DIR`, name the private pack in `PACK_CHAIN`, and list
  required packs in `TURNOS_REQUIRED_PACKS`. Missing required data must refuse
  startup, not silently fall back to another installation's catalog. Follow
  the linked data-pack contract for the directory layout and permissions.
- **The `/setup` wizard's install-owned pack.** The wizard writes it beneath
  `/app/data/packs` and persists the private-root, required-pack and chain keys
  in `/app/data/turnos.env`. With Step 4's volume mounted, both the pack and its
  configuration survive rebuilds. Keep backups of both the database and volume.

**The catalog overlay does not replace a missing base pack.** Edits an admin
makes from the panel are stored as rows in the database `catalog` table that
**layer on top of** a base pack file by id — they override individual entries,
they do not conjure a whole catalog that is not on disk. A deployment with
overlays but no resolvable base pack serves gaps, not a site. Ship a complete
base pack first; the overlay is how a local correction survives an upstream
update, not how a catalog comes into existence. See [../UPGRADING.md](../UPGRADING.md)
"The catalog overlay is what makes this safe".

## Step 9 — Verify the deploy actually landed

A single successful request does not prove every request hits the new build —
a stale container left alongside the new one can serve the old and new build on
alternating requests. The repo ships a reusable post-deploy check for exactly
this: [`scripts/smoke_prod.py`](../../scripts/smoke_prod.py). From a checkout
of the same commit you deployed:

```bash
python3 scripts/smoke_prod.py https://your-domain.example
# or, if you set the optional prototype Basic-Auth gate (Step "Health"):
python3 scripts/smoke_prod.py https://your-domain.example YOUR_BASIC_USER:YOUR_BASIC_PASS
```

What it checks is **specific to the reference data pack**: a fixed security
matrix (sensitive paths — source files, the env file, the upload directory,
unauthenticated PHI-listing routes — must be 403/401), a fixed list of pages
that must return 200, byte-for-byte equality of every versioned asset against
the local checkout, and 10 cache-busted home-page requests that must all serve
the **same** build. The page list includes service routes whose slugs come
from the reference pack, so **review and adapt the route matrix to your own
clinic's pack** before relying on the green/red verdict — a route your pack
does not provide is a false negative, and a sensitive route your deployment
adds that the matrix does not cover is a silent gap. Exit 0 = green. Do not
diagnose a data problem in production until it is green.

## Health checks

Two endpoints, both exempt from the optional Basic-Auth gate:

- **`GET /health`** — liveness. Returns `200 {"status":"ok"}`. Process-up only,
  no database, cannot hang or 500. The `Dockerfile`'s `HEALTHCHECK` probes
  it with the image's own Python (there is no curl or wget inside), and
  Coolify picks that directive up by itself: leave Coolify's own health check
  disabled, since it needs curl or wget in the container.
- **`GET /ready`** — readiness. Runs `SELECT 1` + the schema's base table +
  confirms Alembic is at `head` (the same probe the admin «Sistema» panel runs)
  and answers **503** the moment any of those fails. Use this for a stricter
  readiness gate, not for liveness.

These endpoints belong to the native ASGI composition; there is no alternate
legacy serving mode to select.

## Backups — do this before you trust the deploy

An upgrade should be a no-op, but "should" is not a backup
([../UPGRADING.md](../UPGRADING.md) "Before upgrading a production installation"):

```bash
# Postgres
pg_dump "$DATABASE_URL" > backup-$(date +%Y%m%d).sql
```

For `/app/data` (uploaded referrals/photos), the right command depends on what
you created in Step 4:

**If it is a named Docker volume** (the `docker-compose.yml` default, and the
easier one to snapshot), snapshot the volume without touching the running
container — replace `YOUR_APP_DATA_VOLUME` with the actual volume name Coolify
created:

```bash
docker run --rm -v YOUR_APP_DATA_VOLUME:/data -v "$PWD":/out alpine \
  tar czf /out/data-backup-$(date +%Y%m%d).tar.gz /data
```

**If it is a host bind mount**, back up the host directory Coolify mounted at
`/app/data` directly, using whatever path Coolify assigned (find it in the
resource's persistent-storage settings):

```bash
tar czf data-backup-$(date +%Y%m%d).tar.gz -C /your/coolify/data/path .
```

If you are unsure which kind Coolify created, check the resource's
persistent-storage configuration before scripting anything — a `docker run -v`
aimed at a name that is actually a host path (or vice versa) silently does the
wrong thing.

Prefer Coolify's scheduled Postgres backups for the database so they are not
forgotten. For `/app/data`, either keep the manual copy above on a schedule or
configure the optional Backblaze B2 sweep (`TURNOS_B2_*` above; runs on a
schedule when configured). The Postgres dump and the upload-volume copy are
independent failure domains — back up both.

## Rollback

Do not assume every schema migration can be reversed safely. Some historical
revisions deliberately reject downgrade; an additive revision with a tested
inverse does not make a whole production rollback safe. Use the pre-upgrade
backup recovery path if an upgrade goes wrong:

1. Stop the app (Coolify "Stop", or however you stop the container).
2. Restore the pre-upgrade dump against a **fresh/empty** database
   (`psql "$DATABASE_URL" < backup-YYYYMMDD.sql`) — restoring on top of the
   partially migrated one will conflict.
3. Point Coolify at the prior commit/branch and redeploy.

Full detail (including the one-time Alembic cutover recovery, which is a
single metadata row, not a schema rollback) → [../UPGRADING.md](../UPGRADING.md)
"Rollback". This is why the backup step is not optional.

## Optional — the queue worker as its own process

Skip this unless you have a reason. With `TURNOS_QUEUE_WORKER` **unset** (the
default), background jobs run as daemon threads inside the web container. If you
later want to move that work out of the web process, one opt-in,
documented in [../architecture/deploy.md](../architecture/deploy.md):

- **`TURNOS_QUEUE_WORKER=1`** — the web container also spawns and supervises
  `python3 worker.py` as a child process in the same container. The web app's
  `/health` never depends on the worker. The worker exposes its own
  `/health` + `/ready` on `TURNOS_WORKER_HEALTH_PORT` (default `7710`, loopback
  of the container). A down worker never makes the web app unhealthy.

There is no second-container mode (`external` was removed 2026-10-30; no
installation ran it).

Honest caveat on the worker's outbound reach: some migrated jobs send real
traffic (reminders, WhatsApp, HSI pushes, B2 backups). `TURNOS_OUTBOUND_KILL=1`
is a **proof/soak kill switch** (it default-denies outbound sockets for testing
business queues with no side effects) — it is **not** how you turn notifications
off in production. Disabling a channel in prod is per-provider config (leave the
relevant keys unset); never set `TURNOS_OUTBOUND_KILL` on a real clinic deploy.

## ASGI native mode

`TURNOS_ASGI=1` runs the complete FastAPI/Uvicorn composition on one listener.
Production and demo use this topology. There is no dark rollback in the same
revision. Keep `TURNOS_ASGI=1`; use a separately verified prior release and
the upgrade recovery procedure if a release must be rolled back.

## What this walkthrough does not cover

- **Registry publication and Kubernetes operations.** The chart under
  `charts/human-rounds/` and `docker-compose.release.yml` cover other delivery
  paths; their existence is not verification of a published registry image.
  This walkthrough remains a source-build Coolify recipe.
- **HSI integration** — bring your own; the app is fully usable without it.
- **Any instance-specific data or secrets** — this recipe is generic. The
  Pinamar reference deployment's own Coolify setup (real app IDs, webhook
  secrets, domain) is private to that project and is intentionally not needed
  here.

## See also

- [../architecture/deploy.md](../architecture/deploy.md) — the generic deploy
  contract every install path satisfies (the "why" behind these steps).
- [../INSTALL.md](../INSTALL.md) — the Docker Compose and bare-metal install
  paths.
- [../UPGRADING.md](../UPGRADING.md) — backups, schema migrations, rollback,
  release conventions.
- [`.env.example`](../../.env.example) — every variable name and its default;
  the single source of truth for what you can set.
