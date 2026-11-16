"""_routes_fixture.py — disposable-fixture + parallel-safety layer for the
routes tier (Tier 3 Area 2, `docs/roadmap/tier3-harness-design.md`, slice 3).

WHAT THIS TIER IS FOR (see the design doc for the full "why," repeated here
only as a one-line reminder): fast HTTP-level checks — status/JSON/headers/
role-gating for a real `server.Handler` route — meant to run in the
hundreds, in parallel, at pre-commit speed. It does NOT replace
`tests/test_*.py` (business logic) or `tests/e2e/*.spec.mjs` (real browser
flows); nothing moves here from either.

WHY THIS ISN'T `tests/_dbtest.py`: `_dbtest.py`'s `PerModuleDb` gives each
test MODULE its own database, created once, DROPPED at module teardown —
correct for a strictly serial `unittest discover` run, and (per its own
docstring) relies on a deliberate `DATABASE_URL` leak between modules that
a parallel runner would turn into a real bug. This file instead gives each
WORKER PROCESS one long-lived database for the run's whole lifetime, and
disposes between individual TEST METHODS with a TRUNCATE (cheap) instead of
a fresh CREATE DATABASE (slow) — see the design doc's "rejected
alternatives" for why per-test CREATE DATABASE and one-shared-DB-with-
savepoints were both ruled out.

Test files under `tests/routes/` are NOT discovered by the main
`python3 -m unittest discover -s tests -p "test_*.py"` command — verified
empirically (a probe file dropped in an `__init__.py`-less subdirectory of
`tests/` is invisible to that command on this Python), and left undiscovered
on purpose: mixing this tier's fixture lifecycle into the same process as
`_dbtest.py`'s per-module databases would resurrect exactly the
cross-module `DATABASE_URL` leak class `_dbtest.py`'s own comment warns
about. Run this tier only via `scripts/run_routes_tests.sh` (parallel) or
by pointing `python3 -m unittest discover -s tests/routes -p "test_*.py"` at
it directly (serial, worker 0's database, fine for local debugging).

Usage — a routes-tier test file is meant to fit in ~20 lines:

    import unittest
    from _routes_fixture import RouteTestCase, admin_cookie

    class TestHsiSyncGate(RouteTestCase):
        def test_no_cookie_401(self):
            status, _ = self._get_json("/api/admin/hsi-sync")
            self.assertEqual(status, 401)

        def test_admin_cookie_200(self):
            status, body = self._get_json(
                "/api/admin/hsi-sync", headers=admin_cookie())
            self.assertEqual(status, 200)

`RouteTestCase.setUp()` handles everything: binds this worker's disposable
database, truncates it clean, and a real `server.Handler` is already up
(inherited from `_livehttp.LiveServerMixin`) for the whole test class. A
Postgres-less environment degrades the same way `_dbtest.py` does —
`self.skipTest(...)`, never a hard failure — so this file is importable and
the tier is skippable, not crashy, on a machine with no Postgres.
"""

import json
import os
import re
import threading
import unittest
from urllib.parse import urlsplit, urlunsplit

import _pg_sessions

