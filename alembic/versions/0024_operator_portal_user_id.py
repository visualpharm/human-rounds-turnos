"""Staff login through the municipal portal: the portal account id.

`portal_user_id` holds the gateway's `X-User-Id` — the portal account's
immutable identifier — recorded at a staff member's first portal login
(matched by email, together with the validated CUIT of 0023). From then on it
is the only key the portal login uses. The partial unique index makes «one
portal account, one operator» a database rule. The column is NULL everywhere
until the portal login is switched on, so the index builds on any
installation.
"""
from alembic import op

revision = "0024_operator_portal_user_id"
down_revision = "0023_operator_cuit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE operators ADD COLUMN IF NOT EXISTS portal_user_id text")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS operators_portal_user_id_uniq "
               "ON operators (portal_user_id) WHERE portal_user_id IS NOT NULL")


def downgrade():
    raise NotImplementedError("portal account binding is forward-only; restore a verified backup")
