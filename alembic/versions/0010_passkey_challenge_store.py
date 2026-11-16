"""Persist digest-only WebAuthn challenge replay state.

Revision ID: 0010_passkey_challenge_store
Revises: 0009_day_queue_event_outbox
Create Date: 2026-09-03

The raw signed challenge remains browser-only.  This table contains its
SHA-256 digest, expiry, ceremony purpose, and (for registration) the session
owner, which lets every ASGI process reject a replay atomically.
"""
from alembic import op


revision = "0010_passkey_challenge_store"
down_revision = "0009_day_queue_event_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS passkey_challenges (
            challenge_digest bytea PRIMARY KEY,
            purpose          text NOT NULL CHECK (purpose IN ('registration', 'login')),
            username         text,
            expires_at       timestamptz NOT NULL,
            consumed_at      timestamptz,
            created_at       timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS passkey_challenges_expiry_idx
            ON passkey_challenges (expires_at)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS passkey_challenges")
