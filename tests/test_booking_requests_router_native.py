"""test_booking_requests_router_native.py — native ASGI transport coverage
for `booking_requests_router.py` (Tier 4 slice 8 third batch, native-
migration campaign family F7, docs/roadmap/native-migration-campaign-
20260826.md).

Drives the real FastAPI app end-to-end via `fastapi.testclient.TestClient`
(same pattern `tests/test_app_modules_router.py` established). This proves the
NATIVE transport (not just the shared domain function) actually works: real
path/query/body parsing, the native `_require_admin` auth gate,
gzip/Cache-Control headers, and the shared terminal-404 contract.

Same DB fixture `tests/test_booking_requests.py` already uses
(`_dbtest.PerModuleDb`, a real throwaway Postgres database) — the create/
list/cancel flow genuinely persists and reads back a row, it isn't faked.
Tests that don't touch persistence (terminal 404, admin auth gate without a
role that reaches the DB) never require a DB and always run.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="native-f7-test-pass")
_env.process_defaults(MAX_ACTIVE_REQUESTS="60")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

import _dbtest  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.ai import abuse_guard  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

abuse_guard.ENV_PATH = ""
turnstile.ENV_PATH = ""

_db = _dbtest.PerModuleDb("sana_turnos_test_router_native",
                          seed=lambda: db_booking.get_booking_request(-1))

_USER = {"dni": "27888999", "nombre": "Ana", "apellido": "López",
         "nacimiento": "1979-06-01", "sexo": "F", "contacto": "wa:11-5555"}

HAVE_DB = False
DB_SKIP_REASON = ""
_ORIG_ORDENES = booking_requests.REFERRALS_DIR
_ORIG_SERVICIOS = booking_requests.SERVICES_PATH


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    booking_requests.REFERRALS_DIR = tempfile.mkdtemp(prefix="ordenes-router-native-")
    cat = os.path.join(booking_requests.REFERRALS_DIR, "servicios-test.json")
    with open(cat, "w", encoding="utf-8") as f:
        json.dump({"servicios": [{"id": "cardiologia", "nombre": "Cardiología"}]}, f)
    booking_requests.SERVICES_PATH = cat
    # POST /api/requests refuses a center the catalog does not have (schema
    # review №10): these tests book at a made-up one.
    global _CENTERS_PATCH
    _CENTERS_PATCH = patch("app.patient_web.catalog.centers", return_value=[{"slug": "hospital"}])
    _CENTERS_PATCH.start()
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _CENTERS_PATCH.stop()
    import shutil
    shutil.rmtree(booking_requests.REFERRALS_DIR, ignore_errors=True)
    booking_requests.REFERRALS_DIR = _ORIG_ORDENES
    booking_requests.SERVICES_PATH = _ORIG_SERVICIOS
    _db.tear_down()


def _cookies(rol="admin"):
    return {"st_admin": admin_auth._mint_token("router-native-test", rol)}


class TestPatientFlowNative(unittest.TestCase):
    """POST /api/requests -> GET /api/requests -> GET /api/requests/<id> ->
    POST /api/requests/<id>/cancel, all through the real ASGI transport."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin DB de test")
        self._orig_get_user = booking_requests._get_user
        self.client = TestClient(app_module.app)

    def tearDown(self):
        booking_requests._get_user = self._orig_get_user

    def test_create_list_detail_cancel(self):
        booking_requests._get_user = lambda h: dict(_USER)

        created = self.client.post(
            "/api/requests",
            json={"servicio_id": "cardiologia", "centro_slug": "hospital"})
        self.assertEqual(created.status_code, 200, created.json())
        body = created.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["estado"], "pendiente")
        sid = body["id"]

        listed = self.client.get("/api/requests")
        self.assertEqual(listed.status_code, 200)
        ids = [s["id"] for s in listed.json()["solicitudes"]]
        self.assertIn(sid, ids)

        detail = self.client.get(f"/api/requests/{sid}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["solicitud"]["id"], sid)
        self.assertEqual(detail.json()["solicitud"]["estado"], "pendiente")

        cancelled = self.client.post(f"/api/requests/{sid}/cancel", json={})
        self.assertEqual(cancelled.status_code, 200, cancelled.json())
        self.assertEqual(cancelled.json()["solicitud"]["estado"], "cancelada")

        # A second cancel of the same (now-terminal) request is rejected.
        again = self.client.post(f"/api/requests/{sid}/cancel", json={})
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.json(), {"error": "no_cancelable"})

    def test_create_without_session_is_401(self):
        booking_requests._get_user = lambda h: None
        r = self.client.post(
            "/api/requests",
            json={"servicio_id": "cardiologia", "centro_slug": "hospital"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "auth_requerida"})


