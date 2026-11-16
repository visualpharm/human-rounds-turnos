"""test_doctor_appointments.py — login de médico: matriz de permisos + matcheo
médico↔solicitud + feed ICS propio.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_doctor_appointments -v

Sigue el patrón de test_admin_content.py (webdata propio vía
TURNOS_WEBDATA_DIR, aislado del real) + test_admin_users.py (base de test
dedicada, TestClient contra el router nativo). Base propia `sana_turnos_test_medico_turnos`
para correr en paralelo con las otras suites sin pisarlas.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

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
from app.auth import admin_auth       # noqa: E402
from app.admin_panel import admin_users   # noqa: E402
from app.admin_panel import admin_content  # noqa: E402
from app.admin_panel import reports          # noqa: E402
from app.modules.turnos import booking_requests      # noqa: E402
from app.modules.turnos import doctor_appointments    # noqa: E402
from app.modules.turnos import calendar_feed    # noqa: E402
from app.patient_web import catalog         # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

_CLIENT = TestClient(app_module.app)

from app.auth import turnstile          # noqa: E402
from app.core import config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

_CLIENT = TestClient(app_module.app)
turnstile.ENV_PATH = ""   # no leer el .env real (mismo truco que los otros tests)
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_medico_turnos", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
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
            cur.execute("TRUNCATE catalog")
            cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")


# ---- fixtures del catálogo (aisladas del webdata real) ----------------------

_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS = {"servicios": [
    {"id": "serv-cardio", "nombre": "Cardiología", "centro": "centro-a", "modo": "solicitud", "cluster": "cardiologia"},
]}
# med-1 lleva centros[]/cluster/matricula (para /api/admin/doctors/stats);
# med-2 se queda sin agenda a propósito (caso "sin horarios" del stats);
# med-3-rol es una fila-rol (Enfermería) — debe quedar afuera del stats.
_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología", "centro": "centro-a",
     "cluster": "cardiologia", "matricula": {"numero": "12345"},
     "centros": [{"centro": "centro-a",
                  "franjas": [{"dias": [1, 3], "desde": "09:00", "hasta": "11:00", "cada_min": 30}],
                  "excepciones": []}]},
    {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Pediatría", "centro": "centro-a"},
    {"id": "med-3-rol", "nombre": "Enfermería", "especialidad": "", "centro": "centro-a", "rol": True,
     "centros": [{"centro": "centro-a",
                  "franjas": [{"dias": [2], "desde": "08:00", "hasta": "12:00", "cada_min": 15}],
                  "excepciones": []}]},
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


def _futuro(horas=48, tz=timezone.utc):
    return datetime.now(tz) + timedelta(hours=horas)


class MedicoTurnosTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        self._tmp = tempfile.mkdtemp(prefix="medico-turnos-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(catalog._JSON_CACHE.clear)
        self.addCleanup(catalog.invalidate)
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_tables()

    # ---- helpers ------------------------------------------------------

    def _crear_operador_medico(self, usuario, medico_id):
        return db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)

    def _seed_solicitud(self, dni, nombre, apellido, medico_asignado=None, medico_asignado_id=None,
                        turno_dt=None, servicio_id="serv-cardio", centro_slug="centro-a", ai_json=None):
        sid = db_booking.create_booking_request(dni, nombre, apellido, "1980-01-01", "wa:11",
                                 servicio_id, centro_slug)
        if ai_json is not None:
            db_booking.save_ai(sid, ai_json)
        db_booking.approve(sid, turno_dt or _futuro(), medico_asignado=medico_asignado,
                  medico_asignado_id=medico_asignado_id, resuelta_por="admin")
        return sid

    # ---- alta de cuenta médico exige medico_id válido (server-side) ---

    def test_crear_cuenta_medico_exige_medico_id_del_catalogo(self):
        native = _CLIENT.post(
            "/api/admin/users",
            json={"usuario": "dr-sin-medico", "password": "clave1234", "rol": "medico"},
            headers=_admin_headers(),
        )
        self.assertEqual(native.status_code, 400)

        native = _CLIENT.post(
            "/api/admin/users",
            json={"usuario": "dr-invalido", "password": "clave1234", "rol": "medico",
                  "medico_id": "no-existe"},
            headers=_admin_headers(),
        )
        self.assertEqual(native.status_code, 400)

        native = _CLIENT.post(
            "/api/admin/users",
            json={"usuario": "dr-fernandez", "password": "clave1234", "rol": "medico",
                  "medico_id": "med-1"},
            headers=_admin_headers(),
        )
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json()["item"]["medico_id"], "med-1")
        self.assertEqual(db_operators.operator_doctor_id("dr-fernandez"), "med-1")

    # ---- el médico ve SOLO sus propios turnos --------------------------

    def test_medico_ve_solo_sus_turnos_por_id(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_operador_medico("dr-gomez", "med-2")
        self._seed_solicitud("40111111", "Paciente", "Uno", medico_asignado_id="med-1")
        self._seed_solicitud("40222222", "Paciente", "Dos", medico_asignado_id="med-2")

        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["total"], 1)
        self.assertEqual(h.json()["turnos"][0]["dni"], "40111111")
        self.assertEqual(h.json()["medico"]["id"], "med-1")

    def test_medico_ve_turnos_por_nombre_cuando_falta_el_id(self):
        # filas viejas / sin match automático: sólo `medico_asignado` (texto libre)
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._seed_solicitud("40333333", "Paciente", "Tres", medico_asignado="María José Fernández")
        # tokens sin acentos y orden invertido: sigue matcheando
        self._seed_solicitud("40444444", "Paciente", "Cuatro", medico_asignado="Fernandez, Maria Jose")

        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        dnis = {t["dni"] for t in h.json()["turnos"]}
        self.assertEqual(dnis, {"40333333", "40444444"})

    def test_medico_no_ve_turnos_de_otro_medico(self):
        self._crear_operador_medico("dr-gomez", "med-2")
        self._seed_solicitud("40555555", "Paciente", "Cinco", medico_asignado="María José Fernández")

        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_cookie("dr-gomez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["total"], 0)

    def test_dias_recorta_turnos_lejanos(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._seed_solicitud("40666666", "Cerca", "Uno", medico_asignado_id="med-1", turno_dt=_futuro(24))
        self._seed_solicitud("40777777", "Lejos", "Dos", medico_asignado_id="med-1", turno_dt=_futuro(24 * 60))

        h = _CLIENT.request("GET", "/api/admin/my-appointments?days=7", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["total"], 1)
        self.assertEqual(h.json()["turnos"][0]["dni"], "40666666")

    def test_preparacion_se_reexpone_igual_que_a_pacientes(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        ai_json = {"preparacion": [{"sin_preparacion": False, "ayuno": {"required": True, "horas": 8},
                                    "preparacion": ["Traer estudios previos"]}]}
        self._seed_solicitud("40888888", "Paciente", "Ocho", medico_asignado_id="med-1", ai_json=ai_json)

        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.json()["turnos"][0]["preparacion"], ai_json["preparacion"])

    def test_tiene_horarios_distingue_quincena_vacia_de_medico_sin_agenda(self):
        # «Mis turnos» vacío dice cosas distintas: sin turnos todavía, o un
        # médico al que nadie puede sacarle turno porque no tiene horarios.
        from app.modules.turnos import doctor_appointments as da
        self.assertFalse(da.has_schedule(None))
        self.assertFalse(da.has_schedule({"centros": [{"centro": "c", "franjas": []}]}))
        self.assertTrue(da.has_schedule({"centros": [{"centro": "c", "franjas": [{"dias": [1]}]}]}))
        self.assertTrue(da.has_schedule({"agenda": {"franjas": [{"dias": [2]}]}}))

        self._crear_operador_medico("dr-sin-catalogo", "med-que-no-existe")
        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_cookie("dr-sin-catalogo", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["turnos"], [])
        self.assertIs(h.json()["tiene_horarios"], False)

        self._crear_operador_medico("dr-fernandez", "med-1")
        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_cookie("dr-fernandez", "medico"))
        self.assertIn("tiene_horarios", h.json())

    # ---- matriz de permisos --------------------------------------------

    def test_medico_ve_sus_solicitudes_pero_no_usuarios_ni_reportes(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        headers = _cookie("dr-fernandez", "medico")

        h = _CLIENT.request("GET", "/api/admin/requests", headers=headers)
        self.assertEqual(h.status_code, 200)

        # Lee el catálogo público (2026-08-05, para dar un hueco propio); lo
        # que no puede es tocarlo.
        native = _CLIENT.get("/api/admin/content/services", headers=headers)
        self.assertEqual(native.status_code, 200)

        native = _CLIENT.get("/api/admin/users", headers=headers)
        self.assertEqual(native.status_code, 403)

        native = _CLIENT.get("/api/admin/reports", headers=headers)
        self.assertEqual(native.status_code, 403)

    def test_medico_puede_whoami_y_logout(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        headers = _cookie("dr-fernandez", "medico")

        h = _CLIENT.get("/api/admin/whoami", headers=headers)
        self.assertEqual(h.status_code, 200)
        out = h.json()
        self.assertEqual(out["rol"], "medico")

        h = _CLIENT.post("/api/admin/logout", headers=headers)
        self.assertEqual(h.status_code, 200)

    def test_medico_reasigna_solicitud_y_sigue_pendiente_fuera_de_su_lista(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        sid = db_booking.create_booking_request(
            "40999999", "Paciente", "Pendiente", "1980-01-01", "wa:11",
            "serv-cardio", "centro-a")
        db_booking.assign_pending_booking_request(
            sid, "María José Fernández", "med-1")
        headers = _cookie("dr-fernandez", "medico")

        h = _CLIENT.request("GET", "/api/admin/requests", headers=headers)
        self.assertEqual([s["id"] for s in h.json()["solicitudes"]], [sid])

        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/assign", json={"medico": "Carlos Gómez"}, headers=headers)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(db_booking.get_booking_request(sid)["estado"], "pendiente")
        self.assertEqual(db_booking.get_booking_request(sid)["medico_asignado_id"], "med-2")

        h = _CLIENT.request("GET", "/api/admin/requests", headers=headers)
        self.assertEqual(h.json()["solicitudes"], [])

    def test_admin_y_admisionista_403_en_mis_turnos(self):
        db_operators.operator_create("mesa-cualquiera", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_cookie("mesa-cualquiera", "admisionista"))
        self.assertEqual(h.status_code, 403)

        h = _CLIENT.request("GET", "/api/admin/my-appointments", headers=_admin_headers())
        self.assertEqual(h.status_code, 403)

    def test_sin_sesion_401_en_mis_turnos(self):
        h = _CLIENT.request("GET", "/api/admin/my-appointments")
        self.assertEqual(h.status_code, 401)

    # ---- guardado hacia adelante: resolver_medico_id al aprobar --------

    def test_aprobar_resuelve_y_guarda_medico_asignado_id(self):
        sid = db_booking.create_booking_request("40999999", "Nueve", "Paciente", "1980-01-01", "wa:11",
                                 "serv-cardio", "centro-a")
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2026-08-03T09:00", "medico": "María José Fernández"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["medico_asignado"], "María José Fernández")
        self.assertEqual(row["medico_asignado_id"], "med-1")

    def test_aprobar_con_nombre_fuera_del_catalogo_falla_sin_agenda(self):
        sid = db_booking.create_booking_request("41000000", "Diez", "Paciente", "1980-01-01", "wa:11",
                                 "serv-cardio", "centro-a")
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2026-08-03T09:00", "medico": "Alguien Que No Está En El Catálogo"}, headers=_admin_headers())
        self.assertEqual(h.status_code, 409)
        # Desde 2026-10-23 la aprobación frena antes: un nombre que no es de
        # ningún médico del catálogo no atiende el servicio (antes caía en
        # «sin agenda verificable», igual de cerrado pero menos claro).
        self.assertEqual(h.json()["error"], "medico_no_atiende_servicio")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["estado"], "pendiente")
        self.assertIsNone(row["medico_asignado"])
        self.assertIsNone(row["medico_asignado_id"])

    # ---- feed ICS del médico --------------------------------------------

    def test_feed_ics_solo_turnos_del_dueno_y_hora_art(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_operador_medico("dr-gomez", "med-2")
        # 12:30 UTC == 09:30 -03:00 (ART, sin DST) — prueba la conversión.
        # La FECHA es relativa a hoy: el feed sólo lista turnos futuros, así
        # que una fecha fija convierte este test en una bomba de tiempo (lo
        # fue: quedó en rojo el 2026-08-01, el día que la fecha alcanzó al
        # calendario).
        dia = (datetime.now(timezone.utc) + timedelta(days=7)).date()
        turno_utc = datetime(dia.year, dia.month, dia.day, 12, 30, 0, tzinfo=timezone.utc)
        self._seed_solicitud("41111111", "Once", "Paciente", medico_asignado_id="med-1", turno_dt=turno_utc)
        self._seed_solicitud("41222222", "Doce", "Paciente", medico_asignado_id="med-2", turno_dt=_futuro())

        token = db_operators.operator_feed_token_issue("dr-fernandez")["token"]
        self.assertTrue(token)
        h = _CLIENT.request("GET", f"/feed/doctor/{token}.ics")
        self.assertEqual(h.status_code, 200)
        ics = h.content.decode("utf-8")
        self.assertNotIn("41222222", ics)  # el turno del otro médico no aparece
        self.assertIn("DTSTART;TZID=America/Argentina/Buenos_Aires:"
                      + dia.strftime("%Y%m%d") + "T093000", ics)
        self.assertIn("SUMMARY:Turno · Cardiología", ics)
        self.assertIn("LOCATION:Centro A\\, Calle 1", ics)

    def test_feed_ics_no_lleva_datos_del_paciente(self):
        # El feed sale a un calendario de afuera (Google, Apple): ni nombre,
        # ni DNI (con o sin puntos), ni teléfono — sólo el servicio, el centro
        # y el enlace al turno en el panel.
        self._crear_operador_medico("dr-fernandez", "med-1")
        sid = self._seed_solicitud("41333333", "Rosalía", "Benavídez", medico_asignado_id="med-1")
        token = db_operators.operator_feed_token_issue("dr-fernandez")["token"]
        ics = _CLIENT.request("GET", f"/feed/doctor/{token}.ics").content.decode("utf-8")
        self.assertIn("BEGIN:VEVENT", ics)
        for dato in ("Rosalía", "Benavídez", "41333333", "41.333.333", "wa:11"):
            self.assertNotIn(dato, ics)
        self.assertIn("/admin/mis-turnos#turno-%s" % sid, ics)

    def test_feed_ics_token_invalido_404(self):
        h = _CLIENT.request("GET", "/feed/doctor/no-existe.ics")
        self.assertEqual(h.status_code, 404)

    def test_feed_de_otro_medico_y_token_de_paciente_no_sirven(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_operador_medico("dr-gomez", "med-2")
        self._seed_solicitud("41444444", "Paciente", "Uno", medico_asignado_id="med-1")
        token_gomez = db_operators.operator_feed_token_issue("dr-gomez")["token"]
        ics = _CLIENT.request("GET", f"/feed/doctor/{token_gomez}.ics").content.decode("utf-8")
        self.assertNotIn("BEGIN:VEVENT", ics)  # el de Gómez no trae los turnos de Fernández
        self.assertEqual(_CLIENT.request("GET", "/feed/doctor/%s.ics" % ("x" + token_gomez)).status_code, 404)

    def test_cambiar_la_direccion_revoca_la_anterior(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        viejo = db_operators.operator_feed_token_issue("dr-fernandez")["token"]
        self.assertEqual(_CLIENT.request("GET", f"/feed/doctor/{viejo}.ics").status_code, 200)

        h = _CLIENT.post("/api/admin/my-appointments/feed/rotate", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        nueva = h.json()["url"]
        self.assertTrue(nueva.endswith(".ics"))
        self.assertNotIn(viejo, nueva)
        self.assertEqual(_CLIENT.request("GET", f"/feed/doctor/{viejo}.ics").status_code, 404)
        nuevo = nueva.rsplit("/", 1)[1][:-4]
        self.assertEqual(_CLIENT.request("GET", f"/feed/doctor/{nuevo}.ics").status_code, 200)
        self.assertEqual(h.json()["hint"], nuevo[-4:])
        # el GET de después no la vuelve a mostrar ni crea otra: la base guarda su hash
        h = _CLIENT.request("GET", "/api/admin/my-appointments/feed", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.json(), {"url": None, "hint": nuevo[-4:]})
        self.assertEqual(_CLIENT.request("GET", f"/feed/doctor/{nuevo}.ics").status_code, 200)

    def test_cambiar_la_direccion_solo_el_medico(self):
        h = _CLIENT.post("/api/admin/my-appointments/feed/rotate", headers=_admin_headers())
        self.assertEqual(h.status_code, 403)
        h = _CLIENT.post("/api/admin/my-appointments/feed/rotate")
        self.assertIn(h.status_code, (401, 403))

    def test_feed_url_endpoint(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        h = _CLIENT.request("GET", "/api/admin/my-appointments/feed", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.headers["cache-control"], "no-store")
        url = h.json()["url"]
        self.assertIn("/feed/doctor/", url)
        self.assertTrue(url.endswith(".ics"))
        token = url.rsplit("/", 1)[1][:-4]
        self.assertEqual(h.json()["hint"], token[-4:])
        # Shown once: the next visit gets the last 4 characters, the address keeps working.
        h = _CLIENT.request("GET", "/api/admin/my-appointments/feed", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.json(), {"url": None, "hint": token[-4:]})
        self.assertEqual(_CLIENT.request("GET", f"/feed/doctor/{token}.ics").status_code, 200)

    def test_admin_cambia_la_direccion_del_calendario_de_un_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_operador_medico("dr-gomez", "med-2")
        vieja = db_operators.operator_feed_token_issue("dr-fernandez")["token"]
        otra = db_operators.operator_feed_token_issue("dr-gomez")["token"]
        h = _CLIENT.request("POST", "/api/admin/users/actions/reset-calendar",
                            json={"medico_ids": ["med-1"]}, headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["completados"], 1)
        self.assertEqual(_CLIENT.request("GET", f"/feed/doctor/{vieja}.ics").status_code, 404)
        # La próxima vez que el médico abre «Agregar a mi calendario» ve una nueva, entera.
        h = _CLIENT.request("GET", "/api/admin/my-appointments/feed", headers=_cookie("dr-fernandez", "medico"))
        nueva = h.json()["url"].rsplit("/", 1)[1][:-4]
        self.assertNotEqual(nueva, vieja)
        self.assertEqual(_CLIENT.request("GET", f"/feed/doctor/{nueva}.ics").status_code, 200)
        # El otro médico no se toca.
        self.assertEqual(db_operators.operator_by_feed_token(otra)["usuario"], "dr-gomez")

    def test_solo_el_admin_cambia_la_direccion_de_otro_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        vieja = db_operators.operator_feed_token_issue("dr-fernandez")["token"]
        h = _CLIENT.request("POST", "/api/admin/users/actions/reset-calendar",
                            json={"medico_ids": ["med-1"]}, headers=_cookie("dr-fernandez", "medico"))
        self.assertIn(h.status_code, (401, 403))
        self.assertEqual(db_operators.operator_by_feed_token(vieja)["usuario"], "dr-fernandez")

    # ---- GET /api/admin/doctors/<id>/appointments: tab "Calendario" del editor ----
    # (admin Y operador ven el calendario de CUALQUIER médico del catálogo —
    # reunión 2026-07-17: las administrativas manejan las agendas; 'medico'
    # sigue cerrado)

    def test_medicos_turnos_401_sin_sesion(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments")
        self.assertEqual(h.status_code, 401)

    def test_medicos_turnos_403_rol_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 403)

    def test_medicos_turnos_200_rol_admin(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["medico"]["id"], "med-1")

    def test_medicos_stats_200_rol_admin(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)

    def test_medicos_turnos_admin_ve_solo_los_del_medico_pedido(self):
        self._seed_solicitud("42111111", "Paciente", "Uno", medico_asignado_id="med-1")
        self._seed_solicitud("42222222", "Paciente", "Dos", medico_asignado_id="med-2")

        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["medico"]["id"], "med-1")
        self.assertEqual(h.json()["total"], 1)
        self.assertEqual(h.json()["turnos"][0]["dni"], "42111111")

    def test_medicos_turnos_dias_detalle_capacidad_y_ocupados(self):
        # calendario coloreado (2026-07-20): cada día con agenda trae capacidad
        # y ocupados; un turno aprobado suma en su día.
        turno = _futuro(48)
        self._seed_solicitud("42111111", "Paciente", "Uno",
                             medico_asignado_id="med-1", turno_dt=turno)
        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments?days=30", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        detalle = h.json()["dias_detalle"]
        self.assertTrue(detalle, "dias_detalle vacío")
        for d in detalle:
            self.assertIn("dia", d)
            self.assertIn("capacidad", d)
            self.assertIn("ocupados", d)
        dia_turno = h.json()["turnos"][0]["dia_iso"]
        fila = [d for d in detalle if d["dia"] == dia_turno]
        self.assertTrue(fila, "el día del turno no aparece en dias_detalle")
        self.assertEqual(fila[0]["ocupados"], 1)
        # los días vienen ordenados
        dias = [d["dia"] for d in detalle]
        self.assertEqual(dias, sorted(dias))

    def test_medicos_turnos_expone_contacto_edad_sobreturno(self):
        # auditoría 2026-07-20: contacto/nacimiento/sobreturno viajan al cliente
        self._seed_solicitud("42111111", "Paciente", "Uno", medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments", headers=_admin_headers())
        t = h.json()["turnos"][0]
        self.assertIn("contacto", t)
        self.assertIn("nacimiento", t)
        self.assertIn("sobreturno", t)
        self.assertIn("consultorio", t)
        self.assertFalse(t["sobreturno"])

    # ---- el contacto: una sola regla y de quién es (problems.md №4) --------

    def _turno_de(self, dni):
        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments", headers=_admin_headers())
        return [t for t in h.json()["turnos"] if t["dni"] == dni][0]

    def test_contacto_principal_es_el_de_la_solicitud_y_la_cuenta_distinta_se_ve_aparte(self):
        # El responsable del producto (hub, 2026-10-23): para el turno manda el teléfono de la
        # solicitud; el de la cuenta no se pisa y, si es otro, el médico ve
        # los dos, cada uno con su procedencia.
        db_accounts.create_user("42111112", "Carla", "Reserva", "1990-06-15", "F", "+5492254100002", "x" * 12)
        self._seed_solicitud("42111112", "Carla", "Reserva", medico_asignado_id="med-1")
        t = self._turno_de("42111112")
        self.assertEqual(t["contacto"], "wa:11")
        self.assertEqual(t["contacto_de"], "solicitud")
        self.assertEqual(t["contacto_cuenta"], "+5492254100002")
        self.assertEqual(t["contacto_cuenta_de"], "paciente")
        # y la cuenta sigue con su número: pedir el turno no la toca
        self.assertEqual(db_accounts.get_user("42111112")["contacto"], "+5492254100002")

    def test_mismo_numero_escrito_distinto_se_muestra_una_sola_vez(self):
        db_accounts.create_user("42111116", "Luz", "Mismo", "1990-06-15", "F", "+5492254100016", "x" * 12)
        sid = db_booking.create_booking_request("42111116", "Luz", "Mismo", "1990-06-15", "2254 10-0016",
                                        "serv-cardio", "centro-a")
        db_booking.approve(sid, _futuro(), medico_asignado_id="med-1", resuelta_por="admin")
        t = self._turno_de("42111116")
        self.assertEqual((t["contacto"], t["contacto_de"]), ("+5492254100016", "paciente"))
        self.assertIsNone(t["contacto_cuenta"])

    def test_contacto_de_un_familiar_a_cargo_es_del_responsable_con_su_nombre(self):
        db_accounts.create_user("42111113", "ANA", "PEREZ", "1985-01-01", "F", "+5492254100003", "x" * 12)
        sid = db_booking.create_booking_request("42111114", "Tomi", "Perez", "2018-01-01", "+5492254100003",
                                        "serv-cardio", "centro-a", solicitante_dni="42111113")
        db_booking.approve(sid, _futuro(), medico_asignado_id="med-1", resuelta_por="admin")
        t = self._turno_de("42111114")
        self.assertEqual(t["contacto"], "+5492254100003")
        self.assertEqual(t["contacto_de"], "responsable")
        self.assertEqual(t["contacto_responsable"], "Ana Perez")
        self.assertIsNone(t["contacto_cuenta"])

    def test_familiar_con_otro_numero_en_la_solicitud_muestra_los_dos(self):
        db_accounts.create_user("42111117", "ANA", "PEREZ", "1985-01-01", "F", "+5492254100017", "x" * 12)
        sid = db_booking.create_booking_request("42111118", "Tomi", "Perez", "2018-01-01", "+5492254199918",
                                        "serv-cardio", "centro-a", solicitante_dni="42111117")
        db_booking.approve(sid, _futuro(), medico_asignado_id="med-1", resuelta_por="admin")
        t = self._turno_de("42111118")
        self.assertEqual((t["contacto"], t["contacto_de"]), ("+5492254199918", "solicitud"))
        self.assertEqual((t["contacto_cuenta"], t["contacto_cuenta_de"]),
                         ("+5492254100017", "responsable"))
        self.assertEqual(t["contacto_responsable"], "Ana Perez")

    def test_contacto_sin_cuenta_dice_que_es_el_de_la_solicitud(self):
        self._seed_solicitud("42111115", "Sin", "Cuenta", medico_asignado_id="med-1")
        t = self._turno_de("42111115")
        self.assertEqual(t["contacto"], "wa:11")
        self.assertEqual(t["contacto_de"], "solicitud")

    def test_medicos_turnos_404_medico_desconocido(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/no-existe/appointments", headers=_admin_headers())
        self.assertEqual(h.status_code, 404)
        self.assertEqual(h.json()["error"], "medico_desconocido")

    def test_medico_incompleto_tiene_su_calendario_vacio_no_404(self):
        # A doctor saved without a center yet (the editor no longer
        # preselects one, on-site tester 2026-10-21) is hidden from the public
        # catalog, but its own panel calendar answers, empty.
        from unittest import mock
        nuevo = {"id": "med-nuevo", "nombre": "Nuevo Sin Centro", "incompleto": True}
        real = catalog.doctors
        with mock.patch.object(catalog, "doctors",
                               lambda incluir_incompletos=False: real(incluir_incompletos) + ([nuevo] if incluir_incompletos else [])):
            h = _CLIENT.request("GET", "/api/admin/doctors/med-nuevo/appointments", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["total"], 0)

    def test_medicos_turnos_dias_clamp(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments?days=999", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["dias"], 90)

        h = _CLIENT.request("GET", "/api/admin/doctors/med-1/appointments?days=0", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["dias"], 1)

    # ---- GET /api/admin/doctors/stats: métricas de gestión del listado ----

    def test_medicos_stats_401_sin_sesion(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/stats")
        self.assertEqual(h.status_code, 401)

    def test_medicos_stats_403_rol_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 403)

    def test_medicos_stats_200_admin_claves_esperadas(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        meds = h.json()["medicos"]
        self.assertIn("med-1", meds)
        self.assertIn("med-2", meds)
        claves = {"horas_semana", "proximo_dia", "proximo_dia_iso", "turnos_14d", "slots_14d",
                  "ocupacion_14d", "tiene_foto", "tiene_login", "matricula", "servicios"}
        self.assertEqual(claves, set(meds["med-1"].keys()))

    def test_medicos_stats_filas_rol_excluidas(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
        self.assertNotIn("med-3-rol", h.json()["medicos"])

    def test_medicos_stats_horas_semana_y_servicios_con_agenda(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
        st = h.json()["medicos"]["med-1"]
        # lunes+miércoles, 09:00-11:00 (2 h) * 2 días = 4.0 h/semana
        self.assertEqual(st["horas_semana"], 4.0)
        self.assertIsNotNone(st["proximo_dia"])
        self.assertIn(st["proximo_dia"], ("Hoy", "Mañana", "Lunes", "Miércoles"))
        self.assertEqual(st["matricula"], "12345")
        # serv-cardio matchea cluster+centro de med-1
        self.assertEqual(st["servicios"], 1)
        self.assertFalse(st["tiene_login"])
        self.assertFalse(st["tiene_foto"])

    def test_medicos_stats_ocupacion_null_sin_agenda(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
        st = h.json()["medicos"]["med-2"]
        self.assertEqual(st["horas_semana"], 0.0)
        self.assertIsNone(st["proximo_dia"])
        self.assertIsNone(st["ocupacion_14d"])
        self.assertEqual(st["turnos_14d"], 0)
        self.assertEqual(st["slots_14d"], 0)
        self.assertEqual(st["servicios"], 0)  # sin cluster propio, no matchea nada
        self.assertIsNone(st["matricula"])

    def test_medicos_stats_turnos_14d_y_ocupacion(self):
        # un turno DENTRO de los 14 días cuenta; uno a 20 días no.
        self._seed_solicitud("43111111", "Cerca", "Uno", medico_asignado_id="med-1", turno_dt=_futuro(48))
        self._seed_solicitud("43222222", "Lejos", "Dos", medico_asignado_id="med-1", turno_dt=_futuro(24 * 20))

        # Capturado antes del request: doctors_stats_data calcula su propio
        # "ahora" internamente. Tomar el snapshot acá
        # arriba en vez de abajo, después de la llamada, angosta la ventana en
        # la que un slot podría cruzar de banda entre las dos lecturas del reloj.
        ahora = datetime.now()  # naive/local — mismo criterio que _medicos_stats
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
        st = h.json()["medicos"]["med-1"]
        self.assertEqual(st["turnos_14d"], 1)
        self.assertIsNotNone(st["ocupacion_14d"])

        franjas, excepciones = doctor_appointments._windows_and_exceptions(_MEDICOS["medicos"][0])
        slots = doctor_appointments._slots_in_days(franjas, excepciones, doctor_appointments._STATS_DAYS, ahora)
        self.assertEqual(st["slots_14d"], slots)
        self.assertEqual(st["ocupacion_14d"], round(100 * 1 / slots))

    def test_medicos_stats_tiene_login_true_con_cuenta_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
        self.assertTrue(h.json()["medicos"]["med-1"]["tiene_login"])
        self.assertFalse(h.json()["medicos"]["med-2"]["tiene_login"])

    def test_medicos_stats_503_sin_db(self):
        # simulamos la base caída con un desde= que dispara DbUnavailable
        orig = db_booking.list_approved_future

        def _boom(*a, **kw):
            raise db.DbUnavailable("sin conexión")
        db_booking.list_approved_future = _boom
        try:
            h = _CLIENT.request("GET", "/api/admin/doctors/stats", headers=_admin_headers())
            self.assertEqual(h.status_code, 503)
        finally:
            db_booking.list_approved_future = orig


if __name__ == "__main__":
    unittest.main()


class ServiciosCompatiblesTestCase(unittest.TestCase):
    """GET /api/admin/doctors/<id>/compatible-services (responsable del producto, 2026-07-24): el
    combo "esta franja es de <servicio>" del editor de agenda, ordenado por
    compatibilidad — propios primero, después por uso en la especialidad,
    después mismo cluster, después el resto."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        admin_auth._ATTEMPTS.clear()
        self._tmp = tempfile.mkdtemp(prefix="compat-serv-test-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir, exist_ok=True)
        centros = {"centros": [{"slug": "centro-a", "name": "Centro A"},
                                {"slug": "centro-b", "name": "Centro B"}]}
        servicios = {"servicios": [
            {"id": "serv-antic", "nombre": "Anticoncepción", "centro": "centro-a",
             "modo": "solicitud", "cluster": "clinica"},
            {"id": "serv-general", "nombre": "Medicina general", "centro": "centro-a",
             "modo": "solicitud", "cluster": "clinica"},
            {"id": "serv-testeos", "nombre": "Testeos", "centro": "centro-b",
             "modo": "walkin", "cluster": "clinica"},
            {"id": "serv-eco", "nombre": "Ecografía", "centro": "centro-a",
             "modo": "solicitud_con_orden", "cluster": "diagnostico"},
        ]}
        medicos = {"medicos": [
            # la médica que editamos: ya tiene DOS servicios etiquetados
            {"id": "med-funes", "nombre": "Andrea Funes",
             "especialidad": "Medicina General", "centro": "centro-a", "cluster": "clinica",
             "centros": [{"centro": "centro-a",
                          "franjas": [
                              {"dias": [1], "desde": "08:00", "hasta": "12:00",
                               "cada_min": 20, "servicio": "serv-antic"},
                              {"dias": [2], "desde": "08:00", "hasta": "12:00",
                               "cada_min": 20, "servicio": "serv-general"}],
                          "excepciones": []}]},
            # otra generalista: usa testeos — cuenta para el uso por especialidad
            {"id": "med-otra", "nombre": "Otra Generalista",
             "especialidad": "Medicina General", "centro": "centro-b", "cluster": "clinica",
             "centros": [{"centro": "centro-b",
                          "franjas": [{"dias": [3], "desde": "08:00", "hasta": "12:00",
                                       "cada_min": 20, "servicio": "serv-testeos"}],
                          "excepciones": []}]},
            # de OTRA especialidad: su etiqueta no debe contar para Funes
            {"id": "med-cardio", "nombre": "Cardióloga Ajena",
             "especialidad": "Cardiología", "centro": "centro-a", "cluster": "cardiologia",
             "centros": [{"centro": "centro-a",
                          "franjas": [{"dias": [4], "desde": "08:00", "hasta": "12:00",
                                       "cada_min": 20, "servicio": "serv-eco"}],
                          "excepciones": []}]},
        ]}
        with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
            json.dump(centros, f)
        with open(os.path.join(webdir, "servicios.json"), "w", encoding="utf-8") as f:
            json.dump(servicios, f)
        with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
            json.dump(medicos, f)
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(catalog._JSON_CACHE.clear)
        self.addCleanup(catalog.invalidate)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _get(self, path, headers=None):
        h = _CLIENT.request("GET", path, headers=headers or _admin_headers())
        return h

    def test_propios_primero_despues_uso_de_especialidad(self):
        h = self._get("/api/admin/doctors/med-funes/compatible-services")
        self.assertEqual(h.status_code, 200)
        ids = [it["id"] for it in h.json()["items"]]
        # (1) los DOS servicios que ya tiene en sus franjas, en su orden
        self.assertEqual(ids[:2], ["serv-antic", "serv-general"])
        # (2) el que usa otra generalista gana al resto
        self.assertEqual(ids[2], "serv-testeos")
        # (3) la eco (otro cluster, usada por otra especialidad) va última
        self.assertEqual(ids[-1], "serv-eco")
        self.assertTrue(h.json()["items"][0]["propio"])
        self.assertEqual(h.json()["items"][2]["usos_especialidad"], 1)

    def test_medico_nuevo_por_especialidad(self):
        # sin id todavía: el editor manda ?especialidad= y ordena por uso
        h = self._get("/api/admin/doctors/nuevo/compatible-services"
                      "?especialidad=Medicina%20General")
        self.assertEqual(h.status_code, 200)
        ids = [it["id"] for it in h.json()["items"]]
        # nadie es "propio"; ganan los usados por la especialidad
        self.assertEqual(set(ids[:3]), {"serv-antic", "serv-general", "serv-testeos"})
        self.assertEqual(ids[-1], "serv-eco")
        self.assertFalse(any(it["propio"] for it in h.json()["items"]))

    def test_requiere_sesion(self):
        h = _CLIENT.request("GET", "/api/admin/doctors/med-funes/compatible-services")
        self.assertEqual(h.status_code, 401)