# Same env-hygiene every existing `_LiveServerMixin` call site did directly
# (test_http_server.py, test_module_runtime.py) — set here ONCE so a
# concrete `tests/routes/test_*.py` file doesn't have to repeat it: import
# order matters because `server.Handler` is imported lazily, the first time
# `LiveServer()` boots (see `_livehttp.py`), which can happen as soon as a
# test class's `setUpClass` runs.
import _env  # noqa: E402
# DATABASE_URL "": importable/bootable without Postgres yet
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_DNI_CLAIMS="0")
# site_auth.py's pre-launch gate only engages with BOTH set — never let a
# developer's real .env make every routes-tier request 401 before it even
# reaches the route under test.
_env.process_set(TURNOS_BASIC_AUTH_USER=None, TURNOS_BASIC_AUTH_PASS=None)
# accounts.py's `_auth_mode()` is DB-first with fallback to this env — a
# developer's real `.env` (this checkout's included, mirroring Pinamar's
# real production config) sets TURNOS_AUTH_MODE=renaper, which routes every
# POST /api/account/login through `_login_renaper` instead of the plain
# password path (caught by test_account_login.py's first run: every case
# came back 400 "proveedor_de_identidad_requerido" instead of the expected
# 401/200 password-path responses). `setdefault` here forces the site's own
# DEFAULT ("password remains the safe default", accounts.py's own
# docstring) for the whole routes tier, same hermetic-env doctrine as the
# TURNOS_SKIP_HSI/MAIL_ENABLED lines above — a test that wants to exercise
# the renaper branch on purpose can still override os.environ itself.
_env.process_defaults(TURNOS_AUTH_MODE="password")
# email_mailgun.py's `_cfg()` reads MAILGUN_API_KEY/DOMAIN straight out of
# `.env` (`config.get()`, no test-mode override) — on a developer's own
# checkout that's the REAL Human Rounds Mailgun account. A slice 4 coverage
# test (test_cancel_waitlist_trigger.py) that exercises the notification
# path caught this live: the first run genuinely sent an email to a
# throwaway `beto@example.com` fixture address via the real API. Force
# MAIL_ENABLED=0 for the whole routes tier — same "hermetic env" doctrine
# fixture.py's own docstring states for TURNOS_SKIP_HSI, just for the mail
# side effect instead of the HSI one. `setdefault`, not an unconditional
# set: a future test that WANTS to assert on a real send can still opt back
# in per-process.
_env.process_defaults(MAIL_ENABLED="0")
# admin_auth._signing_key() fail-closes with "TURNOS_ADMIN_SECRET o
# TURNOS_ADMIN_PASS deben estar configurados" (auth/admin_auth.py) when
# NEITHER is set — same proxy_trust.py pattern, deliberately no forgeable
# fallback literal. The managed "routes"/"local-ci" executor profile
# (scripts/turnos_managed_exec.py, `_db_key` allowlist) forwards only
# DATABASE_URL/TEST_DATABASE_URL to this process and points TURNOS_ENV_FILE
# at os.devnull, so under the managed gate no admin secret reaches routes
# tests at all — every `admin_cookie()` call (this file, below) would raise
# that RuntimeError before a single request is sent. `admin_cookie()` mints
# AND this same in-process LiveServer (_livehttp.py runs it on a thread, not
# a subprocess, so it shares this exact os.environ) verifies with the SAME
# key, so the value only has to be non-empty and stable for this one
# process — not a real credential. Same hermetic-env doctrine as
# TURNOS_AUTH_MODE/MAIL_ENABLED above: `setdefault`, so a developer's real
# `.env` admin password (when TURNOS_ENV_FILE isn't nulled) still wins.
_env.process_defaults({"TURNOS_ADMIN_SECRET": "routes-tier-hermetic-signing-key"})

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_schema  # noqa: E402
from _livehttp import LiveServerMixin, call as _http_call, response_ceiling  # noqa: E402


def _pg_connect_timeout():
    """Postgres connect timeout of this tier: the same ceiling as an HTTP
    answer (`_livehttp.response_ceiling()`), so a loaded machine is not read
    as «Postgres no accesible». A refused connection still fails at once."""
    return max(1, int(response_ceiling()))


def default_workers():
    """`min(os.cpu_count(), 4)` per the design doc — a small, FIXED pool,
    never one database per test case."""
    try:
        return max(1, min(os.cpu_count() or 1, 4))
    except Exception:  # noqa: BLE001
        return 1