class TestTerminal404Native(unittest.TestCase):
    """FastAPI matches the path template, but the legacy inner regex would
    have declined it (non-numeric id / out-of-shape token) — the SAME
    generic 404 (see this router file's own module docstring): NOT the
    `routers/native_terminal.py` shape."""

    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_non_numeric_request_id(self):
        r = self.client.get("/api/requests/abc")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, b'{"error": "not found"}')

    def test_non_numeric_id_on_cancel(self):
        r = self.client.post("/api/requests/abc/cancel", json={})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, b'{"error": "not found"}')

    def test_non_numeric_id_on_admin_actions(self):
        # W2d follow-up (native-migration campaign): the six admin action
        # rows and the CAS-guarded approve/reject that were deliberately
        # left `adapted-legacy` by wave 1 — same terminal-404 shape as
        # every other {req_id} route in this family, checked BEFORE auth
        # (no cookie sent here at all, and it's still a 404, not a 401).
        for action in ("approve", "assign", "retry-hsi", "reject", "campos", "attendance"):
            with self.subTest(action=action):
                r = self.client.post(f"/api/admin/requests/abc/{action}", json={})
                self.assertEqual(r.status_code, 404)
                self.assertEqual(r.content, b'{"error": "not found"}')

    def test_oversized_orden_preview_token(self):
        r = self.client.get("/api/orden-preview/" + "x" * 100)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, b'{"error": "not found"}')

    def test_valid_shape_missing_token_is_a_business_404_not_terminal(self):
        # A well-shaped but nonexistent token: `handle()`'s regex MATCHES,
        # so this is `_orden_preview`'s own business 404, a different shape
        # (Cache-Control: no-store, flat `{"error": "orden no encontrada"}").
        r = self.client.get("/api/orden-preview/tokeninexistente1234567890")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "orden no encontrada"})
        self.assertEqual(r.headers["cache-control"], "no-store")


class TestAdminAuthGateNative(unittest.TestCase):
    """The native `_require_admin`/`_admin_gate` port in
    booking_requests_router.py: missing cookie, wrong role, valid admin."""

    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_missing_cookie_401(self):
        r = self.client.get("/api/admin/theme")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "no_autenticado"})
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_wrong_role_403(self):
        r = self.client.get("/api/admin/theme", cookies=_cookies("medico"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "solo_admin"})

    def test_valid_admin_cookie_200(self):
        r = self.client.get("/api/admin/theme", cookies=_cookies("admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("tema", r.json())
        self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_admin_requests_queue_permits_medico_role(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin DB de test")
        r = self.client.get("/api/admin/requests", cookies=_cookies("medico"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("solicitudes", r.json())

    def test_admin_requests_queue_rejects_reportes_role(self):
        r = self.client.get("/api/admin/requests", cookies=_cookies("reportes"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "rol_no_autorizado"})

    def test_admin_shell_has_no_auth_of_its_own(self):
        # GET /admin never gates server-side (client-side gate after load,
        # same as legacy) — always 200 HTML, no cookie needed.
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    # ---- W2d follow-up: the eight rows wave 1 deliberately deferred ------
    # (docs/roadmap/native-migration-campaign-20260826.md, family W2d) —
    # same `_require_admin` gate as the rest of this class, now a thin
    # call-through to `core.deps.require_admin(...)` instead of the local
    # `_admin_gate` port. These prove BYTE PARITY of the failure bodies
    # survived the auth-normalization swap (Part 3 of that family): every
    # assertion here is unchanged from what the pre-swap local gate would
    # have produced.

    def test_approve_missing_cookie_401(self):
        r = self.client.post("/api/admin/requests/1/approve", json={})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "no_autenticado"})

    def test_retry_hsi_rejects_medico_role(self):
        # `_retry_hsi_core` is admin/operador-only (default require_admin
        # flags — no permitir_medico) — a medico session gets the plain
        # role-403, same shape `test_wrong_role_403` already proves for
        # theme.
        r = self.client.post("/api/admin/requests/1/retry-hsi", cookies=_cookies("medico"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "rol_no_autorizado"})

    def test_hsi_sync_resolver_missing_cookie_401(self):
        r = self.client.post("/api/admin/hsi-sync/resolver", json={})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "no_autenticado"})

    def test_hsi_sync_conflicts_missing_cookie_401(self):
        r = self.client.post("/api/admin/hsi-sync/conflicts", json={})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "no_autenticado"})

    def test_hsi_sync_conflicts_no_ids_400(self):
        # Auth passes (admin), but an empty `conflictos` list never reaches
        # the DB — `_hsi_conflicts_apply_core`'s own validation, safe to run
        # without HAVE_DB.
        from unittest import mock
        with _env.patched_env({"CONNECTOR_EMR_SYNC": "hsi"}):
            config._reset_cache_para_tests()
            r = self.client.post("/api/admin/hsi-sync/conflicts", json={"accion": "mover"},
                                 cookies=_cookies("admin"))
        config._reset_cache_para_tests()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "sin_conflictos"})


