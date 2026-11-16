"""test_i18n.py — i18n.py: catalog lookup/fallback, active-language
resolution (cookie/default/Accept-Language), switcher modes (off/nav/
secret), the {{t.*}}/{{lang}} template pipeline, and the
GET /api/i18n/<lang>.json route (ETag/304).

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_i18n.py"
"""

import io
import json
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import i18n_catalog_router  # noqa: E402
from app.core import site_identity  # noqa: E402
from app.core import site_identity_router  # noqa: E402


class FakeHandler:
    def __init__(self, path="/", method="GET", headers=None):
        self.path = path
        self.command = method
        self.headers = headers or {}
        self.status = None
        self.header_list = []
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.header_list.append((k, v))

    def end_headers(self):
        pass

    def header(self, k):
        for hk, hv in self.header_list:
            if hk.lower() == k.lower():
                return hv
        return None

    def _send_json(self, obj, status=200):
        self.status = status
        self.sent = obj


_ENV_KEYS = ("LANGUAGES", "LANG_DEFAULT", "LANG_SWITCHER_MODE",
             "CHAT_GREETING_ES", "CHAT_GREETING_EN")


def _native_client():
    app = FastAPI()
    app.include_router(i18n_catalog_router.router)
    app.include_router(site_identity_router.router)
    return TestClient(app)


class I18nTestBase(unittest.TestCase):
    def setUp(self):
        # registered first, so they run last: the caches are dropped after the environment is back
        self.addCleanup(i18n.invalidate)
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, **dict.fromkeys(_ENV_KEYS))
        # The DB settings overlay beats os.environ (config.get): a settings
        # row left in the shared test lane (ten languages, `nav`) turned
        # every «unconfigured» assertion red. These tests are about env.
        overrides = patch.object(config, "_db_overrides", return_value={})
        overrides.start()
        self.addCleanup(overrides.stop)
        config._reset_cache_para_tests()
        i18n.invalidate()


# ============================================================== catalogs / t()

class TestCatalogsOnDisk(I18nTestBase):
    def test_es_and_en_exist_with_the_about_page_keys(self):
        self.assertIn("es", i18n.available_locales())
        self.assertIn("en", i18n.available_locales())
        es = i18n._load_catalog("es")
        en = i18n._load_catalog("en")
        self.assertIn("about.hero.intro", es)
        self.assertIn("about.hero.intro", en)
        self.assertGreaterEqual(len(en), 25)  # the "~30 keys" proof seed

    def test_es_is_canonical_no_key_missing_from_es(self):
        # Every key en.json seeds also exists in es.json (the canonical
        # source every language translates FROM).
        es = i18n._load_catalog("es")
        en = i18n._load_catalog("en")
        for key in en:
            if key == "_comment":
                continue
            self.assertIn(key, es, f"{key} in en.json but not in canonical es.json")


class TestT(I18nTestBase):
    def test_t_resolves_from_active_language(self):
        self.assertEqual(i18n.t("about.hero.intro", "es"), "Acerca de")
        self.assertEqual(i18n.t("about.hero.intro", "en"), "About")

    def test_t_falls_back_to_es_when_key_missing_in_lang(self):
        # A language catalog missing a key falls back to the canonical
        # Spanish string, never a blank.
        self.assertEqual(i18n.t("about.hero.intro", "xx"), "Acerca de")

    def test_t_falls_back_to_the_key_itself_when_truly_missing(self):
        self.assertEqual(i18n.t("no.such.key", "es"), "no.such.key")

    def test_t_substitutes_variables(self):
        with patch.object(i18n, "_load_catalog",
                           lambda lang: {"greet": "Hola {name}!"} if lang == "zz" else {}):
            self.assertEqual(i18n.t("greet", "zz", name="Ilya"), "Hola Ilya!")

    def test_default_lang_is_es_when_key_missing_argument(self):
        self.assertEqual(i18n.t("about.hero.intro"), "Acerca de")


class TestRender(I18nTestBase):
    def test_render_replaces_t_tokens_html_escaped(self):
        out = i18n.render("<h1>{{t.about.hero.intro}}</h1>", "es")
        self.assertEqual(out, "<h1>Acerca de</h1>")

    def test_render_leaves_non_t_tokens_untouched(self):
        out = i18n.render("{{sitio.city}} and {{t.about.hero.intro}}", "es")
        self.assertEqual(out, "{{sitio.city}} and Acerca de")

    def test_render_escapes_html_special_chars_in_value(self):
        with patch.object(i18n, "_load_catalog",
                           lambda lang: {"x": "<b>&\"'"} if lang == "zz" else {}):
            out = i18n.render("{{t.x}}", "zz")
        self.assertNotIn("<b>", out)
        self.assertIn("&lt;b&gt;", out)


