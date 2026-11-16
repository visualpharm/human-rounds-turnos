# Synthetic restore rehearsal

For the installation-independent encrypted producer, use
[encrypted backup acceptance](backup-acceptance.md) and the
[operator runbook](backup-and-restore.md). Those R2 and clean-host rehearsals
are **NOT RUN**. The historical manual/B2 procedure below remains separate.

Status: **NOT RUN — procedure under independent verification.** This plan
does not close audit row 8.3. An operator other than the backup implementation
author must execute it, retain actual results, and obtain independent review.
Use only fictional records and generated file bytes. Never substitute a live
installation, real credentials or patient data.

## Prepare two isolated installations

Record the exact source commit and image digest. Allocate distinct, uniquely
named source and recovery projects, volumes and registered test ports. Check
actual ownership and record resource IDs; do not reuse an existing project.
Build explicitly from the recorded revision.

Use a clean checkout with no local environment file. The repository's
docker-compose.yml optionally imports .env: a normal development checkout
is not a safe rehearsal source. The runner must supply an allowlisted
environment and explicit Compose configuration with synthetic database
credentials, separate data volumes and loopback-only HTTP exposure. Review
the resolved configuration before boot. Block provider traffic and prove
that the database remains reachable under the chosen isolation boundary;
missing provider keys alone do not prove isolation.

Initialize the fictional source through /setup with a bundled example pack,
a synthetic staff account and connectors disabled. Keep generated login
credentials in the approved credential store, not the evidence report.
Keep the recovery app stopped until restore and integrity checks finish.

## Seed and freeze a non-empty baseline

Create a catalog edit, fictional booking and its audit/history event through
their owning APIs or existing fixture helpers. Create generated upload bytes
and record their relative path, byte count and SHA-256. Add non-empty backup
journal, receipt and tombstone fixtures through the fake-provider seam in
test/test_data_backups_restore.py, never a real remote bucket.

Record exact fixture values, schema revision, constraints and file hashes.
Counts alone do not prove integrity. An empty audit table cannot prove audit
trail restoration.

Stop the source app and every writer (its supervised worker child stops with it).
Verify their exit and absence of remaining migration/job writers before
capturing the database and volume. Leave the database available to its dump
tool. Concurrent database and file captures while writes continue do not
establish one consistent recovery point.

## Capture and retain the source

For the repository's synthetic Compose database, invoke pg_dump through
docker compose --project-name <recorded-source> exec -T db, using its
documented synthetic user/database sana/sana_turnos. Capture exit status and
stderr separately. Write one exact new dump path; never select artifacts
with a glob or pass credential-bearing URLs in process arguments.

Snapshot the source data volume read-only, with archive entries relative to
the volume root. Reject absolute paths, parent traversal and escaping links.
Record dump/archive SHA-256, exact artifact paths, source revision and capture
time in one manifest. Keep the bundle private: installation data volumes can
contain sensitive canonical configuration.

Retain the source database and volumes. Simulated loss means withholding
them from recovery, not deleting them. No volume deletion, database drop,
migration-metadata reset or retention deletion belongs in this rehearsal.

## Restore into the second target

Start only the recovery database. Verify its resource IDs differ from the
source, it has zero application tables, and its data volume is empty.
Unexpected existing state is an abort, not permission to clear it.

Verify both backup hashes against the manifest. Restore the exact SQL dump
with psql -v ON_ERROR_STOP=1 via non-TTY input (exec -T) into the recovery
database. Any nonzero exit fails the restore. Keep the partially restored
target and logs; do not boot the app.

Extract the reviewed archive only into the verified-empty recovery volume,
never a host root. Compare exact fixture rows, constraints, schema revision
and file hashes before boot. Do not change alembic_version to force a match.
A missing audit record is failure even if subsequent readiness would pass.

## Verify the recovered application

Boot the recovery app from the recorded source image and synthetic settings,
with outbound isolation still enforced.

| Check | Required evidence |
|---|---|
| Health | Actual /health and /ready HTTP statuses and bodies, both 200 |
| Build | Served SOURCE_COMMIT or equivalent immutable deployment identity matches the source manifest |
| Assets | Non-empty actual served HTML asset inventory; fetch every first-party fingerprinted JS/CSS URL, require 200 and compare response bytes with the source build |
| State | Exact fixture rows and generated file hashes still match after boot |
| Staff journey | Synthetic login, restored catalog edit and booking visible; screenshots retained |
| Mutation | New synthetic edit saves and survives reload without corrupting baseline fixtures |
| Audit | Restored non-empty history and backup records match; new operation records its expected event |
| Isolation | No real provider calls, patient services or credentials used |

An empty asset inventory or empty audit fixture fails the proof. Calling a
local hash resolver alone does not prove served bytes. Check build identity
across responses; dynamic HTML need not have an identical whole-page hash.

## Verify the operator backup boundary separately

Run test/test_data_backups_restore.py through the managed test harness with
a disposable database and fake provider, serially after other database and
browser gates. Require executed tests and retained output, not a skip caused
by missing database configuration.

ops.data_backups_restore exposes reconcile_live,
restore_version(version, destination_root, scratch_root, fetch_fn), and
retention_plan (dry-run by default). It is not a CLI, boot hook or automatic
recovery service. Verify corrupted-byte refusal, exact immutable receipt
checks, disjoint scratch/destination roots, no overwrite and protected
unknown versions. Never add deletion to recovery.

Fake-provider tests do not prove a live bucket is recoverable. A real remote
bucket exercise requires separate installation authorization. A manual dump
also does not validate the deployment platform's scheduled backup artifact.

## Failure, evidence and cleanup

On any mismatch, preserve the source, backup bundle and failed target.
Record the exact failing step and observed result. Diagnose before another
attempt; never stamp a revision, delete data or weaken assertions to pass.

Use a durable unique evidence directory. Record source commit/image, resource
IDs, backup hashes, fixture comparisons, commands and exit statuses, managed
gate output, screenshots, actual start/end times, operator and reviewer.
Production RTO/RPO cannot be inferred from this small synthetic dataset.

Only after verified success and ownership reconciliation may the coordinator
remove exact synthetic resources. Retain evidence. Cleanup is a separate
reviewed operation, never an automatic response to an unexplained failure.

## Results and remaining prerequisites

| Stage | Actual result |
|---|---|
| Isolated topology and outbound boundary | NOT RUN |
| Non-empty baseline and consistent database/volume capture | NOT RUN |
| Empty-target restore and exact integrity comparison | NOT RUN |
| Served build, assets and staff journeys | NOT RUN |
| Managed fake-provider tests | NOT RUN for this rehearsal |
| Independent operator and reviewer acceptance | NOT RUN |

Still to verify: resolved rehearsal configuration, concrete fixture commands,
complete independent execution, platform-produced backups, real-scale timing
and any separately authorized remote recovery. This is a plan, not an
operator-ready or completed recovery proof.

See [Incident response](../policies/INCIDENT_RESPONSE.md) and
[High availability](../policies/HIGH_AVAILABILITY.md).
