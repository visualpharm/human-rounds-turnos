"""test_doctor_appointments_router_native.py — transport parity for the
native FastAPI router (docs/roadmap/native-migration-campaign-20260826.md,
family F7): `doctor_appointments_router.py`'s 7 GET endpoints against the
same domain behavior `test_doctor_appointments.py`/`test_cola_dia.py`
already cover through native routes.

Two classes, split by whether they need a real Postgres:

- `DoctorAppointmentsRouterAuthTestCase` — no DB required. The central
  admin_auth-shaped gate (401 no session, 403 wrong role) and the terminal
  404 for a malformed `req_id` never touch Postgres, and
  `/compatible-services` only reads the JSON catalog — so this class runs
  fully offline.
- `DoctorAppointmentsRouterDbTestCase` — needs Postgres (`tests/_dbtest.py`,
  same per-module throwaway database pattern as `test_doctor_appointments.
  py`). Covers one genuine 200 for each of the OTHER role-gate shapes
  (permitir_reportes, permitir_medico, permitir_medico+permitir_admisionista)
  plus the domain-level errors (solo_medico, cuenta_sin_medico,
  medico_desconocido, orden no encontrada) that only make sense with real
  operator/booking rows. Skips cleanly (HAVE_DB check) where no managed
  Postgres is reachable — same contract every other DB-backed suite here
  already has.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_doctor_appointments_router_native -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="router-native-test-pass")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

turnstile.ENV_PATH = ""

import _dbtest  # noqa: E402


def _cookies(usuario, rol):
    return {"st_admin": admin_auth._mint_token(usuario, rol)}


def _write_webdata_fixtures(webdir):
    import json
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
        json.dump({"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}, f)
    with open(os.path.join(webdir, "servicios.json"), "w", encoding="utf-8") as f:
        json.dump({"servicios": [
            {"id": "serv-cardio", "nombre": "Cardiología", "centro": "centro-a",
             "modo": "solicitud", "cluster": "cardiologia"},
        ]}, f)
    with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
        json.dump({"medicos": [
            {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología",
             "centro": "centro-a", "cluster": "cardiologia"},
            {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Pediatría", "centro": "centro-a"},
        ]}, f)


class _WebdataFixtureMixin:
    """Shared webdata-catalog isolation, same pattern as
    test_doctor_appointments.py — a temp TURNOS_WEBDATA_DIR so the catalog
    never touches the real site data."""

    def _set_up_webdata(self):
        self._tmp = tempfile.mkdtemp(prefix="doctor-appts-router-native-")
        webdir = os.path.join(self._tmp, "webdata")
        _write_webdata_fixtures(webdir)
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(catalog._JSON_CACHE.clear)
        self.addCleanup(catalog.invalidate)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def _tear_down_webdata(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


# ---- no-DB class: auth gate + terminal 404 + the one DB-free success path --

class DoctorAppointmentsRouterAuthTestCase(_WebdataFixtureMixin, unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        self._set_up_webdata()
        self.addCleanup(self._tear_down_webdata)
        self.client = TestClient(app_module.app)

    def test_no_cookie_is_401_no_autenticado(self):
        r = self.client.get("/api/admin/my-appointments")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "no_autenticado"})

    def test_queue_event_stream_uses_the_same_cookie_and_role_gate(self):
        # Both rejections happen before StreamingResponse starts, so this
        # proves the long-lived endpoint never becomes an anonymous feed.
        anonymous = self.client.get("/api/admin/day-queue/events")
        self.assertEqual(anonymous.status_code, 401)
        reportes = self.client.get(
            "/api/admin/day-queue/events", cookies=_cookies("reportes", "reportes"))
        self.assertEqual(reportes.status_code, 403)

    def test_queue_event_stream_whose_listen_connection_times_out_is_the_standard_503(self):
        # The separate non-pooled LISTEN connection failing to open (psycopg ConnectionTimeout)
        # used to escape from inside the StreamingResponse as an unhandled 500. It is opened
        # before the response starts, so the client gets the repo's DB-unavailable answer and
        # EventSource retries.
        import psycopg
        from unittest import mock

        async def refuse(*_a, **_k):
            raise psycopg.errors.ConnectionTimeout("connection timeout expired")

        client = TestClient(app_module.app, raise_server_exceptions=False)
        with mock.patch("app.core.db._db_url", return_value="postgresql://x@127.0.0.1:1/x"), \
                mock.patch.object(psycopg.AsyncConnection, "connect", side_effect=refuse), \
                mock.patch.object(admin_auth, "centros_permitidos", return_value=None), \
                self.assertLogs("uvicorn.error", level="WARNING") as logged:
            r = client.get("/api/admin/day-queue/events", cookies=_cookies("admin", "admin"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"error": "base de datos no disponible"})
        self.assertEqual(r.headers["retry-after"], "3")
        self.assertIn("ConnectionTimeout", "\n".join(logged.output))

    def test_queue_event_stream_whose_database_drops_before_the_first_read_is_the_standard_503(self):
        from unittest import mock
        from app.core import db, db_slots
        from app.modules.turnos import queue_events

        class _Listener:
            closed = 0

            async def close(self):
                self.closed += 1

        listener = _Listener()

        async def opened():
            return listener

        client = TestClient(app_module.app, raise_server_exceptions=False)
        with mock.patch.object(queue_events._Listener, "open", side_effect=opened), \
                mock.patch.object(db_slots, "day_queue_event_high_water",
                                  side_effect=db.DbUnavailable("pool agotado")), \
                mock.patch.object(admin_auth, "centros_permitidos", return_value=None), \
                self.assertLogs("uvicorn.error", level="WARNING"):
            r = client.get("/api/admin/day-queue/events", cookies=_cookies("admin", "admin"))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.headers["retry-after"], "3")
        self.assertEqual(listener.closed, 1)

    def _feed_response(self, listener):
        """The route's response for a fake listener, built the way the route builds it."""
        import asyncio
        from types import SimpleNamespace
        from unittest import mock
        from app.modules.turnos import doctor_appointments_router as router
        from app.modules.turnos import queue_events

        async def disconnected():
            return False

        async def feed(_last_event_id):
            return listener, 7

        async def identity(*_a, **_k):
            return {"usuario": "admin", "rol": "admin"}

        request = SimpleNamespace(is_disconnected=disconnected, headers={})

        async def build():
            return await router.day_queue_events(request)

        with mock.patch.object(router, "_require_admin", side_effect=identity), \
                mock.patch.object(admin_auth, "centros_permitidos", return_value=None), \
                mock.patch.object(queue_events, "open_feed", side_effect=feed):
            return asyncio.run(build())

    def test_a_client_gone_before_the_first_byte_still_closes_the_listener(self):
        # `send` fails on the response start (the client is gone): the body generator never
        # starts, so neither its `finally` nor a background task runs. The response itself closes.
        import asyncio
        from starlette.requests import ClientDisconnect

        class _Listener:
            closed = 0

            async def wait(self, timeout):
                await asyncio.sleep(3600)

            async def close(self):
                await asyncio.sleep(0)
                self.closed += 1

        listener = _Listener()
        response = self._feed_response(listener)

        async def gone(_message):
            raise OSError("client disconnected")

        async def receive():
            return {"type": "http.disconnect"}

        async def call():
            with self.assertRaises(ClientDisconnect):
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, gone)

        asyncio.run(call())
        self.assertEqual(listener.closed, 1)

    def test_a_cancelled_response_closes_the_listener_to_the_end(self):
        # The task is cancelled (the server drops the request) while the stream waits; the close
        # awaits inside the cancellation and still completes.
        import asyncio

        class _Listener:
            closed = 0

            async def wait(self, timeout):
                await asyncio.sleep(3600)

            async def close(self):
                await asyncio.sleep(0.01)
                self.closed += 1

        listener = _Listener()
        response = self._feed_response(listener)
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            await asyncio.sleep(3600)

        async def call():
            task = asyncio.ensure_future(
                response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
            while not any(m.get("type") == "http.response.body" for m in sent):
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(call())
        self.assertGreaterEqual(listener.closed, 1)     # the stream's own close and the response's

    def test_reportes_only_route_rejects_medico_role(self):
        # /api/admin/doctors/stats requires permitir_reportes=True; a plain
        # 'medico' session never gets past the CENTRAL admin_auth-shaped
        # gate (403 rol_no_autorizado) — the domain function never runs.
        r = self.client.get("/api/admin/doctors/stats", cookies=_cookies("dr-x", "medico"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "rol_no_autorizado"})

    def test_plain_gate_rejects_medico_role(self):
        # /api/admin/doctors/{doctor}/appointments has no permitir_medico
        # flag: the central gate itself rejects a 'medico' session before
        # ever resolving the doctor id.
        r = self.client.get(
            "/api/admin/doctors/med-1/appointments", cookies=_cookies("dr-x", "medico"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "rol_no_autorizado"})

    def test_admisionista_rejected_where_not_opted_in(self):
        # /api/admin/doctors/stats doesn't pass permitir_admisionista.
        r = self.client.get(
            "/api/admin/doctors/stats", cookies=_cookies("mesa-1", "admisionista"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "rol_no_autorizado"})

    def test_malformed_req_id_is_the_route_via_handle_terminal_404(self):
        # FastAPI's plain {req_id} segment matches "abc"; non-numeric
        # request ids are rejected before auth with the shared terminal
        # body/status (see module docstring).
        r = self.client.get("/api/admin/my-appointments/abc/order")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, b'{"error": "not found"}')
        # A digit-only id that just doesn't exist yet is NOT this case —
        # it clears routing and reaches the auth gate like any other id.
        r2 = self.client.get("/api/admin/my-appointments/999999/order")
        self.assertEqual(r2.status_code, 401)

    def test_compatible_services_200_reads_only_the_catalog(self):
        # No Postgres touched anywhere in this path (patient_web.catalog is
        # pure JSON-file lookups) — a real success response, not a stub.
        r = self.client.get(
            "/api/admin/doctors/med-1/compatible-services",
            cookies=_cookies(admin_auth._admin_user(), "admin"),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")
        body = r.json()
        ids = [item["id"] for item in body["items"]]
        self.assertIn("serv-cardio", ids)


# ---- DB-backed class: one 200 per remaining role-gate shape + domain errors

_db = _dbtest.PerModuleDb("sana_turnos_test_doctor_appts_router",
                          seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


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
            cur.execute("TRUNCATE catalog")


def _futuro(horas=48):
    from datetime import timezone
    return datetime.now(timezone.utc) + timedelta(hours=horas)


class DoctorAppointmentsRouterDbTestCase(_WebdataFixtureMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        self._set_up_webdata()
        self.addCleanup(self._tear_down_webdata)
        self.client = TestClient(app_module.app)

    def tearDown(self):
        _reset_tables()

    def _crear_operador_medico(self, usuario, medico_id):
        return db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)

    def _seed_solicitud(self, dni, nombre, apellido, medico_asignado_id=None, orden=False):
        sid = db_booking.create_booking_request(
            dni, nombre, apellido, "1980-01-01", "wa:11", "serv-cardio", "centro-a")
        db_booking.approve(sid, _futuro(), medico_asignado_id=medico_asignado_id, resuelta_por="admin")
        if orden:
            import psycopg
            ruta = os.path.join(self._tmp, "orden-%s.pdf" % sid)
            with open(ruta, "wb") as f:
                f.write(b"%PDF-1.4 orden de prueba")
            with psycopg.connect(_TEST_URL, connect_timeout=5) as c:
                with c.cursor() as cur:
                    cur.execute(
                        "UPDATE booking_requests SET referral_path=%s, referral_mime=%s WHERE id=%s",
                        (ruta, "application/pdf", sid))
                c.commit()
        return sid

    # ---- permitir_medico shape ------------------------------------------

    def test_my_appointments_200_for_own_doctor(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        r = self.client.get(
            "/api/admin/my-appointments", cookies=_cookies("dr-fernandez", "medico"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["medico"]["id"], "med-1")
        self.assertEqual(body["total"], 0)

    def test_my_appointments_403_solo_medico_is_a_domain_error_not_the_gate(self):
        # admin clears the central gate (permitir_medico only restricts the
        # 'medico' role) but the DOMAIN function itself still says no —
        # different check, same flat shape.
        r = self.client.get(
            "/api/admin/my-appointments", cookies=_cookies(admin_auth._admin_user(), "admin"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {"error": "solo_medico"})

    def test_my_appointments_feed_200(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        r = self.client.get(
            "/api/admin/my-appointments/feed", cookies=_cookies("dr-fernandez", "medico"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["url"].endswith(".ics"))

    def test_my_referral_200_owner_404_stranger(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_operador_medico("dr-gomez", "med-2")
        sid_propia = self._seed_solicitud("41077777", "Propia", "Orden",
                                          medico_asignado_id="med-1", orden=True)
        sid_ajena = self._seed_solicitud("41088888", "Ajena", "Orden",
                                         medico_asignado_id="med-2", orden=True)

        r = self.client.get(
            "/api/admin/my-appointments/%s/order" % sid_propia,
            cookies=_cookies("dr-fernandez", "medico"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertIn(b"orden de prueba", r.content)

        r2 = self.client.get(
            "/api/admin/my-appointments/%s/order" % sid_ajena,
            cookies=_cookies("dr-fernandez", "medico"))
        self.assertEqual(r2.status_code, 404)
        self.assertEqual(r2.json(), {"error": "orden no encontrada"})

    # ---- plain gate shape -------------------------------------------------

    def test_doctor_appointments_admin_200_for_admin(self):
        r = self.client.get(
            "/api/admin/doctors/med-1/appointments",
            cookies=_cookies(admin_auth._admin_user(), "admin"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["medico"]["id"], "med-1")

    def test_doctor_appointments_admin_404_unknown_doctor(self):
        r = self.client.get(
            "/api/admin/doctors/no-existe/appointments",
            cookies=_cookies(admin_auth._admin_user(), "admin"))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "medico_desconocido"})

    # ---- permitir_reportes shape -------------------------------------------

    def test_doctors_stats_200_for_admin(self):
        r = self.client.get(
            "/api/admin/doctors/stats", cookies=_cookies(admin_auth._admin_user(), "admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("med-1", r.json()["medicos"])

    # ---- permitir_medico + permitir_admisionista shape ---------------------

    def test_day_queue_200_for_admisionista(self):
        r = self.client.get(
            "/api/admin/day-queue", cookies=_cookies("mesa-1", "admisionista"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("grupos", r.json())

    def test_day_queue_says_where_the_desk_installs_the_hsi_extension(self):
        # La contraparte técnica, 2026-10-27: «nunca entendí cómo… llegábamos a esa pantalla».
        # The desk's «Pacientes de hoy» carries where to install the extension
        # (store or ZIP, and the guide); a doctor's own day and a server push
        # to HSI carry nothing.
        from unittest import mock
        from app.core import hsi_extension
        from app.modules.turnos import booking_requests
        inst = {"tienda": None, "zip": "/api/admin/hsi-extension/extension.zip",
                "guia": "/api/admin/hsi-extension/guide.pdf"}
        with mock.patch.object(hsi_extension, "install", return_value=inst):
            r = self.client.get("/api/admin/day-queue", cookies=_cookies("mesa-1", "admisionista"))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["hsi_instalar"], inst)
            self._crear_operador_medico("dr-fernandez", "med-1")
            r = self.client.get("/api/admin/day-queue", cookies=_cookies("dr-fernandez", "medico"))
            self.assertEqual(r.status_code, 200)
            self.assertIsNone(r.json()["hsi_instalar"])
            with mock.patch.object(booking_requests, "_emr_sync_enabled", return_value=True):
                r = self.client.get("/api/admin/day-queue", cookies=_cookies("mesa-1", "admisionista"))
            self.assertIsNone(r.json()["hsi_instalar"])

    def test_day_queue_400_medico_sin_cuenta_vinculada(self):
        # A 'medico' session whose operator row has no medico_id: the
        # central gate admits the role, the domain function is the one
        # that still says no.
        db_operators.operator_create("dr-huerfano", "clave1234", "medico", medico_id=None)
        r = self.client.get(
            "/api/admin/day-queue", cookies=_cookies("dr-huerfano", "medico"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "cuenta_sin_medico"})


if __name__ == "__main__":
    unittest.main()
