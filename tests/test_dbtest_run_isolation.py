"""test_dbtest_run_isolation.py — regression for the backend tier's
cross-run database isolation (2026-08-09).

Every DB-backed backend module owns a throwaway database through
`_dbtest.PerModuleDb`. Until this change those names were FIXED
(`sana_turnos_test_<module>`), so two concurrent invocations — a full lane
plus a reviewer agent, or two sibling sessions — shared them, and one run's
`DROP DATABASE ... WITH (FORCE)` terminated the other's live connections:
`psycopg.errors.AdminShutdown` plus missing job rows mid-suite (the Tier 4
HSI gate incident; same class as the routes tier's v6 collision, see
test_routes_run_isolation.py).

The fix appends one per-PROCESS run token (`t<hex-epoch>p<pid>r<rand>`) to
every _dbtest-owned database name, and reclaims databases orphaned by a
run killed before its teardown — stale by embedded stamp only, never the
current run's, never one with live sessions, and with a no-FORCE drop so a
racing connection makes Postgres refuse instead of being terminated.

Three layers of evidence:

`RunScopingTestCase` — pure-logic assertions on the name construction,
through the REAL `run_scoped()`/`PerModuleDb`, plus a real subprocess
proving a second process mints a DIFFERENT token (the actual concurrency
axis: runs are processes).

`ReclaimLadderTestCase` — pure-logic assertions on `_reclaimable()`, the
extracted reclamation decision: exact shape only, never the current run's
token (even ancient), never fresh or future-dated stamps, never with live
sessions, lookalikes (legacy fixed names, routes-tier names) untouchable.

`RunIsolationPostgresTestCase` — REAL-Postgres evidence (skips without a
database): two runs' same-module databases are disjoint; a sibling's
FORCE-drop of ITS database leaves the other run's live connection
untouched (the exact AdminShutdown hit); `reclaim_stale_databases` drops a
genuinely stale orphan but spares one with a live session — which stays
usable after the sweep; and a subprocess that opens a real ConnectionPool
and exits WITHOUT closing it terminates rc=0 with no
`PythonFinalizationError` / destructor stack trace on stderr (the atexit
`close_pools` registration in core/db.py, Python 3.14).

`RealProcessLifecycleTestCase` — the same claims exercised through REAL
runs, not simulated siblings: two concurrent processes complete the full
PerModuleDb lifecycle on the SAME base name, each pausing (blocked on its
own stdin) right after creating its database, so the parent can assert
BOTH databases exist AT THE SAME INSTANT before releasing either child —
genuine overlap, not just two Popen calls issued back to back — both then
finish green with disjoint names and, after normal teardown, neither
database survives; a sequential rerun is undisturbed by the previous run's
teardown, and its database is likewise gone afterward; and a run genuinely
SIGKILLed mid-work leaves its database behind as an orphan that
`reclaim_stale_databases` then recovers (age gate lowered to 0 there —
the just-killed stamp is seconds old, and the 2 h gating itself is what
`ReclaimLadderTestCase` proves). All child reads are wall-clock bounded (a
hung child cannot hang the test) and all child processes are reaped
idempotently on every exit path, including failure.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_dbtest_run_isolation.py"
"""

import os
import selectors
import subprocess
import sys
import threading
import time
import unittest
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

import _dbtest  # noqa: E402
import _pg_sessions  # noqa: E402
import _wait  # noqa: E402

_db =_dbtest.PerModuleDb("sana_turnos_test_dbtest_iso")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _other_token(epoch=None, pid=None, rand=7):
    """A syntactically valid run token that is NOT this process's — the
    stand-in for a sibling run in the assertions below."""
    if epoch is None:
        epoch = int(time.time())
    if pid is None:
        # Different from ours by construction, still a plausible pid.
        pid = (os.getpid() % 90000) + 1
        if ("p%d" % pid) in _dbtest._RUN_TOKEN:
            pid += 1
    return "t%xp%dr%d" % (epoch, pid, rand)


