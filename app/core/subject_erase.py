"""core/subject_erase.py — erase or anonymise one person's data (Ley 25.326 art. 16, P4).

DRY-RUN BY DEFAULT: `plan()` only counts. `apply()` is the one function that
writes, and it refuses to run without a ledger request (core/subject_requests)
of kind `erasure` for THIS person whose identity was verified, and without a
named authorisation (`authorised_by`: who in the hospital — or which counsel's
answer — allows it). What is kept and what goes comes from
`core/personal_data.REGISTRY`; the statute does not oblige us to erase what a
legal duty obliges us to keep (art. 16 inc. 5, Ley 26.529 art. 18), and which
rows those are is a question for the hospital's counsel (Q1/Q2) — the entries
marked `disputed` take the reversible side of it.

Two modes:

* `anonymise` (the recommendation): the derived and non-clinical data goes
  (class A: sessions, tokens, chats, caches, waiting list, HSI mirrors, lab
  bytes, referral files, the profile photo); the rows that may be the care
  record stay WITHOUT the person — their DNI-keyed identity first moves to a
  random key (`db_accounts.change_dni_in`, the same mechanism as a DNI correction, so
  nobody who registers with that DNI later inherits it), then the personal
  fields are emptied. Order is children first, as in the registry.
* `block`: the same class A, and the account is deactivated and its tokens
  revoked, but the care records stay as they are under the person's own DNI
  (restricted use, not anonymised).

The clinical access log is never touched (append-only, kept 10 years). One
transaction for the database; the files go after it commits (a failed file is
reported, never silently skipped); the ledger gets an `erased` event with the
counts — that event is also what has to be applied again to a database restored
from an older backup. Refused (nothing written) while the person has live
appointments, an HSI intent still pending, or an unresolved sync conflict.
"""
import json
import os
import secrets
import sys

import sqlalchemy as sa

from app.core import db, personal_data, sqlcore, subject_export, subject_requests
from app.core import db_accounts
from app.core import db_backup
from app.core import db_values
from app.core import db_locks
from app.core.personal_data import ANONYMISE, DELETE, KEEP
from app.core.schema_model import booking_requests, hsi_outbox, hsi_sync_conflicts, metadata

MODES = ("anonymise", "block")
ANON_PREFIX = "ANON-"
REVOKE = "revoke"  # block mode: the entry's secrets and `active` only


class ErasureRefused(Exception):
    """Nothing was written; `reasons` says why."""

    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = list(reasons)


def _col(blank_item):
    return blank_item if isinstance(blank_item, str) else blank_item[0]


def _revocations(entry):
    return tuple(b for b in entry.blank if _col(b) in entry.secret or _col(b) == "active")


def effective_action(entry, mode):
    """What this entry gets in this mode."""
    if mode == "anonymise":
        return entry.erase
    if entry.erase == DELETE and not entry.disputed:
        return DELETE
    if entry.erase == ANONYMISE and _revocations(entry):
        return REVOKE
    return KEEP


def _table(entry):
    return metadata.tables[entry.table]


def _where(entry, subject):
    clause = personal_data.where_clause(entry, subject)
    return sa.and_(clause, sa.text(entry.only_where)) if entry.only_where else clause


def _set_values(items):
    """The `SET` of a step: an item is a column (emptied to NULL) or `(column, <sql expression>)`."""
    return {(item if isinstance(item, str) else item[0]): sa.null() if isinstance(item, str) else sa.text(item[1])
            for item in items}


def _count(cur, entry, subject, extra=None):
    where = _where(entry, subject)
    if extra is not None:
        where = sa.and_(where, extra)
    return sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(_table(entry))
                       .where(where)).fetchone()["n"]


def _count_of(cur, table, *conditions):
    return sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(table)
                       .where(*conditions)).fetchone()["n"]


