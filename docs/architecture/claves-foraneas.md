# Foreign keys (Alembic `0032_foreign_keys`)

Until 2026-10-25 no application table had a foreign key: the links between a request and its history, an
interview and its consultations, an account and its sessions lived only in code. The 18 links whose parent is a
real row of the database now have one. The live map is `core.db_accounts.FOREIGN_KEYS` (a test compares it with the
schema), the DDL is the revision, the schema picture is `alembic/schema-head.txt`.

## The rule

**The database refuses to delete a parent that still has children** (`ON DELETE RESTRICT` on every key) — the
same rule as «se deshabilitan, no se eliminan»: a request with appointment history, attendance, holds, a waiting
list entry, an interview or a consultation stays; an account with sessions, tokens, dependents or uploads stays; a
role held by an operator stays. Production code never deletes those parents (it disables or archives them); the
rule is what stops a mistake, a script or a test from doing it quietly.

Erasing a person (Ley 25.326, art. 16) therefore goes **from the
children to the parents** — or anonymises by `UPDATE` instead of `DELETE`.

`ON UPDATE CASCADE` only on the links to `users(dni)` (sessions, reset tokens, the adult of a dependent link,
who uploaded a lab file) and `operators(username)` (the access row): a DNI correction or an operator rename is ONE
update of the parent row and the database moves the children; `change_dni` and `operator_rename` keep their
explicit updates, which then touch nothing (the account / operator row is updated FIRST — keep it that way).

## What is NOT a foreign key, and why

- The DNI of a clinical identity with no account (walk-in requests, interviews, history, labs, Alepo/Marqui
  imports): there is no parent row. `DNI_MOVED_COLUMNS` + `test_change_dni_closure` + the code checks hold them.
- Catalog ids (doctors, services, centers) live in JSON packs and the overlay, not in a table.
- Staff names written as text in logs (`resolved_by`, `actor`, …): an operator rename updates them in code.
- Backup tombstones (`backup_version_tombstones.file_id`): the B2 sweep records the deletion of a remote version
  it may never have received into the receipts ledger (`backup_versions`) — a tombstone has no parent by design.
- `dni_claims` (Alembic 0045): `claimant_key` (an account) and `dni_claimed` (a number that may have no
  account yet, or one the desk is about to move): the claim is a record for the desk, not a working link, and a
  key from `dni_claims` must survive the key move that resolves it. `DNI_MOVED_COLUMNS` / `DNI_KEPT_COLUMNS` hold it.
- HSI mirrors, the access log (it keeps an HMAC, never the DNI) and `interviews.detached_request_id` (a trace of
  an unbinding, not a working link).

## Adding a link

A new foreign key is a new Alembic revision (`ADD CONSTRAINT … NOT VALID`, then `VALIDATE`, see below) **and** an
entry in `core.db_accounts.FOREIGN_KEYS`; `test_foreign_keys` fails when either is missing. Index the referencing column.
Write cleanup code and tests children first: `tests/_dbtest.py`/`_routes_fixture.truncate_all` use
`TRUNCATE … CASCADE`; per-row deletes of a parent must clear its children before.

## How the revision stays safe at app start

The revision runs at boot, and a failed revision means «database unavailable», so it is built not to fail on data:

1. each constraint is added `NOT VALID` — nothing is scanned, old rows are not judged, new rows are checked at once;
2. `VALIDATE CONSTRAINT` then runs outside the migration transaction, one constraint at a time; a
   `foreign_key_violation` is logged with the orphan **count** (never ids or DNIs), the constraint stays `NOT
   VALID` and the site starts;
3. `python3 scripts/fixes/validate_foreign_keys.py` lists every key as valid / not valid / missing with the orphan
   count, and with `--validate` retries once the data is fixed. It changes no data.

An orphan is never removed by a script: restore the parent, set an optional link to NULL, move the row to a
quarantine table with a verified copy, or leave the key `NOT VALID` (new rows stay protected meanwhile) — a decision
each time. A lock timeout on `ADD CONSTRAINT` (15 s) stops the boot with a clear error and the next start continues
(the revision is idempotent).

A doctor account whose doctor is no longer in the catalog (`operators.doctor_id` points at nothing) is not a
foreign key — the catalog is JSON packs plus an overlay. `scripts/fixes/disable_orphan_doctor_accounts.py` lists and,
with `--apply`, switches off the ENABLED ones (disabled ones stay as the record; nothing is deleted).

If a key gets in the way: a new revision `ALTER TABLE … DROP CONSTRAINT <name>` (the data is untouched). Never
revert the commit that carries a migration whole — the revision file stays (`docs/UPGRADING.md`, Rollback).
