"""Patient login through the municipal portal: the portal account id.

`users.portal_user_id` holds the gateway's `X-User-Id` — the portal account's
immutable identifier — recorded at a patient's first portal request on the
account with the DNI of the validated CUIT (found or created). From then on it
is the only key the patient portal login uses. The partial unique index makes
«one portal account, one patient» a database rule. The column is NULL
everywhere until the patient portal login is switched on, so the index builds
on any installation.
"""
from alembic import op

revision = "0025_patient_portal_user_id"
down_revision = "0024_operator_portal_user_id"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS portal_user_id text")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_portal_user_id_uniq "
               "ON users (portal_user_id) WHERE portal_user_id IS NOT NULL")


def downgrade():
    raise NotImplementedError("portal account binding is forward-only; restore a verified backup")
