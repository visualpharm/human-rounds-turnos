"""test_auth_renaper.py — login con identidad nacional (auth_renaper.py).

Cubre la "setting" de métodos de login (TURNOS_AUTH_METHODS), el anti-lockout,
la cookie de estado firmada (state/nonce/next) y el flujo fake completo
(inicio -> callback -> sesión) sin red y sin Postgres (db stubbeado).
"""

import os
import sys
import time
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.cuentas import auth_renaper  # noqa: E402
from app.core import config  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.cuentas import auth_renaper_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import _env  # noqa: E402

auth_renaper.ENV_PATH = ""  # ignorar el .env del checkout de dev
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev


class EnvCase(unittest.TestCase):
    KEYS = ("TURNOS_AUTH_METHODS", "TURNOS_RENAPER_CLIENT_ID",
            "TURNOS_RENAPER_CLIENT_SECRET", "TURNOS_FAKE_RENAPER",
            "TURNOS_FAKE_RENAPER_DNI", "TURNOS_RENAPER_PER_HOUR",
            "TURNOS_RENAPER_REDIRECT_URL", "TURNOS_ADMIN_SECRET",
            "TURNOS_ADMIN_PASS", "TURNOS_PUBLIC_ORIGIN")

    def setUp(self):
        _env.set_env(self, {k: None for k in self.KEYS})
        auth_renaper._HITS.clear()


class TestMetodos(EnvCase):
    def test_default_es_password(self):
        self.assertEqual(auth_renaper.metodos(), {"password": True, "renaper": False})

    def test_renaper_sin_credenciales_degrada_a_password(self):
        # anti-lockout: "renaper" solo, sin client_id/secret ni fake -> password
        _env.set_env(self, TURNOS_AUTH_METHODS="renaper")
        self.assertEqual(auth_renaper.metodos(), {"password": True, "renaper": False})

    def test_renaper_solo_con_fake(self):
        _env.set_env(self, TURNOS_AUTH_METHODS="renaper", TURNOS_FAKE_RENAPER="1")
        self.assertEqual(auth_renaper.metodos(), {"password": False, "renaper": True})

    def test_los_dos_a_la_vez(self):
        _env.set_env(self, TURNOS_AUTH_METHODS="password, renaper", TURNOS_RENAPER_CLIENT_ID="cid",
                     TURNOS_RENAPER_CLIENT_SECRET="sec")
        self.assertEqual(auth_renaper.metodos(), {"password": True, "renaper": True})

    def test_basura_degrada_a_password(self):
        _env.set_env(self, TURNOS_AUTH_METHODS="carrier-pigeon,")
        self.assertEqual(auth_renaper.metodos(), {"password": True, "renaper": False})

    def test_config_publico_trae_label_solo_si_hay_renaper(self):
        self.assertNotIn("renaper_label", auth_renaper.config_publico())
        _env.set_env(self, TURNOS_AUTH_METHODS="password,renaper", TURNOS_FAKE_RENAPER="1")
        self.assertEqual(auth_renaper.config_publico()["renaper_label"], "Mi Argentina")


class TestEstadoFirmado(EnvCase):
    def test_roundtrip(self):
        p = {"s": "abc", "n": "xyz", "next": "/account", "t": int(time.time())}
        val = auth_renaper._firmar_estado(p)
        self.assertEqual(auth_renaper._leer_estado(val), p)

    def test_firma_adulterada_rechaza(self):
        p = {"s": "abc", "n": "x", "next": "/", "t": int(time.time())}
        val = auth_renaper._firmar_estado(p)
        self.assertIsNone(auth_renaper._leer_estado(val[:-4] + "AAAA"))
        self.assertIsNone(auth_renaper._leer_estado("garbage"))

    def test_estado_vencido_rechaza(self):
        p = {"s": "abc", "n": "x", "next": "/", "t": int(time.time()) - 700}
        val = auth_renaper._firmar_estado(p)
        self.assertIsNone(auth_renaper._leer_estado(val))


class TestSanitizarNext(unittest.TestCase):
    def test_rutas_locales_pasan(self):
        self.assertEqual(auth_renaper._sanitizar_next("/my-requests"), "/my-requests")

    def test_todo_lo_demas_cae_a_account(self):
        for malo in ("//evil.com", "https://evil.com", "javascript:alert(1)",
                     "", None, "\\\\evil", "account"):
            self.assertEqual(auth_renaper._sanitizar_next(malo), "/account")


class TestExtraerDatos(unittest.TestCase):
    def test_dni_directo(self):
        d = auth_renaper._extraer_datos({"dni": "30.123.456", "given_name": "Ana",
                                         "family_name": "Paz", "birthdate": "1990-02-03",
                                         "gender": "female"})
        self.assertEqual(d["dni"], "30123456")
        self.assertEqual(d["sexo"], "F")
        self.assertEqual(d["nacimiento"], "1990-02-03")

    def test_dni_desde_cuil(self):
        d = auth_renaper._extraer_datos({"cuil": "20-30123456-7"})
        self.assertEqual(d["dni"], "30123456")

    def test_sin_dni_devuelve_none(self):
        self.assertIsNone(auth_renaper._extraer_datos({"given_name": "Ana"}))

    def test_fecha_rara_no_rompe(self):
        d = auth_renaper._extraer_datos({"dni": "30123456", "birthdate": "03/02/1990"})
        self.assertIsNone(d["nacimiento"])


