"""Site-level holidays (contraparte técnica, hospital, 2026-10-15): «bloqueo masivo de los
turnos programados — por ejemplo cerrar de antemano los feriados nacionales
del año». One list in Settings closes those dates on every agenda at once.

Pins:
- the slot generator's single day resolution (`site_availability._day_exceptions`)
  turns a holiday into a full-day closure for every consumer — wizard grid,
  next slot, next opening, «Dar este turno» minutes, capacity counts and
  connector slots;
- the national list is the official calendar, copied, never computed
  (source: https://www.argentina.gob.ar/jefatura/feriados-nacionales-2026);
- already-given appointments are counted per date, never cancelled;
- only an admin reaches the Settings API that edits the list.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_site_holidays.py"
"""

import contextlib
import datetime
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402
from app.admin_panel import settings_admin, settings_admin_router  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import config, db  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.modules.turnos import doctor_appointments, site_holidays  # noqa: E402
from app.patient_web import site_availability, site_schedule

OFFICIAL_SOURCE = "https://www.argentina.gob.ar/jefatura/feriados-nacionales-2026"

# Monday 2026-12-07, 07:00 ART; Tuesday 12-08 (Inmaculada Concepción) and
# Friday 12-25 (Navidad) are the holidays under test.
_LUNES = datetime.datetime(2026, 12, 7, 7, 0)
_MAR_VIE = {"dias": [2, 5], "desde": "08:00", "hasta": "09:00", "cada_min": 20}
_HOLIDAYS = {"2026-12-08": "Inmaculada Concepción de María", "2026-12-25": "Navidad"}


def _service(agenda, **extra):
    return dict({"id": "svc-x", "nombre": "Medicina general", "centro": "caps-x",
                 "modo": "turno", "agenda": agenda}, **extra)


def _argentine_chain():
    """The reference deployment's chain includes the country pack `ar`; the
    bundled default (`global,example-town`) does not."""
    stack = contextlib.ExitStack()
    stack.enter_context(_env.patched_env({"PACK_CHAIN": "global,ar,ar-b"}))
    stack.enter_context(mock.patch.object(config, "_db_overrides", return_value={}))
    return stack


def _with_holidays(value=None):
    return mock.patch.object(site_holidays, "by_date", return_value=dict(_HOLIDAYS if value is None else value))


class TestSlotGeneratorSkipsHolidays(unittest.TestCase):
    def test_day_exceptions_adds_a_full_day_closure_on_a_holiday_only(self):
        with _with_holidays():
            excs = site_availability._day_exceptions([], "2026-12-08")
            self.assertEqual(excs, [{"tipo": "cerrado", "fecha": "2026-12-08",
                                     "motivo": "Inmaculada Concepción de María", "feriado": True}])
            self.assertEqual(site_availability._day_exceptions([], "2026-12-09"), [])

    def test_wizard_grid_offers_nothing_on_a_holiday(self):
        s = _service({"franjas": [_MAR_VIE], "dias_anticipacion": 31})
        with _with_holidays({}):
            free = {t["dia"] for t in site_availability._agenda_slots(s["agenda"], now=_LUNES, service=s)}
        self.assertIn("2026-12-08", free)
        with _with_holidays():
            days = {t["dia"] for t in site_availability._agenda_slots(s["agenda"], now=_LUNES, service=s)}
        self.assertNotIn("2026-12-08", days)
        self.assertNotIn("2026-12-25", days)
        self.assertIn("2026-12-11", days)

    def test_next_slot_jumps_over_the_holiday(self):
        with _with_holidays():
            self.assertEqual(site_availability._next_multi([_MAR_VIE], [], now=_LUNES), ("2026-12-11", "08:00"))

    def test_next_opening_skips_the_holiday(self):
        # Horizon 2 (today + tomorrow): tomorrow is the holiday, so the next
        # bookable slot is Friday 12-11, which enters the window on Thursday.
        s = _service({"franjas": [_MAR_VIE], "dias_anticipacion": 2})
        with _with_holidays():
            self.assertEqual(site_availability._agenda_slots(s["agenda"], now=_LUNES, service=s), [])
            self.assertEqual(site_availability._next_opening(s, now=_LUNES),
                             {"estado": "fecha", "dia": "2026-12-10", "hora": ""})

    def test_desk_free_minutes_are_empty_on_a_holiday(self):
        # «Dar este turno» in «Pacientes de hoy» reads the day through _minutes_of_day.
        with _with_holidays():
            self.assertEqual(doctor_appointments._minutes_of_day([_MAR_VIE], [], "2026-12-08"), [])
            self.assertEqual(doctor_appointments._minutes_of_day([_MAR_VIE], [], "2026-12-11"),
                             ["08:00", "08:20", "08:40"])

    def test_capacity_counts_drop_the_holiday(self):
        with _with_holidays():
            by_day = doctor_appointments._slots_by_day([_MAR_VIE], [], 5, _LUNES)
        self.assertEqual(by_day, {"2026-12-11": 3})

    def test_connector_slots_on_a_holiday_are_not_offerable(self):
        with _with_holidays():
            self.assertFalse(site_availability._is_future_slot("2026-12-08", "08:00", now=_LUNES))
            self.assertTrue(site_availability._is_future_slot("2026-12-09", "08:00", now=_LUNES))

    def test_walk_in_rule_unchanged(self):
        walkin = _service({"franjas": [_MAR_VIE]}, modo="walkin")
        with _with_holidays():
            self.assertIsNone(site_availability._next_opening(walkin, now=_LUNES))


