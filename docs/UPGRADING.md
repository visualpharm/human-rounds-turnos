# Upgrading

Code + data packs live upstream (this git repo, tagged `v1.x` releases once
they start). What's local to your installation is the Postgres database
(operator accounts, catalog overlays, translation cache, config rows,
prompt edits) and the `/app/data` volume (uploaded referrals, photos). An
upgrade pulls new code, never touches your local data except through an
explicit, numbered migration.

## Explicit EMR selection

The Core no longer selects the temporary HSI adapter implicitly. Before
upgrading an installation that intentionally connects to HSI, set
`CONNECTOR_EMR_SYNC=hsi` alongside its existing HSI configuration. New
Enterprise deployments select `CONNECTOR_EMR_SYNC=emr_bridge` with the bridge
URL and token. Leaving the setting absent is the safe Core-only mode: booking
continues locally and no vendor package is imported or contacted.

## Docker Compose

```bash
git pull
docker compose up -d --build
```

That's it. Migrations run automatically at boot (see below) — no separate
migrate step, no downtime beyond the container restart itself.

## Bare metal

```bash
git pull
python3 -m pip install --break-system-packages -U 'psycopg[binary]' 'Pillow>=11.0'
# restart the process (systemctl restart sana-turnos, or however you're running it)
```

## How schema migrations work

**Alembic is the schema authority.** At startup, `db.py` takes a
cross-process `pg_advisory_lock` (so two containers booting at once can't
race each other) and runs Alembic's `command.upgrade(cfg, "head")` — a no-op
when the database is already at the tip, the baseline and then every
revision on a fresh install, and exactly the pending forward revisions on an
existing one. There is no separate migrate command to run.

You don't write or run migrations by hand — a new schema change ships as a
new revision file (`alembic/versions/000N_*.py`, plain SQL via
`op.get_bind().exec_driver_sql(...)`, no ORM) and applies itself the next
time the app boots.

The FIRST revision (`0001_baseline`) replays the text that used to be
`db.py`'s `_SCHEMA_SQL` — since 2026-10-25 the frozen file
`alembic/baseline/0001_schema.sql`, never edited again (a test pins its
hash). For a fresh database, running the baseline and then every revision
produces the schema committed in `alembic/schema-head.txt`.

### A database from before Alembic

Every installation that ran this app before 2026-08-06 was moved under
Alembic by a one-time bridge on its first boot of a newer version (the old
numbered runner in `migrations/`, tracked in a `schema_migrations` table
that stays behind as a harmless fossil). The bridge was removed on
2026-11-05. Such a database can only come back from an old dump: one taken
from an installation that had not yet booted a release from 2026-08-06 or
later. Its tables are there but `alembic_version` has no row.

The boot refuses it before running any DDL, with the message
`this database predates Alembic (tables …, no alembic_version row)`.
Replaying the baseline over it would create empty English tables next to
the Spanish-era ones that hold the data. To bring it forward:

1. Restore the dump into an empty database (see "Rollback" below).
2. Boot that database **once** with the release tagged `bridge-last` (the
   last commit that still carries the bridge). Its boot converts the
   schema and records the Alembic revision; if the data forbids a repair
   (duplicate rows under a unique key, NULLs under a NOT NULL) it fails
   with the exact statement instead — fix those rows and boot again.
3. Stop it and boot the current release, which applies the rest of the
   revisions as on any existing installation.

## Before upgrading a production installation

**Back up first** — an upgrade should be a no-op, but "should" isn't a
backup:

```bash
# Postgres
pg_dump "$DATABASE_URL" > backup-$(date +%Y%m%d).sql

# /app/data (uploads) — if you're not already relying on the B2 off-box
# backup (data_backups.py, TURNOS_B2_* — see .env.example), take a manual copy:
docker compose exec app tar czf - /app/data > data-backup-$(date +%Y%m%d).tar.gz
```

If you have `TURNOS_B2_KEY_ID`/`TURNOS_B2_APP_KEY`/`TURNOS_B2_BUCKET_ID`
configured, `data/` already gets swept to Backblaze B2 on a schedule
(`data_backups.py`, default every 6h) — check the Sistema page in `/admin`
for the last successful run before you skip the manual copy.

## Rollback

Rollback is **revision-specific**, not a generic undo. Revisions 0001–0008
refuse downgrade; 0008 rewrites legacy EMR observations in place. Revisions
0009 and 0010 have real downgrades that drop the day-queue event and passkey
challenge tables respectively, losing their contents. That is not a general
license to downgrade an installation. Boot reapplies the deployed code's
migration head, so a schema-only downgrade is not a persistent code rollback.
If an upgrade goes wrong:

1. Stop the app and every worker; verify writes have ceased. Preserve the
   current database, volume, logs and known-good backup before repair.
2. Restore the exact pre-upgrade database dump into a separate verified-empty
   database with `psql -v ON_ERROR_STOP=1`. Supply credentials through the
   installation's approved secret mechanism, not a URL in process arguments.
   Restore the matching data-volume recovery point into a separate empty
   volume. Do not overwrite the original installation.
3. Verify restored rows, audit records, file hashes and schema revision, then
   boot the matching prior code/image in the recovery environment. Verify
   health, build identity and synthetic user journeys before an authorized
   traffic cutover. Preserve concurrent checkout work; never force-checkout it.

### Older code against a newer database

Booting an older release against a database a newer release already migrated
is refused, not served: the stamp in `alembic_version` names a revision the
older code has no script for. The app stays up but every connection is
refused at once — `/ready` answers 503, the panel login shows a card instead
of the form, and the log carries one ERROR line from `turnos.schema` with the
stored revision, the newest revision this code knows and the way out (deploy
that newer release again or a later one, or restore the backup taken before
its migration; a downgrade is run with the newer release's own scripts, never
with this one). The decision is taken once per process; the app re-reads only
the stamp on each connection, so a restored database is served again without
a restart. Pinned by `tests/test_schema_unknown_revision.py`.

