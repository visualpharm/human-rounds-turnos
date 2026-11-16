"""Durable replayable invalidations for the staff day queue.

Revision ID: 0009_day_queue_event_outbox
Revises: 0008_normalized_emr_observations
Create Date: 2026-09-03

This narrowly-owned additive table has a real downgrade. It is the latest
migration contract: operators can prove an upgrade and downgrade without
mutating appointment or clinical data.
"""
from alembic import op


revision = "0009_day_queue_event_outbox"
down_revision = "0008_normalized_emr_observations"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS day_queue_events (
            id                  bigserial PRIMARY KEY,
            appointment_day     date NOT NULL,
            center_slug         text,
            assigned_doctor_id  text,
            assigned_doctor     text,
            created             timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS day_queue_events_replay_idx
            ON day_queue_events (id)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS day_queue_events")