class TestHoursViews(unittest.TestCase):
    def test_doctor_workspace_says_once_that_holidays_close_the_agenda(self):
        # The product owner, 2026-10-27: one line of meaning, date first, never «Feriado»
        # on every row; the names open on request.
        from app.admin_panel import doctor_workspace
        with _with_holidays(dict(_HOLIDAYS, **{"2026-01-01": "Año Nuevo", "2027-01-01": "Año Nuevo"})):
            out = doctor_workspace.upcoming_holidays(today=datetime.date(2026, 12, 1))
        self.assertEqual(out["line"], "En los feriados no se dan turnos. Los próximos: "
                                      "8 de diciembre, 25 de diciembre y 1 de enero de 2027.")
        # A past holiday is not shown; the year only when it is not this one.
        self.assertEqual([(r["dia"], r["nombre"]) for r in out["rows"]],
                         [("8 de diciembre", "Inmaculada Concepción de María"), ("25 de diciembre", "Navidad"),
                          ("1 de enero de 2027", "Año Nuevo")])
        self.assertEqual(out["more"], "Ver los 3 feriados")
        self.assertNotIn("Feriado:", out["line"])

    def test_doctor_workspace_single_and_no_holiday(self):
        from app.admin_panel import doctor_workspace
        with _with_holidays({"2026-12-25": "Navidad"}):
            out = doctor_workspace.upcoming_holidays(today=datetime.date(2026, 12, 1))
        self.assertEqual(out, {"line": "En los feriados no se dan turnos. El próximo es el 25 de diciembre (Navidad).",
                               "rows": []})
        with _with_holidays({}):
            self.assertEqual(doctor_workspace.upcoming_holidays(today=datetime.date(2026, 12, 1)), {})

    def test_the_holiday_claim_is_what_the_slot_generator_does(self):
        # The sentence «no se dan turnos» is only true while every slot
        # surface reads a day through `_day_exceptions`: pin that a holiday
        # is a full-day closure there (the grids above prove the surfaces).
        with _with_holidays():
            excs = site_availability._day_exceptions([], "2026-12-25")
        self.assertTrue(any(e["tipo"] == "cerrado" and e.get("feriado") for e in excs))

    def test_upcoming_carries_the_friendly_date(self):
        with _with_holidays():
            (h, _) = site_holidays.upcoming_labeled(today=datetime.date(2026, 12, 1))
        self.assertEqual(h["fecha"], "2026-12-08")
        self.assertEqual(h["dia"], site_schedule._friendly_date("2026-12-08"))


