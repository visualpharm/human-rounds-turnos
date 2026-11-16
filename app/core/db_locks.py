"""core/db_locks.py — the advisory locks of the persistence layer: the `hashtext(...)` key and the
transaction-level / session-level take, try and release.

The KEY is the contract (another process takes the same one), so every lock spells it the same way.

Split out of `core/db.py` (the DB split of 2026-10-30). It imports only `core/db_values.py`.
"""

import sqlalchemy as sa

from app.core import db_values
from app.core import sqlcore


def _lock_key(*pieces):
    """`hashtext(<piece> || <piece> ...)`, the 32-bit key an advisory lock is taken
    on. A `str` piece is a constant of the key and stays in the statement as a SQL
    literal; anything else is a Core expression (`_text_value(x)` binds a value)."""
    parts = [sa.literal_column("'%s'" % p) if isinstance(p, str) else p for p in pieces]
    joined = parts[0]
    for part in parts[1:]:
        joined = joined.op("||")(part)
    return sa.func.hashtext(joined)


def _advisory_xact_lock(cur, *pieces):
    """`SELECT pg_advisory_xact_lock(hashtext(...))`: waits for, then holds until the
    end of the caller's transaction, the lock named by the key pieces."""
    sqlcore.run(cur, sa.select(sa.func.pg_advisory_xact_lock(_lock_key(*pieces))))


def _advisory_key(key):
    """`hashtext(<key>)` over a bound text value: the key a lock is taken on. The KEY is the contract
    (another process takes the same one), so every lock below spells it the same way."""
    return sa.func.hashtext(db_values._text_value(key))


def _advisory_lock(cur, key):
    """Blocks until the SESSION-level lock `key` is held (released by `_advisory_unlock` or the
    end of the connection)."""
    sqlcore.run(cur, sa.select(sa.func.pg_advisory_lock(_advisory_key(key))))


def _try_advisory_lock(cur, key):
    """True when the SESSION-level lock `key` was free and is now held, False when another session
    holds it (never waits). `cur` yields dict rows."""
    return sqlcore.run(cur, sa.select(
        sa.func.pg_try_advisory_lock(_advisory_key(key)).label("acquired"))).fetchone()["acquired"]


def _try_advisory_xact_lock(cur, key):
    """Like `_try_advisory_lock`, but held until the end of the caller's transaction."""
    return sqlcore.run(cur, sa.select(
        sa.func.pg_try_advisory_xact_lock(_advisory_key(key)).label("acquired"))).fetchone()["acquired"]


def _advisory_unlock(cur, key, function="pg_advisory_unlock"):
    """Releases the SESSION-level lock `key`. `function` is only ever changed by the test seam that
    makes the unlock fail on purpose (a function that does not exist)."""
    sqlcore.run(cur, sa.select(getattr(sa.func, function)(_advisory_key(key))))
