"""test_chart_summary.py — «Resumen del paciente» (chart_summary.py) y los
conectores clinical_history_lookup (alepo/marqui, 2026-08-01).

Cubre: los DOS modos de cada conector (fake con datos de muestra + plantilla
determinística para DNI desconocido; real = ProviderUnavailable honesto), el
registro (manifiestos válidos, adapter conforme al ABC), la extracción de
series POR CÓDIGO (nunca de la IA), la generación determinística persistida
con fingerprint (regenera sólo cuando las fuentes cambian), el gate HTTP
(medico usa siempre su propio id) y el batch de pre-generación (tick).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_chart_summary -v
"""

import os
import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta

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
from app.auth import admin_auth       # noqa: E402
from app.ai import chart_summary  # noqa: E402
from app.ai import chart_summary_router  # noqa: E402
from app.patient_web import catalog          # noqa: E402
from app import connectors       # noqa: E402
from app.auth import turnstile        # noqa: E402
from app.core import config  # noqa: E402
from app.core import tz  # noqa: E402
from app.connectors.alepo import alepo    # noqa: E402
from app.connectors.marqui import marqui  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

_ART = tz.tzinfo()

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_chartsummary", seed=lambda: db_operators.operators_list())

# DNI con historia curada en sample_historias.json (Sofía Herrera, la
# paciente insignia) y uno que NO está (cae en la plantilla determinística).
_DNI_RICO = "99000001"
_DNI_DESCONOCIDO = "45678901"


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
            cur.execute("TRUNCATE patient_summaries RESTART IDENTITY")
            cur.execute("SELECT to_regclass('clinical_access_log') IS NOT NULL AS hay")
            if cur.fetchone()[0]:
                cur.execute("TRUNCATE clinical_access_log RESTART IDENTITY")


_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS = {"servicios": [
    {"id": "serv-cardio", "nombre": "Cardiología", "centro": "centro-a", "modo": "solicitud", "cluster": "cardiologia"},
]}
_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología", "centro": "centro-a"},
    {"id": "med-2", "nombre": "Carlos Gómez", "especialidad": "Pediatría", "centro": "centro-a"},
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


def _mount_summary_router():
    # HANDLER RETIREMENT (2026-08-30): el transporte legacy `handle()` fue
    # eliminado de chart_summary; los casos del gate HTTP pegan al router
    # nativo chart_summary_router (montaje mínimo, mismo patrón que
    # test_ai_native_routers.py) con los valores exactos de antes.
    app = FastAPI()
    app.include_router(chart_summary_router.router)
    return TestClient(app)


class FakeEnvMixin:
    """Prende los fakes de alepo/marqui sólo durante el test (el fixture los
    limpia) — el entorno es global al proceso y las otras suites no deben heredarlo."""

    _KEYS = ("TURNOS_FAKE_ALEPO", "TURNOS_FAKE_MARQUI")

    def _fake_on(self):
        _env.set_env(self, dict.fromkeys(self._KEYS, "1"))

    def _fake_off(self):
        _env.set_env(self, dict.fromkeys(self._KEYS))


class ConnectorsTestCase(FakeEnvMixin, unittest.TestCase):
    def setUp(self):
        self._fake_on()

    def test_manifiestos_y_abc(self):
        for cid, mod in (("alepo", alepo), ("marqui", marqui)):
            m = connectors.load_manifest(cid)
            self.assertEqual(m["id"], cid)
            self.assertEqual(m["capability"], connectors.CLINICAL_HISTORY_LOOKUP)
            self.assertIn(f"TURNOS_FAKE_{cid.upper()}", m["config_keys"])

    def test_fake_sirve_paciente_curado(self):
        filas = alepo.lookup_by_dni(_DNI_RICO)
        self.assertTrue(filas)
        self.assertTrue(all(f.get("sistema") == "alepo" for f in filas))
        fechas = [f["fecha"] for f in filas]
        self.assertEqual(fechas, sorted(fechas))
        # Alepo cubre 2020-2024: nada fuera de esa era.
        self.assertTrue(all("2020" <= f[:4] <= "2024" for f in fechas))
        viejas = marqui.lookup_by_dni(_DNI_RICO)
        self.assertTrue(viejas)
        self.assertTrue(all(f["fecha"][:4] <= "2016" for f in viejas))

    def test_fake_dni_desconocido_cae_en_plantilla_deterministica(self):
        una = alepo.lookup_by_dni(_DNI_DESCONOCIDO)
        dos = alepo.lookup_by_dni(_DNI_DESCONOCIDO)
        self.assertTrue(una)
        self.assertEqual(una, dos)

    def test_real_mode_es_provider_unavailable(self):
        self._fake_off()
        with self.assertRaises(connectors.ProviderUnavailable):
            alepo.lookup_by_dni(_DNI_RICO)
        with self.assertRaises(connectors.ProviderUnavailable):
            marqui.lookup_by_dni(_DNI_RICO)

    def test_adapter_conforme(self):
        from app.connectors.alepo.adapter import AlepoConnector
        filas = AlepoConnector().lookup_by_dni(_DNI_RICO)
        self.assertTrue(filas)


