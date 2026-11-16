"""MCP connector tokens are stored as SHA-256 hashes, never in the clear.

Revision ID: 0038_mcp_token_hash
Revises: 0037_booking_requested_at
Create Date: 2026-11-06

`users.mcp_token` held the raw Bearer token a patient pastes into an AI
assistant, so anyone able to read the database (a copy, a dump, a backup)
could act as that patient over MCP: see their requests, book and cancel in
their name (finding 2026-11-06). The code now stores `sha256(token)` in the
same `mcp_token` column, looks the token up by that hash (like
`reset_tokens.token`, revision 0033), and keeps the token's last 4 characters
in the new `mcp_token_hint` column so «Mi cuenta» can say which token is in
force without being able to show it again.

What it does to the existing rows: every raw token is HASHED IN PLACE and its
last 4 characters copied to the hint, in one UPDATE (both read the old value),
so a patient whose assistant already has the token keeps working without
regenerating anything. An empty string becomes NULL (no token), so the next
visit to «Mi cuenta» issues a real one.

Safe on a large live table: the new column is nullable without a default
(a catalog-only change), and the UPDATE touches only the rows that carry a
token, a small fraction of the patients. Idempotent: the hashing is guarded by
the shape of the value (a stored hash is 64 lowercase hex characters, a raw
token is 32 url-safe characters), so a re-run never hashes a hash nor
overwrites a hint. Forward-only.
"""
from alembic import op

revision = "0038_mcp_token_hash"
down_revision = "0037_booking_requested_at"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mcp_token_hint text")
    op.execute("UPDATE users SET mcp_token = NULL WHERE mcp_token = ''")
    op.execute(
        "UPDATE users"
        " SET mcp_token_hint = right(mcp_token, 4),"
        " mcp_token = encode(sha256(convert_to(mcp_token, 'UTF8')), 'hex')"
        " WHERE mcp_token IS NOT NULL AND mcp_token !~ '^[0-9a-f]{64}$'")


def downgrade():
    raise NotImplementedError(
        "the raw MCP tokens are gone by design; old code would compare the Bearer token with the hash "
        "and refuse every assistant — each patient regenerates the token in «Mi cuenta»")
