"""SEO contract for public, private, and retired page templates."""

import html
import os
import re
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")

from app.patient_web import (
    site_catalog,
    site_centers,
    site_news,
    site_page_centers,
    site_page_doctors,
    site_page_service,
)
from app.patient_web import seo


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFERENCE_ORIGIN = "https://pinamar.humanrounds.org"


def _assert_public(test, page, path):
    if isinstance(page, bytes):
        page = page.decode("utf-8")
    test.assertEqual(page.count("<title>"), 1)
    test.assertEqual(page.count('<meta name="description"'), 1)
    test.assertIn('<meta name="robots" content="index,follow">', page)
    test.assertIn(
        f'<link rel="canonical" href="{REFERENCE_ORIGIN}{path}">',
        page,
    )
    test.assertIn('<meta property="og:title"', page)
    test.assertIn('<meta name="twitter:card" content="summary">', page)
    match = re.search(r'<meta name="description" content="([^"]+)">', page)
    test.assertIsNotNone(match)
    description = html.unescape(match.group(1))
    test.assertGreaterEqual(len(description), 50)
    test.assertLessEqual(len(description), 161)


class TestSeoHelper(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN=REFERENCE_ORIGIN)

    def test_public_head_is_complete_and_escaped(self):
        head = seo.head(
            'Cardiología & corazón | Pinamar Turnos',
            'Consultá médicos, centros y turnos de cardiología en Pinamar.',
            "/d/cardiologia",
        )
        self.assertIn("Cardiología &amp; corazón", head)
        self.assertIn(
            '<link rel="canonical" href="https://pinamar.humanrounds.org/d/cardiologia">',
            head,
        )
        self.assertIn('<meta property="og:url"', head)
        self.assertIn('<meta name="twitter:description"', head)

    def test_unconfigured_checkout_uses_the_local_development_origin(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN=None)
        self.assertEqual(seo.origin(), "http://127.0.0.1:7700")

    def test_origin_uses_shared_runtime_config_precedence(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN=None)
        with patch("app.core.config.get",
                   return_value="https://configured.example/"):
            self.assertEqual(seo.origin(), "https://configured.example")

    def test_private_head_has_no_public_preview(self):
        head = seo.head(
            "Mi solicitud | Pinamar Turnos",
            "Consultá el estado de tu solicitud de turno.",
            index=False,
        )
        self.assertIn('<meta name="robots" content="noindex,nofollow">', head)
        self.assertNotIn('rel="canonical"', head)
        self.assertNotIn('property="og:', head)

    def test_empty_description_is_rejected(self):
        with self.assertRaises(ValueError):
            seo.head("Página | Pinamar Turnos", "", "/pagina")


