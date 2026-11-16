"""Staff login through the municipal portal: the operator's validated CUIT.

`cuit` is recorded at a staff member's first portal login (matched by email)
and from then on is the only key the portal login uses. The partial unique
index makes «one CUIT, one account» a database rule, so two first logins
racing on two accounts can never bind the same person twice. The column is
NULL everywhere until the portal login is switched on, so the index builds on
any installation.
"""
from alembic import op

revision = "0023_operator_cuit"
down_revision = "0022_doctor_account"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE operators ADD COLUMN IF NOT EXISTS cuit text")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS operators_cuit_uniq "
               "ON operators (cuit) WHERE cuit IS NOT NULL")


def downgrade():
    raise NotImplementedError("portal CUIT binding is forward-only; restore a verified backup")
