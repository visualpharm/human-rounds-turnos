"""Calendar-feed tokens: one formula for the patient's and the doctor's address.

A calendar address (/feed/appointments/<token>.ics for a patient,
/feed/doctor/<token>.ics for a doctor) is a bearer credential: whoever holds it
reads the appointments. The database keeps only the SHA-256 hex of the token
(`users.feed_token`, `operators.feed_token`) and its last 4 characters
(`feed_token_hint`), so a dump or a backup hands out no working address; the
token is 128 random bits, so a plain hash is enough (no salt, no slow KDF) and
the lookup stays an indexed equality. The full address is shown once, when it
is created or replaced (2026-11-06, alembic 0040).

A token still in the clear is one the previous build wrote while both builds
served during a deploy, after 0040 had run (every issuer ever used
`token_urlsafe(16)`: 22 characters, never the 64-hex shape of a hash). It is
hashed in place — never replaced — on the next open of the address or the
next calendar poll, so a subscription made in that overlap keeps working.
"""
import hashlib
import re
import secrets

import sqlalchemy as sa

from app.core import sqlcore

# The stored shape: what alembic 0040's guard and this module's repair skip.
_DIGEST = re.compile(r"[0-9a-f]{64}")
_DIGEST_SQL = "^[0-9a-f]{64}$"


def digest(token):
    """The stored form of a token: SHA-256 hex of its UTF-8 bytes, the same
    expression alembic 0040 applies in SQL."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def new():
    """A fresh token and the column values that store it:
    (token, {"feed_token": <digest>, "feed_token_hint": <last 4>})."""
    token = secrets.token_urlsafe(16)
    return token, {"feed_token": digest(token), "feed_token_hint": token[-4:]}


def hash_in_place(cur, t, key):
    """Hash the row's token where it is still in the clear (alembic 0040's
    UPDATE for one row; the hint is taken from the raw token, so a stale one
    left by the previous build's rotation is corrected). A no-op on a hashed,
    empty or missing token."""
    sqlcore.run(cur, sa.update(t).where(
        key, t.c.feed_token.is_not(None), t.c.feed_token != "",
        t.c.feed_token.op("!~")(_DIGEST_SQL),
    ).values(feed_token=sa.func.encode(sa.func.sha256(sa.func.convert_to(t.c.feed_token, "UTF8")), "hex"),
             feed_token_hint=sa.func.right(t.c.feed_token, 4)))


def heal_raw(cur, t, token):
    """On a lookup miss: the token may be stored in the clear (see the module
    docstring). Hash that row in place and answer True so the caller looks it
    up again. A value of the hash's own shape is never compared raw — a leaked
    hash must not work as an address."""
    if not token or _DIGEST.fullmatch(str(token)):
        return False
    row = sqlcore.run(cur, sa.update(t).where(t.c.feed_token == str(token)).values(
        feed_token=digest(token), feed_token_hint=str(token)[-4:]).returning(t.c.feed_token)).fetchone()
    return row is not None
