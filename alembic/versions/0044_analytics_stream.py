"""The anonymous analytics stream: events, public-schedule snapshots, export keys.

Revision ID: 0044_analytics_stream
Revises: 0043_mcp_token_lifetime
Create Date: 2026-11-13

Three additive tables, empty until `TURNOS_ANALYTICS=1`:
  analytics_events    one row per page view, search or booking step, only allowlisted fields (no IP, no
                      agent string, no persistent id, no document number, name or e-mail);
  analytics_schedule  per day, a snapshot of public availability by doctor/center/service (hours, slots,
                      how many are taken) — counts only, never who took them;
  analytics_keys      read-only keys of the export API; only the SHA-256 of a key is stored.
Forward-only.
"""
from alembic import op

revision = "0044_analytics_stream"
down_revision = "0043_mcp_token_lifetime"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS analytics_events (
            id text NOT NULL,
            ts timestamptz NOT NULL,
            type text NOT NULL,
            route text,
            rid text,
            ref text,
            lang text,
            device text,
            jid text,
            pl text,
            q text,
            n integer,
            origin text,
            step text,
            service text,
            CONSTRAINT analytics_events_pkey PRIMARY KEY (id)
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS analytics_events_ts_idx ON analytics_events (ts)")
    op.execute("CREATE INDEX IF NOT EXISTS analytics_events_type_ts_idx ON analytics_events (type, ts)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS analytics_schedule (
            snapshot date NOT NULL,
            day date NOT NULL,
            doctor text NOT NULL,
            center text NOT NULL DEFAULT '',
            service text NOT NULL DEFAULT '',
            hours numeric(6,2) NOT NULL DEFAULT 0,
            slots_total integer NOT NULL DEFAULT 0,
            slots_web integer NOT NULL DEFAULT 0,
            taken integer NOT NULL DEFAULT 0,
            free integer NOT NULL DEFAULT 0,
            CONSTRAINT analytics_schedule_pkey PRIMARY KEY (snapshot, day, doctor, center, service)
        )""")
    op.execute("""
        CREATE TABLE IF NOT EXISTS analytics_keys (
            id serial NOT NULL,
            label text NOT NULL,
            key_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            revoked_at timestamptz,
            last_used_at timestamptz,
            CONSTRAINT analytics_keys_pkey PRIMARY KEY (id),
            CONSTRAINT analytics_keys_hash_key UNIQUE (key_hash)
        )""")


def downgrade():
    raise NotImplementedError("the analytics tables are forward-only")
