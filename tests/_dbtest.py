"""_dbtest.py — shared setUpModule/tearDownModule boilerplate for the
DB-backed test files.

Each Postgres-backed test module creates/drops its OWN throwaway database
(sana_turnos_test_<algo>) so the suites can run in the same process without
stepping on each other's schema/data (see docker-compose.yml / CI: one
Postgres server, many per-module databases). Before this file, that dance —
compute the maintenance URL, DROP/CREATE the test db, point DATABASE_URL at
it, clear db_schema._ensured, seed the schema, and the mirror-image teardown — was
copy-pasted at the top of ~28 test files. A new file that got any piece of
it wrong (skipped `db_schema._ensured.clear()`, forgot to point DATABASE_URL back
at the real base in tearDown) would silently keep reusing a PREVIOUS
module's cached schema/connection state — `db_schema._ensured` is a
process-wide cache, so a stale entry leaks across modules within the same
`unittest discover` run, not just within one file.

Not named test_*.py on purpose: unittest's discovery (`-p "test_*.py"`)
must not try to collect this as a test module itself.

Usage — keeps every existing call site (`HAVE_DB`, `DB_SKIP_REASON`,
`_TEST_URL`, `_MAINT_URL`, `self.skipTest(DB_SKIP_REASON or "...")`)
working unchanged; only the setUpModule/tearDownModule/_base_url block at
the top of the file collapses to this:

    import _dbtest

    _db = _dbtest.PerModuleDb("sana_turnos_test_catalogo",
                               seed=lambda: db_catalog.catalog_list("centros"))

    def setUpModule():
        global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
        _db.set_up()
        HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
        _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url

    def tearDownModule():
        _db.tear_down()

`seed` is whatever no-arg call the module used to "crear el esquema" (any
`db.*` call that touches the connection is enough — `db.py` creates tables
lazily on first use). Defaults to `db_catalog.catalog_list("centros")`, the most
common one.

Per-RUN ownership (2026-08-09, after the Tier 4 gate incident): the module
name is only the PREFIX of the real database name — every process appends
one run token (`t<hex-epoch>p<pid>r<rand>`, same stamp shape
`scripts/run_routes_tests.sh` mints for the routes tier), so two concurrent
invocations (a full lane + a reviewer agent, two sibling sessions) own
DISJOINT databases and one run's `DROP ... WITH (FORCE)` can no longer
terminate the other's live connections (the `AdminShutdown` + missing-rows
collision documented in docs/solutions/dev/
lineas-db-de-test-estrictamente-en-serie.md). A run killed before its
teardown (SIGKILL, power loss) leaves its databases behind; the first
`set_up()` of a LATER run reclaims them — staleness is decided from the
embedded stamp alone, only ever for session-free databases older than 2 h,
with a no-FORCE drop so a racing live connection makes Postgres refuse
instead of being terminated (same ladder as
`_routes_fixture.reclaim_stale_run_databases`).
"""

import os
import re
import secrets
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import _env
import _pg_sessions
from app.core import db
from app.core import db_catalog
from app.core import db_schema

# One token per PROCESS, minted at import: every PerModuleDb in the same run
# shares it (the ~30 modules of one `unittest discover` already differ by
# prefix), while a second concurrent run — even one started the same second —
# differs by pid. Stamp shape matches the routes runner's so staleness is
# decidable from the database name alone (see module docstring).
_RUN_TOKEN = "t%xp%dr%d" % (int(time.time()), os.getpid(), secrets.randbelow(1000))

# What a run-scoped _dbtest database name looks like. The token is anchored at
# the END, so routes-tier names (`sana_turnos_test_routes_<token>_w<n>`) and
# the legacy fixed names (no stamp) can never match.
_SCOPED_SHAPE = re.compile(
    r"^(sana_turnos_test_[a-z0-9_]+)_(t[0-9a-f]{1,12}p[0-9]+r[0-9]+)$")
_STAMP_RE = re.compile(r"^t([0-9a-f]{1,12})p[0-9]+r[0-9]+$")
_STALE_MAX_AGE_SECONDS = 2 * 60 * 60
_reclaimed_this_process = False


def run_scoped(base_name):
    """`<base_name>_<run token>` — the ONE place a per-run database name is
    composed. Also for the ad-hoc throwaway databases some suites create
    outside PerModuleDb (test_waitlist_queue.py's schema probes,
    test_integrity_races.py's bootstrap race): name them through this and
    they get the same collision-free per-run ownership. Raises immediately
    on a name Postgres would silently truncate at 63 chars — truncation
    would cut the token, the exact part that makes runs disjoint."""
    name = f"{base_name}_{_RUN_TOKEN}"
    if len(name) > 63:
        raise ValueError(
            f"test database name {name!r} is {len(name)} chars; Postgres "
            "truncates identifiers at 63, which would cut the run token. "
            f"Shorten the base name (max {63 - len(_RUN_TOKEN) - 1} chars).")
    return name