def _worker_index():
    """Which worker database this PROCESS binds to. Set by
    `scripts/run_routes_tests.sh` via `TURNOS_ROUTES_WORKER`; defaults to 0
    for a lone `python3 -m unittest discover -s tests/routes` run — still
    correct, just no parallelism."""
    raw = (os.environ.get("TURNOS_ROUTES_WORKER") or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


_DB_PREFIX = "sana_turnos_test_routes_"


def _sanitize_token(raw):
    """The ONE place a run token is normalized: lowercased, reduced to
    `[a-z0-9]`, capped so the full db name stays well under Postgres's 63-char
    limit. Every path (the env read, an explicit `run_token=` argument, and
    the cleanup helper) goes through this, so construction and cleanup can
    never disagree on the resulting name."""
    return re.sub(r"[^a-z0-9]", "", str(raw or "").strip().lower())[:24]


def _run_token():
    """A per-INVOCATION token that run-scopes the worker database names, so
    two concurrent full-CI runs never share one. `scripts/run_routes_tests.sh`
    exports `TURNOS_ROUTES_RUN_ID` once per invocation and ALL of that run's
    workers inherit the same value — within-run parallelism is unchanged
    (workers still differ by index), but a second invocation gets a disjoint
    namespace, so its `DROP DATABASE ... WITH (FORCE)` can no longer terminate
    the first run's live worker connection (the `AdminShutdown` collision,
    2026-08-08). Empty (a lone `unittest discover` with no runner) falls back
    to the legacy unscoped name, preserving the single-process debugging path
    this file's module docstring describes."""
    return _sanitize_token(os.environ.get("TURNOS_ROUTES_RUN_ID"))


def _worker_db_name(worker, run_token=None):
    # An explicit run_token goes through the SAME sanitizer as the env path,
    # so a caller that constructs a name and a caller that cleans it up always
    # agree on the result.
    rid = _run_token() if run_token is None else _sanitize_token(run_token)
    if rid:
        return f"{_DB_PREFIX}{rid}_w{worker}"
    return f"{_DB_PREFIX}w{worker}"


def _base_url():
    """Same resolution order as `_dbtest.py`'s `_base_url()` — the
    environment wins, `.env` fills in what's missing. No hardcoded
    fallback DSN (no password in the repo)."""
    env_file = db_schema._load_env_file()
    return (os.environ.get("TEST_DATABASE_URL")
            or os.environ.get("DATABASE_URL")
            or env_file.get("TEST_DATABASE_URL")
            or env_file.get("DATABASE_URL"))


def _maint_url(base):
    """The Postgres maintenance-database URL for a base DSN — the ONE place
    this substitution lives, shared by bootstrap, exit cleanup and stale
    reclamation so their connection targets can never drift apart."""
    p = urlsplit(base)
    return urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))


def drop_run_databases(run_id=None):
    """DROP every routes-tier database belonging to ONE run token (the current
    `TURNOS_ROUTES_RUN_ID` by default). Called by `run_routes_tests.sh` on
    exit so run-scoped databases never accumulate. It filters by the EXACT
    run prefix, so it can only ever drop its own run's databases — never a
    concurrent sibling run's, which is the whole point of the run token. A
    blank token (legacy unscoped run) is a no-op: those fixed-name databases
    are reused DROP/CREATE by the next run, not left to pile up.

    Returns the list of database names dropped, and prints a one-line summary
    so the runner's exit cleanup is OBSERVABLE. It does NOT swallow errors: a
    real failure (Postgres unreachable, a database that won't drop) raises,
    and the runner's EXIT trap turns that into a NONZERO exit — a cleanup
    failure fails the "no stale buildup" gate rather than passing green with
    a warning (a test failure still dominates the exit code when both happen;
    see `scripts/run_routes_tests.sh`). The identifier passed to DROP is
    composed with `psycopg.sql.Identifier`, never string-interpolated, even
    though the names are our own construction."""
    import sys
    rid = _run_token() if run_id is None else _sanitize_token(run_id)
    if not rid:
        return []
    base = _base_url()
    if not base:
        print("drop_run_databases: no TEST_DATABASE_URL/DATABASE_URL set; "
              "nothing to clean", file=sys.stderr)
        return []
    maint = _maint_url(base)
    # EXACT predicate, not LIKE: `_DB_PREFIX` is full of underscores, and every
    # `_` is a single-char LIKE wildcard — so a LIKE would also match unrelated
    # databases with any character in those positions. An anchored POSIX-regex
    # (`~`) with the whole prefix regex-escaped and the worker suffix pinned to
    # `_w<digits>` selects only our own run's worker databases, nothing that
    # merely looks like them.
    pattern = "^" + re.escape(f"{_DB_PREFIX}{rid}_w") + r"[0-9]+$"
    import psycopg
    from psycopg import sql
    dropped = []
    with psycopg.connect(maint, autocommit=True, connect_timeout=_pg_connect_timeout()) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT datname FROM pg_database WHERE datname ~ %s", (pattern,))
            names = [r[0] for r in cur.fetchall()]
        for n in names:
            with c.cursor() as cur:
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                            .format(sql.Identifier(n)))
            dropped.append(n)
    print("drop_run_databases: dropped %d database(s) for run %r%s"
          % (len(dropped), rid, (": " + ", ".join(dropped)) if dropped else ""))
    return dropped


