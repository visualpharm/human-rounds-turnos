"""MCP OAuth single use against a real Postgres: a code (or a consent) is taken once for the whole
installation, not once per process. The claim is a hash row in `alert_claims`."""
import base64
import hashlib
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config, db  # noqa: E402
from app.modules.chat import mcp_oauth  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_mcp_oauth_once")

VERIFIER = "v" * 50
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
REDIRECT = "https://chatgpt.com/cb"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestOnceInPostgres(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, TURNOS_ADMIN_SECRET="oauth-once-key")
        config._reset_cache_para_tests()
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM alert_claims WHERE alert_key LIKE 'mcp-oauth:%'")

    def _code(self):
        return mcp_oauth.sign("code", {"dni": "30111222", "cid": "c", "ru": REDIRECT, "cc": CHALLENGE, "res": ""}, 60)

    def _form(self, code):
        return {"grant_type": "authorization_code", "code": code, "client_id": "c", "redirect_uri": REDIRECT,
                "code_verifier": VERIFIER}

    def test_a_code_is_exchanged_once_with_nothing_in_the_process_memory(self):
        code = self._code()
        issued = []
        first = mcp_oauth.exchange(self._form(code), lambda dni: issued.append(dni) or "tok")
        second = mcp_oauth.exchange(self._form(code), lambda dni: issued.append(dni) or "tok")
        self.assertEqual(first[0], 200)
        self.assertEqual((second[0], second[1]["error"]), (400, "invalid_grant"))
        self.assertEqual(issued, ["30111222"])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT alert_key FROM alert_claims WHERE alert_key LIKE 'mcp-oauth:%'")
            keys = [r["alert_key"] for r in cur.fetchall()]
        self.assertEqual(len(keys), 1)
        self.assertNotIn(code, keys[0])

    def test_concurrent_exchanges_of_one_code_issue_one_token(self):
        code = self._code()
        issued, results = [], []

        def go():
            results.append(mcp_oauth.exchange(self._form(code), lambda dni: issued.append(dni) or "tok")[0])

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200] + [400] * 7)
        self.assertEqual(len(issued), 1)

    def test_a_consent_is_decided_once_for_the_installation(self):
        consent = mcp_oauth.sign("consent", {"dni": "30111222", "client_id": "c", "redirect_uri": REDIRECT,
                                             "state": "", "code_challenge": CHALLENGE, "resource": ""}, 600)
        self.assertTrue(mcp_oauth.decide(consent, "30111222", True).startswith(REDIRECT))
        self.assertIsNone(mcp_oauth.decide(consent, "30111222", True))


if __name__ == "__main__":
    unittest.main()