def _token_epoch(token):
    m = _STAMP_RE.match(token or "")
    return int(m.group(1), 16) if m else None


def _reclaimable(name, live_sessions, now, max_age_seconds):
    """The reclamation DECISION, pure and testable: True only for an exact
    scoped-shape name whose token is not the CURRENT run's, whose stamp is
    older than `max_age_seconds` (future-dated = fresh: clock skew), and
    with zero live sessions (an autovacuum worker is not one, see
    `_pg_sessions`). Everything else survives."""
    m = _SCOPED_SHAPE.match(name)
    if not m:
        return False
    token = m.group(2)
    epoch = _token_epoch(token)
    return (token != _RUN_TOKEN and epoch is not None
            and now - epoch >= max_age_seconds and not live_sessions)


def reclaim_stale_databases(maint_url, max_age_seconds=_STALE_MAX_AGE_SECONDS,
                            now=None, prefix=None):
    """Drop _dbtest databases orphaned by a run that died before its
    tear_down. Selection ladder (everything else SURVIVES): exact scoped
    shape only; never the CURRENT run's token; only a stamp older than
    `max_age_seconds` (future-dated = fresh, clock skew); only with zero
    live sessions, and the DROP goes WITHOUT `FORCE` so a connection racing
    in makes Postgres refuse (ObjectInUse → skipped as live, never
    terminated). Returns the names dropped.

    `prefix` narrows the sweep to names starting with it — for focused
    proofs that lower `max_age_seconds` (test_dbtest_run_isolation.py's
    real-SIGKILL recovery test): an age-0 server-wide sweep could reclaim a
    LIVE sibling run's database in the moment it has no session, the exact
    collision class this module exists to prevent. Production callers
    (`_reclaim_once`) never pass it."""
    if now is None:
        now = time.time()
    import psycopg
    from psycopg import sql
    dropped = []
    with psycopg.connect(maint_url, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database WHERE datname ~ %s",
                        (_SCOPED_SHAPE.pattern,))
            names = [r[0] for r in cur.fetchall()
                     if prefix is None or r[0].startswith(prefix)]
            # An autovacuum worker on the orphan is not a run (_pg_sessions).
            sessions = _pg_sessions.by_database(cur, names)
        for n in sorted(names):
            if not _reclaimable(n, sessions.get(n, 0), now, max_age_seconds):
                continue
            try:
                with c.cursor() as cur:
                    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}")
                                .format(sql.Identifier(n)))
                dropped.append(n)
            except psycopg.errors.ObjectInUse:
                continue  # a session raced in — it's live, leave it alone
    if dropped:
        print("_dbtest: reclaimed %d stale test database(s): %s"
              % (len(dropped), ", ".join(dropped)), file=sys.stderr)
    return dropped


def _reclaim_once(maint_url):
    """Best-effort, once per process, before the first database of a run is
    created. A failure here never blocks the run (our own database is
    token-unique regardless) — but it is REPORTED, never swallowed silently:
    orphan buildup with a mute sweep would look like health."""
    global _reclaimed_this_process
    if _reclaimed_this_process:
        return
    _reclaimed_this_process = True
    try:
        reclaim_stale_databases(maint_url)
    except Exception as e:  # noqa: BLE001
        print(f"_dbtest: stale-database reclamation failed ({e!r}); "
              "continuing — this run's databases are token-unique anyway",
              file=sys.stderr)


# --- Foreign keys (Alembic 0032): children first --------------------------------
#
# The database refuses to delete a parent that still has rows under it, and to
# insert a child whose parent does not exist. Tests that used to delete a
# request or hang a session on a DNI with no account go through these.

def ensure_user(dni, cur=None):
    """A bare patient account for `dni` (idempotent) — a session, a reset token,
    a dependent link or a lab upload needs its account row to exist."""
    def run(c):
        c.execute("INSERT INTO users (dni, first_name, last_name, pass_hash) "
                  "VALUES (%s, 'Test', 'Fixture', 'x') ON CONFLICT (dni) DO NOTHING", (dni,))
    if cur is not None:
        run(cur)
        return
    with db._connect() as conn, conn.cursor() as c:
        run(c)


def delete_booking_requests(cur, where="TRUE", params=()):
    """DELETE FROM booking_requests WHERE <where>, after everything under those
    requests: attendance/appointment history, slot holds, waiting-list entries
    (and their offers), interviews (and their consultations); a health profile
    that cites one of those interviews keeps its row and loses the link."""
    ids = "SELECT id FROM booking_requests WHERE " + where
    interviews = "SELECT id FROM interviews WHERE request_id IN (" + ids + ")"
    waits = "SELECT id FROM waitlist WHERE request_id IN (" + ids + ")"
    for sql in (
        "DELETE FROM consultations WHERE interview_id IN (" + interviews + ")",
        "UPDATE patient_health_profile SET source_interview_id = NULL "
        "WHERE source_interview_id IN (" + interviews + ")",
        "DELETE FROM interviews WHERE request_id IN (" + ids + ")",
        "DELETE FROM appointment_offers WHERE waitlist_id IN (" + waits + ")",
        "DELETE FROM waitlist WHERE request_id IN (" + ids + ")",
        "DELETE FROM attendance_events WHERE request_id IN (" + ids + ")",
        "DELETE FROM appointment_events WHERE request_id IN (" + ids + ")",
        "DELETE FROM slot_holds WHERE request_id IN (" + ids + ")",
    ):
        cur.execute(sql, params or None)
    cur.execute("DELETE FROM booking_requests WHERE " + where, params or None)


