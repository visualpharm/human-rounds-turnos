"""The «Contanos tu caso» conversation travels with its request (`booking_requests.case_chat`).

Revision ID: 0039_booking_case_chat
Revises: 0038_mcp_token_hash
Create Date: 2026-11-07

With no free slot the patient used to type their case into an empty box.
Now a short chat asks one to three questions (what they need, since when,
how urgent) and the request keeps that conversation: `{"resumen": {...},
"mensajes": [{"role", "texto"}, ...]}`. The desk reads the summary in the
queue and opens the conversation on demand (a logged clinical read).
`patient_note` keeps the patient's own words, so every older reader keeps
working.

Additive: one nullable jsonb column, no backfill (older requests had no
chat). Forward-only.
"""
from alembic import op

revision = "0039_booking_case_chat"
down_revision = "0038_mcp_token_hash"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS case_chat jsonb")


def downgrade():
    raise NotImplementedError("case_chat is forward-only")
