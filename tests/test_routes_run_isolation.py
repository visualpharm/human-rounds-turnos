"""test_routes_run_isolation.py — regression for the routes-tier cross-run
database isolation (2026-08-08).

The routes tier gives every worker its OWN Postgres database so workers can
run in parallel. Until this change those names were the FIXED
`sana_turnos_test_routes_w0..w3`, so two concurrent full-CI invocations
shared them — and one run's `DROP DATABASE ... WITH (FORCE)` (issued when a
worker bootstraps its db) terminated the OTHER run's live worker connection,
surfacing as `psycopg.errors.AdminShutdown: terminating connection due to
administrator command` mid-`truncate_all()`. Multi-session local CI is the
enforcing path here, so that collision is a real defect, not a flake.

The fix run-scopes the names off a per-invocation token
(`TURNOS_ROUTES_RUN_ID`, exported once by `scripts/run_routes_tests.sh` and
shared by all of that run's workers). Two layers of evidence here:

`RoutesRunIsolationTestCase` — pure-logic assertions on the name
construction (no database), proving:
  1. within a run, workers still get DISTINCT names (parallelism preserved);
  2. across two runs, the two full worker-name sets are DISJOINT (no shared
     name to collide on);
  3. the same (run, worker) is STABLE (a worker rebinds its own db, never a
     fresh one);
  4. a blank token falls back to the legacy unscoped name (the lone
     `unittest discover` debugging path the fixture docstring describes);
  5. names stay within Postgres's 63-char identifier limit;
  6. the runner's EXIT-trap cleanup snippet (extracted verbatim from
     scripts/run_routes_tests.sh) reads the token as ENV DATA, so a hostile
     externally supplied `TURNOS_ROUTES_RUN_ID` cannot break or inject the
     cleanup command — the sanitizer reduces it to `[a-z0-9]` first.

`RoutesRunCleanupPostgresTestCase` — REAL-Postgres evidence (skips without a
database): two run tokens' databases are actually created, and one run's
cleanup — selecting by an anchored regex over the exact run prefix, never a
LIKE — drops ONLY its own. It does not terminate the other run's LIVE
connection (the exact `AdminShutdown` v6 hit) nor remove its database; each
run's databases are gone after its own cleanup; and lookalike databases (a
non-underscore where the prefix has one, a non-digit worker suffix) are
never selected.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_routes_run_isolation.py"
"""

import os
import sys
import unittest
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

import _routes_fixture as rf  # noqa: E402


def _dead_pid():
    """The pid of a child that has already exited and been reaped."""
    import subprocess
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


