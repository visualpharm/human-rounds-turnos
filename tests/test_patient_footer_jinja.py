"""Golden contracts for the shared patient-site footer and ASGI language switch.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p
"test_patient_footer_jinja.py"
"""

import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import jinja_env  # noqa: E402
from app.core import site_identity  # noqa: E402
from app.patient_web import site_shell


# 2026-09-14: Updated frozen footer bytes after removal of credit, mission, and
# software lines per the product owner's zero-small-print rule.
_DYNAMIC = {
    "es": (238, "667a82dd40b645dd19898f3007293231ebbe3aae42587efeb9c4886b4b2b7129"),
    "en": (233, "380c565565cac286601c8f06939ff7b045460867e7aa1cc266a601ce623aa0b8"),
}
_INDEX = {
    "es": "d8e4ee220b9af0e073e3639c5c0dbe4b405d913a41bcc47c97a80627e466e90b",
    "en": "ce6fd6795d585220b624686fd2fb8085f8ca9e2241baf352c22563064366a820",
}


def _test_identity(key):
    return {"city": "Test City", "brand": "Test Brand"}.get(
        key, site_identity._DEFAULTS.get(key, ""))


class TestPatientFooterJinja(unittest.TestCase):
    def test_dynamic_shell_and_index_keep_the_frozen_footer_bytes(self):
        for lang, (length, digest) in _DYNAMIC.items():
            with self.subTest(surface="dynamic", lang=lang):
                _bound, token = i18n.bind_lang(lang)
                try:
                    with patch.object(site_identity, "get", side_effect=_test_identity):
                        dynamic = site_shell._site_footer().encode("utf-8")
                        page = jinja_env.render("index.html")
                finally:
                    i18n.unbind(token)
                self.assertEqual(len(dynamic), length)
                self.assertEqual(hashlib.sha256(dynamic).hexdigest(), digest)
                start = page.index('  <footer class="site">')
                end = page.index('  </footer>', start) + len('  </footer>')
                self.assertEqual(hashlib.sha256(page[start:end].encode()).hexdigest(), _INDEX[lang])

    def test_one_partial_owns_the_footer_and_dynamic_shell_keeps_its_boundary(self):
        self.assertFalse(hasattr(site_shell, "_FOOTER"))
        self.assertEqual(site_shell._site_footer()[0], "\n")
        self.assertTrue(site_shell._site_footer().endswith("\n"))
        index = Path(ROOT, "templates", "index.html").read_text(encoding="utf-8")
        self.assertIn('{% include "_site_footer.html" -%}', index)

    def test_current_runtime_and_architecture_docs_name_only_the_partial_owner(self):
        current_roots = (
            Path(ROOT, "app", "patient_web"),
            Path(ROOT, "app", "core"),
            Path(ROOT, "templates"),
            Path(ROOT, "docs", "architecture"),
        )
        forbidden = ("site_shell._FOOTER", "site_shell.py _FOOTER", "hospital._FOOTER")
        offenders = []
        for root in current_roots:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in {".py", ".html", ".md"}:
                    text = path.read_text(encoding="utf-8")
                    if any(term in text for term in forbidden):
                        offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])


class TestPatientSiteRouterLanguageSwitch(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, LANGUAGES="es,en", LANG_SWITCHER_MODE="nav", LANG_DEFAULT=None)
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        i18n.invalidate()
        self._overrides = {}
        self._patch = patch.object(config, "_db_overrides",
                                   side_effect=lambda: dict(self._overrides))
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(config._reset_cache_para_tests)
        self.addCleanup(i18n.invalidate)
        from app.main import app
        from fastapi.testclient import TestClient
        self.client = TestClient(app)

    def tearDown(self):
        config.ENV_PATH = self._env_path

    def _set_enabled(self, value):
        self._overrides["ENABLED_MODULES"] = value
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    def test_switch_redirect_precedes_turnos_module_gate(self):
        self._set_enabled("intake")
        switched = self.client.get("/doctors?lang=en", follow_redirects=False)
        self.assertEqual(switched.status_code, 302)
        self.assertEqual(switched.content, b"")
        self.assertEqual(switched.headers["location"], "/doctors")
        self.assertIn("lang=en", switched.headers["set-cookie"])
        disabled = self.client.get("/doctors", follow_redirects=False)
        self.assertEqual(disabled.status_code, 404)
        self.assertEqual(disabled.json(), {"detail": {"error": "no_encontrado"}})


if __name__ == "__main__":
    unittest.main()
