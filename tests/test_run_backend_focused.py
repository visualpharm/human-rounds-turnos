"""`scripts/run_backend_focused.py` — one test file on a database of its own.

The `backend-focused` profile runs a file through it, so the file never meets the shared
development database (2026-11-06: it was stamped with a revision main does not have, and
every lone run inherited it). Proven here against the test server: the child sees both
database variables pointed at a fresh database whose name `_dbtest` can reclaim, the
database is gone when the child ends, the child's exit code is the run's, and without a
reachable server the file still runs, with no URL in what is printed.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import _dbtest  # noqa: E402
import run_backend_focused as focused  # noqa: E402
import run_backend_shards as shards  # noqa: E402

_CHILD = (
    "import os, sys\n"
    "out = sys.argv[1]\n"
    "with open(out, 'w') as f:\n"
    "    f.write(os.environ['DATABASE_URL'] + '\\n' + os.environ['TEST_DATABASE_URL'])\n"
    "raise SystemExit(int(sys.argv[2]))\n"
)


def _base_url():
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def _exists(maint, database):
    import psycopg
    with psycopg.connect(maint, autocommit=True, connect_timeout=10) as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone()
    return row is not None


def _reachable(url):
    if not url:
        return False
    try:
        import psycopg
        with psycopg.connect(shards._urls(url, "postgres")[0], connect_timeout=5):
            return True
    except Exception:  # noqa: BLE001 - no server: the database cases skip
        return False


class TestOwnDatabase(unittest.TestCase):
    def setUp(self):
        self.base = _base_url()
        if not _reachable(self.base):
            self.skipTest("no test Postgres")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, code):
        out = os.path.join(self.tmp.name, "seen.txt")
        child = [sys.executable, "-c", _CHILD, out, str(code)]
        with mock.patch.object(focused, "command", return_value=child):
            rc = focused.main(["test_anything.py"])
        return rc, Path(out).read_text().splitlines()

    def test_the_child_runs_on_a_fresh_database_that_is_dropped_afterwards(self):
        rc, (database_url, test_url) = self._run(0)
        self.assertEqual(rc, 0)
        self.assertEqual(database_url, test_url)
        database = database_url.rsplit("/", 1)[1].split("?", 1)[0]
        self.assertRegex(database, _dbtest._SCOPED_SHAPE)
        self.assertTrue(database.startswith(focused.DATABASE_BASE + "_"))
        self.assertNotEqual(database_url, self.base)
        self.assertFalse(_exists(shards._urls(self.base, "postgres")[0], database))

    def test_the_child_exit_code_is_the_run_exit_code(self):
        rc, _seen = self._run(3)
        self.assertEqual(rc, 3)


class TestWithoutServer(unittest.TestCase):
    def test_an_unreachable_server_runs_the_file_as_given_without_printing_the_url(self):
        url = "postgresql://fixture:focused-sentinel@127.0.0.1:1/fixture"
        err = mock.MagicMock()
        with mock.patch.object(focused.sys, "stderr", err):
            self.assertIsNone(focused.own_database(url))
        printed = "".join(str(c.args[0]) for c in err.write.call_args_list if c.args)
        self.assertIn("no own database", printed)
        self.assertNotIn("focused-sentinel", printed)

    def test_the_argument_is_one_test_file_name_or_the_whole_suite(self):
        for bad in ([], ["a.py", "b.py"], ["tests/test_x.py"], ["test_x"], ["test_a*.py"]):
            with mock.patch.object(focused.sys, "stderr", mock.MagicMock()):
                self.assertEqual(focused.main(bad), 2, bad)


class TestProfiles(unittest.TestCase):
    """Both one-process profiles go through this runner, never straight to unittest."""

    def test_focused_and_full_run_on_a_database_of_their_own(self):
        import turnos_managed_exec as managed_exec
        focused_cmd = managed_exec.PROFILES["backend-focused"].command("tests/test_config.py")
        full_cmd = managed_exec.PROFILES["backend-full"].command(None)
        for cmd, arg in ((focused_cmd, "test_config.py"), (full_cmd, focused.ALL)):
            self.assertIn("scripts/run_backend_focused.py", cmd)
            self.assertEqual(cmd[-1], arg)
            self.assertNotIn("unittest", cmd)


if __name__ == "__main__":
    unittest.main()
