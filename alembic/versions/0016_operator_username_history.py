"""Reserve retired operator logins and preserve rename audit."""
from alembic import op
revision = "0016_operator_username_history"
down_revision = "0015_formal_invariants"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS operator_username_history (
    old_username text PRIMARY KEY,
    new_username text NOT NULL,
    operator_id integer NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);
""")

def downgrade():
    # Repo convention (0001/0003/0006/0007/0008/0015): forward-only revisions
    # refuse the downgrade with NotImplementedError, and the queue-SSE test
    # pins that a refused downgrade leaves the head and the data intact.
    raise NotImplementedError("retired operator logins stay reserved")