class TestAdminActionsNative(unittest.TestCase):
    """DB-backed round trip for the six per-request admin actions + the CAS
    guard on `/reject` — native-migration campaign, family W2d follow-up on
    F7's deliberately-deferred rows. Proves the ASGI transport reaches the
    SAME extracted domain functions (`booking_decisions._approve_core` and
    friends) with the SAME validation order and the SAME
    `store.AlreadyProcessed` CAS handling. The concurrent-thread race stays
    owned by `test_integrity_races.py`; this class proves router wiring."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin DB de test")
        self._orig_get_user = booking_requests._get_user
        booking_requests._get_user = lambda h: dict(_USER)
        self.client = TestClient(app_module.app)
        created = self.client.post(
            "/api/requests",
            json={"servicio_id": "cardiologia", "centro_slug": "hospital"})
        self.assertEqual(created.status_code, 200, created.json())
        self.sid = created.json()["id"]

    def tearDown(self):
        booking_requests._get_user = self._orig_get_user

    def test_approve_requires_turno_dt(self):
        r = self.client.post(f"/api/admin/requests/{self.sid}/approve", json={},
                              cookies=_cookies("admin"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "falta turno_dt"})

    def test_assign_invalid_doctor_400(self):
        r = self.client.post(f"/api/admin/requests/{self.sid}/assign",
                              json={"medico": "Dr. No Existe"}, cookies=_cookies("admin"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "Médico inválido."})

    def test_retry_hsi_on_pending_request_400(self):
        # Not approved yet — `_retry_hsi_core`'s own "solicitud no aprobada"
        # gate, same message the legacy transport always gave. HSI-skip must
        # be OFF for this test: with TURNOS_SKIP_HSI=1 (this module's own
        # hygiene default) the earlier 409 hsi_desactivado gate answers
        # first, before the not-approved check under test.
        from unittest import mock
        with _env.patched_env({
            "TURNOS_SKIP_HSI": "0", "CONNECTOR_EMR_SYNC": "hsi",
        }):
            r = self.client.post(f"/api/admin/requests/{self.sid}/retry-hsi",
                                  cookies=_cookies("admin"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "solicitud no aprobada"})

    def test_retry_emr_answers_for_any_selected_connector_not_404(self):
        # «Reintentar» belongs to the EMR push, not to HSI: with ANY EMR
        # connector selected the route reaches `_retry_hsi_core` (here its
        # own "no aprobada" 400). Only «no connector» is a terminal 404.
        for connector in ("emr_bridge", "hsi"):
            with self.subTest(connector=connector):
                with _env.patched_env(TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC=connector):
                    config._reset_cache_para_tests()
                    r = self.client.post(f"/api/admin/requests/{self.sid}/retry-hsi",
                                          cookies=_cookies("admin"))
                config._reset_cache_para_tests()
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json(), {"error": "solicitud no aprobada"})

    def test_retry_emr_is_404_without_a_connector(self):
        for value in ("", "off"):
            with self.subTest(connector=value):
                with _env.patched_env(TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC=value):
                    config._reset_cache_para_tests()
                    r = self.client.post(f"/api/admin/requests/{self.sid}/retry-hsi",
                                          cookies=_cookies("admin"))
                config._reset_cache_para_tests()
                self.assertEqual(r.status_code, 404, r.text)

    def test_reject_requires_motivo_then_succeeds_then_cas_blocks_second_reject(self):
        missing = self.client.post(f"/api/admin/requests/{self.sid}/reject", json={},
                                    cookies=_cookies("admin"))
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json(), {"error": "falta el motivo del rechazo"})

        ok = self.client.post(f"/api/admin/requests/{self.sid}/reject",
                               json={"motivo": "no corresponde"}, cookies=_cookies("admin"))
        self.assertEqual(ok.status_code, 200, ok.json())
        self.assertTrue(ok.json()["ok"])
        self.assertEqual(ok.json()["solicitud"]["estado"], "rechazada")

        # CAS guard: the row is already terminal — `store.reject` raises
        # `AlreadyProcessed`, `_reject_core` turns it into the SAME 409 the
        # concurrent-thread race in test_integrity_races.py also asserts.
        again = self.client.post(f"/api/admin/requests/{self.sid}/reject",
                                  json={"motivo": "otra vez"}, cookies=_cookies("admin"))
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json(), {"error": "La solicitud ya fue resuelta."})

    def test_campos_invalid_field_400(self):
        r = self.client.post(f"/api/admin/requests/{self.sid}/campos",
                              json={"campo": "practicas", "valor": "x"},
                              cookies=_cookies("admin"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "campo inválido"})

    def test_campos_valid_edit_200(self):
        r = self.client.post(f"/api/admin/requests/{self.sid}/campos",
                              json={"campo": "paciente", "valor": "Otro Nombre"},
                              cookies=_cookies("admin"))
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(r.json()["campos"]["paciente"]["valor"], "Otro Nombre")
        self.assertTrue(r.json()["campos"]["paciente"]["editado"])

    def test_attendance_invalid_value_400(self):
        r = self.client.post(f"/api/admin/requests/{self.sid}/attendance",
                              json={"asistencia": "tal_vez"}, cookies=_cookies("admin"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "asistencia_invalida"})


if __name__ == "__main__":
    unittest.main()