class TestInjectHtmlLangAndBootstrap(I18nTestBase):
    def test_inject_html_lang_replaces_token(self):
        self.assertEqual(
            i18n.inject_html_lang('<html lang="{{lang}}">', "en"),
            '<html lang="en">')

    def test_inject_html_lang_noop_without_token(self):
        self.assertEqual(i18n.inject_html_lang("<html lang=\"es\">", "en"),
                          '<html lang="es">')

    def test_inject_bootstrap_embeds_json_payload(self):
        out = i18n.inject_bootstrap("HEAD{{i18n_bootstrap}}TAIL", lang="es")
        self.assertTrue(out.startswith("HEAD<script>window.SANA_I18N="))
        self.assertTrue(out.endswith("TAIL"))
        blob = out[len("HEAD"):-len("TAIL")]
        raw_json = blob[len("<script>window.SANA_I18N="):-len(";</script>")]
        payload = json.loads(raw_json)
        self.assertEqual(payload["active"], "es")
        self.assertEqual(payload["enabled"], ["es"])
        self.assertEqual(payload["mode"], "off")
        # The catalog travels INLINE (2026-08-04) so SanaI18n.t() is correct
        # at parse time — the fetch-only version could flash a raw dotted key
        # and that was the blocker on translating any JS literal.
        self.assertIsInstance(payload["catalog"], dict)
        self.assertNotIn("_comment", payload["catalog"])
        # …and it is EMPTY on a single-language installation: the JS call
        # sites carry the canonical literal as their fallback, so there is
        # nothing to ship and nothing that renders differently.
        self.assertEqual(payload["catalog"], {})

    def test_bootstrap_catalog_is_inlined_once_a_second_language_is_on(self):
        _env.set_env(self, LANGUAGES="es,en")
        config._reset_cache_para_tests()
        i18n.invalidate()
        blob = i18n.bootstrap_script(lang="en")
        payload = json.loads(blob[len("<script>window.SANA_I18N="):
                                  -len(";</script>")])
        self.assertEqual(payload["active"], "en")
        self.assertEqual(payload["catalog"].get("footer.privacy"),
                         i18n.raw("footer.privacy", "en"))
        self.assertNotIn("_comment", payload["catalog"])

    def test_admin_chat_greeting_override_is_inlined_in_canonical_language(self):
        _env.set_env(self, CHAT_GREETING_ES="**Hola {brand}.**\n\n* Sacá un turno")
        config._reset_cache_para_tests()
        blob = i18n.bootstrap_script(lang="es")
        payload = json.loads(blob[len("<script>window.SANA_I18N="):
                                  -len(";</script>")])
        self.assertEqual(payload["catalog"]["chat.greeting"],
                         "**Hola {brand}.**\n\n* Sacá un turno")

    def test_admin_chat_greeting_override_uses_active_language(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, CHAT_GREETING_ES="Hola ES")
        _env.set_env(self, CHAT_GREETING_EN="Hello EN")
        config._reset_cache_para_tests()
        self.assertEqual(i18n._client_catalog("en")["chat.greeting"], "Hello EN")

    def test_inject_bootstrap_noop_without_token(self):
        self.assertEqual(i18n.inject_bootstrap("plain text"), "plain text")


# ============================================================== resolution

class TestEnabledLanguagesAndDefault(I18nTestBase):
    def test_default_is_just_es_when_unconfigured(self):
        self.assertEqual(i18n.enabled_languages(), ["es"])
        self.assertEqual(i18n.default_lang(), "es")

    def test_languages_config_filters_to_real_catalogs(self):
        _env.set_env(self, LANGUAGES="es,en,xx")  # xx has no catalog file
        self.assertEqual(i18n.enabled_languages(), ["es", "en"])

    def test_explicit_lang_default_wins_when_enabled(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, LANG_DEFAULT="en")
        self.assertEqual(i18n.default_lang(), "en")

    def test_lang_default_ignored_if_not_enabled(self):
        _env.set_env(self, LANGUAGES="es")
        _env.set_env(self, LANG_DEFAULT="en")
        self.assertEqual(i18n.default_lang(), "es")


