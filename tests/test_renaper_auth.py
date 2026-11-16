"""Focused coverage for the federated test-identity provider flow."""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import _dbtest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core import config
from app.core import proxy_trust
from app.cuentas import accounts
from app.cuentas import accounts_router
from app.auth import mode as auth_mode
from app.core import db_accounts
from app.core import db_catalog
from app.connectors.renaper import renaper
import _env  # noqa: E402


class TestIdentityRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = os.path.join(self.tmp.name, "identities.json")
        _env.set_env(self, TURNOS_TEST_IDENTITIES_PATH=self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_registry_starts_with_three_visibly_synthetic_people(self):
        items = renaper.list_test_identities()
        self.assertEqual(len(items), 3)
        self.assertEqual([item["dni"] for item in items], ["99000001", "99000002", "99000003"])
        self.assertEqual({item["origen"] for item in items}, {"predefinida"})
        self.assertTrue(all(item["email"].endswith("@example.invalid") for item in items))

    def test_random_person_is_added_and_persisted(self):
        created = renaper.create_random_identity()
        self.assertEqual(created["origen"], "aleatoria")
        self.assertTrue(created["dni"].startswith("99"))
        self.assertEqual(len(renaper.list_test_identities()), 4)
        self.assertTrue(os.path.exists(self.store))

    def test_manual_person_can_be_created_and_edited_including_dni(self):
        created = renaper.save_test_identity({
            "dni": "40111222", "nombre": "Ada", "apellido": "Prueba",
            "nacimiento": "1991-06-02", "sexo": "F", "contacto": "2254550000",
            "email": "ada@example.invalid", "domicilio": "Shaw 50",
            "localidad": "Pinamar", "origen": "manual",
        })
        edited = dict(created, dni="40111223", nombre="Adela", origen="editada")
        renaper.save_test_identity(edited, original_dni="40111222")
        self.assertIsNone(renaper.get_test_identity("40111222"))
        self.assertEqual(renaper.get_test_identity("40111223")["nombre"], "Adela")

    def test_registered_identity_wins_over_anything_else(self):
        self.assertEqual(renaper.authenticate("99.000.001", "stub")["nombre"], "Sofía")
        # ...even when a caller ALSO hands in existing-account data: a
        # hand-registered identity in Sistema always outranks the account.
        otro = {"nombre": "Otro", "apellido": "Nombre", "nacimiento": "2000-01-01", "sexo": "F"}
        self.assertEqual(renaper.authenticate("99000001", "stub", existing_user=otro)["nombre"], "Sofía")
        with self.assertRaises(renaper.ProviderUnavailable):
            renaper.authenticate("99000001", "real")

    def test_any_dni_logs_in_by_synthesizing_and_persisting_a_persona(self):
        # The product owner, 2026-08-03: "I should be able to log in using the stub with
        # any DNI" — no more identidad_no_encontrada dead end.
        persona = renaper.authenticate("30123456", "stub")
        self.assertEqual(persona["dni"], "30123456")
        self.assertEqual(persona["origen"], "aleatoria")
        self.assertEqual(len(renaper.list_test_identities()), 4)
        # persisted: a second login is the SAME person, not a new roll.
        again = renaper.authenticate("30123456", "stub")
        self.assertEqual((again["nombre"], again["apellido"]), (persona["nombre"], persona["apellido"]))

    def test_existing_account_is_refused_not_copied(self):
        # Never clobber a real account's civil data — and never copy it into
        # the test registry either (2026-10-23, problems.md №5): a DNI with a
        # real account and no registered test person is refused.
        cuenta = {
            "nombre": "Ilya", "apellido": "Belov", "nacimiento": "1990-05-04",
            "sexo": "M", "contacto": "2254123456", "email": "ilya@example.invalid",
            "domicilio": "Shaw 50", "localidad": "Pinamar",
        }
        with self.assertRaises(renaper.IdentityNotFound):
            renaper.authenticate("20456781", "stub", existing_user=cuenta)
        self.assertIsNone(renaper.get_test_identity("20456781"))

    def test_invalid_length_dni_is_still_rejected(self):
        with self.assertRaises(renaper.InvalidIdentity):
            renaper.authenticate("123", "stub")
        with self.assertRaises(renaper.InvalidIdentity):
            renaper.authenticate("1234567890", "stub")


_renaper_db = _dbtest.PerModuleDb("sana_turnos_test_renaper",
                                   seed=lambda: db_catalog.catalog_list("renaper_test_identity"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _renaper_db.set_up()
    HAVE_DB, DB_SKIP_REASON = _renaper_db.have_db, _renaper_db.skip_reason


def tearDownModule():
    _renaper_db.tear_down()


class TestRegistryDbPersistence(unittest.TestCase):
    """DB-backed storage (default from 2026-08-03) is what actually survives
    a Coolify redeploy — no persistent volume is mounted at /app/data on the
    prod app (confirmed via Coolify's API). These exercise the real thing
    against a throwaway Postgres database, not a mock."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "Postgres no accesible")
        _env.set_env(self, TURNOS_TEST_IDENTITIES_PATH=None)
        self.tmp = tempfile.TemporaryDirectory()
        self.legacy_path = os.path.join(self.tmp.name, "legacy-identities.json")
        self.path_patch = patch.object(renaper, "DEFAULT_STORE_PATH", self.legacy_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()
        for row in db_catalog.catalog_list("renaper_test_identity"):
            db_catalog.catalog_delete_row("renaper_test_identity", row["id"])

    def test_registry_survives_a_redeploy_via_old_path_migration(self):
        # The bug this fixes: an identity registered on the old, per-container
        # file path is gone the moment the container is rebuilt. Simulate
        # that — the file exists, but the DB (the new location) is empty.
        with open(self.legacy_path, "w", encoding="utf-8") as f:
            json.dump([{
                "dni": "20456781", "nombre": "Ilya", "apellido": "Belov",
                "nacimiento": "1990-05-04", "sexo": "M", "contacto": "2254123456",
                "email": "ilya@example.invalid", "domicilio": "Shaw 50",
                "localidad": "Pinamar", "origen": "manual",
            }], f)
        self.assertTrue(renaper._use_db())
        migrado = renaper.get_test_identity("20456781")
        self.assertIsNotNone(migrado)
        self.assertEqual(migrado["nombre"], "Ilya")
        # Now really persisted in Postgres — survives even if the old file
        # (and everything else in the container) is gone.
        os.unlink(self.legacy_path)
        self.assertEqual(renaper.get_test_identity("20456781")["nombre"], "Ilya")


_APP = FastAPI()
_APP.include_router(accounts_router.router)


class TestIdentityProviderRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_values = {
            "TURNOS_AUTH_MODE": "renaper",
            "TURNOS_RENAPER_PROVIDER": "stub",
            "TURNOS_ASGI": "1",
            "TURNOS_TEST_IDENTITIES_PATH": os.path.join(self.tmp.name, "identities.json"),
            # Real signing secret so the native request peer marker verifies under a
            # TURNOS_ASGI=1 CI pass (proxy_trust only trusts a marker when a
            # real secret is configured — fail-closed otherwise).
            "TURNOS_PROXY_TRUST_SECRET": "test-proxy-trust-renaper",
        }
        _env.set_env(self, self.env_values)
        self.old_config_env_path = config.ENV_PATH
        self.old_auth_mode_cache = dict(auth_mode._cache)
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        # This class exercises the RENAPER transport, not the independent
        # DB-backed mode selector. Pin its declared mode so an earlier test
        # module's three-second process cache cannot override this fixture.
        auth_mode._cache["v"] = "renaper"
        auth_mode._cache["t"] = time.time()

    def _client(self, ip="127.0.0.1", host="127.0.0.1:7700"):
        return TestClient(
            _APP,
            base_url=f"http://{host}",
            follow_redirects=False,
            client=(ip, 54321),
            headers={proxy_trust.HEADER: proxy_trust.sign(ip)},
        )

    def tearDown(self):
        auth_mode._cache.clear()
        auth_mode._cache.update(self.old_auth_mode_cache)
        config.ENV_PATH = self.old_config_env_path
        self.tmp.cleanup()
        config._reset_cache_para_tests()

    def test_public_config_exposes_test_provider_without_secrets(self):
        response = self._client().get("/api/account/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auth_mode"], "renaper")
        self.assertEqual(response.json()["renaper_provider"], "stub")
        self.assertTrue(response.json()["test_identity_provider"])
        self.assertNotIn("secret", response.json())

    def test_page_has_provider_context_table_and_all_identity_columns(self):
        response = self._client().get("/auth/test?provider=mi_argentina")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Simulador de autenticación RENAPER", html)
        self.assertIn("Crear persona aleatoria", html)
        self.assertIn("Solo pruebas locales.", html)
        self.assertIn("No es un servicio oficial de RENAPER", html)
        self.assertIn("logo_argentina-azul.svg", html)
        self.assertIn('class="tid-agency"><strong>RENAPER</strong>', html)
        self.assertIn("--tid-navy:#252d4f", html)
        self.assertNotIn('id="stznav"', html)
        self.assertNotIn('src="/nav.js', html)
        self.assertNotIn('href="/human-rounds.css', html)
        self.assertNotIn("Acerca de Sana Turnos", html)
        for label in ("DNI", "Nombre", "Apellido", "Sesión", "Datos", "Nacimiento", "Sexo",
                      "Teléfono", "Email", "Domicilio", "Localidad", "Origen"):
            self.assertIn("<th>%s</th>" % label, html)
        self.assertIn(".tid-actions{display:flex;flex-wrap:nowrap", html)
        self.assertIn(".tid-actions{display:grid;grid-template-columns:1fr;width:100%;}", html)
        self.assertIn(".tid-actions .tid-btn{width:100%;}", html)
        self.assertIn(".tid-table thead{display:none;}", html)
        self.assertIn('.tid-table td:nth-child(4)::before{content:"Sesión";}', html)

    def test_get_create_and_random_routes_mutate_the_registry(self):
        client = self._client()
        listed = client.get("/api/account/test-identities")
        # A runner once answered without "identidades" (KeyError, no body in
        # the log): keep the status and body in the failure message. The body
        # was then 404 test_auth_no_disponible, which any of the three gate
        # inputs of accounts._test_auth_enabled explains — name them all.
        if listed.status_code != 200:
            gate = {
                # Age first: reading the mode refreshes an expired cache.
                "auth_mode_cache_age_s": round(time.time() - auth_mode._cache["t"], 3),
                "auth_mode": accounts._auth_mode(),
                "renaper_provider": accounts._renaper_provider(),
                "stub_desplegado": accounts._stub_desplegado(),
                "real_peer": proxy_trust.real_peer_from(
                    {proxy_trust.HEADER: client.headers.get(proxy_trust.HEADER)}, "127.0.0.1"),
                "TURNOS_ASGI": os.environ.get("TURNOS_ASGI"),
            }
            self.fail("%s %s; gate now: %s" % (listed.status_code, listed.text, gate))
        self.assertIn("identidades", listed.json(), listed.text)
        self.assertEqual(len(listed.json()["identidades"]), 3)

        manual = client.post("/api/account/test-identities", json={"identidad": {
            "dni": "40111222", "nombre": "Ada", "apellido": "Prueba",
            "nacimiento": "1991-06-02", "sexo": "F", "email": "ada@example.invalid",
        }})
        self.assertEqual(manual.status_code, 200)

        random_response = client.post("/api/account/test-identities/random", json={})
        self.assertEqual(random_response.status_code, 201)
        self.assertEqual(len(renaper.list_test_identities()), 5)

    def test_selected_person_creates_session_with_provider_identity(self):
        person = renaper.list_test_identities()[0]
        created = dict(person, cobertura=None, avisos=None)
        client = self._client()
        # 3 calls: accounts._login_test_identity's own existing-account probe
        # (None — no account yet), then _persist_provider_identity's own
        # existente check (None again) and its final post-create fetch.
        with patch.object(accounts.store, "get_user", side_effect=[None, None, created]), \
             patch.object(accounts.store, "create_user") as create_user, \
             patch.object(accounts.store, "create_session", return_value="session-token"):
            response = client.post(
                "/api/account/test-login",
                json={"dni": person["dni"], "provider": "mi_argentina"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["usuario"]["dni"], "99000001")
        self.assertEqual(response.json()["auth_provider"], "mi_argentina")
        self.assertIn("Set-Cookie", response.headers)
        self.assertEqual(create_user.call_args.args[:6],
                         ("99000001", "Sofía", "Herrera", "1953-08-11", "F", "2254990001"))
        self.assertEqual(create_user.call_args.kwargs["email"], "sofia.99000001@example.invalid")

    def test_password_route_cannot_bypass_provider_handoff(self):
        response = self._client().post(
            "/api/account/login", json={"dni": "99000001"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "proveedor_de_identidad_requerido")

    def test_test_routes_disappear_when_stub_is_off(self):
        with _env.patched_env({"TURNOS_RENAPER_PROVIDER": "real"}, clear=False):
            response = self._client().get("/api/account/test-identities")
        self.assertEqual(response.status_code, 404)

    def test_stub_routes_and_federated_ui_are_loopback_only(self):
        # OJO: 203.0.113.x (TEST-NET) NO es "global" para ipaddress — con el
        # auth_mode renaper guardado en la config del admin, ese peer pasaba
        # el gate de red local y el test fallaba por estado de la DB dev. Un
        # peer realmente público es lo que este test quiere.
        client = self._client(ip="8.8.8.8")
        config = client.get("/api/account/config")
        self.assertEqual(config.json()["auth_mode"], "password")
        self.assertFalse(config.json()["test_identity_provider"])

        listed = client.get("/api/account/test-identities")
        self.assertEqual(listed.status_code, 404)

        login = client.post("/api/account/test-login", json={
            "dni": "99000001", "provider": "renaper",
        })
        self.assertEqual(login.status_code, 404)

    def test_lan_peer_with_dev_host_enables_the_stub(self):
        # devbox:7700 desde la misma máquina/red: peer LAN + Host de una etiqueta.
        config = self._client(
            ip="192.168.1.40", host="devbox:7700").get("/api/account/config")
        self.assertEqual(config.json()["auth_mode"], "renaper")
        self.assertTrue(config.json()["test_identity_provider"])

    def test_private_peer_with_public_host_keeps_the_stub_off(self):
        # Coolify: el peer es la red docker pero el Host es el dominio público.
        config = self._client(
            ip="172.18.0.5",
            host="sana-turnos.brieflysites.com",
        ).get("/api/account/config")
        self.assertEqual(config.json()["auth_mode"], "password")
        self.assertFalse(config.json()["test_identity_provider"])

    def test_deployed_installation_can_opt_into_the_simulator(self):
        # Pinamar muestra el login de RENAPER mientras llegan las credenciales
        # reales (responsable del producto, 2026-08-02). El opt-in es una env explícita por
        # instalación — el demo público no la tiene y sigue en contraseña.
        # Desde 2026-10-23 exige además una instalación demo
        # (TURNOS_DEMO_MODE=1): con pacientes reales no hay simulador.
        publico = self._client(
            ip="8.8.8.8", host="demo.humanrounds.org").get("/api/account/config")
        self.assertEqual(publico.json()["auth_mode"], "password")

        with _env.patched_env({"TURNOS_RENAPER_STUB_DEPLOYED": "1",
                                     "TURNOS_DEMO_MODE": "1"}, clear=False):
            client = self._client(ip="8.8.8.8", host="pinamar.humanrounds.org")
            optin = client.get("/api/account/config")
            self.assertEqual(optin.json()["auth_mode"], "renaper")
            self.assertTrue(optin.json()["test_identity_provider"])
            page = client.get("/auth/sso")
            self.assertEqual(page.status_code, 200)

    def test_sso_page_serves_locally_and_hides_publicly(self):
        page = self._client().get("/auth/sso")
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertIn("mero de Documento", html)   # "Número de Documento" (entidad)
        self.assertIn("/api/account/test-login", html)

        hidden = self._client(ip="8.8.8.8").get("/auth/sso")
        self.assertEqual(hidden.status_code, 404)

    def test_sso_page_asks_only_for_the_document_and_remembers_it(self):
        # El documento es el único campo: el sexo no aporta nada en un
        # simulador y era una cosa más para elegir mal (responsable del producto, 2026-08-02).
        page = self._client().get("/auth/sso")
        html = page.text
        self.assertNotIn('name="sso-sexo"', html)
        self.assertNotIn("No Binario", html)
        self.assertNotIn("sexo_no_coincide", html)
        # …y el número tipeado la última vez vuelve escrito.
        self.assertIn('localStorage.getItem(RECORDADO)', html)
        self.assertIn('localStorage.setItem(RECORDADO, dni.value)', html)

    def test_sso_login_validates_document_sex(self):
        # Nuestra página ya no lo manda, pero el back sigue validándolo para
        # cualquier cliente que sí lo mande (el SSO real lo pide).
        person = renaper.list_test_identities()[0]  # Sofía Herrera, F
        # Sex mismatch is resolved before persistence; do not let this
        # credential-boundary isolation test depend on a developer database.
        with patch.object(db_accounts, "get_user", return_value=None):
            wrong = self._client().post("/api/account/test-login", json={
                "dni": person["dni"], "provider": "renaper", "sexo": "M",
            })
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.json()["error"], "sexo_no_coincide")

    def test_unimplemented_real_provider_falls_back_to_password_login(self):
        with _env.patched_env({"TURNOS_RENAPER_PROVIDER": "real"}, clear=False):
            response = self._client().get("/api/account/config")
        self.assertEqual(response.json()["auth_mode"], "password")
        self.assertFalse(response.json()["test_identity_provider"])


class TestFederatedFrontendContract(unittest.TestCase):
    def test_federated_login_navigates_to_the_sso_page(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "static", "platform", "account-form.mjs"), encoding="utf-8") as source:
            frontend = source.read()
        # El login federado ES el SSO: navegación de página completa a
        # /auth/sso (estilo sso-renaper), sin overlay ni iframe embebido.
        self.assertIn('"/auth/sso?provider="', frontend)
        self.assertIn("Ingresar con RENAPER", frontend)
        self.assertIn('window.location.replace(ssoUrl("renaper"))', frontend)
        self.assertNotIn('className = "sc-provider-overlay"', frontend)
        self.assertNotIn("sana-test-auth-complete", frontend)
        with open(os.path.join(root, "static", "platform", "auth-test.mjs"), encoding="utf-8") as source:
            simulator = source.read()
        self.assertIn('class="tid-person" tabindex="0" data-dni=', simulator)
        self.assertIn('event.target.closest(".tid-person")', simulator)
        # El batch 2 de i18n envolvió el título en T(...) (namespace
        # account.mine.auth_test.title) — el texto de respaldo (lo que se ve
        # sin catálogo cargado) sigue siendo el mismo, sólo cambió la forma.
        self.assertIn('var simulatorTitle = T(', simulator)
        self.assertIn('"Simulador de autenticación " + providerLabel', simulator)
        self.assertIn('document.title = simulatorTitle;', simulator)
        self.assertIn('Volviendo a la aplicación', simulator)
        self.assertNotIn('Volviendo a Sana Turnos', simulator)


if __name__ == "__main__":
    unittest.main()