class PerModuleDb:
    """One throwaway Postgres database for the lifetime of a test module,
    named per run — see the module docstring's "Per-RUN ownership" block."""

    def __init__(self, test_db_name, seed=None):
        self.base_name = test_db_name
        self.test_db_name = run_scoped(test_db_name)
        self._seed = seed or (lambda: db_catalog.catalog_list("centros"))
        self.have_db = False
        self.skip_reason = ""
        self.test_url = ""
        self.maint_url = ""
        self._original_url = None

    def _base_url(self):
        env_file = db_schema._load_env_file()
        return (os.environ.get("TEST_DATABASE_URL")
                or os.environ.get("DATABASE_URL")
                or env_file.get("TEST_DATABASE_URL")
                or env_file.get("DATABASE_URL"))

    def set_up(self):
        self._original_url = self._base_url()
        if not self._original_url:
            # No hardcoded fallback DSN on purpose (audit item 0.1 — no password
            # in the repo). Degrade the same way a Postgres-connect failure below
            # does: skip with a clear message instead of raising.
            self.skip_reason = (
                "TEST_DATABASE_URL no configurada (ni DATABASE_URL) — definí "
                "TEST_DATABASE_URL en el entorno o en .env para correr los tests "
                "que usan Postgres."
            )
            return
        p = urlsplit(self._original_url)
        self.maint_url = urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))
        self.test_url = urlunsplit(
            (p.scheme, p.netloc, "/" + self.test_db_name, p.query, p.fragment))
        try:
            import psycopg
            _reclaim_once(self.maint_url)
            with psycopg.connect(self.maint_url, autocommit=True, connect_timeout=5) as c:
                with c.cursor() as cur:
                    cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % self.test_db_name)
                    cur.execute("CREATE DATABASE %s" % self.test_db_name)
            # Process-wide on purpose, and never undone: see tear_down below.
            _env.process_set(DATABASE_URL=self.test_url)
            db_schema._ensured.clear()
            db.close_pools()
            self._seed()
            self.have_db = True
        except Exception as e:  # noqa: BLE001
            self.skip_reason = f"Postgres no accesible: {e}"

    def tear_down(self):
        if not self.have_db:
            return
        try:
            import psycopg
            # Fiel al comportamiento original de los 28 archivos que este
            # helper reemplaza: llama a _base_url() DE NUEVO acá (no memoiza
            # self._original_url), y en ese momento os.environ["DATABASE_URL"]
            # todavía apunta a la base de test recién creada — así que esto
            # es, en la práctica, un no-op que deja DATABASE_URL apuntando a
            # la base de test (que se dropea dos líneas más abajo). Un test
            # module sin DB propia que corra justo después (p.ej.
            # test_turnstile.py) hereda ese DATABASE_URL roto y sus llamadas
            # a la base fallan rápido — turnstile.encendido() lo trata como
            # "base caída" y cae al default seguro. Restaurar la URL real acá
            # sonaba más "correcto" pero filtraba el estado de la base de dev
            # real a esos tests (comprobado: rompía test_turnstile.py al
            # migrar a este helper). No relitigar sin auditar todo lo que
            # depende de esta fuga.
            # Deliberate no-op re-assignment, NOT self._base_url() — see the
            # comment above. Now that TEST_DATABASE_URL can be a persistent env
            # var (unaffected by the os.environ override in set_up), calling
            # _base_url() again here would resolve to the REAL dev/test URL
            # instead of leaving DATABASE_URL pointed at the throwaway db, which
            # is exactly the leak test_turnstile.py relies on.
            _env.process_defaults(DATABASE_URL=self.test_url)
            db_schema._ensured.clear()
            # Cierra el pool de ESTA base de test antes de dropearla: si no,
            # sus hilos de fondo (min_size) siguen vivos reintentando
            # reconectar contra una base que ya no existe hasta que el
            # proceso de test termina — ~30 módulos por corrida, ~30 pools
            # zombis si no se hace esto.
            db.close_pools()
            with psycopg.connect(self.maint_url, autocommit=True, connect_timeout=5) as c:
                with c.cursor() as cur:
                    cur.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % self.test_db_name)
        except Exception:  # noqa: BLE001
            pass
