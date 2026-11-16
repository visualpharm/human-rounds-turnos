"""Doctor onboarding completion; preserve the doctor's scoped request access."""
from alembic import op

revision = '0017_doctor_onboarding'
down_revision = '0016_operator_username_history'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE operators ADD COLUMN IF NOT EXISTS onboarding_completed_at timestamptz')
    # Navigation is separate from authorization: do not alter role permissions.
    op.execute("""UPDATE roles SET description='Ve sus pacientes de hoy y sus turnos. Revisa sus datos, su foto y sus agendas.'
                  WHERE slug='medico' AND builtin AND description='Ve las solicitudes asignadas, sus pacientes de hoy y sus turnos. Puede reasignar una solicitud a otro médico.'""")


def downgrade():
    raise NotImplementedError('doctor onboarding is forward-only')
