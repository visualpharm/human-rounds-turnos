"""test_intake_alerts.py — campanita de triage rojo (modules/intake/
intake_admin.py, 2026-08-01): GET .../alerts/count, GET .../alerts,
POST .../alerts/<id>/ack. Cubre el gate (medico ve/ackea sólo las suyas,
404 en una ajena — nunca 403, mismo criterio que el resto del módulo),
que sólo ROJO sin ack cuenta, y que un ack repetido no revienta.

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
         discover -s tests -p "test_intake_alerts.py"

Usa el router FastAPI real, cookie firmada con admin_auth._mint_token y una
base de datos dedicada.
"""

import os
import sys
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.patient_web import catalog             # noqa: E402
from app.core import config  # noqa: E402
from app.core import deps as core_deps  # noqa: E402
from app.auth import admin_auth         # noqa: E402
from app.auth import turnstile            # noqa: E402
from app.modules.intake import intake_admin, intake_admin_router  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_intakealerts", seed=lambda: db_operators.operators_list())


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
            cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE catalog")
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE operator_access")
            cur.execute("SELECT to_regclass('clinical_access_log') IS NOT NULL AS hay")
            if cur.fetchone()[0]:
                cur.execute("TRUNCATE clinical_access_log RESTART IDENTITY")


_env.process_set(ENABLED_MODULES="turnos,intake")
_APP = FastAPI()
_APP.add_exception_handler(
    core_deps.AdminGateError, core_deps.render_admin_gate_error)
_APP.include_router(intake_admin_router.router)
_CLIENT = TestClient(_APP)


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


_MED1 = {"id": "al-med-1", "nombre": "María José Fernández",
        "especialidad": "Cardiología", "centro": "centro-a"}
_MED2 = {"id": "al-med-2", "nombre": "Carlos Gómez",
        "especialidad": "Pediatría", "centro": "centro-a"}


def _seed_doctor(doc):
    db_catalog.catalog_upsert("medicos", doc["id"], dict(doc), borrado=False)
    catalog.invalidate()
    return doc


class BaseAlertsTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def tearDown(self):
        catalog.invalidate()

    def _get(self, path, headers=None):
        return _CLIENT.get(path, headers=headers or {}), True

    def _post(self, path, body=None, headers=None):
        return _CLIENT.post(path, json=body or {}, headers=headers or {}), True

    def _count(self, headers):
        return self._get("/api/admin/intake/alerts/count", headers=headers)

    def _list(self, headers):
        return self._get("/api/admin/intake/alerts", headers=headers)

    def _ack(self, iid, headers):
        return self._post(f"/api/admin/intake/alerts/{iid}/ack", headers=headers)

    def _medico_headers(self, usuario, medico_id):
        db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)
        return _cookie(usuario, "medico")

    def _rojo(self, doctor_id, dni="30999990"):
        fila = db_clinical.interview_create(doctor_id=doctor_id, entry_mode="qr", dni=dni)
        db_clinical.interview_update_analysis(fila["id"], "ROJO", "Dolor de pecho agudo",
                                     {}, [], [], [], "fake-model")
        db_clinical.interview_set_status(fila["id"], "done")
        return fila["id"]

    def _verde(self, doctor_id, dni="30999991"):
        fila = db_clinical.interview_create(doctor_id=doctor_id, entry_mode="qr", dni=dni)
        db_clinical.interview_update_analysis(fila["id"], "VERDE", "Control de rutina",
                                     {}, [], [], [], "fake-model")
        db_clinical.interview_set_status(fila["id"], "done")
        return fila["id"]


# ---------------------------------------------------------------- gates

