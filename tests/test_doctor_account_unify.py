"""Médico = usuario (responsable del producto, 2026-10-19, m585886 + m585916).

Pins the invariants of the single doctor/account entity:
  * creating a doctor creates its account in the SAME transaction — an
    injected failure on the account insert leaves neither row;
  * a doctor created with only a name has an INACTIVE «sin invitar» account
    and stays off the public catalog until name, specialty and center exist;
  * an email at create sends the invitation by itself; an invite with every
    field empty hands back a copyable link that really sets a password;
  * the database allows one account per doctor (operators_doctor_id_uniq);
  * the backfill reconciles with unlinked accounts first, lists ambiguous
    matches instead of guessing, and is idempotent (run twice → no dupes);
  * the onboarding cannot finish without specialty, email and a center.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "fixes"))

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import admin_content, admin_users  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402
import backfill_doctor_accounts as backfill  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""
_CLIENT = TestClient(app_module.app)

_FIXTURE = {"medicos": [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología",
     "email": "maria.fernandez@example.com"},
    {"id": "med-2", "nombre": "Pedro Actis Bernasconi", "nombre_pila": "Pedro",
     "apellido": "Actis Bernasconi", "especialidad": "Clínica médica"},
    {"id": "med-3", "nombre": "Andrés Rojas", "especialidad": "Clínica médica"},
    {"id": "med-4", "nombre": "Ana Gómez", "especialidad": "Clínica médica"},
    {"id": "enfermeria", "nombre": "Enfermería", "rol": True, "especialidad": "Enfermería"},
]}

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_doctor_account_unify", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    # Invitation mails carry links; without a public origin they are not sent.
    _env.set_env_for_module(TURNOS_PUBLIC_ORIGIN="https://example.health.invalid")
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON, _TEST_URL = _db.have_db, _db.skip_reason, _db.test_url


def tearDownModule():
    _db.tear_down()


def _admin():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


class _Base(unittest.TestCase):
    def setUp(self):
        _CLIENT.cookies.clear()
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            for table in ("operators", "admin_account_security", "admin_reset_tokens", "catalog",
                          "operator_username_history"):
                cur.execute("TRUNCATE %s RESTART IDENTITY CASCADE" % table)
        admin_auth._ATTEMPTS.clear()
        self._tmp = tempfile.mkdtemp(prefix="doctor-account-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
            json.dump(_FIXTURE, f)
        self.addCleanup(self._clear_caches)  # registered first: runs once the env is back
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        self._clear_caches()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @staticmethod
    def _clear_caches():
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def create(self, body):
        return _CLIENT.post("/api/admin/content/doctors", json=body, headers=_admin())


class UsernameShapeTest(unittest.TestCase):
    def test_first_name_dot_whole_surname_in_ascii(self):
        self.assertEqual(admin_users.doctor_username_base(
            {"nombre_pila": "Pedro", "apellido": "Actis Bernasconi"}), "pedro.actisbernasconi")
        self.assertEqual(admin_users.doctor_username_base({"nombre": "Dora María Villaverde"}), "dora.villaverde")
        self.assertEqual(admin_users.doctor_username_base({"nombre": "Íñigo Núñez"}), "inigo.nunez")
        self.assertEqual(admin_users.doctor_username_base({"nombre": ""}), "medico.nuevo")


class CreateIsOneEntityTest(_Base):
    def test_name_only_doctor_gets_an_inactive_uninvited_account_and_stays_private(self):
        with patch("app.notifications.mailgun.send") as send:
            response = self.create({"nombre_pila": "Carla", "apellido": "Núñez"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        doctor_id = body["item"]["id"]
        self.assertTrue(body["item"]["incompleto"])
        account = db_operators.operator_by_doctor_id(doctor_id)
        self.assertEqual(account["usuario"], "carla.nunez")
        self.assertFalse(account["activo"])
        self.assertEqual(account["cuenta_estado"], "sin_invitar")
        self.assertEqual(body["cuenta"]["cuenta_estado"], "sin_invitar")
        send.assert_not_called()
        self.assertNotIn(doctor_id, {d["id"] for d in catalog.doctors()})
        self.assertIn(doctor_id, {d["id"] for d in catalog.doctors(incluir_incompletos=True)})
        # Nobody can log in to it before the invitation.
        self.assertIsNone(db_operators.operator_verify_password("carla.nunez", "anything-at-all"))

    def test_email_at_create_sends_the_invitation_by_itself(self):
        with patch("app.notifications.mailgun.send", return_value=True) as send:
            response = self.create({"nombre": "Laura Paz", "email": "laura@example.test"})
        self.assertEqual(response.status_code, 200, response.text)
        cuenta = response.json()["cuenta"]
        self.assertTrue(cuenta["email_enviado"])
        self.assertNotIn("enlace", cuenta)
        self.assertEqual(send.call_args.args[0], "laura@example.test")
        self.assertIn("/admin/reset-password?token=", send.call_args.args[2])
        account = db_operators.operator_get_public(cuenta["usuario"])
        self.assertTrue(account["activo"])
        self.assertEqual(account["cuenta_estado"], "invitado")

    def test_empty_invite_end_to_end_link_redeem_one_doctor_one_account(self):
        with patch("app.notifications.mailgun.send") as send:
            response = self.create({"_invitar": True})
        self.assertEqual(response.status_code, 200, response.text)
        send.assert_not_called()
        body = response.json()
        self.assertTrue(body["item"]["id"].startswith("medico-"))
        link = body["cuenta"]["enlace"]
        token = link.split("token=", 1)[1]
        self.assertEqual(db_auth.admin_reset_token_consume(token, "Synthetic-pass-1"), body["cuenta"]["usuario"])
        self.assertIsNotNone(db_operators.operator_verify_password(body["cuenta"]["usuario"], "Synthetic-pass-1"))
        rows = [o for o in db_operators.operators_list() if o.get("medico_id") == body["item"]["id"]]
        self.assertEqual(len(rows), 1)

    def test_the_doctor_form_email_is_the_accounts_email_both_ways(self):
        # One email per doctor (2026-11-03): «Email» in Información básica is
        # where its invitation and password resets go; cleared, both clear.
        with patch("app.notifications.mailgun.send", return_value=True):
            created = admin_content.create_item(
                "medicos", {"nombre": "Laura Paz", "email": "laura@example.test", "_invitar": True})
        doctor_id = created["item"]["id"]
        doc = dict(db_catalog.catalog_get("medicos", doctor_id)["doc"])
        admin_content.edit_item("medicos", doctor_id, dict(doc, email="nueva@example.test"))
        self.assertEqual(db_operators.operator_by_doctor_id(doctor_id)["email"], "nueva@example.test")
        doc = dict(db_catalog.catalog_get("medicos", doctor_id)["doc"])
        admin_content.edit_item("medicos", doctor_id, dict(doc, email=""))
        self.assertIsNone(db_operators.operator_by_doctor_id(doctor_id)["email"])
        self.assertNotIn("email", db_catalog.catalog_get("medicos", doctor_id)["doc"])

    def test_injected_account_failure_leaves_no_doctor(self):
        with patch.object(db_operators, "operator_create", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.create({"nombre": "Fallida Uno"})
        self.assertIsNone(db_catalog.catalog_get("medicos", "fallida-uno"))
        self.assertEqual(db_operators.operators_list(), [])

    def test_complete_doctor_cannot_be_unpublished_by_clearing_its_specialty(self):
        response = _CLIENT.put("/api/admin/content/doctors/med-3", headers=_admin(),
                               json={"nombre": "Andrés Rojas", "especialidad": "", "centro": ""})
        self.assertEqual(response.status_code, 400, response.text)


class PortalInstallationInviteTest(_Base):
    """salud: staff enter only through the municipal portal, where the email IS
    the match. A list of doctors pasted twice (or two rows sharing an email)
    must not leave a half-made doctor behind: the refusal comes BEFORE the
    doctor and its account are created (Lira, 13.10: bulk creation)."""

    def setUp(self):
        super().setUp()
        self.enterContext(_env.patched_env(TURNOS_STAFF_PORTAL_LOGIN="1"))
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)

    def test_an_email_already_active_is_refused_before_the_doctor_is_created(self):
        with patch("app.notifications.mailgun.send", return_value=True):
            first = admin_content.create_item(
                "medicos", {"nombre": "Laura Paz", "email": "laura@example.test", "_invitar": True})
            self.assertEqual(first["cuenta"]["cuenta_estado"], "invitado")
            doctors = len(catalog.admin_list("medicos"))
            accounts = len(db_operators.operators_list())
            with self.assertRaises(admin_content.ContentError) as caught:
                admin_content.create_item(
                    "medicos", {"nombre": "Laura Otra", "email": "Laura@Example.test", "_invitar": True})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.payload["field"], "email")
        self.assertEqual(len(catalog.admin_list("medicos")), doctors)
        self.assertEqual(len(db_operators.operators_list()), accounts)

    def test_the_doctor_form_cannot_take_an_email_another_active_account_logs_in_with(self):
        # One email per doctor (2026-11-03): the record's «Email» is its
        # account's portal match too, refused at the field before saving.
        with patch("app.notifications.mailgun.send", return_value=True):
            admin_content.create_item(
                "medicos", {"nombre": "Laura Paz", "email": "laura@example.test", "_invitar": True})
            other = admin_content.create_item(
                "medicos", {"nombre": "Ana Paz", "email": "ana@example.test", "_invitar": True})
        doctor_id = other["item"]["id"]
        doc = {k: v for k, v in db_catalog.catalog_get("medicos", doctor_id)["doc"].items()}
        with self.assertRaises(admin_content.ContentError) as caught:
            admin_content.edit_item("medicos", doctor_id, dict(doc, email="Laura@example.test"))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.payload["error"], "email_en_uso")
        self.assertEqual(db_catalog.catalog_get("medicos", doctor_id)["doc"]["email"], "ana@example.test")
        self.assertEqual(db_operators.operator_by_doctor_id(doctor_id)["email"], "ana@example.test")

    def test_a_row_without_email_still_creates_an_uninvited_account(self):
        result = admin_content.create_item("medicos", {"nombre": "Sin Correo"})
        self.assertEqual(result["cuenta"]["cuenta_estado"], "sin_invitar")

    def test_an_invitation_without_email_is_refused_before_anything_is_written(self):
        # The email IS the portal match: there is no link to copy instead, so
        # «Invitar médico» without one is a 400 at the field — never a 500
        # after the doctor and its account were already saved. The router
        # answers a ContentError with its status and payload.
        doctors = len(catalog.admin_list("medicos"))
        with self.assertRaises(admin_content.ContentError) as caught:
            admin_content.create_item("medicos", {"nombre": "Sin Correo", "_invitar": True})
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.payload, {"error": "Escribí el email con el que entra al portal.",
                                                    "field": "email"})
        catalog.invalidate()
        self.assertEqual(len(catalog.admin_list("medicos")), doctors)
        self.assertEqual(db_operators.operators_list(), [])


class DisablingADoctorDisablesItsAccountTest(_Base):
    """Deshabilitar a doctor (catalog delete) must not leave a usable login."""

    def test_deleting_a_doctor_deactivates_its_account_and_never_deletes_it(self):
        with patch("app.notifications.mailgun.send", return_value=True):
            response = self.create({"nombre": "Laura Paz", "email": "laura@example.test"})
        self.assertEqual(response.status_code, 200, response.text)
        doctor_id = response.json()["item"]["id"]
        self.assertTrue(db_operators.operator_by_doctor_id(doctor_id)["activo"])
        from app.admin_panel import admin_content
        admin_content.delete_item("medicos", doctor_id)
        account = db_operators.operator_by_doctor_id(doctor_id)
        self.assertIsNotNone(account, "the account is kept for the audit trail")
        self.assertFalse(account["activo"])

    def test_deleting_a_doctor_keeps_the_account_of_an_administrator_active(self):
        # (2026-11-02): a doctor promoted to admin keeps the doctor link;
        # disabling the doctor must not switch the admin off.
        with patch("app.notifications.mailgun.send", return_value=True):
            response = self.create({"nombre": "Clara Admin", "email": "clara@example.test"})
        self.assertEqual(response.status_code, 200, response.text)
        doctor_id = response.json()["item"]["id"]
        usuario = db_operators.operator_by_doctor_id(doctor_id)["usuario"]
        db_operators.operator_update(usuario, rol="admin")
        from app.admin_panel import admin_content
        admin_content.delete_item("medicos", doctor_id)
        account = db_operators.operator_by_doctor_id(doctor_id)
        self.assertEqual(account["rol"], "admin")
        self.assertTrue(account["activo"])

    def test_recreating_a_deleted_doctor_with_the_same_name_gets_a_fresh_id(self):
        # Prod, 2026-10-19: «Prueba QA Borde» deleted (its account stays
        # inactive holding doctor_id «prueba-qa-borde»), then created again →
        # «No se pudo guardar.» Since 2026-10-23 the account is a reference:
        # the doctor is kept disabled, never deleted (problems.md №6).
        response = self.create({"nombre_pila": "Prueba QA", "apellido": "Borde"})
        self.assertEqual(response.status_code, 200, response.text)
        old_id = response.json()["item"]["id"]
        old_user = response.json()["cuenta"]["usuario"]
        self.assertEqual(old_id, "prueba-qa-borde")
        from app.admin_panel import admin_content
        self.assertTrue(admin_content.delete_item("medicos", old_id)["conservado"])
        catalog.invalidate()
        self.assertTrue(db_catalog.catalog_get("medicos", old_id)["borrado"])
        response = self.create({"nombre_pila": "Prueba QA", "apellido": "Borde"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["item"]["id"], "prueba-qa-borde-2")
        self.assertNotEqual(body["cuenta"]["usuario"], old_user)
        # The old account is never relinked: it still points at the old id,
        # inactive, and the new doctor has its own.
        old = db_operators.operator_get_public(old_user)
        self.assertEqual(old["medico_id"], old_id)
        self.assertFalse(old["activo"])
        self.assertEqual(db_operators.operator_by_doctor_id("prueba-qa-borde-2")["usuario"], body["cuenta"]["usuario"])

    def test_disabling_a_doctor_releases_the_pending_requests_it_was_given(self):
        # Prod, 2026-10-23: a patient's pending cardiology request had been
        # handed by the queue's suggestion to the panel's test doctor; once
        # that doctor is disabled the desk must see the request unassigned,
        # never parked on a doctor nobody can book.
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
        pendiente = db_booking.create_booking_request("30111222", "Ana", "Paz", "1951-01-01", "+5492254000000",
                                              "cardiologia-hospital", "hospital-pinamar")
        aprobada = db_booking.create_booking_request("30111333", "Eva", "Paz", "1951-01-01", "+5492254000001",
                                             "cardiologia-hospital", "hospital-pinamar")
        db_booking.assign_pending_booking_request(pendiente, "Andrés Rojas", "med-3")
        db_booking.assign_pending_booking_request(aprobada, "Andrés Rojas", "med-3")
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("UPDATE booking_requests SET status='aprobada' WHERE id=%s", (aprobada,))
        from app.admin_panel import admin_content
        admin_content.delete_item("medicos", "med-3")
        libre = db_booking.get_booking_request(pendiente)
        self.assertIsNone(libre["medico_asignado_id"])
        self.assertIsNone(libre["medico_asignado"])
        dada = db_booking.get_booking_request(aprobada)
        self.assertEqual(dada["medico_asignado_id"], "med-3", "a given turno keeps its doctor (history)")

    def test_deleting_a_base_doctor_without_account_still_works(self):
        from app.admin_panel import admin_content
        result = admin_content.delete_item("medicos", "med-3")
        self.assertTrue(result["ok"])
        self.assertIsNone(db_operators.operator_by_doctor_id("med-3"))


class OneAccountPerDoctorTest(_Base):
    def test_database_refuses_a_second_account_on_the_same_doctor(self):
        db_operators.operator_create("first", "synthetic-pass", "medico", medico_id="med-1")
        import psycopg
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with psycopg.connect(_TEST_URL, autocommit=True) as c, c.cursor() as cur:
                cur.execute("INSERT INTO operators (username, pass_hash, role, doctor_id) "
                            "VALUES ('second', 'x', 'admin', 'med-1')")


class BackfillTest(_Base):
    def run_backfill(self, apply):
        lines = []
        report = backfill.run(apply=apply, emit=lines.append)
        return report, lines

    def test_dry_run_writes_nothing_and_apply_is_idempotent(self):
        db_operators.operator_create("already", "synthetic-pass", "medico", medico_id="med-1")
        report, _ = self.run_backfill(apply=False)
        self.assertEqual(report["creados_ahora"], 3)
        self.assertEqual(len(db_operators.operators_list()), 1)
        report, _ = self.run_backfill(apply=True)
        self.assertEqual((report["ya_vinculados"], report["creados_ahora"]), (1, 3))
        for doctor_id in ("med-2", "med-3", "med-4"):
            account = db_operators.operator_by_doctor_id(doctor_id)
            self.assertFalse(account["activo"])
            self.assertEqual(account["cuenta_estado"], "sin_invitar")
        self.assertEqual(db_operators.operator_by_doctor_id("med-2")["usuario"], "pedro.actisbernasconi")
        again, _ = self.run_backfill(apply=True)
        self.assertEqual((again["ya_vinculados"], again["creados_ahora"]), (4, 0))
        self.assertEqual(len(db_operators.operators_list()), 4)

    def test_reconciles_unlinked_accounts_and_lists_ambiguous_ones(self):
        # Unique login-name match → linked, not duplicated.
        db_operators.operator_create("andres.rojas", "synthetic-pass", "medico")
        # Email match for med-1.
        db_operators.operator_create("mfernandez", "synthetic-pass", "medico", email="Maria.Fernandez@example.com")
        # Two candidates for med-4 → ambiguous, left alone.
        db_operators.operator_create("ana.gomez", "synthetic-pass", "medico")
        db_operators.operator_create("ana.gomez2", "synthetic-pass", "medico")
        report, lines = self.run_backfill(apply=True)
        self.assertEqual(report["vinculados_ahora"], 2)
        self.assertEqual(report["ambiguos"], 1)
        self.assertEqual(report["creados_ahora"], 1)   # med-2
        self.assertEqual(db_operators.operator_by_doctor_id("med-3")["usuario"], "andres.rojas")
        self.assertEqual(db_operators.operator_by_doctor_id("med-1")["usuario"], "mfernandez")
        self.assertIsNone(db_operators.operator_by_doctor_id("med-4"))
        self.assertTrue(any(line.startswith("AMBIGUO") and "med-4" in line for line in lines))

    def test_role_entries_get_no_account_and_duplicate_records_are_listed(self):
        # «andres.rojas» already belongs to another record of the same person.
        db_operators.operator_create("andres.rojas", "synthetic-pass", "medico", medico_id="rojas-andres")
        report, lines = self.run_backfill(apply=True)
        self.assertEqual(report["roles_sin_cuenta"], 1)
        self.assertEqual(report["posibles_duplicados"], 1)
        self.assertIsNone(db_operators.operator_by_doctor_id("enfermeria"))
        self.assertIsNone(db_operators.operator_by_doctor_id("med-3"))
        self.assertTrue(any(line.startswith("DUPLICADO") and "med-3" in line for line in lines))

    def test_backfill_racing_a_create_never_duplicates(self):
        decision = backfill.plan(backfill._doctors(), db_operators.operators_list())
        # The doctor gets its account between the plan and the write.
        admin_users.provision_doctor_account({"id": "med-3", "nombre": "Andrés Rojas"})
        with patch.object(backfill, "plan", return_value=decision):
            report, _ = self.run_backfill(apply=True)
        self.assertEqual(report["carrera"], 1)
        self.assertEqual(len([o for o in db_operators.operators_list() if o.get("medico_id") == "med-3"]), 1)


class DeactivationEndsOpenSessionsTest(_Base):
    def test_a_deactivated_account_is_signed_out_on_its_next_request(self):
        db_operators.operator_create("dr.activo", "synthetic-pass", "medico", medico_id="med-3")
        db_auth.admin_security_set_deadline("dr.activo", None)
        cookie = {"Cookie": "st_admin=" + admin_auth._mint_token("dr.activo", "medico")}
        self.assertEqual(_CLIENT.get("/api/admin/content/doctors", headers=cookie).status_code, 200)
        db_operators.operator_deactivate("dr.activo")
        response = _CLIENT.get("/api/admin/content/doctors", headers=cookie)
        self.assertEqual(response.status_code, 401, response.text)


class OnboardingAsksEverythingTest(_Base):
    def test_onboarding_cannot_finish_without_specialty_email_and_center(self):
        response = self.create({"nombre": "Sofía Ruiz", "_invitar": True})
        usuario = response.json()["cuenta"]["usuario"]
        cookie = {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, "medico")}
        done = _CLIENT.post("/api/admin/onboarding/complete", json={}, headers=cookie)
        self.assertEqual(done.status_code, 400, done.text)
        self.assertEqual(set(done.json()["faltan"]), {"especialidad", "email", "centro"})
        self.assertIsNone(db_operators.operator_get_public(usuario)["onboarding_completed_at"])


if __name__ == "__main__":
    unittest.main()
