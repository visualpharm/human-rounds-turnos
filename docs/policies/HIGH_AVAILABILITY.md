# High availability

What a Human Rounds installation can and cannot survive: what is supported
and tested here, and which promises (RTO/RPO) are deliberately not made.
Companions: [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md),
[docs/UPGRADING.md](../UPGRADING.md),
[backup-and-restore.md](../operations/backup-and-restore.md) and
[restore-rehearsal.md](../operations/restore-rehearsal.md).

## Publication

- The public repository is one commit made with `git archive` from the private main branch; its history is not published.
- Licence AGPL-3.0, copyright Generated Media, Inc.; [LICENSING.md](LICENSING.md) lists the assets that are not under the AGPL.
- Kept out of the release (`export-ignore` in `.gitattributes`): the reference deployment's private data pack, agent harness files (`.claude/`, `CLAUDE.md`), research records made against the real catalog, the login screen's humor images, and installation-only operations notes.
- Everyone who wrote code for the project agreed on 2026-11-11 to its publication under AGPL-3.0.

## Availability model

**Supported and tested (single node):**

- One process entrypoint, `python3 -m app` (`app/asgi_main.py`), with
  `TURNOS_ASGI=1`; any other value is a boot error. There is no second
  serving topology to fail over to
  ([deploy.md](../architecture/deploy.md)).
- `/health` (process alive, no database) and `/ready` (Postgres reachable,
  base table present, Alembic at head; 503 otherwise), both outside the
  optional site-wide Basic Auth (`app/routers/health.py`, `app/core/health.py`).
- Schema changes apply at boot under a cross-process advisory lock, so two
  booting containers cannot race a migration.
- The optional queue worker (`TURNOS_QUEUE_WORKER=1`, a supervised child of
  the web process): a down worker never makes the web app unhealthy, and
  durable jobs resume when it returns.
- Fingerprinted assets bind URLs to content (`app/core/static_fingerprint.py`);
  verify served build identity after every release.

**Single points of failure (deliberate):** one app container, one Postgres,
one host, one `/app/data` volume. Off-host copies exist only if the
installation configures a backup destination.

**Who runs what:** the municipal installation `salud.pinamar.gob.ar` is hosted
by the municipality, updated from a release package in an agreed window, and
its backups are the municipality's. The test sites `pinamar.humanrounds.org`
and `demo.humanrounds.org` deploy on every push to the main branch.

No SLA ([SUPPORT.md](../../.github/SUPPORT.md)); nothing here is an uptime
commitment.

## RTO and RPO

**No RTO or RPO is published, promised, or measured.** A figure an
installation quotes must come from its own timed restore on its own hardware
and data; until then both are undefined.

What follows from the design, not from measurement:

- **Recovery time** is: a host, the recorded release, the database restore,
  the files, and the boot-time migration check. None has a measured duration.
- **Data loss** reaches back to the last backup that was captured, verified
  and can be read. The encrypted full backup runs daily by default
  (`TURNOS_BACKUP_INTERVAL_MIN=1440`); the legacy B2 sweep every 6 hours. A
  backup never restored is not a backup.

## Backups

An installation is protected by whatever subset it configured and verified.

1. **Encrypted full backup** (`app/ops/backup_full.py`, the recommended path;
   [backup-and-restore.md](../operations/backup-and-restore.md)): database
   dump, the whole data volume, private packs and the installation env file
   (recovery keys removed) in one archive, AES-256-GCM encrypted before it
   leaves, to an R2 or path-style S3 bucket the installation owns. Every
   archive is downloaded back and verified; a weekly restore test runs into a
   scratch database when one is configured. The «Sistema» screen shows upload,
   integrity and restore-test age. Retention deletes nothing unless explicitly
   enabled.
2. **Legacy B2 copy of `data/`** (`app/ops/data_backups.py`, `TURNOS_B2_*`):
   raw files, unencrypted, up to 200 MiB each, journaled in `backup_journal`
   and `backup_versions`. Restore is operator-only and no-replace
   (`app/ops/data_backups_restore.py`).
3. **Platform database dumps and manual volume copies**
   ([coolify-reference.md](../deploy/coolify-reference.md), "Backups"); by
   default on the same host.

**The municipal installation:** the application never deletes or overwrites
a remote backup there, by any path (`app/core/backup_lock.py`, held by
`tests/test_backup_lock.py` and `tests/test_salud_guards.py`); rotation is
the municipality's.

**What covers what:** the database holds appointments, accounts, catalog
edits, prompts, queue jobs and the backup journals; the data volume holds
referral files, photos, private packs and `turnos.env`. The full backup
covers both; a database dump alone or a volume copy alone covers half.
Runtime secrets and the backup recovery key stay in the installation's own
secret store; code is rebuilt from the recorded release. Backups contain
personal data and configuration: restrict access to them.

## Degradation contracts

Booking keeps working when an optional dependency fails:

- **Email** (Mailgun or Mailjet): a missing key is a silent skip; approval
  persists locally.
- **HSI:** a failed push never blocks approval; the sync journal retries it
  ([hsi.md](../architecture/hsi.md)).
- **AI:** no key or a failed call falls back to deterministic paths; the
  desk reviews every request.
- **Error tracking:** off without a DSN; a failed send never breaks a
  request (`app/core/error_tracking.py`).
- **Backups:** unconfigured or failing leaves the site working; «Sistema»
  shows a warning.
- **Database down:** `/health` stays 200, `/ready` answers 503.
- **Queue worker down:** the web app stays healthy; jobs wait.
- `TURNOS_OUTBOUND_KILL=1` is a drill tool, not a production control.

## Approval record

record: approval:hr-6e9fdfb1134ba9f8

date: 2026-11-16
