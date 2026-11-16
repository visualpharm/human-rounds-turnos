"""core/db_stream.py — the anonymous analytics stream's tables: events, public-schedule snapshots, export keys.

Writers (`insert_events`, `replace_schedule`, `prune`) are called by turnos/analytics_export.py and never
raise into a request. Readers feed the export API (turnos/analytics_api_router.py) and the Reportes screens
(turnos/analytics_report.py). The tables hold counts and allowlisted fields only — see
turnos/analytics_events.py for the contract and docs/policies/DATA_MAP.md for the table list.

Like every `db_<domain>` module: the connection is `db._connect()`, statements are Core built from
`core/schema_model.py`, reached at call time.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import schema_model as sm
from app.core import sqlcore

_EVENT_COLS = ("route", "rid", "ref", "lang", "device", "jid", "pl", "q", "n", "origin", "step", "service")


# ---- writing ---------------------------------------------------------------------------------------------

def insert_events(events):
    """Writes the cleaned events; a repeated id is ignored. Returns the rows written."""
    if not events:
        return 0
    t = sm.analytics_events
    rows = []
    for e in events:
        row = {"id": e["id"], "ts": datetime.fromtimestamp(int(e["ts"]), tz=timezone.utc), "type": e["type"]}
        row.update({c: e.get(c) for c in _EVENT_COLS})
        rows.append(row)
    with db._connect() as conn, conn.cursor() as cur:
        res = sqlcore.run(cur, _pg.insert(t).values(rows).on_conflict_do_nothing(index_elements=[t.c.id]))
        return res.rowcount


def replace_schedule(snapshot, rows):
    """The day's schedule snapshot: replaces whatever the same `snapshot` date held, in one transaction."""
    t = sm.analytics_schedule
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(t.c.snapshot == snapshot))
        for i in range(0, len(rows), 500):
            sqlcore.run(cur, _pg.insert(t).values([dict(r, snapshot=snapshot) for r in rows[i:i + 500]]))
    return len(rows)


def has_snapshot(snapshot):
    t = sm.analytics_schedule
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(sa.literal(1)).select_from(t).where(t.c.snapshot == snapshot)
                           .limit(1)).fetchone() is not None


def prune(now, event_days, limit=20000):
    """Events older than `event_days`, snapshots older than a third of it (bounded per call)."""
    ev, sc = sm.analytics_events, sm.analytics_schedule
    ctid = sa.literal_column("ctid")
    cut = now - timedelta(days=int(event_days))
    snap = (now - timedelta(days=max(30, int(event_days) // 3))).date()
    with db._connect() as conn, conn.cursor() as cur:
        a = sqlcore.run(cur, sa.delete(ev).where(ctid.in_(
            sa.select(ctid).select_from(ev).where(ev.c.ts < cut).limit(limit)))).rowcount
        b = sqlcore.run(cur, sa.delete(sc).where(ctid.in_(
            sa.select(ctid).select_from(sc).where(sc.c.snapshot < snap).limit(limit)))).rowcount
    return a + b


def taken_by_day(first, last, tzname):
    """`{(day, doctor, center, service): n}` of pending + approved appointments from `first` to `last`
    (calendar days in the installation's zone). Counts only: no request, time or patient leaves."""
    t = sm.booking_requests
    doctor = sa.func.coalesce(sa.func.nullif(t.c.slot_doctor_id, ""), sa.func.nullif(t.c.assigned_doctor_id, ""))
    day = sa.cast(sa.func.timezone(tzname, t.c.appointment_at), sa.Date)
    n = sa.func.count().label("n")
    center = sa.func.coalesce(t.c.center_slug, "")
    service = sa.func.coalesce(t.c.service_id, "")
    q = sa.select(day.label("day"), doctor.label("doctor"), center.label("center"),
                  service.label("service"), n
                  ).where(t.c.status.in_(["pendiente", "aprobada"]), t.c.appointment_at.isnot(None),
                          day >= first, day <= last, doctor.isnot(None)
                          ).group_by(day, doctor, center, service)
    with db._connect() as conn, conn.cursor() as cur:
        return {(r["day"], r["doctor"], r["center"], r["service"]): r["n"]
                for r in sqlcore.run(cur, q).fetchall()}


# ---- reading: the export API -----------------------------------------------------------------------------

def events_page(since=None, until=None, kind=None, after=None, limit=1000, before_id=None):
    """Events by time window, newest last, keyset-paged on `id` (ids sort by time). `before_id` holds back
    ids that sort at or after it (the export's lag: see analytics_api_router)."""
    t = sm.analytics_events
    q = sa.select(*t.c).order_by(t.c.id).limit(int(limit))
    if since is not None:
        q = q.where(t.c.ts >= since)
    if until is not None:
        q = q.where(t.c.ts < until)
    if kind:
        q = q.where(t.c.type == kind)
    if after:
        q = q.where(t.c.id > after)
    if before_id:
        q = q.where(t.c.id < before_id)
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, q).fetchall()


def schedule_page(since=None, until=None, after=None, limit=1000):
    """Schedule rows with `snapshot` in [since, until), keyset-paged on the primary key."""
    t = sm.analytics_schedule
    keys = (t.c.snapshot, t.c.day, t.c.doctor, t.c.center, t.c.service)
    q = sa.select(*t.c).order_by(*keys).limit(int(limit))
    if since is not None:
        q = q.where(t.c.snapshot >= since)
    if until is not None:
        q = q.where(t.c.snapshot < until)
    if after:
        q = q.where(sa.tuple_(*keys) > sa.tuple_(*[sa.literal(v) for v in after]))
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, q).fetchall()


# ---- reading: reports ------------------------------------------------------------------------------------

def events_iter(since, kinds=None, chunk=5000):
    """Every event of the window as dict rows in `(ts, id)` order, read `chunk` rows at a time (keyset on
    `(ts, id)`, one short query each): a report over a year never holds the window in memory — the caller
    aggregates as the rows pass. Rows that arrive during the walk and sort before the cursor are not seen."""
    t = sm.analytics_events
    last = None
    while True:
        q = sa.select(*t.c).where(t.c.ts >= since).order_by(t.c.ts, t.c.id).limit(int(chunk))
        if kinds:
            q = q.where(t.c.type.in_(list(kinds)))
        if last is not None:
            q = q.where(sa.tuple_(t.c.ts, t.c.id) > sa.tuple_(sa.literal(last[0]), sa.literal(last[1])))
        with db._connect() as conn, conn.cursor() as cur:
            rows = sqlcore.run(cur, q).fetchall()
        yield from rows
        if len(rows) < int(chunk):
            return
        last = (rows[-1]["ts"], rows[-1]["id"])


def latest_snapshot():
    t = sm.analytics_schedule
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sa.func.max(t.c.snapshot).label("d")).select_from(t)).fetchone()
        return row["d"] if row else None


