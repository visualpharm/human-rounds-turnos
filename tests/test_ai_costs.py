"""test_ai_costs.py — costos de IA + fair-use + reporte admin (ai_costs.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_ai_costs.py"

Mismo patrón que test_reports.py: base dedicada `sana_turnos_test_costos`,
envs del dueño fijadas ANTES de importar nada. El fair-use
es 100% en memoria (no necesita base): esos tests corren siempre.

HANDLER RETIREMENT (2026-08-30): el transporte legacy `ai_costs.handle()`
que ReporteEndpointTestCase manejaba con un FakeHandler fue eliminado del
módulo; los casos del reporte ahora pegan al router nativo
`ai_costs_router.router` (montado solo en un FastAPI mínimo, mismo patrón
que test_ai_native_routers.py) y fijan status/body con los valores exactos
que el transporte retirado producía.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_operators  # noqa: E402
from app.core import db_ai  # noqa: E402
from app.core import tz  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.ai import ai_costs  # noqa: E402
from app.ai import ai_costs_router  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_costos", seed=lambda: db_operators.operators_list())


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
            cur.execute("TRUNCATE ai_costs RESTART IDENTITY")
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


def _mount_costs_router():
    app = FastAPI()
    app.include_router(ai_costs_router.router)
    return TestClient(app)


# ============================================================================
# fair-use (100% en memoria — corre con o sin base)
# ============================================================================

class FairUseTestCase(unittest.TestCase):
    def setUp(self):
        ai_costs._WINDOW.clear()
        ai_costs._calls = 0
        _env.set_env(self, TURNOS_AI_ABUSE_OFF=None, TURNOS_AI_ABUSE_MIN="8",
                     TURNOS_AI_ABUSE_FAILS="6")

    def tearDown(self):
        ai_costs._WINDOW.clear()

    def _hits(self, ip, n, reconocido):
        for _ in range(n):
            ai_costs.record("ocr_frente", cost_usd=0.001, ip=ip, recognized=reconocido)

    def test_pocas_llamadas_no_bloquea(self):
        self._hits("1.1.1.1", 5, False)   # 5 fallos, por debajo del mínimo (8)
        self.assertFalse(ai_costs.blocked("1.1.1.1"))

    def test_rafaga_de_basura_bloquea(self):
        self._hits("2.2.2.2", 8, False)   # 8 llamadas, todas irreconocibles
        self.assertTrue(ai_costs.blocked("2.2.2.2"))

    def test_muchas_reconocidas_no_bloquea(self):
        # 8 llamadas: 7 OK + 1 fallo -> sólo 1 fallo (< 6): jamás es abuso.
        self._hits("3.3.3.3", 7, True)
        self._hits("3.3.3.3", 1, False)
        self.assertFalse(ai_costs.blocked("3.3.3.3"))

    def test_tasa_de_exito_alta_no_bloquea(self):
        # 12 llamadas, 6 fallos + 6 OK -> tasa_ok 0.5 (>= 0.25): no es abuso
        # aunque haya 6 fallos.
        self._hits("4.4.4.4", 6, False)
        self._hits("4.4.4.4", 6, True)
        self.assertFalse(ai_costs.blocked("4.4.4.4"))

    def test_casi_todo_falla_bloquea(self):
        # 10 llamadas, 9 fallos + 1 OK -> tasa_ok 0.1 (< 0.25) y 9 fallos: abuso.
        self._hits("5.5.5.5", 9, False)
        self._hits("5.5.5.5", 1, True)
        self.assertTrue(ai_costs.blocked("5.5.5.5"))

    def test_reconocido_none_no_cuenta(self):
        # reconocido=None (p.ej. una operación sin veredicto) no alimenta la
        # ventana: 20 no mueven la aguja.
        self._hits("6.6.6.6", 20, None)
        self.assertFalse(ai_costs.blocked("6.6.6.6"))
        self.assertNotIn("6.6.6.6", ai_costs._WINDOW)

    def test_off_switch_desactiva(self):
        self._hits("7.7.7.7", 10, False)
        self.assertTrue(ai_costs.blocked("7.7.7.7"))
        _env.set_env(self, TURNOS_AI_ABUSE_OFF="1")
        self.assertFalse(ai_costs.blocked("7.7.7.7"))

    def test_ip_vacia_nunca_bloquea(self):
        self.assertFalse(ai_costs.blocked(""))
        self.assertFalse(ai_costs.blocked(None))

    def test_marca_sospechosa(self):
        # misma regla sobre el total del período (no la ventana de 1h)
        self.assertTrue(ai_costs._flag_suspicious(
            {"operaciones": 10, "no_reconocidas": 9, "costo_usd": 0.02}))
        self.assertFalse(ai_costs._flag_suspicious(
            {"operaciones": 3, "no_reconocidas": 3, "costo_usd": 0.01}))   # pocas
        self.assertFalse(ai_costs._flag_suspicious(
            {"operaciones": 12, "no_reconocidas": 2, "costo_usd": 0.03}))  # casi todo OK


# ============================================================================
# db + reporte (necesitan base)
# ============================================================================

def _log(op, costo, ip, reconocido, modelo="fake"):
    db_ai.log_ai_cost(op, costo_usd=costo, ip=ip, reconocido=reconocido, modelo=modelo)


class DbAgregacionesTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def test_resumen_totales_y_por_ip(self):
        _log("analizar_orden", 0.004, "10.0.0.1", True)
        _log("analizar_orden", 0.004, "10.0.0.1", False)
        _log("ocr_frente", 0.001, "10.0.0.2", True)
        r = db_ai.costs_summary(30)
        self.assertEqual(r["operaciones"], 3)
        self.assertAlmostEqual(r["total_usd"], 0.009, places=6)
        self.assertEqual(r["reconocidas"], 2)
        self.assertEqual(r["no_reconocidas"], 1)
        self.assertEqual(r["ips"], 2)
        # gasto por IP: 0.008 y 0.001 -> mediana 0.0045, promedio 0.0045
        self.assertAlmostEqual(r["mediana_por_ip"], 0.0045, places=6)
        self.assertAlmostEqual(r["promedio_por_ip"], 0.0045, places=6)

    def test_por_operacion(self):
        _log("analizar_orden", 0.004, "1.2.3.4", True)
        _log("ocr_frente", 0.001, "1.2.3.4", True)
        _log("ocr_dorso", 0.001, "1.2.3.4", False)
        out = {r["operacion"]: r for r in db_ai.costs_by_operation(30)}
        self.assertEqual(out["analizar_orden"]["cantidad"], 1)
        self.assertAlmostEqual(out["analizar_orden"]["costo_usd"], 0.004, places=6)
        self.assertEqual(out["ocr_dorso"]["no_reconocidas"], 1)

    def test_top_ips_ordena_por_costo(self):
        _log("analizar_orden", 0.02, "9.9.9.9", False)
        _log("ocr_frente", 0.001, "8.8.8.8", True)
        top = db_ai.costs_top_ips(30)
        self.assertEqual(top[0]["ip"], "9.9.9.9")
        self.assertEqual(top[0]["no_reconocidas"], 1)

    def test_diario_agrupa(self):
        _log("ocr_frente", 0.001, "1.1.1.1", True)
        _log("ocr_dorso", 0.001, "1.1.1.1", True)
        out = db_ai.costs_daily(30)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["operaciones"], 2)
        self.assertAlmostEqual(out[0]["costo_usd"], 0.002, places=6)

    def test_vacio_da_ceros(self):
        r = db_ai.costs_summary(30)
        self.assertEqual(r["operaciones"], 0)
        self.assertEqual(r["total_usd"], 0.0)
        self.assertIsNone(r["mediana_por_ip"])
        self.assertEqual(db_ai.costs_by_operation(30), [])
        self.assertEqual(db_ai.costs_top_ips(30), [])

    def test_burn_mensual_descarta_primer_dia_y_hoy(self):
        import psycopg
        # ART, no reloj local del proceso: costs_burn_monthly() calcula "hoy"
        # en SQL vía `AT TIME ZONE _ART_SQL` (db.py) — si el sistema que corre
        # el test está en otro huso (como el runner de CI, en UTC), un
        # datetime.now().date() naive puede caer un día antes o después.
        hoy = datetime.now(tz.tzinfo()).date()
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                for fecha, costo in (
                    (hoy - timedelta(days=3), 9.0),
                    (hoy - timedelta(days=2), 0.02),
                    (hoy, 9.0),
                ):
                    cur.execute(
                        """INSERT INTO ai_costs (operation,cost_usd,ts)
                           VALUES ('analizar_orden',%s,%s::date + interval '12 hours')""",
                        (costo, fecha),
                    )
        burn = db_ai.costs_burn_monthly()
        self.assertEqual(burn["dias_completos"], 2)
        self.assertAlmostEqual(burn["total_usd_dias_completos"], 0.02, places=6)
        self.assertAlmostEqual(burn["run_rate_mensual_usd"], 0.3, places=6)


class ReporteEndpointTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        self.client = _mount_costs_router()

    def tearDown(self):
        self.client.close()

    def test_reporte_resumen_y_labels(self):
        _log("analizar_orden", 0.004, "10.0.0.1", True)
        _log("ocr_frente", 0.001, "10.0.0.2", True)
        _log("ocr_dorso", 0.001, "10.0.0.2", False)
        r = self.client.get("/api/admin/costs?days=30", headers=_admin_headers())
        self.assertEqual(r.status_code, 200)
        res = r.json()["resumen"]
        self.assertEqual(res["operaciones"], 3)
        self.assertAlmostEqual(res["total_usd"], 0.006, places=6)
        self.assertEqual(res["reconocidas"], 2)
        # label legible por operación
        labels = {o["operacion"]: o["label"] for o in r.json()["por_operacion"]}
        self.assertEqual(labels["ocr_frente"], "Foto del DNI — frente")
        self.assertEqual(labels["analizar_orden"], "Análisis de la orden")

    def test_every_recorded_operation_reads_as_words(self):
        # Critical review 08.11 (E8): the bar read «resumen_paciente».
        _log("resumen_paciente", 0.002, "10.0.0.3", True)
        _log("laboratorio_paciente", 0.002, "10.0.0.3", True)
        _log("operacion_nueva", 0.001, "10.0.0.3", True)
        r = self.client.get("/api/admin/costs?days=30", headers=_admin_headers())
        labels = {o["operacion"]: o["label"] for o in r.json()["por_operacion"]}
        self.assertEqual(labels["resumen_paciente"], "Resumen del paciente")
        self.assertEqual(labels["laboratorio_paciente"], "Lectura de análisis de laboratorio")
        self.assertEqual(labels["operacion_nueva"], "Otra operación de IA")

    def test_reporte_marca_sospechosos(self):
        for _ in range(10):
            _log("ocr_frente", 0.001, "6.6.6.6", False)   # basura pura
        _log("analizar_orden", 0.004, "7.7.7.7", True)     # usuario normal
        r = self.client.get("/api/admin/costs", headers=_admin_headers())
        sos = [t["ip"] for t in r.json()["sospechosos"]]
        self.assertIn("6.6.6.6", sos)
        self.assertNotIn("7.7.7.7", sos)

    def test_dias_invalido_cae_al_default(self):
        r = self.client.get("/api/admin/costs?days=15", headers=_admin_headers())
        self.assertEqual(r.json()["dias"], 30)

    def test_vacio_estructura(self):
        r = self.client.get("/api/admin/costs", headers=_admin_headers())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["resumen"]["operaciones"], 0)
        self.assertEqual(r.json()["por_operacion"], [])
        self.assertEqual(r.json()["top_ips"], [])
        self.assertEqual(r.json()["sospechosos"], [])

    def test_operador_403_admin_200(self):
        db_operators.operator_create("op-costos", "clave1234", "admisionista")
        r = self.client.get("/api/admin/costs", headers=_cookie("op-costos", "admisionista"))
        self.assertEqual(r.status_code, 403)

        r = self.client.get("/api/admin/costs", headers=_admin_headers())
        self.assertEqual(r.status_code, 200)

    def test_sin_sesion_401(self):
        r = self.client.get("/api/admin/costs")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
