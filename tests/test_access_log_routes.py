"""test_access_log_routes.py — las pantallas de la mesa y la ficha dejan su
fila en el registro de accesos, y «quién vio a este paciente» es sólo del
admin (K10, 2026-10-23; core/access_log.py).

Cubre: la ficha (abrirla registra; un médico con «pacientes» agregado sólo
abre la de los suyos), la orden de derivación (admin y médico propio
registrados; el intento sobre la de otro médico, registrado como negado),
la cola de solicitudes y la de «Pacientes de hoy» (una fila por paciente,
sin duplicar en los refrescos), el hallazgo por DNI exacto de un paciente
ajeno, y el endpoint del registro (admin 200, mesa y médico 403).

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
         -m unittest discover -s tests -p "test_access_log_routes.py"
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="")
_env.process_set(TURNOS_TURNSTILE_SECRET="")
_env.process_set(ENABLED_MODULES="turnos,intake")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import deps as core_deps  # noqa: E402
from app.core import access_log  # noqa: E402
from app.core import tz  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402
from app.admin_panel import admin_agenda_print_screen, admin_patients_router  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.modules.turnos import booking_requests_router, doctor_appointments, doctor_appointments_router  # noqa: E402
from app.patient_web import catalog  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_access_routes", seed=lambda: db_operators.operators_list())

_APP = FastAPI()
_APP.add_exception_handler(core_deps.AdminGateError, core_deps.render_admin_gate_error)
_APP.include_router(admin_patients_router.router)
_APP.include_router(booking_requests_router.router)
_APP.include_router(doctor_appointments_router.router)
_APP.include_router(admin_agenda_print_screen.api_router)
_CLIENT = TestClient(_APP)

_MEDICOS = [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología", "centro": "centro-a"},
    {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Pediatría", "centro": "centro-a"},
]
_DNI, _AJENO = "30111222", "30333444"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin():
    return _cookie(admin_auth._admin_user(), "admin")


def _acciones(dni):
    return [(r["actor"], r["action"], r["allowed"]) for r in reversed(access_log.history(dni))]


class RoutesCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE clinical_access_log, booking_requests, interviews, users, "
                        "operators, operator_access RESTART IDENTITY CASCADE")
        access_log._seen.clear()
        admin_auth._ATTEMPTS.clear()
        p = mock.patch.object(catalog, "doctors", return_value=[dict(m) for m in _MEDICOS])
        p.start()
        self.addCleanup(p.stop)
        db_operators.operator_create("mesa", "clave1234", "admisionista")
        db_operators.operator_create("dra", "clave1234", "medico", medico_id="med-1")

    def _turno(self, dni, medico_id, cuando=None):
        sid = db_booking.create_booking_request(dni, "Ana", "Paz", "1980-01-01", "ana@example.com",
                                        "cardiologia", "centro-a")
        db_booking.approve(sid, cuando or (datetime.now(tz.tzinfo()) + timedelta(days=1)),
                   medico_asignado_id=medico_id, medico_asignado=next(
                       m["nombre"] for m in _MEDICOS if m["id"] == medico_id),
                   resuelta_por="admin")
        return sid

    def _usuario(self, dni):
        db_accounts.create_user(dni, "Ana", "Paz", "1980-01-01", "F", "ana@example.com", "clave1234")


class TestFicha(RoutesCase):
    def test_abrir_la_ficha_queda_registrado(self):
        self._usuario(_DNI)
        r = _CLIENT.get(f"/api/admin/patients/{_DNI}", headers=_admin())
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.json()["ver_accesos"], True)
        r = _CLIENT.get(f"/api/admin/patients/{_DNI}", headers=_cookie("mesa", "admisionista"))
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.json()["ver_accesos"], False)
        self.assertEqual(_acciones(_DNI), [(admin_auth._admin_user(), "ficha", True),
                                           ("mesa", "ficha", True)])

    def test_medico_con_pacientes_agregado_solo_abre_la_de_los_suyos(self):
        self._usuario(_DNI)
        self._usuario(_AJENO)
        self._turno(_DNI, "med-1")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, granted) VALUES ('dra', %s)",
                        (["pacientes"],))
        h = _cookie("dra", "medico")
        self.assertEqual(_CLIENT.get(f"/api/admin/patients/{_DNI}", headers=h).status_code, 200)
        r = _CLIENT.get(f"/api/admin/patients/{_AJENO}", headers=h)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "paciente no encontrado"})
        self.assertEqual(_acciones(_AJENO), [("dra", "ficha", False)])


class TestRegistroEndpoint(RoutesCase):
    def test_solo_el_admin_ve_quien_miro_al_paciente(self):
        self._usuario(_DNI)
        _CLIENT.get(f"/api/admin/patients/{_DNI}", headers=_cookie("mesa", "admisionista"))
        r = _CLIENT.get(f"/api/admin/patients/{_DNI}/access-log", headers=_admin())
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(i["usuario"], i["accion"], i["permitido"]) for i in r.json()["items"]],
                         [("mesa", "ficha", True)])
        self.assertEqual(r.json()["retencion_dias"], access_log.retention_days())
        for usuario, rol in (("mesa", "admisionista"), ("dra", "medico")):
            self.assertEqual(_CLIENT.get(f"/api/admin/patients/{_DNI}/access-log",
                                         headers=_cookie(usuario, rol)).status_code, 403)


class TestOrden(RoutesCase):
    def test_orden_admin_y_medico_propio_registrados_el_ajeno_negado(self):
        mia = self._turno(_DNI, "med-1")
        ajena = self._turno(_AJENO, "med-2")
        _CLIENT.get(f"/api/admin/order/{mia}", headers=_admin())
        _CLIENT.get(f"/api/admin/order/{mia}", headers=_cookie("dra", "medico"))
        self.assertEqual(_CLIENT.get(f"/api/admin/order/{ajena}",
                                     headers=_cookie("dra", "medico")).status_code, 404)
        _CLIENT.get(f"/api/admin/my-appointments/{mia}/order", headers=_cookie("dra", "medico"))
        self.assertEqual(_CLIENT.get(f"/api/admin/my-appointments/{ajena}/order",
                                     headers=_cookie("dra", "medico")).status_code, 404)
        self.assertEqual(_acciones(_DNI), [(admin_auth._admin_user(), "orden", True),
                                           ("dra", "orden", True), ("dra", "orden", True)])
        self.assertEqual(_acciones(_AJENO), [("dra", "orden", False), ("dra", "orden", False)])


class TestListas(RoutesCase):
    def test_cola_de_solicitudes_registra_cada_paciente_una_vez(self):
        for dni in (_DNI, _AJENO):
            db_booking.create_booking_request(dni, "Ana", "Paz", "1980-01-01", "ana@example.com",
                                      "cardiologia", "centro-a")
        for _ in range(2):
            self.assertEqual(_CLIENT.get("/api/admin/requests",
                                         headers=_cookie("mesa", "admisionista")).status_code, 200)
        self.assertEqual(_acciones(_DNI), [("mesa", "solicitudes", True)])
        self.assertEqual(_acciones(_AJENO), [("mesa", "solicitudes", True)])

    def test_pacientes_de_hoy_registra_a_los_que_muestra(self):
        hoy = datetime.now(tz.tzinfo()).replace(hour=23, minute=0, second=0, microsecond=0)
        self._turno(_DNI, "med-1", cuando=hoy)
        self._turno(_AJENO, "med-2", cuando=hoy)
        self.assertEqual(_CLIENT.get("/api/admin/day-queue", headers=_cookie("dra", "medico")).status_code, 200)
        self.assertEqual(_acciones(_DNI), [("dra", "cola", True)])
        self.assertEqual(_acciones(_AJENO), [])
        self.assertEqual(_CLIENT.get("/api/admin/day-queue",
                                     headers=_cookie("mesa", "admisionista")).status_code, 200)
        self.assertEqual(_acciones(_AJENO), [("mesa", "cola", True)])


class TestBusqueda(RoutesCase):
    def test_medico_halla_por_dni_exacto_a_un_ajeno_y_queda_registrado(self):
        self._usuario(_AJENO)
        r = _CLIENT.get(f"/api/admin/patients?q={_AJENO}", headers=_cookie("dra", "medico"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([i["dni"] for i in r.json()["items"]], [_AJENO])
        # The list itself leaves a row per listed patient (security review 2026-11): the search row and the list row.
        self.assertEqual(_acciones(_AJENO), [("dra", "busqueda", True), ("dra", "ficha", True)])
        # Su paciente no deja fila de búsqueda: ya lo puede ver (sólo la fila de la lista).
        self._usuario(_DNI)
        self._turno(_DNI, "med-1")
        _CLIENT.get(f"/api/admin/patients?q={_DNI}", headers=_cookie("dra", "medico"))
        self.assertEqual(_acciones(_DNI), [("dra", "ficha", True)])


class TestImpresionDeLaAgenda(RoutesCase):
    """«Turnos programados» (/admin/cola/print, 2026-11-07): the day of one
    doctor on HSI's A4 sheet — header, one block per agenda window, an unknown
    field an empty cell; every printed patient leaves an `agenda_impresa` row
    and a doctor asking for another doctor's day is refused and recorded."""

    def setUp(self):
        super().setUp()
        self.manana = (datetime.now(tz.tzinfo()) + timedelta(days=1)).replace(
            hour=17, minute=30, second=0, microsecond=0)
        dia_semana = self.manana.date().isoweekday() % 7
        medico = dict(_MEDICOS[0], centros=[{"centro": "centro-a", "franjas": [
            {"dias": [dia_semana], "desde": "17:00", "hasta": "19:20", "cada_min": 20},
            {"dias": [dia_semana], "desde": "19:20", "hasta": "20:00", "cada_min": 20}]}])
        catalogo = {"med-1": medico, "med-2": dict(_MEDICOS[1])}
        for p in (mock.patch.object(doctor_appointments, "doctor_of",
                                    lambda mid, incluir_incompletos=False: catalogo.get(mid)),
                  mock.patch.object(catalog, "services", return_value=[])):
            p.start()
            self.addCleanup(p.stop)

    def _hoja(self, headers, medico="med-1"):
        return _CLIENT.get(f"/admin/cola/print?dia={self.manana.date().isoformat()}&medico={medico}",
                           headers=headers)

    def test_la_hoja_tiene_las_columnas_las_franjas_y_celdas_vacias(self):
        sid = db_booking.create_booking_request(_DNI, "Ana", "Paz", "1980-01-01", "ana@example.com",
                                                "cardiologia", "centro-a")
        db_booking.approve(sid, self.manana, hsi_result={"estado": "ok", "patient_id": "4242"},
                           medico_asignado_id="med-1", medico_asignado=_MEDICOS[0]["nombre"],
                           resuelta_por="admin")
        self._turno(_AJENO, "med-1", cuando=self.manana.replace(hour=19, minute=40))
        self._turno("30555666", "med-1", cuando=self.manana.replace(hour=21, minute=0))
        r = self._hoja(_cookie("mesa", "admisionista"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        html = r.text
        for columna in ("Hora", "ID Paciente", "Documento", "Nombres", "Apellidos", "Cobertura médica",
                        "Nro. de credencial", "Teléfono", "Estado del turno"):
            self.assertIn(f"<th>{columna}</th>", html)
        self.assertIn("María José Fernández", html)
        bloques = [html.index(t) for t in ("Turnos programados (17:00 a 19:20)",
                                           "Turnos programados (19:20 a 20:00)",
                                           "Turnos fuera de la agenda")]
        self.assertEqual(bloques, sorted(bloques))
        self.assertIn('<td class="nowrap">4242</td>', html)
        self.assertIn(f"DNI {_DNI}", html)
        self.assertIn("<td>Asignado</td>", html)
        # Coverage, member number and phone are unknown: empty cells, never a dash or a zero.
        self.assertIn('<td></td>\n  <td class="nowrap"></td>\n  <td class="nowrap"></td>', html)
        self.assertNotIn("—", html.split("<tbody>", 1)[1])
        self.assertIn("@media print", html)
        self.assertNotIn("adm5-side", html)
        self.assertEqual(_acciones(_DNI), [("mesa", "agenda_impresa", True)])
        self.assertEqual(_acciones(_AJENO), [("mesa", "agenda_impresa", True)])

    def test_el_medico_imprime_su_dia_y_el_de_otro_queda_negado(self):
        self._turno(_DNI, "med-1", cuando=self.manana)
        self._turno(_AJENO, "med-2", cuando=self.manana)
        h = _cookie("dra", "medico")
        r = self._hoja(h, medico="med-1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Turnos programados (17:00 a 19:20)", r.text)
        self.assertNotIn(f"DNI {_AJENO}", r.text)
        r = self._hoja(h, medico="med-2")
        self.assertEqual(r.status_code, 404)
        self.assertIn("No encontramos a ese médico.", r.text)
        self.assertNotIn(f"DNI {_AJENO}", r.text)
        self.assertEqual(_acciones(_DNI), [("dra", "agenda_impresa", True)])
        self.assertEqual(_acciones(_AJENO), [])
        negadas = [(f["actor"], f["action"], f["target_id"], f["allowed"])
                   for f in db_clinical.list_clinical_access("")]
        self.assertEqual(negadas, [("dra", "agenda_impresa", "medico:med-2", False)])

    def test_la_mesa_sin_medico_y_un_dia_invalido(self):
        h = _cookie("mesa", "admisionista")
        r = _CLIENT.get("/admin/cola/print", headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Elegí un médico para imprimir su agenda.", r.text)
        self.assertEqual(_CLIENT.get("/admin/cola/print?dia=31-12&medico=med-1", headers=h).status_code, 400)
        r = self._hoja(h)
        self.assertEqual(r.status_code, 200)
        self.assertIn("No hay turnos para este día.", r.text)


if __name__ == "__main__":
    unittest.main()
