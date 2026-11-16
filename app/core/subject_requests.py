"""core/subject_requests.py — the ledger of data-subject requests (Ley 25.326 arts. 14-16, P3).

A person may ask to SEE their data (art. 14: answer within 10 calendar days),
to CORRECT it or to have it ERASED (art. 16: 5 working days). Before this module
nothing recorded that such a request had arrived, when it was due, who checked
the person's identity, or what was answered.

* `register()` opens a request: kind, the day it was received, the due day
  (10 calendar days, or 5 working days — weekends and the installation's
  holidays, `site_holidays`, do not count; day 1 is the next working day), the
  channel it came through. The person is stored as `access_log.dni_hash(dni)`,
  never the DNI.
* `set_identity_check()` records HOW the identity was verified. Nothing is
  released ("answered") before that: a release to the wrong person is itself a
  breach. A request whose identity cannot be verified is answered by REFUSING it
  with that as the stated reason.
* `start_review()` marks a rectification as under review: while a correction is
  verified the data is flagged (art. 16 inc. 6) — `is_under_review(dni)`.
* `answer()` / `refuse()` close it; a refusal must state its legal basis (for an
  erasure that the law obliges us to keep: art. 16 inc. 5).
* `overdue()` lists what is past its due day. `next_free_access_on()` answers the
  six-month rule of art. 14 inc. 3: after an answered access request the person
  may ask again for free six months later (sooner only with a legitimate interest).

Every change appends a line to `data_subject_request_events` (append-only,
database trigger). Whether a request that arrives incomplete stops the clock is
a question for the hospital's counsel (Q9): here the clock never stops.
"""
import json
from datetime import date, datetime, timedelta

import sqlalchemy as sa

from app.core import access_log, db, sqlcore, tz
from app.core.schema_model import data_subject_request_events as _events, data_subject_requests as _requests

KINDS = ("access", "rectification", "erasure")
STATUSES = ("received", "under_review", "answered", "refused")
OPEN = ("received", "under_review")
ACCESS_CALENDAR_DAYS = 10
CORRECTION_WORKING_DAYS = 5
FREE_ACCESS_MONTHS = 6


class RequestError(ValueError):
    """A step that the ledger refuses; `str(exc)` says why."""


def today():
    return datetime.now(tz.tzinfo()).date()


def add_working_days(start, days, holidays=()):
    """The date `days` working days after `start`: Saturdays, Sundays and every
    date in `holidays` (ISO strings or dates) are skipped; `start` itself does not count."""
    skip = {h.isoformat() if isinstance(h, date) else str(h) for h in holidays}
    current = start
    left = days
    while left > 0:
        current += timedelta(days=1)
        if current.weekday() < 5 and current.isoformat() not in skip:
            left -= 1
    return current


def add_months(start, months):
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    last_day = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(start.day, last_day))


def _site_holidays():
    try:
        from app.modules.turnos import site_holidays
        return set(site_holidays.by_date())
    except Exception:  # noqa: BLE001 — no catalog: weekends only, never a failure
        return set()


def due_date(kind, received_on, holidays=None):
    if kind not in KINDS:
        raise RequestError(f"unknown kind {kind!r}")
    if kind == "access":
        return received_on + timedelta(days=ACCESS_CALENDAR_DAYS)
    return add_working_days(received_on, CORRECTION_WORKING_DAYS,
                            _site_holidays() if holidays is None else holidays)


def _event(cur, request_id, actor, event, detail=""):
    sqlcore.run(cur, sa.insert(_events).values(
        request_id=request_id, actor=actor, event=event, detail=detail or ""))


def _get(cur, request_id, lock=False):
    stmt = sa.select(_requests).where(_requests.c.id == request_id)
    row = sqlcore.run(cur, stmt.with_for_update() if lock else stmt).fetchone()
    if row is None:
        raise RequestError(f"no request {request_id}")
    return dict(row)


def register(kind, dni, registered_by, received_on=None, channel="", detail="", holidays=None):
    """Open a request; returns its row. `received_on` is the day it ARRIVED (a
    request taken on paper on Friday and typed in on Monday keeps Friday)."""
    digits = access_log.clean_dni(dni)
    if not digits:
        raise RequestError("not a DNI")
    if not str(registered_by or "").strip():
        raise RequestError("who registers it?")
    received_on = received_on or today()
    due_on = due_date(kind, received_on, holidays)
    with db._connect() as conn, conn.cursor() as cur:
        row = dict(sqlcore.run(cur, sa.insert(_requests).values(
            kind=kind, subject_hash=access_log.dni_hash(digits), received_on=received_on,
            due_on=due_on, channel=channel or "", registered_by=registered_by
        ).returning(*_requests.c)).fetchone())
        _event(cur, row["id"], registered_by, "received",
               f"{kind} via {channel or 'unspecified'}; due {due_on.isoformat()}"
               + (f"; {detail}" if detail else ""))
        return row