class RoutesRunIsolationTestCase(unittest.TestCase):
    def _names(self, run_id, n_workers=4):
        _env.set_env(self, TURNOS_ROUTES_RUN_ID=run_id)
        # Construct through the real WorkerDb so the test tracks whatever the
        # fixture actually binds, not a re-derived copy of the formula.
        return [rf.WorkerDb(worker=w).db_name for w in range(n_workers)]

    def test_within_run_workers_are_distinct(self):
        names = self._names("runalpha")
        self.assertEqual(len(names), len(set(names)))

    def test_two_runs_have_disjoint_worker_sets(self):
        a = set(self._names("runalpha"))
        b = set(self._names("runbravo"))
        self.assertEqual(a & b, set(),
                         "two concurrent runs share a worker db name -> they can "
                         "drop each other's live database")

    def test_same_run_and_worker_is_stable(self):
        # Fully deterministic — two EXPLICIT tokens, never reads whatever
        # TURNOS_ROUTES_RUN_ID the ambient environment happened to carry.
        _env.set_env(self, TURNOS_ROUTES_RUN_ID="runstable")
        a = rf.WorkerDb(worker=2).db_name
        b = rf.WorkerDb(worker=2).db_name
        self.assertEqual(a, b)  # same run + worker rebinds the SAME db
        _env.set_env(self, TURNOS_ROUTES_RUN_ID="runother")
        c = rf.WorkerDb(worker=2).db_name
        self.assertNotEqual(a, c)  # a different run, same worker -> different db

    def test_blank_token_falls_back_to_legacy_unscoped_name(self):
        _env.set_env(self, TURNOS_ROUTES_RUN_ID=None)
        self.assertEqual(rf.WorkerDb(worker=0).db_name, "sana_turnos_test_routes_w0")
        self.assertEqual(rf.WorkerDb(worker=3).db_name, "sana_turnos_test_routes_w3")

    def test_token_is_sanitized_and_length_bounded(self):
        # A messy token (uppercase, dashes, punctuation, overlong) still
        # yields a legal, bounded identifier — never SQL-injectable, never
        # over Postgres's 63-char limit.
        _env.set_env(self, TURNOS_ROUTES_RUN_ID="P12345-r6789; DROP DATABASE x;--" + "z" * 80)
        name = rf.WorkerDb(worker=3).db_name
        self.assertTrue(name.startswith("sana_turnos_test_routes_"))
        self.assertLessEqual(len(name), 63)
        self.assertRegex(name, r"^sana_turnos_test_routes_[a-z0-9]+_w3$")

    def test_cleanup_predicate_targets_only_its_own_run(self):
        # The drop helper selects by an anchored regex over the exact
        # run-scoped prefix (not LIKE — see drop_run_databases), so a sibling
        # run's databases never match. Assert the two prefixes are
        # non-overlapping at the name level; the real-Postgres cases below
        # prove the selection behavior end to end.
        _env.set_env(self, TURNOS_ROUTES_RUN_ID="runalpha")
        alpha = rf._worker_db_name(0)
        _env.set_env(self, TURNOS_ROUTES_RUN_ID="runbravo")
        bravo_prefix = "%s%s_w" % (rf._DB_PREFIX, rf._run_token())
        self.assertFalse(alpha.startswith(bravo_prefix))

    def test_runner_mints_a_fresh_token_even_with_one_inherited(self):
        """A CI shell can export TURNOS_ROUTES_RUN_ID at job level (real
        pattern, not per-step) — if the runner only minted a token when the
        variable was UNSET, two invocations under that same job would
        inherit the identical value and recreate the exact shared-database
        collision this mechanism exists to prevent. This extracts the
        ACTUAL minting lines from scripts/run_routes_tests.sh (no
        re-derived copy) and runs them TWICE as separate subprocesses, both
        started with the SAME pre-set TURNOS_ROUTES_RUN_ID, proving the
        runner disregards the inherited value and always exports a fresh
        one — so the two invocations' worker namespaces are disjoint."""
        import re as _re
        import subprocess
        script_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scripts", "run_routes_tests.sh")
        with open(script_path, encoding="utf-8") as fh:
            script = fh.read()
        m = _re.search(
            r'(TURNOS_ROUTES_RUN_ID="t\$\(printf[^\n]*\)p\$\$r\$\{RANDOM\}"\n'
            r'export TURNOS_ROUTES_RUN_ID)',
            script)
        self.assertIsNotNone(m, "run-id minting snippet not found in runner")
        mint_snippet = m.group(1)
        self.assertNotIn("if ", mint_snippet,
                         "minting must be unconditional — no [ -z ... ] guard")

        inherited = "sameforbothinvocations"
        results = []
        for _ in range(2):
            env = dict(os.environ)
            env["TURNOS_ROUTES_RUN_ID"] = inherited
            r = subprocess.run(
                ["bash", "-c", mint_snippet + '\necho "$TURNOS_ROUTES_RUN_ID"'],
                env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            results.append(r.stdout.strip())

        for got in results:
            self.assertNotEqual(got, inherited,
                                "runner kept the inherited token instead of minting fresh")
        self.assertNotEqual(results[0], results[1],
                            "two invocations sharing an inherited env var produced the "
                            "SAME token -> they would collide on worker database names")
        # The minted token carries a parseable creation stamp (what makes a
        # SIGKILL-orphaned namespace reclaimable later from its name alone),
        # and the stamp is the runner's actual launch time.
        import time as _time
        for got in results:
            born = rf._token_epoch(rf._sanitize_token(got))
            self.assertIsNotNone(
                born, "minted token %r carries no parseable creation stamp" % got)
            self.assertLess(abs(_time.time() - born), 300,
                            "minted stamp is not the runner's launch time")

    def test_runner_cleanup_snippet_is_injection_proof(self):
        """The runner's EXIT-trap cleanup once interpolated
        TURNOS_ROUTES_RUN_ID into Python source — a caller-supplied token
        with a quote could break or inject it. This extracts the ACTUAL
        single-quoted `python3 -c` source from scripts/run_routes_tests.sh
        (no re-derived copy) and runs it with a hostile externally supplied
        token: it must exit 0, must NOT execute the payload, and the
        sanitizer must reduce the token to [a-z0-9] before any SQL use."""
        import re as _re
        import subprocess
        import tempfile
        script_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scripts", "run_routes_tests.sh")
        with open(script_path, encoding="utf-8") as fh:
            script = fh.read()
        m = _re.search(r"python3 -c '\n(.*?)'\n", script, _re.S)
        self.assertIsNotNone(m, "cleanup python snippet not found in runner")
        src = m.group(1)
        self.assertNotIn("$", src, "cleanup snippet must contain no shell expansion")

        marker = os.path.join(tempfile.gettempdir(),
                              "routes-inject-proof-%d" % os.getpid())
        if os.path.exists(marker):
            os.unlink(marker)
        hostile = "x'; import os; open(%r,'w'); '" % marker
        env = dict(os.environ)
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["TURNOS_ROUTES_REPO"] = repo
        env["TURNOS_ROUTES_RUN_ID"] = hostile
        r = subprocess.run([sys.executable, "-c", src], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(marker),
                         "hostile token EXECUTED through the cleanup path")
        # The sanitizer ran: the observable summary carries the token reduced
        # to [a-z0-9] only (and capped), never the quotes/semicolons.
        self.assertIn("dropped 0 database(s)", r.stdout)
        tok = _re.search(r"for run '([^']*)'", r.stdout)
        self.assertIsNotNone(tok, r.stdout)
        self.assertRegex(tok.group(1), r"^[a-z0-9]{1,24}$")


class RoutesRunCleanupPostgresTestCase(unittest.TestCase):
    """Real-Postgres evidence for the isolation the name-math above only
    asserts on strings: with two run tokens' databases actually CREATED in
    Postgres, one run's cleanup drops ONLY its own — it neither terminates a
    concurrent run's LIVE connection nor removes that run's database — and
    each run's databases are genuinely gone after its own cleanup. This is
    the exact failure v6 hit (an `AdminShutdown` on a sibling's live
    connection), proven prevented against a real server. Skips when no
    Postgres is reachable, same criterion as the rest of the DB suites."""

    def setUp(self):
        base = rf._base_url()
        if not base:
            self.skipTest("no TEST_DATABASE_URL/DATABASE_URL for the routes-isolation DB proof")
        import psycopg
        self.psycopg = psycopg
        p = urlsplit(base)
        self._netloc, self._scheme = p.netloc, p.scheme
        self._query, self._fragment = p.query, p.fragment
        self.maint = urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))
        # Two distinct run tokens that are ALSO unique to THIS test process:
        # this regression itself creates and FORCE-drops real databases, so
        # two concurrent backend suites running it would collide on fixed
        # names — the exact bug under test. A per-process nonce (pid + random)
        # keeps concurrent regression processes disjoint while alpha/bravo
        # stay separated within a process. `_sanitize_token` strips it to
        # [a-z0-9], so the shape stays legal.
        nonce = rf._sanitize_token("iso%s%s" % (os.getpid(), os.urandom(3).hex()))
        self.tok_a = nonce + "a"
        self.tok_b = nonce + "b"

    def tearDown(self):
        # Belt-and-suspenders drop of both runs' dbs — NOT swallowed: a
        # cleanup failure here is a real signal (Postgres trouble, or a db
        # that won't drop), so let it surface rather than hide a leak.
        errors = []
        for tok in (self.tok_a, self.tok_b):
            try:
                rf.drop_run_databases(tok)
            except Exception as e:  # noqa: BLE001
                errors.append("%s: %r" % (tok, e))
        if errors:
            raise AssertionError("routes-isolation teardown cleanup failed: "
                                 + "; ".join(errors))

    def _url_for(self, db_name):
        return urlunsplit((self._scheme, self._netloc, "/" + db_name,
                           self._query, self._fragment))

    def _create(self, db_name):
        from psycopg import sql
        with self.psycopg.connect(self.maint, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name)))
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))

    def _exists(self, db_name):
        with self.psycopg.connect(self.maint, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db_name,))
            return cur.fetchone() is not None

    def test_sibling_cleanup_spares_a_live_connection_and_that_runs_db(self):
        name_a = rf._worker_db_name(0, run_token=self.tok_a)
        name_b = rf._worker_db_name(0, run_token=self.tok_b)
        self.assertNotEqual(name_a, name_b)
        self._create(name_a)
        self._create(name_b)

        # Hold a LIVE connection on run A's database — the exact thing v6's
        # sibling FORCE-drop terminated.
        live = self.psycopg.connect(self._url_for(name_a), autocommit=True, connect_timeout=5)
        try:
            with live.cursor() as cur:
                cur.execute("SELECT 1")
                self.assertEqual(cur.fetchone()[0], 1)

            # Run B cleans up. It must touch ONLY name_b.
            dropped_b = rf.drop_run_databases(self.tok_b)
            self.assertEqual(dropped_b, [name_b])

            # Run A's live connection was NOT terminated (no AdminShutdown)...
            with live.cursor() as cur:
                cur.execute("SELECT 1")
                self.assertEqual(cur.fetchone()[0], 1)
            # ...and run A's database still exists; run B's is gone.
            self.assertTrue(self._exists(name_a))
            self.assertFalse(self._exists(name_b))
        finally:
            live.close()

        # Run A's own cleanup removes A's database — no stale buildup.
        dropped_a = rf.drop_run_databases(self.tok_a)
        self.assertEqual(dropped_a, [name_a])
        self.assertFalse(self._exists(name_a))

    def test_cleanup_does_not_touch_lookalike_databases(self):
        """Destructive-scope proof: cleanup must select the EXACT run-scoped
        worker names, never a database that merely resembles one. Two
        lookalikes are created that the old unescaped-LIKE predicate WOULD
        have matched — one with a non-underscore where the prefix has an
        underscore (LIKE's `_` is a single-char wildcard), one with a
        non-digit worker suffix — plus the genuine worker db. Cleanup for
        this run must drop ONLY the genuine one and leave both lookalikes
        untouched."""
        real = rf._worker_db_name(0, run_token=self.tok_a)
        # 'sana_turnos...' -> 'sanaXturnos...': the old LIKE matched X against
        # the wildcard '_'; the anchored regex requires a literal underscore.
        look_prefix = real.replace("sana_turnos", "sanaXturnos", 1)
        # '..._w0' -> '..._wX': worker suffix must be digits; a letter is out.
        look_suffix = real[: -len("_w0")] + "_wX"
        self.assertNotEqual(look_prefix, real)
        self.assertNotEqual(look_suffix, real)

        made = [real, look_prefix, look_suffix]
        for n in made:
            self._create(n)
        try:
            dropped = rf.drop_run_databases(self.tok_a)
            self.assertEqual(dropped, [real],
                             "cleanup selected a lookalike database, not just "
                             "the exact run-scoped worker name")
            self.assertFalse(self._exists(real))
            self.assertTrue(self._exists(look_prefix),
                            "a prefix-lookalike database was wrongly dropped")
            self.assertTrue(self._exists(look_suffix),
                            "a suffix-lookalike database was wrongly dropped")
        finally:
            from psycopg import sql
            with self.psycopg.connect(self.maint, autocommit=True, connect_timeout=5) as c:
                for n in (look_prefix, look_suffix, real):
                    with c.cursor() as cur:
                        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                                    .format(sql.Identifier(n)))


