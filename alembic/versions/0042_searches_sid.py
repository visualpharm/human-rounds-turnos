"""Typing session of a search (`searches.sid`).

Revision ID: 0042_searches_sid
Revises: 0041_booking_reminder_skipped
Create Date: 2026-11-13

The search report listed «ped», «pedi», «pediat» as separate queries because
nothing tied them together. The page now sends a random id it makes up per
load (no person, no IP); the report glues the prefixes of one id into the
longest query. Additive nullable column, no backfill (older rows stay as
they were). Forward-only.
"""
from alembic import op

revision = "0042_searches_sid"
down_revision = "0041_booking_reminder_skipped"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE searches ADD COLUMN IF NOT EXISTS sid text")


def downgrade():
    raise NotImplementedError("searches.sid is forward-only")
