"""test_medico_por_id.py — el médico de una solicitud es UNO: id + nombre del
catálogo, siempre juntos (problems.md №3, 2026-10-23).

Antes la aprobación guardaba nombre e id con dos COALESCE sueltos (un nombre
fuera del catálogo dejaba el id del médico ANTERIOR), HSI ponía el nombre y
anulaba el id (la fila desaparecía de «Mis pacientes» y salía del índice
único médico+horario) y la ocupación se comparaba por nombre exacto. Acá se
pinea la regla decidida por el hospital: la fuente de verdad es el id; el
nombre es el del catálogo; un nombre que no resuelve a UN médico queda con id
NULL, nunca con el id de otro.

Correr con discover (ver CONTRIBUTING.md), base propia
`sana_turnos_test_medico_por_id`.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_booking  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import config  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.modules.turnos import booking_capacity  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_queue  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402
from app.modules.turnos import doctor_appointments  # noqa: E402
from app.patient_web import catalog  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_medico_por_id")
HAVE_DB, DB_SKIP_REASON, _TEST_URL = False, None, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON, _TEST_URL = _db.have_db, _db.skip_reason, _db.test_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE catalog")


def _sql(query, params=()):
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute(query, params)


_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS = {"servicios": [
    {"id": "serv-cardio", "nombre": "Cardiología", "centro": "centro-a",
     "modo": "solicitud", "cluster": "cardiologia"},
]}
_MED_1 = {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología",
          "centro": "centro-a", "cluster": "cardiologia"}
_MED_2 = {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Cardiología",
          "centro": "centro-a", "cluster": "cardiologia"}
# Dos «Pérez»: «Pérez» solo es ambiguo (grupo 3 del backfill).
_MED_3 = {"id": "med-3", "nombre": "Ana Pérez", "especialidad": "Cardiología",
          "centro": "centro-a", "cluster": "cardiologia"}
_MED_4 = {"id": "med-4", "nombre": "Luis Pérez", "especialidad": "Cardiología",
          "centro": "centro-a", "cluster": "cardiologia"}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    for fname, data in (("centros-salud.json", _CENTROS), ("servicios.json", _SERVICIOS),
                        ("medicos.json", {"medicos": [_MED_1, _MED_2, _MED_3, _MED_4]})):
        with open(os.path.join(webdir, fname), "w", encoding="utf-8") as f:
            json.dump(data, f)


def _futuro(horas=48):
    return (datetime.now(timezone.utc) + timedelta(hours=horas)).replace(minute=0, second=0, microsecond=0)


class MedicoPorIdTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self._tmp = tempfile.mkdtemp(prefix="medico-por-id-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_tables()

    def _pendiente(self, dni="40111111"):
        return db_booking.create_booking_request(dni, "Paciente", "Prueba", "1980-01-01", "wa:11",
                                         "serv-cardio", "centro-a")

    # ---- escritura: nombre e id juntos ---------------------------------

    def test_aprobar_con_nombre_no_resoluble_no_conserva_el_id_de_otro_medico(self):
        sid = self._pendiente()
        db_booking.assign_pending_booking_request(sid, "Carlos Gómez", "med-2")
        booking_decisions._persist_approval(sid, _futuro(), medico="Dr. Nadie Conocido",
                                           resuelta_por="admin")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["medico_asignado"], "Dr. Nadie Conocido")
        # Antes: quedaba "med-2" — la fila decía «Dr. Nadie» y era de Gómez.
        self.assertIsNone(row["medico_asignado_id"])

    def test_aprobar_con_nombre_del_catalogo_guarda_id_y_nombre_del_catalogo(self):
        sid = self._pendiente()
        booking_decisions._persist_approval(sid, _futuro(), medico="Dra. FERNANDEZ Maria Jose",
                                           resuelta_por="admin")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["medico_asignado_id"], "med-1")
        self.assertEqual(row["medico_asignado"], "María José Fernández")

    def test_db_approve_pisa_el_par_entero(self):
        sid = self._pendiente()
        db_booking.assign_pending_booking_request(sid, "Carlos Gómez", "med-2")
        db_booking.approve(sid, _futuro(), medico_asignado="Otro Nombre", medico_asignado_id=None,
                   resuelta_por="admin")
        row = db_booking.get_booking_request(sid)
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Otro Nombre", None))

    def test_db_approve_sin_medico_deja_el_par_como_estaba(self):
        sid = self._pendiente()
        db_booking.assign_pending_booking_request(sid, "Carlos Gómez", "med-2")
        db_booking.approve(sid, _futuro(), resuelta_por="admin")
        row = db_booking.get_booking_request(sid)
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Carlos Gómez", "med-2"))

    def test_hsi_asigna_un_nombre_del_catalogo_con_su_id_y_mis_pacientes_lo_ve(self):
        from app.connectors.hsi import hsi_resolver
        sid = self._pendiente()
        db_booking.approve(sid, _futuro(), medico_asignado="Carlos Gómez", medico_asignado_id="med-2",
                   resuelta_por="admin")
        hsi_resolver._asignar_profesional(sid, "FERNANDEZ, MARIA JOSE")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["medico_asignado_id"], "med-1")
        self.assertEqual(row["medico_asignado"], "María José Fernández")
        self.assertIn(sid, [r["id"] for r in db_clinical.patients_of_doctor("med-1")])
        self.assertNotIn(sid, [r["id"] for r in db_clinical.patients_of_doctor("med-2")])

    def test_hsi_asigna_un_nombre_fuera_del_catalogo_tal_cual_y_sin_id(self):
        from app.connectors.hsi import hsi_resolver
        sid = self._pendiente()
        db_booking.approve(sid, _futuro(), medico_asignado="Carlos Gómez", medico_asignado_id="med-2",
                   resuelta_por="admin")
        hsi_resolver._asignar_profesional(sid, "Dr. Externo Hsi", sobreturno=True)
        row = db_booking.get_booking_request(sid)
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Dr. Externo Hsi", None))
        self.assertTrue(row["sobreturno"])

    # ---- lectura: el médico se identifica por id -----------------------

    def test_ocupacion_del_horario_por_id_dos_nombres_del_mismo_medico_chocan(self):
        cuando = _futuro()
        sid = self._pendiente()
        db_booking.approve(sid, cuando, medico_asignado="María José Fernández", medico_asignado_id="med-1",
                   resuelta_por="admin")
        # Otro texto, mismo médico del catálogo → ocupado.
        self.assertTrue(booking_capacity._doctor_appointment_busy("Dra. Fernández María José", cuando))
        self.assertTrue(booking_capacity._doctor_appointment_busy("cualquiera", cuando, medico_id="med-1"))
        # Mismo minuto, otro médico → libre.
        self.assertFalse(booking_capacity._doctor_appointment_busy("Carlos Gómez", cuando))

    def test_ocupacion_no_confunde_un_nombre_viejo_con_el_id_de_otro(self):
        cuando = _futuro()
        sid = self._pendiente()
        db_booking.approve(sid, cuando, medico_asignado="Carlos Gómez", medico_asignado_id="med-2",
                   resuelta_por="admin")
        # Fila desincronizada (dato viejo): dice «Gómez» pero es de med-1.
        _sql("UPDATE booking_requests SET assigned_doctor_id='med-1' WHERE id=%s", (sid,))
        self.assertTrue(booking_capacity._doctor_appointment_busy("María José Fernández", cuando))
        self.assertFalse(booking_capacity._doctor_appointment_busy("Carlos Gómez", cuando))

    def test_horarios_ocupados_del_sugerido_por_id_aunque_el_nombre_guardado_sea_otro(self):
        sid = self._pendiente()
        db_booking.approve(sid, _futuro(), medico_asignado="Nombre Viejo", medico_asignado_id="med-1",
                   resuelta_por="admin")
        mapa = booking_queue._approved_by_doctor_art()
        self.assertEqual(len(booking_queue._doctor_busy_slots(mapa, _MED_1)), 1)
        self.assertEqual(len(booking_queue._doctor_busy_slots(mapa, _MED_2)), 0)

    def test_count_appointments_of_por_id_y_nombre_solo_para_filas_sin_id(self):
        a = self._pendiente("40111111")
        db_booking.approve(a, _futuro(), medico_asignado="María José Fernández", medico_asignado_id="med-2",
                   resuelta_por="admin")   # desincronizada: el nombre no manda
        b = self._pendiente("40222222")
        db_booking.approve(b, _futuro(50), medico_asignado="María José Fernández", resuelta_por="admin")
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1", "María José Fernández"), 1)
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-2", "Carlos Gómez"), 1)

    # ---- renombrar un médico ------------------------------------------

    def test_renombrar_al_medico_actualiza_sus_filas_y_siguen_siendo_suyas(self):
        sid = self._pendiente()
        db_booking.approve(sid, _futuro(), medico_asignado="María José Fernández", medico_asignado_id="med-1",
                   resuelta_por="admin")
        db_catalog.catalog_upsert("medicos", "med-1", dict(_MED_1, nombre="María José Fernández de Paz"))
        catalog.invalidate()
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["medico_asignado"], "María José Fernández de Paz")
        self.assertIn(sid, [r["id"] for r in doctor_appointments.appointments_of(
            "med-1", "María José Fernández de Paz")])
        self.assertEqual(db_slots.count_appointments_of("medicos", "med-1", "María José Fernández de Paz"), 1)

    def test_lo_que_se_muestra_es_el_nombre_del_catalogo_por_id(self):
        sid = self._pendiente()
        db_booking.approve(sid, _futuro(), medico_asignado="María José Fernández", medico_asignado_id="med-1",
                   resuelta_por="admin")
        # Un pack nuevo renombró al médico sin pasar por el panel: la fila
        # guarda el nombre viejo, pero lo que ve el paciente es el del catálogo.
        _sql("UPDATE booking_requests SET assigned_doctor='Nombre Viejo' WHERE id=%s", (sid,))
        out = booking_requests._public_row(db_booking.get_booking_request(sid))
        self.assertEqual(out["medico_asignado"], "María José Fernández")
        from app.notifications import send
        self.assertEqual(send._doctor_name(db_booking.get_booking_request(sid)), "María José Fernández")

    def test_un_medico_deshabilitado_conserva_su_id(self):
        # Deshabilitar = borrado en el overlay del catálogo: el médico sale de
        # las listas públicas, pero sus solicitudes siguen siendo suyas
        # (2bd548d2 ya las nombra por id con los deshabilitados incluidos).
        db_catalog.catalog_upsert("medicos", "med-2", dict(_MED_2), borrado=True)
        catalog.invalidate()
        sid = self._pendiente()
        booking_decisions._persist_approval(sid, _futuro(), medico="Gomez", medico_id="med-2",
                                           resuelta_por="admin")
        row = db_booking.get_booking_request(sid)
        self.assertEqual((row["medico_asignado"], row["medico_asignado_id"]), ("Carlos Gómez", "med-2"))
        _sql("UPDATE booking_requests SET assigned_doctor='Gomez' WHERE id=%s", (sid,))
        self.assertEqual(doctor_appointments.display_name(db_booking.get_booking_request(sid)), "Carlos Gómez")
        # Una fila vieja con sólo el nombre de ese médico deshabilitado: el
        # backfill la completa (UN deshabilitado la nombra).
        vieja = self._pendiente("40222222")
        db_booking.approve(vieja, _futuro(30), resuelta_por="admin")
        _sql("UPDATE booking_requests SET assigned_doctor='Carlos Gomez', assigned_doctor_id=NULL "
             "WHERE id=%s", (vieja,))
        out = self._correr()
        self.assertEqual([f["id"] for f in out["corregir_nombre"]], [sid])
        self.assertEqual([(f["id"], f["medico_id"]) for f in out["completar_id"]], [(vieja, "med-2")])
        self.assertEqual(out["a_mano"], [])
        self._correr("--apply")
        r = db_booking.get_booking_request(vieja)
        self.assertEqual((r["medico_asignado"], r["medico_asignado_id"]), ("Carlos Gómez", "med-2"))
        self.assertEqual(self._correr()["completar_id"], [])

    # ---- backfill ------------------------------------------------------

    def _sembrar_backfill(self):
        def fila(dni, nombre, mid, horas):
            sid = self._pendiente(dni)
            db_booking.approve(sid, _futuro(horas), resuelta_por="admin")
            _sql("UPDATE booking_requests SET assigned_doctor=%s, assigned_doctor_id=%s WHERE id=%s",
                 (nombre, mid, sid))
            return sid
        return {
            "completa_id": fila("40000001", "Dra. Fernandez Maria Jose", None, 30),
            "corrige_nombre": fila("40000002", "Gomez", "med-2", 31),
            "ambigua": fila("40000003", "Pérez", None, 32),
            "sin_match": fila("40000004", "Dr. Nadie", None, 33),
            "id_desconocido": fila("40000005", "Alguien", "med-borrado", 34),
            "ok": fila("40000006", "Carlos Gómez", "med-2", 35),
        }

    def _correr(self, *args):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "medico_por_id", os.path.join(ROOT, "scripts", "medico_por_id.py"))
        medico_por_id = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(medico_por_id)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = medico_por_id.main(["--json", *args])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def test_backfill_dry_run_reporta_y_no_escribe(self):
        ids = self._sembrar_backfill()
        antes = {k: dict(db_booking.get_booking_request(v)) for k, v in ids.items()}
        out = self._correr()
        self.assertFalse(out["aplicado"])
        self.assertEqual([f["id"] for f in out["completar_id"]], [ids["completa_id"]])
        self.assertEqual([f["id"] for f in out["corregir_nombre"]], [ids["corrige_nombre"]])
        self.assertEqual(sorted(f["id"] for f in out["a_mano"]),
                         sorted([ids["ambigua"], ids["sin_match"], ids["id_desconocido"]]))
        # Sin PII: sólo ids, ids de médico y motivos.
        self.assertNotIn("40000001", json.dumps(out))
        for k, v in ids.items():
            despues = db_booking.get_booking_request(v)
            self.assertEqual((despues["medico_asignado"], despues["medico_asignado_id"]),
                             (antes[k]["medico_asignado"], antes[k]["medico_asignado_id"]))

    def test_backfill_apply_corrige_grupos_1_y_2_y_deja_el_3(self):
        ids = self._sembrar_backfill()
        out = self._correr("--apply")
        self.assertTrue(out["aplicado"])
        r = db_booking.get_booking_request(ids["completa_id"])
        self.assertEqual((r["medico_asignado"], r["medico_asignado_id"]), ("María José Fernández", "med-1"))
        r = db_booking.get_booking_request(ids["corrige_nombre"])
        self.assertEqual((r["medico_asignado"], r["medico_asignado_id"]), ("Carlos Gómez", "med-2"))
        for k, nombre, mid in (("ambigua", "Pérez", None), ("sin_match", "Dr. Nadie", None),
                               ("id_desconocido", "Alguien", "med-borrado")):
            r = db_booking.get_booking_request(ids[k])
            self.assertEqual((r["medico_asignado"], r["medico_asignado_id"]), (nombre, mid))
        # Idempotente: una segunda corrida ya no encuentra nada que corregir.
        otra = self._correr()
        self.assertEqual((otra["completar_id"], otra["corregir_nombre"]), ([], []))


    # ---- turnos web sin médico (contraparte técnica, 2026-10-24) -----------------------
    #
    # La confirmación automática aprobaba sin guardar al médico del horario que
    # el paciente había elegido (`slot_doctor_id`): el turno salía en «Sin
    # médico asignado». scripts/medico_de_web.py completa los futuros ya
    # escritos con la misma regla del camino corregido.

    def _sembrar_web(self):
        def fila(dni, resuelta_por, slot_medico, medico=None, medico_id=None, horas=48):
            sid = self._pendiente(dni)
            db_booking.approve(sid, _futuro(horas), medico_asignado=medico, medico_asignado_id=medico_id,
                       resuelta_por=resuelta_por)
            _sql("UPDATE booking_requests SET slot_doctor_id=%s WHERE id=%s", (slot_medico, sid))
            return sid
        return {
            "completa": fila("40100001", "auto:web", "med-1", horas=48),
            "id_desconocido": fila("40100002", "auto:web", "med-borrado", horas=49),
            "sin_medico_en_el_horario": fila("40100003", "auto:web", None, horas=50),
            "de_la_mesa": fila("40100004", "mesa:alguien", "med-2", horas=51),
            "ya_con_medico": fila("40100005", "auto:web", "med-1", "Carlos Gómez", "med-2", horas=52),
            "pasado": fila("40100006", "auto:web", "med-1", horas=-48),
        }

    def _correr_web(self, *args):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "medico_de_web", os.path.join(ROOT, "scripts", "medico_de_web.py"))
        medico_de_web = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(medico_de_web)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = medico_de_web.main(["--json", *args])
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue())

    def test_medico_de_web_dry_run_no_escribe_y_trae_la_copia(self):
        ids = self._sembrar_web()
        out = self._correr_web()
        self.assertFalse(out["aplicado"])
        self.assertEqual([c["id"] for c in out["completar"]], [ids["completa"]])
        self.assertEqual([c["id"] for c in out["a_mano"]], [ids["id_desconocido"]])
        # La copia lleva sólo las dos filas elegibles, sin datos del paciente.
        self.assertEqual(sorted(c["id"] for c in out["copia"]), sorted([ids["completa"], ids["id_desconocido"]]))
        self.assertTrue(all("dni" not in c and "first_name" not in c for c in out["copia"]))
        r = db_booking.get_booking_request(ids["completa"])
        self.assertFalse(r["medico_asignado"] or r["medico_asignado_id"])

    def test_medico_de_web_apply_completa_solo_lo_elegible_y_la_repeticion_queda_vacia(self):
        ids = self._sembrar_web()
        out = self._correr_web("--apply")
        self.assertEqual(out["escritas"], 1)
        r = db_booking.get_booking_request(ids["completa"])
        self.assertEqual((r["medico_asignado"], r["medico_asignado_id"]), ("María José Fernández", "med-1"))
        for k in ("id_desconocido", "sin_medico_en_el_horario", "de_la_mesa", "pasado"):
            r = db_booking.get_booking_request(ids[k])
            self.assertFalse(r["medico_asignado"] or r["medico_asignado_id"], k)
        r = db_booking.get_booking_request(ids["ya_con_medico"])
        self.assertEqual((r["medico_asignado"], r["medico_asignado_id"]), ("Carlos Gómez", "med-2"))
        otra = self._correr_web("--apply")
        self.assertEqual((otra["escritas"], otra["completar"]), (0, []))


if __name__ == "__main__":
    unittest.main()