class TestSwitcherMode(I18nTestBase):
    def test_default_mode_is_off(self):
        self.assertEqual(i18n.switcher_mode(), "off")

    def test_valid_modes_pass_through(self):
        for mode in ("off", "nav", "secret"):
            _env.set_env(self, LANG_SWITCHER_MODE=mode)
            self.assertEqual(i18n.switcher_mode(), mode)

    def test_unknown_mode_degrades_to_off(self):
        _env.set_env(self, LANG_SWITCHER_MODE="bogus")
        self.assertEqual(i18n.switcher_mode(), "off")


class TestResolveLang(I18nTestBase):
    def test_single_language_default_ignores_cookie(self):
        # LANGUAGES unset => only "es" enabled: even a crafted cookie can't
        # produce a different language. This is what keeps the default
        # golden-page behavior byte-identical.
        h = FakeHandler(headers={"Cookie": "lang=en"})
        self.assertEqual(i18n.resolve_lang(h), "es")

    def test_cookie_wins_when_language_enabled(self):
        _env.set_env(self, LANGUAGES="es,en")
        h = FakeHandler(headers={"Cookie": "lang=en"})
        self.assertEqual(i18n.resolve_lang(h), "en")

    def test_cookie_naming_a_disabled_language_is_ignored(self):
        _env.set_env(self, LANGUAGES="es,en")
        h = FakeHandler(headers={"Cookie": "lang=fr"})
        self.assertEqual(i18n.resolve_lang(h), "es")

    def test_explicit_admin_default_beats_accept_language(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, LANG_DEFAULT="es")
        h = FakeHandler(headers={"Accept-Language": "en-US,en;q=0.9"})
        self.assertEqual(i18n.resolve_lang(h), "es")

    def test_accept_language_breaks_the_tie_with_no_explicit_default(self):
        _env.set_env(self, LANGUAGES="es,en")
        h = FakeHandler(headers={"Accept-Language": "en-US,en;q=0.9,es;q=0.8"})
        self.assertEqual(i18n.resolve_lang(h), "en")

    def test_no_signal_at_all_falls_back_to_first_enabled(self):
        _env.set_env(self, LANGUAGES="en,es")
        h = FakeHandler()
        self.assertEqual(i18n.resolve_lang(h), "en")

    def test_none_handler_resolves_like_no_request_context(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, LANG_DEFAULT="en")
        self.assertEqual(i18n.resolve_lang(None), "en")


# ============================================================== routes

class TestCatalogRoute(I18nTestBase):
    def test_get_serves_the_catalog_with_etag(self):
        with _native_client() as client:
            response = client.get("/api/i18n/es.json")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.headers.get("etag"))
        self.assertEqual(response.json().get("about.hero.intro"), "Acerca de")

    def test_conditional_get_304_on_matching_etag(self):
        with _native_client() as client:
            first = client.get("/api/i18n/es.json")
            second = client.get(
                "/api/i18n/es.json",
                headers={"If-None-Match": first.headers["etag"]},
            )
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.content, b"")

    def test_get_catalog_includes_admin_chat_greeting_override(self):
        _env.set_env(self, CHAT_GREETING_ES="Saludo administrado")
        config._reset_cache_para_tests()
        with _native_client() as client:
            response = client.get("/api/i18n/es.json")
        self.assertEqual(response.json()["chat.greeting"], "Saludo administrado")

    def test_unknown_language_404s(self):
        with _native_client() as client:
            response = client.get("/api/i18n/klingon.json")
        self.assertEqual(response.status_code, 404)

    def test_post_is_not_claimed(self):
        with _native_client() as client:
            response = client.post("/api/i18n/es.json")
        self.assertEqual(response.status_code, 405)