# ---- stale-namespace reclamation (2026-08-08 follow-up) -------------------
#
# The runner's EXIT trap drops its own run's databases, but SIGKILL or power
# loss bypasses any trap — those run-scoped databases would otherwise persist
# forever. The runner mints its token with an embedded creation stamp
# (`t<hex-epoch>p<pid>r<rand>`, scripts/run_routes_tests.sh), so staleness is
# decidable from the database NAME alone — no pg_stat_file, no superuser-only
# server-file access. Reclamation is deliberately narrow, in this order:
#   1. exact anchored shape only (`<prefix><token>_w<digits>`) — the legacy
#      fixed names (`..._w0`, no token) and lookalikes never match;
#   2. never the CURRENT run's token;
#   3. only a token whose stamp parses AND is older than `max_age_seconds`
#      (default 2 h — far beyond any legitimate run) — unstamped/foreign
#      tokens are conservatively left alone, and a future-dated stamp (clock
#      skew) is treated as fresh;
#   4. only with ZERO live sessions on the database, and the DROP is issued
#      WITHOUT `FORCE`, so a connection racing in between the check and the
#      drop makes Postgres refuse (ObjectInUse) — skipped as live, never
#      terminated.
# An unreachable Postgres at connect time is tolerated (warn + skip — the
# run's own tests need that same server and fail with a better signal);
# past the connect, failures other than the two benign races (in-use,
# already gone) raise, so the runner's fail-loud doctrine holds for
# reclamation too.

#
# A namespace whose runner is GONE (the `p<pid>` of its token no longer
# exists on this host) is reclaimed after `_DEAD_RUNNER_GRACE_SECONDS`
# instead of the 2 h (2026-11-08): one night left 46 dead runs' databases,
# 3 GB on the colima VM disk, because pre-commit's reaper TERMs the runner and
# KILLs it 10 s later while the runner's own trap is still reaping its
# workers, before its drop. The grace leaves a just-killed runner's trap time
# to finish; the session guard and the no-FORCE drop below still apply. A
# reused pid only makes a dead run look alive (spared until the 2 h rule).

_STAMP_RE = re.compile(r"^t([0-9a-f]{1,12})p([0-9]+)r[0-9]+$")
_STALE_RUN_MAX_AGE_SECONDS = 2 * 60 * 60
_DEAD_RUNNER_GRACE_SECONDS = 5 * 60


def _token_pid(token):
    """The runner pid embedded in a runner-minted run token, or None."""
    m = _STAMP_RE.match(token or "")
    return int(m.group(2)) if m else None


