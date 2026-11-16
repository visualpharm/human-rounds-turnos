"""PostgreSQL regression coverage for the historical passkey row shape."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _env  # noqa: E402
_env.process_defaults({"TURNOS_FAKE_AI": "1", "TURNOS_ADMIN_SECRET": "test-only-secret"})

from app.auth import admin_auth  # noqa: E402
from app.auth import passkeys as pk  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
import _dbtest  # noqa: E402
from test_passkeys import Autenticador, ORIGEN  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_passkeys_real_shape", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class StoredRowAuthenticationTest(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE passkey_challenges")
                cur.execute("TRUNCATE passkeys RESTART IDENTITY")
        self.user = admin_auth._admin_user()

    def test_registered_row_is_reused_by_library_login(self):
        auth = Autenticador()
        register_challenge = pk._issue_reto("registro", self.user)
        registered = pk.register_result(
            admin_auth._mint_token(self.user, "admin"), "POST", pk._BASE + "/register",
            "localhost:7700", None,
            json.dumps(dict(auth.alta(register_challenge, ORIGEN), nombre="compatibility key")).encode(),
        )
        self.assertEqual(registered.status, 200, registered.body)
        credential_id = pk.b64u(auth.credential_id)
        stored = db_auth.passkey_by_credential(credential_id)
        self.assertEqual(set(stored["public_key"]), {"alg", "x", "y"})
        login_challenge = pk._issue_reto("login")
        result = pk.login_result(
            {"Host": "localhost:7700"}, "127.0.0.1",
            json.dumps(auth.ingreso(login_challenge, ORIGEN, count=1)).encode(),
        )
        self.assertEqual(result.status, 200, result.body)
        self.assertTrue(result.body["ok"])


if __name__ == "__main__":
    unittest.main()