class RoutesStaleReclamationLogicTestCase(unittest.TestCase):
    """Pure name-math for the SIGKILL-orphan reclamation (no database): the
    creation stamp parses ONLY out of the runner's own minted shape, so every
    other token — legacy `p<pid>r<rand>`, hand-set debugging tokens, foreign
    strings — is structurally un-reclaimable."""

    def test_stamp_parses_the_runner_shape(self):
        self.assertEqual(rf._token_epoch("t68915a00p123r456"),
                         int("68915a00", 16))
        self.assertEqual(rf._token_epoch("t1p2r3"), 1)

    def test_everything_else_is_unstamped(self):
        for bad in ("p123r456",        # legacy pre-stamp runner token
                    "iso1234abc",      # test-suite nonce token
                    "runalpha",        # hand-set debugging token
                    "", None,
                    "t123",            # stamp with no pid/rand tail
                    "tzzp1r2",         # non-hex stamp
                    "t68915a00p123",   # missing rand
                    "t68915a00r456"):  # missing pid
            self.assertIsNone(rf._token_epoch(bad), repr(bad))

    def test_runner_pid_parses_out_of_the_stamp(self):
        self.assertEqual(rf._token_pid("t68915a00p123r456"), 123)
        for bad in ("p123r456", "iso1234abc", "", None, "t68915a00r456"):
            self.assertIsNone(rf._token_pid(bad), repr(bad))

    def test_runner_gone_only_for_a_pid_that_no_longer_exists(self):
        # 2026-11-08: 46 of 56 routes runs of one night were gone with their
        # databases still there (3 GB): the runner is TERMed by pre-commit's
        # reaper and KILLed 10 s later, while its own trap still reaps its
        # workers. A dead runner's namespace is reclaimable before 2 h.
        self.assertTrue(rf._runner_gone(_dead_pid()))
        self.assertFalse(rf._runner_gone(os.getpid()))
        self.assertFalse(rf._runner_gone(None))

    def test_unreachable_postgres_warns_and_skips_instead_of_raising(self):
        """A Postgres that refuses the connect must NOT abort the runner
        before any test executes (independent review, 2026-08-08): the
        run's own tests need that same server and fail with a real test
        signal. Point reclamation at a closed port and require a clean
        empty return."""
        _env.set_env(self, TEST_DATABASE_URL="postgresql://nouser:nopass@127.0.0.1:1/postgres")
        self.assertEqual(rf.reclaim_stale_run_databases(), [])

    def test_legacy_fixed_names_never_match_the_reclaim_shape(self):
        # The reclaim selector requires `<prefix><token>_w<digits>`; the
        # legacy unscoped names have NO token segment, so they can never be
        # candidates no matter their age.
        import re as _re
        shape = _re.compile(
            "^" + _re.escape(rf._DB_PREFIX) + r"([a-z0-9]+)_w[0-9]+$")
        for w in range(4):
            self.assertIsNone(shape.match("sana_turnos_test_routes_w%d" % w))


