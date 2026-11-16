# Encrypted backup acceptance

Status: **NOT RUN**. Unit/fake-protocol results do not prove Cloudflare R2,
a municipal-host restore, RPO/RTO, or production readiness.

## Isolated R2 test — NOT RUN

Use only a new test bucket/prefix, a synthetic PostgreSQL installation,
generated file bytes, synthetic env/private pack and dedicated scoped
credentials. Never point this at a live deployment's database/data/.
Retain IDs, timing, source commit/image and a redacted operator transcript.

1. Provision the isolated source through /setup, including the backup step.
   Prove the one-time key is downloadable, not in cookie/DB/logs, and that
   later Settings reads show only the matching fingerprint.
2. Test connection; verify the probe object is removed. Run a manual backup.
   HEAD its exact object; independently download and compare encrypted hash.
3. Run backup verify and backup verify --restore-test against an explicitly
   separate scratch PostgreSQL cluster. Verify row counts/head, private
   doctor-photo originals, referral paths and all manifest hashes.
4. Repeat with generated content >100 MiB to force multipart. Interrupt
   after a journaled upload ID, restart, and verify abort/stale cleanup only
   touches that exact owned ID. Exercise a >2-GiB complete integrity check.
5. Exercise LIST pagination, 503/timeout retries and denied credentials.
   Prove TURNOS_OUTBOUND_KILL=1 blocks transport before writes/requests.
6. Produce enough synthetic archives to cross retention buckets. Dry-run
   deletes nothing. Applied mode needs a newer tested archive, protects
   three tested copies, verifies candidate ownership, and preserves an
   unrelated same-prefix object and an unjournaled multipart upload.
7. Rotate the KEK. Recover an older archive with its old saved key and a new
   archive with the new key. Verify rewrapping leaves ciphertext unchanged.
8. Record actual duration/size, provider response metadata and costs; remove
   only exact synthetic resources after independent review.

## Empty-host recovery — NOT RUN

An operator other than the implementation author performs this on an empty
isolated host. Give them ONLY archive.hrb, the saved key text, and the retained
release commit/image. Withhold the original host, DB, env and secret service.
Retain source resources; simulated loss does not mean deleting them.

Follow backup-and-restore.md's exact rebuild/restore sequence. Before source
capture seed a fictional booking + audit event, catalog overlay, patient and
staff accounts, generated referral/photo originals and private town pack.
Record baseline identifiers/values/hashes privately. Freeze fixture edits
while checking the baseline; the producer itself must still take its exported
snapshot before files. Exercise one missing file and one changing file to
prove partial status and visible omissions.

Require: checksum-validated saved key, matching release/requirements/Alembic
head, exact baseline records and nonempty history, all file hashes, no implicit
live DB restore, no overwrite, /health and /ready 200, served build/assets from
the retained release, synthetic staff/patient journeys and a new persisted edit.
Prove external connectors remain isolated throughout the rehearsal.

Reject wrong keys, modified headers/chunks, missing final/trailing bytes,
traversal/symlink tar entries, inconsistent manifest hashes and existing
destination/target DB objects. Preserve failed target/logs instead of clearing
or stamping them. A partial backup cannot pass completeness acceptance.

Measure on the municipal host: dataset size; pg_dump/copy/encryption time;
upload/download bandwidth and duration; complete decrypt/hash/pg_restore time;
code rebuild/boot/login duration; achieved recovery point. RPO and RTO remain
unmeasured until these actual results exist.

## Browser profiles to run later

- e2e-settings-asgi: Settings destination controls, masking, lock, generation,
  copy/download, rotation and phone fit at 360/390/768/834/1024/1280/1440.
- e2e-system-asgi: single backup row, three independent evidence states,
  unconfigured/stale/partial/plaintext warnings and Settings deep link.
- e2e-asgi: the complete suite. There is currently no dedicated setup-wizard
  browser profile; add the clean-install /setup/backups journey to this gate
  before claiming browser acceptance. Python setup router tests are not that proof.

No browser e2e profile was run for this slice.
