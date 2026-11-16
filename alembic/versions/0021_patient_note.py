"""The patient's own case text on a no-slots request («Contanos tu caso»)."""
from alembic import op

revision = "0021_patient_note"
down_revision = "0020_real_study"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE booking_requests ADD COLUMN IF NOT EXISTS patient_note text")


def downgrade():
    raise NotImplementedError("patient notes are forward-only; restore a verified backup")
