"""test_admin_users.py — pruebas de admin_users.py (CRUD de operadores)
+ el rol/identidad de admin_auth.py (login de operador, TOTP por cuenta,
desactivación, token viejo rechazado) + la auditoría `resuelta_por` que
booking_requests.py escribe al aprobar/rechazar.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_admin_users -v

Sigue el patrón de test_admin_content.py: base dedicada
`sana_turnos_test_admin_usuarios` (conectándose a la maintenance db
`postgres`) y los routers FastAPI reales con `TestClient`. Turnstile apagado con el mismo truco que
test_accounts.py (repuntar ENV_PATH — NUNCA parchear turnstile._secret, que es
compartido entre los tests de otros módulos del mismo proceso). Las env vars
del dueño se fijan ANTES de importar nada para que el .env real del repo
(con secretos de producción) nunca se cuele en un test.
"""

import os
import sys
import base64
import json
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pyotp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="")   # determinístico: el dueño no pide 2FA acá
_env.process_set(TURNOS_TURNSTILE_SECRET="")

from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.auth import admin_auth      # noqa: E402
from app.admin_panel import admin_users  # noqa: E402
from app.admin_panel import admin_account   # noqa: E402
from app.patient_web import catalog        # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

_CLIENT = TestClient(app_module.app)

_RFC_TOTP_VECTOR = "JBSWY3DPEHPK3PXP"

from app.auth import turnstile         # noqa: E402
from app.core import config  # noqa: E402
turnstile.ENV_PATH = ""  # mismo truco que test_accounts.py: no leer el .env real
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev

# Fixture de médicos AISLADA del webdata real (mismo patrón que
# test_admin_content.py) — la alta/edición de una cuenta 'medico' valida
# medico_id contra el catálogo, así que los tests de rol 'medico' necesitan
# un catálogo determinístico, no los 99 profesionales reales.
_MEDICOS_FIXTURE = {"medicos": [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología",
     "email": "maria.fernandez@example.com"},
    {"id": "med-2", "nombre": "Lucía Torres", "especialidad": "Medicina general",
     "email": "lucia.torres@example.com"},
    {"id": "med-3", "nombre": "Andrés Rojas", "especialidad": "Clínica médica",
     "email": "andres.rojas@example.com"},
]}

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_admin_usuarios", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    # Invitation and recovery mails carry links; without a public origin they are not sent.
    _env.set_env_for_module(TURNOS_PUBLIC_ORIGIN="https://example.health.invalid")
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE admin_account_security")
            cur.execute("TRUNCATE admin_preferences")
            cur.execute("TRUNCATE admin_reset_tokens")
            cur.execute("TRUNCATE catalog")


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


def _totp_now(secret):
    return pyotp.TOTP(secret).now()


def _out(h):
    """Decode a native FastAPI response body."""
    return h.json()