class TestNationalList(unittest.TestCase):
    def test_pack_file_is_the_official_calendar(self):
        path = os.path.join(ROOT, "packs", "ar", site_holidays.NATIONAL_FILE)
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["source"], OFFICIAL_SOURCE)
        self.assertEqual({h["type"] for h in doc["holidays"]}, {"inamovible", "trasladable"})
        dates = [h["date"] for h in doc["holidays"]]
        self.assertEqual(len(dates), 18)
        self.assertEqual(dates, sorted(dates))
        # Spot-checks against the published calendar (moved dates included).
        for fecha in ("2026-02-16", "2026-02-17", "2026-06-15", "2026-08-17",
                      "2026-10-12", "2026-11-09", "2026-11-23", "2027-01-01"):
            self.assertIn(fecha, dates)
        # No 2027 calendar was published when this was written: only its
        # 1 January, which the 2026 calendar itself lists.
        self.assertEqual([d for d in dates if d >= "2027"], ["2027-01-01"])

    def test_national_reads_the_active_pack_chain(self):
        with _argentine_chain():
            nat = site_holidays.national()
        self.assertEqual(nat["source"], OFFICIAL_SOURCE)
        self.assertIn({"fecha": "2026-12-25", "nombre": "Navidad"}, nat["items"])

    def test_a_chain_without_a_country_list_offers_none(self):
        with _env.patched_env({"PACK_CHAIN": "global,example-town"}), \
                mock.patch.object(config, "_db_overrides", return_value={}):
            self.assertEqual(site_holidays.national(), {"source": "", "items": []})

    def test_merge_adds_only_future_missing_dates_and_keeps_the_admin_name(self):
        today = datetime.date(2026, 12, 1)
        current = [{"fecha": "2026-12-25", "nombre": "Navidad (cerrado todo el día)"}]
        with _argentine_chain():
            merged = site_holidays.clean(site_holidays.merge_national(current, today=today))
        self.assertEqual(merged, [
            {"fecha": "2026-12-08", "nombre": "Inmaculada Concepción de María"},
            {"fecha": "2026-12-25", "nombre": "Navidad (cerrado todo el día)"},
            {"fecha": "2027-01-01", "nombre": "Año Nuevo"},
        ])


class TestValidation(unittest.TestCase):
    def test_clean_sorts_dedupes_and_drops_blank_rows(self):
        self.assertEqual(site_holidays.clean([
            {"fecha": "2026-12-25", "nombre": " Navidad "},
            {"fecha": "", "nombre": ""},
            {"fecha": "2026-12-08", "nombre": "Inmaculada"},
        ]), [{"fecha": "2026-12-08", "nombre": "Inmaculada"}, {"fecha": "2026-12-25", "nombre": "Navidad"}])

    def test_clean_rejects_bad_rows(self):
        for bad in ([{"fecha": "2026-13-01", "nombre": "x"}], [{"fecha": "2026-12-25", "nombre": ""}],
                    [{"fecha": "2026-12-25", "nombre": "x" * 121}], "2026-12-25"):
            with self.assertRaises(site_holidays.HolidayError):
                site_holidays.clean(bad)


def _gate(status=None, rol="admin"):
    def fake(*a, **k):
        if status:
            return admin_auth.GateResult(status=status, body={"error": "sin_permiso"})
        return admin_auth.GateResult(identity={"usuario": rol, "rol": rol})
    return mock.patch.object(admin_auth, "evaluate_admin_gate", fake)


