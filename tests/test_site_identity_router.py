"""test_site_identity_router.py — native ASGI route contract for the
canonical public identity pages (docs/roadmap/native-migration-campaign-
20260826.md, families F9 + W2c; the dark-vs-native comparison this file
used to run was retired with the strict core/ops handle sweep, 2026-08-30 —
`site_identity.handle()` is deleted and the router is pinned directly).
test_site_identity.py already covers `site_identity.render_page()`/
sitio.json resolution directly; this file proves the FastAPI router serves
those same pages, and that the `?lang=` switch (extracted as
`i18n.resolve_lang_switch()`) redirects/sets the cookie with no
legacy-shim transport involved.

`/llms.txt`/`/robots.txt` (family W2c) reuse the same `render_page()` — the
test below pins the router's bytes against that canonical renderer."""

import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.core import site_identity  # noqa: E402


class TestSiteIdentityRouter(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app, follow_redirects=False)
        self.addCleanup(config._reset_cache_para_tests)

    def test_all_five_public_pages_render_html(self):
        for path in ("/", "/index.html", "/about.html", "/privacy.html", "/terms.html"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.headers["content-type"], "text/html; charset=utf-8")
            self.assertEqual(r.headers["cache-control"], "no-store")

    def test_lang_switch_is_a_noop_by_default(self):
        # LANG_SWITCHER_MODE defaults to "off" -> the ?lang= query is
        # ignored and the page renders normally, same as the legacy
        # i18n.handle()'s own default-off contract.
        r = self.client.get("/", params={"lang": "en"})
        self.assertEqual(r.status_code, 200)

    def test_lang_switch_redirects_and_sets_cookie_when_enabled(self):
        # No DATABASE_URL in this test process -> set the config keys
        # through the env-var fallback (config.get()'s DB > env precedence
        # degrades cleanly with no DB configured) instead of config.guardar(),
        # which requires a live connection.
        _env.set_env(self, LANGUAGES="es,en", LANG_SWITCHER_MODE="nav")
        r = self.client.get("/about.html", params={"lang": "en"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/about.html")
        self.assertIn("lang=en", r.headers["set-cookie"])

    def test_llms_txt_and_robots_txt_serve_the_canonical_renderer_bytes(self):
        # gzip disabled on the request so the bytes compare uncompressed
        # directly (gzip-if-accepted is exercised right below). The router's
        # plain-text endpoints must be exactly what the canonical renderer
        # produces — no unresolved token anywhere in the public file.
        for path, filename in (("/llms.txt", "llms.txt"), ("/robots.txt", "robots.txt")):
            with self.subTest(path=path):
                r = self.client.get(path, headers={"Accept-Encoding": "identity"})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["content-type"], "text/plain; charset=utf-8")
                self.assertEqual(r.headers["cache-control"], "no-store")
                body, _ctype = site_identity.render_page(filename)
                self.assertEqual(r.content, body)
                self.assertNotIn(b"{{", r.content)

    def test_llms_txt_gzips_when_accepted(self):
        r = self.client.get("/llms.txt", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-encoding"), "gzip")
        self.assertEqual(r.headers.get("vary"), "Accept-Encoding")


if __name__ == "__main__":
    unittest.main()