def schedule_days(snapshot, first, last):
    """Per day and center of one snapshot: slots, taken, free (summed over doctors/services)."""
    t = sm.analytics_schedule
    q = sa.select(t.c.day, t.c.center, sa.func.sum(t.c.slots_total).label("slots_total"),
                  sa.func.sum(t.c.taken).label("taken"), sa.func.sum(t.c.free).label("free"),
                  sa.func.sum(t.c.slots_web).label("slots_web")
                  ).where(t.c.snapshot == snapshot, t.c.day >= first, t.c.day <= last
                          ).group_by(t.c.day, t.c.center).order_by(t.c.day, t.c.center)
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, q).fetchall()


# ---- export keys -----------------------------------------------------------------------------------------

def _hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_key(label):
    """A new read-only key. Returns `(id, plaintext)`: the plaintext is shown once, only its hash is kept."""
    plain = "hra_" + secrets.token_urlsafe(32)
    t = sm.analytics_keys
    with db._connect() as conn, conn.cursor() as cur:
        kid = sqlcore.run(cur, sa.insert(t).values(label=str(label)[:80], key_hash=_hash(plain))
                          .returning(t.c.id)).fetchone()["id"]
    return kid, plain


def verify_key(plain):
    """True for a live key; its `last_used_at` is refreshed at most once a minute."""
    if not plain or not plain.startswith("hra_") or len(plain) > 100:
        return False
    t = sm.analytics_keys
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.id, t.c.last_used_at).where(
            t.c.key_hash == _hash(plain), t.c.revoked_at.is_(None))).fetchone()
        if row is None:
            return False
        sqlcore.run(cur, sa.update(t).where(t.c.id == row["id"], sa.or_(
            t.c.last_used_at.is_(None), t.c.last_used_at < sa.func.now() - sa.text("interval '1 minute'")
        )).values(last_used_at=sa.func.now()))
        return True


def list_keys():
    t = sm.analytics_keys
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t.c.id, t.c.label, t.c.created_at, t.c.revoked_at,
                                          t.c.last_used_at).order_by(t.c.id)).fetchall()


def revoke_key(key_id):
    t = sm.analytics_keys
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).where(t.c.id == int(key_id), t.c.revoked_at.is_(None))
                           .values(revoked_at=sa.func.now())).rowcount == 1
