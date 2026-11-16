# Encrypted installation backup and recovery

The backup_full job supersedes the plaintext data-only B2 recommendation.
It needs no Coolify account. It backs up the application database first,
then the data volume, private packs, any configured legacy webdata directory
and canonical env file into one encrypted
archive. The program is identified by release commit/image and requirements
hash; keep that exact release available in the repository/registry separately.

## Configure on any installation

Use /setup/backups on a new installation, or
/admin/settings?field=TURNOS_BACKUP_S3_ENDPOINT later. Enter the R2 endpoint,
bucket, access key and secret. Cloudflare R2 account endpoints and generic
path-style S3 over HTTPS are supported. Region defaults to auto for R2;
set the provider's exact signing region for generic S3. No virtual-hosted
buckets, endpoint base paths, redirects, STS session tokens or plain HTTP
production endpoints are supported. Give the bucket credential object
GET/PUT/HEAD/LIST/DELETE and multipart create/list/complete/abort permissions.

Generate the recovery key, copy/download it and store it OUTSIDE the server.
Only its fingerprint is shown on later reads. The KEK is held in the owner-only
canonical env file through core.managed_secrets so jobs can run unattended.
It is never stored in PostgreSQL. Both active and retained KEKs are removed
from every canonical-env copy in the archive, including its duplicate in data/.
Losing the server and the operator's saved key means losing recovery.

Test connection writes, reads, HEADs and deletes one tiny random object.
It tests the saved Settings destination; save edits before pressing Test.
The setup step probes staged unsaved values. Setup keeps credentials and
keys in expiring process memory; its signed cookie contains only a random
token. A restart/30-minute expiry requires re-entering that step.
Only the final setup commit persists any settings. Explicitly skipping
external backups leaves a warning and disables new backups.
TURNOS_CONFIG_LOCK=1 refuses backup edits/key generation. When a KEK is
injected directly into the process environment, rotate at that managed
injection source; writing a shadowed env-file key is refused.

## Settings and scheduling

All keys have the TURNOS_BACKUP_ prefix:

| Suffix | Default / purpose |
|---|---|
| ENABLED | 0, explicitly enable after setting destination/key |
| S3_ENDPOINT, S3_BUCKET, S3_KEY_ID, S3_SECRET | installation-owned destination |
| S3_PREFIX, S3_REGION | human-rounds/ and auto |
| RECOVERY_KEY, OLD_KEYS_SECRET | managed installation secrets, never DB settings |
| INTERVAL_MIN | 1440; shared legacy cadence setting when B2 is explicitly active |
| RETAIN_DAILY, RETAIN_WEEKLY, RETAIN_MONTHLY | 7 / 4 / 6 distinct calendar buckets |
| RETENTION_APPLY | 0; deletion requires explicit 1 — never on a `*.gob.ar` origin, where the application deletes or overwrites no backup (`core/backup_lock.py`; =1 is ignored with an ERROR and a «Sistema» warning) |
| UPLOAD_MAX_HOURS, RESTORE_MAX_DAYS | 36 hours / 10 days freshness |
| RESTORE_INTERVAL_DAYS | 7 days |
| SCRATCH_DATABASE_URL | empty; explicit administrative URL for an isolated test PostgreSQL cluster |

Run python3 manage.py backup run for a manual immediate attempt. The worker
registry declares filesystem/outbound capabilities and a rerun contract.
An hourly due check runs a daily backup by default; attempts are serialized
through a PostgreSQL session advisory lock across thread, worker and CLI.
In thread topology the producer polls due state every minute. With
TURNOS_QUEUE_WORKER=1 the worker is a child of the web container and sees
the same data, private packs and canonical env file. A down worker does not imply backups
are healthy; Sistema reads actual journal timestamps.

Pass SOURCE_COMMIT as a build argument (or inject the immutable release commit
with SOURCE_COMMIT/COOLIFY_GIT_COMMIT_SHA), and retain image digest/tag metadata
when available. With neither commit nor immutable image digest, capture is
partial because the matching program cannot be identified.