class TestLangSwitchRoute(I18nTestBase):
    def test_off_mode_ignores_lang_param(self):
        _env.set_env(self, LANGUAGES="es,en")
        with _native_client() as client:
            response = client.get("/about.html?lang=en", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)

    def test_single_language_ignores_lang_param_even_in_nav_mode(self):
        _env.set_env(self, LANG_SWITCHER_MODE="nav")
        with _native_client() as client:
            response = client.get("/about.html?lang=en", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)

    def test_nav_mode_sets_cookie_and_redirects_clean(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, LANG_SWITCHER_MODE="nav")
        with _native_client() as client:
            response = client.get("/about.html?lang=en", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/about.html")
        cookie = response.headers.get("set-cookie", "")
        self.assertIn("lang=en", cookie)

    def test_secret_mode_also_works_undocumented(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, LANG_SWITCHER_MODE="secret")
        with _native_client() as client:
            response = client.get("/?lang=en", follow_redirects=False)
        self.assertEqual(response.status_code, 302)

    def test_unknown_lang_value_not_claimed(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, LANG_SWITCHER_MODE="nav")
        with _native_client() as client:
            response = client.get("/about.html?lang=fr", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)

    def test_no_lang_param_not_claimed(self):
        _env.set_env(self, LANGUAGES="es,en")
        _env.set_env(self, LANG_SWITCHER_MODE="nav")
        with _native_client() as client:
            response = client.get("/about.html", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("location", response.headers)


# ============================================================== site_identity wiring

class TestSiteIdentityPipeline(I18nTestBase):
    def test_default_render_page_is_es_and_fully_resolved(self):
        body, _ctype = site_identity.render_page("about.html")
        text = body.decode("utf-8")
        self.assertNotIn("{{t.", text)
        self.assertNotIn("{{sitio.", text)
        self.assertNotIn("{{lang}}", text)
        self.assertIn('<html lang="es"', text)
        self.assertIn("Acerca de", text)
        self.assertIn("Información clara, en un solo lugar", text)

    def test_render_page_with_handler_cookie_switches_language(self):
        _env.set_env(self, LANGUAGES="es,en")
        h = FakeHandler("/about.html", headers={"Cookie": "lang=en"})
        body, _ctype = site_identity.render_page("about.html", h)
        text = body.decode("utf-8")
        self.assertIn('<html lang="en"', text)
        self.assertIn("About", text)
        self.assertIn("Clear information, in one place", text)

    def test_index_html_default_unaffected_by_i18n_pipeline(self):
        # No {{t.*}} tokens on index.html: i18n.render() must be a byte
        # no-op there, and {{lang}} must still resolve to "es" by default
        # (golden-page byte-identity for "/").
        body, _ctype = site_identity.render_page("index.html")
        text = body.decode("utf-8")
        self.assertIn('<html lang="es"', text)
        self.assertNotIn("{{lang}}", text)


# ============================================================== module catalogs (Tier 2 pilot)

class TestModuleCatalogMerge(I18nTestBase):
    """i18n.py merges a module's OWN `locales/<lang>/*.json` dir into that
    language's catalog (docs/modules.md's `locales` manifest field,
    app_modules.module_locale_roots()). Tier 2 pilot, 2026-08-05:
    modules/intake/ is the first REAL manifest to use this — proven here
    against the actual on-disk catalog, not a synthetic fixture, so a
    regression in the merge (or in the move itself) fails a real test
    instead of just "looking right" in the browser."""

    def test_intake_nav_key_resolves_from_the_moved_module_catalog(self):
        # This key used to live in locales/{en,es}/admin.shell.json (the
        # platform's own catalog) back when "Entrevistas" was an admin.html
        # literal <a>; it moved into modules/intake/locales/ alongside the
        # sidebar entry becoming manifest-derived.
        self.assertEqual(i18n.t_or("intake.nav.entrevistas", "MISSING", "es"),
                          "Captura de síntomas")
        self.assertEqual(i18n.t_or("intake.nav.entrevistas", "MISSING", "en"),
                          "Symptom intake")

    def test_pre_existing_intake_keys_still_resolve_after_the_physical_move(self):
        # locales/{en,es}/intake.json moved to modules/intake/locales/ —
        # every key that lived there before the move must still resolve.
        self.assertNotEqual(i18n.t_or("intake.entrevista.title", "MISSING", "es"), "MISSING")

    def test_intake_namespace_still_discovered(self):
        self.assertIn("intake", i18n.namespaces("es"))

    def test_admin_shell_no_longer_owns_the_entrevistas_nav_key(self):
        # The key moved OUT of the platform catalog — leaving a stale copy
        # behind would silently mask a future regression in the merge.
        import json
        with open(os.path.join(i18n.LOCALES_DIR, "es", "admin.shell.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("admin.shell.nav.entrevistas", data)


if __name__ == "__main__":
    unittest.main()