class SeriesTestCase(unittest.TestCase):
    def test_series_extraidas_por_codigo(self):
        historia = [
            {"fecha": "2020-01-01", "valores": {"glucemia_mg_dl": 100}},
            {"fecha": "2021-01-01", "valores": {"glucemia_mg_dl": 118}},
            {"fecha": "2022-01-01", "valores": {"glucemia_mg_dl": 130,
                                                "hba1c_pct": 6.1}},
            {"fecha": "2023-01-01", "valores": {"hba1c_pct": 6.4,
                                                "clave_desconocida": 9}},
        ]
        series = chart_summary._series(historia)
        ids = {s["id"] for s in series}
        self.assertIn("glucemia_mg_dl", ids)          # 3 puntos: entra
        self.assertNotIn("hba1c_pct", ids)            # 2 puntos: afuera
        self.assertNotIn("clave_desconocida", ids)    # fuera del catálogo
        glu = [s for s in series if s["id"] == "glucemia_mg_dl"][0]
        self.assertEqual([p[1] for p in glu["puntos"]], [100, 118, 130])
        self.assertEqual(glu["unidad"], "mg/dL")

    def test_indicadores_completan_cuatro_tarjetas_sin_numeros_del_modelo(self):
        def serie(sid, label, value, rango):
            return {"id": sid, "label": label, "unidad": "u", "rango_ok": rango,
                    "puntos": [["2022-01-01", value - 2],
                               ["2023-01-01", value - 1],
                               ["2024-01-01", value]]}

        series = [
            serie("ta_sistolica", "TA sistólica", 146, [90, 130]),
            serie("ta_diastolica", "TA diastólica", 92, [60, 85]),
            serie("glucemia_mg_dl", "Glucemia", 145, [70, 110]),
            serie("hba1c_pct", "HbA1c", 6.9, [4, 5.7]),
            serie("colesterol_total_mg_dl", "Colesterol", 210, [120, 200]),
        ]
        tendencias = [dict(series[0], comentario=""),
                      dict(series[1], comentario=""),
                      dict(series[2], comentario="")]
        indicadores = chart_summary._indicadores(series, tendencias)
        self.assertEqual({s["id"] for s in indicadores},
                         {"ta_sistolica", "ta_diastolica", "glucemia_mg_dl",
                          "hba1c_pct", "colesterol_total_mg_dl"})
        self.assertTrue(all(s["puntos"] in [x["puntos"] for x in series]
                            for s in indicadores))

    def test_fallback_includes_prior_doctor_notes_not_only_charts(self):
        resumen = chart_summary._deterministic([], [], [
            {"fecha": "2024-01-02", "tipo": "evolucion",
             "servicio": "Clínica Médica",
             "texto": "Control. Se revisan estudios previos y medicación."},
        ], [], "fake")
        self.assertEqual(resumen["antecedentes"][0]["titulo"],
                         "Clínica Médica · 2024")
        self.assertIn("estudios previos", resumen["antecedentes"][0]["texto"])


