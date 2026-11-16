"""/preparation/<id> — one public page per preparation guide that the active
catalog actually offers (SEO audit 2026-10-01, Pinamar item 17: «cuántas
horas de ayuno para análisis de sangre», «preparación para ecografía
abdominal / transvaginal» had no page, while the same instructions were
already published inside every service page).

The text is ONLY estudios-preparacion.json's (the sourced guide the service
pages already show); the page adds the list of services where the study is
done and the guide's own sources. Nothing medical is written here."""

import os
import re
import sys
import unittest
import _lifespan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.patient_web import site_catalog, site_page_service, site_preparation
from app.patient_web import seo  # noqa: E402
from app.patient_web import sitemap  # noqa: E402


def _visible(html):
    html = re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", html)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def setUpModule():
    _lifespan.no_background_services()


class TestPreparationPage(unittest.TestCase):
    def setUp(self):
        self.ids = site_preparation._prep_page_ids()
        self.assertIn("laboratorio-sangre", self.ids)
        self.prep = next(e for e in site_catalog._preparations() if e["id"] == "laboratorio-sangre")

    def test_page_shows_the_guide_and_where_to_do_it(self):
        page = site_preparation._page_preparation("laboratorio-sangre").decode("utf-8")
        self.assertEqual(page.count("<h1"), 1)
        self.assertIn(self.prep["nombre"], _visible(page))
        self.assertIn("8 horas", _visible(page))
        for item in self.prep["traer"]:
            self.assertIn(item, _visible(page))
        servicios = self.ids["laboratorio-sangre"]
        self.assertTrue(servicios)
        for s in servicios:
            self.assertIn(f'href="/service/{s["id"]}"', page)
        self.assertIn(f'href="{self.prep["sources"][0]["url"]}"', page)
        self.assertIn('<meta name="robots" content="index,follow">', page)
        self.assertIn(f'<link rel="canonical" href="{seo.canonical_url("/preparation/laboratorio-sangre")}">', page)

    def test_only_guides_the_catalog_offers_get_a_page(self):
        self.assertIsNone(site_preparation._page_preparation("no-existe"))
        offered = set(self.ids)
        for e in site_catalog._preparations():
            if e["id"] not in offered:
                self.assertIsNone(site_preparation._page_preparation(e["id"]), e["id"])

    def test_listed_in_the_sitemap(self):
        body = sitemap.xml().decode("utf-8")
        for pid in self.ids:
            self.assertIn(f"/preparation/{pid}</loc>", body)

    def test_service_page_links_its_guide(self):
        s = self.ids["laboratorio-sangre"][0]
        page = site_page_service._page_service(s["id"]).decode("utf-8")
        self.assertIn('href="/preparation/laboratorio-sangre"', page)

    def test_route_serves_it_and_unknown_ids_are_404(self):
        # A guide that is not (or no longer) in the catalog answers a real
        # 404, not a redirect to /d/diagnostico: Google reads a redirect
        # from a dead page to a section as a soft 404.
        from app import main as app_module
        from fastapi.testclient import TestClient
        with TestClient(app_module.app) as client:
            r = client.get("/preparation/laboratorio-sangre", follow_redirects=False)
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/html", r.headers.get("content-type", ""))
            r = client.get("/preparation/no-existe", follow_redirects=False)
            self.assertEqual(r.status_code, 404)
            self.assertIsNone(r.headers.get("location"))
            self.assertIn("text/html", r.headers.get("content-type", ""))
            self.assertIn('<meta name="robots" content="noindex', r.text)


if __name__ == "__main__":
    unittest.main()


class TestPreparationTitles(unittest.TestCase):
    """The H1 and the «Guía completa» link name the study without the
    catalog's placeholder notes (hr-pinamar review, 2026-10-07)."""

    def test_no_placeholder_in_any_h1(self):
        bad = re.compile(r"\((?:[^()]*especific[^()]*)\)", re.I)
        for pid in site_preparation._prep_page_ids():
            page = site_preparation._page_preparation(pid).decode("utf-8")
            h1 = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", page)
            self.assertIsNotNone(h1, pid)
            self.assertNotRegex(_visible(h1.group(1)), bad, pid)

    def test_no_placeholder_in_guide_links_and_no_duplicate_h1(self):
        bad = re.compile(r"\((?:[^()]*especific[^()]*)\)", re.I)
        titles = {}
        for e in site_catalog._preparations():
            title = site_preparation._prep_title(e.get("nombre"))
            self.assertNotRegex(title, bad, e.get("id"))
            if e.get("id") in site_preparation._prep_page_ids():
                self.assertNotIn(title, titles, f"{e['id']} and {titles.get(title)} share an H1")
                titles[title] = e["id"]

    def test_generic_entries_read_as_the_study(self):
        self.assertEqual(site_preparation._prep_title("Ecografía (tipo no especificado)"), "Ecografía")
        self.assertEqual(site_preparation._prep_title("Tomografía / TAC (sin especificar contraste)"), "Tomografía / TAC")
        self.assertEqual(site_preparation._prep_title("Perfil lipídico (colesterol, triglicéridos)"),
                         "Perfil lipídico (colesterol, triglicéridos)")
