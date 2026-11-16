"""test_w2b_intake_native_routers.py — native-route migration (family W2b,
2026-08-26): transport-contract tests for the native FastAPI endpoints in
`modules/intake/{interview_patient,consult_admin,intake_admin,printable}_
router.py`.

Born as byte-parity proofs against the legacy `handle(handler, method)`
path; those transports were deleted for intake on 2026-08-30, so the file
now pins the native contracts directly. Each router is mounted alone in a
minimal `FastAPI()` app (same pattern as `test_ai_native_routers.py`) and
driven with `TestClient`.

Two behavior changes the migration deliberately introduced (NOT bugs) are
pinned explicitly:
  - a path segment shape the old inner regex would have declined (e.g. a
    non-numeric `aid`/`cid`, a token with disallowed characters, a too-long
    door code) answers with the campaign's shared terminal-404 contract
    (`{"error": "not found"}`, `Cache-Control: no-store`) — same convention
    `modules/turnos/appointment_confirm_router.py` established. This
    decline is checked BEFORE the staff-auth gate, so an unauthenticated
    request with a malformed id still 404s, never 401s.
  - a staff-auth failure on a JSON endpoint renders the flat legacy
    `{"error": ...}` body (`core.deps.AdminGateError`, family F1) rather
    than FastAPI's default `{"detail": ...}` envelope, when mounted on the
    real composed `app` (which registers the exception handler) — see
    `test_full_app_auth_failure_is_flat_not_wrapped_in_detail`.

Some cases need a real Postgres (interview/consultation persistence, alert
rows, door tokens); those skip cleanly without `TEST_DATABASE_URL`, same
convention as every other `_dbtest`-backed file in this suite. Cases that
never touch the DB (missing session, missing/malformed path segment, unknown
token) always run. No real patient data anywhere — every DNI/name/token
below is fictional, minted fresh per test.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_w2b_intake_native_routers -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="w2b-intake-test-pass")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")
# Deliberately NOT touching ENABLED_MODULES — see test_ai_native_routers.py's
# identical comment: unset means "every discovered module", which already
# covers `intake`, and pinning it here previously leaked into later test
# modules in the same pytest process.

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.patient_web import catalog  # noqa: E402

from app.modules.intake import consult_admin, consult_admin_router  # noqa: E402
from app.modules.intake import intake_admin, intake_admin_router  # noqa: E402
from app.modules.intake import interview_patient, interview_patient_router  # noqa: E402
from app.modules.intake import printable, printable_router  # noqa: E402

config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_w2bnative", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _admin_cookie():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


def _medico_cookie(usuario):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, "medico")}


def _mount(router):
    app = FastAPI()
    app.include_router(router)
    from app.core import deps as _early_deps
    _early_deps.install_early_response(app)  # the app's answer of a dependency
    return TestClient(app)


class TestPrintableRouter(unittest.TestCase):
    """No DB needed for the unknown-token 404 — `printable._consulta_para`
    degrades DbUnavailable to None already."""

    def setUp(self):
        self.client = _mount(printable_router.router)

    def test_unknown_token_404(self):
        for path in ("/r/no-existe-fixture", "/o/no-existe-fixture"):
            with self.subTest(path=path):
                native = self.client.get(path)
                self.assertEqual(native.status_code, 404)

    def test_receta_and_orden_round_trip(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        doctor_id = "w2b-fixture-medico-1"
        db_catalog.catalog_upsert("medicos", doctor_id,
                           {"id": doctor_id, "nombre": "Fixture Medico", "especialidad": "Clinica"},
                           borrado=False)
        catalog.invalidate()  # bust patient_web.catalog's 3s "medicos" cache
        interview = db_clinical.interview_create(doctor_id=doctor_id, entry_mode="qr",
                                        dni="40000001", matched=False)
        db_clinical.consultation_create(
            interview_id=interview["id"], doctor_id=doctor_id, diagnoses=[], exams=[],
            meds=[], notes="", receta_json={"titulo": "Receta"}, orden_json={"titulo": "Orden"},
            receta_token="w2b-receta-tk", orden_token="w2b-orden-tk",
            created_by="w2b-fixture-medico-1")
        for path in ("/r/w2b-receta-tk", "/o/w2b-orden-tk"):
            with self.subTest(path=path):
                native = self.client.get(path)
                self.assertEqual(native.status_code, 200)


class TestIntakeAdminRouter(unittest.TestCase):
    def setUp(self):
        self.client = _mount(intake_admin_router.router)

    def test_auth_failure_is_flat_via_full_app(self):
        """Isolated-router mounts (this file's `_mount`, same convention as
        `test_ai_native_routers.py`) never register `core.deps.
        AdminGateError`'s exception handler, so `core.deps.require_admin`'s
        HTTPException comes back as FastAPI's default `{"detail": ...}` here
        — that is a test-harness artifact of the isolated mount, not
        production behavior. `app.py` DOES register that handler
        (`app.add_exception_handler(core_deps.AdminGateError, core_deps.
        render_admin_gate_error)`), so the flat body is asserted against the
        REAL composed app instead."""
        from app import main as app_module
        full_client = TestClient(app_module.app)
        for path in ("/api/admin/intake/settings", "/api/admin/intake/my-settings",
                     "/api/admin/intake/alerts", "/api/admin/intake/alerts/count"):
            with self.subTest(path=path):
                response = full_client.get(path)
                self.assertEqual(response.status_code, 401)
                body = response.json()
                self.assertNotIn("detail", body)
                self.assertEqual(body, {"error": "no_autenticado"})

    def test_alert_ack_malformed_aid_is_terminal_404_before_auth(self):
        """Shape decline wins over auth — an UNAUTHENTICATED request with a
        non-numeric `aid` still 404s (never 401s), matching `handle()`'s own
        dispatch order (the legacy inner regex never reaches
        `admin_auth.require_admin` for a shape it doesn't recognize)."""
        native = self.client.post("/api/admin/intake/alerts/not-a-number/ack")
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "not found"})
        self.assertEqual(native.headers["cache-control"], "no-store")

    def test_settings_get(self):
        headers = _admin_cookie()
        native = self.client.get("/api/admin/intake/settings", headers=headers)
        if not HAVE_DB:
            # Degrades to 503 without a DB — still a real contract, not
            # skipped.
            self.assertEqual(native.status_code, 503)
            return
        self.assertEqual(native.status_code, 200)
        self.assertIn("medicos", native.json())

    def test_my_settings_and_door_round_trip(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        doctor_id = "w2b-fixture-medico-2"
        db_catalog.catalog_upsert("medicos", doctor_id,
                           {"id": doctor_id, "nombre": "Fixture Medico Dos", "especialidad": "Clinica"},
                           borrado=False)
        catalog.invalidate()  # bust patient_web.catalog's 3s "medicos" cache —
        # my_settings_payload()/catalog.doctor_doc() reads the doctor
        # through catalog.doctors(), which is a process-global 3s-TTL cache
        # keyed only by "medicos" (patient_web/catalog.py's _RAW_CACHE), not
        # scoped per-test-db. Production write paths that touch the medicos
        # catalog (intake_admin._write_door_token/_write_override) always
        # call catalog.invalidate() right after db_catalog.catalog_upsert() for
        # exactly this reason; this fixture wrote the row directly through
        # db_catalog.catalog_upsert() without going through either of those, so it
        # has to invalidate the same cache itself. Without this, whether the
        # assertion below sees the fixture doctor depends on whether an
        # EARLIER test in this class already warmed catalog.doctors() within
        # the last 3 seconds (order- and wall-clock-dependent — reproduced
        # as a deterministic failure under `python -m unittest discover`,
        # never under `pytest`, purely from timing differences between the
        # two runners relative to the 3s window).
        db_operators.operator_create("w2b-fixture-medico-usr", "clave1234", "medico", medico_id=doctor_id)
        headers = _medico_cookie("w2b-fixture-medico-usr")

        native = self.client.get("/api/admin/intake/my-settings", headers=headers)
        self.assertEqual(native.status_code, 200)

        native_door = self.client.get(f"/api/admin/intake/door/{doctor_id}", headers=headers)
        self.assertEqual(native_door.status_code, 200)
        self.assertIn(b"Fixture Medico Dos", native_door.content)

        native_regen = self.client.post(
            f"/api/admin/intake/door/{doctor_id}/regenerate", headers=headers)
        self.assertEqual(native_regen.status_code, 200)
        self.assertTrue(native_regen.json().get("ok"))

    def test_alerts_count_and_ack(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        headers = _admin_cookie()
        native = self.client.get("/api/admin/intake/alerts/count", headers=headers)
        self.assertEqual(native.status_code, 200)

        native_ack = self.client.post(
            "/api/admin/intake/alerts/999999/ack", headers=headers)
        self.assertEqual(native_ack.status_code, 404)


class TestConsultAdminRouter(unittest.TestCase):
    def setUp(self):
        self.client = _mount(consult_admin_router.router)

    def test_consult_malformed_cid_is_terminal_404_before_auth(self):
        native = self.client.get("/api/admin/intake/consult/not-a-number")
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "not found"})
        native_confirm = self.client.post("/api/admin/intake/consult/not-a-number/confirm")
        self.assertEqual(native_confirm.status_code, 404)
        self.assertEqual(native_confirm.json(), {"error": "not found"})

    def test_consult_unknown_interview(self):
        headers = _admin_cookie()
        native = self.client.get("/api/admin/intake/consult/999999", headers=headers)
        if not HAVE_DB:
            self.assertEqual(native.status_code, 503)
            return
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "entrevista no encontrada"})

    def test_consult_and_confirm_round_trip(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        doctor_id = "w2b-fixture-medico-3"
        db_catalog.catalog_upsert("medicos", doctor_id,
                           {"id": doctor_id, "nombre": "Fixture Medico Tres", "especialidad": "Clinica"},
                           borrado=False)
        catalog.invalidate()  # bust patient_web.catalog's 3s "medicos" cache
        # — consult_admin.py's confirm path resolves the doctor through
        # catalog.doctors() too (see test_my_settings_and_door_round_trip's
        # comment for the full mechanism).
        interview = db_clinical.interview_create(doctor_id=doctor_id, entry_mode="qr",
                                        dni="40000003", matched=False)
        iid = interview["id"]
        headers = _admin_cookie()

        native = self.client.get(f"/api/admin/intake/consult/{iid}", headers=headers)
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json()["id"], iid)

        confirm_body = {"diagnosticos": [], "estudios": [], "medicacion": [], "notas": "fixture"}
        old_client = self.client.post(
            f"/api/admin/intake/consult/{iid}/confirm", json=confirm_body, headers=headers)
        self.assertEqual(old_client.status_code, 400)
        self.assertIsNone(db_clinical.consultation_latest_for_interview(iid))
        confirm_body.update({
            "patient_reviewed": {"nombre": native.json()["nombre"], "dni": "40000003"},
            "choices_reviewed": {"diagnosticos": [], "estudios": [], "medicacion": [], "notas": "fixture"},
        })
        native_confirm = self.client.post(
            f"/api/admin/intake/consult/{iid}/confirm", json=confirm_body, headers=headers)
        self.assertEqual(native_confirm.status_code, 200)
        self.assertTrue(native_confirm.json().get("ok"))
        self.assertIn("receta_url", native_confirm.json())