def _runner_gone(pid):
    """True only when no process with this pid exists on this host."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:  # EPERM: it exists, under another user
        return False
    return False


def _token_epoch(token):
    """Creation time (epoch seconds) embedded in a runner-minted run token,
    or None for any token that doesn't carry the runner's stamp shape (legacy
    `p<pid>r<rand>` tokens, hand-set debugging tokens, foreign strings)."""
    m = _STAMP_RE.match(token or "")
    if not m:
        return None
    return int(m.group(1), 16)


def reclaim_stale_run_databases(max_age_seconds=_STALE_RUN_MAX_AGE_SECONDS,
                                now=None):
    """DROP routes-tier databases orphaned by a runner that died without its
    EXIT trap (SIGKILL, power loss). See the block comment above for the
    exact selection ladder; everything not provably an old, session-free,
    non-current, stamp-carrying run namespace SURVIVES. Prints an observable
    summary and returns the list of names dropped.

    An UNREACHABLE Postgres (psycopg.OperationalError at connect) is a
    warn-and-return-[], never a raise: the tests about to run need that same
    server and will fail with a far better signal than an aborted-before-
    any-test run would give (independent review, 2026-08-08 — the exit-time
    cleanup lets a test failure dominate; a startup raise here had no test
    signal to dominate WITH). Any error past the connect — including an
    unexpected one mid-drop — still raises, same fail-loud doctrine as
    drop_run_databases."""
    import sys
    import time
    if now is None:
        now = time.time()
    base = _base_url()
    if not base:
        print("reclaim_stale_run_databases: no TEST_DATABASE_URL/DATABASE_URL "
              "set; nothing to reclaim", file=sys.stderr)
        return []
    current = _run_token()
    maint = _maint_url(base)
    # Same anchored-regex doctrine as drop_run_databases: `_DB_PREFIX` is full
    # of underscores, so LIKE is out. The token group is re-parsed in Python
    # with the identical pattern so selection and parsing cannot disagree.
    shape = re.compile("^" + re.escape(_DB_PREFIX) + r"([a-z0-9]+)_w[0-9]+$")
    import psycopg
    from psycopg import sql
    dropped, spared = [], []
    try:
        conn = psycopg.connect(maint, autocommit=True, connect_timeout=_pg_connect_timeout())
    except psycopg.OperationalError as e:
        print("reclaim_stale_run_databases: Postgres unreachable (%s); "
              "skipping reclamation — the run's own tests will surface a "
              "real outage" % e, file=sys.stderr)
        return []
    with conn as c:
        with c.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database WHERE datname ~ %s",
                        (shape.pattern,))
            names = [r[0] for r in cur.fetchall()]
            # ONE batched session census for every candidate (not one query
            # per database): counts read here are only a pre-filter — the
            # authoritative guard stays the no-FORCE DROP below, which
            # refuses if a session appears after this read.
            # An autovacuum worker is not a run holding it (_pg_sessions,
            # the same census as _dbtest's sweep).
            session_counts = _pg_sessions.by_database(cur, names)
        for n in sorted(names):
            m = shape.match(n)
            if not m:  # defensive: Postgres and Python regex must agree
                continue
            token = m.group(1)
            if current and token == current:
                spared.append((n, "current run"))
                continue
            born = _token_epoch(token)
            if born is None:
                spared.append((n, "unstamped token"))
                continue
            age = now - born
            if age < max_age_seconds and not (
                    age >= _DEAD_RUNNER_GRACE_SECONDS
                    and _runner_gone(_token_pid(token))):
                spared.append((n, "fresh (%.0fs old)" % max(age, 0)))
                continue
            sessions = session_counts.get(n, 0)
            if sessions:
                spared.append((n, "%d live session(s)" % sessions))
                continue
            try:
                with c.cursor() as cur:
                    # NO `WITH (FORCE)`: a session racing in after the check
                    # makes this refuse instead of terminating it.
                    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}")
                                .format(sql.Identifier(n)))
            except psycopg.errors.ObjectInUse:
                spared.append((n, "became live during reclaim"))
                continue
            dropped.append(n)
    print("reclaim_stale_run_databases: dropped %d stale database(s)%s%s"
          % (len(dropped),
             (": " + ", ".join(dropped)) if dropped else "",
             ("; spared %d" % len(spared)) if spared else ""))
    return dropped


class WorkerDb:
    """One disposable Postgres database for the lifetime of a worker
    PROCESS (never per test case, never per test file — see the module
    docstring). Bootstrapped exactly once per process: DROP/CREATE DATABASE
    + first schema application, guarded by a `pg_advisory_lock` scoped to
    THIS worker's db name so N worker processes starting at the same
    wall-clock moment never collide on Postgres's own catalog-level
    contention for concurrent `CREATE DATABASE` (reuses `db.py`'s exact
    `pg_advisory_lock(hashtext(...))` idiom — see `db.py:1027` — with a new
    lock name, not a new locking pattern). Disposal between tests is
    `truncate_all()`, a TRUNCATE, not a repeat of this bootstrap.
    """

    # In-process guard: several RouteTestCase subclasses in the SAME
    # process/module import share this ONE WorkerDb instance (`_WORKER`
    # below), so `ensure()` must be safe to call from every test's setUp —
    # the first caller does the real work, everyone after is a fast no-op.
    _lock = threading.Lock()

    def __init__(self, worker=None):
        self.worker = _worker_index() if worker is None else worker
        # Run-scoped (see _run_token / _worker_db_name): distinct per worker
        # WITHIN a run, disjoint ACROSS concurrent runs.
        self.db_name = _worker_db_name(self.worker)
        self.have_db = False
        self.skip_reason = ""
        self.test_url = ""
        self.maint_url = ""
        self._checked = False

    def ensure(self):
        with WorkerDb._lock:
            if self._checked:
                return
            self._checked = True
            base = _base_url()
            if not base:
                self.skip_reason = (
                    "TEST_DATABASE_URL no configurada (ni DATABASE_URL) — "
                    "definí una para correr el routes tier."
                )
                return
            p = urlsplit(base)
            self.maint_url = _maint_url(base)
            self.test_url = urlunsplit(
                (p.scheme, p.netloc, "/" + self.db_name, p.query, p.fragment))
            lock_key = f"turnos:routes-test-pool:{self.db_name}"
            try:
                import psycopg
                with psycopg.connect(self.maint_url, autocommit=True, connect_timeout=_pg_connect_timeout()) as c:
                    with c.cursor() as cur:
                        cur.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)", (lock_key,))
                    try:
                        with c.cursor() as cur:
                            cur.execute(
                                "DROP DATABASE IF EXISTS %s WITH (FORCE)" % self.db_name)
                            cur.execute("CREATE DATABASE %s" % self.db_name)
                    finally:
                        with c.cursor() as cur:
                            cur.execute(
                                "SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (lock_key,))
                _env.process_set(DATABASE_URL=self.test_url)  # this worker's db, for the process's lifetime
                db_schema._ensured.clear()
                db_catalog.catalog_list("centros")  # forces _ensure_schema() on the fresh db
                # A freshly created db has zero operators AND zero catalog
                # "config" rows -- setup.is_fresh_install() would 303 every
                # request to /setup (tests/e2e/fixture.py hits the same
                # thing and seeds a throwaway operator row per DB; that
                # trick doesn't survive HERE because truncate_all() wipes
                # `operators` before every test). setup.py's own in-process
                # latch is the right tool instead: flip it ONCE, for this
                # worker PROCESS's whole lifetime, never touching a table
                # truncate_all() would clear.
                from app.ops import setup as _setup
                _setup.mark_configured()
                self.have_db = True
            except Exception as e:  # noqa: BLE001
                self.skip_reason = f"Postgres no accesible: {e}"

    def bind_env(self):
        """Point THIS process's `DATABASE_URL` at the worker db. Idempotent
        and cheap — called from every test's `setUp` so a test (or a
        module imported by the route under test) that reassigns
        `os.environ["DATABASE_URL"]` can never drift the rest of the run
        off this worker's database."""
        if not self.have_db:
            return
        _env.process_set(DATABASE_URL=self.test_url)
        db_schema._ensured.add(self.test_url)

    def truncate_all(self):
        """Per-test disposal: ONE `TRUNCATE ... RESTART IDENTITY CASCADE`
        over every table `pg_tables` currently reports in `public` — never
        a hand-maintained table list, so a new table `db.py` adds is
        covered automatically, exactly the self-updating property the
        design doc calls for."""
        if not self.have_db:
            return
        import psycopg
        with psycopg.connect(self.test_url, autocommit=True, connect_timeout=_pg_connect_timeout()) as c:
            with c.cursor() as cur:
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                tables = [r[0] for r in cur.fetchall()]
                if tables:
                    quoted = ", ".join('"%s"' % t for t in tables)
                    cur.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")
        # Los roles del panel son datos de REFERENCIA, no datos de un test: sin
        # ellos el portón no reconoce ningún rol y toda ruta admin responde 403.
        # El TRUNCATE de arriba es deliberadamente ciego a qué tabla borra, así
        # que la siembra se repone acá (idempotente, ON CONFLICT DO NOTHING).
        db_operators.seed_builtin_roles()


