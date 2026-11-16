"""Staff sessions that «Cerrar sesión» ended (so a copy of the cookie stops working).

Revision ID: 0035_admin_session_revocations
Revises: 0034_backup_purge_state
Create Date: 2026-10-28

The staff cookie (`st_admin`) is a signed, stateless token: signing out only cleared it in the
browser that signed out, and a copy made earlier kept working until its expiry (TASK-55). The token
now carries a session id (`sid`; a token issued before this change uses its own `nonce`) and signing
out writes that id here; `auth.admin_auth._verify_token` refuses a token whose id is listed.

One row per signed-out session: the id (a random token, not a name), the account it belonged to and
when the row may go (`expires_at`, never before the longest a copy of that session could still live;
every write also deletes the rows past it). No person's data beyond the staff username. Additive only:
one new empty table, nothing read or rewritten in existing tables. Forward-only.
"""
from alembic import op

revision = "0035_admin_session_revocations"
down_revision = "0034_backup_purge_state"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS admin_session_revocations ("
        " sid text PRIMARY KEY,"
        " username text NOT NULL,"
        " revoked_at timestamp with time zone NOT NULL DEFAULT now(),"
        " expires_at timestamp with time zone NOT NULL)")


def downgrade():
    raise NotImplementedError(
        "admin_session_revocations is forward-only; dropping it would make every signed-out "
        "cookie that has not expired work again")