class RoutesStaleReclamationPostgresTestCase(unittest.TestCase):
    """Real-Postgres evidence for the reclamation ladder: an old, session-free,
    stamped orphan is dropped; the current run's namespace, a fresh run's, a
    LIVE old one, the legacy fixed name, an unstamped orphan, and lookalike
    names all survive. Skips without Postgres, same criterion as the suites
    above."""

    def setUp(self):
        base = rf._base_url()
        if not base:
            self.skipTest("no TEST_DATABASE_URL/DATABASE_URL for the reclamation DB proof")
        import time
        import psycopg
        self.psycopg = psycopg
        p = urlsplit(base)
        self.maint = urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))
        self._created = []
        now = int(time.time())
        pid = os.getpid()

        def rand():
            return int.from_bytes(os.urandom(3), "big") % 90000 + 10000

        # Stamped tokens, unique to THIS test process (pid + random), all
        # under the sanitizer's 24-char cap so the stamp survives intact.
        self.tok_old = "t%xp%dr%d" % (now - 3 * 3600, pid, rand())
        self.tok_old_live = "t%xp%dr%d" % (now - 3 * 3600, pid, rand())
        self.tok_fresh = "t%xp%dr%d" % (now - 60, pid, rand())
        # Current-run token deliberately stamped OLD: only the current-token
        # guard (not the age guard) can be what protects it.
        self.tok_current = "t%xp%dr%d" % (now - 3 * 3600, pid, rand())
        self.tok_unstamped = rf._sanitize_token(
            "iso%s%s" % (pid, os.urandom(3).hex()))
        _env.set_env(self, TURNOS_ROUTES_RUN_ID=self.tok_current)

    def tearDown(self):
        from psycopg import sql
        errors = []
        with self.psycopg.connect(self.maint, autocommit=True, connect_timeout=5) as c:
            for n in self._created:
                try:
                    with c.cursor() as cur:
                        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                                    .format(sql.Identifier(n)))
                except Exception as e:  # noqa: BLE001
                    errors.append("%s: %r" % (n, e))
        if errors:
            raise AssertionError("reclamation teardown cleanup failed: "
                                 + "; ".join(errors))

    def _url_for(self, db_name):
        p = urlsplit(rf._base_url())
        return urlunsplit((p.scheme, p.netloc, "/" + db_name, p.query, p.fragment))

    def _create(self, db_name, track=True):
        from psycopg import sql
        with self.psycopg.connect(self.maint, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                        .format(sql.Identifier(db_name)))
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        if track:
            self._created.append(db_name)

    def _exists(self, db_name):
        with self.psycopg.connect(self.maint, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db_name,))
            return cur.fetchone() is not None

    def test_reclaims_only_the_old_sessionless_orphan(self):
        name_old = rf._worker_db_name(0, run_token=self.tok_old)
        name_old_live = rf._worker_db_name(0, run_token=self.tok_old_live)
        name_fresh = rf._worker_db_name(0, run_token=self.tok_fresh)
        name_current = rf._worker_db_name(0, run_token=self.tok_current)
        name_unstamped = rf._worker_db_name(0, run_token=self.tok_unstamped)
        # Lookalikes off a stamped-OLD token: reclaimable by age, protected
        # only by the exact-shape selector.
        look_suffix = name_old[: -len("_w0")] + "_wX"
        look_prefix = name_old.replace("sana_turnos", "sanaXturnos", 1)
        # Legacy-SHAPE witness: tokenless like the real fixed names, but a
        # worker index no runner ever uses (workers are 0..3), so this test
        # never creates or drops a database a concurrent standalone
        # `unittest discover` run could legitimately own (`..._w0`). The
        # selector proof is identical — no token segment, so it can never
        # match the reclaim shape regardless of the index.
        legacy = "sana_turnos_test_routes_w999"

        for n in (name_old, name_old_live, name_fresh, name_current,
                  name_unstamped, look_suffix, look_prefix, legacy):
            self._create(n)

        live = self.psycopg.connect(self._url_for(name_old_live),
                                    autocommit=True, connect_timeout=5)
        try:
            dropped = rf.reclaim_stale_run_databases()
            # The old, session-free orphan is reclaimed...
            self.assertIn(name_old, dropped)
            self.assertFalse(self._exists(name_old))
            # ...and NOTHING protected was: not the live old one (its
            # connection also stays unterminated), not the fresh or current
            # run, not the unstamped orphan, the legacy name or lookalikes.
            protected = (name_old_live, name_fresh, name_current,
                         name_unstamped, look_suffix, look_prefix, legacy)
            for n in protected:
                self.assertNotIn(n, dropped, n)
                self.assertTrue(self._exists(n), "%s was wrongly dropped" % n)
            with live.cursor() as cur:
                cur.execute("SELECT 1")
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            live.close()

    def test_reclaims_a_dead_runners_namespace_before_the_age_limit(self):
        import time
        now = int(time.time())
        dead = _dead_pid()
        rand = int.from_bytes(os.urandom(3), "big") % 90000 + 10000
        # Runner gone, run started 10 min ago: reclaimed although far under 2 h.
        tok_dead = "t%xp%dr%d" % (now - 600, dead, rand)
        # Runner gone a moment ago: its own EXIT trap may still be dropping.
        tok_dead_young = "t%xp%dr%d" % (now - 30, dead, rand + 1)
        name_dead = rf._worker_db_name(2, run_token=tok_dead)
        name_dead_young = rf._worker_db_name(2, run_token=tok_dead_young)
        # Same age as tok_dead, but its runner (this process) is alive.
        name_alive = rf._worker_db_name(
            2, run_token="t%xp%dr%d" % (now - 600, os.getpid(), rand + 2))
        for n in (name_dead, name_dead_young, name_alive):
            self._create(n)
        dropped = rf.reclaim_stale_run_databases()
        self.assertIn(name_dead, dropped)
        self.assertFalse(self._exists(name_dead))
        for n in (name_dead_young, name_alive):
            self.assertNotIn(n, dropped, n)
            self.assertTrue(self._exists(n), "%s was wrongly dropped" % n)

    def test_reclaim_is_idempotent_and_quiet_when_nothing_qualifies(self):
        name_fresh = rf._worker_db_name(1, run_token=self.tok_fresh)
        self._create(name_fresh)
        first = rf.reclaim_stale_run_databases()
        second = rf.reclaim_stale_run_databases()
        self.assertNotIn(name_fresh, first + second)
        self.assertTrue(self._exists(name_fresh))


if __name__ == "__main__":
    unittest.main()
