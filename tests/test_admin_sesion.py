"""La sesión del admin no vence mientras se usa el panel.

El responsable del producto, 2026-07-22: "el admin me desloguea todo el tiempo". La cookie era de 12 h
fijas y stateless, así que cada mañana había que volver a loguearse. Ahora dura
30 días (`TURNOS_ADMIN_SESSION_DAYS` la ajusta) y `whoami` la RE-EMITE cuando ya
pasó la mitad de su vida — usar el panel la mantiene viva sola.
"""
import io
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
from app.auth import admin_auth  # noqa: E402


class FakeHandler:
    def __init__(self, cookie=None, proto=None):
        headers = {}
        if cookie:
            headers["Cookie"] = "st_admin=" + cookie
        if proto:
            headers["X-Forwarded-Proto"] = proto
        self.headers = headers
        self.wfile = io.BytesIO()
        self.sent_headers = {}
        self.status = None
        self.client_address = ("127.0.0.1", 0)

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent_headers[k] = v

    def end_headers(self):
        pass


def _payload(token):
    """Decode the one maintained session format for test inspection."""
    return admin_auth._itsdangerous_serializer().loads(str(token))


class TestDuracion(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {"TURNOS_ADMIN_SESSION_DAYS": None, "TURNOS_ADMIN_SECRET": "test-session-signing-secret", "TURNOS_ADMIN_PASS": None})

    def test_default_30_dias(self):
        self.assertEqual(admin_auth._max_age(), 30 * 24 * 3600)

    def test_env_manda(self):
        _env.set_env(self, TURNOS_ADMIN_SESSION_DAYS="7")
        self.assertEqual(admin_auth._max_age(), 7 * 24 * 3600)

    def test_env_basura_cae_al_default(self):
        _env.set_env(self, TURNOS_ADMIN_SESSION_DAYS="ni idea")
        self.assertEqual(admin_auth._max_age(), admin_auth.COOKIE_MAX_AGE)

    def test_cookie_y_token_usan_la_misma_duracion(self):
        _env.set_env(self, TURNOS_ADMIN_SESSION_DAYS="2")
        token = admin_auth._mint_token("admin", "admin")
        self.assertIn("Max-Age=%d" % (2 * 24 * 3600),
                      admin_auth._set_cookie_from({}, token))
        restante = _payload(token)["exp"] - int(time.time())
        self.assertGreater(restante, 2 * 24 * 3600 - 60)


class TestRenovacion(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {"TURNOS_ADMIN_SECRET": "test-session-signing-secret", "TURNOS_ADMIN_PASS": None})

    def test_token_fresco_no_se_renueva(self):
        self.assertFalse(admin_auth._debe_renovar(admin_auth._mint_token("admin", "admin")))

    def test_token_pasada_la_mitad_se_renueva(self):
        token = admin_auth._mint_token("admin", "admin")
        p = _payload(token)
        p["exp"] = int(time.time()) + admin_auth._max_age() // 3
        self.assertTrue(admin_auth._debe_renovar(_firmar(p)))

    def test_token_roto_no_rompe(self):
        self.assertFalse(admin_auth._debe_renovar("basura"))
        self.assertFalse(admin_auth._debe_renovar(None))

    def test_whoami_reemite_la_cookie_cuando_corresponde(self):
        p = _payload(admin_auth._mint_token(admin_auth._admin_user(), "admin"))
        p["exp"] = int(time.time()) + admin_auth._max_age() // 3
        h = FakeHandler(cookie=_firmar(p))
        result = admin_auth.whoami_result(h.headers, "GET", "/api/admin/whoami")
        self.assertEqual(result.status, 200)
        cookie = result.set_cookie or ""
        self.assertTrue(cookie.startswith("st_admin="))
        nuevo = cookie.split("=", 1)[1].split(";", 1)[0]
        self.assertGreater(_payload(nuevo)["exp"], p["exp"])

    def test_whoami_no_manda_cookie_si_la_sesion_esta_fresca(self):
        h = FakeHandler(cookie=admin_auth._mint_token(admin_auth._admin_user(), "admin"))
        result = admin_auth.whoami_result(h.headers, "GET", "/api/admin/whoami")
        self.assertEqual(result.status, 200)
        self.assertIsNone(result.set_cookie)


def _firmar(payload):
    return admin_auth._itsdangerous_serializer().dumps(payload)


if __name__ == "__main__":
    unittest.main()
