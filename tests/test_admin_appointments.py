"""test_admin_appointments.py — la mesa saca un turno por el paciente (2026-07-24).

Correr: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_appointments.py"
(OJO: `python3 -m unittest test.test_x` NO resuelve — el paquete `test` de la
stdlib tapa este directorio; ver CONTRIBUTING.md.)

Base dedicada `sana_turnos_test_turnos` + webdata fixture propio, mismo patrón
que test_admin_patients.py / test_doctor_photos.py.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")
_env.process_set(TURNOS_SKIP_HSI="1")      # sin handoff clínico en pruebas
# El alta de mostrador termina en _persistir_aprobacion, que le manda el mail
# de confirmación al paciente: sin esto la suite golpeaba Mailgun de verdad
# (se vio "sent to sofia@example.com -> HTTP 200" corriendo los tests).
_env.process_set(MAIL_ENABLED="0")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")


from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.auth import admin_auth        # noqa: E402
from app.patient_web import catalog          # noqa: E402
from app.admin_panel import admin_appointments, admin_appointments_router  # noqa: E402
from app.core import tz  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(admin_appointments_router.router)
from app.core import deps as _early_deps  # noqa: E402
_early_deps.install_early_response(_app)  # the app's answer of a dependency
from app.core import api_body as _api_body  # noqa: E402
_api_body.install(_app)  # the family's 400 to a body that does not match its model, as in the app
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_turnos", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


_SERVICIOS = {"servicios": [
    {"id": "cardio-hosp", "nombre": "Cardiología", "centro": "hospital",
     "cluster": "cardiologia", "categoria": "Consultorios", "modo": "solicitud",
     "agenda": {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
    {"id": "guardia", "nombre": "Guardia", "centro": "hospital",
     "cluster": "clinica", "categoria": "Guardia", "modo": "walkin"},
    # Otro cluster con agenda propia: es lo que un médico cardiólogo NO puede
    # cargar aunque el servicio dé turnos igual que el suyo.
    {"id": "derma-hosp", "nombre": "Dermatología", "centro": "hospital",
     "cluster": "dermatologia", "categoria": "Consultorios", "modo": "solicitud",
     "agenda": {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
    # Laboratorio: da turnos pero NUNCA asigna médico, así que no cae en la
    # agenda de nadie (booking_requests._is_lab, decidido por texto).
    {"id": "lab-hosp", "nombre": "Laboratorio de análisis clínicos", "centro": "hospital",
     "cluster": "laboratorio", "categoria": "Estudios", "modo": "solicitud",
     "agenda": {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
]}
_CENTROS = {"centros": [{"slug": "hospital", "name": "Hospital", "address": "Av. 1"}]}
# Dos cardiólogos en el mismo centro: uno con cupo de sobreturnos en su franja
# y otro sin nada configurado — el sobreturno se habilita por agenda, no por
# ser médico.
_FRANJA = {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}
_MEDICOS = {"medicos": [
    {"id": "med-sobre", "nombre": "Ana Sobre", "especialidad": "Cardióloga",
     "cluster": "cardiologia", "centro": "hospital",
     "centros": [{"centro": "hospital",
                  "franjas": [dict(_FRANJA, cupos={"sobreturnos": 2})]}]},
    {"id": "med-sin", "nombre": "Beto Sin", "especialidad": "Cardiólogo",
     "cluster": "cardiologia", "centro": "hospital",
     "centros": [{"centro": "hospital", "franjas": [dict(_FRANJA)]}]},
    {"id": "med-percent", "nombre": "Pia Percent", "especialidad": "Cardióloga",
     "cluster": "cardiologia", "centro": "hospital",
     "centros": [{"centro": "hospital", "franjas": [{
         "dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "10:00", "cada_min": 20,
         "cupos": {"overbooking_percent": 50},
     }]}]},
]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    for name, doc in (("servicios.json", _SERVICIOS), ("centros-salud.json", _CENTROS),
                      ("medicos.json", _MEDICOS)):
        with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f)


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


def _un_dia_habil():
    """Un lunes futuro: la agenda del fixture atiende de lunes a viernes."""
    d = datetime.now(tz.tzinfo()).date() + timedelta(days=1)
    while d.isoweekday() > 5:
        d += timedelta(days=1)
    return d.isoformat()


class AdminTurnosTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, catalog, slot_holds RESTART IDENTITY CASCADE")
        self._tmp = tempfile.mkdtemp(prefix="admin-turnos-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        # la caché de ocupados vive 3 s: entre tests del mismo run arrastraría
        # los turnos del test anterior y haría chocar al siguiente
        from app.modules.turnos import slot_holds
        slot_holds._TAKEN_CACHE["ts"] = 0
        db_accounts.create_user("30111222", "Sofía", "Herrera", "1953-08-11", "F",
                         "2254550001", "secreto123", email="sofia@example.com")

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _crear(self, body, auth=True):
        h = _CLIENT.request("POST", "/api/admin/appointments", json=body, headers=_admin_headers() if auth else {})
        return h

    def _slots(self, servicio_id="cardio-hosp", medico=None):
        from urllib.parse import quote
        path = "/api/admin/appointment-slots?servicio_id=" + quote(servicio_id)
        if medico:
            path += "&medico=" + quote(medico)
        h = _CLIENT.request("GET", path, headers=_admin_headers())
        return h

    # ---- happy path ----------------------------------------------------

    def test_nace_aprobado_y_con_la_marca_de_la_mesa(self):
        dia = _un_dia_habil()
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        row = db_booking.get_booking_request(h.json()["id"])
        # el turno de mostrador no pasa por la cola: ya está resuelto
        self.assertEqual(row["estado"], "aprobada")
        self.assertEqual(row["resuelta_por"], "mesa:admin")
        self.assertEqual(row["servicio_id"], "cardio-hosp")
        # los datos del paciente salen de su ficha, nunca del body
        self.assertEqual(row["nombre"], "Sofía")
        self.assertEqual(row["apellido"], "Herrera")
        self.assertEqual(row["contacto"], "2254550001")

    def test_la_hora_se_guarda_en_hora_de_argentina(self):
        # el proceso puede correr en cualquier TZ (en dev se vio New_York):
        # el turno tiene que quedar a las 09:20 de Argentina igual.
        dia = _un_dia_habil()
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20"})
        row = db_booking.get_booking_request(h.json()["id"])
        from zoneinfo import ZoneInfo
        art = row["turno_dt"].astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
        self.assertEqual(art.strftime("%Y-%m-%d %H:%M"), f"{dia} 09:20")

    # ---- validaciones ---------------------------------------------------

    def test_paciente_inexistente_404(self):
        h = self._crear({"dni": "99999999", "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 404)
        self.assertEqual(h.json()["error"], "paciente_no_encontrado")

    def test_paciente_archivado_no_saca_turno(self):
        db_accounts.user_admin_update("30111222", {"activo": False})
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 400)

    def test_walkin_nunca_da_turno_tampoco_en_la_mesa(self):
        # invariante duro del proyecto: un walk-in NO ofrece turnos en NINGUNA
        # superficie (responsable del producto, 2×) — la mesa no es la excepción.
        h = self._crear({"dni": "30111222", "servicio_id": "guardia",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 400)
        self.assertIn("orden de llegada", h.json()["error"])

    def test_servicio_inexistente_400(self):
        h = self._crear({"dni": "30111222", "servicio_id": "no-existe",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 400)

    def test_dia_u_hora_invalidos_400(self):
        for body in ({"dia": "28-07-2026", "hora": "09:20"},
                     {"dia": _un_dia_habil(), "hora": "9:20"},
                     {"dia": "", "hora": ""}):
            body.update({"dni": "30111222", "servicio_id": "cardio-hosp"})
            h = self._crear(body)
            self.assertEqual(h.status_code, 400, body)

    def test_dni_invalido_400(self):
        h = self._crear({"dni": "123", "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 400)

    def test_sin_sesion_no_crea(self):
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"}, auth=False)
        self.assertIn(h.status_code, (401, 403))
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM booking_requests")
            self.assertEqual(cur.fetchone()["n"], 0)

    # ---- horarios que ofrece la mesa -----------------------------------

    def test_los_libres_son_los_mismos_que_ve_el_paciente(self):
        h = self._slots()
        self.assertEqual(h.status_code, 200)
        libres = h.json()["libres"]
        self.assertTrue(libres)
        from app.patient_web import site_availability
        del_sitio = []
        for m in (site_availability._appointments_for_service(catalog.services()[0]).get("medicos") or []):
            del_sitio += [(t["dia"], t["hora"]) for t in m["turnos"]]
        self.assertEqual([(t["dia"], t["hora"]) for t in libres], sorted(del_sitio))

    def test_un_horario_ocupado_sale_de_los_libres(self):
        dia = _un_dia_habil()
        self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                     "dia": dia, "hora": "09:20"})
        from app.modules.turnos import slot_holds
        slot_holds._TAKEN_CACHE["ts"] = 0
        libres = self._slots().json()["libres"]
        self.assertNotIn((dia, "09:20"), [(t["dia"], t["hora"]) for t in libres])

    def test_sin_medico_no_se_ofrecen_sobreturnos(self):
        # el cupo vive en la franja de una persona: sin médico no hay dónde
        # mirarlo, y adivinarlo sería ofrecer un sobreturno que no existe
        dia = _un_dia_habil()
        self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                     "dia": dia, "hora": "09:20", "medico": "Ana Sobre"})
        self.assertEqual(self._slots().json()["sobreturnos"], [])

    def test_el_sobreturno_se_ofrece_donde_la_agenda_lo_habilita(self):
        dia = _un_dia_habil()
        self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                     "dia": dia, "hora": "09:20", "medico": "Ana Sobre"})
        sobres = self._slots(medico="Ana Sobre").json()["sobreturnos"]
        self.assertEqual([(s["dia"], s["hora"], s["restantes"]) for s in sobres],
                         [(dia, "09:20", 2)])

    def test_un_medico_sin_cupo_no_ofrece_sobreturnos(self):
        dia = _un_dia_habil()
        self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                     "dia": dia, "hora": "09:20", "medico": "Beto Sin"})
        self.assertEqual(self._slots(medico="Beto Sin").json()["sobreturnos"], [])

    def test_slots_de_un_walkin_vienen_vacios(self):
        h = self._slots("guardia")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json(), {"libres": [], "sobreturnos": []})

    # ---- sobreturno ------------------------------------------------------

    def _con_turno_previo(self, medico="Ana Sobre", hora="09:20"):
        dia = _un_dia_habil()
        primero = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                               "dia": dia, "hora": hora, "medico": medico})
        self.assertEqual(primero.status_code, 200, primero.json())
        db_accounts.create_user("30999888", "Otro", "Paciente", "1980-01-01", "M",
                         "2254550002", "secreto123")
        return dia

    def test_el_sobreturno_se_guarda_marcado_y_encima_del_otro(self):
        dia = self._con_turno_previo()
        h = self._crear({"dni": "30999888", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20", "medico": "Ana Sobre",
                         "sobreturno": True})
        self.assertEqual(h.status_code, 200, h.json())
        row = db_booking.get_booking_request(h.json()["id"])
        self.assertTrue(row["sobreturno"])
        self.assertEqual(row["estado"], "aprobada")

    def test_sobreturno_en_horario_libre_no_va(self):
        # un sobreturno se agrega ENCIMA de un turno: si el horario está
        # libre, lo que corresponde es el turno normal
        dia = _un_dia_habil()
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20", "medico": "Ana Sobre",
                         "sobreturno": True})
        self.assertEqual(h.status_code, 409)
        self.assertEqual(h.json()["error"], "sobreturno_requiere_turno_previo")

    def test_sobreturno_sin_medico_no_va(self):
        dia = self._con_turno_previo()
        h = self._crear({"dni": "30999888", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20", "sobreturno": True})
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"], "sobreturno_requiere_medico")

    def test_sobreturno_sin_cupo_en_la_agenda_no_va(self):
        dia = self._con_turno_previo(medico="Beto Sin")
        h = self._crear({"dni": "30999888", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20", "medico": "Beto Sin",
                         "sobreturno": True})
        self.assertEqual(h.status_code, 409)
        self.assertEqual(h.json()["error"], "sobreturno_no_configurado")

    def test_el_cupo_de_sobreturnos_se_agota(self):
        dia = self._con_turno_previo()
        db_accounts.create_user("30777666", "Tercero", "Paciente", "1980-01-01", "M",
                         "2254550003", "secreto123")
        db_accounts.create_user("30555444", "Cuarto", "Paciente", "1980-01-01", "M",
                         "2254550004", "secreto123")
        for dni in ("30999888", "30777666"):
            h = self._crear({"dni": dni, "servicio_id": "cardio-hosp", "dia": dia,
                             "hora": "09:20", "medico": "Ana Sobre", "sobreturno": True})
            self.assertEqual(h.status_code, 200, h.json())
        # dos sobreturnos sobre el turno original: el tercero ya no entra
        h = self._crear({"dni": "30555444", "servicio_id": "cardio-hosp", "dia": dia,
                         "hora": "09:20", "medico": "Ana Sobre", "sobreturno": True})
        self.assertEqual(h.status_code, 409)
        self.assertEqual(h.json()["error"], "sobreturno_cupo_agotado")
        self.assertEqual(self._slots(medico="Ana Sobre").json()["sobreturnos"], [])

    def test_porcentaje_agota_y_oculta_todo_el_bloque_en_mesa(self):
        dia = _un_dia_habil()
        db_accounts.create_user("30777666", "Tercero", "Paciente", "1980-01-01", "M",
                       "2254550003", "secreto123")
        db_accounts.create_user("30555444", "Cuarto", "Paciente", "1980-01-01", "M",
                       "2254550004", "secreto123")
        for dni, hora in (("30111222", "09:00"), ("30999888", "09:20")):
            if dni == "30999888":
                db_accounts.create_user(dni, "Otro", "Paciente", "1980-01-01", "M",
                               "2254550002", "secreto123")
            h = self._crear({"dni": dni, "servicio_id": "cardio-hosp", "dia": dia,
                             "hora": hora, "medico": "Pia Percent"})
            self.assertEqual(h.status_code, 200, h.json())
        first = self._crear({"dni": "30777666", "servicio_id": "cardio-hosp", "dia": dia,
                             "hora": "09:00", "medico": "Pia Percent", "sobreturno": True})
        self.assertEqual(first.status_code, 200, first.json())
        self.assertEqual(self._slots(medico="Pia Percent").json()["sobreturnos"], [])
        exhausted = self._crear({"dni": "30555444", "servicio_id": "cardio-hosp", "dia": dia,
                                 "hora": "09:20", "medico": "Pia Percent", "sobreturno": True})
        self.assertEqual(exhausted.status_code, 409, exhausted.json())
        self.assertEqual(exhausted.json()["error"], "sobreturno_cupo_agotado")

    def test_sobreturno_invalido_400(self):
        dia = _un_dia_habil()
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20", "sobreturno": "si"})
        # The strict body boundary (TASK-76 item F) answers it before the domain's own «sobreturno_invalido».
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"], "JSON inválido: campo sobreturno: se esperaba true o false")

    def test_el_horario_ya_tomado_da_409(self):
        dia = _un_dia_habil()
        primero = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                               "dia": dia, "hora": "09:20"})
        self.assertEqual(primero.status_code, 200, primero.json())
        db_accounts.create_user("30999888", "Otro", "Paciente", "1980-01-01", "M",
                         "2254550002", "secreto123")
        from app.modules.turnos import slot_holds
        slot_holds._TAKEN_CACHE["ts"] = 0   # la caché de 3 s no puede tapar el choque
        segundo = self._crear({"dni": "30999888", "servicio_id": "cardio-hosp",
                               "dia": dia, "hora": "09:20"})
        self.assertEqual(segundo.status_code, 409, segundo.json())
        self.assertEqual(segundo.json()["error"], "turno_tomado")

    # ---- respeta el hold VIGENTE de un paciente (data-integrity guards, ----
    # 2026-08-05, tier 14): antes de esto la mesa sólo miraba una caché de 3 s
    # (`slot_holds.taken()`), así que un paciente a mitad del wizard, con un
    # hold propio sobre el slot, podía perderlo contra el mostrador sin que
    # nadie lo viera venir.

    def test_la_mesa_choca_contra_el_hold_vigente_de_un_paciente(self):
        dia = _un_dia_habil()
        # simula al paciente EN EL WIZARD: un hold propio, de OTRO token, aún
        # sin convertirse en solicitud/turno.
        self.assertTrue(db_slots.hold_acquire("cardio-hosp", dia, "09:20", "wizard-tok-paciente"))
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20"})
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "turno_tomado")
        # y sobre todo: no se creó nada
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM booking_requests")
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_sin_hold_vigente_la_mesa_reserva_normal(self):
        # el otro lado del mismo chequeo: sin nadie sosteniendo el slot, el
        # flujo de mostrador no cambia en nada.
        dia = _un_dia_habil()
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())

    def test_el_hold_de_la_mesa_no_queda_bloqueando_el_slot(self):
        # el hold propio que la mesa toma para chequear se libera al terminar
        # la request — no debe quedar una fila fantasma en slot_holds.
        dia = _un_dia_habil()
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM slot_holds WHERE request_id IS NULL")
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_hiccup_de_slot_holds_falla_cerrado_no_crea_turno(self):
        # fail CLOSED (misma convención que booking_requests._create): un
        # error inesperado en el chequeo de holds nunca debe traducirse en
        # "seguí sin protección" — se trata como "no lo pude tomar".
        from app.modules.turnos import slot_holds
        dia = _un_dia_habil()
        orig = slot_holds.try_take
        slot_holds.try_take = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                             "dia": dia, "hora": "09:20"})
        finally:
            slot_holds.try_take = orig
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "turno_tomado")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM booking_requests")
            self.assertEqual(cur.fetchone()["n"], 0)

    # ---- turno anotado a mano, sin ficha (the product owner, 2026-07-27) -------------
    #
    # Es lo que hoy hacen en HCI mientras atienden gente: "le pongo una letra
    # y confirmo, y ya está en el listado". Un turno anotado a medias sirve
    # más que un turno que no se cargó porque faltaba dar de alta la ficha.

    def test_una_nota_alcanza_para_ocupar_el_turno(self):
        dia = _un_dia_habil()
        h = self._crear({"nota": "Señora de Lazcano", "servicio_id": "cardio-hosp",
                         "dia": dia, "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertTrue(h.json()["sin_ficha"])
        row = db_booking.get_booking_request(h.json()["id"])
        self.assertEqual(row["estado"], "aprobada")
        self.assertEqual(row["nombre"], "Señora de Lazcano")
        self.assertIsNone(row["dni"])
        self.assertIsNone(row["solicitante_dni"])

    def test_el_turno_anotado_ocupa_el_horario_como_cualquier_otro(self):
        dia = _un_dia_habil()
        self.assertEqual(self._crear({"nota": "Prueba", "servicio_id": "cardio-hosp",
                                      "dia": dia, "hora": "09:20"}).status_code, 200)
        from app.modules.turnos import slot_holds
        slot_holds._TAKEN_CACHE["ts"] = 0
        segundo = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                               "dia": dia, "hora": "09:20"})
        self.assertEqual(segundo.status_code, 409, segundo.json())

    def test_sin_dni_al_HSI_no_se_encola_nada(self):
        # Sin paciente no hay handoff posible: 'skip' (no se intentó) en vez de
        # 'error', que invitaría a reintentar para siempre.
        h = self._crear({"nota": "Prueba", "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(db_booking.get_booking_request(h.json()["id"])["hsi_estado"], "skip")

    # La contraparte técnica, 2026-10-19: después de crear el turno la mesa tiene que ver cómo
    # quedó en HSI. La respuesta lo dice; el modal lo muestra.
    def test_la_respuesta_dice_que_HSI_no_esta_conectado(self):
        h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertFalse(h.json()["hsi"]["conectado"])
        self.assertEqual(h.json()["hsi"]["estado"], "skip")

    def test_sin_conexion_ofrece_la_extension_donde_la_extension_corre(self):
        # La extensión de la mesa declara en qué sitios corre: su manifest y
        # los que el operador habilita (TURNOS_OPCIONALES, salud desde 0.4.0);
        # el panel la ofrece en esos y en ningún otro.
        from app.core import site_identity
        with open(os.path.join(ROOT, "extensions", "hsi-humanizador", "store", "item.json")) as f:
            tienda = json.load(f)["store_url"]
        for origen, esperado in (("https://pinamar.humanrounds.org", tienda),
                                 ("https://demo.humanrounds.org", tienda),
                                 ("https://salud.pinamar.gob.ar", tienda),
                                 ("https://otra-ciudad.example", None)):
            with mock.patch.object(site_identity, "base_url", return_value=origen):
                h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                                 "dia": _un_dia_habil(), "hora": "09:20"})
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(h.json()["hsi"]["extension"], esperado, origen)
            # el mismo horario sirve para la vuelta siguiente
            with db._connect() as c, c.cursor() as cur:
                _dbtest.delete_booking_requests(cur)
            from app.modules.turnos import slot_holds
            slot_holds._TAKEN_CACHE["ts"] = 0

    def test_sin_conexion_dice_donde_se_instala_la_extension(self):
        # La contraparte técnica, 2026-10-27: «nunca entendí cómo… llegábamos a esa pantalla».
        # Junto a «Cargar en HSI» va dónde instalarla: tienda (publicada) y la
        # guía en PDF; en un sitio ajeno, nada.
        from app.core import site_identity, hsi_extension
        for origen in ("https://demo.humanrounds.org", "https://otra-ciudad.example"):
            with mock.patch.object(site_identity, "base_url", return_value=origen):
                esperado = hsi_extension.install()
                h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                                 "dia": _un_dia_habil(), "hora": "09:20"})
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(h.json()["hsi"]["instalar"], esperado, origen)
            with db._connect() as c, c.cursor() as cur:
                _dbtest.delete_booking_requests(cur)
            from app.modules.turnos import slot_holds
            slot_holds._TAKEN_CACHE["ts"] = 0
        self.assertEqual(esperado, None)

    def test_la_guia_y_el_zip_se_descargan_desde_el_panel(self):
        from app.core import hsi_extension
        for nombre, tipo in (("guide.pdf", "application/pdf"), ("extension.zip", "application/zip")):
            with open(hsi_extension.file_path(nombre), "rb") as f:
                bytes_ = f.read()
            for cabeceras in (_admin_headers(), self._medico()):
                h = _CLIENT.request("GET", "/api/admin/hsi-extension/" + nombre, headers=cabeceras)
                self.assertEqual(h.status_code, 200, nombre)
                self.assertEqual(h.headers["content-type"], tipo)
                self.assertIn("attachment", h.headers["content-disposition"])
                self.assertEqual(h.content, bytes_)
            # sin sesión, nada
            self.assertEqual(_CLIENT.request("GET", "/api/admin/hsi-extension/" + nombre).status_code, 401)
        # sólo esos dos archivos
        for otro in ("hsi-humanizador.sha256", "manifest.json", "x.pdf"):
            h = _CLIENT.request("GET", "/api/admin/hsi-extension/" + otro, headers=_admin_headers())
            self.assertEqual(h.status_code, 404, otro)

    def test_con_HSI_conectado_la_respuesta_trae_el_estado_del_envio(self):
        from app.modules.turnos import booking_requests, hsi_journal
        with mock.patch.object(booking_requests, "_emr_sync_enabled", return_value=True), \
                mock.patch.object(hsi_journal, "enqueue_appointment", return_value=None):
            h = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                             "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        # se intentó solo, sin que nadie apriete nada: queda pendiente hasta
        # que HSI lo confirme, y el turno local ya está guardado
        self.assertTrue(h.json()["hsi"]["conectado"])
        self.assertEqual(h.json()["hsi"]["estado"], "pendiente")
        # con HSI conectado no hace falta la extensión
        self.assertIsNone(h.json()["hsi"]["extension"])
        self.assertIsNone(h.json()["hsi"]["instalar"])
        self.assertEqual(db_booking.get_booking_request(h.json()["id"])["estado"], "aprobada")

    def test_la_nota_se_recorta_y_se_normaliza(self):
        h = self._crear({"nota": "  Juan   Pérez  " + "x" * 200,
                         "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        nombre = db_booking.get_booking_request(h.json()["id"])["nombre"]
        self.assertTrue(nombre.startswith("Juan Pérez "))
        self.assertLessEqual(len(nombre), 80)

    def test_el_dni_gana_sobre_la_nota(self):
        # Mandar los dos no es ambiguo: la ficha es más específica que el texto.
        h = self._crear({"dni": "30111222", "nota": "Otra cosa",
                         "servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertFalse(h.json()["sin_ficha"])
        self.assertEqual(db_booking.get_booking_request(h.json()["id"])["dni"], "30111222")

    def test_sin_dni_ni_nota_sigue_siendo_400(self):
        h = self._crear({"servicio_id": "cardio-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 400)

    # ---- alcance por centro (2026-07-27) --------------------------------

    def test_una_mesa_acotada_no_carga_turnos_de_otro_centro(self):
        db_operators.operator_create("mesa-otro", "clave1234", "admisionista", centros=["otro-centro"])
        h = _CLIENT.request("POST", "/api/admin/appointments", json={"dni": "30111222", "servicio_id": "cardio-hosp",
                              "dia": _un_dia_habil(), "hora": "09:20"}, headers=_cookie("mesa-otro", "admisionista"))
        # Lo de otro centro no existe para esa cuenta: 404, no 403 (2026-10-23).
        self.assertEqual(h.status_code, 404, h.json())

    def test_una_mesa_de_su_centro_sí_carga(self):
        db_operators.operator_create("mesa-propia", "clave1234", "admisionista", centros=["hospital"])
        h = _CLIENT.request("POST", "/api/admin/appointments", json={"dni": "30111222", "servicio_id": "cardio-hosp",
                              "dia": _un_dia_habil(), "hora": "09:20"}, headers=_cookie("mesa-propia", "admisionista"))
        self.assertEqual(h.status_code, 200, h.json())

    # ---- el médico da un hueco de SU día (responsable del producto, 2026-08-05) --------------
    # "clicking an empty slot creates a turno": desde la consola médica, donde
    # #cola muestra una sola agenda. El permiso es angosto a propósito — la
    # agenda es la propia y ninguna otra, y el servidor lo impone sin confiar
    # en lo que mande el cliente.

    def _medico(self, usuario="dra-ana", medico_id="med-sobre"):
        # setUp trunca solicitudes/pacientes/catálogo, no `operators`: el mismo
        # usuario se reusa entre tests, así que se rearma en vez de chocar.
        with db._connect() as c, c.cursor() as cur:
            cur.execute("DELETE FROM operators WHERE username=%s", (usuario,))
        db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)
        return _cookie(usuario, "medico")

    def _crear_como_medico(self, body, usuario="dra-ana", medico_id="med-sobre"):
        h = _CLIENT.request("POST", "/api/admin/appointments", json=body, headers=self._medico(usuario, medico_id))
        return h

    def test_el_medico_carga_el_turno_en_su_propia_agenda(self):
        h = self._crear_como_medico({"dni": "30111222", "servicio_id": "cardio-hosp",
                                     "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT assigned_doctor, resolved_by FROM booking_requests")
            fila = cur.fetchone()
        self.assertEqual(fila["assigned_doctor"], "Ana Sobre")
        # la auditoría distingue el turno que dio el médico del de la mesa
        self.assertEqual(fila["resolved_by"], "medico:dra-ana")

    def test_el_medico_no_puede_cargarle_un_turno_a_un_colega(self):
        h = self._crear_como_medico({"dni": "30111222", "servicio_id": "cardio-hosp",
                                     "dia": _un_dia_habil(), "hora": "09:20",
                                     "medico": "Beto Sin"})
        self.assertEqual(h.status_code, 200, h.json())
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT assigned_doctor FROM booking_requests")
            # el nombre que mandó se ignora: se le fuerza el suyo
            self.assertEqual(cur.fetchone()["assigned_doctor"], "Ana Sobre")

    def test_el_medico_no_carga_un_servicio_que_no_atiende(self):
        h = self._crear_como_medico({"dni": "30111222", "servicio_id": "derma-hosp",
                                     "dia": _un_dia_habil(), "hora": "09:20"})
        # lo ajeno no existe para quien no lo ve: 404, no 403
        self.assertEqual(h.status_code, 404, h.json())
        self.assertIn("tu agenda", h.json()["error"])

    def test_el_medico_no_carga_un_turno_de_laboratorio(self):
        # Un laboratorio no asigna médico: el turno no caería en ninguna
        # agenda, y una agenda propia es todo lo que este permiso habilita.
        h = self._crear_como_medico({"dni": "30111222", "servicio_id": "lab-hosp",
                                     "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 403, h.json())

    def test_una_cuenta_medica_sin_ficha_no_carga_nada(self):
        h = self._crear_como_medico({"dni": "30111222", "servicio_id": "cardio-hosp",
                                     "dia": _un_dia_habil(), "hora": "09:20"},
                                    usuario="dra-suelta", medico_id=None)
        self.assertEqual(h.status_code, 400, h.json())
        self.assertEqual(h.json()["error"], "cuenta_sin_medico")

    def test_los_horarios_que_ve_el_medico_son_los_de_su_agenda(self):
        from urllib.parse import quote
        h = _CLIENT.request("GET", "/api/admin/appointment-slots?servicio_id=cardio-hosp"
                        "&medico=" + quote("Beto Sin"), headers=self._medico())
        self.assertEqual(h.status_code, 200, h.json())
        # pidió los de un colega y le vuelven los suyos
        self.assertTrue(h.json()["libres"])
        self.assertEqual({t["medico"] for t in h.json()["libres"]}, {"Ana Sobre"})


class AperturaMismoDiaTestCase(unittest.TestCase):
    """Un servicio por orden de llegada que abre su listado el mismo día
    (the reference-deployment counterpart, reunión 2026-07-24). Es la única excepción al invariante
    "walk-in nunca ofrece turnos", y es angosta: sólo HOY.

    NO hereda de AdminTurnosTestCase a propósito (heredaría también sus
    tests, que corren contra el fixture sin el flag y ahí sí un walk-in no
    ofrece nada): sólo se reusan el armado y los dos helpers."""

    tearDown = AdminTurnosTestCase.tearDown
    _crear = AdminTurnosTestCase._crear
    _slots = AdminTurnosTestCase._slots

    def setUp(self):
        AdminTurnosTestCase.setUp(self)
        # Reloj fijo: la agenda de abajo abre 00:00–23:59, así que pasada la
        # última hora el horizonte de un solo día se queda sin horarios y estas
        # pruebas fallaban según la hora a la que se corriera la suite (visto
        # 2026-07-27, 23:35). Mismo truco y misma razón que en
        # test_apertura_mismo_dia.py, donde además vive la prueba del final del
        # día. `_agenda_slots` ya recibe `now`.
        import datetime as _dt
        from app.patient_web import site_availability, site_base
        _real = site_availability._agenda_slots

        def _fijo(agenda, now=None, solo_hoy=False, **policy):
            # fecha en ART (no la del sistema/proceso): `_hoy()`, más abajo en
            # esta misma clase, ya calculaba "hoy" en ART — este fallback
            # calculaba la fecha con date.today() (naive/local), así que en
            # un runner con TZ != ART las dos quedaban desincronizadas cerca
            # de la medianoche ART (visto en CI, Python 3.12 UTC, 2026-07-30).
            ahora = now or _dt.datetime.combine(
                _dt.datetime.now(tz.tzinfo()).date(), _dt.time(9, 0))
            return _real(agenda, ahora, solo_hoy, **policy)

        site_availability._agenda_slots = _fijo
        site_base._CACHE.clear()
        self.addCleanup(site_base._CACHE.clear)
        self.addCleanup(setattr, site_availability, "_agenda_slots", _real)
        # el mismo walk-in de siempre, ahora con apertura el mismo día
        servicios = json.loads(json.dumps(_SERVICIOS))
        for s in servicios["servicios"]:
            if s["id"] == "guardia":
                s["apertura_mismo_dia"] = True
                s["agenda"] = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00",
                               "hasta": "23:59", "cada_min": 60}
        with open(os.path.join(os.environ["TURNOS_WEBDATA_DIR"], "servicios.json"),
                  "w", encoding="utf-8") as f:
            json.dump(servicios, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def _hoy(self):
        return datetime.now(tz.tzinfo()).date().isoformat()

    def test_los_horarios_que_ofrece_son_todos_de_hoy(self):
        h = self._slots(servicio_id="guardia")
        self.assertEqual(h.status_code, 200, h.json())
        dias = {t["dia"] for t in h.json()["libres"]}
        self.assertTrue(h.json()["libres"], "tendría que ofrecer los del día")
        self.assertEqual(dias, {self._hoy()})

    def test_un_turno_de_hoy_se_puede_cargar(self):
        libres = self._slots(servicio_id="guardia").json()["libres"]
        self.assertTrue(libres)
        h = self._crear({"dni": "30111222", "servicio_id": "guardia",
                         "dia": libres[0]["dia"], "hora": libres[0]["hora"]})
        self.assertEqual(h.status_code, 200, h.json())

    def test_la_mesa_choca_contra_un_hold_vigente_en_apertura_mismo_dia(self):
        # data-integrity guards (2026-08-05, tier 14): antes de extender
        # `_service_holdable` un walk-in con apertura_mismo_dia NUNCA era
        # holdable, así que un paciente no podía sostener un hold acá y este
        # choque era imposible de ver. Ahora sí — mismo chequeo que en
        # cardio-hosp (`solicitud`), sobre el otro tipo de servicio que la
        # mesa puede cargar.
        from app.modules.turnos import slot_holds
        self.assertTrue(slot_holds._service_holdable(catalog.service_by_id("guardia")))
        libres = self._slots(servicio_id="guardia").json()["libres"]
        self.assertTrue(libres)
        dia, hora = libres[0]["dia"], libres[0]["hora"]
        self.assertTrue(db_slots.hold_acquire("guardia", dia, hora, "wizard-tok-paciente"))
        h = self._crear({"dni": "30111222", "servicio_id": "guardia",
                         "dia": dia, "hora": hora})
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "turno_tomado")

    def test_uno_de_mañana_no(self):
        from datetime import date, timedelta as td
        manana = (date.fromisoformat(self._hoy()) + td(days=1)).isoformat()
        h = self._crear({"dni": "30111222", "servicio_id": "guardia",
                         "dia": manana, "hora": "10:00"})
        self.assertEqual(h.status_code, 400)
        self.assertIn("día de hoy", h.json()["error"])

    def test_el_catalogo_no_deja_marcarlo_en_un_servicio_con_turno(self):
        doc, err = catalog._validate_service(
            {"id": "x", "nombre": "X", "centro": "hospital", "modo": "solicitud",
             "apertura_mismo_dia": True}, True)
        self.assertIsNone(doc)
        self.assertIn("orden de llegada", err)

    def test_un_walkin_sin_el_flag_sigue_sin_poder_tener_agenda(self):
        doc, err = catalog._validate_service(
            {"id": "x", "nombre": "X", "centro": "hospital", "modo": "walkin",
             "agenda": {"dias": [1], "desde": "09:00", "hasta": "12:00", "cada_min": 20}}, True)
        self.assertIsNone(doc)
        self.assertIn("walk-in nunca ofrece turnos", err)


class TestDeskSlotsNameTheDoctor(unittest.TestCase):
    """la contraparte técnica (2026-10-19): «les ahorraría tiempo si le aparecieran el nombre
    del profesional además del caps y los horarios». A service without its
    own schedule is offered from its doctors' agendas; the patient grid
    merges them anonymously, the desk sees whose each time is. Borrows the
    fixture of AdminTurnosTestCase without inheriting its tests."""

    tearDown = AdminTurnosTestCase.tearDown
    _slots = AdminTurnosTestCase._slots

    def setUp(self):
        AdminTurnosTestCase.setUp(self)
        doc = json.loads(json.dumps(_SERVICIOS))
        doc["servicios"].append({"id": "cardio-medicos", "nombre": "Cardiología por médico",
                                 "centro": "hospital", "cluster": "cardiologia",
                                 "categoria": "Consultorios", "modo": "solicitud"})
        with open(os.path.join(self._tmp, "webdata", "servicios.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def test_every_free_time_carries_its_doctor(self):
        h = self._slots("cardio-medicos")
        self.assertEqual(h.status_code, 200, h.json())
        libres = h.json()["libres"]
        self.assertTrue(libres)
        self.assertEqual({t["medico"] for t in libres}, {"Ana Sobre", "Beto Sin", "Pia Percent"})

    def test_a_service_with_its_own_schedule_keeps_the_merged_grid(self):
        libres = self._slots("cardio-hosp").json()["libres"]
        self.assertTrue(libres)
        self.assertEqual({t["medico"] for t in libres}, {""})


if __name__ == "__main__":
    unittest.main()
