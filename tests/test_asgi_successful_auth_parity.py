"""test_asgi_successful_auth_parity.py — Tier 4 ASGI cutover readiness,
blocker #2 (docs/roadmap/tier4-replatform-design.md, "ASGI cutover
readiness"): `route_parity_check.py` and every parity test written so far
(ESCENARIOS/ESCENARIOS_INTAKE/etc.) only ever prove the REJECTION path for
authenticated admin routes — no session cookie -> 401==401 on both
transports (route_parity_check.py:261/265, "sin cookie -> 401"). This file
closed the corresponding gap for a VALID session cookie on the two
highest-risk authenticated GETs: /api/admin/whoami (session identity) and
GET /api/admin/passkeys (credential metadata listing, high-risk because it
lists WebAuthn credentials).

NATIVE-MIGRATION FINAL WAVE (2026-08-27): the legacy HTTP topology
(`server.py`, the dispatch that ran
`admin_auth.whoami(handler)`/`passkeys.handle(handler, "GET")` over a real
socket) is retired — `app.py`'s native FastAPI composition is the only
transport left. This file no longer proves legacy-vs-native EQUALITY; it
pins the NATIVE (`TestClient` against the real `app.app`) response shape
directly. The values asserted below (status 200, exact JSON keys, which
fields never appear) are exactly what the retired parity assertions proved
were shared with the legacy handler — this is that same contract, now
asserted absolutely instead of by comparison.

The current `itsdangerous` cookie is the only accepted session format. The
legacy hand-written HMAC compatibility path was retired after its maximum
30-day migration window and has a dedicated rejection fixture.

whoami needs no Postgres: `admin_auth.require_admin` resolves identity/role
straight from the signed token, never from a `operadores` row (core/db.py is
only consulted for the 2FA deadline inside `account_security`, which
degrades to `None`/`False` without a database — see
admin_auth.account_security's own docstring: the absence of a DB never
blocks access). The passkeys-list case genuinely needs a real
Postgres-backed passkey row, so it runs inside a throwaway per-module
database (`_dbtest.PerModuleDb`, same pattern as test_passkeys.py) and
skips gracefully when Postgres isn't reachable — see
TestPasskeysListSuccessfulAuthParity's own docstring. Wrapping the WHOLE
file's DB traffic in that throwaway database (even for the no-DB-required
whoami cases) means running this file never writes a stray row into a real
dev/prod `admin_account_security` table, whether or not Postgres happens to
be reachable in the environment running it.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_asgi_successful_auth_parity.py"
"""
import json
import os
import sys
import unittest
import _lifespan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
from app.auth import admin_auth  # noqa: E402
from app.auth import passkeys as pk  # noqa: E402
from app.core import config  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

import _dbtest  # noqa: E402
# Reused, not reimplemented (repo convention — see test_passkeys.py's own
# "Autenticador" docstring): the fake WebAuthn authenticator, its FakeHandler
# shim and the cookie/output helpers already proven correct by
# test_passkeys.py's own full-ceremony suite.
from test_passkeys import Autenticador, ORIGEN  # noqa: E402

_AUTH_ENV = {
    "TURNOS_ADMIN_SECRET": "test-only-asgi-auth-parity-signing-key",
    "TURNOS_CONFIG_LOCK": "1",
}


def setUpModule():
    _lifespan.no_background_services()
    """Keep this module's auth fixture out of sibling test modules."""
    unittest.addModuleCleanup(config._reset_cache_para_tests)   # runs after the environment is put back
    _env.set_env_for_module(_AUTH_ENV)
    config._reset_cache_para_tests()


# ---- the one remaining (native) transport ----------------------------------

def _run_fastapi(client, path, token):
    resp = client.get(path, cookies={"st_admin": token},
                      headers={"Host": "testserver"}, follow_redirects=False)
    return resp.status_code, resp.content


def _json_body(raw):
    return json.loads(raw.decode("utf-8"))