def _blockers(cur, subject):
    reasons = []
    b = booking_requests.c
    if _count_of(cur, booking_requests, b.dni == subject.dni,
                 b.status.in_(("pendiente", "aprobada")),
                 sa.or_(b.appointment_at > sa.func.now(),
                        sa.and_(b.status == sa.literal_column("'pendiente'"), b.appointment_at.is_(None)))):
        reasons.append("the person has live appointments: cancel them first, with the patient's "
                       "agreement (Pacientes → archivar offers it)")
    # The HSI resync daemon re-sends a future approved / cancelled appointment whose push failed
    # (`booking_requests_hsi_pending`): with the name gone it would push an empty identity.
    if _count_of(cur, booking_requests, b.dni == subject.dni,
                 b.status.in_(("aprobada", "cancelada")), b.appointment_at >= sa.func.now(),
                 b.hsi_status.in_(("error", "pendiente"))):
        reasons.append("an appointment of this person still has to be sent (or cancelled) in HSI: "
                       "let the HSI resync finish first")
    outbox = personal_data.entries_of("hsi_outbox")[0]
    if _count_of(cur, hsi_outbox, personal_data.where_clause(outbox, subject),
                 hsi_outbox.c.state != sa.literal_column("'confirmed'")):
        reasons.append("an HSI intent for this person is still pending, sending, failed or in "
                       "conflict: let the sync finish or resolve it first")
    conflicts = personal_data.entries_of("hsi_sync_conflicts")[0]
    if _count_of(cur, hsi_sync_conflicts, personal_data.where_clause(conflicts, subject),
                 hsi_sync_conflicts.c.resolved_at.is_(None)):
        reasons.append("an HSI sync conflict about this person is unresolved: resolve it first")
    return reasons


blockers = _blockers


def _referral_paths(cur, subject):
    b = booking_requests.c
    return [r["referral_path"] for r in sqlcore.run(cur, sa.select(b.referral_path).where(
        b.dni == subject.dni, b.referral_path.is_not(None))).fetchall()]


def _photo_paths(dni):
    from app.cuentas import account_photo
    return account_photo.photo_paths(dni)


