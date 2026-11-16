# Fail-fast per call isn't enough — cache "this URL is broken" too

**2026-08-06 · db.py (`_broken_urls`, `_connect`, `close_pools`) — commits be9f6947 → de8471fd → d263eebb**

## The problem

[connection-pool-preserve-fail-fast.md](connection-pool-preserve-fail-fast.md) (same day,
earlier) made a single `_connect()` call against a dead `DATABASE_URL` fail in ~3s instead
of hanging 300s, and claimed the next call on that URL "pays the same bounded ~3s once."
That claim was wrong: `_diagnose_pool_timeout` evicts and closes the broken pool, but the
NEXT `_connect()` call on the same URL calls `_get_pool()` again, which finds no cached
pool and builds a brand-new `ConnectionPool` from scratch — so it pays the full
`getconn(timeout=3)` checkout wait AGAIN, every single time, forever, for as long as the
URL stays broken.

This matters because `test/_dbtest.py`'s `PerModuleDb.tear_down()` *deliberately* leaves
`DATABASE_URL` pointed at the database it just dropped (documented there, needed so
`test_turnstile.py` sees "DB down" and exercises its fallback — restoring the real dev
URL instead was tried before and broke that test). Every test module that runs after a
`_dbtest`-fixture module, before the next one calls its own `set_up()`, inherits that
broken URL. Reproduced concretely: `scripts/ci_local.sh`'s backend gate
(`python3 -m unittest discover -s test -p test_*.py`, ~3255 tests) never finished — it
was still logging `FATAL: database "sana_turnos_test_housekeeping" does not exist` in a
loop after 300s+ and had to be killed, even with the be9f6947 fail-fast fix already in
place. The fail-fast fix was necessary (it makes the error visible instead of a silent
300s hang) but not sufficient — the *cost per call* was still real and it's paid once per
`_connect()`, and a module with dozens of assertions each touching the DB pays it dozens
of times.

## The fix

Added a process-wide negative cache, `_broken_urls: dict[url, (expires_at, message)]`
with `_BROKEN_URL_TTL = 5.0` seconds. `_connect()` checks it BEFORE touching `_get_pool`
or the pool machinery at all: if this exact URL failed less than `_BROKEN_URL_TTL`
seconds ago, raise `DbUnavailable` with the same cached message immediately — no new
`ConnectionPool`, no `getconn()` wait, no network at all. `_diagnose_pool_timeout`
populates the cache the moment it confirms case (b) ("DB actually down", not just "pool
busy"). `close_pools()` clears `_broken_urls` alongside `_pools` — a test module that
just created its OWN fresh, healthy database must not inherit the previous module's
"this is broken" verdict.

Net effect: only the FIRST caller after a URL goes bad pays the ~3s probe; every other
caller within the next 5s fails instantly. Verified end to end: the full backend suite
(3255 tests) went from "does not terminate" to `OK` in ~213-219s, exit 0, 0 skipped.

Also folded in while touching this code: `_PooledConn` gained a real `.close()` (it only
had `__enter__`/`__exit__` before; call sites and tests that call `conn.close()` directly
instead of using `with` were hitting `AttributeError` since the pooling migration).

## Watch out — general rule

A "fail fast instead of hang" fix bounds the cost of ONE call. It does not make repeated
calls against the same known-broken target cheap — if the broken state can realistically
persist (a dropped test DB, a misconfigured URL left in place, a real outage), anything
that gets called in a loop or across many call sites needs its OWN short-TTL negative
cache in front of the retry/pool layer, not just a bounded timeout inside it. Size the TTL
to the realistic lifetime of the bad state (here: a few seconds, since a healthy module's
`set_up()` clears it as soon as it runs) — long enough to actually save the repeated cost,
short enough that a real recovery (Postgres coming back) is picked up promptly.