class RunScopingTestCase(unittest.TestCase):
    def test_scoped_name_is_base_plus_this_runs_token(self):
        name = _dbtest.run_scoped("sana_turnos_test_ejemplo")
        self.assertEqual(name,
                         "sana_turnos_test_ejemplo_" + _dbtest._RUN_TOKEN)
        m = _dbtest._SCOPED_SHAPE.match(name)
        self.assertIsNotNone(m, "scoped name must match the scoped shape")
        self.assertEqual(m.group(2), _dbtest._RUN_TOKEN)

    def test_same_process_is_stable_and_permoduledb_uses_it(self):
        # A module rebinds its OWN database, never a fresh one — and
        # PerModuleDb composes through run_scoped, not a re-derived copy.
        a = _dbtest.run_scoped("sana_turnos_test_ejemplo")
        b = _dbtest.run_scoped("sana_turnos_test_ejemplo")
        self.assertEqual(a, b)
        pm = _dbtest.PerModuleDb("sana_turnos_test_ejemplo")
        self.assertEqual(pm.test_db_name, a)
        self.assertEqual(pm.base_name, "sana_turnos_test_ejemplo")

    def test_token_stamp_decodes_to_this_processs_mint_time(self):
        epoch = _dbtest._token_epoch(_dbtest._RUN_TOKEN)
        self.assertIsNotNone(epoch)
        # Minted at import of _dbtest, so between process start and now.
        self.assertLessEqual(epoch, time.time() + 1)
        self.assertGreater(epoch, time.time() - 24 * 3600)

    def test_second_process_mints_a_different_token(self):
        # The real concurrency axis: a sibling RUN is a sibling PROCESS.
        # Same second, different pid -> disjoint names for every module.
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'tests'); import _dbtest; "
             "print(_dbtest.run_scoped('sana_turnos_test_ejemplo'))"],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        sibling = out.stdout.strip()
        self.assertRegex(sibling, _dbtest._SCOPED_SHAPE)
        self.assertNotEqual(sibling,
                            _dbtest.run_scoped("sana_turnos_test_ejemplo"))

    def test_overlong_base_name_raises_instead_of_truncating(self):
        # Postgres truncates identifiers at 63 chars, which would cut the
        # token — the exact part that makes runs disjoint. Fail loud.
        with self.assertRaises(ValueError):
            _dbtest.run_scoped("sana_turnos_test_" + "x" * 60)

    def test_every_tracked_base_name_fits_under_the_63_char_limit(self):
        # The guard in run_scoped protects call sites at RUNTIME; this scans
        # the tree so a too-long base name fails here, in one obvious place,
        # not as a mid-suite surprise in whatever module added it.
        import re
        test_dir = os.path.join(ROOT, "tests")
        pat = re.compile(r'(?:PerModuleDb\(\s*|run_scoped\(\s*)"([^"]+)"')
        for fn in sorted(os.listdir(test_dir)):
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(test_dir, fn), encoding="utf-8") as f:
                for base in pat.findall(f.read()):
                    with self.subTest(file=fn, base=base):
                        _dbtest.run_scoped(base)  # raises past the limit


