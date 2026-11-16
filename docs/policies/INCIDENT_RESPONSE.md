# Incident response

Runbook for whoever operates a Human Rounds installation: recognise,
contain, recover from and learn from an incident. It covers the engine in
this repository and a single-node deployment built from it
([deploy.md](../architecture/deploy.md)). Companions:
[HIGH_AVAILABILITY.md](HIGH_AVAILABILITY.md),
[backup-and-restore.md](../operations/backup-and-restore.md),
[restore-rehearsal.md](../operations/restore-rehearsal.md),
[docs/UPGRADING.md](../UPGRADING.md) and [SECURITY.md](../../SECURITY.md).

This is a volunteer project with no SLA ([SUPPORT.md](../../.github/SUPPORT.md))
and no incident team; maintainers do not watch installations. Each
installation owns its monitoring, escalation and timings. The municipal
installation `salud.pinamar.gob.ar` is run by the municipality: its host,
logs, database and backups are the municipality's.

## Publication

- The public repository is one commit made with `git archive` from the private main branch; its history is not published.
- Licence AGPL-3.0, copyright Generated Media, Inc.; [LICENSING.md](LICENSING.md) lists the assets that are not under the AGPL.
- Kept out of the release (`export-ignore` in `.gitattributes`): the reference deployment's private data pack, agent harness files (`.claude/`, `CLAUDE.md`), research records made against the real catalog, the login screen's humor images, and installation-only operations notes.
- Everyone who wrote code for the project agreed on 2026-11-11 to its publication under AGPL-3.0.

## Preparation

Before an incident, have:

1. The deployed release's commit recorded next to the deployment.
2. A verified backup: the encrypted full backup with a recent restore test,
   or a database dump plus a copy of the data volume (the dump alone lacks
   the uploaded files). A backup never restored is not a backup.
3. Runtime secrets and the backup recovery key in your own secret store,
   outside the server.
4. Authority to stop and restart the app, the worker and the database.
5. This runbook rehearsed once, with synthetic data.

## Severity levels

| Level | Meaning | Typical signals |
|---|---|---|
| **SEV1** | Patient service down, or suspected exposure or loss of patient data | `/ready` answers 503; 5xx on the patient site; an exposure report |
| **SEV2** | Degraded, booking still persists | mail failing (a missing key is a silent skip); HSI push failing; backup overdue in «Sistema»; worker down while the web stays healthy |
| **SEV3** | Cosmetic or single-user, no data risk | a misrendered screen, a lost preference |

Signals:

```bash
curl -s http://127.0.0.1:7700/health   # {"status":"ok"}: process alive, no DB check
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7700/ready
# 200 = Postgres reachable, base table present, Alembic at head; 503 otherwise
```

Both answer outside the optional site-wide Basic Auth. The «Sistema» screen
(`app/admin_panel/system_status.py`) groups database, storage, backups, HSI
and mail. With `TURNOS_SENTRY_SIGNALS` and a DSN set, an installation also
reports closed failure codes, a daily digest of counts and worker check-ins
to its own Sentry project ([deploy.md](../architecture/deploy.md)).

## Reporting

- **Suspected vulnerability or patient-data exposure:** follow
  [SECURITY.md](../../SECURITY.md), a private advisory, never a public issue.
  Never attach real patient data, referrals or DNI photos; reproduce with
  synthetic data.
- **Operational incidents:** the installation's own operators and
  coordinators. Maintainers cannot see your deployment; do not wait for them.
- **Engine defects found during an incident:** the public issue tracker,
  after containment, with a synthetic reproduction.

## Response runbook

**0. Record.** Keep a timeline of detection, actions and decisions. Later
steps destroy volatile state.

**1. Triage.** Compare `/health` and `/ready`: alive but not ready means the
database side is broken; do not restart-loop it. Check container state, the
«Sistema» screen, and the worker registry (`python3 -m app.worker --jobs`,
read-only). Log lines worth searching: `[data_backups_queue]`,
`BLOQUEADO` (the outbound kill switch refusing a connection in a drill).

**2. Contain.** Stop the app to freeze writes (`docker compose stop app` or
the platform's stop) and confirm no writer remains. Workers are threads in
the web process or (`TURNOS_QUEUE_WORKER=1`) its child, so they stop with it.
Do not set `TURNOS_OUTBOUND_KILL=1` in production; silence notifications
through provider configuration.

**3. Preserve.** Before any destructive fix, dump the current database and
copy the data volume and logs; the damaged state is evidence:

```bash
docker compose exec -T db pg_dump -U sana -d sana_turnos > incident-db.sql
```

Volume-copy commands: [coolify-reference.md](../deploy/coolify-reference.md),
"Backups".

**4. Recover.**

- Prefer a verified backup over a migration downgrade; a downgrade function
  does not prove that data survives.
- Restore only into a fresh, empty database and host, keeping the original
  database, volume and backups. The full-backup recovery steps are in
  [backup-and-restore.md](../operations/backup-and-restore.md).
- Never clear migration metadata to force a retry.
- Restores never overwrite: the legacy B2 restore refuses an existing
  destination (`app/ops/data_backups_restore.py`). Deleting remote backups
  needs an explicit setting; on a `*.gob.ar` installation the application
  never deletes or overwrites one (`app/core/backup_lock.py`).
- Switch traffic only after the checks below.

**5. Verify.**

- `/ready` answers 200 and `alembic_version` matches the release's head.
- Counts and spot checks of the tables that mattered (`booking_requests`,
  `catalog`, `operators`, `users`) against the last good baseline.
- Backups resume: «Sistema» shows fresh upload and integrity times.
  An incident that silently stopped backups is not over.
- The site serves the build you expect; every fingerprinted asset resolves
  (`scripts/smoke_prod.py` on a deployment it is calibrated for).

**6. Communicate.** Tell staff and the coordinating institution what to
expect through the installation's channels; no patient identifiers in any
engine-side report.

## Post-incident review

Write a short blameless record: timeline, root cause, what was lost and up
to which backup, the steps actually taken, and where this runbook was wrong.
Fix the engine or this file in the same change, and re-run the synthetic
restore rehearsal after any change to the backup or restore path. Tests that
travel with this runbook: `tests/test_data_backups.py`,
`tests/test_data_backups_restore.py`, `tests/test_data_backups_worker_proof.py`,
`tests/test_queue_worker.py`, `tests/test_backup_lock.py`.

## Approval record

record: approval:hr-5e1dc7cee644bed7

date: 2026-11-16