def get(request_id):
    with db._connect() as conn, conn.cursor() as cur:
        row = _get(cur, request_id)
        row["events"] = [dict(r) for r in sqlcore.run(cur, sa.select(
            _events.c.ts, _events.c.actor, _events.c.event, _events.c.detail
        ).where(_events.c.request_id == request_id).order_by(_events.c.id)).fetchall()]
        return row


def set_identity_check(request_id, how, actor):
    how = str(how or "").strip()
    if not how:
        raise RequestError("say how the identity was verified")
    with db._connect() as conn, conn.cursor() as cur:
        row = _get(cur, request_id, lock=True)
        _closed_guard(row)
        sqlcore.run(cur, sa.update(_requests).where(_requests.c.id == request_id).values(
            identity_check=how, updated=sa.func.now()))
        _event(cur, request_id, actor, "identity_verified", how)


def start_review(request_id, actor, detail=""):
    """Rectification / erasure: the data is flagged «under review» while it is checked."""
    with db._connect() as conn, conn.cursor() as cur:
        row = _get(cur, request_id, lock=True)
        _closed_guard(row)
        if row["kind"] == "access":
            raise RequestError("an access request has no review step")
        sqlcore.run(cur, sa.update(_requests).where(_requests.c.id == request_id).values(
            status=sa.literal_column("'under_review'"), updated=sa.func.now()))
        _event(cur, request_id, actor, "under_review", detail)


def note(request_id, text, actor):
    with db._connect() as conn, conn.cursor() as cur:
        _get(cur, request_id)
        _event(cur, request_id, actor, "note", str(text or ""))


def _closed_guard(row):
    if row["status"] not in OPEN:
        raise RequestError(f"request {row['id']} is already {row['status']}")


def answer(request_id, outcome, actor, on=None):
    """Close it as answered. Refused without a recorded identity check, and
    without saying what was done."""
    outcome = str(outcome or "").strip()
    if not outcome:
        raise RequestError("say what was answered")
    with db._connect() as conn, conn.cursor() as cur:
        row = _get(cur, request_id, lock=True)
        _closed_guard(row)
        if not row["identity_check"]:
            raise RequestError("the identity has not been verified: record how "
                               "(set_identity_check), or refuse the request saying so")
        _close(cur, request_id, "answered", outcome, actor, on)


def refuse(request_id, legal_basis, actor, on=None):
    """Close it as refused; the basis is mandatory (e.g. Ley 25.326 art. 16 inc. 5,
    or «identity could not be verified»)."""
    basis = str(legal_basis or "").strip()
    if not basis:
        raise RequestError("a refusal must state its legal basis")
    with db._connect() as conn, conn.cursor() as cur:
        row = _get(cur, request_id, lock=True)
        _closed_guard(row)
        _close(cur, request_id, "refused", basis, actor, on)


def _close(cur, request_id, status, outcome, actor, on):
    on = on or today()
    sqlcore.run(cur, sa.update(_requests).where(_requests.c.id == request_id).values(
        status=status, outcome=outcome, decided_by=actor, answered_on=on, updated=sa.func.now()))
    _event(cur, request_id, actor, status, outcome)


def list_open(limit=200):
    with db._connect() as conn, conn.cursor() as cur:
        return [dict(r) for r in sqlcore.run(cur, sa.select(_requests).where(
            _requests.c.status.in_(OPEN)).order_by(_requests.c.due_on, _requests.c.id).limit(limit)
        ).fetchall()]


def overdue(on=None):
    """Open requests past their due day (due today is not yet overdue)."""
    on = on or today()
    return [r for r in list_open(1000) if r["due_on"] < on]


def for_subject(dni):
    with db._connect() as conn, conn.cursor() as cur:
        return [dict(r) for r in sqlcore.run(cur, sa.select(_requests).where(
            _requests.c.subject_hash == access_log.dni_hash(dni)
        ).order_by(_requests.c.received_on.desc(), _requests.c.id.desc())).fetchall()]


