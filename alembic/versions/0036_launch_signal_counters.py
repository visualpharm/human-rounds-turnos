"""Hourly counters of operational events, for the daily launch digest.

Revision ID: 0036_launch_signal_counters
Revises: 0035_admin_session_revocations
Create Date: 2026-10-30

Staff sign-ins, outgoing mail and WhatsApp messages and the background failures the code
swallows on purpose leave no row anywhere: the log is the only trace, and an installation whose
logs the developers cannot read (salud, behind the portal gateway) has no way to see them.
`core/launch_signals.py` adds one to a counter here when TURNOS_SENTRY_SIGNALS lists `digest`;
the worker reads the last 24 hours once a day and sends the numbers to the installation's own
error tracker (`ops/launch_digest.py`).

One row per (hour, closed code): the hour the event happened in (truncated, the installation
clock is UTC in the database) and how many times. The code is one of a fixed list in
`core/launch_signals.py`; nothing else is ever written, so no row can hold a name, a DNI, a phone
number or an address. Additive only: one new empty table, nothing read or rewritten in existing
tables. Forward-only.
"""
from alembic import op

revision = "0036_launch_signal_counters"
down_revision = "0035_admin_session_revocations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS launch_signal_counters ("
        " bucket timestamp with time zone NOT NULL,"
        " code text NOT NULL,"
        " n bigint NOT NULL DEFAULT 0,"
        " PRIMARY KEY (bucket, code))")


def downgrade():
    raise NotImplementedError(
        "launch_signal_counters is forward-only; its rows are counters a day old at most")
