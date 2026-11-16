"""tests/routes/test_reports_response_models.py — every operation of the staff
reports and the system status (part "reports" of `tests/_insight_area.py`)
answered by the live app, and the answer checked against the model the
OpenAPI schema names for it (TASK-76, OpenAPI stage D, area c): the success
status the schema documents, a JSON body that matches the model, and no
top-level key the model does not name.

The catalog is the fictional public example-town pack's. Two patients (one
with an insurer and a private plan, one child with public coverage only),
approved appointments ahead and behind, a rejected and a pending request, a
search, the login screen's events and one AI call are made through the
app's own functions on a freshly truncated database, so every report has
rows to count. `test_every_operation_of_the_part_is_answered_here` keeps the
list complete: a new report needs its answer here.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import sqlalchemy as sa

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _insight_area as area  # noqa: E402

from app.core import db, db_accounts, db_ai, db_analytics, db_booking, schema_model as sm, sqlcore, tz  # noqa: E402

_SERVICIO = "medicina-general-riverside-clinic"
_CENTRO = "riverside-clinic"
_MEDICO_ID = "gonzalez-juan"
_ADULT = "30999621"
_CHILD = "30999622"

# (method, path template) -> the test that answers it.
_ANSWERED = {}


def answers(*operations):
    def mark(fn):
        for operation in operations:
            _ANSWERED[operation] = fn.__name__
        return fn
    return mark


def _in_days(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)


class TestReportsAnswers(area.AnswerChecks, RouteTestCase):

    part = "reports"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_documented()

    def setUp(self):
        super().setUp()
        from app.core import config
        # Settings are cached for a few seconds: the class before this one read its own.
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)

    def _request_for(self, dni, nombre, nacimiento):
        return db_booking.create_booking_request(dni, nombre, "Gomez", nacimiento, None, _SERVICIO, _CENTRO)

    def _ok(self, method, path, body=None):
        status, _hdrs, raw = self._request(method, path, headers=admin_cookie(), body=body)
        self.assertEqual(status, 200, raw)

    def _approve(self, sid, days):
        self._ok("POST", f"/api/admin/requests/{sid}/approve", {"turno_dt": _in_days(days).isoformat()})

    def _move_to(self, sid, days):
        """The appointment already happened: its hour moved `days` into the past."""
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.update(sm.booking_requests).where(sm.booking_requests.c.id == sid)
                        .values(appointment_at=_in_days(days)))

    def _seed(self):
        """Rows for every report; returns the day of the appointment that already happened."""
        db_accounts.create_user(
            _ADULT, "Marta", "Gomez", "1980-05-10", "F", "+542254000621", "throwaway-12345",
            email="marta@example.com", domicilio={"calle": "Del Mar 1"},
            cobertura={"obras_sociales": [{"nombre": "IOMA", "afiliado": "123", "origen": "puco"}],
                       "prepaga": {"nombre": "OSDE"}})
        db_accounts.create_user(_CHILD, "Leo", "Gomez", "2022-02-02", "M", "+542254000622", "throwaway-12345")
        ahead = self._request_for(_ADULT, "Marta", "1980-05-10")
        self._approve(ahead, 3)
        behind = self._request_for(_ADULT, "Marta", "1980-05-10")
        self._approve(behind, 4)
        self._move_to(behind, -2)
        self._ok("POST", f"/api/admin/requests/{behind}/attendance", {"asistencia": "presente"})
        child = self._request_for(_CHILD, "Leo", "2022-02-02")
        self._approve(child, 5)
        self._move_to(child, -1)
        self._ok("POST", f"/api/admin/requests/{self._request_for(_ADULT, 'Marta', '1980-05-10')}/reject",
                 {"motivo": "Orden vencida"})
        self._request_for(_CHILD, "Leo", "2022-02-02")
        status, _hdrs, raw = self._request("POST", "/api/search-log", body={"q": "clinica", "resultados": 3})
        self.assertEqual(status, 204, raw)
        for variante, evento, tab, item, ms in (("consejos", "exposicion", "consejos", "c-1", None),
                                                ("consejos", "tab", "humor", "h-1", None),
                                                ("consejos", "login_ok", None, None, 1200)):
            db_analytics.log_panel_login("s-621", variante, evento, tab, item, ms)
        db_ai.log_ai_cost("analizar_orden", 0.002, reconocido=True)
        return _in_days(-2).astimezone(tz.tzinfo())

    def test_every_operation_of_the_part_is_answered_here(self):
        self.assert_every_operation_answered(_ANSWERED)

    @answers(("get", "/api/admin/reports"), ("get", "/api/admin/reports/patients"),
             ("get", "/api/admin/reports/login-panel"), ("get", "/api/admin/reports/searches"))
    def test_management_and_patients(self):
        self._seed()
        admin = admin_cookie()
        report = self._answer("get", "/api/admin/reports", "/api/admin/reports?days=30", headers=admin)
        self.assertTrue(report["volumen"])
        self.assertTrue(report["rechazos"])
        self.assertTrue(report["funnel"]["pasos"])
        self.assertTrue(report["ahorro"]["disponible"])
        patients = self._answer("get", "/api/admin/reports/patients", headers=admin)
        self.assertEqual(patients["kpis"]["total_cuentas"], 2)
        self.assertTrue(patients["cobertura"]["top_obras_sociales"])
        panel = self._answer("get", "/api/admin/reports/login-panel", headers=admin)
        self.assertEqual(panel["sesiones"], 1)
        self.assertEqual(panel["logins"], 1)
        self.assertEqual(panel["errores"], 0)
        self.assertEqual(panel["sesiones_con_error"], 0)
        self.assertEqual(panel["ms_mediana_login"], 1200.0)
        self.assertTrue(self._answer("get", "/api/admin/reports/searches", headers=admin)["top"])

    @answers(("get", "/api/admin/reports/visits"))
    def test_the_visits_report(self):
        visits = self._answer("get", "/api/admin/reports/visits", headers=admin_cookie())
        self.assertEqual(visits["visitas"], 0)

    @answers(("get", "/api/admin/reports"))
    def test_an_empty_period(self):
        report = self._answer("get", "/api/admin/reports", headers=admin_cookie())
        self.assertEqual(report["ahorro"], {"disponible": False})

    @answers(("get", "/api/admin/reports/cost-recovery"), ("get", "/api/admin/reports/sumar"),
             ("get", "/api/admin/reports/statistics"))
    def test_billing_and_statistics(self):
        happened = self._seed()
        admin = admin_cookie()
        recovery = self._answer("get", "/api/admin/reports/cost-recovery", headers=admin)
        self.assertTrue(recovery["por_obra_social"])
        sumar = self._answer("get", "/api/admin/reports/sumar", headers=admin)
        self.assertEqual(sumar["kpis"]["prestaciones"], 1)
        month = f"{happened.year:04d}-{happened.month:02d}"
        stats = self._answer("get", "/api/admin/reports/statistics",
                             f"/api/admin/reports/statistics?mes={month}", headers=admin)
        self.assertTrue(stats["por_especialidad"])

    @answers(("get", "/api/admin/reports/capacity"), ("get", "/api/admin/reports/reviews"))
    def test_capacity_and_reviews(self):
        self._seed()
        admin = admin_cookie()
        self.assertFalse(self._answer("get", "/api/admin/reports/capacity", headers=admin)["temporada"]["disponible"])
        _env.set_env(self, TURNOS_SEASON_FACTOR="1.5")
        capacity = self._answer("get", "/api/admin/reports/capacity", headers=admin)
        self.assertTrue(capacity["temporada"]["disponible"])
        self.assertTrue(capacity["demanda_semanal"])
        self.assertEqual(self._answer("get", "/api/admin/reports/reviews", headers=admin)["fuente"], "curado")

    @answers(("get", "/api/admin/reports/reviews"))
    def test_the_reviews_analysis(self):
        """A pack with `scripts/analizar_resenas.py`'s file answers it as it is, `_meta` included."""
        from app.admin_panel import reports
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        analysis = os.path.join(tmp, "resenas-analisis.json")
        with open(analysis, "w", encoding="utf-8") as fh:
            json.dump({"_meta": {"source": "curado", "centers": 1},
                       "centers": {_CENTRO: {"name": "Riverside Clinic", "hero": []}},
                       "comparison": {"hero": []}}, fh)
        real = reports.sitedata.path
        with patch.object(reports.sitedata, "path",
                          lambda name: analysis if name == "resenas-analisis.json" else real(name)):
            reviews = self._answer("get", "/api/admin/reports/reviews", headers=admin_cookie())
        self.assertEqual(reviews["fuente"], "analisis")
        self.assertEqual(reviews["_meta"]["centers"], 1)

    @answers(("get", "/api/admin/reports/funnel"), ("post", "/api/admin/reports/funnel/steps"))
    def test_the_booking_path(self):
        self._seed()
        admin = admin_cookie()
        path = self._answer("get", "/api/admin/reports/funnel", headers=admin)
        self.assertTrue(path["pasos"])
        self.assertTrue(path["opciones"]["servicios"])
        self._answer("get", "/api/admin/reports/funnel",
                     f"/api/admin/reports/funnel?center={_CENTRO}&service={_SERVICIO}&doctor={_MEDICO_ID}",
                     headers=admin)
        steps = [{"key": step["key"], "on": True} for step in path["pasos_config"]]
        saved = self._answer("post", "/api/admin/reports/funnel/steps", headers=admin, body={"steps": steps})
        self.assertEqual([s["key"] for s in saved["pasos_config"]], [s["key"] for s in steps])

    @answers(("get", "/api/admin/system"), ("get", "/api/admin/system/hsi"))
    def test_system_status(self):
        admin = admin_cookie()
        status = self._answer("get", "/api/admin/system", headers=admin)
        self.assertEqual(status["total"], len(status["clusters"]))
        self.assertTrue(self._answer("get", "/api/admin/system/hsi", headers=admin)["centros"])


if __name__ == "__main__":
    unittest.main()
