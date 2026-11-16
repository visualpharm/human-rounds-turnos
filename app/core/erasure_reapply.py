"""core/erasure_reapply.py — apply the erasures again to a restored database (Ley 25.326 art. 16, P5).

An erased person survives in every backup older than the erasure. After ANY restore from a
backup (docs/operations/backup-and-restore.md) the erasures that were carried out since have to be
carried out again, before the application serves a request. The list comes from the erasure
tombstones: the ledger of data-subject requests (`subject_requests.erasures()`: the `erased`
events — the person as an HMAC of the DNI, the mode, the instant) of the restored database itself
and, because a backup older than the erasure does not hold that event, from a file exported from
the live ledger BEFORE the restore (`export_file`). Both sources carry hashes, never a DNI.

* The DNIs found in the restored database (every DNI column of the registry) are hashed and
  compared with the tombstones: that is how a hash finds its person again.
* DRY-RUN BY DEFAULT: `run(entries)` counts what would change inside a read-only transaction.
  `run(entries, apply=True)` writes: one transaction per person, the same steps and proofs as the
  erasure itself (`subject_erase.carry_out`), the files after the commit.
* Idempotent: a person with nothing left to change is counted as `already_applied`; `anonymise`
  moves the DNI to a random key, so a second run does not find the person at all.
* A DNI can be erased, registered again and erased again: those are different people. The erasure
  that applies to an account is the first one AFTER the account was created; an account created
  after every erasure of its DNI is a new person, left alone (`skipped_registered_after`). With no
  account row (a person known only from bookings) the latest erasure decides.
* The erasure itself refuses to run while the person has live appointments, an HSI intent still
  pending or an unresolved sync conflict, and its proofs do not look at those HSI rows. The restored
  database is older than the decision, so such a person is NOT touched by default and the report
  counts `blocked` (exit code 2 on --apply): the operator resolves what the restore brought back,
  or runs again with `despite_blockers` knowing that those HSI rows keep their payload.
* A tombstone made with another access-log key cannot match anybody: a file written under a
  different key is refused. A tombstone with an unreadable mode or instant is counted, never guessed.
* The report holds counts per registry entry, never a DNI, a name or a hash.
* Files are removed after the commit: a file that fails is counted (`files_failed`) and a second run
  does not retry it (the person is no longer found), so the operator removes it by hand.
"""
import json
import os
import re
from datetime import datetime, timezone

import sqlalchemy as sa

from app.core import access_log, db, db_locks, db_values, personal_data, sqlcore, subject_erase
from app.core import subject_export, subject_requests
from app.core.schema_model import metadata, users

FORMAT = 1
_DNI_VALUE = re.compile(r"(\d{7,9})$")
_DNI_KEY = re.compile(r"dni:(\d{7,9})(?::|$)")
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def key_id():
    """A fingerprint of the access-log key, written into the tombstone file. It is the hash of a
    constant, so it names the key without revealing anything about a person."""
    return access_log.dni_hash("00000000")[:16]


def merge(*sources):
    """The distinct tombstones of the sources (a person erased twice has two), oldest first."""
    seen = {}
    for entries in sources:
        for entry in entries:
            if entry.get("subject_hash"):
                seen.setdefault((entry["subject_hash"], str(entry.get("erased_at"))), entry)
    return sorted(seen.values(), key=lambda e: (e["subject_hash"], _when(e) or _EPOCH))


def export_file(path, extra=()):
    """Write the tombstones of the live ledger (and of `extra`, an earlier file: after a recovery
    the ledger no longer holds them) to `path`, owner-only, never overwritten. Returns how many.
    Keep the file with the backup key, OUTSIDE the database."""
    entries = merge(subject_requests.erasures(), extra)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"format": FORMAT, "key_id": key_id(), "erasures": entries}, f, indent=1, sort_keys=True)
        f.write("\n")
    return len(entries)


def read_file(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("erasures"), list):
        raise ValueError("not a tombstone file of this version")
    if data.get("key_id") != key_id():
        raise ValueError("written under another access-log key: its hashes cannot match here")
    return data["erasures"]


def _when(entry):
    try:
        moment = datetime.fromisoformat(str(entry.get("erased_at")))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _readable(entry):
    return entry.get("mode") in subject_erase.MODES and _when(entry) is not None