# One instance per PROCESS — every RouteTestCase subclass in this worker
# shares it, which is what makes `ensure()`'s once-per-process bootstrap
# actually once.
_WORKER = WorkerDb()


class RouteTestCase(LiveServerMixin, unittest.TestCase):
    """Base class for a routes-tier test file. Gives every test method:

      - a real `server.Handler` on an ephemeral port (`LiveServerMixin`,
        one per TestCase class, extracted in `_livehttp.py`);
      - this worker's disposable Postgres database, truncated clean
        immediately before the test runs;
      - `self._get_json()` / `self._post_json()` / `self._delete()` HTTP
        helpers so a concrete test never touches `urllib` directly.

    A Postgres-less environment (no `TEST_DATABASE_URL`/`DATABASE_URL`, or
    Postgres unreachable) skips every test in the class with a clear
    reason — same degrade-not-crash contract as `_dbtest.py`.
    """

    db = _WORKER

    def setUp(self):
        self.db.ensure()
        if not self.db.have_db:
            self.skipTest(self.db.skip_reason or "Postgres no disponible para el routes tier")
        self.db.bind_env()
        self.db.truncate_all()

    # ---- HTTP helpers --------------------------------------------------

    def _request(self, method, path, headers=None, body=None):
        """Real HTTP request against this class's live server. Returns
        (status, headers_dict, raw_bytes). Never raises on a non-2xx
        status — `urllib.error.HTTPError` IS the response, same as
        `test_module_runtime.py`'s `_status()` helper treats it. Waiting
        for the answer is a ceiling, not part of the test: a server silent
        past `_livehttp.response_ceiling()` fails with `NoAnswer` naming
        this request (see `_livehttp`'s docstring)."""
        data = None
        req_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        status, resp_headers, raw = _http_call(
            self._base_url, method, path, data=data, headers=req_headers)
        # HTTP header names are case-insensitive; the legacy stdlib server
        # sent Title-Case while uvicorn (the native LiveServer transport)
        # sends lowercase. Title-casing here keeps every existing
        # `headers["Set-Cookie"]`/`headers.get("Content-Type")` call site
        # working unchanged across the topology retirement.
        return status, {k.title(): v for k, v in dict(resp_headers or {}).items()}, raw

    def _get_json(self, path, headers=None):
        status, _, raw = self._request("GET", path, headers=headers)
        return status, _parse_json(raw)

    def _post_json(self, path, payload=None, headers=None):
        status, _, raw = self._request("POST", path, headers=headers, body=payload or {})
        return status, _parse_json(raw)

    def _delete(self, path, headers=None):
        status, _, raw = self._request("DELETE", path, headers=headers)
        return status, _parse_json(raw)


def _parse_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def admin_cookie(usuario="routes.tier.test", rol="admin"):
    """`{"Cookie": "st_admin=<token>"}` for `usuario`/`rol`, minted the
    exact same way `admin_auth._mint_token()` mints a real login session —
    no DB round trip, no real login flow, same pattern already used by
    `test_booking_requests.py` (`admin_auth._mint_token(usuario, rol)`).
    """
    from app.auth import admin_auth
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}