def plan(subject, mode="anonymise"):
    """What `apply` would do, as counts. Writes nothing (a read-only transaction)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    steps = []
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.text("SET LOCAL transaction_read_only = on"))
        for entry in personal_data.REGISTRY:
            action = effective_action(entry, mode)
            steps.append({"entry": entry.label, "action": action, "disputed": entry.disputed,
                          "rows": _count(cur, entry, subject), "why": entry.reason})
        blockers = _blockers(cur, subject)
        referrals = _referral_paths(cur, subject) if mode == "anonymise" else []
    photos = _photo_paths(subject.dni)
    return {"mode": mode, "steps": steps, "blockers": blockers,
            "files": {"referrals": len(referrals), "profile_photo": len(photos)},
            "notes": list(subject.notes), "not_erased": list(subject_export.NOT_REACHABLE)}


def _run_step(cur, entry, action, subject):
    where = _where(entry, subject)
    table = _table(entry)
    if action == DELETE:
        stmt = sa.delete(table).where(where)
    elif action == ANONYMISE:
        if not entry.blank:
            return 0  # nothing to empty: the key move is what anonymises this one
        stmt = sa.update(table).where(where).values(_set_values(entry.blank))
    elif action == REVOKE:
        stmt = sa.update(table).where(where).values(_set_values(_revocations(entry)))
    else:
        raise ValueError(action)
    return sqlcore.run(cur, stmt).rowcount


def _blanked(items):
    """The condition that is true for a row whose `items` have all been emptied the way
    `_set_values` empties them (a column set to NULL, or equal to the expression it was set to)."""
    parts = []
    for item in items:
        col, expr = (item, sa.null()) if isinstance(item, str) else (item[0], sa.text(item[1]))
        parts.append(sa.column(col).is_not_distinct_from(expr))
    return sa.and_(*parts)


def _unblanked(cur, subject, mode):
    """Entries that still hold a personal value after their `blank` step. Runs BEFORE the
    key move (afterwards the DNI no longer finds the rows). An empty list is the proof that
    no `blank` silently missed."""
    left = []
    for entry in personal_data.REGISTRY:
        action = effective_action(entry, mode)
        items = {ANONYMISE: entry.blank, REVOKE: _revocations(entry)}.get(action)
        if items and _count(cur, entry, subject, sa.not_(_blanked(items))):
            left.append(entry.label)
    return left


def _leftovers(cur, subject, mode):
    """Entries that still find the person after the steps and the move, except the ones
    kept on purpose (the access log, the audit rows, `block`'s records)."""
    left = []
    for entry in personal_data.REGISTRY:
        action = effective_action(entry, mode)
        if action in (DELETE, ANONYMISE) and _count(cur, entry, subject):
            left.append(entry.label)
    return left


def pending(cur, subject, mode):
    """{entry label: rows} the steps of `mode` would still change for this person: the rows
    of a `delete` entry that are still there, of an `anonymise` entry that still answer to the
    person's keys, of a `revoke` entry that still hold a secret or an active flag. Empty = the
    erasure is already in place (what re-applying it after a restore asks first)."""
    left = {}
    for entry in personal_data.REGISTRY:
        action = effective_action(entry, mode)
        if action in (DELETE, ANONYMISE):
            rows = _count(cur, entry, subject)
        elif action == REVOKE:
            rows = _count(cur, entry, subject, sa.not_(_blanked(_revocations(entry))))
        else:
            continue
        if rows:
            left[entry.label] = rows
    return left


def carry_out(cur, subject, mode):
    """The write steps of an erasure inside the caller's transaction: class A, the blank step,
    the key move, then the two proofs. Returns `(executed, referral_paths)`; raises
    `ErasureRefused` (the caller rolls back). Neither the ledger nor the authorisation is
    checked here: `apply` checks them, `core/erasure_reapply.py` answers to the ledger itself."""
    referrals = _referral_paths(cur, subject) if mode == "anonymise" else []
    executed = []
    for entry in personal_data.REGISTRY:
        action = effective_action(entry, mode)
        if action == KEEP:
            continue
        executed.append({"entry": entry.label, "action": action,
                         "rows": _run_step(cur, entry, action, subject)})
    unblanked = _unblanked(cur, subject, mode)
    if unblanked:
        raise ErasureRefused([f"personal fields left after the blank step: {', '.join(unblanked)}"])
    if mode == "anonymise":
        if not db_accounts.change_dni_in(cur, subject.dni, ANON_PREFIX + secrets.token_hex(8)):
            raise ErasureRefused(["the identity could not be moved to a random key"])
    left = _leftovers(cur, subject, mode)
    if left:
        raise ErasureRefused([f"still reachable after the steps: {', '.join(left)}"])
    return executed, referrals


def apply(subject, mode, request_id, authorised_by, operator):
    """Carry the erasure out. Returns the report; raises `ErasureRefused` (nothing written)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not str(authorised_by or "").strip():
        raise ErasureRefused(["no authorisation named: who allows this erasure, or which "
                              "counsel's answer does?"])
    with db._connect() as conn, conn.cursor() as cur:
        db_locks._advisory_xact_lock(cur, "subject_erase:", db_values._text_value(subject.dni))
        try:
            subject_requests.check_erasure_request(cur, request_id, subject.dni_hash)
        except subject_requests.RequestError as exc:
            raise ErasureRefused([str(exc)]) from None
        reasons = _blockers(cur, subject)
        if reasons:
            raise ErasureRefused(reasons)
        executed, referrals = carry_out(cur, subject, mode)
        summary = {"mode": mode, "authorised_by": authorised_by,
                   "rows": {s["entry"]: s["rows"] for s in executed if s["rows"]}}
        subject_requests.append_event(cur, request_id, operator, "erased",
                                      json.dumps(summary, sort_keys=True))
    files = remove_files(subject.dni, referrals)
    detail = (f"files: {files['removed']} removed, {len(files['failed'])} failed"
              + (f" ({', '.join(files['failed'])})" if files["failed"] else ""))
    try:
        subject_requests.note(request_id, detail, operator)
    except Exception:  # noqa: BLE001 — the erasure is committed; its report must still come back
        files["ledger_note_failed"] = True
    return {"mode": mode, "steps": executed, "files": files}


def _journal_removal(path, kind):
    """Writes a REFERRAL file's data-relative path in `purged_files` BEFORE it
    is removed (schema review №11): the next backup sweep then deletes its B2
    copies and a restore refuses it. Best-effort — the erasure itself never
    waits on the journal. A profile photo is NOT journaled: its path is
    `cuenta-fotos/<dni>.<ext>`, so the journal would keep the very DNI the
    erasure removes; its B2 copies stay a manual cleanup."""
    if kind != "referral":
        return
    try:
        from app.modules.turnos import booking_referrals
        db_backup.purged_file_record(booking_referrals._referral_data_path(path), "erasure")
    except Exception as e:  # noqa: BLE001
        print(f"[subject_erase] could not journal a removed file: {type(e).__name__}",
              file=sys.stderr, flush=True)


def remove_files(dni, referral_paths):
    removed, failed = 0, []
    for path, kind in ([(p, "referral") for p in referral_paths]
                       + [(p, "photo") for p in _photo_paths(dni)]):
        _journal_removal(path, kind)
        try:
            os.remove(path)
            removed += 1
        except FileNotFoundError:
            pass  # already gone (the retention sweep)
        except OSError:
            failed.append(os.path.basename(path))
    return {"removed": removed, "failed": failed}