class TestRenderedSeo(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN=REFERENCE_ORIGIN)

    def test_public_page_families_have_metadata(self):
        center = site_centers._centers()[0]
        service = site_catalog._services()[0]
        doctor = site_catalog._professionals()[0]
        pages = [
            (site_page_centers._page_centers(), "/centers"),
            (site_page_centers._page_center(center["slug"]), f'/center/{center["slug"]}'),
            (site_page_service._page_service(service["id"]), f'/service/{service["id"]}'),
            (site_page_doctors._page_doctors(), "/doctors"),
            (site_page_doctors._page_doctor(doctor["id"]), f'/doctor/{doctor["id"]}'),
        ]
        for page, path in pages:
            with self.subTest(path=path):
                _assert_public(self, page, path)

    def test_news_changelog_is_not_indexed_nor_in_the_sitemap(self):
        # /news is the hand-curated changelog for patients AND staff
        # (novedades-changelog skill), ~7,700 words of panel changes with no
        # search demand: it stays reachable from the footer but out of the
        # index and the sitemap (SEO audit 2026-10-01, Pinamar item 4).
        page = site_news._page_news().decode("utf-8")
        self.assertIn('<meta name="robots" content="noindex,nofollow">', page)
        self.assertNotIn('rel="canonical"', page)
        from app.patient_web import sitemap
        self.assertNotIn(b"/news</loc>", sitemap.xml())

    def test_llms_txt_does_not_recommend_news(self):
        # /news is noindex (above): llms.txt must not send AI agents there
        # either, or the two signals contradict each other.
        from app.core import site_identity
        body, _ctype = site_identity.render_page("llms.txt", lang="es")
        self.assertFalse("/news)" in body.decode("utf-8"),
                         "llms.txt still recommends /news")

    def test_transactional_pages_are_not_indexed(self):
        service = next(
            s for s in site_catalog._services()
            if s.get("modo") in ("solicitud", "solicitud_con_orden")
        )
        pages = [
            site_page_service._page_service_appointment(service["id"]).decode("utf-8"),
        ]
        for page in pages:
            self.assertIn('<meta name="robots" content="noindex,nofollow">', page)
            self.assertNotIn('rel="canonical"', page)
            self.assertNotIn('property="og:', page)

    def test_account_and_auth_templates_are_not_indexed(self):
        for filename in (
                "account.html", "account-reset.html", "auth-test.html",
                "auth-sso.html"):
            with open(os.path.join(ROOT, "templates", filename),
                      encoding="utf-8") as fh:
                page = fh.read()
            with self.subTest(filename=filename):
                self.assertIn('<meta name="description"', page)
                self.assertIn(
                    '<meta name="robots" content="noindex,nofollow">', page)

    def test_static_public_templates_have_complete_metadata(self):
        for filename in ("index.html", "about.html", "privacy.html", "terms.html"):
            with open(os.path.join(ROOT, "templates", filename), encoding="utf-8") as fh:
                page = fh.read()
            with self.subTest(filename=filename):
                self.assertEqual(page.count("<title>"), 1)
                self.assertEqual(page.count('<meta name="description"'), 1)
                self.assertIn('<meta name="robots" content="index,follow">', page)
                self.assertIn('rel="canonical"', page)
                self.assertIn('<meta property="og:title"', page)
                self.assertIn('<meta name="twitter:card" content="summary">', page)

    def test_admin_template_has_description_and_noindex(self):
        # Direct admin screens inherit this head.  The title and description
        # stay translated through the admin.shell namespace.
        with open(os.path.join(ROOT, "templates", "admin", "base.html"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("admin.shell.meta.title", page)
        self.assertIn("admin.shell.meta.description", page)
        self.assertIn('<meta name="description"', page)
        self.assertIn('<meta name="robots" content="noindex,nofollow">', page)

    def test_retired_templates_are_absent_and_their_routes_redirect_home(self):
        # The former noindex fallback heads lived on disk behind the 302s.
        # Physical deletion is the SEO contract now: nothing to index, and
        # the public routes still 302 home (query dropped, no cache header).
        for filename in ("app.html", "panel.html", "directorio.html"):
            with self.subTest(filename=filename):
                self.assertFalse(
                    os.path.exists(os.path.join(ROOT, filename)), filename)
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module
        from fastapi.testclient import TestClient
        client = TestClient(app_module.app)
        for path in ("/app.html", "/app.html?foo=bar",
                     "/panel.html", "/panel.html?x=1",
                     "/directorio.html", "/directorio.html?q=1"):
            with self.subTest(path=path):
                resp = client.get(path, follow_redirects=False)
                self.assertEqual(resp.status_code, 302, msg=path)
                self.assertEqual(resp.headers["location"], "/", msg=path)
                self.assertFalse(resp.headers.get("cache-control"), msg=path)


class TestStructuredData(unittest.TestCase):
    """schema.org on center and doctor pages (SEO audit 2026-10-01, Pinamar
    item 11): the hospital is a Hospital, the CAPS a MedicalClinic, and every
    url is absolute."""

    @staticmethod
    def _ld(page):
        import json as _json
        import re as _re
        blocks = _re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, _re.S)
        return [_json.loads(b) for b in blocks]

    def test_center_pages_declare_hospital_or_clinic(self):
        from app.core import site_identity
        main = site_identity.get("main_center")
        for c in site_centers._centers():
            page = site_page_centers._page_center(c["slug"]).decode("utf-8")
            with self.subTest(center=c["slug"]):
                ld = [d for d in self._ld(page) if d.get("@type") in ("Hospital", "MedicalClinic")]
                self.assertEqual(len(ld), 1)
                ld = ld[0]
                self.assertEqual(ld["@type"], "Hospital" if c["slug"] == main else "MedicalClinic")
                self.assertEqual(ld["name"], c["name"])
                self.assertEqual(ld["url"], seo.canonical_url(f'/center/{c["slug"]}'))
                if c.get("address"):
                    self.assertEqual(ld["address"]["streetAddress"], c["address"])
                if c.get("phone"):
                    self.assertTrue(ld["telephone"])

    def test_physician_url_is_absolute_and_member_of_is_typed(self):
        from app.core import site_identity
        main = site_identity.get("main_center")
        doctor = next(m for m in site_catalog._professionals()
                      if m.get("centro") == main and not m.get("demo"))
        page = site_page_doctors._page_doctor(doctor["id"]).decode("utf-8")
        ld = next(d for d in self._ld(page) if d.get("@type") == "Physician")
        self.assertEqual(ld["url"], seo.canonical_url(f'/doctor/{doctor["id"]}'))
        self.assertEqual(ld["memberOf"]["@type"], "Hospital")
        self.assertEqual(ld["memberOf"]["url"], seo.canonical_url(f"/center/{main}"))


class TestCenterPhotoAlt(unittest.TestCase):
    """Gallery photos don't all share the center's name as alt (SEO audit
    2026-10-01, Pinamar item 12: 11 of 12 images on /center/hospital-pinamar
    had alt="Hospital de Pinamar")."""

    def test_gallery_alt_uses_data_then_a_numbered_fallback(self):
        c = site_centers._centers()[0]
        gal = {c["slug"]: [{"src": "/g/1.jpg", "alt": "Entrada de la guardia"},
                           {"src": "/g/2.jpg"}, {"src": "/g/3.jpg"}]}
        with patch.object(site_centers, "_centers_gallery", return_value=gal):
            page = site_page_centers._page_center(c["slug"]).decode("utf-8")
        alts = re.findall(r'<img src="/g/\d\.jpg" alt="([^"]*)"', page)
        self.assertEqual(alts[0], "Entrada de la guardia")
        self.assertEqual(len(set(alts)), 3)
        self.assertEqual(alts[1], f'{c["name"]}, foto 2 de 3')


if __name__ == "__main__":
    unittest.main()
