"""Non-clinical AI change evidence and human notification attestations."""
from alembic import op

revision = '0019_ai_register_changes'
down_revision = '0018_backup_runs'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS ai_register_changes (
        id bigserial PRIMARY KEY, function_id text NOT NULL, kind text NOT NULL,
        actor text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now(),
        substantial boolean NOT NULL DEFAULT false, details jsonb NOT NULL DEFAULT '{}'::jsonb,
        status text NOT NULL CHECK (status IN ('recorded', 'pending', 'notified')),
        notification_reference text, notified_on date, notified_by text, notified_at timestamptz
    )""")


def downgrade():
    raise NotImplementedError('Forward-only audit evidence; restore a verified backup')
