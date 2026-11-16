"""A test doctor never reaches a public surface (pinamar review 2026-11-15,
item 1: «Laura Prueba (test)», created in the panel with name, specialty, center
and a schedule, was bookable on /doctors and counted on the home page).

No database, no network: the shipped catalog plus the test doctor injected
through the admin overlay seam (`site_base._with_overlay`), the way the live
one arrives.
"""
import unittest
from unittest import mock

import _env  # noqa: F401
from app.patient_web import (
    catalog,
    site_base,
    site_catalog,
    site_doctors,
    site_listing,
    site_page_doctors,
    sitemap,
)


def _clone_as_test(base, **extra):
    m = dict(base)
    m.update({"id": "laura-prueba-test", "nombre": "Laura Prueba (test)"}, **extra)
    return m


class TestIsTestDoctor(unittest.TestCase):
    def test_name_marker_and_flag(self):
        self.assertTrue(catalog.is_test_doctor({"nombre": "Laura Prueba (test)"}))
        self.assertTrue(catalog.is_test_doctor({"nombre": "Ana Pérez ( Prueba )"}))
        self.assertTrue(catalog.is_test_doctor({"nombre": "Ana Pérez", "prueba": True}))

    def test_real_doctors_are_not_test(self):
        self.assertFalse(catalog.is_test_doctor({"nombre": "Ana Testa"}))
        self.assertFalse(catalog.is_test_doctor({"nombre": "Ana Pérez", "prueba": "yes"}))
        self.assertFalse(catalog.is_test_doctor(None))


class TestTestDoctorStaysOffThePublicSite(unittest.TestCase):
    def setUp(self):
        real = site_catalog._professionals()
        if not real:
            self.skipTest("the shipped catalog has no professionals")
        self.base = real[0]
        self.test_doctor = _clone_as_test(self.base)
        original = site_base._with_overlay

        def overlay(tipo, base):
            out = original(tipo, base)
            return out + [dict(self.test_doctor)] if tipo == "medicos" else out
        patcher = mock.patch.object(site_base, "_with_overlay", overlay)
        patcher.start()
        self.addCleanup(patcher.stop)
        site_catalog._SERVICES_MEMO.clear()

    def test_not_in_the_doctor_lists(self):
        ids = {m.get("id") for m in site_catalog._doctors()}
        self.assertNotIn("laura-prueba-test", ids)
        self.assertIn(self.base["id"], ids)

    def test_not_on_doctors_page_nor_its_own_page(self):
        html = site_page_doctors._page_doctors().decode("utf-8")
        self.assertNotIn("laura-prueba-test", html)
        self.assertIsNone(site_page_doctors._page_doctor("laura-prueba-test"))
        self.assertIsNone(site_doctors._doctor_by_id("laura-prueba-test"))

    def test_not_counted_on_the_home_directory(self):
        before = {c["id"]: c["n_medicos"] for c in site_listing._directory_data()["clusters"]}
        with mock.patch.object(catalog, "is_test_doctor", return_value=False):
            with_test = {c["id"]: c["n_medicos"] for c in site_listing._directory_data()["clusters"]}
        self.assertEqual(sum(with_test.values()) - sum(before.values()),
                         len(site_catalog._doctor_clusters(self.base)))

    def test_not_in_the_sitemap_nor_the_chat_search(self):
        with mock.patch.object(catalog, "doctors",
                               return_value=[dict(self.base), dict(self.test_doctor)]):
            self.assertEqual([m["id"] for m in catalog.public_doctors()], [self.base["id"]])
            locs = sitemap.xml().decode("utf-8")
        self.assertNotIn("/doctor/laura-prueba-test", locs)


if __name__ == "__main__":
    unittest.main()