def _applies(entries, created):
    """The tombstone that applies to the account created at `created`: the first erasure after it
    (None when the account is newer than all of them); no account row = the latest one."""
    entries = sorted(entries, key=_when)
    if created is None:
        return entries[-1]
    return next((e for e in entries if _when(e) > created), None)


def _candidates(cur):
    """{dni_hash: dni} of every DNI-shaped value in a DNI column of the registry, and of every
    `dni:<dni>` chat key. A key already moved to `ANON-…` is not DNI-shaped, so an anonymised person
    is not found again."""
    found = {}
    for entry in personal_data.REGISTRY:
        for sel in entry.find:
            if sel[0] not in ("dni", "key"):
                continue
            column = metadata.tables[entry.table].c[sel[1]]
            pattern = _DNI_VALUE if sel[0] == "dni" else _DNI_KEY
            for row in sqlcore.run(cur, sa.select(column.label("v")).distinct()).fetchall():
                match = pattern.match(row["v"] or "")
                if match:
                    found.setdefault(access_log.dni_hash(match.group(1)), match.group(1))
    return found


def _report():
    return {"ledger_entries": 0, "unreadable_entries": 0, "in_database": 0, "already_applied": 0,
            "skipped_registered_after": 0, "blocked": 0, "with_live_blockers": 0,
            "shared_identifier_left_out": 0, "to_apply": 0, "applied": 0, "refused": 0,
            "files_removed": 0, "files_failed": 0, "ledger_events_written": 0, "rows": {}}


def _add_rows(report, rows):
    for label, n in rows.items():
        report["rows"][label] = report["rows"].get(label, 0) + n


def run(entries, apply=False, operator="reapply_erasures", despite_blockers=False):
    """Apply (or, by default, only count) the erasures of `entries` on the connected database."""
    report = _report()
    wanted = {}
    for entry in merge(entries):
        report["ledger_entries"] += 1
        if _readable(entry):
            wanted.setdefault(entry["subject_hash"], []).append(entry)
        else:
            report["unreadable_entries"] += 1
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.text("SET LOCAL transaction_read_only = on"))
        found = {h: d for h, d in _candidates(cur).items() if h in wanted}
    report["in_database"] = len(found)
    for dni_hash, dni in sorted(found.items()):
        _one(report, wanted[dni_hash], dni, apply, operator, despite_blockers)
    return report


def _one(report, entries, dni, apply, operator, despite_blockers):
    subject = subject_export.resolve_subject(dni)
    if subject.notes:
        report["shared_identifier_left_out"] += 1
    try:
        with db._connect() as conn, conn.cursor() as cur:
            if not apply:
                sqlcore.run(cur, sa.text("SET LOCAL transaction_read_only = on"))
            else:
                db_locks._advisory_xact_lock(cur, "subject_erase:", db_values._text_value(subject.dni))
            row = sqlcore.run(cur, sa.select(users.c.created).where(users.c.dni == subject.dni)).fetchone()
            entry = _applies(entries, row["created"] if row else None)
            if entry is None:
                report["skipped_registered_after"] += 1
                return
            mode = entry["mode"]
            left = subject_erase.pending(cur, subject, mode)
            if not left:
                report["already_applied"] += 1
                return
            if subject_erase.blockers(cur, subject):
                report["with_live_blockers"] += 1
                if not despite_blockers:
                    report["blocked"] += 1
                    return
            if not apply:
                report["to_apply"] += 1
                _add_rows(report, left)
                return
            executed, referrals = subject_erase.carry_out(cur, subject, mode)
            rows = {s["entry"]: s["rows"] for s in executed if s["rows"]}
            if subject_requests.note_reapplied(cur, entry.get("request_id"), subject.dni_hash, operator,
                                               json.dumps({"mode": mode, "rows": rows}, sort_keys=True)):
                report["ledger_events_written"] += 1
    except subject_erase.ErasureRefused:
        report["refused"] += 1
        return
    report["applied"] += 1
    _add_rows(report, rows)
    files = subject_erase.remove_files(subject.dni, referrals)
    report["files_removed"] += files["removed"]
    report["files_failed"] += len(files["failed"])
