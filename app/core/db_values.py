"""core/db_values.py — the small value helpers the statements of every `core/db_*.py` domain share: the
installation timezone, the date / datetime / JSON coercions and the Core spellings of an interval, a day count,
a local date, a text bind and the last ten digits of a phone.

Split out of `core/db.py` (the DB split of 2026-10-30): the first layer, imports no other `core/db_*.py`. A domain
module reaches these through THIS module (`db_values._coerce_dt(...)`), so a test patching `db_values.<name>`
patches what every caller uses.
"""

import json
from datetime import date, datetime

import sqlalchemy as sa


def _coerce_date(v):
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _coerce_dt(v):
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).strip().replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _json_clean(value):
    """Make DB journal payloads JSON-safe, including psycopg rows/datetimes."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    return value


def _days_ago(dias):
    """The Core spelling of `now() - <dias> * interval '1 day'` — the cut-off
    the report queries and the housekeeping purges compare a timestamp with."""
    return sa.func.now() - (sa.literal(dias, sa.Integer)
                            * sa.literal_column("interval '1 day'", sa.Interval))


def _interval(amount, unit):
    """`<amount> * interval '1 <unit>'` (unit: hour, month, ...): a span the caller adds to
    or subtracts from `now()`."""
    return sa.literal(amount, sa.Integer) * sa.literal_column("interval '1 %s'" % unit, sa.Interval)


def _last_ten_digits(column):
    """`right(regexp_replace(<column>, '\\D', '', 'g'), 10)`: the last ten digits of a free-text
    phone, written with inline literals so a query is the expression the two phone-lookup
    indexes are built on (`schema_model.last_ten_digits`) and Postgres can use them."""
    return sa.func.right(sa.func.regexp_replace(
        column, sa.literal_column(r"'\D'"), sa.literal_column("''"), sa.literal_column("'g'")),
        sa.literal_column("10"))


def _jsonb(value):
    """A JSONB value for a nullable column that took `_coerce_jsonb(value)`: `None` is the SQL
    NULL (the JSON `null` would be a stored value), anything else is the document itself."""
    return sa.null() if value is None else value


def _local_date(ts):
    """`(<ts> AT TIME ZONE '<installation tz>')::date`: the calendar day of an
    instant in the installation's timezone. The zone is an inlined literal, as
    it always was, so that a SELECT-list expression and the GROUP BY that repeats
    it are the same text for Postgres."""
    return sa.cast(ts.op("AT TIME ZONE")(sa.literal_column("'%s'" % _ART_SQL)), sa.Date)


def _text_value(value):
    return sa.literal(value, sa.Text)


def _coerce_jsonb(v):
    """dict/list -> string JSON para castear con %s::jsonb (evita depender del
    adaptador de psycopg para json). None pasa como NULL."""
    if v is None:
        return None
    return json.dumps(v, ensure_ascii=False)


# The timezone comes LAST: `core.tz` pulls in `core.config`, which reads the settings overlay through `core.db` while it
# is imported, and that import loads every domain module, which build statements out of the helpers above. Whoever
# imports this module first reaches this line with every helper already defined.
from app.core import tz as _tz  # noqa: E402

_ART = _tz.tzinfo()  # instalación (zoneinfo, DST-safe — ver tz.py)
_ART_SQL = _tz.tz_name()  # nombre IANA para `AT TIME ZONE` embebido en SQL
