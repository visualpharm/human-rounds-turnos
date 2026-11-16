"""El simulador de identidad no toca cuentas reales (2026-10-23).

Hallazgo (problems.md №5, base de prod 21.10): con el simulador prendido en
una instalación desplegada, un login con el DNI de un paciente real abría SU
cuenta y copiaba su teléfono, email y domicilio al registro de personas de
prueba (catálogo `renaper_test_identity`, origen `cuenta_existente`), que
`GET /api/account/test-identities` devuelve a cualquiera que vea el
simulador. Ahora:

  - el simulador sólo entra con personas de prueba: un DNI que ya tiene una
    cuenta propia y no está registrado como persona de prueba se rechaza
    (409 `cuenta_real_no_simulable`) y no se copia nada;
  - en una instalación desplegada el simulador exige además el modo demo
    (`TURNOS_DEMO_MODE=1`): `TURNOS_RENAPER_STUB_DEPLOYED=1` solo ya no
    alcanza en una instalación con pacientes reales.
"""

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import mode as auth_mode
from app.connectors.renaper import renaper
from app.core import config
from app.core import proxy_trust
from app.cuentas import accounts
from app.cuentas import accounts_router
import _env  # noqa: E402

_CIVIL = {
    "nombre": "Real", "apellido": "Paciente", "nacimiento": "1970-02-03",
    "sexo": "F", "contacto": "2254111111", "email": "real@example.invalid",
    "domicilio": "Shaw 50", "localidad": "Pinamar",
}


