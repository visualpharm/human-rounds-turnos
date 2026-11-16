"""A day-before reminder with nothing to send is not counted as sent (`booking_requests.reminder_skipped`).

Revision ID: 0041_booking_reminder_skipped
Revises: 0040_feed_token_hash
Create Date: 2026-11-10

The sweep claims a reminder (`reminder_at`) before it sends it, and the
hospital's report counted every claimed one as sent. One with nothing to
send (no address and no phone, or the channels off by configuration) keeps
its claim, so no sweep takes it again, and is now marked here, so the report
leaves it out (the product owner's hub, 2026-11-10).

Additive: one boolean column with a constant default (no table rewrite), no
backfill — older rows cannot be told apart and stay counted as before.
Forward-only.
"""
from alembic import op

revision = "0041_booking_reminder_skipped"
down_revision = "0040_feed_token_hash"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS reminder_skipped boolean NOT NULL DEFAULT false")


def downgrade():
    raise NotImplementedError("reminder_skipped is forward-only")
