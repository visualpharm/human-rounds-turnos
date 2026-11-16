"""Dates and times stored as text become `date` / `time` (problems.md №13, 2026-10-25).

`slot_holds.day` / `slot_holds.time`, `lab_report_analysis.fecha` and
`patient_lab_uploads.fecha` were `text`, checked only by a regex in the code:
«9:00» and «09:00» were two different slots to the double-booking guard,
«2024-13-45» was accepted, and ordering by the text broke on a foreign format.
The code kept writing valid values, so nothing was on fire — this closes the
gap in the database itself. Callers are unchanged: `core/db.py` still hands
out `YYYY-MM-DD` / `HH:MM` text.

Safe on every boot path, never raises on data and never destroys it:
* a column that is already `date` / `time` (a fresh install built from
  `_SCHEMA_SQL`) or a table that does not exist is skipped;
* `fecha`: a value that is not a real ISO day is stored as NULL («no date»);
  an ISO day with a time suffix keeps its day;
* `slot_holds`: «9:00»/«09:00» collapse to one slot; a hold whose day or time
  is not real, and the other copies of a duplicated slot, cannot live in the
  typed columns. The linked one wins (it is the only place a pending request
  keeps its chosen time), then the newest.
* every value or row the conversion could not keep is first copied, whole,
  into `date_time_conversion_leftovers` (the revision creates it, empty, on
  every install — it is also in `_SCHEMA_SQL`) with the reason, and logged by
  id — nothing is lost, an operator can restore it.

`scripts/fixes/report_fechas_texto.py` lists, read-only, what this revision
would copy aside — run it before upgrading. The reference installation held 9
holds (all valid, no duplicate) and empty lab tables on 2026-10-25.
"""
import logging
import re
from datetime import date, datetime, time

from alembic import op

revision = "0030_date_time_columns"
down_revision = "0029_purged_files"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

_LEFTOVERS = "date_time_conversion_leftovers"


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = str(value or "").strip()
    if not _DAY_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _time(value):
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    value = str(value or "").strip()
    if not _TIME_RE.match(value):
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def _fecha(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = str(value or "").strip()
    if not _FECHA_RE.match(value):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _column_type(bind, table, column):
    row = bind.exec_driver_sql(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
        (table, column)).fetchone()
    return row[0] if row else None


def _create_leftovers(bind):
    bind.exec_driver_sql(
        f"CREATE TABLE IF NOT EXISTS {_LEFTOVERS} ("
        "id bigserial PRIMARY KEY, revision text NOT NULL, source_table text NOT NULL, "
        "row_id bigint NOT NULL, reason text NOT NULL, original jsonb NOT NULL, "
        "created timestamptz NOT NULL DEFAULT now())")


def _set_aside_rows(bind, table, ids, reason):
    """Copy whole rows of `table` into the leftovers table, then the caller
    removes them from `table`."""
    if not ids:
        return
    bind.exec_driver_sql(
        f"INSERT INTO {_LEFTOVERS} (revision, source_table, row_id, reason, original) "
        f"SELECT %s, %s, t.id, %s, to_jsonb(t) FROM {table} t WHERE t.id = ANY(%s)",
        (revision, table, reason, ids))


def _convert_slot_holds(bind):
    if _column_type(bind, "slot_holds", "day") != "text" \
            and _column_type(bind, "slot_holds", "time") != "text":
        return
    rows = bind.exec_driver_sql(
        "SELECT id, service_id, day, time, request_id FROM slot_holds ORDER BY id").fetchall()
    groups = {}
    impossible = []
    for id_, service_id, day, hour, request_id in rows:
        key = (service_id, _day(day), _time(hour))
        if key[1] is None or key[2] is None:
            log.warning("slot_holds %s: day=%r time=%r is not a real day/time, set aside",
                        id_, day, hour)
            impossible.append(id_)
            continue
        groups.setdefault(key, []).append((request_id is not None, id_))
    repeated = []
    for key, members in groups.items():
        if len(members) > 1:
            # A hold linked to a request outlives an unlinked one; then the newest.
            members.sort()
            kept = members.pop()
            log.warning("slot_holds: %s %s %s is held %d times, kept %s, set aside %s",
                        key[0], key[1], key[2], len(members) + 1, kept[1],
                        [m[1] for m in members])
            repeated.extend(m[1] for m in members)
    _set_aside_rows(bind, "slot_holds", impossible, "day/time is not a real day/time")
    _set_aside_rows(bind, "slot_holds", repeated, "same slot as a kept hold")
    losers = impossible + repeated
    if losers:
        bind.exec_driver_sql("DELETE FROM slot_holds WHERE id = ANY(%s)", (losers,))
    if _column_type(bind, "slot_holds", "day") == "text":
        bind.exec_driver_sql("ALTER TABLE slot_holds ALTER COLUMN day TYPE date USING day::date")
    if _column_type(bind, "slot_holds", "time") == "text":
        bind.exec_driver_sql("ALTER TABLE slot_holds ALTER COLUMN time TYPE time USING time::time")


def _convert_fecha(bind, table):
    if _column_type(bind, table, "fecha") != "text":
        return
    for id_, fecha in bind.exec_driver_sql(
            f"SELECT id, fecha FROM {table} WHERE fecha IS NOT NULL").fetchall():
        fixed = _fecha(fecha)
        iso = fixed.isoformat() if fixed else None
        if iso == fecha:
            continue
        log.warning("%s %s: fecha %r %s", table, id_, fecha,
                    "-> %s" % iso if iso else "is not a real day, set to NULL")
        if iso is None:
            bind.exec_driver_sql(
                f"INSERT INTO {_LEFTOVERS} (revision, source_table, row_id, reason, original) "
                "VALUES (%s, %s, %s, %s, jsonb_build_object('fecha', %s::text))",
                (revision, table, id_, "fecha is not a real day, now NULL", fecha))
        bind.exec_driver_sql(f"UPDATE {table} SET fecha = %s WHERE id = %s", (iso, id_))
    bind.exec_driver_sql(f"ALTER TABLE {table} ALTER COLUMN fecha TYPE date USING fecha::date")


def upgrade():
    bind = op.get_bind()
    _create_leftovers(bind)
    _convert_slot_holds(bind)
    _convert_fecha(bind, "lab_report_analysis")
    _convert_fecha(bind, "patient_lab_uploads")


def downgrade():
    bind = op.get_bind()
    if _column_type(bind, "slot_holds", "day") == "date":
        bind.exec_driver_sql(
            "ALTER TABLE slot_holds ALTER COLUMN day TYPE text USING to_char(day, 'YYYY-MM-DD')")
    if _column_type(bind, "slot_holds", "time") == "time without time zone":
        bind.exec_driver_sql(
            "ALTER TABLE slot_holds ALTER COLUMN time TYPE text USING to_char(time, 'HH24:MI')")
    for table in ("lab_report_analysis", "patient_lab_uploads"):
        if _column_type(bind, table, "fecha") == "date":
            bind.exec_driver_sql(
                f"ALTER TABLE {table} ALTER COLUMN fecha TYPE text USING to_char(fecha, 'YYYY-MM-DD')")
