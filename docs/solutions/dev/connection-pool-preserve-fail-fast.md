# A connection pool must not swallow a fatal DB error into a 300s hang

**2026-08-06 · db.py (`_get_pool`, `_connect`, `_diagnose_pool_timeout`) — commits 8895d4b → be9f6947**

## The problem

`db._connect()` was rewritten to use `psycopg_pool.ConnectionPool` instead of a bare
`psycopg.connect()` per call (commit 8895d4b). That silently changed the failure mode
of a bad or unreachable `DATABASE_URL`: before, `psycopg.connect()` raised the real
Postgres error (e.g. `invalid_catalog_name`) almost instantly; after, `getconn()`
blocked on the pool's default `timeout`, and the pool's background thread kept
retrying against the broken URL for `reconnect_timeout`'s default of **300s**.

This hung the whole backend test suite — a test deliberately points at a nonexistent
database to verify the fail-fast path — and would have hung production startup the
same way on a misconfigured `DATABASE_URL`, with ~30 background threads silently
hammering Postgres for 5 minutes before ever surfacing the real error.

## The fix

`_get_pool`: `ConnectionPool(timeout=3, reconnect_timeout=10, ...)` — checkout blocks
at most 3s, and the background reconnect thread gives up on a dead URL in 10s, not
300s. `reconnect_timeout` only bounds how long one retry attempt hammers a broken
URL before yielding; it doesn't hurt recovery from a real transient blip — every
`getconn()` that finds the pool empty triggers a fresh growth attempt.

`_connect()`: `pool.getconn(timeout=3)`. On `PoolTimeout`, `_diagnose_pool_timeout()`
runs **one** direct `psycopg.connect(url, connect_timeout=3)` outside the pool to
distinguish the two things a `PoolTimeout` can mean:
- **Pool healthy but busy** (all connections checked out) — the probe connects fine,
  closes unused, and the original "pool exhausted" message is returned.
- **DB actually down** (bad URL, bad auth, unreachable host) — the probe fails almost
  instantly with Postgres's real error, which is what gets raised. This case also
  evicts and closes the broken pool from `_pools` right there, so its background
  thread stops retrying and degrading Postgres for every other caller; the next
  `_connect()` on that URL builds a fresh pool and pays the same bounded ~3s once.

## Watch out — general rule

Wrapping a connection primitive in a pool/retry layer is not free: read the pool
library's retry/timeout defaults (psycopg_pool's `reconnect_timeout` defaults to
300s) and check what happens on a FATAL error, not just a transient one. Preserve
the original fail-fast contract explicitly — a fatal error (nonexistent DB, bad
auth) must still fail in seconds with the original exception class/message; only a
transient condition (pool momentarily full) should retry or wait. When in doubt,
add a direct out-of-pool probe to tell the two cases apart before raising.
