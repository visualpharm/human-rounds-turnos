"""The appointment a request asked for (`booking_requests.requested_at`).

Revision ID: 0037_booking_requested_at
Revises: 0036_launch_signal_counters
Create Date: 2026-10-31

A request for a concrete slot kept that slot only in its `slot_holds` row,
and approving, rejecting or cancelling the request deletes the hold — so a
rejected or cancelled request no longer knew which appointment it had been
for, and «Mis solicitudes» showed it without a date (the product owner 26.10: the account
list should say which day the patient tried to book). The request now
keeps the instant it asked for, written once at creation and never moved;
`appointment_at` stays the appointment the hospital gave.

Backfill: a pending request whose hold is still linked takes that hold's day
and time, read as the installation's wall clock (America/Argentina/Buenos_Aires,
the same reading `slot_holds` gets everywhere). Requests already approved,
rejected or cancelled have lost their hold; nothing is invented for them.
Additive: one nullable column, no person's data. Forward-only.
"""
from alembic import op

revision = "0037_booking_requested_at"
down_revision = "0036_launch_signal_counters"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS requested_at timestamptz")
    op.execute(
        "UPDATE booking_requests b"
        " SET requested_at = (h.day + h.time) AT TIME ZONE 'America/Argentina/Buenos_Aires'"
        " FROM slot_holds h"
        " WHERE h.request_id = b.id AND b.requested_at IS NULL")


def downgrade():
    raise NotImplementedError("requested_at is forward-only; the slot it records is gone from slot_holds")
