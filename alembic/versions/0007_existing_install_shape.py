"""0007_existing_install_shape — converge DDL added after the baseline cutover.

Revision ID: 0007_existing_install_shape
Revises: 0006_legacy_runner_tables
Create Date: 2026-08-19

The Alembic baseline imports the current ``core.db._SCHEMA_SQL`` text.  That
keeps genuinely fresh installations complete, but an installation already
stamped at 0001 never replays the baseline body.  Four later additions reached
the living baseline without matching forward DDL: the laboratory-analysis
table, two query indexes, and the current default for ``operators.role``.

This revision carries those exact changes to existing installations.  Every
statement is idempotent because fresh databases already receive the same shape
from 0001.  It then asks the baseline shape model to prove these four targeted
features: a same-name wrong index or partial pre-existing table fails the
transaction instead of being silently accepted by ``IF NOT EXISTS``.  The
proof builds the same table and indexes again under probe names inside a
savepoint, compares what the catalog renders for both, and rolls the probes
back (until 2026-11-05 it went through the removed ``migrations/`` shape
parser).  The default change is metadata-only; it does not rewrite existing
operator rows.
Forward-only, like the rest of the lineage.
"""

from alembic import op


revision = "0007_existing_install_shape"
down_revision = "0006_legacy_runner_tables"
branch_labels = None
depends_on = None


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lab_report_analysis (
    id           bigserial PRIMARY KEY,
    dni          text NOT NULL,
    report_hash  text NOT NULL,
    prompt_hash  text NOT NULL,
    fecha        text,
    sistema      text,
    tipo_estudio text,
    resumen      text,
    tono         text,
    sensible     boolean NOT NULL DEFAULT false,
    valores      jsonb,
    modelo       text,
    created      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dni, report_hash)
);
CREATE INDEX IF NOT EXISTS lab_report_analysis_dni_idx
    ON lab_report_analysis (dni);
"""

_DEFAULT_SQL = "ALTER TABLE operators ALTER COLUMN role SET DEFAULT 'medico';"

# (index name, the rest of its CREATE INDEX statement after the name)
_INDEXES = (
    ("booking_requests_doctor_appointment_idx",
     """ON booking_requests (assigned_doctor_id, appointment_at)
    WHERE assigned_doctor_id IS NOT NULL AND appointment_at IS NOT NULL"""),
    ("chat_messages_whatsapp_tel_idx",
     """ON chat_messages (channel, right(regexp_replace(key, '\\D', '', 'g'), 10))
    WHERE channel='whatsapp'"""),
)

_PROBE = "p0007_"

_COLUMNS_SQL = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid)
FROM pg_attribute a
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = to_regclass(%(t)s) AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attname
"""
_CONSTRAINTS_SQL = """
SELECT pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid = to_regclass(%(t)s) ORDER BY 1
"""
_TABLE_INDEXES_SQL = """
SELECT pg_get_indexdef(indexrelid) FROM pg_index
WHERE indrelid = to_regclass(%(t)s) ORDER BY 1
"""
_INDEX_SQL = "SELECT pg_get_indexdef(to_regclass(%(t)s))"


def upgrade():
    bind = op.get_bind()
    bind.exec_driver_sql(_TABLE_SQL)
    bind.exec_driver_sql(_DEFAULT_SQL)
    for name, rest in _INDEXES:
        bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS %s\n    %s" % (name, rest))
    remaining = _shape_gaps(bind)
    if remaining:
        raise RuntimeError(
            "0007 could not prove the existing-install schema repair: %s"
            % "; ".join(remaining)
        )


def _rows(bind, sql, table):
    return [tuple(str(v) for v in row) for row in bind.exec_driver_sql(sql, {"t": "public." + table})]


def _shape_gaps(bind):
    """What the catalog says about the four owned features, compared with the
    same DDL run again under probe names; the probes never outlive the
    savepoint."""
    gaps = []
    nested = bind.begin_nested()
    try:
        bind.exec_driver_sql(_TABLE_SQL.replace("lab_report_analysis", _PROBE + "lab_report_analysis"))
        for name, rest in _INDEXES:
            bind.exec_driver_sql("CREATE INDEX %s%s\n    %s" % (_PROBE, name, rest))

        def probe_rows(sql, table):
            return [tuple(v.replace(_PROBE, "") for v in row) for row in _rows(bind, sql, _PROBE + table)]

        for sql in (_COLUMNS_SQL, _CONSTRAINTS_SQL, _TABLE_INDEXES_SQL):
            live = _rows(bind, sql, "lab_report_analysis")
            if sql is _COLUMNS_SQL:
                # 0030 later turns `fecha` into a date, and the frozen baseline
                # already carries that: a fresh database has a date here.
                live = [("fecha", "text") + row[2:] if row[:2] == ("fecha", "date") else row
                        for row in live]
            if live != probe_rows(sql, "lab_report_analysis"):
                gaps.append("lab_report_analysis differs from its definition")
                break
        for name, _ in _INDEXES:
            if _rows(bind, _INDEX_SQL, name) != probe_rows(_INDEX_SQL, name):
                gaps.append("%s differs from its definition" % name)
    finally:
        nested.rollback()
    default = bind.exec_driver_sql(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='operators' AND column_name='role'").scalar()
    if default != "'medico'::text":
        gaps.append("operators.role default is %r" % default)
    return gaps


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "Rollback) — restore from a backup if an upgrade goes wrong."
    )
