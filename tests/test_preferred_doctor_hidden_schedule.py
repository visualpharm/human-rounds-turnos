"""test_preferred_doctor_hidden_schedule.py — entering the booking wizard from a
professional's row (`/service/<id>/book?medico=<id>`) must show THAT person's
minutes or none, never the service's other professionals under no name.

Bug seen live (hospital administrator's video, 2026-10-14): a surgeon was
closed from today until the end of the month, the listing said "la fecha la
confirma el centro", and the wizard behind its button still offered Monday to
Thursday mornings — the pool of the service's OTHER surgeons, unnamed, as if
the closure did nothing. `_doctor_next` already follows the rule ("a schedule
hidden by the public horizon must not reappear via a fallback"); the wizard's
`_appointments_for_service_preferred` fell back to the whole service instead.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_preferred_doctor_hidden_schedule.py"
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.patient_web import catalog  # noqa: E402
from app.patient_web import site_availability, site_base, site_catalog, site_doctors
from app.patient_web import patient_site_router  # noqa: E402

_CENTROS = {"centros": [
    {"slug": "hospital", "name": "Hospital", "address": "Calle 1"},
]}
_SERVICIOS = {"servicios": [
    {"id": "cirugia", "nombre": "Cirugía", "centro": "hospital",
     "cluster": "otras", "modo": "solicitud"},
    {"id": "curaciones", "nombre": "Curaciones", "centro": "hospital",
     "cluster": "otras", "modo": "solicitud"},
]}


def _doctor(did, nombre, franjas, excepciones=()):
    """The real catalog mirrors the same windows in `agenda` and `centros[]`."""
    franjas = list(franjas)
    excepciones = list(excepciones)
    return {"id": did, "nombre": nombre, "especialidad": "Cirugía",
            "cluster": "otras", "centro": "hospital",
            "agenda": {"franjas": franjas, "excepciones": excepciones},
            "centros": [{"centro": "hospital", "franjas": franjas,
                         "excepciones": excepciones}]}


class PreferredDoctorHiddenScheduleTestCase(unittest.TestCase):
    def setUp(self):
        today = site_base._today_art()
        closed_until = (today + datetime.timedelta(days=120)).isoformat()
        all_week = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00",
                    "hasta": "23:59", "cada_min": 60, "servicio": "cirugia"}
        medicos = {"medicos": [
            # works Tuesdays at this service, closed for the whole horizon
            _doctor("cerrada", "Vera Llanos",
                    [{"dias": [2], "desde": "13:00", "hasta": "15:30",
                      "cada_min": 15, "servicio": "cirugia"}],
                    [{"tipo": "cerrado", "desde_fecha": today.isoformat(),
                      "hasta_fecha": closed_until, "motivo": "Turnos dados"}]),
            # the service's other surgeon: bookable every day, so the pool is full
            _doctor("abierto", "Tomás Ibarra", [all_week]),
            # windows exist, but for another office of the same center
            _doctor("otro-consultorio", "Elena Sosa",
                    [dict(all_week, servicio="curaciones")]),
            # no windows at all
            {"id": "sin-agenda", "nombre": "Raúl Pons", "especialidad": "Cirugía",
             "cluster": "otras", "centro": "hospital"},
        ]}
        self._tmp = tempfile.mkdtemp(prefix="preferred-hidden-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        for name, doc in (("centros-salud.json", _CENTROS),
                          ("servicios.json", _SERVICIOS),
                          ("medicos.json", medicos)):
            with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
                json.dump(doc, f)
        self.addCleanup(self._clear_caches)  # registered first: runs once the env is back
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        self._clear_caches()
        self.service = site_catalog._service_by_id("cirugia")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @staticmethod
    def _clear_caches():
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()

    def test_the_service_itself_still_offers_the_other_surgeons_minutes(self):
        data = site_availability._appointments_for_service(self.service)
        self.assertTrue(data.get("medicos"), "fixture: the service's pool must have slots")

    def test_a_closed_doctor_is_not_replaced_by_the_services_pool(self):
        out = site_availability._appointments_for_service_preferred(self.service, "cerrada")
        self.assertEqual(out, {}, "closed doctor: the wizard must ask for a franja, not show other people's minutes")

    def test_the_listing_and_the_wizard_agree_for_a_closed_doctor(self):
        # the cell promises "la fecha la confirma el centro"…
        px = site_availability._doctor_next(site_doctors._doctor_by_id("cerrada"))
        self.assertEqual(px.get("kind"), "solicitud")
        # …so the wizard behind its button offers no concrete minute
        sync = patient_site_router._service_slots_sync
        out = sync("cirugia", None, None, "cerrada")
        self.assertNotIn("medicos", out)
        # …and the empty step says when THAT doctor's turnos open again
        # (the reference-deployment counterpart, 2026-10-17), from her own windows, never the pool's.
        self.assertEqual(out["sin_turnos"]["apertura"]["estado"], "fecha")

    def test_an_open_doctor_comes_first_and_named(self):
        out = site_availability._appointments_for_service_preferred(self.service, "abierto")
        self.assertEqual(out["medicos"][0]["medico_id"], "abierto")
        self.assertEqual(out["preferido"], "abierto")

    def test_without_windows_at_this_service_the_pool_still_stands_in(self):
        # nothing of the doctor's own is hidden: there is nothing to protect
        for ref in ("sin-agenda", "otro-consultorio"):
            out = site_availability._appointments_for_service_preferred(self.service, ref)
            self.assertTrue(out.get("medicos"), ref)
            self.assertNotIn("preferido", out, ref)

    def test_an_unknown_reference_keeps_the_generic_answer(self):
        out = site_availability._appointments_for_service_preferred(self.service, "no-existe")
        self.assertTrue(out.get("medicos"))


if __name__ == "__main__":
    unittest.main()