class TestFlujoFake(EnvCase):
    """El flujo entero sin red: /auth/renaper -> callback -> Set-Cookie st_ses."""

    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_AUTH_METHODS="password,renaper", TURNOS_FAKE_RENAPER="1")
        app = FastAPI()
        app.include_router(auth_renaper_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def _inicio(self, path="/auth/renaper?next=/my-requests"):
        response = self.client.get(path, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        return response

    def test_inicio_setea_estado_y_redirige_al_callback(self):
        h = self._inicio()
        loc = h.headers["location"]
        self.assertTrue(loc.startswith("/auth/renaper/callback?"))
        self.assertIn(auth_renaper.STATE_COOKIE, h.cookies)

    def test_callback_crea_sesion_y_vuelve_a_next(self):
        h = self._inicio()
        loc = h.headers["location"]
        with mock.patch.object(db_accounts, "login_renaper", return_value=1) as ln, \
             mock.patch.object(db_accounts, "create_session", return_value="tok123") as cs:
            h2 = self.client.get(loc, follow_redirects=False)
        self.assertEqual(h2.status_code, 302)
        self.assertEqual(h2.headers["location"], "/my-requests")
        cookies = h2.headers.get_list("set-cookie")
        self.assertTrue(any("st_ses=tok123" in cookie for cookie in cookies))
        self.assertTrue(any("HttpOnly" in cookie for cookie in cookies))
        # y limpia la cookie de estado
        self.assertTrue(any(cookie.startswith(auth_renaper.STATE_COOKIE + "=") and "Max-Age=0" in cookie for cookie in cookies))
        # el usuario se creó/actualizó con el DNI fake y los claims crudos
        dni, datos, claims = ln.call_args[0]
        self.assertEqual(dni, "99000001")
        self.assertEqual(datos["dni"], "99000001")
        self.assertTrue(claims.get("fake"))
        cs.assert_called_once_with("99000001")

    def test_callback_sin_cookie_rechaza(self):
        h = self._inicio()
        loc = h.headers["location"]
        self.client.cookies.clear()
        self.assertEqual(self.client.get(loc, follow_redirects=False).status_code, 400)

    def test_callback_con_state_ajeno_rechaza(self):
        h = self._inicio()
        with mock.patch.object(db_accounts, "create_session", return_value="tok") :
            h2 = self.client.get("/auth/renaper/callback?code=fake&state=OTRO", follow_redirects=False)
        self.assertEqual(h2.status_code, 400)
        self.assertNotIn("st_ses", h2.headers.get("set-cookie", ""))

    def test_error_del_idp_no_abre_sesion(self):
        h = self._inicio()
        state = urllib.parse.parse_qs(
            urllib.parse.urlparse(h.headers["location"]).query)["state"][0]
        h2 = self.client.get("/auth/renaper/callback?error=access_denied&state=" + state,
                             follow_redirects=False)
        self.assertEqual(h2.status_code, 400)
        self.assertNotIn("st_ses", h2.headers.get("set-cookie", ""))

    def test_renaper_apagado_redirige_al_login_comun(self):
        _env.set_env(self, TURNOS_FAKE_RENAPER=None)  # sin creds -> renaper off
        h = self.client.get("/auth/renaper", follow_redirects=False)
        self.assertEqual(h.status_code, 302)
        self.assertEqual(h.headers["location"], "/account?mode=login")

    def test_rate_limit(self):
        _env.set_env(self, TURNOS_RENAPER_PER_HOUR="2")
        self._inicio(); self._inicio()
        self.assertEqual(self.client.get("/auth/renaper", follow_redirects=False).status_code, 400)

    def test_rutas_ajenas_no_se_tocan(self):
        self.assertEqual(self.client.get("/account").status_code, 404)
        self.assertEqual(self.client.post("/auth/renaper").status_code, 405)


class TestRedirectUri(EnvCase):
    def test_configured_public_origin_ignores_host_header(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="https://appointments.example.org")
        self.assertEqual(auth_renaper._redirect_uri(),
                         "https://appointments.example.org/auth/renaper/callback")

    def test_fresh_checkout_uses_loopback_origin_not_host(self):
        self.assertEqual(
            auth_renaper._redirect_uri(),
            "http://127.0.0.1:7700/auth/renaper/callback",
        )

    def test_override_gana(self):
        _env.set_env(self, TURNOS_RENAPER_REDIRECT_URL="https://x.example/cb")
        self.assertEqual(auth_renaper._redirect_uri(), "https://x.example/cb")


if __name__ == "__main__":
    unittest.main()
