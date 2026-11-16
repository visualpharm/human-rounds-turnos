"""MCP connector tokens expire after 30 days without use.

Revision ID: 0043_mcp_token_lifetime
Revises: 0042_searches_sid
Create Date: 2026-11-13

Owner's security decision (2026-11-13): the patient's MCP token dies after 30
days WITHOUT USE (sliding window). `users.mcp_token_at` is when the token was
issued, `users.mcp_token_used` when an assistant last used it (written at most
every 10 minutes); the token is alive while `coalesce(used, at)` is less than
30 days old. «Mi cuenta» shows the last use and offers the revoke.

Existing tokens get `mcp_token_at = now()`: the clock of every connected
assistant starts today, none is cut off by the migration. Both columns are
nullable without a default (catalog-only change). Idempotent, forward-only.
"""
from alembic import op

revision = "0043_mcp_token_lifetime"
down_revision = "0042_searches_sid"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mcp_token_at timestamptz")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mcp_token_used timestamptz")
    op.execute("UPDATE users SET mcp_token_at = now() WHERE mcp_token IS NOT NULL AND mcp_token_at IS NULL")


def downgrade():
    raise NotImplementedError("forward-only: the lifetime columns carry the security decision of 2026-11-13")
