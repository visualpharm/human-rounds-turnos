"""test_doctor_patients.py — «Mis pacientes» (doctor_patients.py, 2026-08-01):
el roster longitudinal de un médico. Cubre el gate (medico ve sólo lo suyo,
admin necesita ?medico=), las DOS fuentes (solicitud por
assigned_doctor_id/slot_doctor_id, entrevista por doctor_id — incluida la
que nunca matcheó turno) y el orden por severidad de triage.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_doctor_patients -v

Mismo patrón que test_cola_dia.py: webdata propio vía TURNOS_WEBDATA_DIR,
router FastAPI real con `TestClient`, sesiones por cookie firmada y base de
datos dedicada para correr en paralelo sin pisar otras suites.
"""

import os
import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.auth import admin_auth       # noqa: E402
from app.admin_panel import doctor_patients, doctor_patients_router  # noqa: E402
from app.patient_web import catalog          # noqa: E402
from app.auth import turnstile        # noqa: E402
from app.core import config  # noqa: E402
from app.core import tz  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(doctor_patients_router.router)
_CLIENT = TestClient(_app)

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

_ART = tz.tzinfo()

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_docpatients", seed=lambda: db_operators.operators_list())


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


_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS = {"servicios": [
    {"id": "serv-cardio", "nombre": "Cardiología", "centro": "centro-a", "modo": "solicitud", "cluster": "cardiologia"},
]}
_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología", "centro": "centro-a",
     "entrevista_habilitada": True},
    {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Pediatría", "centro": "centro-a",
     "entrevista_habilitada": False},
]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
        json.dump(_CENTROS, f)
    with open(os.path.join(webdir, "servicios.json"), "w", encoding="utf-8") as f:
        json.dump(_SERVICIOS, f)
    with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
        json.dump(_MEDICOS, f)


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


def _reportes_headers(usuario="reportera-1"):
    return _cookie(usuario, "reportes")


def _medico_headers(usuario, medico_id):
    db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)
    return _cookie(usuario, "medico")


def _dt_art(dias_desde_hoy, hora=10):
    d = datetime.now(_ART) + timedelta(days=dias_desde_hoy)
    return d.replace(hour=hora, minute=0, second=0, microsecond=0)


class DoctorPatientsTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        self._tmp = tempfile.mkdtemp(prefix="doctor-patients-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    # ---- helpers --------------------------------------------------------

    def _get(self, qs="", headers=None):
        return _CLIENT.get("/api/admin/doctor-patients" + qs, headers=headers)

    def _seed_aprobada(self, dni, nombre, apellido, turno_dt, medico_asignado_id=None):
        sid = db_booking.create_booking_request(dni, nombre, apellido, "1980-01-01", "wa:11",
                                        "serv-cardio", "centro-a")
        # sobreturno=True: distintos pacientes sintéticos comparten a propósito
        # (medico_asignado_id, turno_dt) en varios tests de este archivo (orden
        # por severidad/recencia, no colisión de horario) — desde
        # booking_requests_doctor_slot_uniq (2026-08-05) esa combinación exige
        # el mismo exención que usa un sobreturno real.
        db_booking.approve(sid, turno_dt, medico_asignado_id=medico_asignado_id,
                  resuelta_por="admin", sobreturno=True)
        return sid

    def _seed_con_slot(self, dni, nombre, apellido, medico_id):
        """Reservó un turno concreto de la agenda de `medico_id` (slot_doctor_id)
        sin que el hospital haya aprobado todavía — segunda fuente de
        patients_of_doctor, distinta de assigned_doctor_id."""
        return db_booking.create_booking_request(dni, nombre, apellido, "1980-01-01", "wa:11",
                                         "serv-cardio", "centro-a", slot_medico_id=medico_id)

    def _seed_entrevista(self, doctor_id, request_id=None, dni=None, triage="VERDE",
                         resumen="Resumen", entered_name=None, entered_surname=None):
        fila = db_clinical.interview_create(
            doctor_id=doctor_id, entry_mode="post_booking" if request_id else "qr",
            request_id=request_id, dni=dni, entered_name=entered_name,
            entered_surname=entered_surname, matched=request_id is not None,
        )
        db_clinical.interview_update_analysis(fila["id"], triage, resumen, {}, [], [], [], "fake-model")
        db_clinical.interview_set_status(fila["id"], "done")
        return fila["id"]

    # ---- el contacto: la misma regla que «Mis turnos» (problems.md №4) ------

    def _item(self, dni):
        h = self._get("?medico=med-1", headers=_admin_headers())
        return [p for p in h.json()["items"] if p["dni"] == dni][0]

    def test_contacto_principal_es_el_de_la_solicitud_y_la_cuenta_distinta_se_ve_aparte(self):
        # El responsable del producto (hub, 2026-10-23): la misma regla que «Mis turnos».
        db_accounts.create_user("42500001", "Carla", "Reserva", "1990-06-15", "F", "+5492254100002", "x" * 12)
        self._seed_aprobada("42500001", "Carla", "Reserva", _dt_art(2), medico_asignado_id="med-1")
        p = self._item("42500001")
        self.assertEqual((p["contacto"], p["contacto_de"]), ("wa:11", "solicitud"))
        self.assertEqual((p["contacto_cuenta"], p["contacto_cuenta_de"]), ("+5492254100002", "paciente"))

    def test_familiar_a_cargo_muestra_al_responsable_no_dice_paciente(self):
        # Antes: el contacto del turno del menor (el teléfono del adulto) sin
        # decir de quién era — el médico creía estar llamando al paciente.
        db_accounts.create_user("42500002", "Ana", "Perez", "1985-01-01", "F", "+5492254100003", "x" * 12)
        sid = db_booking.create_booking_request("42500003", "Tomi", "Perez", "2018-01-01", "+5492254100003",
                                        "serv-cardio", "centro-a", solicitante_dni="42500002")
        db_booking.approve(sid, _dt_art(2), medico_asignado_id="med-1", resuelta_por="admin", sobreturno=True)
        p = self._item("42500003")
        self.assertEqual(p["contacto"], "+5492254100003")
        self.assertEqual(p["contacto_de"], "responsable")
        self.assertEqual(p["contacto_responsable"], "Ana Perez")

    def test_familiar_con_otro_numero_en_la_solicitud_muestra_los_dos(self):
        db_accounts.create_user("42500005", "Ana", "Perez", "1985-01-01", "F", "+5492254100005", "x" * 12)
        sid = db_booking.create_booking_request("42500006", "Tomi", "Perez", "2018-01-01", "+5492254199999",
                                        "serv-cardio", "centro-a", solicitante_dni="42500005")
        db_booking.approve(sid, _dt_art(2), medico_asignado_id="med-1", resuelta_por="admin", sobreturno=True)
        p = self._item("42500006")
        self.assertEqual((p["contacto"], p["contacto_de"]), ("+5492254199999", "solicitud"))
        self.assertEqual((p["contacto_cuenta"], p["contacto_cuenta_de"]),
                         ("+5492254100005", "responsable"))
        self.assertEqual(p["contacto_responsable"], "Ana Perez")

    def test_sin_cuenta_el_contacto_es_el_de_la_solicitud_mas_nueva(self):
        self._seed_aprobada("42500004", "Sin", "Cuenta", _dt_art(2), medico_asignado_id="med-1")
        p = self._item("42500004")
        self.assertEqual((p["contacto"], p["contacto_de"]), ("wa:11", "solicitud"))

    # ---- gate -------------------------------------------------------------

    def test_401_sin_sesion(self):
        h = self._get()
        self.assertEqual(h.status_code, 401)

    def test_admisionista_403(self):
        db_operators.operator_create("mesa", "clave1234", "admisionista")
        h = self._get(headers=_cookie("mesa", "admisionista"))
        self.assertEqual(h.status_code, 403)

    def test_medico_sin_cuenta_vinculada_400(self):
        db_operators.operator_create("dr-sin-medico", "clave1234", "medico")
        h = self._get(headers=_cookie("dr-sin-medico", "medico"))
        self.assertEqual(h.status_code, 400)

    def test_admin_sin_medico_devuelve_picker(self):
        h = self._get(headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["items"], [])
        ids = [m["id"] for m in h.json()["medicos"]]
        self.assertIn("med-1", ids)
        self.assertIn("med-2", ids)

    def test_admin_con_medico_200(self):
        h = self._get("?medico=med-1", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["medico_id"], "med-1")

    def test_reportes_403(self):
        # No hay rol 'operador' en el esquema actual (operators_role_check
        # sólo admite admin/medico/admisionista/reportes) — 'reportes' es la
        # otra cuenta no-admin que existe y que este endpoint no habilita
        # (permitir_reportes no se pasa).
        h = self._get("?medico=med-1", headers=_reportes_headers())
        self.assertEqual(h.status_code, 403)

    # ---- scope: un médico nunca ve pacientes de otro ----------------------

    def test_medico_ve_solo_sus_propios_pacientes(self):
        self._seed_aprobada("30111111", "Ana", "Uno", _dt_art(3), medico_asignado_id="med-1")
        self._seed_aprobada("30222222", "Beto", "Dos", _dt_art(3), medico_asignado_id="med-2")
        headers = _medico_headers("dr-fernandez", "med-1")
        h = self._get(headers=headers)
        self.assertEqual(h.status_code, 200)
        dnis = [p["dni"] for p in h.json()["items"]]
        self.assertIn("30111111", dnis)
        self.assertNotIn("30222222", dnis)

    def test_medico_ignora_el_query_param(self):
        # rol medico siempre ve lo suyo, nunca lo que pida ?medico= (mismo
        # criterio que day-queue).
        self._seed_aprobada("30111111", "Ana", "Uno", _dt_art(3), medico_asignado_id="med-1")
        headers = _medico_headers("dr-fernandez", "med-1")
        h = self._get("?medico=med-2", headers=headers)
        self.assertEqual(h.json()["medico_id"], "med-1")

    def test_rol_propio_con_atencion_medica_no_elige_otro_medico(self):
        # K10 (2026-10-23): un rol creado en el panel con «atención médica»
        # pasaba ?medico= de cualquiera y veía la lista (con triage y resumen
        # de entrevista) de ese otro médico.
        from app.core import permissions
        self._seed_aprobada("30222222", "Beto", "Dos", _dt_art(3), medico_asignado_id="med-2")
        try:
            slug = permissions.create_role("Guardia", "Atiende la guardia.", ["atencion_medica"])["slug"]
        except permissions.RoleError:
            slug = "guardia"
        db_operators.operator_create("guardia", "clave1234", "admisionista", medico_id="med-1")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, role_slug) VALUES ('guardia', %s)",
                        (slug,))
        h = self._get("?medico=med-2", headers=_cookie("guardia", slug))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["medico_id"], "med-1")
        self.assertEqual(h.json()["items"], [])

    def test_rol_propio_sin_medico_vinculado_no_ve_ninguna_lista(self):
        from app.core import permissions
        self._seed_aprobada("30222222", "Beto", "Dos", _dt_art(3), medico_asignado_id="med-2")
        try:
            slug = permissions.create_role("Guardia", "Atiende la guardia.", ["atencion_medica"])["slug"]
        except permissions.RoleError:
            slug = "guardia"
        db_operators.operator_create("guardia2", "clave1234", "admisionista")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, role_slug) VALUES ('guardia2', %s)",
                        (slug,))
        h = self._get("?medico=med-2", headers=_cookie("guardia2", slug))
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json(), {"error": "cuenta_sin_medico"})

    def test_todos_los_pacientes_elige_cualquier_medico_y_queda_registrado(self):
        # El responsable del producto 2026-10-23 (q781376, variante B): la jefatura y la auditoría
        # ven los pacientes de todos los médicos sin ser admin; apagado por
        # default y cada paciente mostrado queda en el registro.
        from app.core import access_log, permissions
        access_log._seen.clear()
        self._seed_aprobada("30222222", "Beto", "Dos", _dt_art(3), medico_asignado_id="med-2")
        try:
            slug = permissions.create_role("Jefatura", "Jefatura de servicio.",
                                           ["atencion_medica", "todos_los_pacientes"])["slug"]
        except permissions.RoleError:
            slug = "jefatura"
        db_operators.operator_create("jefa", "clave1234", "admisionista")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, role_slug) VALUES ('jefa', %s)",
                        (slug,))
        h = self._get("?medico=med-2", headers=_cookie("jefa", slug))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["medico_id"], "med-2")
        self.assertEqual([i["dni"] for i in h.json()["items"]], ["30222222"])
        self.assertIn("medicos", h.json())
        self.assertEqual([(r["actor"], r["action"], r["allowed"])
                          for r in access_log.history("30222222")],
                         [("jefa", "mis_pacientes", True)])

    def test_medico_con_todos_los_pacientes_sin_parametro_ve_los_suyos(self):
        # La pantalla del médico nunca manda ?medico=: con el permiso agregado
        # sigue arrancando por su propia lista.
        self._seed_aprobada("30111111", "Ana", "Uno", _dt_art(3), medico_asignado_id="med-1")
        headers = _medico_headers("dr-fernandez", "med-1")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, granted) VALUES ('dr-fernandez', %s)",
                        (["todos_los_pacientes"],))
        h = self._get(headers=headers)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["medico_id"], "med-1")
        self.assertEqual([i["dni"] for i in h.json()["items"]], ["30111111"])

    def test_la_lista_deja_en_el_registro_a_cada_paciente(self):
        from app.core import access_log
        access_log._seen.clear()
        self._seed_aprobada("30111111", "Ana", "Uno", _dt_art(3), medico_asignado_id="med-1")
        self._get(headers=_medico_headers("dr-fernandez", "med-1"))
        self.assertEqual([(r["actor"], r["action"]) for r in access_log.history("30111111")],
                         [("dr-fernandez", "mis_pacientes")])

    # ---- dos fuentes: assigned_doctor_id / slot_doctor_id / entrevista ----

    def test_incluye_por_slot_doctor_id(self):
        self._seed_con_slot("30333333", "Cami", "Tres", "med-1")
        h = self._get("?medico=med-1", headers=_admin_headers())
        dnis = [p["dni"] for p in h.json()["items"]]
        self.assertIn("30333333", dnis)

    def test_incluye_entrevista_sin_turno_matcheado(self):
        # QR sin match: no hay booking_requests, sólo la entrevista con dni.
        self._seed_entrevista("med-1", request_id=None, dni="30444444",
                              triage="AMARILLO", entered_name="Delia", entered_surname="Cuatro")
        h = self._get("?medico=med-1", headers=_admin_headers())
        dnis = [p["dni"] for p in h.json()["items"]]
        self.assertIn("30444444", dnis)
        item = next(p for p in h.json()["items"] if p["dni"] == "30444444")
        self.assertEqual(item["ultima_entrevista"]["triage"], "AMARILLO")
        self.assertEqual(item["nombre"], "Delia Cuatro")

    def test_proximo_turno_solo_aprobada_y_futura(self):
        self._seed_aprobada("30555555", "Elsa", "Cinco", _dt_art(5), medico_asignado_id="med-1")
        self._seed_aprobada("30666666", "Fede", "Seis", _dt_art(-5), medico_asignado_id="med-1")
        h = self._get("?medico=med-1", headers=_admin_headers())
        futuro = next(p for p in h.json()["items"] if p["dni"] == "30555555")
        pasado = next(p for p in h.json()["items"] if p["dni"] == "30666666")
        self.assertIsNotNone(futuro["proximo_turno"])
        self.assertIsNone(pasado["proximo_turno"])

    # ---- orden por severidad de triage -------------------------------------

    def test_orden_por_severidad_de_triage(self):
        sid_verde = self._seed_aprobada("30777770", "Verde", "Paciente", _dt_art(2), medico_asignado_id="med-1")
        self._seed_entrevista("med-1", request_id=sid_verde, dni="30777770", triage="VERDE")

        sid_rojo = self._seed_aprobada("30777771", "Rojo", "Paciente", _dt_art(2), medico_asignado_id="med-1")
        self._seed_entrevista("med-1", request_id=sid_rojo, dni="30777771", triage="ROJO")

        sid_naranja = self._seed_aprobada("30777772", "Naranja", "Paciente", _dt_art(2), medico_asignado_id="med-1")
        self._seed_entrevista("med-1", request_id=sid_naranja, dni="30777772", triage="NARANJA")

        # Sin entrevista: va al final, después de todas las severidades conocidas.
        self._seed_aprobada("30777773", "SinEntrevista", "Paciente", _dt_art(2), medico_asignado_id="med-1")

        h = self._get("?medico=med-1", headers=_admin_headers())
        orden_dnis = [p["dni"] for p in h.json()["items"]]
        self.assertEqual(orden_dnis, ["30777771", "30777772", "30777770", "30777773"])

    def test_ties_por_severidad_ordenan_por_recencia(self):
        sid_a = self._seed_aprobada("30888880", "PrimeraRoja", "Paciente", _dt_art(2), medico_asignado_id="med-1")
        self._seed_entrevista("med-1", request_id=sid_a, dni="30888880", triage="ROJO")

        sid_b = self._seed_aprobada("30888881", "SegundaRoja", "Paciente", _dt_art(2), medico_asignado_id="med-1")
        self._seed_entrevista("med-1", request_id=sid_b, dni="30888881", triage="ROJO")

        h = self._get("?medico=med-1", headers=_admin_headers())
        orden_dnis = [p["dni"] for p in h.json()["items"] if p["dni"] in ("30888880", "30888881")]
        # La entrevista creada DESPUÉS (30888881) es la más reciente -> primero.
        self.assertEqual(orden_dnis, ["30888881", "30888880"])

    # ---- la entrevista previa apagada no deja rastro en la lista -------------

    def test_con_la_entrevista_encendida_la_respuesta_lo_dice_y_trae_el_triage(self):
        self._seed_entrevista("med-1", dni="30999991", triage="ROJO", entered_name="Ana", entered_surname="Uno")
        j = self._get("?medico=med-1", headers=_admin_headers()).json()
        self.assertTrue(j["entrevista_activa"])
        self.assertEqual(j["items"][0]["ultima_entrevista"]["triage"], "ROJO")

    def test_sin_entrevista_habilitada_no_hay_triage_ni_resumen_ni_entrevista_en_ninguna_fila(self):
        # El responsable del producto 27.10: «triage, en curso» en la lista de un médico que no la tiene
        # encendida. Las entrevistas que hubiera (de antes de apagarla, o por QR)
        # no viajan: la columna no se oculta sola si la celda llega con datos.
        sid = self._seed_aprobada("30999992", "Con", "Turno", _dt_art(2), medico_asignado_id="med-2")
        self._seed_entrevista("med-2", request_id=sid, dni="30999992", triage="ROJO", resumen="Dolor de pecho")
        self._seed_entrevista("med-2", dni="30999993", triage="AMARILLO",
                              entered_name="Solo", entered_surname="Entrevista")
        j = self._get("?medico=med-2", headers=_admin_headers()).json()
        self.assertFalse(j["entrevista_activa"])
        self.assertEqual([p["dni"] for p in j["items"]], ["30999992"])
        self.assertIsNone(j["items"][0]["ultima_entrevista"])

    def test_el_medico_sin_entrevista_habilitada_recibe_la_lista_sin_triage(self):
        headers = _medico_headers("dra-dos", "med-2")
        sid = self._seed_aprobada("30999994", "Otra", "Persona", _dt_art(2), medico_asignado_id="med-2")
        self._seed_entrevista("med-2", request_id=sid, dni="30999994", triage="NARANJA")
        j = self._get(headers=headers).json()
        self.assertFalse(j["entrevista_activa"])
        self.assertIsNone(j["items"][0]["ultima_entrevista"])


if __name__ == "__main__":
    unittest.main()