class TestAlertGates(BaseAlertsTestCase):
    def test_count_sin_sesion_401(self):
        h, manejado = self._count(headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_list_sin_sesion_401(self):
        h, manejado = self._list(headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_ack_sin_sesion_401(self):
        h, manejado = self._ack(1, headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_admisionista_403(self):
        db_operators.operator_create("mesa", "clave1234", "admisionista")
        h, _ = self._count(headers=_cookie("mesa", "admisionista"))
        self.assertEqual(h.status_code, 403)


# ---------------------------------------------------------------- count/list

class TestAlertCountAndList(BaseAlertsTestCase):
    def test_solo_rojo_sin_ack_cuenta(self):
        _seed_doctor(_MED1)
        self._rojo(_MED1["id"])
        self._verde(_MED1["id"])
        h, _ = self._count(headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["count"], 1)

    def test_ackeada_no_cuenta(self):
        _seed_doctor(_MED1)
        iid = self._rojo(_MED1["id"])
        db_clinical.interview_mark_alert_seen(iid, "admin")
        h, _ = self._count(headers=_admin_headers())
        self.assertEqual(h.json()["count"], 0)

    def test_medico_ve_solo_las_suyas(self):
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        self._rojo(_MED1["id"], dni="30111110")
        self._rojo(_MED2["id"], dni="30111111")
        headers = self._medico_headers("dr-fernandez", _MED1["id"])
        h, _ = self._count(headers=headers)
        self.assertEqual(h.json()["count"], 1)
        h, _ = self._list(headers=headers)
        dnis = [a["dni"] for a in h.json()["items"]]
        self.assertEqual(dnis, ["30111110"])

    def test_rol_propio_con_atencion_medica_ve_solo_las_de_su_medico(self):
        # K10 (2026-10-23): sólo «medico» quedaba acotado; un rol creado en el
        # panel con «atención médica» veía las alertas ROJAS de todos.
        from app.core import permissions
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        self._rojo(_MED1["id"], dni="30111110")
        self._rojo(_MED2["id"], dni="30111111")
        try:
            slug = permissions.create_role("Guardia", "Atiende la guardia.", ["atencion_medica"])["slug"]
        except permissions.RoleError:
            slug = "guardia"
        db_operators.operator_create("guardia", "clave1234", "admisionista", medico_id=_MED1["id"])
        db_operators.operator_create("guardia2", "clave1234", "admisionista")
        with db._connect() as conn, conn.cursor() as cur:
            for usuario in ("guardia", "guardia2"):
                cur.execute("INSERT INTO operator_access (username, role_slug) VALUES (%s, %s)",
                            (usuario, slug))
        h, _ = self._list(headers=_cookie("guardia", slug))
        self.assertEqual(h.status_code, 200)
        self.assertEqual([a["dni"] for a in h.json()["items"]], ["30111110"])
        # Sin médico vinculado no ve ninguna.
        h, _ = self._list(headers=_cookie("guardia2", slug))
        self.assertEqual(h.json()["items"], [])

    def test_la_lista_deja_en_el_registro_a_cada_paciente_mostrado(self):
        from app.core import access_log
        access_log._seen.clear()
        _seed_doctor(_MED1)
        self._rojo(_MED1["id"], dni="30111110")
        headers = self._medico_headers("dr-fernandez", _MED1["id"])
        self._list(headers=headers)
        self._list(headers=headers)   # la lista se refresca: no duplica
        self.assertEqual([(r["actor"], r["action"]) for r in access_log.history("30111110")],
                         [("dr-fernandez", "alertas")])
        self.assertEqual(access_log.history("30111111"), [])

    def test_admin_ve_todas(self):
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        self._rojo(_MED1["id"], dni="30111110")
        self._rojo(_MED2["id"], dni="30111111")
        h, _ = self._count(headers=_admin_headers())
        self.assertEqual(h.json()["count"], 2)

    def test_list_trae_paciente_servicio_medico(self):
        _seed_doctor(_MED1)
        sid = db_booking.create_booking_request("30222220", "Rita", "Paciente", "1980-01-01",
                                        "wa:1122334455", "serv-cardio", "centro-a")
        fila = db_clinical.interview_create(doctor_id=_MED1["id"], entry_mode="post_booking",
                                   request_id=sid, dni="30222220", matched=True)
        db_clinical.interview_update_analysis(fila["id"], "ROJO", "resumen", {}, [], [], [], "fake-model")
        db_clinical.interview_set_status(fila["id"], "done")
        h, _ = self._list(headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        item = h.json()["items"][0]
        self.assertEqual(item["paciente"], "Rita Paciente")
        self.assertEqual(item["medico"], _MED1["nombre"])
        self.assertEqual(item["contacto"], "wa:1122334455")

    def test_medico_sin_cuenta_vinculada_cuenta_cero(self):
        db_operators.operator_create("dr-sin-medico", "clave1234", "medico")
        h, _ = self._count(headers=_cookie("dr-sin-medico", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["count"], 0)


# ---------------------------------------------------------------- ack

class TestAlertAck(BaseAlertsTestCase):
    def test_ack_admin_200_y_desaparece(self):
        _seed_doctor(_MED1)
        iid = self._rojo(_MED1["id"])
        h, _ = self._ack(iid, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        h, _ = self._count(headers=_admin_headers())
        self.assertEqual(h.json()["count"], 0)

    def test_ack_repetido_no_revienta(self):
        _seed_doctor(_MED1)
        iid = self._rojo(_MED1["id"])
        h1, _ = self._ack(iid, headers=_admin_headers())
        self.assertEqual(h1.status_code, 200)
        # Ya estaba vista: interview_mark_alert_seen no vuelve a matchear
        # (WHERE alert_seen_at IS NULL), así que el segundo ack es "no encontrado".
        h2, _ = self._ack(iid, headers=_admin_headers())
        self.assertEqual(h2.status_code, 404)

    def test_ack_inexistente_404(self):
        h, _ = self._ack(999999, headers=_admin_headers())
        self.assertEqual(h.status_code, 404)

    def test_medico_no_puede_ackear_una_ajena(self):
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        iid_ajena = self._rojo(_MED2["id"])
        headers = self._medico_headers("dr-fernandez", _MED1["id"])
        h, _ = self._ack(iid_ajena, headers=headers)
        self.assertEqual(h.status_code, 404)
        # Sigue sin ackear: sigue contando para su dueño real.
        h, _ = self._count(headers=_admin_headers())
        self.assertEqual(h.json()["count"], 1)

    def test_medico_ackea_la_propia(self):
        _seed_doctor(_MED1)
        iid = self._rojo(_MED1["id"])
        headers = self._medico_headers("dr-fernandez", _MED1["id"])
        h, _ = self._ack(iid, headers=headers)
        self.assertEqual(h.status_code, 200)


# ------------------------------------------------------- reanálisis ROJO ---
# "Actualizar entrevista" (2026-08-01): reabrir una entrevista YA ackeada y
# que el reanálisis vuelva a dar ROJO tiene que hacer sonar la campanita de
# nuevo — db_clinical.interview_update_analysis resetea alert_seen_at/_by cada vez
# que el triage NUEVO es ROJO (ver su docstring), primera vez o reanálisis
# da lo mismo. Nivel DB puro, sin pasar por interview_agent — más directo
# para probar exactamente esta regla.

class TestReanalisisRojoReabreAlerta(BaseAlertsTestCase):
    def test_reanalisis_que_sigue_rojo_limpia_el_ack(self):
        _seed_doctor(_MED1)
        iid = self._rojo(_MED1["id"])
        db_clinical.interview_mark_alert_seen(iid, "admin")
        vista = db_clinical.interview_get(iid)
        self.assertIsNotNone(vista["alert_seen_at"])

        # Reanálisis (mismo triage ROJO) — el ack se limpia solo.
        db_clinical.interview_update_analysis(iid, "ROJO", "Sigue con dolor de pecho",
                                     {}, [], [], [], "fake-model")
        actualizada = db_clinical.interview_get(iid)
        self.assertIsNone(actualizada["alert_seen_at"])
        self.assertIsNone(actualizada["alert_seen_by"])

    def test_reanalisis_que_escala_a_rojo_tambien_limpia_el_ack(self):
        _seed_doctor(_MED1)
        iid = self._verde(_MED1["id"])
        db_clinical.interview_mark_alert_seen(iid, "admin")  # no-op: nunca fue ROJO

        db_clinical.interview_update_analysis(iid, "ROJO", "Ahora dolor de pecho en reposo",
                                     {}, [], [], [], "fake-model")
        actualizada = db_clinical.interview_get(iid)
        self.assertEqual(actualizada["triage"], "ROJO")
        self.assertIsNone(actualizada["alert_seen_at"])

    def test_reanalisis_que_baja_de_rojo_no_toca_un_ack_previo(self):
        _seed_doctor(_MED1)
        iid = self._rojo(_MED1["id"])
        db_clinical.interview_mark_alert_seen(iid, "admin")

        db_clinical.interview_update_analysis(iid, "VERDE", "Se resolvió solo",
                                     {}, [], [], [], "fake-model")
        actualizada = db_clinical.interview_get(iid)
        self.assertEqual(actualizada["triage"], "VERDE")
        self.assertIsNotNone(actualizada["alert_seen_at"])

    def test_reopen_marca_reopened_at(self):
        _seed_doctor(_MED1)
        iid = self._rojo(_MED1["id"])
        reabierta = db_clinical.interview_reopen(iid)
        self.assertIsNotNone(reabierta)
        self.assertEqual(reabierta["status"], "active")
        self.assertIsNotNone(reabierta["reopened_at"])

    def test_reopen_sobre_entrevista_activa_no_matchea(self):
        _seed_doctor(_MED1)
        fila = db_clinical.interview_create(doctor_id=_MED1["id"], entry_mode="qr", dni="30999992")
        self.assertIsNone(db_clinical.interview_reopen(fila["id"]))


if __name__ == "__main__":
    unittest.main()
