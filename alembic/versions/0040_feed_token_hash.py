"""Calendar-feed tokens are stored as SHA-256 hashes, never in the clear.

Revision ID: 0040_feed_token_hash
Revises: 0039_booking_case_chat
Create Date: 2026-11-06

`users.feed_token` (a patient's calendar address, /feed/appointments/<token>.ics)
and `operators.feed_token` (a doctor's, /feed/doctor/<token>.ics) held the raw
token, so anyone able to read the database (a copy, a dump, a backup) could
subscribe to a patient's appointments or a doctor's whole agenda; the patient's
token also travelled in every /api/account/* user dict and the doctor's in every
operator row of the panel (finding 2026-11-06, the same as revision 0038 for the
MCP token). The code now stores `sha256(token)` in the same `feed_token`
column, looks the address up by that hash, and keeps the token's last 4
characters in the new `feed_token_hint` columns so the screen can say which
address is in force without being able to show it again.

What it does to the existing rows, per table: an empty string becomes NULL (no
address yet), then every raw token is HASHED IN PLACE and its last 4
characters copied to the hint in one UPDATE (both SET expressions read the old
value), so a calendar app already subscribed keeps updating without anyone
re-subscribing. Every issuer since the feed appeared (2026-07-11) used
`token_urlsafe(16)` — 22 characters, never the 64-hex shape the guard skips.
A token the previous build writes in the clear while both builds serve the
deploy is hashed later, in place, by `core/feed_tokens.py`.

Safe on a large live table: the new columns are nullable without a default (a
catalog-only change), and each UPDATE touches only the rows that carry a token.
The unique constraints hold throughout: a hash is 64 characters and a raw token
22, so a hashed value never meets a raw one. Idempotent: the hashing is guarded
by the shape of the value (a stored hash is 64 lowercase hex characters), so a
re-run never hashes a hash nor overwrites a hint. Forward-only.
"""
from alembic import op

revision = "0040_feed_token_hash"
down_revision = "0039_booking_case_chat"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("users", "operators"):
        op.execute("ALTER TABLE %s ADD COLUMN IF NOT EXISTS feed_token_hint text" % table)
        op.execute("UPDATE %s SET feed_token = NULL WHERE feed_token = ''" % table)
        op.execute(
            "UPDATE %s"
            " SET feed_token_hint = right(feed_token, 4),"
            " feed_token = encode(sha256(convert_to(feed_token, 'UTF8')), 'hex')"
            " WHERE feed_token IS NOT NULL AND feed_token !~ '^[0-9a-f]{64}$'" % table)


def downgrade():
    raise NotImplementedError(
        "the raw calendar-feed tokens are gone by design; old code would compare the address with the hash "
        "and answer 404 to every subscribed calendar — each patient or doctor creates a new address")