def is_under_review(dni):
    """Is a correction of this person's data being verified right now (art. 16 inc. 6)?"""
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(_requests).where(
            _requests.c.subject_hash == access_log.dni_hash(dni),
            _requests.c.status == sa.literal_column("'under_review'"),
            _requests.c.kind.in_(("rectification", "erasure"))
        ).limit(sa.literal_column("1"))).fetchone() is not None


def next_free_access_on(dni):
    """The first day the person may ask for access again for free (art. 14 inc. 3:
    six months after the last answered access request), or None if they never got one."""
    with db._connect() as conn, conn.cursor() as cur:
        last = sqlcore.run(cur, sa.select(sa.func.max(_requests.c.answered_on).label("last")).where(
            _requests.c.subject_hash == access_log.dni_hash(dni),
            _requests.c.kind == "access",
            _requests.c.status == "answered")).fetchone()["last"]
        return add_months(last, FREE_ACCESS_MONTHS) if last else None


def check_erasure_request(cur, request_id, subject_hash):
    """Lock and validate the request an erasure answers (core/subject_erase.py):
    an OPEN request of kind `erasure`, of THIS person, whose identity was
    verified. Raises RequestError otherwise; returns the row."""
    row = _get(cur, request_id, lock=True)
    _closed_guard(row)
    if row["kind"] != "erasure":
        raise RequestError(f"request {request_id} is not an erasure request")
    if row["subject_hash"] != subject_hash:
        raise RequestError(f"request {request_id} is another person's")
    if not row["identity_check"]:
        raise RequestError("the identity has not been verified: record how (set_identity_check)")
    if sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(_events).where(
            _events.c.request_id == request_id, _events.c.event == "erased"
    ).limit(sa.literal_column("1"))).fetchone():
        # One request, one erasure: a DNI that is registered again later is a new person
        raise RequestError(f"request {request_id} was already carried out: register a new one")
    return row


def append_event(cur, request_id, actor, event, detail=""):
    """A line in the trail from inside the caller's own transaction."""
    _event(cur, request_id, actor, event, detail)


def note_reapplied(cur, request_id, subject_hash, actor, detail):
    """After an erasure was applied again to a restored database: a line in the request's trail,
    when the restored database still has that request (an older backup may not). Counts only."""
    if request_id is None:
        return False
    row = sqlcore.run(cur, sa.select(_requests.c.subject_hash).where(
        _requests.c.id == request_id)).fetchone()
    if row is None or row["subject_hash"] != subject_hash:
        return False
    _event(cur, request_id, actor, "erasure_reapplied", detail)
    return True


def erased_subject_hashes():
    """The people whose erasure was carried out (the hash only): what has to be
    applied again to a database restored from an older backup."""
    with db._connect() as conn, conn.cursor() as cur:
        return [r["subject_hash"] for r in sqlcore.run(cur, sa.select(_requests.c.subject_hash).distinct(
        ).select_from(_requests.join(_events, _events.c.request_id == _requests.c.id)).where(
            _requests.c.kind == "erasure", _events.c.event == "erased"
        ).order_by(_requests.c.subject_hash)).fetchall()]


def erasures():
    """Every erasure carried out, oldest first: dicts with `subject_hash`, `mode`
    (`anonymise` | `block`; None when the line is unreadable), `erased_at` (ISO instant) and
    `request_id`. All of them, not one per person: a DNI that was erased, registered again and erased
    again is two people, and `core/erasure_reapply.py` picks the erasure that follows an account."""
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(
            _requests.c.id, _requests.c.subject_hash, _events.c.ts, _events.c.detail
        ).select_from(_requests.join(_events, _events.c.request_id == _requests.c.id)).where(
            _requests.c.kind == "erasure", _events.c.event == "erased"
        ).order_by(_events.c.ts, _events.c.id)).fetchall()
    found = []
    for r in rows:
        try:
            mode = json.loads(r["detail"]).get("mode")
        except (ValueError, AttributeError):
            mode = None
        found.append({"subject_hash": r["subject_hash"], "request_id": r["id"],
                      "mode": mode, "erased_at": r["ts"].isoformat()})
    return found


def record_export(request_id, actor, detail=""):
    """The export for an access request was generated (a line in its trail)."""
    with db._connect() as conn, conn.cursor() as cur:
        row = _get(cur, request_id)
        if row["kind"] != "access":
            raise RequestError("only an access request is answered with an export")
        _event(cur, request_id, actor, "export_generated", detail)