pg_dump and pg_restore must match or be newer than the source server's
major version. The Dockerfile includes postgresql-client; custom installations
must install compatible tools. Missing pg_dump is a hard failure with a clear
CLI message, never a data-only success. No database credentials appear in
subprocess argv or error output; libpq receives them in the child environment.

## Consistency and completeness

pg_dump uses an exported repeatable-read snapshot. Reference paths, schema
head and key-table counts are read from that SAME snapshot. Copying files
starts after the dump: files are written before referencing rows commit.
A referenced file that exists but did not make it into the archive, or a
reference outside every copied root, makes the archive partial and is reported
by restore (reference_check.missing). A referenced file already gone from the
source disk when the copy closes is listed in reference_check.absent_at_source
and leaves the status alone: no archive could hold it, and a file lost once
must not turn every later archive partial.
File changes trigger one reread; any observed changing file is recorded and
makes the run partial. Size cap is 200 MiB per uploaded/private-pack/env file
(the database dump is not capped). Symlinks, special/unreadable files, unsafe
paths and capped files are listed in manifest.skipped, never silently omitted.
Only explicitly documented obsolete producer manifests, ephemeral env-writer
staging and recovery-key redaction are exempt from partial status. See DATA_MAP's backup inventory.

Temporary plaintext staging is owner-only and removed after the operation;
provide adequate private scratch disk (several times database + file size).
This implementation does not claim secure erasure on SSDs or encrypted swap.
Runtime errors have value-free journal codes. SIGKILL can leave private
temporary staging directories for operator cleanup and unjournaled encrypted
remote objects; unknown objects are protected from automatic deletion.

## Verification and retention

Evidence is separate:

- uploaded: HEAD length and ETag match the upload receipt.
- integrity_verified: EVERY archive, of EVERY size, is downloaded completely,
  its encrypted SHA-256 receipt checked, decrypted, and every manifest hash
  verified. This exceeds the complete-under-2-GiB default requirement.
- restore_tested: weekly, restore into a newly created UUID-named database on
  the explicitly configured scratch cluster; compare Alembic head and exact
  counts of booking_requests, catalog, users, operators and appointment_events;
  all file hashes are checked. No live database writes. Only this invocation's
  successful scratch DB is dropped. A failed scratch DB is retained.

Without a scratch destination, uploaded/integrity can succeed but restore_tested
stays false and Sistema remains overdue. Provision a scratch role with
CREATEDB on an isolated test cluster. The restore-test creates one database
from template0; it does not clear a pre-existing database.

python3 manage.py backup verify repeats complete integrity validation.
Add --restore-test to exercise the scratch restore immediately.
Sistema has ONE backup row with latest upload time/size/destination and the
three evidence states. Stale upload (>36h), missing/stale restore proof (>10d),
partial captures and plaintext B2 activity remain visible. No new alert/email.

Retention is a dry-run plan in backup_runs evidence by default. Applied
retention requires a newer COMPLETE restore_tested archive and always keeps
at least the last three tested archives, plus configured daily/weekly/monthly
buckets. Before each deletion, download and authenticate the exact journaled
archive, producer id, UUID filename and manifest MAC. Do not prune manually
from filenames alone. Only journaled multipart upload IDs under this job's
prefix, older than two days, can be aborted; cleanup is separate from archive
retention. Ambiguous initiations not journaled by ID remain protected.

RPO/RTO have NOT been measured. On the municipal host measure dataset/dump
size, capture duration, total upload/download time and complete restore time
on a fresh host, including code rebuild and operator login.

## Recover on an empty municipality server

Inputs: one .hrb archive, the operator's saved key text, and the exact manifest
release commit (or its retained immutable image). No original DB/server or
developer credential vault is needed.