If `alembic_version` appears inconsistent, compare the actual schema and
data against the exact migration source in an isolated copy. Do not clear,
stamp or reinsert a revision marker merely to make boot pass. Clearing it
can invoke the legacy classification/repair path, not just change one row.
A migration-specific metadata repair requires independently established
schema/data state and preserved backups.

`tests/test_queue_sse.py` exercises the real head-to-0008 round trip and the
refused downgrade through 0008, including unchanged synthetic booking rows
and non-empty queue/challenge state after refusal. These narrow regression
tests are not an independent installation restore or proof that every
future migration is reversible. Rehearse backups before relying on them.

## The catalog overlay is what makes this safe

This is the mechanism that lets upstream keep shipping data-pack updates
without ever clobbering an installation's local edits, worked through with
one concrete example:

1. An admin at some hospital opens `/admin` → Centros and edits a center's
   name — say, correcting a typo in `centros-salud.json`'s "Hospital de
   Pinamar" entry. Saving does **not** touch the file on disk. It writes one
   row into the `catalog` Postgres table: `tipo='centros'`, `id='<the
   center's id>'`, `doc={"nombre": "<the corrected name>", ...}`.
2. Every request that renders that center reads through
   `catalog.merge_with_overlay()`: load `centros-salud.json` from the
   active pack, then for each id, if a `catalog` row exists for it, that
   row's `doc` **replaces the base entry whole**. The corrected name is
   what patients see.
3. The installation updates its private town pack's
   `centros-salud.json` — maybe it adds a new center, maybe it fixes an
   unrelated field on a different entry. **The `catalog` row is untouched**
   because it lives in Postgres, not in the pack. The next request still
   resolves the admin's corrected name because the overlay wins by id. The
   typo fix survived a base-data update it had nothing to do with.
4. If upstream's new base file happens to ALSO fix that exact typo, nothing
   breaks either — the overlay row still wins, it's just now redundant. An
   admin can always "Restaurar original" from the panel, which deletes the
   overlay row and falls back to whatever the current base file says.

Full mechanism (soft-delete, restore, per-type validation, the 3-second
cache, DB-down degradation) → [architecture/catalogo-y-datos.md](architecture/catalogo-y-datos.md).
The same override rule applies one layer up, between pack levels
(`architecture/data-packs.md`) — a town's own pack overriding a shared
global file works the same way, file-for-file instead of row-for-row.

**One caveat while the JSON-key English rename is in progress**
([rename-map.md](rename-map.md)): a `catalog` row is a snapshot of
whatever key names the base file used *at the moment it was edited*. If a
file's base keys get renamed (`nombre` → `name`) in a later upstream
release, an old overlay row for that same id keeps its old Spanish keys
until the row itself is re-saved — the reader has to reconcile old-key
rows against a new-key base, which is why the rename proceeds file by
file with its own test coverage rather than in one sweep. Not a reason to
delay upgrading; it only matters if you're also tracking the rename's
progress closely.

## What upstream owns vs. what's yours

| Upstream (this repo) | Local (your installation) |
|---|---|
| Engine code (`.py`, `.js`) | Postgres database (operators, catalog overlays, config rows, translation cache, appointments, patient accounts) |
| Public base and example packs (`packs/global`, `packs/ar`, `packs/ar-b`, `packs/example-town`) | Any private or town-specific pack under `TURNOS_PRIVATE_PACKS_DIR`, plus overlay rows on top of it |
| `alembic/versions/*.py` | The rows those migrations create/modify |
| `.env.example` (documents var names) | Your actual `.env` (never committed, never touched by a pull) |
| Everything under `docs/` | The `/app/data` volume (uploaded referrals, photos) |

The `catalog` table's admin-edit overlay is the general mechanism this
relies on: any content an admin edited from the panel lives in the
database, layered on top of the JSON base — pulling a new base file never
silently reverts a local edit. Worked example above.

## Release conventions

Tagged releases (`v1.0.0`, `v1.1.0`, ...) start once the engine is stable
enough for a first cut — see [CHANGELOG.md](../CHANGELOG.md) for what's
shipped so far, currently tracked as `v0.9.0-pre` (pre-release, no tag cut
yet). Semantic versioning once tags start: a MAJOR bump for a breaking
schema or config change that needs manual intervention beyond `git pull`,
MINOR for new features/packs/connectors, PATCH for fixes. Every release
gets a `CHANGELOG.md` entry before the tag, written for someone deciding
whether to upgrade — what changed, not a commit log dump.

**Pinamar prod tracks `main`, not a tag** — see the section below. That's a
valid installation choice (you always get the latest fix, at the cost of
occasionally getting the latest bug too), not the recommended default.
**If you're running your own deployment, pin to the latest tagged release**
once tags exist, and read the CHANGELOG between your current tag and the
one you're upgrading to before pulling.

## The Pinamar reference deployment

Prod (`https://pinamar.humanrounds.org`) tracks `main` directly — Coolify
redeploys on every push (the exact setup moved to the private ops repo
`humanrounds-pinamar` on 2026-08-05, see
`<workspace>/human-rounds/humanrounds-pinamar/docs/deploy-coolify.md`; the
generic contract any installation's deploy pipeline has to satisfy stays
here at [architecture/deploy.md](architecture/deploy.md)).
It's an installation like any other; it just happens to always run the
latest commit instead of a tagged release, because it's also where the
engine gets built and dogfooded in real use.
