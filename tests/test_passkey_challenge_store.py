"""PostgreSQL proofs for durable, digest-only WebAuthn challenge state.

The concurrency case uses two spawned Python processes, rather than two calls
through one pool, so it proves the one-time claim survives separate ASGI
workers.  Every database is `_dbtest`'s disposable run-scoped database.
"""

import multiprocessing
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Challenge tokens are signed with the admin secret; the suite used to get one
# from whichever module happened to be imported before this one.
import _env  # noqa: E402
_env.process_defaults({"TURNOS_ADMIN_SECRET": "test-only-passkey-challenge-key"})

from app.auth import passkeys as pk  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
import _dbtest  # noqa: E402


def _consume_from_separate_process(database_url, digest, purpose, username, result_queue):
    """Independent process/pool boundary used by the replay proof below."""
    # A spawned child: these settings are for its whole life, never undone.
    _env.process_set(DATABASE_URL=database_url, TURNOS_ENV_FILE=os.devnull)
    from app.core import db as child_db
    from app.core import db_auth
    child_db.close_pools()
    try:
        result_queue.put(("ok", db_auth.passkey_challenge_consume(digest, purpose, username)))
    except Exception as exc:  # pragma: no cover - assertion reports child failure
        result_queue.put(("error", repr(exc)))


_db = _dbtest.PerModuleDb(
    "sana_turnos_test_passkey_challenges", seed=lambda: db_operators.operators_list(),
)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class DurablePasskeyChallengeTest(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE passkey_challenges")
                cur.execute("TRUNCATE passkeys RESTART IDENTITY")

    def _issue(self, purpose="login", username=None):
        token = pk.nuevo_reto("registro" if purpose == "registration" else "login", username or "")
        digest = pk._challenge_digest(token)
        db_auth.passkey_challenge_issue(digest, purpose, username, 300)
        return token, digest

    def test_database_contains_digest_only_and_registration_owner(self):
        token, digest = self._issue("registration", "admin")
        import psycopg
        with psycopg.connect(_db.test_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT challenge_digest, purpose, username, expires_at > now()
                               FROM passkey_challenges""")
                row = cur.fetchone()
                cur.execute("""SELECT column_name FROM information_schema.columns
                               WHERE table_name='passkey_challenges'""")
                columns = {r[0] for r in cur.fetchall()}
        self.assertEqual(bytes(row[0]), digest)
        self.assertEqual(row[1:], ("registration", "admin", True))
        self.assertNotIn("challenge", columns)
        self.assertNotIn(token, str(row))

    def test_consume_requires_matching_purpose_and_registration_owner(self):
        _token, digest = self._issue("registration", "admin")
        self.assertFalse(db_auth.passkey_challenge_consume(digest, "login", None))
        self.assertFalse(db_auth.passkey_challenge_consume(digest, "registration", "other"))
        self.assertTrue(db_auth.passkey_challenge_consume(digest, "registration", "admin"))
        self.assertFalse(db_auth.passkey_challenge_consume(digest, "registration", "admin"))

    def test_expired_and_old_consumed_rows_are_not_usable_or_retained(self):
        _token, expired = self._issue("login")
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE passkey_challenges SET expires_at=now() - interval '1 second'")
        self.assertFalse(db_auth.passkey_challenge_consume(expired, "login", None))
        self._issue("login")  # issuance runs bounded cleanup
        with psycopg.connect(_db.test_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM passkey_challenges")
                self.assertEqual(cur.fetchone()[0], 1)

    def test_two_separate_processes_can_consume_a_challenge_only_once(self):
        _token, digest = self._issue("login")
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        workers = [context.Process(
            target=_consume_from_separate_process,
            args=(_db.test_url, digest, "login", None, results),
        ) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(20)
            self.assertEqual(worker.exitcode, 0, "child process failed or timed out")
        observed = [results.get(timeout=5) for _ in workers]
        self.assertEqual([status for status, _value in observed], ["ok", "ok"])
        self.assertEqual(sorted(value for _status, value in observed), [False, True])

    def test_nonzero_counter_cannot_move_backward_after_an_atomic_advance(self):
        db_auth.passkey_create("admin", "counter-proof", {"alg": "ES256", "x": "x", "y": "y"})
        self.assertTrue(db_auth.passkey_mark_use("counter-proof", 9))
        self.assertFalse(db_auth.passkey_mark_use("counter-proof", 8))
        self.assertEqual(db_auth.passkey_by_credential("counter-proof")["sign_count"], 9)


if __name__ == "__main__":
    unittest.main()
