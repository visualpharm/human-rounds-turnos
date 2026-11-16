"""Every doctor is one account: invitation timestamp and one account per doctor.

`invited_at` separates «sin invitar» from «invitado» (an invitation email or a
copied link). The partial unique index makes «one doctor, one account» a
database rule instead of a code-only guard, so a backfill racing a create can
never leave two accounts on one doctor. Production had no duplicates when this
shipped (2026-10-19 baseline); an installation that does must resolve them
before upgrading — the index creation fails loudly rather than guessing which
account wins.
"""
from alembic import op

revision = "0022_doctor_account"
down_revision = "0021_patient_note"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE operators ADD COLUMN IF NOT EXISTS invited_at timestamptz")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS operators_doctor_id_uniq "
               "ON operators (doctor_id) WHERE doctor_id IS NOT NULL")


def downgrade():
    raise NotImplementedError("doctor accounts are forward-only; restore a verified backup")