class GenerateTestCase(FakeEnvMixin, unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        self.addCleanup(self._reset_catalog)   # registered first, so it runs after the environment is put back
        self._fake_on()
        self._tmp = tempfile.mkdtemp(prefix="chart-summary-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        self._reset_catalog()

    @staticmethod
    def _reset_catalog():
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def test_genera_persiste_y_reusa_por_fingerprint(self):
        out = chart_summary.generate(_DNI_RICO, "med-1")
        self.assertTrue(out and out["resumen"]["titular"])
        self.assertEqual(out["especialidad"], "Cardiología")
        self.assertTrue(out["resumen"]["tendencias"])
        self.assertGreaterEqual(len(out["resumen"]["indicadores"]),
                                len(out["resumen"]["tendencias"]))
        for t in out["resumen"]["tendencias"]:
            self.assertGreaterEqual(len(t["puntos"]), 3)
        self.assertTrue(any("Alepo" in f for f in out["resumen"]["fuentes"]))
        self.assertTrue(any("Marqui" in f for f in out["resumen"]["fuentes"]))
        # La medicación sale de las recetas digitalizadas, siempre a verificar.
        self.assertTrue(out["resumen"]["medicacion"])
        self.assertTrue(all(m["estado"] == "verificar"
                            for m in out["resumen"]["medicacion"]))
        primero = out["generado"]
        self.assertTrue(primero)
        # Fuentes sin cambios → misma fila, sin regenerar.
        self.assertEqual(chart_summary.generate(_DNI_RICO, "med-1")["generado"],
                         primero)
        # force=True regenera aunque nada haya cambiado.
        self.assertTrue(chart_summary.generate(_DNI_RICO, "med-1", force=True))

    def test_conectores_apagados_degrada_a_solo_lo_nuestro(self):
        self._fake_off()
        sid = db_booking.create_booking_request(_DNI_RICO, "Sofía", "Herrera", "1953-08-11",
                                        "wa:2254", "serv-cardio", "centro-a")
        db_booking.approve(sid, datetime.now(_ART) + timedelta(days=1),
                   medico_asignado_id="med-1", resuelta_por="admin")
        out = chart_summary.generate(_DNI_RICO, "med-1")
        self.assertTrue(out)
        self.assertEqual(out["fuentes_estado"].get("alepo"), "no_disponible")
        self.assertFalse(out["resumen"]["tendencias"])
        self.assertTrue(any("Este sistema" in f for f in out["resumen"]["fuentes"]))

    def _turno_de(self, medico_id, dni=_DNI_RICO):
        sid = db_booking.create_booking_request(dni, "Sofía", "Herrera", "1953-08-11",
                                        "wa:2254", "serv-cardio", "centro-a")
        db_booking.approve(sid, datetime.now(_ART) + timedelta(days=1),
                   medico_asignado_id=medico_id, resuelta_por="admin")
        return sid

    def _accesos(self):
        from app.core import access_log
        return access_log.history(_DNI_RICO)

    def test_medico_no_abre_el_resumen_de_un_paciente_ajeno(self):
        # K10 (2026-10-23): un médico pedía el resumen IA de CUALQUIER DNI —
        # sin turno ni entrevista con él — y recibía la historia entera.
        db_operators.operator_create("dra-fernandez", "clave1234", "medico", medico_id="med-1")
        self._turno_de("med-2")
        client = _mount_summary_router()
        with client:
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}",
                           headers=_cookie("dra-fernandez", "medico"))
            # Chart ajeno = 404, no 403 (lo que no podés ver no existe para vos).
            self.assertEqual(r.status_code, 404)
            self.assertNotIn("resumen", r.json())
            r = client.post("/api/admin/patient-summary", json={"dni": _DNI_RICO},
                            headers=_cookie("dra-fernandez", "medico"))
            self.assertEqual(r.status_code, 404)
        self.assertIsNone(db_clinical.summary_get(_DNI_RICO, "med-1"))
        accesos = self._accesos()
        self.assertEqual([(a["actor"], a["action"], a["allowed"]) for a in accesos],
                         [("dra-fernandez", "resumen_ia", False)] * 2)

    def test_cada_lectura_del_resumen_queda_registrada(self):
        db_operators.operator_create("dra-fernandez", "clave1234", "medico", medico_id="med-1")
        self._turno_de("med-1")
        client = _mount_summary_router()
        with client:
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}",
                           headers=_cookie("dra-fernandez", "medico"))
            self.assertEqual(r.status_code, 200)
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}&medico=med-1",
                           headers=_admin_headers())
            self.assertEqual(r.status_code, 200)
        accesos = self._accesos()
        self.assertEqual({(a["actor"], a["action"], a["allowed"]) for a in accesos},
                         {("dra-fernandez", "resumen_ia", True),
                          (admin_auth._admin_user(), "resumen_ia", True)})
        # El registro no guarda el DNI: sólo su HMAC.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM clinical_access_log WHERE dni_hash = %s",
                        (_DNI_RICO,))
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_rol_propio_con_atencion_medica_no_elige_medico_ni_paciente(self):
        # 2026-10-23: un rol creado en el panel con «atención médica» pasaba
        # ?medico= y ?dni= de cualquiera y recibía el resumen IA.
        from app.core import permissions
        self._turno_de("med-2")
        try:
            slug = permissions.create_role("Guardia", "Atiende la guardia.", ["atencion_medica"])["slug"]
        except permissions.RoleError:
            slug = "guardia"
        db_operators.operator_create("guardia", "clave1234", "admisionista")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, role_slug) VALUES ('guardia', %s)"
                        " ON CONFLICT (username) DO UPDATE SET role_slug = EXCLUDED.role_slug",
                        (slug,))
        client = _mount_summary_router()
        with client:
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}&medico=med-2",
                           headers=_cookie("guardia", slug))
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.json(), {"error": "cuenta_sin_medico"})
        self.assertIsNone(db_clinical.summary_get(_DNI_RICO, "med-2"))

    def test_todos_los_pacientes_abre_el_resumen_ajeno_y_queda_registrado(self):
        # El responsable del producto 2026-10-23 (q781376, variante B): con «todos los pacientes» el
        # resumen de otro médico se abre — y cada lectura queda registrada.
        # Sacarle el permiso lo cierra en el pedido siguiente.
        db_operators.operator_create("dra-fernandez", "clave1234", "medico", medico_id="med-1")
        self._turno_de("med-2")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, granted) VALUES ('dra-fernandez', %s)",
                        (["todos_los_pacientes"],))
        client = _mount_summary_router()
        with client:
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}&medico=med-2",
                           headers=_cookie("dra-fernandez", "medico"))
            self.assertEqual(r.status_code, 200)
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE operator_access SET granted = '{}' WHERE username = 'dra-fernandez'")
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}&medico=med-2",
                           headers=_cookie("dra-fernandez", "medico"))
            self.assertEqual(r.status_code, 404)
        self.assertEqual([(a["actor"], a["action"], a["allowed"]) for a in reversed(self._accesos())],
                         [("dra-fernandez", "resumen_ia", True), ("dra-fernandez", "resumen_ia", False)])

    def test_gate_medico_usa_su_propio_id(self):
        db_operators.operator_create("dra-fernandez", "clave1234", "medico", medico_id="med-1")
        self._turno_de("med-1")
        client = _mount_summary_router()
        with client:
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}&medico=med-2",
                           headers=_cookie("dra-fernandez", "medico"))
            self.assertEqual(r.status_code, 200)
            # Ignoró ?medico=med-2: el resumen quedó filtrado por SU especialidad.
            self.assertEqual(r.json()["especialidad"], "Cardiología")

    def test_gate_admin_necesita_medico(self):
        client = _mount_summary_router()
        with client:
            r = client.get(f"/api/admin/patient-summary?dni={_DNI_RICO}",
                           headers=_admin_headers())
            self.assertEqual(r.status_code, 400)

    def test_post_regenera(self):
        client = _mount_summary_router()
        with client:
            r = client.post("/api/admin/patient-summary",
                            json={"dni": _DNI_RICO, "medico": "med-1"},
                            headers=_admin_headers())
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["resumen"]["titular"])

    def test_tick_pregenera_turnos_proximos(self):
        sid = db_booking.create_booking_request(_DNI_RICO, "Sofía", "Herrera", "1953-08-11",
                                        "wa:2254", "serv-cardio", "centro-a")
        db_booking.approve(sid, datetime.now(_ART) + timedelta(hours=20),
                   medico_asignado_id="med-1", resuelta_por="admin")
        self.assertGreaterEqual(chart_summary.tick(), 1)
        fila = db_clinical.summary_get(_DNI_RICO, "med-1")
        self.assertTrue(fila and fila["resumen"]["titular"])
        # Segunda pasada: nada cambió, nada que regenerar.
        self.assertEqual(chart_summary.tick(), 0)


class PregenOnDemoTestCase(unittest.TestCase):
    """The demo re-seeds its fictional agenda every night (new dates, new
    fingerprints), so a nightly sweep would pay ~130 model calls a day for
    summaries of invented patients; on the demo they are made on open."""

    def test_pregen_is_off_on_the_demo_unless_asked_for(self):
        with _env.patched_env(TURNOS_DEMO_MODE="1", TURNOS_SUMMARY_PREGEN=None):
            self.assertFalse(chart_summary._pregen_enabled())
        with _env.patched_env(TURNOS_DEMO_MODE="1", TURNOS_SUMMARY_PREGEN="1"):
            self.assertTrue(chart_summary._pregen_enabled())

    def test_pregen_stays_on_elsewhere_and_off_with_the_flag(self):
        with _env.patched_env(TURNOS_DEMO_MODE=None, TURNOS_SUMMARY_PREGEN=None):
            self.assertTrue(chart_summary._pregen_enabled())
        with _env.patched_env(TURNOS_DEMO_MODE=None, TURNOS_SUMMARY_PREGEN="0"):
            self.assertFalse(chart_summary._pregen_enabled())


if __name__ == "__main__":
    unittest.main()