class TestInterviewPatientRouter(unittest.TestCase):
    def setUp(self):
        self.client = _mount(interview_patient_router.router)

    def test_entrevista_sin_sesion_401(self):
        native = self.client.get("/entrevista/1")
        self.assertEqual(native.status_code, 401)

    def test_entrevista_malformed_sid_is_terminal_404(self):
        native = self.client.get("/entrevista/not-a-number")
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "not found"})

    def test_transcript_malformed_token_is_terminal_404(self):
        native = self.client.get("/api/intake/t/bad token")
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "not found"})

    def test_e_door_code_too_long_is_terminal_404(self):
        native = self.client.get("/e/" + "a" * 65)
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "not found"})

    def test_qr_page_unknown_door_code(self):
        native = self.client.get("/e/no-existe-fixture-door")
        self.assertEqual(native.status_code, 404)

    def test_transcript_unknown_token(self):
        native = self.client.get("/api/intake/t/no-existe-fixture-token")
        if not HAVE_DB:
            self.assertEqual(native.status_code, 503)
            return
        self.assertEqual(native.status_code, 404)

    def test_full_round_trip(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        interview = db_clinical.interview_create(doctor_id=None, entry_mode="post_booking",
                                        dni="40000004", matched=True)
        token = interview["patient_token"]

        native = self.client.get(f"/api/intake/t/{token}")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json()["status"], "active")

        msg_path = f"/api/intake/t/{token}/message"
        body = {"texto": "hola, fixture de prueba"}
        native_msg = self.client.post(msg_path, json=body)
        self.assertEqual(native_msg.status_code, 200)
        self.assertIn("texto", native_msg.json())

        reopen_path = f"/api/intake/t/{token}/reopen"
        native_reopen = self.client.post(reopen_path)
        self.assertEqual(native_reopen.status_code, 200)
        self.assertEqual(native_reopen.json()["status"], "active")


if __name__ == "__main__":
    unittest.main()