class AdminUsuariosTestCase(unittest.TestCase):
    def setUp(self):
        _CLIENT.cookies.clear()
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()  # el throttle es global al proceso — aislar entre tests
        self._tmp = tempfile.mkdtemp(prefix="admin-usuarios-test-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir, exist_ok=True)
        with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
            json.dump(_MEDICOS_FIXTURE, f)
        self.addCleanup(self._reset_catalog)   # registered first, so it runs after the environment is put back
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        self._reset_catalog()

    @staticmethod
    def _reset_catalog():
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- CRUD happy paths -------------------------------------------------

    def test_crear_listar_editar_desactivar(self):
        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "op-carla", "password": "clave1234", "rol": "admisionista"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        self.assertEqual(h.json()["item"]["usuario"], "op-carla")
        self.assertEqual(h.json()["item"]["rol"], "admisionista")
        self.assertNotIn("totp", h.json())

        h = _CLIENT.request("GET", "/api/admin/users", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        usuarios = [it["usuario"] for it in h.json()["items"]]
        self.assertIn("op-carla", usuarios)
        self.assertIn(admin_auth._admin_user(), usuarios)

        h = _CLIENT.request("PUT", "/api/admin/users/op-carla", json={"rol": "admin", "activo": False}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["rol"], "admin")
        self.assertFalse(h.json()["item"]["activo"])

        row = db_operators.operator_get("op-carla")
        self.assertEqual(row["rol"], "admin")
        self.assertFalse(row["activo"])

        h = _CLIENT.request("DELETE", "/api/admin/users/op-carla", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertFalse(db_operators.operator_get("op-carla")["activo"])

    def test_preferencias_de_listas_se_guardan_por_usuario(self):
        headers = _cookie("mesa-1", "admisionista")
        h = _CLIENT.request("PUT", "/api/admin/preferences", json={"key": "patients.search", "value": "30111"}, headers=headers)
        self.assertEqual(h.status_code, 200)

        h = _CLIENT.request("GET", "/api/admin/preferences", headers=headers)
        self.assertEqual(h.json()["items"]["patients.search"], "30111")

        h = _CLIENT.request("GET", "/api/admin/preferences", headers=_cookie("mesa-2", "admisionista"))
        self.assertNotIn("patients.search", h.json()["items"])

    def test_estado_de_navegacion_se_guarda_por_usuario_en_el_servidor(self):
        headers = _cookie("mesa-1", "admisionista")
        preferencias = {
            "nav.groups": {
                "atencion": False,
                "organizacion": True,
                "sistema": False,
            },
            "nav.last_screen": "pacientes/30111222",
        }
        for key, value in preferencias.items():
            h = _CLIENT.request("PUT", "/api/admin/preferences", json={"key": key, "value": value}, headers=headers)
            self.assertEqual(h.status_code, 200)

        h = _CLIENT.request("GET", "/api/admin/preferences", headers=headers)
        self.assertEqual(h.json()["items"]["nav.groups"], preferencias["nav.groups"])
        self.assertEqual(h.json()["items"]["nav.last_screen"], "pacientes/30111222")

    def test_invitar_medico_crea_usuario_y_envia_reset(self):
        sent = {}

        def fake_send(to, subject, text):
            sent.update(to=to, subject=subject, text=text)
            return True

        with patch("app.notifications.mailgun.send", side_effect=fake_send):
            h = _CLIENT.request("POST", "/api/admin/users/actions/invite", json={"medico_ids": ["med-1"]}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["completados"], 1)
        item = db_operators.operator_by_doctor_id("med-1")
        self.assertEqual(item["email"], "maria.fernandez@example.com")
        self.assertIn("/admin/reset-password?token=", sent["text"])

    def test_creation_email_becomes_the_doctors_one_email(self):
        for action, mid in (("create", "med-1"), ("invite", "med-2")):
            doctor = {"id": mid, "nombre": "Synthetic Doctor", "especialidad": "Test"}
            with patch.object(catalog, "doctors", return_value=[doctor]), \
                    patch("app.notifications.mailgun.send", return_value=True):
                response = _CLIENT.post("/api/admin/users/actions/" + action,
                    json={"medico_ids": [mid], "usuario": "doctor-" + mid,
                          "email": "new-" + mid + "@example.test"}, headers=_admin_headers())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["completados"], 1, response.text)
            self.assertEqual(db_operators.operator_by_doctor_id(mid)["email"], "new-" + mid + "@example.test")
            self.assertEqual(db_catalog.catalog_get("medicos", mid)["doc"]["email"], "new-" + mid + "@example.test")
        response = _CLIENT.post("/api/admin/users", json={"usuario": "direct-doctor",
            "password": "synthetic-password", "rol": "medico", "medico_id": "med-3",
            "email": "different@example.test"}, headers=_admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        # One email per doctor (2026-11-03): the account's replaces the
        # record's instead of leaving two that disagree.
        self.assertEqual(next(m for m in catalog.doctors() if m["id"] == "med-3")["email"],
                         "different@example.test")

    def test_bulk_invitation_uses_individual_emails_and_keeps_row_errors(self):
        with patch("app.notifications.mailgun.send", return_value=True) as send:
            response = _CLIENT.post("/api/admin/users/actions/invite", json={
                "medico_ids": ["med-1", "med-2", "med-3", "missing"],
                "emails": {"med-1": "first@example.test", "med-2": "invalid",
                           "med-3": "third@example.test"}}, headers=_admin_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["ok"] for r in response.json()["resultados"]], [True, False, True, False])
        self.assertEqual([c.args[0] for c in send.call_args_list],
                         ["first@example.test", "third@example.test"])
        links = [next(line for line in call.args[2].splitlines() if "/admin/reset-password?token=" in line)
                 for call in send.call_args_list]
        self.assertEqual(len(set(links)), 2)
        self.assertIsNone(db_operators.operator_by_doctor_id("med-2"))
        for result, call in zip([r for r in response.json()["resultados"] if r["ok"]], send.call_args_list):
            self.assertNotIn("password", result)
            self.assertIn(result["usuario"], call.args[2])
            self.assertIsNotNone(admin_auth.account_security(
                {"usuario": result["usuario"], "rol": "medico"})["2fa_deadline"])

    def test_supplied_invalid_email_never_creates_account(self):
        for action in ("create", "invite"):
            response = _CLIENT.post("/api/admin/users/actions/" + action, json={
                "medico_ids": ["med-1"], "usuario": "new-doctor", "email": "bad-address"},
                headers=_admin_headers())
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["resultados"][0]["ok"])
            self.assertIsNone(db_operators.operator_by_doctor_id("med-1"))

    def test_bulk_invitation_hands_back_the_link_when_email_fails(self):
        with patch("app.notifications.mailgun.send", return_value=False):
            response = _CLIENT.post("/api/admin/users/actions/invite", json={
                "medico_ids": ["med-1"], "emails": {"med-1": "first@example.test"}},
                headers=_admin_headers())
        self.assertEqual(response.status_code, 200)
        row = response.json()["resultados"][0]
        self.assertTrue(row["ok"])
        self.assertFalse(row["email_enviado"])
        # The email did not leave: the one-use link comes back to copy and
        # hand over; the random password is never revealed (2026-10-19).
        self.assertNotIn("password", row)
        self.assertIn("/admin/reset-password?token=", row["enlace"])
        token = row["enlace"].split("token=", 1)[1]
        self.assertTrue(db_auth.admin_reset_token_status(token))
        item = db_operators.operator_get_public(row["usuario"])
        self.assertTrue(item["activo"])
        self.assertEqual(item["cuenta_estado"], "invitado")

    def test_bulk_invitation_without_email_returns_a_link_and_sends_nothing(self):
        doctor = {"id": "med-2", "nombre": "Sin Correo"}
        with patch.object(admin_users, "_doctors_by_ids", return_value=[doctor]), \
                patch("app.notifications.mailgun.send") as send:
            response = _CLIENT.post("/api/admin/users/actions/invite", json={
                "medico_ids": ["med-2"]}, headers=_admin_headers())
        row = response.json()["resultados"][0]
        self.assertTrue(row["ok"], row)
        send.assert_not_called()
        self.assertFalse(row["email_enviado"])
        self.assertIn("/admin/reset-password?token=", row["enlace"])
        self.assertEqual(db_operators.operator_by_doctor_id("med-2")["usuario"], row["usuario"])

    def test_operator_email_login_is_unique_case_insensitive_and_preserves_totp(self):
        db_operators.operator_create("email-doctor", "synthetic-password", "medico",
                           email="Doctor@Example.test", totp_secret=_RFC_TOTP_VECTOR)
        response = _CLIENT.post("/api/admin/login", json={
            "usuario": " doctor@example.TEST ", "password": "synthetic-password"})
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.json().get("need_totp"))
        response = _CLIENT.post("/api/admin/login", json={
            "usuario": "doctor@example.test", "password": "synthetic-password",
            "codigo": _totp_now(_RFC_TOTP_VECTOR)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["usuario"], "email-doctor")
        db_operators.operator_create("duplicate-email", "other-password", "medico", email="doctor@example.test")
        self.assertIsNone(admin_auth._authenticate("doctor@example.test", "synthetic-password"))

    def test_recovery_by_login_or_email_is_neutral_and_token_is_one_use(self):
        from app.auth import password_reset
        from urllib.parse import urlsplit, parse_qs
        db_operators.operator_create("recover-doctor", "old-password", "medico", email="recover@example.test")
        responses = []
        for identifier in ("recover-doctor", "RECOVER@example.test", "absent"):
            with patch.object(password_reset, "_allow_request", return_value=True), \
                    patch("app.notifications.mailgun.send", return_value=True) as send:
                response = _CLIENT.post("/api/admin/reset-password", json={"action": "request", "usuario": identifier})
            self.assertEqual(response.status_code, 200)
            responses.append(response.json())
            if identifier == "absent":
                send.assert_not_called()
                continue
            text = send.call_args.args[2]
            link = next(line for line in text.splitlines() if "/admin/reset-password?token=" in line)
            token = parse_qs(urlsplit(link).query)["token"][0]
            changed = _CLIENT.post("/api/admin/reset-password", json={"token": token, "password": "new-password"})
            self.assertEqual(changed.status_code, 200)
            self.assertIsNotNone(db_operators.operator_verify_password("recover-doctor", "new-password"))
            self.assertEqual(_CLIENT.post("/api/admin/reset-password", json={"token": token, "password": "other-password"}).status_code, 400)
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[1], responses[2])

    def test_recovery_link_cannot_reenable_a_disabled_doctor(self):
        db_operators.operator_create("disabled-doctor", "old-password", "medico", email="disabled@example.test")
        token = db_auth.admin_reset_token_create("disabled-doctor")
        db_operators.operator_deactivate("disabled-doctor")
        response = _CLIENT.post("/api/admin/reset-password", json={"token": token, "password": "new-password"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(db_operators.operator_get("disabled-doctor")["activo"])

    def test_expired_and_replaced_links_cannot_change_password(self):
        db_operators.operator_create("token-doctor", "old-password", "medico", email="token@example.test")
        expired = db_auth.admin_reset_token_create("token-doctor", horas=-1)
        self.assertFalse(db_auth.admin_reset_token_status(expired))
        self.assertIsNone(db_auth.admin_reset_token_consume(expired, "new-password"))
        old = db_auth.admin_reset_token_create("token-doctor")
        latest = db_auth.admin_reset_token_create("token-doctor")
        self.assertIsNone(db_auth.admin_reset_token_consume(old, "new-password"))
        self.assertEqual(db_auth.admin_reset_token_consume(latest, "new-password"), "token-doctor")
        self.assertIsNone(db_auth.admin_reset_token_consume(latest, "other-password"))

    def test_direct_invitation_fills_catalog_email_and_never_returns_password(self):
        doctor = {"id": "med-1", "nombre": "Synthetic Doctor"}
        with patch.object(catalog, "doctors", return_value=[doctor]), \
                patch("app.notifications.mailgun.send", return_value=True) as send:
            response = _CLIENT.post("/api/admin/users", json={"usuario": "direct-invite",
                "rol": "medico", "medico_id": "med-1", "email": "direct@example.test",
                "access_mode": "invite"}, headers=_admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("password", response.json())
        self.assertTrue(response.json()["email_enviado"])
        self.assertEqual(db_catalog.catalog_get("medicos", "med-1")["doc"]["email"], "direct@example.test")
        self.assertIn("direct-invite", send.call_args.args[2])

    def test_direct_invitation_hands_back_the_link_when_email_fails(self):
        doctor = {"id": "med-nomail", "nombre": "Synthetic Doctor"}
        with patch.object(catalog, "doctors", return_value=[doctor]), \
                patch("app.notifications.mailgun.send", return_value=False):
            response = _CLIENT.post("/api/admin/users", json={"usuario": "invite-nomail",
                "rol": "medico", "medico_id": "med-nomail", "email": "nomail@example.test",
                "access_mode": "invite"}, headers=_admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["email_enviado"])
        # The email did not leave: the link to copy replaces the old one-time
        # password (2026-10-19) — nobody ever sees a generated password here.
        self.assertNotIn("password", body)
        self.assertTrue(db_auth.admin_reset_token_status(body["enlace"].split("token=", 1)[1]))
        # And with no email at all the invitation still works — link only.
        doctor_none = {"id": "med-noemail", "nombre": "Synthetic Doctor"}
        with patch.object(catalog, "doctors", return_value=[doctor_none]), \
                patch("app.notifications.mailgun.send") as send:
            none = _CLIENT.post("/api/admin/users", json={"usuario": "invite-noemail",
                "rol": "medico", "medico_id": "med-noemail", "access_mode": "invite"},
                headers=_admin_headers())
        self.assertEqual(none.status_code, 200, none.text)
        send.assert_not_called()
        self.assertIn("/admin/reset-password?token=", none.json()["enlace"])
        # Y con el email enviado sigue sin volver jamás.
        doctor_ok = {"id": "med-conmail", "nombre": "Synthetic Doctor"}
        with patch.object(catalog, "doctors", return_value=[doctor_ok]), \
                patch("app.notifications.mailgun.send", return_value=True):
            ok = _CLIENT.post("/api/admin/users", json={"usuario": "invite-mail",
                "rol": "medico", "medico_id": "med-conmail", "email": "ok@example.test",
                "access_mode": "invite"}, headers=_admin_headers())
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertNotIn("password", ok.json())

    def test_vincular_usuario_existente_con_medico_preserva_la_cuenta(self):
        db_operators.operator_create(
            "mesa-existente", "clave1234", "admisionista",
            totp_secret=_RFC_TOTP_VECTOR, email="mesa@example.com")

        h = _CLIENT.request(
            "POST", "/api/admin/users/actions/link",
            json={"medico_ids": ["med-1"], "usuario": "mesa-existente"},
            headers=_admin_headers())

        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["completados"], 1)
        item = db_operators.operator_get("mesa-existente")
        self.assertEqual(item["rol"], "medico")
        self.assertEqual(item["medico_id"], "med-1")
        self.assertEqual(item["email"], "mesa@example.com")
        self.assertEqual(item["totp_secret"], _RFC_TOTP_VECTOR)
        self.assertTrue(item["activo"])
        self.assertIsNotNone(db_operators.operator_verify_password("mesa-existente", "clave1234"))

    def test_create_doctor_returns_working_password_only_once_without_email(self):
        doctor = {"id": "med-1", "nombre": "María Fernández"}
        with patch.object(admin_users, "_doctors_by_ids", return_value=[doctor]), \
                patch("app.notifications.mailgun.send") as send:
            response = _CLIENT.post("/api/admin/users/actions/create",
                                    json={"medico_ids": ["med-1"], "usuario": "maria.fernandez"},
                                    headers=_admin_headers())
        self.assertEqual(response.status_code, 200)
        result = response.json()["resultados"][0]
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(result["password"]), 16)
        self.assertIsNotNone(db_operators.operator_verify_password(result["usuario"], result["password"]))
        self.assertEqual(db_operators.operator_by_doctor_id("med-1")["usuario"], result["usuario"])
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertNotIn(result["password"], json.dumps(admin_users.list_users()))
        self.assertNotIn(result["password"], json.dumps(db_operators.operator_get(result["usuario"]), default=str))
        self.assertIsNotNone(admin_auth.account_security({"usuario": result["usuario"], "rol": "medico"})["2fa_deadline"])
        send.assert_not_called()

    def test_create_doctor_rejects_duplicates_owner_invalid_name_and_multiple_doctors(self):
        db_operators.operator_create("taken-user", "clave1234", "admisionista")
        for username, ids, status in [
                ("taken-user", ["med-1"], 409), ("admin", ["med-1"], 409),
                ("x", ["med-1"], 400), ("", ["med-1"], 400),
                ("new-user", ["med-1", "med-2"], 400)]:
            response = _CLIENT.post("/api/admin/users/actions/create",
                                    json={"medico_ids": ids, "usuario": username},
                                    headers=_admin_headers())
            self.assertEqual(response.status_code, status, response.text)
        self.assertIsNone(db_operators.operator_by_doctor_id("med-1"))
        db_operators.operator_create("linked-user", "clave1234", "medico", medico_id="med-1")
        response = _CLIENT.post("/api/admin/users/actions/create",
                                json={"medico_ids": ["med-1"], "usuario": "new-user"},
                                headers=_admin_headers())
        self.assertEqual(response.json()["errores"], 1)
        self.assertIsNone(db_operators.operator_get("new-user"))

    def test_create_doctor_is_admin_only(self):
        body = {"medico_ids": ["med-1"], "usuario": "new-user"}
        self.assertEqual(_CLIENT.post("/api/admin/users/actions/create", json=body).status_code, 401)
        for role in ("medico", "admisionista", "reportes"):
            username = "role-" + role
            db_operators.operator_create(username, "clave1234", role)
            response = _CLIENT.post("/api/admin/users/actions/create", json=body,
                                    headers=_cookie(username, role))
            self.assertEqual(response.status_code, 403)
        self.assertIsNone(db_operators.operator_get("new-user"))

    def test_concurrent_doctor_account_writers_allow_only_one_assignment(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)

        def create(username):
            barrier.wait(timeout=5)
            try:
                db_operators.operator_create(username, "clave1234", "medico", medico_id="med-1")
                return "created"
            except db_operators.OperatorConflict as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, ["concurrent-a", "concurrent-b"]))
        self.assertCountEqual(results, ["created", "medico_con_usuario"])
        db_operators.operator_create("unassigned", "clave1234", "admisionista")
        with self.assertRaises(db_operators.OperatorConflict):
            db_operators.operator_update("unassigned", rol="medico", medico_id="med-1")
        self.assertIsNone(db_operators.operator_get("unassigned")["medico_id"])

    def test_link_guard_and_username_conflicts_are_safe(self):
        db_operators.operator_create("assigned", "clave1234", "medico", medico_id="med-1")
        with self.assertRaisesRegex(db_operators.OperatorConflict, "usuario_ya_vinculado"):
            db_operators.operator_update("assigned", rol="medico", medico_id="med-2", link_only=True)
        with self.assertRaisesRegex(db_operators.OperatorConflict, "nombre_ya_existe"):
            db_operators.operator_create("assigned", "clave1234", "admisionista")
        self.assertEqual(db_operators.operator_get("assigned")["medico_id"], "med-1")

    def test_vincular_no_mueve_usuario_de_otro_medico_ni_reemplaza_cuenta(self):
        db_operators.operator_create("dra-vinculada", "clave1234", "medico", medico_id="med-1")
        db_operators.operator_create("mesa-existente", "clave1234", "admisionista")

        h = _CLIENT.request(
            "POST", "/api/admin/users/actions/link",
            json={"medico_ids": ["med-1"], "usuario": "mesa-existente"},
            headers=_admin_headers())

        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["errores"], 1)
        self.assertEqual(db_operators.operator_get("mesa-existente")["rol"], "admisionista")
        self.assertEqual(db_operators.operator_by_doctor_id("med-1")["usuario"], "dra-vinculada")

        db_operators.operator_create("dra-otra", "clave1234", "medico", medico_id="med-2")
        h = _CLIENT.request(
            "POST", "/api/admin/users/actions/link",
            json={"medico_ids": ["med-3"], "usuario": "dra-otra"},
            headers=_admin_headers())

        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["errores"], 1)
        self.assertEqual(db_operators.operator_get("dra-otra")["medico_id"], "med-2")
        self.assertIsNone(db_operators.operator_by_doctor_id("med-3"))

    def test_vincular_exige_un_usuario_y_un_medico(self):
        for body in ({"medico_ids": ["med-1"]},
                     {"medico_ids": ["med-1", "med-2"], "usuario": "alguien"}):
            h = _CLIENT.request(
                "POST", "/api/admin/users/actions/link", json=body,
                headers=_admin_headers())
            self.assertEqual(h.status_code, 400)

    def test_todos_los_roles_se_pueden_crear_de_verdad(self):
        """Guarda contra el desfasaje entre admin_users._ROLES y el CHECK de
        `operadores.rol` en Postgres: 'admisionista' vivió un día en la lista
        de roles sin estar en el CHECK, así que darlo de alta reventaba la
        conexión (respuesta vacía, ni siquiera un 500). Un rol nuevo se agrega
        en los dos lados o este test lo caza."""
        for i, rol in enumerate(admin_users._ROLES):
            if rol == "medico":
                continue  # exige medico_id: tiene sus propios tests
            usuario = "rol-%d" % i
            h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": usuario, "password": "clave1234", "rol": rol}, headers=_admin_headers())
            self.assertEqual(h.status_code, 200, "%s: %r" % (rol, h.json()))
            self.assertEqual(db_operators.operator_get(usuario)["rol"], rol)

    def test_alta_con_totp_devuelve_el_secreto_una_sola_vez(self):
        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "op-2fa", "password": "clave1234", "rol": "admisionista", "totp": True}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIn("totp", h.json())
        self.assertTrue(h.json()["totp"]["secret"])
        self.assertIn("otpauth://totp/", h.json()["totp"]["otpauth_url"])

        h = _CLIENT.request("GET", "/api/admin/users", headers=_admin_headers())
        item = [it for it in h.json()["items"] if it["usuario"] == "op-2fa"][0]
        self.assertTrue(item["tiene_2fa"])
        self.assertNotIn("totp_secret", item)  # el secreto crudo nunca se relee

    def test_alta_puede_exigir_que_la_persona_configure_2fa_al_ingresar(self):
        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "op-pendiente", "password": "clave1234",
                              "rol": "admisionista", "forzar_2fa": True}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertFalse(h.json()["item"]["tiene_2fa"])
        self.assertTrue(h.json()["item"]["requiere_configurar_2fa"])
        self.assertIsNone(db_operators.operator_get("op-pendiente")["totp_secret"])

    def test_forzar_nuevo_2fa_revoca_el_actual_y_se_limpia_al_configurarlo(self):
        db_operators.operator_create("op-reinicio", "clave1234", "admisionista",
                          totp_secret=_RFC_TOTP_VECTOR)
        h = _CLIENT.request("PUT", "/api/admin/users/op-reinicio", json={"forzar_2fa": True}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIsNone(db_operators.operator_get("op-reinicio")["totp_secret"])
        self.assertTrue(h.json()["item"]["requiere_configurar_2fa"])

        # La persona termina el alta desde Cuenta: guardar el secreto limpia
        # el bloqueo/checkbox sin una segunda acción del administrador.
        admin_account._set_totp("op-reinicio", _RFC_TOTP_VECTOR)
        h = _CLIENT.request("GET", "/api/admin/users", headers=_admin_headers())
        item = [it for it in h.json()["items"] if it["usuario"] == "op-reinicio"][0]
        self.assertTrue(item["tiene_2fa"])
        self.assertFalse(item["requiere_configurar_2fa"])

    def test_contrasena_corta_o_usuario_invalido_rechazados(self):
        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "op-corta", "password": "123", "rol": "admisionista"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "a", "password": "clave1234", "rol": "admisionista"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

    # ---- permisos: operador vs admin --------------------------------------

    def test_operador_entra_a_solicitudes_pero_no_a_usuarios(self):
        db_operators.operator_create("op-solo-cola", "clave1234", "admisionista")
        headers = _cookie("op-solo-cola", "admisionista")

        h = _CLIENT.request("GET", "/api/admin/requests", headers=headers)
        self.assertEqual(h.status_code, 200)  # la cola es de rol operador Y admin

        h = _CLIENT.request("GET", "/api/admin/users", headers=headers)
        self.assertEqual(h.status_code, 403)  # usuarios es sólo rol admin

        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "x", "password": "clave1234"}, headers=headers)
        self.assertEqual(h.status_code, 403)

        h = _CLIENT.request("PUT", "/api/admin/users/op-solo-cola", json={"activo": False}, headers=headers)
        self.assertEqual(h.status_code, 403)

    def test_operador_contenido_solo_dia_a_dia(self):
        # Reunión 2026-07-17: el operador edita lo existente (agendas del día
        # a día — la matriz fina vive en test_admin_content.TestRolOperador)
        # pero crear/eliminar y los centros siguen siendo sólo rol admin.
        headers = _cookie("op-solo-cola", "admisionista")
        for method, path, body in (
            ("POST", "/api/admin/content/services", {"nombre": "X"}),
            ("DELETE", "/api/admin/content/services/lab", None),
            ("PUT", "/api/admin/content/centers/hospital", {"nombre": "X"}),
        ):
            h = _CLIENT.request(method, path, json=body, headers=headers)
            self.assertEqual(h.status_code, 403, f"{method} {path}")

        # ver el catálogo sí puede (lo necesita para editar agendas)
        h = _CLIENT.request("GET", "/api/admin/content/services", headers=headers)
        self.assertEqual(h.status_code, 200)

        # el rol admin sí entra
        h = _CLIENT.request("GET", "/api/admin/content/services", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)

    def test_operador_con_rol_admin_si_entra_a_usuarios(self):
        db_operators.operator_create("op-admin2", "clave1234", "admin")
        headers = _cookie("op-admin2", "admin")
        h = _CLIENT.request("GET", "/api/admin/users", headers=headers)
        self.assertEqual(h.status_code, 200)

    def test_sin_sesion_401(self):
        h = _CLIENT.request("GET", "/api/admin/users")
        self.assertEqual(h.status_code, 401)

    # ---- el dueño no se toca desde acá ------------------------------------

    def test_dueno_no_editable_ni_desactivable_ni_duplicable(self):
        owner = admin_auth._admin_user()
        h = _CLIENT.request("PUT", f"/api/admin/users/{owner}", json={"rol": "admisionista"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

        h = _CLIENT.request("DELETE", f"/api/admin/users/{owner}", headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": owner, "password": "clave1234"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 409)

    # ---- ...pero SÍ se edita a sí mismo, desde Cuenta ----------------------
    # (responsable del producto, 2026-07-27: "no puedo editar mi perfil"). El dueño no tiene fila
    # en `operadores`, así que su email vive en los overrides y la lista de
    # Usuarios lo mostraba siempre vacío.

    def test_el_dueno_edita_su_email_desde_cuenta_y_se_ve_en_la_lista(self):
        owner = admin_auth._admin_user()
        h = _CLIENT.request("POST", "/api/admin/account/profile", json={"email": "owner@patient_site.gob.ar"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(admin_auth.owner_email(), "owner@patient_site.gob.ar")

        h = _CLIENT.request("GET", "/api/admin/account", headers=_admin_headers())
        self.assertEqual(h.json()["email"], "owner@patient_site.gob.ar")

        h = _CLIENT.request("GET", "/api/admin/users", headers=_admin_headers())
        fila = [it for it in h.json()["items"] if it["usuario"] == owner][0]
        self.assertEqual(fila["email"], "owner@patient_site.gob.ar")

    def test_un_email_invalido_no_se_guarda(self):
        h = _CLIENT.request("POST", "/api/admin/account/profile", json={"email": "sin-arroba"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)
        self.assertIsNone(admin_auth.owner_email())

    def test_vaciar_el_email_lo_borra(self):
        for valor, esperado in (("owner@patient_site.gob.ar", "owner@patient_site.gob.ar"), ("", None)):
            h = _CLIENT.request("POST", "/api/admin/account/profile", json={"email": valor}, headers=_admin_headers())
            self.assertEqual(h.status_code, 200)
            self.assertEqual(admin_auth.owner_email(), esperado)

    def test_un_operador_edita_su_propio_email(self):
        db_operators.operator_create("op-mesa", "clave1234", "admisionista")
        h = _CLIENT.request("POST", "/api/admin/account/profile", json={"email": "mesa@patient_site.gob.ar"}, headers=_cookie("op-mesa", "admisionista"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(db_operators.operator_get("op-mesa")["email"], "mesa@patient_site.gob.ar")

    def test_quitar_2fa_pide_la_contrasena_y_nada_mas(self):
        """el responsable del producto, 2026-07-27: pedir contraseña Y código junto es justo lo que no
        puede dar quien perdió el teléfono. Reautenticar con UN factor ya
        enrolado alcanza (OWASP; GitHub lo dice como un O)."""
        secreto = admin_users._gen_totp_secret()
        db_operators.operator_create("op-2fa", "clave1234", "admisionista", secreto)
        headers = _cookie("op-2fa", "admisionista")

        h = _CLIENT.request("POST", "/api/admin/account/2fa/remove", json={"actual": "la-que-no-es"}, headers=headers)
        self.assertEqual(h.status_code, 400)
        self.assertTrue(db_operators.operator_get("op-2fa")["totp_secret"])

        h = _CLIENT.request("POST", "/api/admin/account/2fa/remove", json={"actual": "clave1234"}, headers=headers)
        self.assertEqual(h.status_code, 200)
        self.assertFalse(db_operators.operator_get("op-2fa")["totp_secret"])

    def test_la_ultima_visita_del_dueno_queda_registrada(self):
        owner = admin_auth._admin_user()
        self.assertIsNone(admin_auth.owner_ultimo_acceso())
        admin_auth.marcar_acceso(owner)
        self.assertIsNotNone(admin_auth.owner_ultimo_acceso())
        h = _CLIENT.request("GET", "/api/admin/users", headers=_admin_headers())
        fila = [it for it in h.json()["items"] if it["usuario"] == owner][0]
        self.assertTrue(fila["ultimo_acceso"])

    def test_dueno_sigue_logueando_con_operadores_en_la_base(self):
        db_operators.operator_create("op-cualquiera", "clave1234", "admisionista")
        identity = admin_auth._authenticate(admin_auth._admin_user(), admin_auth._admin_pass())
        self.assertIsNotNone(identity)
        self.assertEqual(identity["rol"], "admin")

    # ---- login end-to-end: desactivado / TOTP por cuenta -------------------

    def test_operador_desactivado_no_puede_loguear(self):
        db_operators.operator_create("op-baja", "clave1234", "admisionista")
        db_operators.operator_deactivate("op-baja")
        h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-baja", "password": "clave1234"})
        self.assertEqual(h.status_code, 401)
        self.assertEqual(_out(h).get("error"), "credenciales")

    def test_operador_con_totp_necesita_el_codigo(self):
        secret = _RFC_TOTP_VECTOR
        db_operators.operator_create("op-totp", "clave1234", "admisionista", totp_secret=secret)

        h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-totp", "password": "clave1234"})
        self.assertEqual(h.status_code, 401)
        self.assertTrue(_out(h).get("need_totp"))

        h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-totp", "password": "clave1234", "codigo": "000000"})
        self.assertEqual(h.status_code, 401)
        self.assertEqual(_out(h).get("error"), "codigo")

        h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-totp", "password": "clave1234", "codigo": _totp_now(secret)})
        self.assertEqual(h.status_code, 200)
        out = _out(h)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("rol"), "admisionista")

    def test_operador_sin_totp_loguea_directo(self):
        db_operators.operator_create("op-simple", "clave1234", "admisionista")
        h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-simple", "password": "clave1234"})
        self.assertEqual(h.status_code, 200)
        out = _out(h)
        self.assertEqual(out.get("usuario"), "op-simple")
        self.assertEqual(out.get("rol"), "admisionista")
        self.assertIsNotNone(db_operators.operator_get("op-simple")["ultimo_acceso"])

    # ---- excepción por IP a Turnstile (ip_exempt.py, 2026-08-03) -----------

    def test_ip_exenta_no_llama_a_verificar(self):
        with patch.object(admin_auth.ip_exempt, "exento_from", return_value=True), \
             patch.object(admin_auth.turnstile, "verificar_from") as mock_verificar:
            self.assertTrue(admin_auth._turnstile_ok_from({}, "127.0.0.1", ""))
            mock_verificar.assert_not_called()

    def test_ip_no_exenta_sigue_llamando_a_verificar(self):
        with patch.object(admin_auth.ip_exempt, "exento_from", return_value=False), \
             patch.object(admin_auth.turnstile, "verificar_from", return_value=False) as mock_verificar:
            self.assertFalse(admin_auth._turnstile_ok_from({}, "127.0.0.1", ""))
            mock_verificar.assert_called_once()

    def test_ip_exenta_loguea_sin_token_de_turnstile(self):
        db_operators.operator_create("op-ts-exento", "clave1234", "admisionista")
        with patch.object(admin_auth.ip_exempt, "exento_from", return_value=True), \
             patch.object(admin_auth.turnstile, "verificar_from", return_value=False):
            h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-ts-exento", "password": "clave1234"})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(_out(h).get("usuario"), "op-ts-exento")

    def test_ip_no_exenta_sigue_exigiendo_turnstile(self):
        db_operators.operator_create("op-ts-no-exento", "clave1234", "admisionista")
        with patch.object(admin_auth.ip_exempt, "exento_from", return_value=False), \
             patch.object(admin_auth.turnstile, "verificar_from", return_value=False):
            h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-ts-no-exento", "password": "clave1234"})
        self.assertEqual(h.status_code, 403)
        self.assertEqual(_out(h).get("error"), "turnstile")

    def test_credenciales_y_totp_no_se_saltean_por_estar_exento(self):
        """La excepción es SOLO sobre el token de Turnstile — password y TOTP
        siguen valiendo igual (requisito 4 del pedido)."""
        secret = _RFC_TOTP_VECTOR
        db_operators.operator_create("op-ts-totp", "clave1234", "admisionista", totp_secret=secret)
        with patch.object(admin_auth.ip_exempt, "exento_from", return_value=True), \
             patch.object(admin_auth.turnstile, "verificar_from", return_value=False):
            h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-ts-totp", "password": "mal"})
            self.assertEqual(h.status_code, 401)
            self.assertEqual(_out(h).get("error"), "credenciales")

            h = _CLIENT.request("POST", "/api/admin/login", json={"usuario": "op-ts-totp", "password": "clave1234"})
            self.assertTrue(_out(h).get("need_totp"))

    # ---- token del esquema viejo (sin rol) se rechaza solo -----------------

    def test_token_esquema_viejo_no_verifica(self):
        payload = json.dumps({"u": "admin", "exp": int(time.time()) + 3600},
                             separators=(",", ":")).encode("utf-8")
        p = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        import hmac as _hmac
        import hashlib as _hashlib
        sig = _hmac.new(admin_auth._signing_key(), p.encode("ascii"), _hashlib.sha256).hexdigest()
        old_token = f"{p}.{sig}"
        self.assertIsNone(admin_auth._verify_token(old_token))

    # ---- auditoría resuelta_por ---------------------------------------------

    def test_resuelta_por_en_aprobar_y_rechazar(self):
        db_operators.operator_create("op-audit", "clave1234", "admisionista")
        sid1 = db_booking.create_booking_request("40222333", "Marta", "Diaz", "1980-01-01",
                                  "wa:11", "cardiologia", "hospital-pinamar")
        sid2 = db_booking.create_booking_request("40222334", "Nico", "Ruiz", "1990-02-02",
                                  "wa:11", "cardiologia", "hospital-pinamar")

        h = _CLIENT.request("POST", f"/api/admin/requests/{sid1}/approve", json={"turno_dt": "2026-08-01T09:00"}, headers=_cookie("op-audit", "admisionista"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["solicitud"]["resuelta_por"], "op-audit")
        self.assertEqual(db_booking.get_booking_request(sid1)["resuelta_por"], "op-audit")
        self.assertIsNotNone(db_booking.get_booking_request(sid1)["resuelta_en"])

        h = _CLIENT.request("POST", f"/api/admin/requests/{sid2}/reject", json={"motivo": "La orden está vencida."}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        owner = admin_auth._admin_user()
        self.assertEqual(h.json()["solicitud"]["resuelta_por"], owner)
        self.assertEqual(db_booking.get_booking_request(sid2)["resuelta_por"], owner)

    # ---- rol 'medico': exige medico_id válido del catálogo -----------------

    def test_alta_medico_sin_medico_id_400(self):
        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "dr-x", "password": "clave1234", "rol": "medico"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

    def test_alta_medico_con_id_inexistente_400(self):
        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "dr-x", "password": "clave1234", "rol": "medico",
                              "medico_id": "no-existe"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)

    def test_alta_medico_con_id_valido_200(self):
        h = _CLIENT.request("POST", "/api/admin/users", json={"usuario": "dr-fernandez", "password": "clave1234", "rol": "medico",
                              "medico_id": "med-1"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["rol"], "medico")
        self.assertEqual(h.json()["item"]["medico_id"], "med-1")

    def test_editar_rol_a_medico_exige_medico_id_valido(self):
        db_operators.operator_create("op-a-medico", "clave1234", "admisionista")
        h = _CLIENT.request("PUT", "/api/admin/users/op-a-medico", json={"rol": "medico"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 400)  # sin medico_id, ni antes ni ahora

        h = _CLIENT.request("PUT", "/api/admin/users/op-a-medico", json={"rol": "medico", "medico_id": "med-1"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(db_operators.operator_doctor_id("op-a-medico"), "med-1")

    def test_editar_fuera_de_medico_limpia_medico_id(self):
        db_operators.operator_create("dr-y", "clave1234", "medico", medico_id="med-1")
        h = _CLIENT.request("PUT", "/api/admin/users/dr-y", json={"rol": "admisionista"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIsNone(db_operators.operator_doctor_id("dr-y"))

    def test_medico_ascendido_a_admin_conserva_su_medico(self):
        # El administrador del sitio (02/11): «al ser médica no me deja agregarle ese perfil».
        # Un médico que pasa a administrador sigue siendo ese médico: su cuenta
        # sigue vinculada (agenda y pacientes propios) y el catálogo la ve.
        db_operators.operator_create("dra-admin", "clave1234", "medico", medico_id="med-1",
                           email="dra.admin@example.test")
        h = _CLIENT.request("PUT", "/api/admin/users/dra-admin", json={"rol": "admin"},
                            headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["rol"], "admin")
        self.assertEqual(h.json()["item"]["medico_id"], "med-1")
        self.assertEqual(db_operators.operator_doctor_id("dra-admin"), "med-1")
        self.assertEqual(db_operators.operator_by_doctor_id("med-1")["usuario"], "dra-admin")
        self.assertIn("med-1", db_operators.operator_doctor_ids())

        # Editarla otra vez desde Usuarios (que manda medico_id vacío para todo
        # rol que no es médico) no corta el vínculo.
        h = _CLIENT.request("PUT", "/api/admin/users/dra-admin",
                            json={"rol": "admin", "medico_id": "", "centros": []},
                            headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(db_operators.operator_doctor_id("dra-admin"), "med-1")

        # Sigue valiendo «una cuenta por médico»: ni una cuenta nueva ni otro
        # usuario pueden tomar ese médico.
        db_operators.operator_create("otro", "clave1234", "admisionista")
        with self.assertRaises(db_operators.OperatorConflict):
            db_operators.operator_update("otro", rol="medico", medico_id="med-1")

        # Y vuelve a médico sin pedir el médico de nuevo (con otra persona que
        # administre: la guarda del último administrador sigue en pie).
        h = _CLIENT.request("PUT", "/api/admin/users/dra-admin", json={"rol": "medico"},
                            headers=_admin_headers())
        self.assertEqual(h.status_code, 409)
        db_operators.operator_create("segundo-admin", "clave1234", "admin")
        h = _CLIENT.request("PUT", "/api/admin/users/dra-admin", json={"rol": "medico"},
                            headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["rol"], "medico")
        self.assertEqual(db_operators.operator_doctor_id("dra-admin"), "med-1")

    def test_admin_sin_medico_sigue_sin_medico_y_otros_roles_lo_limpian(self):
        db_operators.operator_create("admin-sin-medico", "clave1234", "admin")
        h = _CLIENT.request("PUT", "/api/admin/users/admin-sin-medico",
                            json={"rol": "admin", "medico_id": "med-2"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIsNone(db_operators.operator_doctor_id("admin-sin-medico"))
        # Pasar de administrador-médico a otro rol sigue limpiando el vínculo.
        db_operators.operator_create("dra-reportes", "clave1234", "medico", medico_id="med-3")
        _CLIENT.request("PUT", "/api/admin/users/dra-reportes", json={"rol": "admin"},
                        headers=_admin_headers())
        h = _CLIENT.request("PUT", "/api/admin/users/dra-reportes", json={"rol": "admisionista"},
                            headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIsNone(db_operators.operator_doctor_id("dra-reportes"))

    def test_deshabilitar_al_medico_no_apaga_su_cuenta_de_administrador(self):
        # Deshabilitar al médico del catálogo desactiva su login de médico; si
        # la cuenta es de administrador, la administración no es del médico.
        from app.admin_panel import admin_content
        db_operators.operator_create("dra-adm", "clave1234", "medico", medico_id="med-1")
        db_operators.operator_create("dr-med", "clave1234", "medico", medico_id="med-2")
        _CLIENT.request("PUT", "/api/admin/users/dra-adm", json={"rol": "admin"},
                        headers=_admin_headers())
        admin_content.delete_item("medicos", "med-1")
        admin_content.delete_item("medicos", "med-2")
        self.assertTrue(db_operators.operator_get("dra-adm")["activo"])
        self.assertFalse(db_operators.operator_get("dr-med")["activo"])

    def test_medico_logueado_no_ve_usuarios(self):
        db_operators.operator_create("dr-z", "clave1234", "medico", medico_id="med-1")
        headers = _cookie("dr-z", "medico")

        h = _CLIENT.request("GET", "/api/admin/users", headers=headers)
        self.assertEqual(h.status_code, 403)

        # El catálogo público SÍ lo lee desde 2026-08-05: es lo que necesita
        # para elegir el servicio al dar un hueco de su propia agenda, y son
        # los mismos servicios que ve cualquiera navegando el sitio. Editarlos
        # sigue siendo solo_admin.
        h = _CLIENT.request("GET", "/api/admin/content/services", headers=headers)
        self.assertEqual(h.status_code, 200)


class AlcancePorCentroTestCase(AdminUsuariosTestCase):
    """Una cuenta puede quedar acotada a uno o varios centros de salud
    (Gabriel, reunión hospital 2026-07-24: la mesa de un CAPS no tiene por qué
    ver la jornada de los otros nueve). Sin centros = todos, que es como venía
    funcionando y lo que sigue valiendo para cualquier cuenta vieja."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(os.environ["TURNOS_WEBDATA_DIR"], "centros-salud.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"centros": [{"slug": "caps-a", "name": "CAPS A"},
                                   {"slug": "caps-b", "name": "CAPS B"}]}, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def _crear(self, body):
        h = _CLIENT.request("POST", "/api/admin/users", json=body, headers=_admin_headers())
        return h

    def test_alta_con_centros_los_guarda(self):
        h = self._crear({"usuario": "mesa-a", "password": "clave1234",
                         "rol": "admisionista", "centros": ["caps-a"]})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["item"]["centros"], ["caps-a"])
        self.assertEqual(db_operators.operator_get("mesa-a")["centros"], ["caps-a"])

    def test_sin_centros_la_cuenta_ve_todo(self):
        h = self._crear({"usuario": "mesa-todo", "password": "clave1234", "rol": "admisionista"})
        self.assertEqual(h.json()["item"]["centros"], [])
        self.assertIsNone(admin_auth.centros_permitidos({"usuario": "mesa-todo"}))

    def test_una_cuenta_acotada_declara_sus_centros(self):
        self._crear({"usuario": "mesa-a", "password": "clave1234",
                     "rol": "admisionista", "centros": ["caps-a", "caps-b"]})
        self.assertEqual(admin_auth.centros_permitidos({"usuario": "mesa-a"}),
                         {"caps-a", "caps-b"})

    def test_un_centro_inventado_se_rechaza(self):
        # Un slug que no existe dejaría la cuenta sin ver NADA: el peor modo de
        # fallar para un permiso es en silencio.
        h = self._crear({"usuario": "mesa-x", "password": "clave1234",
                         "rol": "admisionista", "centros": ["no-existe"]})
        self.assertEqual(h.status_code, 400)
        self.assertIn("no existe", h.json()["error"])

    def test_un_admin_nunca_queda_acotado(self):
        h = self._crear({"usuario": "jefa", "password": "clave1234",
                         "rol": "admin", "centros": ["caps-a"]})
        self.assertEqual(h.json()["item"]["centros"], [])

    def test_editar_cambia_el_alcance_y_vaciar_lo_saca(self):
        self._crear({"usuario": "mesa-a", "password": "clave1234",
                     "rol": "admisionista", "centros": ["caps-a"]})
        h = _CLIENT.request("PUT", "/api/admin/users/mesa-a", json={"centros": ["caps-b"]}, headers=_admin_headers())
        self.assertEqual(h.json()["item"]["centros"], ["caps-b"])

        h2 = _CLIENT.request("PUT", "/api/admin/users/mesa-a", json={"centros": []}, headers=_admin_headers())
        self.assertEqual(h2.json()["item"]["centros"], [])
        self.assertIsNone(admin_auth.centros_permitidos({"usuario": "mesa-a"}))

    def test_editar_sin_mencionar_centros_no_los_toca(self):
        self._crear({"usuario": "mesa-a", "password": "clave1234",
                     "rol": "admisionista", "centros": ["caps-a"]})
        _CLIENT.request("PUT", "/api/admin/users/mesa-a", json={"activo": False}, headers=_admin_headers())
        self.assertEqual(db_operators.operator_get("mesa-a")["centros"], ["caps-a"])

    def test_el_dueno_del_env_nunca_queda_acotado(self):
        # No vive en la tabla de operadores: no hay dónde acotarlo.
        self.assertIsNone(admin_auth.centros_permitidos({"usuario": admin_auth._admin_user()}))


class DoctorOneEmailTestCase(unittest.TestCase):
    """One email per doctor (2026-11-03): the account's login/invitation/reset
    address and the record's «Email» are one value — every write of either
    writes the other."""

    def test_an_account_email_change_moves_the_doctors_record(self):
        doctor = {"id": "d1", "nombre": "Dr A", "email": "old@example.test"}
        with patch.object(admin_users, "_doctors_by_ids", return_value=[doctor]), \
                patch.object(db_catalog, "catalog_set_doctor_email") as set_email, \
                patch.object(catalog, "invalidate"):
            admin_users.follow_account_email({"usuario": "a", "medico_id": "d1"}, "new@example.test")
            admin_users.follow_account_email({"usuario": "a", "medico_id": "d1"}, None)
            admin_users.follow_account_email({"usuario": "a", "medico_id": "d1"}, " old@example.test ")
            admin_users.follow_account_email({"usuario": "staff"}, "x@example.test")
        self.assertEqual([c.args for c in set_email.call_args_list],
                         [(doctor, "new@example.test"), (doctor, None)])

    def test_the_doctors_email_change_moves_its_account(self):
        account = {"usuario": "dr.a", "email": "old@example.test"}
        with patch.object(db_operators, "operator_by_doctor_id", return_value=account), \
                patch.object(db_operators, "operator_update") as update:
            admin_users.doctor_email_changed("d1", " new@example.test ")
            admin_users.doctor_email_changed("d1", "")
            admin_users.doctor_email_changed("d1", "old@example.test")
        self.assertEqual([c.kwargs["email"] for c in update.call_args_list], ["new@example.test", None])
        self.assertEqual({c.args[0] for c in update.call_args_list}, {"dr.a"})
        with patch.object(db_operators, "operator_by_doctor_id", return_value=None), \
                patch.object(db_operators, "operator_update") as update:
            admin_users.doctor_email_changed("d1", "new@example.test")
        update.assert_not_called()

    def test_on_a_portal_installation_an_email_another_active_account_has_is_refused(self):
        account = {"usuario": "dr.a", "email": "old@example.test", "activo": True}
        with patch.object(db_operators, "operator_by_doctor_id", return_value=account), \
                patch.object(admin_users.staff_portal, "enabled", return_value=True), \
                patch.object(db_operators, "operator_active_email_taken", return_value=True) as taken:
            self.assertTrue(admin_users.email_conflict_for_doctor("d1", "other@example.test"))
            self.assertIsNone(admin_users.email_conflict_for_doctor("d1", "OLD@example.test"))
        taken.assert_called_once_with("other@example.test", except_usuario="dr.a")
        with patch.object(db_operators, "operator_by_doctor_id", return_value=account), \
                patch.object(admin_users.staff_portal, "enabled", return_value=False):
            self.assertIsNone(admin_users.email_conflict_for_doctor("d1", "other@example.test"))

    def test_the_doctor_form_refuses_a_taken_email_before_saving(self):
        from app.admin_panel import admin_content
        with patch.object(admin_users, "email_conflict_for_doctor", return_value="Ese email ya es de otra cuenta activa."), \
                patch.object(db_catalog, "catalog_schedule_lock") as lock, \
                patch.object(admin_users, "doctor_email_changed") as changed:
            with self.assertRaises(admin_content.ContentError) as ctx:
                admin_content.edit_item("medicos", "d1", {"nombre": "Dr A", "email": "other@example.test"})
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.payload["error"], "email_en_uso")
        lock.assert_not_called()
        changed.assert_not_called()


class DoctorOnboardingOfflineTestCase(unittest.TestCase):
    """Domain regressions runnable even when the disposable DB is blocked."""

    def test_create_action_forwards_entered_email_and_returns_password_once(self):
        doctor = {"id": "synthetic-1", "nombre": "Synthetic Doctor"}
        with patch.object(admin_users, "_doctors_by_ids", return_value=[doctor]), \
                patch.object(db_operators, "operator_by_doctor_id", return_value=None), \
                patch.object(admin_users, "create_user", return_value={"item": {"usuario": "synthetic"}}) as create:
            result = admin_users.bulk_action("create", {"medico_ids": [doctor["id"]],
                "usuario": "synthetic", "email": " entered@example.test "})
        self.assertEqual(create.call_args.args[0]["email"], "entered@example.test")
        self.assertEqual(result["resultados"][0]["password"], create.call_args.args[0]["password"])

    def test_bulk_invalid_email_does_not_abort_later_invites(self):
        doctors = [{"id": str(n), "nombre": "Synthetic Doctor"} for n in range(3)]
        invited = []

        def invite(item, medico, email=None):
            invited.append(email)
            return {"usuario": item["usuario"], "email_enviado": True}

        with patch.object(admin_users, "_doctors_by_ids", return_value=doctors), \
                patch.object(db_operators, "operator_by_doctor_id", return_value=None), \
                patch.object(admin_users, "provision_doctor_account",
                             side_effect=lambda doctor: {"usuario": "u" + doctor["id"]}), \
                patch.object(admin_users, "invite_account", side_effect=invite):
            result = admin_users.bulk_action("invite", {"medico_ids": ["0", "1", "2", "missing"],
                "emails": {"0": "zero@example.test", "1": "invalid", "2": "two@example.test"}})
        self.assertEqual([r["ok"] for r in result["resultados"]], [True, False, True, False])
        self.assertEqual(invited, ["zero@example.test", "two@example.test"])
        self.assertNotIn("password", json.dumps(result))

    def test_reset_calendar_revokes_the_feed_and_never_returns_a_token(self):
        # Only the token's hash is stored, so a token rotated by the admin could
        # never be shown to the doctor: the action revokes it, and the doctor's
        # next «Agregar a mi calendario» issues a new address and shows it once.
        doctor = {"id": "synthetic-1", "nombre": "Synthetic Doctor"}
        item = {"usuario": "synthetic.login", "medico_id": "synthetic-1"}
        with patch.object(admin_users, "_doctors_by_ids", return_value=[doctor]), \
                patch.object(db_operators, "operator_by_doctor_id", return_value=item), \
                patch.object(db_operators, "operator_revoke_feed_token", return_value=True) as revoke, \
                patch.object(db_operators, "operator_rotate_feed_token") as rotate:
            result = admin_users.bulk_action("reset-calendar", {"medico_ids": [doctor["id"]]})
        revoke.assert_called_once_with("synthetic.login")
        rotate.assert_not_called()
        self.assertEqual(result["resultados"], [{"medico_id": "synthetic-1", "ok": True,
                                                 "usuario": "synthetic.login"}])

    def test_invitation_provider_failure_returns_the_link(self):
        item = {"usuario": "synthetic.login", "email": None}
        with patch.object(db_operators, "operator_update", return_value=dict(item, cuenta_estado="invitado")), \
                patch.object(db_auth, "admin_security_ensure_deadline"), \
                patch.object(db_catalog, "catalog_set_doctor_email"), \
                patch.object(admin_users, "_send_reset", return_value=(False, None)), \
                patch("app.core.staff_access_email.access_link", return_value="https://x/admin/reset-password?token=t"):
            result = admin_users.invite_account(item, {"id": "synthetic"}, email="doctor@example.test")
        self.assertFalse(result["email_enviado"])
        self.assertEqual(result["enlace"], "https://x/admin/reset-password?token=t")

    def test_create_user_invitation_stores_private_hash_input_and_fills_empty_catalog(self):
        doctor = {"id": "synthetic", "nombre": "Synthetic Doctor"}
        with patch.object(admin_users, "_rol_asignable", return_value=True), \
                patch.object(admin_users, "_valid_doctor", return_value=True), \
                patch.object(admin_users, "_doctors_by_ids", return_value=[doctor]), \
                patch.object(db_operators, "operator_get", return_value=None), \
                patch.object(db_operators, "operator_get_public", return_value={"usuario": "synthetic"}), \
                patch.object(admin_users.permissions, "clean_overrides", return_value=([], [])), \
                patch.object(db_operators, "operator_create", return_value={"usuario": "synthetic", "email": "s@example.test"}) as create, \
                patch.object(db_catalog, "catalog_set_doctor_email") as fill, \
                patch.object(db_auth, "admin_security_set_deadline"), \
                patch.object(admin_users, "_with_2fa_status", side_effect=lambda item: item), \
                patch.object(admin_users, "invite_account", return_value={"usuario": "synthetic", "email_enviado": True}) as invite:
            result = admin_users.create_user({"usuario": "synthetic", "medico_id": "synthetic",
                "email": "s@example.test", "access_mode": "invite", "password": "must-be-ignored"})
        self.assertNotEqual(create.call_args.args[1], "must-be-ignored")
        self.assertGreaterEqual(len(create.call_args.args[1]), 32)
        self.assertNotIn(create.call_args.args[1], json.dumps(result))
        fill.assert_called_once_with(doctor, "s@example.test")
        self.assertEqual(invite.call_args.kwargs["email"], "s@example.test")
        self.assertTrue(result["email_enviado"])

    def test_email_auth_resolves_canonical_username_before_password_verification(self):
        identity = {"usuario": "canonical", "rol": "medico", "totp_secret": "synthetic-totp"}
        with patch.object(db_operators, "operator_by_email", return_value={"usuario": "canonical", "activo": True}), \
                patch.object(db_operators, "operator_verify_password", return_value=identity) as verify:
            result = admin_auth._authenticate("doctor@example.test", "synthetic-password")
        verify.assert_called_once_with("canonical", "synthetic-password")
        self.assertEqual(result, identity)
        for account in (None, {"usuario": "canonical", "activo": False}):
            with patch.object(db_operators, "operator_by_email", return_value=account), \
                    patch.object(db_operators, "operator_verify_password") as verify:
                self.assertIsNone(admin_auth._authenticate("doctor@example.test", "synthetic-password"))
            verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
