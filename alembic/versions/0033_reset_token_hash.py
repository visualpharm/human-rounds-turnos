"""Password-recovery links are stored as SHA-256 hashes, never in the clear.

`reset_tokens.token` used to hold the raw token that travels in the emailed
link, so anyone able to read the database (a copy, a dump, a replica) could
build a working link and take the account during the token's hour of life —
while the OTP codes were already hashed (sqla-5 finding, 2026-10-25). The code
now stores `sha256(token)` in the same `token` column and looks the link up by
that hash (like `admin_reset_tokens.token_hash`); this revision converts the
rows written before the change.

What it does to the existing rows:
  - rows already used or expired are deleted: they can never work again and
    they are the only place a raw token would otherwise linger;
  - the rows still open (a link emailed within the last hour) are HASHED IN
    PLACE, so a patient who asked for a link just before the deploy can still
    use it. Nothing is invalidated that was valid, and no raw token remains.

The schema does not change (same table, same column), so a fresh install, a
stamped install and the legacy bridge all converge as before. The hashing is
guarded by the shape of the value (a stored hash is 64 hex characters, a raw
token is 43 url-safe characters), so a re-run never hashes a hash.
"""
from alembic import op

revision = "0033_reset_token_hash"
down_revision = "0032_foreign_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM reset_tokens WHERE used OR expires <= now()")
    op.execute(
        "UPDATE reset_tokens SET token = encode(sha256(convert_to(token, 'UTF8')), 'hex') "
        "WHERE token !~ '^[0-9a-f]{64}$'")


def downgrade():
    raise NotImplementedError(
        "the raw tokens are gone by design; a rollback leaves open links unusable (they expire in an hour) "
        "and old code would need the raw token — ask the patient to request a new link")