class TestSettingsGate(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(settings_admin_router.router)
        from app.core import deps as _early_deps
        _early_deps.install_early_response(app)  # the app's answer of a dependency
        self.client = TestClient(app)
        config._reset_cache_para_tests()

    def test_settings_gate_is_admin_only(self):
        seen = []

        def fake(cookie_token, method, path, solo_admin=False, *a, **k):
            seen.append(solo_admin)
            return admin_auth.GateResult(status=403, body={"error": "sin_permiso"})
        with mock.patch.object(admin_auth, "evaluate_admin_gate", fake), \
                mock.patch.object(site_holidays, "save") as save:
            res = self.client.post("/api/admin/settings", json={"grupo": "feriados", "valores": {"items": []}})
        self.assertEqual(res.status_code, 403)
        save.assert_not_called()
        self.assertTrue(seen and all(seen))

    def test_admin_saves_the_list_and_gets_counts_back(self):
        saved = []
        with _gate(), _argentine_chain(), mock.patch.object(site_holidays, "save", side_effect=lambda items: saved.append(items)), \
                mock.patch.object(site_holidays, "by_date", return_value={"2026-12-25": "Navidad"}), \
                mock.patch.object(site_holidays, "appointment_counts", return_value={"2026-12-25": 3}):
            res = self.client.post("/api/admin/settings", json={
                "grupo": "feriados", "valores": {"items": [{"fecha": "2026-12-25", "nombre": "Navidad"}]}})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(saved, [[{"fecha": "2026-12-25", "nombre": "Navidad"}]])
        self.assertEqual(res.json()["feriados"]["items"], [{"fecha": "2026-12-25", "nombre": "Navidad", "turnos": 3}])
        self.assertTrue(res.json()["feriados"]["nacionales"])

    def test_invalid_list_is_a_400_with_the_reason(self):
        with _gate():
            res = self.client.post("/api/admin/settings", json={
                "grupo": "feriados", "valores": {"items": [{"fecha": "mañana", "nombre": "x"}]}})
        self.assertEqual(res.status_code, 400)
        self.assertIn("fecha", res.json()["error"])

    def test_payload_carries_the_holiday_card(self):
        with _argentine_chain(), mock.patch.object(site_holidays, "by_date", return_value={}):
            self.assertEqual(settings_admin._feriados_payload(), {"items": [], "nacionales": True})


_db = _dbtest.PerModuleDb("sana_turnos_test_feriados", seed=lambda: db_catalog.catalog_list("centros"))
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestStorage(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin Postgres de test")
        site_holidays.invalidate()

    def tearDown(self):
        if HAVE_DB:
            db_catalog.catalog_upsert(site_holidays.CATALOG_TYPE, site_holidays.CATALOG_ID, {"items": []})
            site_holidays.invalidate()

    def test_save_closes_the_date_for_every_agenda(self):
        site_holidays.save([{"fecha": "2026-12-08", "nombre": "Inmaculada"}])
        self.assertEqual(site_holidays.name_for("2026-12-08"), "Inmaculada")
        self.assertEqual(doctor_appointments._minutes_of_day([_MAR_VIE], [], "2026-12-08"), [])

    def test_appointments_on_a_holiday_are_counted_not_cancelled(self):
        with db._connect() as conn, conn.cursor() as cur:
            for status, when in (("aprobada", "2026-12-08 10:00-03"), ("pendiente", "2026-12-08 23:30-03"),
                                 ("cancelada", "2026-12-08 11:00-03"), ("aprobada", "2026-12-09 01:00-03")):
                cur.execute("INSERT INTO booking_requests (dni, service_id, status, appointment_at) "
                            "VALUES ('1', 'svc-x', %s, %s)", (status, when))
        site_holidays.save([{"fecha": "2026-12-08", "nombre": "Inmaculada"}])
        # 23:30 ART is already 12-09 in UTC: the count follows the installation timezone.
        self.assertEqual(site_holidays.appointment_counts(["2026-12-08", "2026-12-25"]), {"2026-12-08": 2})
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM booking_requests WHERE status='aprobada'")
            self.assertEqual(cur.fetchone()["n"], 2)


if __name__ == "__main__":
    unittest.main()