class ReclaimLadderTestCase(unittest.TestCase):
    """`_reclaimable()` is the whole selection ladder, extracted pure so the
    dangerous half of the proof needs no server-wide sweep."""

    def _name(self, token, base="sana_turnos_test_ejemplo"):
        return "%s_%s" % (base, token)

    def test_stale_session_free_orphan_is_reclaimable(self):
        stale = self._name(_other_token(epoch=int(time.time()) - 3 * 3600))
        self.assertTrue(_dbtest._reclaimable(stale, 0, time.time(), 2 * 3600))

    def test_current_runs_token_is_never_reclaimable_even_ancient(self):
        ours = self._name(_dbtest._RUN_TOKEN)
        # Even judged from 10 years in the future: our own databases are
        # never candidates — teardown owns them, not the sweep.
        far_future = time.time() + 10 * 365 * 24 * 3600
        self.assertFalse(_dbtest._reclaimable(ours, 0, far_future, 2 * 3600))

    def test_fresh_and_future_dated_stamps_survive(self):
        now = time.time()
        fresh = self._name(_other_token(epoch=int(now) - 60))
        skewed = self._name(_other_token(epoch=int(now) + 3600))
        self.assertFalse(_dbtest._reclaimable(fresh, 0, now, 2 * 3600))
        self.assertFalse(_dbtest._reclaimable(skewed, 0, now, 2 * 3600))

    def test_live_sessions_always_win(self):
        stale = self._name(_other_token(epoch=int(time.time()) - 3 * 3600))
        self.assertFalse(_dbtest._reclaimable(stale, 1, time.time(), 2 * 3600))

    def test_an_autovacuum_worker_is_not_a_session_anything_else_is(self):
        # The census both sweeps take (_pg_sessions): an autovacuum worker on
        # an orphan's fresh tables once spared it past the SIGKILL test's
        # ceiling; DROP stops such a worker itself. Every other backend still
        # holds the database, DROP would refuse it.
        rows = [("orphan", "autovacuum worker"),
                ("held", "client backend"), ("held", "autovacuum worker"),
                ("bg", "background worker"), ("par", "parallel worker")]
        self.assertEqual(_pg_sessions.count(rows), {"held": 1, "bg": 1, "par": 1})
        stale = self._name(_other_token(epoch=int(time.time()) - 3 * 3600))
        sessions = _pg_sessions.count([(stale, "autovacuum worker")])
        self.assertTrue(_dbtest._reclaimable(stale, sessions.get(stale, 0), time.time(), 2 * 3600))

    def test_lookalikes_are_untouchable(self):
        now, age = time.time(), 0
        for name in (
            "sana_turnos_test_queue_worker",       # legacy fixed name, no stamp
            "sana_turnos_test_routes_abc123_w0",   # routes tier: its own sweep
            "sana_turnos_test_ejemplo_t123zp1r1",  # corrupt stamp (non-hex)
            "sana_turnos_prod",                    # not a test database at all
            "postgres",
        ):
            with self.subTest(name=name):
                self.assertFalse(_dbtest._reclaimable(name, 0, now, age))


