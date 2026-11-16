"""_pg_sessions.py - which pg_stat_activity rows mean "a run still holds this test database".

One census for the two stale-database sweeps (`_dbtest.reclaim_stale_databases`,
`_routes_fixture.reclaim_stale_run_databases`); `scripts/lane_janitor.py` runs the same rule as SQL
(`NOT_A_RUN_SQL`, pinned by test_lane_janitor). Not named test_*.py: discovery must not collect it.

An autovacuum worker is listed under the database it is analysing, and a fresh test database has
just been written, so the worker is often there. It is not a run: DROP DATABASE stops it by itself
(Postgres signals autovacuum workers and waits about 5 s). Counted as a session, it spared an orphan
for as long as the worker lingered and turned the SIGKILL recovery test red in a loaded full run.
Every other backend type still counts: DROP refuses it, so a sweep must not try.
"""

NOT_A_RUN = "autovacuum worker"
NOT_A_RUN_SQL = "backend_type <> '%s'" % NOT_A_RUN

ACTIVITY_SQL = "SELECT datname, backend_type FROM pg_stat_activity WHERE datname = ANY(%s)"


def count(rows):
    """{datname: sessions} out of (datname, backend_type) rows, autovacuum workers left out."""
    out = {}
    for datname, backend_type in rows:
        if backend_type == NOT_A_RUN:
            continue
        out[datname] = out.get(datname, 0) + 1
    return out


def by_database(cur, names):
    """Sessions per database for `names` on the cursor's server (databases with none are absent)."""
    cur.execute(ACTIVITY_SQL, (list(names),))
    return count(cur.fetchall())