class TestWhoamiSuccessfulAuthParity(unittest.TestCase):
    """GET /api/admin/whoami WITH a valid session — native FastAPI response,
    pinned absolutely for the maintained cookie format and both roles
    (admin, medico — whoami is the only endpoint where
    require_admin(permitir_medico=True) lets a doctor through, see
    admin_auth.whoami_result's own docstring). The identity's role and user
    come from the signed token, never from an `operadores` row
    (admin_auth.require_admin never consults the database for that), so
    these cases seed no operator — just a valid token per combination."""

    @classmethod
    def setUpClass(cls):
        # Short on purpose: _dbtest appends a per-run token and Postgres
        # truncates identifiers at 63 chars (run_scoped raises past that).
        cls._db = _dbtest.PerModuleDb("sana_turnos_test_asgi_auth_parity")
        cls._db.set_up()

    @classmethod
    def tearDownClass(cls):
        cls._db.tear_down()

    def setUp(self):
        # ONE client per test METHOD, not one shared at class level: whoami
        # renews the cookie (Set-Cookie) when the token is past half its
        # lifetime (_debe_renovar) — a TestClient/httpx reused across methods
        # stores that renewed cookie in its jar and reapplies it (overriding
        # the next call's explicit `cookies=`) to ANOTHER test's requests,
        # mixing identities between cases. Implementation finding of this
        # very file — a class-wide client made the second test method always
        # see the first one's user.
        self.client = TestClient(app_module.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _assert_identity(self, token, expected_user, expected_role):
        status, body = _run_fastapi(self.client, "/api/admin/whoami", token)
        self.assertEqual(status, 200, body)
        data = _json_body(body)
        self.assertEqual(data["usuario"], expected_user)
        self.assertEqual(data["rol"], expected_role)
        # `permisos`: los permisos efectivos que el portón verificó para ESTE
        # request (roles editables, R5/R6) — el panel arma su navegación con
        # eso en vez de deducirla del rol.
        self.assertEqual(set(data.keys()),
                         {"usuario", "rol", "tiene_2fa", "2fa_deadline",
                          "2fa_vencido", "permisos"})
        self.assertIsInstance(data["permisos"], list)
        self.assertIsInstance(data["tiene_2fa"], bool)
        self.assertIsInstance(data["2fa_vencido"], bool)
        deadline = data["2fa_deadline"]
        self.assertTrue(deadline is None or isinstance(deadline, str))

    def test_whoami_admin_role_new_itsdangerous_cookie(self):
        token = admin_auth._mint_token("parity-admin-role", "admin")
        self._assert_identity(token, "parity-admin-role", "admin")

    def test_whoami_medico_role_new_itsdangerous_cookie(self):
        token = admin_auth._mint_token("dra.parity-medico", "medico")
        # Prove this is a genuine NEW-format token by the serializer itself,
        # not by counting wire dots: itsdangerous may prefix a "." compression
        # marker, so the dot count is legitimately 2 or 3 depending on the
        # payload bytes (that exact-count assertion flaked once the exp
        # timestamp's digits crossed the zlib break-even point). A legacy
        # HMAC token would raise BadData here; the whoami-shape assertion
        # below remains the real acceptance gate.
        data = admin_auth._itsdangerous_serializer().loads(token)
        self.assertEqual((data.get("u"), data.get("r")),
                         ("dra.parity-medico", "medico"))
        self._assert_identity(token, "dra.parity-medico", "medico")

class TestPasskeysListSuccessfulAuthParity(unittest.TestCase):
    """GET /api/admin/passkeys WITH a valid session, NON-empty list — native
    FastAPI response, pinned absolutely. High-risk because the response is
    credential metadata (id, name, registration date): a REAL passkey is
    seeded with the same full WebAuthn ceremony test_passkeys.py uses
    (register-options + register, via `pk.handle()` directly — no HTTP,
    same business-logic call test_passkeys.py's own TestFlujo._alta makes to
    seed data — exactly how TestFlujo._alta builds it), and the test asserts
    the native list neither includes the public key nor the signature
    counter (`core/db.py:_passkey_public` strips them from the wire, see its
    own docstring: never the raw public key nor the counter). Needs a real
    Postgres — skips via skipTest when unreachable, same criterion as
    TestFlujo in test_passkeys.py."""

    @classmethod
    def setUpClass(cls):
        # Short on purpose — same 63-char reason as setUpClass above.
        cls._db = _dbtest.PerModuleDb("sana_turnos_test_asgi_auth_parity_pk")
        cls._db.set_up()
        cls.client = TestClient(app_module.app, base_url="http://localhost:7700")
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls._db.tear_down()

    def setUp(self):
        if not self._db.have_db:
            self.skipTest(self._db.skip_reason)
        import psycopg
        with psycopg.connect(self._db.test_url, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("TRUNCATE passkeys RESTART IDENTITY")
                cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")

    def _seed_passkey(self, usuario):
        """Same ceremony as TestFlujo._alta in test_passkeys.py: request a
        registration challenge, sign it with a fake authenticator, register."""
        token = admin_auth._mint_token(usuario, "admin")
        options = self.client.post(
            pk._BASE + "/register-options", cookies={"st_admin": token})
        self.assertEqual(options.status_code, 200, options.text)
        challenge = options.json()["challenge"]

        aut = Autenticador()
        cred = aut.alta(challenge, ORIGEN)
        cred["nombre"] = "ASGI parity key"
        registered_response = self.client.post(
            pk._BASE + "/register", json=cred, cookies={"st_admin": token})
        self.assertEqual(registered_response.status_code, 200, registered_response.text)
        registered = registered_response.json()
        self.assertTrue(registered["ok"], registered)
        return registered["passkey"]

    def test_passkey_list_native_shape(self):
        usuario = "parity-passkey-owner"
        seeded = self._seed_passkey(usuario)
        # The public key NEVER comes back, not even on registration.
        self.assertNotIn("public_key", seeded)

        token = admin_auth._mint_token(usuario, "admin")
        status, body = _run_fastapi(self.client, pk._BASE, token)

        self.assertEqual(status, 200, body)
        data = _json_body(body)

        self.assertEqual(data["usuario"], usuario)
        self.assertEqual(len(data["passkeys"]), 1)

        row = data["passkeys"][0]
        for secret_field in ("public_key", "clave_publica", "sign_count"):
            self.assertNotIn(secret_field, row,
                             f"native passkeys list leaked {secret_field!r}")
        self.assertEqual(row["nombre"], "ASGI parity key")
        self.assertIn("credential_id", row)
        self.assertIn("creado", row)


if __name__ == "__main__":
    unittest.main()
