"""A doctor whose schedule names several services is found under EACH of
them (probador en sitio, 2026-10-21: a test doctor with Cardiología, Cirugía and
Electrocardiograma only showed under Cardiología).

No database, no network: the shipped catalog plus one synthetic doctor
patched into the professionals list.
"""
import unittest
from unittest import mock

from app.patient_web import (
    site_availability,
    site_catalog,
    site_doctors,
    site_listing,
    site_page_doctors,
    site_page_service,
)


def _three_services():
    """Three bookable, non-walk-in services of one center, of at least two
    different clusters when the catalog has them."""
    by_center = {}
    for s in site_catalog._services():
        if s.get("centro") and s.get("id") and site_availability._offers_appointments(s):
            by_center.setdefault(s["centro"], []).append(s)
    pools = sorted(by_center.values(), key=lambda xs: -len({x.get("cluster") for x in xs}))
    if not pools or len(pools[0]) < 3:
        return None
    pool, picked, clusters = pools[0], [], set()
    for s in pool:
        if s.get("cluster") not in clusters:
            picked.append(s)
            clusters.add(s.get("cluster"))
        if len(picked) == 3:
            break
    for s in pool:
        if len(picked) == 3:
            break
        if s not in picked:
            picked.append(s)
    return picked


class TestDoctorWithSeveralServices(unittest.TestCase):
    def setUp(self):
        self.services = _three_services()
        if not self.services:
            self.skipTest("the installed catalog has no center with three bookable services")
        main = self.services[0]
        self.doctor = {
            "id": "prueba-varios-servicios", "nombre": "Prueba Varios Servicios",
            "especialidad": main.get("nombre"), "cluster": main.get("cluster"),
            "centro": main["centro"],
            "centros": [{"centro": main["centro"], "excepciones": [], "franjas": [
                {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20,
                 "servicio": s["id"]} for s in self.services]}],
        }
        doctors = site_catalog._doctors() + [self.doctor]
        for owner, name, value in (
                (site_catalog, "_doctors", lambda: doctors),
                (site_doctors, "_doctor_by_id", lambda mid: next((d for d in doctors if d.get("id") == mid), None))):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_tagged_service_is_listed_in_schedule_order(self):
        self.assertEqual([s["id"] for s in site_doctors._doctor_tagged_services(self.doctor)],
                         [s["id"] for s in self.services])

    def test_each_service_page_lists_the_doctor(self):
        for s in self.services:
            html = site_page_service._page_service(s["id"]).decode("utf-8")
            self.assertIn("Prueba Varios Servicios", html, s["id"])

    def test_doctor_page_names_every_service_with_its_booking_link(self):
        html = site_page_doctors._page_doctor(self.doctor["id"]).decode("utf-8")
        # «Cuándo atiende» names every service, each booking with this
        # doctor, so no «Atiende:» line repeats them (hr-patient review #23)
        self.assertNotIn("Atiende:", html)
        for s in self.services:
            self.assertIn(site_listing._appointment_href(s) + "?medico=prueba-varios-servicios", html)

    def test_listing_row_for_a_second_service_books_that_service(self):
        second = self.services[1]
        row = site_listing._doctor_listing_row(self.doctor, second)
        self.assertTrue(row["turno_href"].startswith(site_listing._appointment_href(second)))
        self.assertIn(second["nombre"], row["palabras"])


if __name__ == "__main__":
    unittest.main()
