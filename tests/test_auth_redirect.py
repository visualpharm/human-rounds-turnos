"""test_auth_redirect.py — auth_redirect_target / _account_or_renaper (2026-08-03).

Kills the interstitial screen before login: an email link to an auth-needed
patient page (/my-requests, /request/<id>, /account) used to always render
the account page's own login/register form first, and even RENAPER-mode
installs only bounced there client-side. Now, before serving anything, an
anonymous visitor on a renaper install gets a straight 302 to
/auth/renaper?next=<original target> (auth_renaper.py, which already knows
how to carry `next` through the whole login round-trip). Password-mode
installs are untouched — accounts.auth_redirect_target always returns None
for them.

New file (not test_accounts.py / test_booking_requests.py) for the same
reason test_booking_request_detail.py gives itself one: those two belong to
another concurrently-working agent.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"
"""
import unittest
from unittest import mock
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.cuentas import accounts
from app.cuentas import accounts_router
from app.cuentas import auth_renaper
from app.patient_web import site_shell
from app.patient_web import patient_site_router


_APP = FastAPI()
_APP.include_router(accounts_router.router)
_APP.include_router(patient_site_router.router)
_CLIENT = TestClient(
    _APP,
    base_url="http://127.0.0.1:7700",
    follow_redirects=False,
    client=("127.0.0.1", 12345),
)


def _request(path):
    """Build an actual Starlette request for pure auth-target unit tests."""
    raw_path, _, raw_query = path.partition("?")
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode(),
        "query_string": raw_query.encode(),
        "headers": [(b"host", b"127.0.0.1:7700")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 7700),
    })


_RENAPER_ON = {"password": False, "renaper": True}
_PASSWORD_ONLY = {"password": True, "renaper": False}


class TestAuthRedirectTarget(unittest.TestCase):
    def test_password_mode_nunca_redirige(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_PASSWORD_ONLY):
            self.assertIsNone(accounts.auth_redirect_target(None, "/account"))

    def test_con_sesion_no_redirige_aunque_renaper_este_activo(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_RENAPER_ON):
            self.assertIsNone(accounts.auth_redirect_target(
                {"dni": "30111222"}, "/account"))

    def test_sin_sesion_y_renaper_activo_redirige_directo_con_next(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_RENAPER_ON):
            destino = accounts.auth_redirect_target(None, "/account#turnos")
        self.assertIsNotNone(destino)
        self.assertTrue(destino.startswith("/auth/renaper?next="))
        # auth_renaper._sanitizar_next debe aceptar el next tal cual llega
        # (no degradar a /account por defecto) — así el paciente vuelve al
        # target real, no al home de la cuenta.
        next_val = destino.split("next=", 1)[1]
        self.assertEqual(auth_renaper._sanitizar_next(unquote(next_val)), "/account#turnos")


class TestAccountOrRenaper(unittest.TestCase):
    def test_renaper_activo_sin_sesion(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_RENAPER_ON):
            destino = site_shell._account_or_renaper(None, "/account#turnos")
        self.assertTrue(destino.startswith("/auth/renaper?next="))

    def test_password_mode_cae_al_target_de_siempre(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_PASSWORD_ONLY):
            self.assertEqual(
                site_shell._account_or_renaper(None, "/account#turnos"),
                "/account#turnos",
            )


class TestRutasSirvenElRedirect(unittest.TestCase):
    """End-to-end through the native patient and account routers."""

    def test_my_requests_anonimo_renaper_va_directo_a_auth_renaper(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_RENAPER_ON), \
                mock.patch("app.cuentas.accounts.current_user", return_value=None):
            response = _CLIENT.get("/my-requests")
        self.assertEqual(response.status_code, 302)
        loc = response.headers.get("Location")
        self.assertTrue(loc.startswith("/auth/renaper?next="))
        self.assertEqual(
            auth_renaper._sanitizar_next(unquote(loc.split("next=", 1)[1])),
            "/account#turnos",
        )

    def test_my_requests_password_mode_sigue_igual(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_PASSWORD_ONLY):
            response = _CLIENT.get("/my-requests")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/account#turnos")

    def test_request_id_anonimo_renaper_va_directo_a_auth_renaper_con_my_requests(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_RENAPER_ON), \
                mock.patch("app.cuentas.accounts.current_user", return_value=None):
            response = _CLIENT.get("/request/123")
        self.assertEqual(response.status_code, 302)
        loc = response.headers.get("Location")
        self.assertTrue(loc.startswith("/auth/renaper?next="))
        self.assertEqual(
            auth_renaper._sanitizar_next(unquote(loc.split("next=", 1)[1])),
            "/my-requests",
        )

    def test_request_id_password_mode_sigue_igual(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_PASSWORD_ONLY):
            response = _CLIENT.get("/request/123")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("Location"), "/my-requests")

    def test_account_anonimo_renaper_va_directo_sin_pintar_el_formulario(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_RENAPER_ON), \
                mock.patch("app.cuentas.accounts.current_user", return_value=None):
            response = _CLIENT.get("/account")
        self.assertEqual(response.status_code, 302)
        loc = response.headers.get("Location")
        self.assertTrue(loc.startswith("/auth/renaper?next="))
        self.assertEqual(
            auth_renaper._sanitizar_next(unquote(loc.split("next=", 1)[1])),
            "/account",
        )
        # No se escribió ningún cuerpo HTML — el 302 salió antes de pintar nada.
        self.assertEqual(response.content, b"")

    def test_account_con_sesion_pinta_normal_aunque_renaper_este_activo(self):
        with mock.patch("app.cuentas.auth_renaper.metodos", return_value=_RENAPER_ON), \
                mock.patch("app.cuentas.accounts.current_user", return_value={"dni": "30111222", "nombre": "Ana"}), \
                mock.patch("app.modules.turnos.booking_patient.patient_rows", return_value=[]):
            response = _CLIENT.get("/account")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