1. Install Git, Python and matching PostgreSQL client/server packages.
   git clone <repository-url> human-rounds
   then cd human-rounds, git checkout --detach <RECORDED_COMMIT>,
   python3 -m venv .venv, and .venv/bin/pip install -r requirements.txt.
   Keep institutional read access to that repository or an independently
   retained mirror/image; do not depend on a developer's personal vault.
   The recorded revision must contain this recovery reader. For an older
   release, run the versioned reader separately, then install the recorded app.
   Verify requirements.txt SHA-256 against the restored manifest before boot.
2. Keep the app and all workers stopped. Put the archive and key on private
   storage. Create /srv/hr-recovery with owner-only access.
3. Run .venv/bin/python manage.py backup restore /srv/input/archive.hrb
   --key-file /srv/input/human-rounds-recovery-key.txt
   --into /srv/hr-recovery/recovered (one command). The destination must NOT
   exist. The CLI prints status/reference omissions; partial is not complete.
4. Read recovered/manifest.json. Verify its commit/image, requirements hash,
   Alembic head, skipped entries, reference_check and file-copy timestamps.
   Rebuild a container with docker build --label org.opencontainers.image.revision=<RECORDED_COMMIT>
   -t human-rounds:recovery . if using containers; alternatively retain/use the
   manifest's immutable image digest. Never substitute mutable latest.
5. Provision a NEW empty target database. Store its connection URL in a
   separate owner-only file. Repeat the restore command with a NEW --into
   path and --database-url-file /srv/input/target-db-url.txt.
   --database-url is also supported, but the file avoids exposing credentials
   in shell history/process listings. The tool refuses nonempty databases and
   the configured live DB; it never uses --clean or implicitly restores a DB.
6. Copy recovered/data to the NEW installation volume, recovered/private-packs
   to its configured private root, and config/installation.env to the new
   TURNOS_ENV_FILE. Use manifest.sources for nondefault/legacy source mappings.
   Do not overwrite another installation. Update DATABASE_URL and filesystem
   paths for this host. Inject the saved KEK using the installation secret source;
   it was deliberately excluded from the recovered env. Review all other
   recovered credentials before enabling external connectors.
7. Erased people come back with an older backup: before anything is served, with the app and the workers
   still stopped, run `python3 scripts/reapply_erasures.py --from-file /secure/erasures.json` (the count; add
   `--apply` to write; run it again to see nothing is left; exit code 2 = someone is blocked by a live
   appointment or a pending HSI step, resolve it or add `--despite-blockers`). The file is written on the live
   installation by `python3 scripts/reapply_erasures.py --export /secure/erasures.json` after each erasure and
   kept next to the recovery key, outside the database: a backup older than an erasure holds neither the
   erasure nor its ledger line, and it only matches under the same `TURNOS_ACCESS_LOG_KEY`. After the recovery
   write a new file with `--from-file <old> --export <new>`. Counts only are printed.
8. Boot the exact release with outbound traffic disabled for the rehearsal.
   Verify health/readiness, release identity, Alembic revision, counts,
   file hashes, a fictional restored appointment/catalog edit and audit history.
   Follow backup-acceptance.md; do not stamp Alembic metadata to force a match.

Local file-only recovery needs no cloud connection and works with only the
archive/key. An explicit database restore also requires the empty target.

## Migrate plaintext B2

Existing B2 code and receipts are untouched. New wizard installations set
TURNOS_BACKUP_OFF=1, disabling that legacy producer. Configured existing
B2 installs keep their current behavior and show "copia sin cifrar activa"
in the single backup row while it is active.

Configure R2, save the recovery key, run a full backup, verify it, and complete
a clean-host restore rehearsal BEFORE disabling B2 with TURNOS_BACKUP_OFF=1.
Retain independently verified encrypted copies. Historical B2 copies remain
plaintext and may contain raw canonical credentials: restrict access, inventory
versions/retention obligations, rotate affected credentials under operator
control, and approve a separate exact-version deletion plan. This job never
deletes historical B2 copies or claims to encrypt them in place.