class TestStubNeverCopiesARealAccount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.tmp.name, "identities.json")
        _env.set_env(self, TURNOS_TEST_IDENTITIES_PATH=self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_account_is_refused_and_nothing_is_copied(self):
        with self.assertRaises(renaper.IdentityNotFound):
            renaper.authenticate("30111222", "stub", existing_user=_CIVIL)
        dnis = {item["dni"] for item in renaper.list_test_identities()}
        self.assertNotIn("30111222", dnis)
        self.assertNotIn("cuenta_existente",
                         {item["origen"] for item in renaper.list_test_identities()})

    def test_a_leftover_copy_of_the_account_is_refused_too(self):
        # The purge script keeps a copy whose DNI has imported history; that
        # copy must not open the account either.
        renaper.save_test_identity(dict(_CIVIL, dni="30111223", origen="cuenta_existente"))
        with self.assertRaises(renaper.IdentityNotFound):
            renaper.authenticate("30111223", "stub", existing_user=_CIVIL)

    def test_registered_test_person_still_logs_in(self):
        self.assertEqual(renaper.authenticate("99000001", "stub")["nombre"], "Sofía")


_APP = FastAPI()
_APP.include_router(accounts_router.router)


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _env.set_env(self, {"TURNOS_AUTH_MODE": "renaper", "TURNOS_RENAPER_PROVIDER": "stub", "TURNOS_ASGI": "1", "TURNOS_TEST_IDENTITIES_PATH": os.path.join(self.tmp.name, "identities.json"), "TURNOS_PROXY_TRUST_SECRET": "test-proxy-trust-renaper-real", "TURNOS_RENAPER_STUB_DEPLOYED": None, "TURNOS_DEMO_MODE": None})
        self.old_env_path = config.ENV_PATH
        self.old_cache = dict(auth_mode._cache)
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        auth_mode._cache["v"] = "renaper"
        auth_mode._cache["t"] = time.time()

    def tearDown(self):
        auth_mode._cache.clear()
        auth_mode._cache.update(self.old_cache)
        config.ENV_PATH = self.old_env_path
        self.tmp.cleanup()
        config._reset_cache_para_tests()

    def _client(self, ip="127.0.0.1", host="127.0.0.1:7700"):
        return TestClient(_APP, base_url=f"http://{host}", follow_redirects=False,
                          client=(ip, 54321),
                          headers={proxy_trust.HEADER: proxy_trust.sign(ip)})

    def test_login_with_a_real_accounts_dni_is_refused(self):
        cuenta = dict(_CIVIL, dni="30111222", activo=True)
        with patch.object(accounts.store, "get_user", return_value=cuenta), \
             patch.object(accounts.store, "update_user") as update_user, \
             patch.object(accounts.store, "create_session") as create_session:
            response = self._client().post(
                "/api/account/test-login", json={"dni": "30111222", "provider": "renaper"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "cuenta_real_no_simulable")
        update_user.assert_not_called()
        create_session.assert_not_called()
        listed = self._client().get("/api/account/test-identities").json()["identidades"]
        self.assertNotIn("30111222", {item["dni"] for item in listed})

    def test_a_new_test_person_cannot_take_a_real_accounts_dni(self):
        cuenta = dict(_CIVIL, dni="30111222", activo=True)
        with patch.object(accounts.store, "get_user", return_value=cuenta):
            response = self._client().post("/api/account/test-identities", json={"identidad": {
                "dni": "30111222", "nombre": "Otra", "apellido": "Persona",
                "nacimiento": "1990-01-01", "sexo": "F"}})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(renaper.get_test_identity("30111222"))

    def test_deployed_opt_in_still_refuses_a_real_account(self):
        # Whatever the deployment gate (TURNOS_RENAPER_STUB_DEPLOYED=1 on a
        # demo installation), a real account's DNI is refused and nothing of
        # it is copied.
        cuenta = dict(_CIVIL, dni="30111222", activo=True)
        with _env.patched_env({"TURNOS_RENAPER_STUB_DEPLOYED": "1",
                                     "TURNOS_DEMO_MODE": "1"}, clear=False), \
             patch.object(accounts.store, "get_user", return_value=cuenta), \
             patch.object(accounts.store, "create_session") as create_session:
            client = self._client(ip="8.8.8.8", host="pinamar.humanrounds.org")
            response = client.post(
                "/api/account/test-login", json={"dni": "30111222", "provider": "renaper"})
        self.assertEqual(response.status_code, 409)
        create_session.assert_not_called()
        self.assertIsNone(renaper.get_test_identity("30111222"))

    def test_deployed_opt_in_alone_is_not_enough(self):
        with _env.patched_env({"TURNOS_RENAPER_STUB_DEPLOYED": "1"}, clear=False):
            client = self._client(ip="8.8.8.8", host="pinamar.humanrounds.org")
            self.assertEqual(client.get("/api/account/config").json()["auth_mode"], "password")
            self.assertEqual(client.get("/api/account/test-identities").status_code, 404)

    def test_deployed_opt_in_works_on_a_demo_installation(self):
        with _env.patched_env({"TURNOS_RENAPER_STUB_DEPLOYED": "1",
                                     "TURNOS_DEMO_MODE": "1"}, clear=False):
            client = self._client(ip="8.8.8.8", host="demo.example.invalid")
            self.assertEqual(client.get("/api/account/config").json()["auth_mode"], "renaper")
            self.assertEqual(client.get("/api/account/test-identities").status_code, 200)

    def test_editing_a_leftover_copy_does_not_turn_it_into_a_test_person(self):
        # The simulator's edit form sends no `origen`, so saving a leftover
        # copy used to rewrite it as 'manual' — and the next login with that
        # DNI opened the real account again.
        renaper.save_test_identity(dict(_CIVIL, dni="30111224", origen="cuenta_existente"))
        cuenta = dict(_CIVIL, dni="30111224", activo=True)
        with patch.object(accounts.store, "get_user", return_value=cuenta):
            response = self._client().post("/api/account/test-identities", json={
                "original_dni": "30111224", "identidad": {
                    "dni": "30111224", "nombre": "Otra", "apellido": "Persona",
                    "nacimiento": "1990-01-01", "sexo": "F"}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "cuenta_real_no_simulable")
        self.assertEqual(renaper.get_test_identity("30111224")["origen"], "cuenta_existente")

    def test_a_test_person_with_its_own_account_can_still_be_edited(self):
        # An account the simulator itself opened for a test person is a test
        # account: editing that person keeps working.
        renaper.save_test_identity(dict(_CIVIL, dni="30111225", origen="manual"))
        cuenta = dict(_CIVIL, dni="30111225", activo=True)
        with patch.object(accounts.store, "get_user", return_value=cuenta):
            response = self._client().post("/api/account/test-identities", json={
                "original_dni": "30111225", "identidad": {
                    "dni": "30111225", "nombre": "Otra", "apellido": "Persona",
                    "nacimiento": "1990-01-01", "sexo": "F"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(renaper.get_test_identity("30111225")["nombre"], "Otra")

    def test_the_list_never_shows_a_leftover_copy(self):
        # Until the purge runs, a copy still carries a real patient's phone,
        # email and address: the simulator's list must not hand them out.
        renaper.save_test_identity(dict(_CIVIL, dni="30111226", origen="cuenta_existente"))
        listed = self._client().get("/api/account/test-identities").json()["identidades"]
        dnis = {item["dni"] for item in listed}
        self.assertNotIn("30111226", dnis)
        self.assertIn("99000001", dnis)


if __name__ == "__main__":
    unittest.main()