class RunIsolationPostgresTestCase(unittest.TestCase):
    """Real-Postgres evidence. Every database this class creates embeds a
    token unique to this test process (pid + random), so two concurrent
    invocations of THIS regression are themselves collision-free — and the
    only reclaim call uses the default 2 h age gate, so a genuinely live
    sibling run's fresh databases are never candidates."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        self.psycopg = psycopg
        self._created = []
        p = urlsplit(_MAINT_URL)
        self._parts = p

    def tearDown(self):
        # Belt-and-suspenders drop — NOT swallowed: a failure here is a real
        # signal (a database that won't drop is a leak), let it surface.
        errors = []
        for n in self._created:
            try:
                self._drop_force(n)
            except Exception as e:  # noqa: BLE001
                errors.append("%s: %r" % (n, e))
        if errors:
            raise AssertionError("isolation teardown cleanup failed: "
                                 + "; ".join(errors))

    def _url_for(self, db_name):
        p = self._parts
        return urlunsplit((p.scheme, p.netloc, "/" + db_name, p.query,
                           p.fragment))

    def _maint(self):
        return self.psycopg.connect(_MAINT_URL, autocommit=True,
                                    connect_timeout=5)

    def _create(self, db_name):
        from psycopg import sql
        with self._maint() as c, c.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                        .format(sql.Identifier(db_name)))
            cur.execute(sql.SQL("CREATE DATABASE {}")
                        .format(sql.Identifier(db_name)))
        self._created.append(db_name)

    def _drop_force(self, db_name):
        from psycopg import sql
        with self._maint() as c, c.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                        .format(sql.Identifier(db_name)))

    def _exists(self, db_name):
        with self._maint() as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                        (db_name,))
            return cur.fetchone() is not None

    def test_sibling_force_drop_spares_the_other_runs_live_connection(self):
        # Two "runs" of the same module: ours and a simulated sibling's.
        base = "sana_turnos_test_dbtest_iso_x"
        name_ours = _dbtest.run_scoped(base)
        name_sib = "%s_%s" % (base, _other_token())
        self.assertNotEqual(name_ours, name_sib)
        self._create(name_ours)
        self._create(name_sib)

        # Hold a LIVE connection on OUR database — the exact thing the
        # fixed-name collision terminated with AdminShutdown.
        live = self.psycopg.connect(self._url_for(name_ours),
                                    autocommit=True, connect_timeout=5)
        try:
            # The sibling tears down ITS database, PerModuleDb-style
            # (FORCE, which is fine: the name is the ownership boundary).
            self._drop_force(name_sib)

            with live.cursor() as cur:
                cur.execute("SELECT 1")
                self.assertEqual(cur.fetchone()[0], 1)
            self.assertTrue(self._exists(name_ours))
            self.assertFalse(self._exists(name_sib))
        finally:
            live.close()

    # The orphans below are 30 minutes old and the sweep under test lowers its age gate to
    # 10 minutes, scoped by prefix. A 3 h orphan under the default 2 h gate is NOT safe on a
    # shared server: the first set_up of any sibling process (a parallel shard) sweeps every
    # stale database of the server and takes it between our create and our own sweep, which
    # then returns [] (seen once in the full CI). Under 2 h, only this test's sweep sees it.
    _ORPHAN_PREFIX = "sana_turnos_test_dbtest_iso_o_"
    _ORPHAN_AGE = 30 * 60
    _ORPHAN_GATE = 10 * 60

    def _orphan_names(self):
        stamp = int(time.time()) - self._ORPHAN_AGE
        return (self._ORPHAN_PREFIX + _other_token(epoch=stamp, rand=1),
                self._ORPHAN_PREFIX + _other_token(epoch=stamp, rand=2))

    def test_a_sibling_default_sweep_leaves_the_orphan_alone(self):
        # The sibling's first set_up runs the default sweep (2 h gate, whole server) at any
        # moment: here, between creating the orphan and sweeping it ourselves. Deterministic
        # stand-in for the parallel shard that used to take the orphan from under the test.
        name_stale, name_live = self._orphan_names()
        self._create(name_stale)
        self._create(name_live)
        dropped = _dbtest.reclaim_stale_databases(_MAINT_URL)
        self.assertNotIn(name_stale, dropped)
        self.assertNotIn(name_live, dropped)
        self.assertTrue(self._exists(name_stale))
        self.assertTrue(self._exists(name_live))

    def test_an_age_zero_sweep_scoped_to_one_name_spares_a_same_base_orphan(self):
        # Two parallel invocations of the SIGKILL test leave two orphans under one base, both
        # seconds old. Each one's age-0 sweep is scoped to its own full name, so neither takes
        # the other's: a sweep by the shared base would, and the loser saw [] and failed.
        base = "sana_turnos_test_dbtest_iso_k"
        stamp = int(time.time()) - 5
        mine = "%s_%s" % (base, _other_token(epoch=stamp, rand=3))
        theirs = "%s_%s" % (base, _other_token(epoch=stamp, rand=4))
        self._create(mine)
        self._create(theirs)
        dropped = _dbtest.reclaim_stale_databases(
            _MAINT_URL, max_age_seconds=0, prefix=mine)
        self.assertEqual(dropped, [mine])
        self.assertFalse(self._exists(mine))
        self.assertTrue(self._exists(theirs))

    def test_reclaim_drops_the_stale_orphan_and_spares_the_live_one(self):
        name_stale, name_live = self._orphan_names()
        self._create(name_stale)
        self._create(name_live)

        live = self.psycopg.connect(self._url_for(name_live),
                                    autocommit=True, connect_timeout=5)
        try:
            # A sibling process' default sweep landing right here must change nothing.
            _dbtest.reclaim_stale_databases(_MAINT_URL)
            # Scoped to this process' two names (stamp + pid are in the shared part), so another
            # invocation of this same test on the same server is never swept by ours.
            dropped = _dbtest.reclaim_stale_databases(
                _MAINT_URL, max_age_seconds=self._ORPHAN_GATE,
                prefix=os.path.commonprefix([name_stale, name_live]))
            self.assertIn(name_stale, dropped)
            self.assertNotIn(name_live, dropped)
            self.assertFalse(self._exists(name_stale))
            self.assertTrue(self._exists(name_live))
            # The spared database's connection is not just alive but USABLE
            # after the sweep — no-FORCE semantics, nothing terminated.
            with live.cursor() as cur:
                cur.execute("SELECT 1")
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            live.close()

    def test_process_exit_with_open_pool_is_silent_and_clean(self):
        # Regression for the Python 3.14 shutdown noise: a process that
        # opens a real ConnectionPool and exits WITHOUT closing it must
        # terminate rc=0 with NO PythonFinalizationError / stack trace —
        # core/db.py registers close_pools in atexit when the first pool is
        # created, so the pool's threads are joined while joining is legal.
        env = dict(os.environ)
        env["DATABASE_URL"] = _TEST_URL
        env.pop("TEST_DATABASE_URL", None)
        out = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning", "-c",
             "from app.core import db_catalog; db_catalog.catalog_list('centros')"],
            capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        for marker in ("PythonFinalizationError", "Exception ignored",
                       "Traceback", "ResourceWarning"):
            self.assertNotIn(marker, out.stderr,
                             "interpreter shutdown must be silent, got:\n"
                             + out.stderr)


# One-shot scripts for RealProcessLifecycleTestCase below. Base name arrives
# via TURNOS_TEST_BASE_NAME so the tracked-base-name scan above (which greps
# literal PerModuleDb("...") calls) is not what guards these — they are short
# by construction and run_scoped raises inside the child anyway.
# The stdin read after "DB=" is a release gate, not a protocol the child
# cares about: a caller that wants a genuine-overlap proof holds the pipe
# open until it has independently confirmed every sibling's database is
# live, then releases them; a caller that doesn't (the rerun test) just
# calls communicate(), which closes stdin immediately and releases the
# child right away — same script, no special-casing needed either way.
_LIFECYCLE_SCRIPT = """\
import os, sys
sys.path.insert(0, "tests")
sys.path.insert(0, os.getcwd())
import _dbtest
from app.core import db_catalog
pm = _dbtest.PerModuleDb(os.environ["TURNOS_TEST_BASE_NAME"])
pm.set_up()
if not pm.have_db:
    print("SKIP " + pm.skip_reason, flush=True)
    sys.exit(3)
