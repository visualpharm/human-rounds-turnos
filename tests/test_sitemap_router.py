"""test_sitemap_router.py — native ASGI transport coverage for
`GET /sitemap.xml` (docs/roadmap/native-migration-campaign-20260826.md,
family W2c). `sitemap.py` had no route logic to split (`xml()` was already
pure), so the assertions pin the native router's own byte/header contract
directly (status 200, `application/xml; charset=utf-8`,
`Cache-Control: public, max-age=3600`), both compressed and uncompressed."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.patient_web import sitemap  # noqa: E402


class TestSitemapRouter(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)

    def test_uncompressed_contract(self):
        r = self.client.get("/sitemap.xml", headers={"Accept-Encoding": "identity"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, sitemap.xml())
        self.assertEqual(r.headers.get("content-type"), "application/xml; charset=utf-8")
        self.assertEqual(r.headers.get("cache-control"), "public, max-age=3600")

    def test_starts_with_the_home_url(self):
        r = self.client.get("/sitemap.xml", headers={"Accept-Encoding": "identity"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"<urlset", r.content)
        self.assertIn(b"<loc>", r.content)

    def test_gzips_when_accepted(self):
        r = self.client.get("/sitemap.xml", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-encoding"), "gzip")
        self.assertEqual(r.headers.get("vary"), "Accept-Encoding")


class TestSitemapContent(unittest.TestCase):
    """What the sitemap lists (SEO audit 2026-10-01, Pinamar items 2, 4, 14)."""

    def _locs(self):
        import re
        return re.findall(r"<loc>([^<]+)</loc>", sitemap.xml().decode("utf-8"))

    def test_every_cluster_page_with_content_is_listed(self):
        # /d/clinica (medicina general — the entry point of a patient without
        # a referral) answered 200 and was indexable but was missing.
        paths = {u.split("://", 1)[1].split("/", 1)[1] for u in self._locs()}
        self.assertIn("d/clinica", paths)
        self.assertIn("d/diagnostico", paths)
        from app.patient_web import site_page_doctors
        for p in paths:
            if p.startswith("d/"):
                self.assertIsNotNone(site_page_doctors._page_cluster(p[2:]), p)

    def test_no_priority_and_lastmod_only_from_real_edits(self):
        import datetime as _dt
        from unittest import mock
        from app.patient_web import catalog
        svc = catalog.services()[0]["id"]
        # 02:30 UTC on Oct 3 is still Oct 2 in Buenos Aires (UTC-3).
        edited = _dt.datetime(2026, 10, 3, 2, 30, tzinfo=_dt.timezone.utc)
        row = {"id": svc, "doc": dict(catalog.services()[0]), "borrado": False,
               "created": edited, "updated": edited}
        real = catalog._db_rows
        with mock.patch.object(catalog, "_db_rows",
                               lambda tipo, strict=False: [row] if tipo == "servicios" else real(tipo, strict)):
            body = sitemap.xml().decode("utf-8")
        self.assertNotIn("<priority>", body)
        self.assertIn(f"/service/{svc}</loc><lastmod>2026-10-02</lastmod>", body)
        # Items nobody edited carry no invented date.
        self.assertEqual(body.count("<lastmod>"), 1)

    def test_a_failing_preparation_index_keeps_the_doctors(self):
        # The /preparation/<id> pages are an add-on: if building their index
        # fails, the doctors, services and centers must still be listed.
        from unittest import mock
        from app.patient_web import catalog, site_preparation
        doctor = next(m["id"] for m in catalog.doctors() if m.get("id") and not m.get("rol"))
        with mock.patch.object(site_preparation, "_prep_page_ids",
                               side_effect=RuntimeError("boom")):
            body = sitemap.xml().decode("utf-8")
        self.assertTrue(f"/doctor/{doctor}</loc>" in body, "doctors dropped")
        self.assertTrue("/service/" in body, "services dropped")
        self.assertTrue("/center/" in body, "centers dropped")
        self.assertFalse("/preparation/" in body)


if __name__ == "__main__":
    unittest.main()
