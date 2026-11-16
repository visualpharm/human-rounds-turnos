"""test_cola_dia.py — "Pacientes de hoy": doctor_appointments._day_queue y la
apertura de booking_requests._mark_attendance a rol médico (2026-07-24).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_cola_dia -v

Sigue el patrón de test_doctor_appointments.py (webdata propio vía
TURNOS_WEBDATA_DIR, aislado del real; TestClient y sesiones admin/
operador/médico por cookie firmada con admin_auth._mint_token) + base de
datos dedicada `sana_turnos_test_cola` (mismo patrón que test_reports.py)
para correr en paralelo con las otras suites sin pisarlas.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
import _clock  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.auth import admin_auth       # noqa: E402
from app.modules.turnos import booking_queue      # noqa: E402
from app.modules.turnos import doctor_appointments    # noqa: E402
from app.patient_web import catalog         # noqa: E402

from app.auth import turnstile          # noqa: E402
from app.core import config  # noqa: E402
from app.core import tz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

_CLIENT = TestClient(app_module.app)
turnstile.ENV_PATH = ""   # no leer el .env real (mismo truco que los otros tests)
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev

_ART = tz.tzinfo()  # instalación (zoneinfo, DST-safe — ver tz.py)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_cola", seed=lambda: db_operators.operators_list())


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
            cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE catalog")
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE consultations RESTART IDENTITY CASCADE")


# ---- fixtures del catálogo (aisladas del webdata real) ----------------------

_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS = {"servicios": [
    {"id": "serv-cardio", "nombre": "Cardiología", "centro": "centro-a", "modo": "solicitud", "cluster": "cardiologia"},
    {"id": "serv-lab", "nombre": "Laboratorio", "centro": "centro-a", "modo": "walkin_con_orden", "cluster": "laboratorio"},
]}
# med-1/med-2 tienen cuenta médico en algunos tests; med-2 nunca la tiene en
# otros (sirve para el fallback por nombre y para "otro médico").
_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología", "centro": "centro-a",
     "cluster": "cardiologia", "entrevista_habilitada": True},
    {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Pediatría", "centro": "centro-a",
     "entrevista_habilitada": True},
]}
# These queue-integration fixtures explicitly opted in. New professionals stay
# off by default; the disabled-feature case below still verifies its exclusion.


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
        json.dump(_CENTROS, f)
    with open(os.path.join(webdir, "servicios.json"), "w", encoding="utf-8") as f:
        json.dump(_SERVICIOS, f)
    with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
        json.dump(_MEDICOS, f)


def _reset_catalog():
    catalog._JSON_CACHE.clear()
    catalog.invalidate()


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


def _operador_headers(usuario="operadora-1"):
    return _cookie(usuario, "admisionista")


def _hoy_art():
    return datetime.now(_ART).date()


def _dt_art(dia, hora=10, minuto=0):
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=_ART)


class ColaDiaTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        self._tmp = tempfile.mkdtemp(prefix="cola-dia-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        self.addCleanup(_reset_catalog)   # registered first, so it runs after the environment is put back
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        _reset_catalog()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_tables()

    # ---- helpers ------------------------------------------------------

    def _crear_operador_medico(self, usuario, medico_id):
        return db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)

    def _seed(self, dni, nombre, apellido, turno_dt, medico_asignado=None, medico_asignado_id=None,
             servicio_id="serv-cardio", ai_json=None, orden=False, asistencia=None):
        sid = db_booking.create_booking_request(dni, nombre, apellido, "1980-01-01", "wa:11", servicio_id, "centro-a")
        if ai_json is not None:
            db_booking.save_ai(sid, ai_json)
        db_booking.approve(sid, turno_dt, medico_asignado=medico_asignado,
                  medico_asignado_id=medico_asignado_id, resuelta_por="admin")
        if orden or asistencia:
            import psycopg
            with psycopg.connect(_TEST_URL, connect_timeout=5) as c:
                with c.cursor() as cur:
                    if orden:
                        ruta = os.path.join(self._tmp, f"orden-{sid}.pdf")
                        with open(ruta, "wb") as f:
                            f.write(b"%PDF-1.4 orden de prueba")
                        cur.execute("UPDATE booking_requests SET referral_path=%s, referral_mime=%s WHERE id=%s",
                                    (ruta, "application/pdf", sid))
                    if asistencia:
                        cur.execute(
                            """UPDATE booking_requests SET attendance=%s, attendance_updated_at=now(),
                                   attendance_updated_by=%s WHERE id=%s""",
                            (asistencia, "dr-fernandez", sid))
                c.commit()
        return sid

    def _crear_entrevista(self, doctor_id, request_id=None, triage="VERDE",
                          resumen="Resumen de prueba", estado="done",
                          entered_name=None, entered_surname=None):
        """Entrevista de modules/intake/ — matcheada (request_id = id del
        turno) o por QR sin matchear (request_id=None). `estado='active'`
        deja el triage sin analizar (mismo orden real: analysis.py corre
        DESPUÉS de que interview_agent._close pone status='done')."""
        fila = db_clinical.interview_create(
            doctor_id=doctor_id,
            entry_mode="post_booking" if request_id is not None else "qr",
            request_id=request_id, entered_name=entered_name, entered_surname=entered_surname,
            matched=request_id is not None,
        )
        if estado == "done":
            db_clinical.interview_update_analysis(fila["id"], triage, resumen, {}, [], [], [], "fake-model")
            db_clinical.interview_set_status(fila["id"], "done")
        return fila["id"]

    # ---- auth / rol -----------------------------------------------------

    def test_401_sin_sesion(self):
        h = _CLIENT.request("GET", "/api/admin/day-queue")
        self.assertEqual(h.status_code, 401)

    def test_operador_200(self):
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_operador_headers())
        self.assertEqual(h.status_code, 200)

    def test_admisionista_ve_la_cola_completa(self):
        # Rol nuevo (reunión hospital 2026-07-24): la mesa de admisión ve la
        # cola del día de TODOS los médicos, igual que admin/operador.
        hoy = _hoy_art()
        self._seed("40777771", "Paciente", "Uno", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self._seed("40777772", "Paciente", "Dos", _dt_art(hoy, 10), medico_asignado_id="med-2")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("mesa-1", "admisionista"))
        self.assertEqual(h.status_code, 200)
        dnis = [t["dni"] for g in h.json()["grupos"] for t in g["turnos"]]
        self.assertIn("40777771", dnis)
        self.assertIn("40777772", dnis)

    def test_admisionista_entra_a_la_cola_de_solicitudes(self):
        # El gate por default lo rechaza: sólo day-queue y asistencia le abren.
        h = _CLIENT.request("GET", "/api/admin/requests", headers=_cookie("mesa-1", "admisionista"))
        self.assertEqual(h.status_code, 200)

    # ---- alcance por rol --------------------------------------------------

    def test_medico_ve_solo_lo_suyo_por_id_y_por_nombre(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        hoy = _hoy_art()
        self._seed("40111111", "Paciente", "Uno", _dt_art(hoy, 9), medico_asignado_id="med-1")
        # fallback por nombre (tokens sin acentos, orden invertido): sigue matcheando
        self._seed("40222222", "Paciente", "Dos", _dt_art(hoy, 10), medico_asignado="Fernandez, Maria Jose")
        self._seed("40333333", "Paciente", "Tres", _dt_art(hoy, 11), medico_asignado_id="med-2")

        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["total"], 2)
        self.assertEqual(len(h.json()["grupos"]), 1)
        self.assertEqual(h.json()["grupos"][0]["medico_id"], "med-1")
        dnis = {t["dni"] for t in h.json()["grupos"][0]["turnos"]}
        self.assertEqual(dnis, {"40111111", "40222222"})

    def test_medico_sin_medico_id_400(self):
        db_operators.operator_create("dr-sin-medico", "clave1234", "medico")  # sin medico_id: el rol REAL de la cuenta manda sobre el de la cookie
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-sin-medico", "medico"))
        self.assertEqual(h.status_code, 400)

    def test_admin_ve_todos_agrupados_incluido_medico_null(self):
        hoy = _hoy_art()
        self._seed("40444444", "Paciente", "Cuatro", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self._seed("40555555", "Paciente", "Cinco", _dt_art(hoy, 10), medico_asignado_id="med-2")
        self._seed("40666666", "Paciente", "Seis", _dt_art(hoy, 11), servicio_id="serv-lab")  # laboratorio, sin médico

        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["total"], 3)
        medico_ids = {g["medico_id"] for g in h.json()["grupos"]}
        self.assertEqual(medico_ids, {"med-1", "med-2", None})
        grupo_sin_medico = next(g for g in h.json()["grupos"] if g["medico_id"] is None)
        self.assertEqual([t["dni"] for t in grupo_sin_medico["turnos"]], ["40666666"])

    def test_admin_filtra_por_medico(self):
        hoy = _hoy_art()
        self._seed("40777777", "Paciente", "Siete", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self._seed("40888888", "Paciente", "Ocho", _dt_art(hoy, 10), medico_asignado_id="med-2")

        h = _CLIENT.request("GET", "/api/admin/day-queue?medico=med-1", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["total"], 1)
        self.assertEqual(h.json()["grupos"][0]["medico_id"], "med-1")

    # ---- parámetro dia / recorte del día ---------------------------------

    def test_dia_default_hoy_y_hoy_true(self):
        hoy = _hoy_art()
        self._seed("40999999", "Paciente", "Nueve", _dt_art(hoy, 9), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertEqual(h.json()["dia"], hoy.isoformat())
        self.assertTrue(h.json()["hoy"])
        self.assertEqual(h.json()["total"], 1)

    def test_turno_de_hoy_ya_pasado_sigue_en_la_cola(self):
        # listar_aprobadas_dia trae TODO el día — a diferencia de
        # listar_aprobadas_futuras, que se pierde lo pasado-de-hoy.
        hoy = _hoy_art()
        # "Hace una hora" a secas cae en AYER si son menos de las 01:00 (esta
        # suite corrió a las 00:06 y falló por eso). Lo que se prueba es "ya
        # pasó y sigue siendo hoy": el momento más temprano de la jornada
        # cumple las dos cosas a cualquier hora.
        ahora = datetime.now(_ART)
        pasado_de_hoy = max(ahora - timedelta(hours=1),
                            ahora.replace(hour=0, minute=1, second=0, microsecond=0))
        self._seed("41000000", "Paciente", "Diez", pasado_de_hoy, medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertEqual(h.json()["total"], 1)
        self.assertEqual(h.json()["grupos"][0]["turnos"][0]["dia_iso"], hoy.isoformat())

    def test_dia_explicito_otro_dia(self):
        otro = _hoy_art() + timedelta(days=3)
        self._seed("41011111", "Paciente", "Once", _dt_art(otro, 9), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue?dia=%s" % otro.isoformat(), headers=_admin_headers())
        self.assertEqual(h.json()["dia"], otro.isoformat())
        self.assertFalse(h.json()["hoy"])
        self.assertEqual(h.json()["total"], 1)
        # ese mismo día no aparece en la cola de HOY
        h2 = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertEqual(h2.json()["total"], 0)

    # ---- serialización: orden/ai_campos/asistencia/elegibilidad -----------

    def test_campos_de_orden_y_ai_campos_presentes(self):
        hoy = _hoy_art()
        ai_json = {"campos": {"practicas": ["Ergometría de 12 derivaciones"]},
                   "preparacion": [{"sin_preparacion": True}]}
        self._seed("41022222", "Paciente", "Doce", _dt_art(hoy, 9), medico_asignado_id="med-1",
                  ai_json=ai_json, orden=True)
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertTrue(t["tiene_orden"])
        self.assertEqual(t["orden_url"], "/api/admin/order/%s" % t["id"])
        # _seed(orden=True) escribe orden_mime='application/pdf' (ver arriba) —
        # el overlay del front necesita este dato para elegir <embed>/<img>/
        # <object> en vez de adivinar (root cause del box vacío en Chromium).
        self.assertEqual(t["orden_mime"], "application/pdf")
        self.assertEqual(t["ai_campos"], {"practicas": ["Ergometría de 12 derivaciones"]})
        self.assertRegex(t["solicitado_en"], r"^\d{4}-\d{2}-\d{2}T")

    def test_cobertura_del_paciente_disponible_como_columna_opcional(self):
        hoy = _hoy_art()
        db_accounts.create_user(
            "41022223", "Paciente", "Cobertura", "1980-01-01", "f", "wa:11",
            "clave1234",
            cobertura={
                "obras_sociales": [{"nombre": "OSDE"}],
                "prepaga": {"nombre": "Swiss Medical"},
            },
        )
        self._seed("41022223", "Paciente", "Cobertura", _dt_art(hoy, 9),
                   medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertEqual(t["cobertura"], "OSDE · Swiss Medical")

    def test_sexo_del_paciente_viaja_con_el_dni(self):
        # HSI busca al paciente por documento Y sexo: la extensión de la mesa
        # (extensions/hsi-humanizador) necesita los dos en la misma fila.
        hoy = _hoy_art()
        sid = db_booking.create_booking_request("41022224", "Paciente", "Sexo", "1980-01-01", "wa:11",
                                        "serv-cardio", "centro-a", sexo="F")
        db_booking.approve(sid, _dt_art(hoy, 9), medico_asignado_id="med-1", resuelta_por="admin")
        self._seed("41022225", "Paciente", "Sinsexo", _dt_art(hoy, 10), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        por_dni = {t["dni"]: t for t in h.json()["grupos"][0]["turnos"]}
        self.assertEqual(por_dni["41022224"]["sexo"], "F")
        self.assertIsNone(por_dni["41022225"]["sexo"])

    def test_el_centro_viaja_con_su_identificador(self):
        # La extensión de la mesa elige la institución de HSI por el centro de
        # la fila: el nombre se puede editar en el panel, el identificador no.
        hoy = _hoy_art()
        sid = db_booking.create_booking_request("41022226", "Paciente", "Centro", "1980-01-01", "wa:11",
                                        "serv-cardio", "centro-a")
        db_booking.approve(sid, _dt_art(hoy, 9), medico_asignado_id="med-1", resuelta_por="admin")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertEqual(t["centro"], "Centro A")
        self.assertEqual(t["centro_id"], "centro-a")

    def test_orden_url_del_medico_apunta_a_su_propia_ruta(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        hoy = _hoy_art()
        self._seed("41033333", "Paciente", "Trece", _dt_art(hoy, 9), medico_asignado_id="med-1", orden=True)
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertEqual(t["orden_url"], "/api/admin/my-appointments/%s/order" % t["id"])

    def test_cada_fila_lleva_la_institucion_de_hsi_de_su_centro(self):
        # Cada centro es su propia institución en HSI: la extensión de la mesa
        # abre la de la fila. Dos centros seguidos, dos instituciones; un
        # centro sin número no lleva ninguno (la extensión pregunta).
        centros = {"centros": _CENTROS["centros"] + [
            {"slug": "centro-b", "name": "Centro B", "hsi_institution": "1086"},
            {"slug": "centro-c", "name": "Centro C", "hsi_institution": "1077"},
        ]}
        with open(os.path.join(self._tmp, "webdata", "centros-salud.json"), "w", encoding="utf-8") as f:
            json.dump(centros, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        hoy = _hoy_art()
        for dni, centro, hora in (("41055551", "centro-b", 9), ("41055552", "centro-c", 10),
                                  ("41055553", "centro-a", 11)):
            sid = db_booking.create_booking_request(dni, "Paciente", "Centro", "1980-01-01", "wa:11",
                                            "serv-cardio", centro)
            db_booking.approve(sid, _dt_art(hoy, hora), medico_asignado_id="med-1", resuelta_por="admin")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        por_dni = {t["dni"]: t for g in h.json()["grupos"] for t in g["turnos"]}
        self.assertEqual(por_dni["41055551"]["hsi_institucion"], "1086")
        self.assertEqual(por_dni["41055552"]["hsi_institucion"], "1077")
        self.assertIsNone(por_dni["41055553"]["hsi_institucion"])

    def test_datetime_van_como_string_iso(self):
        # Regresión (docs/solutions/hsi/attendance-no-show-sync.md): un
        # datetime crudo en el JSON rompe la serialización silenciosamente.
        hoy = _hoy_art()
        sid = self._seed("41044444", "Paciente", "Catorce", _dt_art(hoy, 9), medico_asignado_id="med-1",
                         asistencia="presente")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        json.dumps(h.json())  # no debe reventar
        self.assertIsInstance(t["turno_dt"], str)
        self.assertIsInstance(t["asistencia_en"], str)
        self.assertEqual(t["asistencia"], "presente")
        self.assertEqual(t["asistencia_por"], "dr-fernandez")
        self.assertEqual(t["id"], sid)

    def test_elegible_asistencia_hoy_true_y_dia_futuro_false(self):
        hoy = _hoy_art()
        futuro = hoy + timedelta(days=2)
        self._seed("41055555", "Hoy", "Elegible", _dt_art(hoy, 23), medico_asignado_id="med-1")
        self._seed("41066666", "Futuro", "NoElegible", _dt_art(futuro, 9), medico_asignado_id="med-1")

        h = _CLIENT.request("GET", "/api/admin/day-queue?dia=%s" % hoy.isoformat(), headers=_admin_headers())
        self.assertTrue(h.json()["grupos"][0]["turnos"][0]["elegible_asistencia"])

        h2 = _CLIENT.request("GET", "/api/admin/day-queue?dia=%s" % futuro.isoformat(), headers=_admin_headers())
        self.assertFalse(h2.json()["grupos"][0]["turnos"][0]["elegible_asistencia"])

    # ---- orden del médico: ownership -----------------------------------

    def test_medico_orden_200_propia_404_ajena(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_operador_medico("dr-gomez", "med-2")
        hoy = _hoy_art()
        sid_propia = self._seed("41077777", "Propia", "Orden", _dt_art(hoy, 9),
                                medico_asignado_id="med-1", orden=True)
        sid_ajena = self._seed("41088888", "Ajena", "Orden", _dt_art(hoy, 10),
                               medico_asignado_id="med-2", orden=True)

        h = _CLIENT.request("GET", "/api/admin/my-appointments/%s/order" % sid_propia, headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.headers.get("Content-Type"), "application/pdf")
        self.assertIn(b"orden de prueba", h.content)

        h2 = _CLIENT.request("GET", "/api/admin/my-appointments/%s/order" % sid_ajena, headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h2.status_code, 404)

    def test_medico_orden_inexistente_404(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        h = _CLIENT.request("GET", "/api/admin/my-appointments/999999/order", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 404)

    def test_admin_no_puede_usar_la_ruta_de_orden_del_medico(self):
        h = _CLIENT.request("GET", "/api/admin/my-appointments/1/order", headers=_admin_headers())
        self.assertEqual(h.status_code, 403)

    # ---- huecos libres del día (2026-07-27) -----------------------------
    #
    # reference-deployment counterpart, reunión 2026-07-24: "acá Roger de 8 a 9:30, pero si hay un
    # turno libre de 8:30 no lo veo para poder dárselo a alguien que vino".

    def _dar_agenda(self, medico_id, franjas, excepciones=None):
        """Reescribe la fixture del catálogo para que ese médico tenga agenda."""
        meds = json.loads(json.dumps(_MEDICOS))
        for m in meds["medicos"]:
            if m["id"] == medico_id:
                m["centros"] = [{"centro": "centro-a", "franjas": franjas,
                                 "excepciones": excepciones or []}]
        with open(os.path.join(os.environ["TURNOS_WEBDATA_DIR"], "medicos.json"),
                  "w", encoding="utf-8") as f:
            json.dump(meds, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def _dia_de_semana_getday(self, d):
        return d.isoweekday() % 7

    def test_los_huecos_libres_del_dia_salen_en_el_grupo(self):
        hoy = _hoy_art()
        self._dar_agenda("med-1", [{"dias": [self._dia_de_semana_getday(hoy)],
                                    "desde": "08:00", "hasta": "09:00", "cada_min": 30}])
        # 08:00 dado; quedan libres 08:30
        self._seed("40911111", "Paciente", "Uno", _dt_art(hoy, 8, 0), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        g = next(g for g in h.json()["grupos"] if g["medico_id"] == "med-1")
        self.assertEqual(g["libres"], ["08:30"])

    # ---- encabezado del grupo (handoff 2026-07-27) ----------------------
    #
    # Con seis médicos en la misma tabla, el nombre solo no ubica a nadie: el
    # encabezado dice también de qué atiende y dónde.

    def test_cada_grupo_dice_especialidad_y_centro(self):
        hoy = _hoy_art()
        self._seed("40922222", "Paciente", "Dos", _dt_art(hoy, 9, 0), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        g = next(g for g in h.json()["grupos"] if g["medico_id"] == "med-1")
        self.assertEqual(g["especialidad"], "Cardiología")
        self.assertEqual(g["centro"], "Centro A")

    def test_un_medico_con_turnos_en_dos_centros_no_afirma_uno(self):
        # Antes que decir el centro equivocado, no decir ninguno: el médico con
        # dos sedes es el caso normal acá.
        dos = [{"centro": "Centro A"}, {"centro": "Centro B"}]
        self.assertIsNone(doctor_appointments._center_of_group({}, dos))
        self.assertEqual(doctor_appointments._center_of_group({}, [{"centro": "Centro A"}]), "Centro A")
        # Sin turnos ese día (agenda entera libre) se cae al catálogo.
        self.assertEqual(
            doctor_appointments._center_of_group({"centros": [{"centro": "centro-a"}]}, []), "Centro A")

    def test_el_filtro_de_centro_encuentra_al_medico_en_cada_uno_de_sus_centros(self):
        # La contraparte técnica, 2026-10-24: «Todos los centros» sólo ofrecía dos. El encabezado
        # no afirma centro con dos sedes (arriba), pero el filtro sí lista todas.
        dos = [{"centro": "Centro A"}, {"centro": "Centro B"}, {"centro": "Centro A"}]
        self.assertEqual(doctor_appointments._centers_of_group({}, dos), ["Centro A", "Centro B"])
        self.assertEqual(
            doctor_appointments._centers_of_group({"centros": [{"centro": "centro-a"}]}, []), ["Centro A"])
        self.assertEqual(doctor_appointments._centers_of_group({}, []), [])

    def test_el_medico_que_pidio_la_paciente_sale_por_id_del_catalogo_y_nunca_por_la_sugerencia(self):
        # La contraparte técnica, 2026-10-24: el panel de Solicitudes dice qué intentó sacar. Un
        # id desconocido no inventa un nombre; `medico_asignado` (la sugerencia
        # de la mesa en una pendiente) no es lo que la paciente pidió.
        pedir = booking_queue._requested_doctor_name
        self.assertEqual(pedir({"servicio_id": "serv-cardio", "slot_medico_id": "med-2"}, None), "Carlos Gómez")
        self.assertEqual(pedir({"servicio_id": "serv-cardio", "slot_medico_id": "id-que-no-existe"}, None), "")
        self.assertEqual(pedir({"servicio_id": "serv-cardio", "medico_asignado_id": "med-1",
                                "medico_asignado": "María José Fernández"}, None), "")
        self.assertEqual(pedir({"servicio_id": "serv-cardio"}, {"dia": "2026-11-26", "hora": "10:00"}), "")

    def test_el_dia_trae_todos_los_centros_de_la_cuenta_aunque_no_tengan_agenda(self):
        hoy = _hoy_art()
        self._seed("40933333", "Paciente", "Tres", _dt_art(hoy, 9, 0), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        body = h.json()
        self.assertEqual(body["centros"], ["Centro A"])
        self.assertEqual(next(g for g in body["grupos"] if g["medico_id"] == "med-1")["centros"], ["Centro A"])

    def test_un_medico_sin_ningun_turno_aparece_igual_con_sus_huecos(self):
        # El caso más útil para la mesa: una agenda entera sin dar.
        hoy = _hoy_art()
        self._dar_agenda("med-2", [{"dias": [self._dia_de_semana_getday(hoy)],
                                    "desde": "10:00", "hasta": "11:00", "cada_min": 30}])
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        g = next(g for g in h.json()["grupos"] if g["medico_id"] == "med-2")
        self.assertEqual(g["turnos"], [])
        self.assertEqual(g["libres"], ["10:00", "10:30"])

    def test_un_dia_cerrado_no_tiene_huecos(self):
        hoy = _hoy_art()
        self._dar_agenda("med-2", [{"dias": [self._dia_de_semana_getday(hoy)],
                                    "desde": "10:00", "hasta": "11:00", "cada_min": 30}],
                         excepciones=[{"fecha": hoy.isoformat(), "tipo": "cerrado"}])
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertFalse(any(g["medico_id"] == "med-2" for g in h.json()["grupos"]))

    def test_un_horario_bloqueado_no_se_ofrece_como_libre(self):
        hoy = _hoy_art()
        self._dar_agenda("med-2", [{"dias": [self._dia_de_semana_getday(hoy)],
                                    "desde": "10:00", "hasta": "11:00", "cada_min": 30}],
                         excepciones=[{"fecha": hoy.isoformat(), "tipo": "bloqueado",
                                       "desde": "10:00", "hasta": "10:30"}])
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        g = next(g for g in h.json()["grupos"] if g["medico_id"] == "med-2")
        self.assertEqual(g["libres"], ["10:30"])

    def test_el_medico_ve_los_huecos_de_su_propia_agenda(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        hoy = _hoy_art()
        self._dar_agenda("med-1", [{"dias": [self._dia_de_semana_getday(hoy)],
                                    "desde": "08:00", "hasta": "09:00", "cada_min": 30}])
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.json()["grupos"][0]["libres"], ["08:00", "08:30"])

    # ---- alcance por centro (2026-07-27) --------------------------------

    def test_una_cuenta_acotada_no_ve_la_jornada_de_otro_centro(self):
        # Gabriel, reunión 2026-07-24: la mesa de un CAPS no tiene por qué ver
        # la de los otros. Sin centros cargados, ve todo (comportamiento viejo).
        hoy = _hoy_art()
        self._seed("40955555", "Paciente", "Propio", _dt_art(hoy, 9), medico_asignado_id="med-1")
        db_operators.operator_create("mesa-otro", "clave1234", "admisionista", centros=["centro-b"])
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("mesa-otro", "admisionista"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["total"], 0)

        db_operators.operator_create("mesa-propia", "clave1234", "admisionista", centros=["centro-a"])
        h2 = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("mesa-propia", "admisionista"))
        self.assertEqual(h2.json()["total"], 1)

    def test_sin_centros_cargados_ve_todo(self):
        hoy = _hoy_art()
        self._seed("40966666", "Paciente", "Uno", _dt_art(hoy, 9), medico_asignado_id="med-1")
        db_operators.operator_create("mesa-libre", "clave1234", "admisionista")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("mesa-libre", "admisionista"))
        self.assertEqual(h.json()["total"], 1)

    # ---- entrevista previa (fase 4, modules/intake/) ---------------------

    def test_turno_con_entrevista_hecha_trae_triage_resumen_estado(self):
        hoy = _hoy_art()
        sid = self._seed("41200000", "Con", "Entrevista", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self._crear_entrevista("med-1", request_id=sid, triage="ROJO", resumen="Dolor de pecho agudo")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertIn("entrevista", t)
        self.assertEqual(t["entrevista"]["triage"], "ROJO")
        self.assertEqual(t["entrevista"]["resumen"], "Dolor de pecho agudo")
        self.assertEqual(t["entrevista"]["estado"], "done")
        self.assertIsInstance(t["entrevista"]["id"], int)

    def test_entrevista_no_aparece_si_esta_deshabilitada_para_el_medico(self):
        # Toggle efectivo en off (intake_admin.enabled_for) — la cola sigue
        # sirviendo el turno, sólo sin el dato auxiliar (best-effort).
        hoy = _hoy_art()
        sid = self._seed("41200001", "Sin", "Entrevista", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self._crear_entrevista("med-1", request_id=sid, triage="VERDE")
        with mock.patch("app.modules.intake.intake_admin.enabled_for", return_value=False):
            h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertNotIn("entrevista", t)

    def test_modulo_intake_apagado_no_rompe_ni_agrega_entrevista(self):
        hoy = _hoy_art()
        sid = self._seed("41200002", "Modulo", "Apagado", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self._crear_entrevista("med-1", request_id=sid, triage="VERDE")
        # Keep the owning turnos router enabled while disabling only the
        # optional intake integration whose best-effort behavior this test
        # covers. A global False correctly makes the real turnos route 404.
        with mock.patch(
            "app.core.app_modules.is_enabled",
            side_effect=lambda module_id: module_id != "intake",
        ):
            h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertNotIn("entrevista", t)

    def test_medico_ve_entrevistas_sin_turno_de_su_propio_medico(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_entrevista("med-1", request_id=None, triage="AMARILLO",
                               resumen="Fiebre hace 2 días",
                               entered_name="Juan", entered_surname="Pérez")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(len(h.json()["entrevistas_sin_turno"]), 1)
        iv = h.json()["entrevistas_sin_turno"][0]
        self.assertEqual(iv["nombre"], "Juan Pérez")
        self.assertEqual(iv["triage"], "AMARILLO")
        self.assertEqual(iv["resumen"], "Fiebre hace 2 días")
        self.assertEqual(iv["estado"], "done")
        self.assertRegex(iv["hora"], r"^\d{2}:\d{2}$")

    def test_las_entrevistas_sin_turno_dejan_registro_de_acceso_y_no_sacan_el_dni(self):
        # Security review 2026-11: the doctor's queue carried the triage and the summary of an unmatched
        # QR interview with no access-log row, and the DNI never travels in the answer.
        from app.core import access_log
        self._crear_operador_medico("dr-fernandez", "med-1")
        iid = self._crear_entrevista("med-1", request_id=None, triage="AMARILLO", resumen="Fiebre")
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("UPDATE interviews SET dni=%s WHERE id=%s", ("41066681", iid))
        access_log._seen.clear()
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(len(h.json()["entrevistas_sin_turno"]), 1)
        self.assertNotIn("_dni", h.json()["entrevistas_sin_turno"][0])
        filas = [r for r in access_log.history("41066681") if r.get("action") == "cola"]
        self.assertEqual([r.get("target_id") for r in filas], ["entrevista:%s" % iid])

    def test_admin_no_recibe_entrevistas_sin_turno(self):
        # Ese campo es sólo del rol médico (ve las suyas); admin/operador ven
        # la cola de TODOS los médicos y no tienen "su" agenda propia.
        hoy = _hoy_art()
        self._seed("41200003", "Paciente", "Trece", _dt_art(hoy, 9), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertNotIn("entrevistas_sin_turno", h.json())

    def test_entrevista_sin_turno_no_matchea_turno_de_otro_medico(self):
        # Regresión de índice: una entrevista sin turno del médico 2 no debe
        # colarse en la cola del médico 1.
        self._crear_operador_medico("dr-fernandez", "med-1")
        self._crear_entrevista("med-2", request_id=None, triage="VERDE")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.json()["entrevistas_sin_turno"], [])

    # ---- "Actualizar entrevista" (2026-08-01) -----------------------------

    def test_turno_con_entrevista_reabierta_trae_actualizada_true(self):
        hoy = _hoy_art()
        sid = self._seed("41200004", "Con", "Actualizacion", _dt_art(hoy, 9), medico_asignado_id="med-1")
        iid = self._crear_entrevista("med-1", request_id=sid, triage="VERDE")
        db_clinical.interview_set_status(iid, "done")
        self.assertIsNotNone(db_clinical.interview_reopen(iid))
        db_clinical.interview_update_analysis(iid, "AMARILLO", "Ahora con más síntomas",
                                     {}, [], [], [], "fake-model")
        db_clinical.interview_set_status(iid, "done")

        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertTrue(t["entrevista"]["actualizada"])

    def test_turno_con_entrevista_nunca_reabierta_trae_actualizada_false(self):
        hoy = _hoy_art()
        sid = self._seed("41200005", "Sin", "Actualizacion", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self._crear_entrevista("med-1", request_id=sid, triage="VERDE")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        t = h.json()["grupos"][0]["turnos"][0]
        self.assertFalse(t["entrevista"]["actualizada"])

    # ---- consulta ya confirmada (problems.md №1, 2026-10-23) --------------

    def _confirmar(self, iid, reemplaza_id=None):
        return db_clinical.consultation_confirm(
            interview_id=iid, doctor_id="med-1", diagnoses=[], exams=[], meds=[],
            notes="", receta_json={}, orden_json={}, receta_token=f"r{iid}-{reemplaza_id}",
            orden_token=f"o{iid}-{reemplaza_id}", created_by="dr-fernandez",
            reemplaza_id=reemplaza_id)

    def test_turno_con_consulta_confirmada_trae_consulta_confirmada(self):
        hoy = _hoy_art()
        sid = self._seed("41200006", "Ya", "Atendido", _dt_art(hoy, 9), medico_asignado_id="med-1")
        iid = self._crear_entrevista("med-1", request_id=sid, triage="VERDE")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertFalse(h.json()["grupos"][0]["turnos"][0]["entrevista"]["consulta_confirmada"])
        primera = self._confirmar(iid)
        self._confirmar(iid, reemplaza_id=primera["id"])
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_admin_headers())
        self.assertTrue(h.json()["grupos"][0]["turnos"][0]["entrevista"]["consulta_confirmada"])

    def test_token_repetido_es_consulta_existente_no_name_error(self):
        """La red de los índices únicos (receta_token/orden_token,
        consultations_vigente_uniq) sale como ConsultationExists(None), que
        consult_admin contesta 409 — no como NameError por un psycopg sin importar."""
        hoy = _hoy_art()
        sid1 = self._seed("41200009", "Token", "Uno", _dt_art(hoy, 9), medico_asignado_id="med-1")
        sid2 = self._seed("41200010", "Token", "Dos", _dt_art(hoy, 10), medico_asignado_id="med-1")
        iid1 = self._crear_entrevista("med-1", request_id=sid1, triage="VERDE")
        iid2 = self._crear_entrevista("med-1", request_id=sid2, triage="VERDE")
        kw = dict(doctor_id="med-1", diagnoses=[], exams=[], meds=[], notes="",
                  receta_json={}, orden_json={}, receta_token="r-dup", created_by="dr-fernandez")
        db_clinical.consultation_confirm(interview_id=iid1, orden_token="o-1", **kw)
        with self.assertRaises(db_clinical.ConsultationExists) as caught:
            db_clinical.consultation_confirm(interview_id=iid2, orden_token="o-2", **kw)
        self.assertIsNone(caught.exception.vigente)
        self.assertIsNone(db_clinical.consultation_current_for_interview(iid2))

    def test_entrevista_sin_turno_con_consulta_confirmada(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        iid = self._crear_entrevista("med-1", request_id=None, triage="VERDE")
        self._confirmar(iid)
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        self.assertTrue(h.json()["entrevistas_sin_turno"][0]["consulta_confirmada"])

    def test_entrevista_sin_turno_reabierta_tambien_trae_actualizada(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        iid = self._crear_entrevista("med-1", request_id=None, triage="VERDE")
        db_clinical.interview_reopen(iid)
        db_clinical.interview_set_status(iid, "done")
        h = _CLIENT.request("GET", "/api/admin/day-queue", headers=_cookie("dr-fernandez", "medico"))
        self.assertTrue(h.json()["entrevistas_sin_turno"][0]["actualizada"])

    def test_token_repetido_es_consulta_existente_no_name_error(self):
        """La red de los índices únicos (receta_token/orden_token,
        consultations_vigente_uniq) sale como ConsultationExists(None), que
        consult_admin contesta 409 — no como NameError por un psycopg sin importar."""
        hoy = _hoy_art()
        sid1 = self._seed("41200009", "Token", "Uno", _dt_art(hoy, 9), medico_asignado_id="med-1")
        sid2 = self._seed("41200010", "Token", "Dos", _dt_art(hoy, 10), medico_asignado_id="med-1")
        iid1 = self._crear_entrevista("med-1", request_id=sid1, triage="VERDE")
        iid2 = self._crear_entrevista("med-1", request_id=sid2, triage="VERDE")
        kw = dict(doctor_id="med-1", diagnoses=[], exams=[], meds=[], notes="",
                  receta_json={}, orden_json={}, receta_token="r-dup", created_by="dr-fernandez")
        db_clinical.consultation_confirm(interview_id=iid1, orden_token="o-1", **kw)
        with self.assertRaises(db_clinical.ConsultationExists) as caught:
            db_clinical.consultation_confirm(interview_id=iid2, orden_token="o-2", **kw)
        self.assertIsNone(caught.exception.vigente)
        self.assertIsNone(db_clinical.consultation_current_for_interview(iid2))

    # ---- /api/admin/day-queue/changes (la extensión de HSI de la mesa) ----

    def _cambios(self, ids, headers=None):
        h = _CLIENT.request("GET", "/api/admin/day-queue/changes?ids=" + ",".join(str(i) for i in ids),
                            headers=headers or _admin_headers())
        self.assertEqual(h.status_code, 200)
        return {c["id"]: c for c in h.json()["cambios"]}

    def test_cambios_una_cancelada_conserva_el_turno_que_tenia(self):
        # La cola de «Pacientes de hoy» ya no la lista; la extensión pregunta por su id y con el
        # paciente, el día, la hora y el médico que tenía encuentra el turno en HSI para cancelarlo.
        hoy = _hoy_art()
        sid = self._seed("41066661", "Paciente", "Cancelada", _dt_art(hoy, 9), medico_asignado_id="med-1")
        self.assertEqual(self._cambios([sid])[sid]["estado"], "aprobada")
        db_booking.cancel(sid, actor="paciente")
        c = self._cambios([sid])[sid]
        self.assertEqual(c["estado"], "cancelada")
        self.assertEqual((c["turno"]["dni"], c["turno"]["hora"]), ("41066661", "09:00"))
        self.assertEqual(c["turno"]["dia_iso"], hoy.isoformat())
        self.assertTrue(c["turno"]["medico"])

    def test_cambios_un_turno_movido_trae_la_fila_nueva(self):
        hoy = _hoy_art()
        sid = self._seed("41066662", "Paciente", "Movida", _dt_art(hoy, 9), medico_asignado_id="med-1")
        db_booking.reassign_appointment(sid, _dt_art(hoy, 11), actor="paciente")
        t = self._cambios([sid])[sid]["turno"]
        self.assertEqual(t["hora"], "11:00")
        self.assertEqual(t["dni"], "41066662")
        self.assertEqual(t["hsi_institucion"], None)

    def test_cambios_trae_la_asistencia_marcada(self):
        hoy = _hoy_art()
        sid = self._seed("41066663", "Paciente", "Presente", _dt_art(hoy, 9), medico_asignado_id="med-1",
                         asistencia="presente")
        self.assertEqual(self._cambios([sid])[sid]["turno"]["asistencia"], "presente")

    def test_cambios_un_turno_que_el_journal_ya_vinculo_no_se_le_ofrece_a_la_extension(self):
        # El journal del servidor es el driver de un turno que ya vincula (hsi_appointment_id)
        # cuando hay un conector EMR elegido: la extensión no debe ofrecer repetir en HSI lo que
        # el servidor ya hace solo (cancelar, mover, presente). Sin `turno` en la respuesta, la
        # extensión lo ignora; sin conector, el turno sigue siendo de la extensión.
        hoy = _hoy_art()
        sid = self._seed("41066671", "Paciente", "Vinculada", _dt_art(hoy, 9), medico_asignado_id="med-1")
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("UPDATE booking_requests SET hsi_appointment_id='900' WHERE id=%s", (sid,))
        self.assertIsNotNone(self._cambios([sid])[sid]["turno"])
        from app.modules.turnos import booking_requests
        with mock.patch.object(booking_requests, "_emr_sync_enabled", return_value=True):
            cambio = self._cambios([sid])[sid]
        self.assertEqual(cambio["estado"], "aprobada")
        self.assertIsNone(cambio["turno"])

    def test_cambios_con_la_sincronizacion_de_hsi_apagada_el_turno_sigue_siendo_de_la_extension(self):
        hoy = _hoy_art()
        sid = self._seed("41066673", "Paciente", "Apagada", _dt_art(hoy, 9), medico_asignado_id="med-1")
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("UPDATE booking_requests SET hsi_appointment_id='902' WHERE id=%s", (sid,))
        from app.modules.turnos import booking_requests
        with mock.patch.object(booking_requests, "_emr_sync_enabled", return_value=True), \
                _env.patched_env(TURNOS_HSI_SYNC_OFF="1"):
            self.assertIsNotNone(self._cambios([sid])[sid]["turno"])

    def test_cambios_deja_registro_de_acceso_tambien_del_turno_que_conduce_el_servidor(self):
        # El `estado` y el `oculto` de un turno que el journal conduce salen en la respuesta aunque
        # no lleve `turno`: leer eso también es un acceso a datos del paciente (Ley 26.529).
        from app.core import access_log
        hoy = _hoy_art()
        sid = self._seed("41066672", "Paciente", "Conducida", _dt_art(hoy, 9), medico_asignado_id="med-1")
        import psycopg
        with psycopg.connect(_TEST_URL, connect_timeout=5) as c, c.cursor() as cur:
            cur.execute("UPDATE booking_requests SET hsi_appointment_id='901' WHERE id=%s", (sid,))
        from app.modules.turnos import booking_requests
        with mock.patch.object(booking_requests, "_emr_sync_enabled", return_value=True):
            cambio = self._cambios([sid])[sid]
        self.assertIsNone(cambio["turno"])
        self.assertEqual(cambio["estado"], "aprobada")
        self.assertTrue([r for r in access_log.history("41066672") if r.get("action") == "cola"])

    def test_cambios_un_id_que_no_existe_o_repetido_se_contesta_una_vez(self):
        hoy = _hoy_art()
        sid = self._seed("41066664", "Paciente", "Uno", _dt_art(hoy, 9), medico_asignado_id="med-1")
        h = _CLIENT.request("GET", "/api/admin/day-queue/changes?ids=%s,%s,99999,abc" % (sid, sid),
                            headers=_admin_headers())
        self.assertEqual([c["id"] for c in h.json()["cambios"]], [sid, 99999])
        self.assertIsNone(h.json()["cambios"][1]["estado"])

    def test_cambios_exige_sesion_de_la_mesa(self):
        self.assertEqual(_CLIENT.request("GET", "/api/admin/day-queue/changes?ids=1").status_code, 401)

    def test_cambios_el_medico_solo_ve_sus_pacientes(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        hoy = _hoy_art()
        propio = self._seed("41066665", "Paciente", "Propio", _dt_art(hoy, 9), medico_asignado_id="med-1")
        ajeno = self._seed("41066666", "Paciente", "Ajeno", _dt_art(hoy, 10), medico_asignado_id="med-2")
        c = self._cambios([propio, ajeno], headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(c[propio]["estado"], "aprobada")
        self.assertIsNone(c[ajeno]["estado"])
        self.assertIsNone(c[ajeno]["turno"])

    # «Ocultar» en el panel de cambios de la extensión (2026-11-05): un turno cancelado que HSI ya
    # no deja cancelar (agenda cerrada, turno pasado) quedaba en el panel de la mesa sin forma de
    # sacarlo. La extensión deja fuera lo que ya pasó con la hora de la instalación, y lo que HSI
    # rechaza se oculta para toda la mesa, con quién y cuándo en el historial del turno.

    def _ocultar(self, sid, headers=None):
        return _CLIENT.request("POST", "/api/admin/day-queue/changes/%s/hide" % sid,
                               headers=_admin_headers() if headers is None else headers)

    def _ocultados(self, sid):
        return [e for e in db_booking.appointment_events(sid) if e["kind"] == "hsi_oculto"]

    def test_cambios_dice_que_hora_es_en_la_instalacion(self):
        # 12:49 UTC son las 09:49 en Buenos Aires: la PC de la mesa puede tener otra hora.
        _clock.freeze(self, "2026-11-05T12:49:00Z")
        h = _CLIENT.request("GET", "/api/admin/day-queue/changes?ids=1", headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["ahora"], "2026-11-05 09:49")

    def test_cambios_ocultar_vale_para_toda_la_mesa_y_queda_en_el_historial(self):
        dia = _hoy_art() + timedelta(days=3)
        sid = self._seed("41066667", "Paciente", "Oculta", _dt_art(dia, 9), medico_asignado_id="med-1")
        db_booking.cancel(sid, actor="paciente")
        self.assertIs(self._cambios([sid])[sid]["oculto"], False)
        h = self._ocultar(sid, headers=_operador_headers("operadora-1"))
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json(), {"ok": True})
        # Otra persona de la mesa, en otra PC, tampoco lo ve.
        self.assertIs(self._cambios([sid], headers=_operador_headers("operadora-2"))[sid]["oculto"], True)
        ev = self._ocultados(sid)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["actor"], "operadora-1")
        self.assertEqual((ev[0]["status_from"], ev[0]["status_to"]), ("cancelada", "cancelada"))
        self.assertEqual(ev[0]["appointment_to"], _dt_art(dia, 9))
        # El turno sigue cancelado: ocultar no cambia nada más.
        self.assertEqual(db_booking.get_booking_request(sid)["estado"], "cancelada")

    def test_cambios_ocultar_dura_hasta_el_proximo_cambio_del_turno(self):
        dia = _hoy_art() + timedelta(days=2)
        sid = self._seed("41066668", "Paciente", "Movida", _dt_art(dia, 9), medico_asignado_id="med-1")
        db_booking.reassign_appointment(sid, _dt_art(dia, 11), actor="paciente")
        self.assertEqual(self._ocultar(sid).status_code, 200)
        self.assertIs(self._cambios([sid])[sid]["oculto"], True)
        # Se mueve de nuevo: es otro cambio para HSI y vuelve al panel.
        db_booking.reassign_appointment(sid, _dt_art(dia, 12), actor="paciente")
        self.assertIs(self._cambios([sid])[sid]["oculto"], False)

    def test_cambios_ocultar_respeta_el_alcance_de_la_cuenta(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        dia = _hoy_art() + timedelta(days=1)
        ajeno = self._seed("41066669", "Paciente", "Ajeno", _dt_art(dia, 10), medico_asignado_id="med-2")
        self.assertEqual(self._ocultar(ajeno, headers=_cookie("dr-fernandez", "medico")).status_code, 404)
        self.assertEqual(self._ocultar(999999).status_code, 404)
        self.assertEqual(self._ocultar(ajeno, headers={}).status_code, 401)
        self.assertEqual(self._ocultados(ajeno), [])


class AsistenciaMedicoTestCase(unittest.TestCase):
    """modules.turnos.booking_decisions._mark_attendance_core abierto a rol médico (decisión #5)."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        self._tmp = tempfile.mkdtemp(prefix="cola-dia-asistencia-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        self.addCleanup(_reset_catalog)   # registered first, so it runs after the environment is put back
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        _reset_catalog()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_tables()

    def _crear_operador_medico(self, usuario, medico_id):
        return db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)

    def _seed(self, dni, turno_dt, medico_asignado_id=None):
        sid = db_booking.create_booking_request(dni, "Paciente", "Prueba", "1980-01-01", "wa:11", "serv-cardio", "centro-a")
        # sobreturno=True: este helper se llama varias veces con el MISMO
        # (medico_asignado_id, turno_dt) para sembrar pacientes sintéticos
        # distintos en la cola de un día — desde booking_requests_doctor_slot_uniq
        # (2026-08-05) esa combinación exige la misma exención que un sobreturno real.
        db_booking.approve(sid, turno_dt, medico_asignado_id=medico_asignado_id,
                  resuelta_por="admin", sobreturno=True)
        return sid

    def _post_asistencia(self, sid, asistencia, headers):
        h = _CLIENT.request("POST", "/api/admin/requests/%s/attendance" % sid, json={"asistencia": asistencia}, headers=headers)
        return h

    def test_medico_marca_su_propio_turno_de_hoy(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        sid = self._seed("41111111", _dt_art(_hoy_art(), 9), medico_asignado_id="med-1")
        h = self._post_asistencia(sid, "presente", _cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["solicitud"]["asistencia"], "presente")

    def test_medico_404_en_turno_ajeno(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        sid = self._seed("41122222", _dt_art(_hoy_art(), 9), medico_asignado_id="med-2")
        h = self._post_asistencia(sid, "ausente", _cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 404)

    def test_medico_futuro_409(self):
        self._crear_operador_medico("dr-fernandez", "med-1")
        futuro = _hoy_art() + timedelta(days=1)
        sid = self._seed("41133333", _dt_art(futuro, 9), medico_asignado_id="med-1")
        h = self._post_asistencia(sid, "ausente", _cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 409)
        self.assertEqual(h.json()["error"], "asistencia_no_elegible")

    def test_medico_sin_medico_id_404(self):
        db_operators.operator_create("dr-sin-medico", "clave1234", "medico")  # sin medico_id: el rol REAL de la cuenta manda sobre el de la cookie
        sid = self._seed("41144444", _dt_art(_hoy_art(), 9))
        h = self._post_asistencia(sid, "presente", _cookie("dr-sin-medico", "medico"))
        self.assertEqual(h.status_code, 404)

    def test_admin_y_operador_siguen_funcionando(self):
        sid = self._seed("41155555", _dt_art(_hoy_art(), 9), medico_asignado_id="med-1")
        h = self._post_asistencia(sid, "presente", _admin_headers())
        self.assertEqual(h.status_code, 200, h.json())

        sid2 = self._seed("41166666", _dt_art(_hoy_art(), 9), medico_asignado_id="med-1")
        h2 = self._post_asistencia(sid2, "ausente", _operador_headers())
        self.assertEqual(h2.status_code, 200, h2.json())

    def test_admisionista_marca_cualquier_turno_del_dia(self):
        # Reunión hospital 2026-07-24: la mesa de admisión "solamente recibe y
        # da presentes" — cualquier turno del día, sin el ownership del médico.
        sid = self._seed("41177777", _dt_art(_hoy_art(), 9), medico_asignado_id="med-2")
        h = self._post_asistencia(sid, "presente", _cookie("mesa-1", "admisionista"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["solicitud"]["asistencia"], "presente")


if __name__ == "__main__":
    unittest.main()