print("DB=" + pm.test_db_name, flush=True)
sys.stdin.readline()
db_catalog.catalog_list("centros")
pm.tear_down()
print("OK", flush=True)
"""

_KILLED_RUN_SCRIPT = """\
import os, sys, time
sys.path.insert(0, "tests")
sys.path.insert(0, os.getcwd())
import _dbtest
from app.core import db_catalog
pm = _dbtest.PerModuleDb(os.environ["TURNOS_TEST_BASE_NAME"])
pm.set_up()
if not pm.have_db:
    print("SKIP " + pm.skip_reason, flush=True)
    sys.exit(3)
db_catalog.catalog_list("centros")
print("DB=" + pm.test_db_name, flush=True)
time.sleep(120)  # the parent SIGKILLs us here — tear_down never runs
"""


class RealProcessLifecycleTestCase(unittest.TestCase):
    """Real-run evidence for the three lifecycle claims the in-process tests
    above can only simulate: concurrency (two live runs, not one run plus a
    hand-built sibling name), rerun, and interrupted-process orphan recovery
    (a genuine SIGKILL, not a fabricated stale name)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        self.psycopg = psycopg
        # stream -> bytes read past the last line _read_line_bounded
        # returned (only ever non-empty if a single read() ever returns
        # more than one line at once, which the scripts below avoid by
        # pausing right after each line they care about). Carried forward
        # so a later proc.communicate() call — which reads the same raw
        # fd — never silently drops them.
        self._pending = {}

    def _spawn(self, script, base_name):
        env = dict(os.environ)
        env["TURNOS_TEST_BASE_NAME"] = base_name
        env.setdefault("TURNOS_FAKE_AI", "1")
        env.setdefault("TURNOS_SKIP_HSI", "1")
        return subprocess.Popen(
            [sys.executable, "-W", "error::ResourceWarning", "-c", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=ROOT, env=env)

    def _finish(self, proc, timeout=120):
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_and_reap(proc)
            self.fail("child pid %s did not exit within %ss (killed)"
                      % (proc.pid, timeout))
        out = self._prepend_pending(proc.stdout, out)
        if proc.returncode == 3:
            self.skipTest("child run skipped: %s" % out.strip())
        return out, err

    def _prepend_pending(self, stream, out):
        """Splice back any bytes _read_line_bounded read past the line it
        returned — communicate() reads the raw fd directly (bypassing the
        stream object entirely) and would otherwise never see them."""
        leftover = self._pending.pop(stream, b"")
        if not leftover:
            return out
        text = leftover.decode(stream.encoding or "utf-8",
                               stream.errors or "replace")
        return text + out

    def _kill_and_reap(self, proc, timeout=30):
        """Idempotent and unconditional: guarantees proc.poll() is not
        None when it returns — a test failure, not a silently unreaped
        child — rather than swallowing a communicate()/wait() that never
        actually confirmed the exit. Safe to call more than once, and
        safe whether the process is already dead or its pipes were
        already drained by an earlier communicate() call."""
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.communicate(timeout=timeout)
            except (subprocess.TimeoutExpired, ValueError):
                pass
        except ValueError:
            pass  # stdin/stdout already closed by an earlier communicate()
        if proc.poll() is None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            self.fail("child pid %s could not be reaped after "
                      "kill+communicate+wait" % proc.pid)

    def _read_line_bounded(self, stream, timeout):
        """Read one line from `stream` with a real wall-clock deadline,
        using a selector on the raw fd instead of a background reader
        thread: a timeout here leaves NOTHING running in the background —
        unlike a thread stranded inside a blocking readline() call, which
        would still be reading the same stream, unsynchronized, the
        instant a later communicate() call also starts reading it. Any
        bytes read past the returned line's newline are held in
        `self._pending` for the next call (or for _finish, via
        _prepend_pending) rather than discarded."""
        buf = self._pending.pop(stream, b"")
        newline = buf.find(b"\n")
        if newline != -1:
            line, rest = buf[:newline + 1], buf[newline + 1:]
            if rest:
                self._pending[stream] = rest
            return line.decode(stream.encoding or "utf-8",
                               stream.errors or "replace")

        deadline = time.time() + timeout
        fd = stream.fileno()
        sel = selectors.DefaultSelector()
        sel.register(fd, selectors.EVENT_READ)
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    if buf:
                        self._pending[stream] = buf
                    return None  # timed out — NOT the same as a real EOF
                if not sel.select(remaining):
                    continue  # spurious wake; the deadline check still guards
                chunk = os.read(fd, 4096)
                if not chunk:
                    # EOF: whatever is left in buf is the final,
                    # unterminated line (readline()'s own semantics).
                    return buf.decode(stream.encoding or "utf-8",
                                      stream.errors or "replace")
                buf += chunk
                newline = buf.find(b"\n")
                if newline != -1:
                    line, rest = buf[:newline + 1], buf[newline + 1:]
                    if rest:
                        self._pending[stream] = rest
                    return line.decode(stream.encoding or "utf-8",
                                       stream.errors or "replace")
        finally:
            sel.close()

    def _read_db_line(self, proc, timeout):
        """Block on `proc`'s stdout for its "DB=<name>" line, bounded by
        `timeout` total (not per-syscall), reaping the child on every
        failure path (SKIP, EOF, timeout) so nothing outlives the test."""
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self._kill_and_reap(proc)
                self.fail("child pid %s produced no DB= line within %ss"
                          % (proc.pid, timeout))
            line = self._read_line_bounded(proc.stdout, remaining)
            if line is None:
                continue  # spurious wake; deadline check above still guards
            if line == "":
                self._kill_and_reap(proc)
                self.fail("child pid %s exited before printing a DB= line"
                          % proc.pid)
            if line.startswith("SKIP"):
                self._kill_and_reap(proc)
                self.skipTest("child run skipped: %s" % line.strip())
            if line.startswith("DB="):
                return line.strip()[3:]
            # any other stray line is noise -- keep reading toward DB=

    def _extract_db_name(self, output):
        for line in output.splitlines():
            if line.startswith("DB="):
                return line[3:]
        self.fail("no DB= line in child output: %r" % output)

    def _exists(self, db_name):
        with self.psycopg.connect(_MAINT_URL, autocommit=True,
                                  connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                        (db_name,))
            return cur.fetchone() is not None

    def _live_sessions(self, db_name):
        """Sessions on `db_name` by the census the sweep itself takes
        (`_pg_sessions`): an autovacuum worker analysing the orphan's fresh
        tables is listed under it too, and once held this test's wait past
        its ceiling in a loaded full run."""
        with self.psycopg.connect(_MAINT_URL, autocommit=True,
                                  connect_timeout=5) as c, c.cursor() as cur:
            return _pg_sessions.by_database(cur, [db_name]).get(db_name, 0)

    def _activity(self, db_name):
        """What is still on `db_name`, for a failure message; never raises."""
        try:
            with self.psycopg.connect(_MAINT_URL, autocommit=True,
                                      connect_timeout=5) as c, c.cursor() as cur:
                cur.execute("SELECT pid, backend_type, state, wait_event_type, "
                            "wait_event, backend_start FROM pg_stat_activity "
                            "WHERE datname=%s", (db_name,))
                return cur.fetchall()
        except Exception as e:  # noqa: BLE001
            return "pg_stat_activity unreadable: %r" % (e,)

    def test_two_concurrent_real_runs_of_the_same_module_both_pass(self):
        # Real overlap, not inferred: each child pauses (blocked on its own
        # stdin) right after creating its database, so the parent confirms
        # BOTH databases exist AT THE SAME INSTANT before releasing either
        # one -- two Popen calls issued back to back is not itself proof
        # the runs overlapped; this assertion, taken while both children
        # are provably still paused, is.
        base = "sana_turnos_test_dbtest_conc"
        a = self._spawn(_LIFECYCLE_SCRIPT, base)
        b = self._spawn(_LIFECYCLE_SCRIPT, base)
        try:
            name_a = self._read_db_line(a, timeout=60)
            name_b = self._read_db_line(b, timeout=60)
            self.assertNotEqual(name_a, name_b,
                                "two concurrent runs must own disjoint"
                                " databases")
            self.assertTrue(self._exists(name_a))
            self.assertTrue(self._exists(name_b))
        except BaseException:
            self._kill_and_reap(a)
            self._kill_and_reap(b)
            raise

        # communicate() closes each child's stdin, releasing its barrier.
        out_a, err_a = self._finish(a)
        out_b, err_b = self._finish(b)
        self.assertEqual(a.returncode, 0, err_a)
        self.assertEqual(b.returncode, 0, err_b)
        self.assertIn("OK", out_a)
        self.assertIn("OK", out_b)
        self.assertFalse(self._exists(name_a),
                         "a normal run's teardown must drop its database")
        self.assertFalse(self._exists(name_b),
                         "a normal run's teardown must drop its database")

    def test_sequential_rerun_is_undisturbed_by_the_previous_teardown(self):
        base = "sana_turnos_test_dbtest_rerun"
        first = self._spawn(_LIFECYCLE_SCRIPT, base)
        out1, err1 = self._finish(first)
        self.assertEqual(first.returncode, 0, err1)
        self.assertIn("OK", out1)
        name1 = self._extract_db_name(out1)
        self.assertFalse(self._exists(name1),
                         "a normal run's teardown must drop its database")

        second = self._spawn(_LIFECYCLE_SCRIPT, base)
        out2, err2 = self._finish(second)
        self.assertEqual(second.returncode, 0, err2)
        self.assertIn("OK", out2)
        name2 = self._extract_db_name(out2)
        self.assertFalse(self._exists(name2),
                         "a normal run's teardown must drop its database")

    def test_sigkilled_run_leaves_an_orphan_a_later_run_reclaims(self):
        base = "sana_turnos_test_dbtest_kill"
        proc = self._spawn(_KILLED_RUN_SCRIPT, base)
        try:
            # Wall-clock bounded: a wedged child cannot hang this test.
            name = self._read_db_line(proc, timeout=60)
        finally:
            self._kill_and_reap(proc)  # SIGKILL -- tear_down never runs

        # The orphan is real: the database survived its owner's death.
        self.assertTrue(self._exists(name),
                        "a SIGKILLed run must leave its database behind")

        # Postgres notices the dead client asynchronously (the close travels
        # the VM's port forward); wait for the backend to reap the connection
        # so the sweep sees zero client sessions. Quiet machine: ~0.1 s; the
        # ceiling stretches with load because the forward runs on the same
        # busy machine.
        try:
            _wait.until(lambda: not self._live_sessions(name),
                        "the killed client's backend goes away", timeout=30,
                        interval=0.3, scale_with_load=True)
        except AssertionError as e:
            self.fail("%s; still on %s: %r" % (e, name, self._activity(name)))

        # The later run's sweep recovers it. Age gate lowered to 0: this
        # orphan's stamp is seconds old, and the 2 h threshold itself is
        # ReclaimLadderTestCase's claim, not this test's. Scoped by prefix
        # so the age-0 sweep can never touch a live sibling run's databases
        # (see reclaim_stale_databases' own docstring). The prefix is this
        # orphan's FULL name, not the shared base: a second invocation of this
        # same test (a parallel run) has its own orphan under the same base,
        # and a base-wide age-0 sweep takes it from under that run, which then
        # sees [] from its own sweep.
        #
        # An autovacuum worker may still be on the orphan: it is not counted
        # (_pg_sessions), and the no-FORCE DROP stops it, waiting ~5 s. Under
        # heavy load it can outlast that and the DROP is refused as "in use";
        # a later sweep takes the orphan then, which is the claim. So the
        # sweep is retried while the refusal is autovacuum's, and the test
        # still fails if the orphan is never reclaimed.
        def sweep():
            if name in _dbtest.reclaim_stale_databases(
                    _MAINT_URL, max_age_seconds=0, prefix=name):
                return True
            if self._live_sessions(name):
                self.fail("a session other than autovacuum holds the orphan: %r"
                          % (self._activity(name),))
            return False
        try:
            _wait.until(sweep, "a later run's sweep reclaims the orphan",
                        timeout=30, interval=1, scale_with_load=True)
        except AssertionError as e:
            self.fail("%s; still on %s: %r" % (e, name, self._activity(name)))
        self.assertFalse(self._exists(name))

    def test_read_line_bounded_timeout_reaps_child_with_no_leak(self):
        """Deterministic proof of the exact defect a reader-thread design
        can hit: a child that never writes anything makes
        _read_line_bounded time out; the fix must return control promptly
        AND leave nothing running in the background, so the
        _kill_and_reap that follows is never racing a stranded reader on
        the same stream when its own communicate() call reads it."""
        threads_before = threading.active_count()
        proc = self._spawn("import time\ntime.sleep(30)\n",
                           "sana_turnos_test_dbtest_timeout")
        started = time.time()
        try:
            line = self._read_line_bounded(proc.stdout, timeout=1.5)
            elapsed = time.time() - started
            self.assertIsNone(
                line, "a silent child must time out, not report EOF/a line")
            self.assertLess(
                elapsed, 5,
                "a timed-out bounded read must return promptly, not hang")
            self.assertEqual(
                threading.active_count(), threads_before,
                "a selector-based bounded read must not leave any"
                " background thread running past its own deadline")
        finally:
            self._kill_and_reap(proc)

        self.assertIsNotNone(
            proc.poll(), "child must be fully reaped after _kill_and_reap")

        # Idempotent: reaping an already-dead, already-drained child a
        # second time is a safe no-op, not an error.
        self._kill_and_reap(proc)
        self.assertIsNotNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
