"""«Ahora no hay turnos» (contraparte técnica, hospital, 2026-10-17): the empty wizard grid
says when turnos appear again (from the booking horizon and the real
schedules), whom to call, and the patient's own case text reaches the desk."""

import datetime
import os
import unittest
from unittest import mock

from app.patient_web import patient_site_router, site_availability, site_catalog, site_centers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Monday 2026-07-06, 07:00 ART.
_LUNES = datetime.datetime(2026, 7, 6, 7, 0)
_MARTES = {"dias": [2], "desde": "08:00", "hasta": "10:00", "cada_min": 20}


def _service(agenda, **extra):
    return dict({"id": "svc-x", "nombre": "Medicina general", "centro": "caps-x",
                 "modo": "turno", "agenda": agenda}, **extra)


class TestNextOpening(unittest.TestCase):
    def test_date_is_the_first_hidden_slot_minus_the_horizon(self):
        # Horizon 3 days (today + 2): Tuesday 07-07 is inside, so close it;
        # the next Tuesday 07-14 opens 3-1 = 2 days before, Sunday 07-12.
        s = _service({"franjas": [_MARTES], "dias_anticipacion": 3,
                      "excepciones": [{"fecha": "2026-07-07", "tipo": "cerrado"}]})
        self.assertEqual(site_availability._agenda_slots(s["agenda"], now=_LUNES, service=s), [])
        self.assertEqual(site_availability._next_opening(s, now=_LUNES),
                         {"estado": "fecha", "dia": "2026-07-12", "hora": ""})

    def test_opening_time_travels_with_the_date(self):
        s = _service({"franjas": [_MARTES], "dias_anticipacion": 3, "apertura_turnos": "08:00",
                      "excepciones": [{"fecha": "2026-07-07", "tipo": "cerrado"}]})
        self.assertEqual(site_availability._next_opening(s, now=_LUNES),
                         {"estado": "fecha", "dia": "2026-07-12", "hora": "08:00"})

    def test_a_slot_that_opens_later_today_says_today(self):
        # Horizon 2 with an 08:00 opening: at 07:00 only today counts, the
        # Tuesday slot enters the window today at 08:00.
        s = _service({"franjas": [_MARTES], "dias_anticipacion": 2, "apertura_turnos": "08:00"})
        self.assertEqual(site_availability._next_opening(s, now=_LUNES),
                         {"estado": "fecha", "dia": "2026-07-06", "hora": "08:00"})

    def test_no_future_web_slot_says_so_without_a_date(self):
        s = _service({"franjas": [dict(_MARTES, cupos={"web": 0, "reservado": 3})]})
        self.assertEqual(site_availability._next_opening(s, now=_LUNES), {"estado": "sin_fechas"})

    def test_walk_in_and_no_schedule_omit_the_line(self):
        walkin = _service({"franjas": [_MARTES]}, modo="walkin")
        self.assertIsNone(site_availability._next_opening(walkin, now=_LUNES))
        self.assertIsNone(site_availability._no_slots_info(walkin, now=_LUNES))
        sin_agenda = _service({})
        with mock.patch.object(site_catalog, "_doctors", return_value=[]):
            self.assertIsNone(site_availability._next_opening(sin_agenda, now=_LUNES))


class TestNoSlotsInfo(unittest.TestCase):
    def _centers(self, phone):
        return {"caps-x": {"slug": "caps-x", "name": "CAPS Ostende", "phone": phone}}

    def test_no_phone_even_when_the_center_has_one(self):
        # La contraparte técnica, 2026-10-18: «sacar la opción del teléfono» — the patient who
        # can't wait writes «Contanos tu caso» and the hospital calls back.
        s = _service({"franjas": [dict(_MARTES, cupos={"web": 0})]})
        with mock.patch.object(site_centers, "_centers_by_slug", return_value=self._centers("(02254) 49-1234")):
            info = site_availability._no_slots_info(s, now=_LUNES)
        self.assertEqual(info, {"apertura": {"estado": "sin_fechas"}})

    def test_slots_api_attaches_the_options_only_when_the_grid_is_empty(self):
        s = _service({"franjas": [dict(_MARTES, cupos={"web": 0})]})
        with mock.patch.object(site_catalog, "_service_by_id", return_value=s), \
                mock.patch.object(site_availability, "_appointments_for_service", return_value={}), \
                mock.patch.object(site_availability, "_no_slots_info",
                                  return_value={"apertura": {"estado": "sin_fechas"}}) as info:
            self.assertEqual(patient_site_router._service_slots_sync("svc-x", None, None, None),
                             {"sin_turnos": {"apertura": {"estado": "sin_fechas"}}})
        full = {"medicos": [{"turnos": [{"dia": "2026-07-07", "hora": "08:00"}]}]}
        with mock.patch.object(site_catalog, "_service_by_id", return_value=s), \
                mock.patch.object(site_availability, "_appointments_for_service", return_value=full), \
                mock.patch.object(site_availability, "_no_slots_info") as info:
            self.assertEqual(patient_site_router._service_slots_sync("svc-x", None, None, None), full)
            info.assert_not_called()


import _dbtest  # noqa: E402
from app.core import db_booking  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_no_slots",
                          seed=lambda: db_booking.get_booking_request(-1))
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestCaseTextPersists(unittest.TestCase):
    def test_the_case_is_stored_and_reaches_the_admin_queue(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        from app.core import db_booking
        fila = db_booking.create_booking_request_with_limit(
            "30111222", "Ana", "Pérez", "1980-01-01", "+5492254000000", "svc-x", "caps-x", 1,
            preferencia_horario="Cualquier horario",
            nota_paciente="Tengo un control post operatorio.")
        self.assertEqual(db_booking.get_booking_request(fila["id"])["nota_paciente"],
                         "Tengo un control post operatorio.")
        cola = [r for r in db_booking.list_admin() if r["id"] == fila["id"]]
        self.assertEqual(cola[0]["nota_paciente"], "Tengo un control post operatorio.")


class TestCaseTextReachesTheDesk(unittest.TestCase):
    def test_schema_wire_and_post_carry_the_case(self):
        from app.core import db_wire
        from app.core import db_schema
        self.assertIn("ADD COLUMN IF NOT EXISTS patient_note text", db_schema.BASELINE_SQL)
        self.assertEqual(db_wire._WIRE_BACK["booking_requests"]["patient_note"], "nota_paciente")
        with open(os.path.join(ROOT, "alembic", "versions", "0021_patient_note.py"), encoding="utf-8") as f:
            self.assertIn("patient_note", f.read())
        with open(os.path.join(ROOT, "app", "modules", "turnos", "booking_create.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('body.get("caso")', src)
        self.assertIn("nota_paciente=None if b.slot_dt is not None else b.nota_paciente", src)

    def test_desk_detail_shows_the_case(self):
        with open(os.path.join(ROOT, "static", "root", "admin-app.js"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("s.nota_paciente", src)
        self.assertIn('"admin.queue.solicitudes.caso_paciente"', src)


if __name__ == "__main__":
    unittest.main()
