"""0015_formal_invariants — the two schema halves of the 2026-09-24 formal
verification fixes, as a real forward revision.

Revision ID: 0015_formal_invariants
Revises: 0014_users_updated_column
Create Date: 2026-09-24

- `appointment_events`: one row per status / time / attendance change of a
  booking request, written in the same transaction as the change
  (AppointmentHsi.tla AuditReconstructable).
- `interviews_request_uniq`: at most one interview bound to a request
  (IntakeTriage.tla OneInterviewPerRequest). Pre-existing duplicates are
  kept: the winner is the one `interview_for_request` already showed
  (non-QR first, then newest); the others get `request_id = NULL` and keep
  the link in `detached_request_id`. No clinical row is deleted.

Same two-halves contract as every revision since 0002: the DDL below is
byte-equivalent to the matching statements in db._SCHEMA_SQL (fresh installs
get it through the baseline replay and this revision is a clean no-op
there); existing stamped databases receive it here. Every statement is
idempotent: `IF NOT EXISTS`, and the backfill only matches rows it has not
fixed yet. (SlotHold.tla OneApprovedPerSlot needed no schema: capacity is
enforced under an advisory lock in `db_booking.approve` — a unique index would have
forbidden the desk's legitimate same-time doctor-less approvals.)

Forward-only, like every revision in this repo (docs/UPGRADING.md,
"Rollback"): `downgrade()` refuses.
"""
from alembic import op

revision = "0015_formal_invariants"
down_revision = "0014_users_updated_column"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS appointment_events (
            id              bigserial PRIMARY KEY,
            request_id      integer NOT NULL,
            kind            text NOT NULL,
            actor           text,
            status_from     text,
            status_to       text,
            appointment_from timestamptz,
            appointment_to  timestamptz,
            attendance_from text,
            attendance_to   text,
            created         timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS appointment_events_request_idx ON appointment_events (request_id, id);

        ALTER TABLE interviews ADD COLUMN IF NOT EXISTS detached_request_id integer;
        UPDATE interviews SET detached_request_id = request_id, request_id = NULL
            WHERE request_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM interviews w
                           WHERE w.request_id = interviews.request_id AND w.id <> interviews.id
                             AND ((w.entry_mode = 'qr') < (interviews.entry_mode = 'qr')
                                  OR ((w.entry_mode = 'qr') = (interviews.entry_mode = 'qr')
                                      AND w.id > interviews.id)));
        CREATE UNIQUE INDEX IF NOT EXISTS interviews_request_uniq ON interviews (request_id)
            WHERE request_id IS NOT NULL;
        """
    )


def downgrade():
    raise NotImplementedError(
        "This repo's migrations are forward-only (see docs/UPGRADING.md, "
        "\"Rollback\") — restore from a backup if an upgrade goes wrong."
    )
