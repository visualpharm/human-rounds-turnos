"""Standing contracts for the four canonical Accounts Jinja pages.

Every test below exercises ONLY the native path (`accounts_router.render_*` /
`.lang_switch_response`) and asserts its status/headers/body directly.
Redirects, test-auth concealment, language switching, bootstrap escaping, and
the absence of inline HTML copies are absolute native assertions.

The rendered pages are pinned by `tests/snapshots.json` (section `pages`), not
by literals in this file: the snapshot hashes each page with its first-party
asset fingerprints replaced by a placeholder, so markup, copy, the English
bootstrap catalog and the set of linked assets are pinned while a CSS/JS edit
that only moves a content hash is not. The hashes themselves are checked
against the files by `snapshots.assert_page`. After a deliberate change to a
page, a template or a catalog: `python3 tests/snapshots.py --write`, and read
what it reports as changed. History of the old whole-page pins (proofs by URL
reversal) is in docs/solutions/security/*-render-proof.json."""
import contextlib
import json
import os
import sys
import unittest
from unittest import mock
from starlette.requests import Request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.core import config  # noqa: E402

from app.auth import mode as auth_mode  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import proxy_trust  # noqa: E402
from app.core import theme  # noqa: E402
from app.cuentas import accounts  # noqa: E402
from app.cuentas import accounts_router  # noqa: E402

_HTML_PATHS = ("/account", "/account/reset", "/auth/test", "/auth/sso")


def _reset_caches():
    config._reset_cache_para_tests()
    auth_mode._cache["v"] = None
    auth_mode._cache["t"] = 0.0
    i18n.invalidate()


_MODULE_ENV = {
    "TURNOS_FAKE_AI": "1",
    "TURNOS_SKIP_HSI": "1",
    "TURNOS_ASGI": "1",
    "TURNOS_ENV_FILE": os.path.join(ROOT, "turnos-env-file-absent"),
    "DATABASE_URL": None,
    "TEST_DATABASE_URL": None,
}


class _ModuleEnvironmentScope(object):
    """Keep this hermetic module from changing the serial test process."""
    def __init__(self):
        self.env_path_before = None
        self._stack = None

    def __enter__(self):
        self.env_path_before = config.ENV_PATH
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(_env.patched_env(_MODULE_ENV))
        config.ENV_PATH = ""
        _reset_caches()
        return self

    def __exit__(self, *exc):
        self._stack.close()
        config.ENV_PATH = self.env_path_before
        _reset_caches()


_module_environment = None


def setUpModule():
    global _module_environment
    _module_environment = _ModuleEnvironmentScope()
    _module_environment.__enter__()


def tearDownModule():
    global _module_environment
    if _module_environment is not None:
        _module_environment.__exit__(None, None, None)
        _module_environment = None


def _request(path, lang=None, extra_headers=None):
    headers = {
        "Host": "127.0.0.1:7700",
        proxy_trust.HEADER: proxy_trust.sign("127.0.0.1"),
    }
    if lang:
        headers["Cookie"] = "lang=%s" % lang
    if extra_headers:
        headers.update(extra_headers)
    raw_path, _, raw_query = path.partition("?")
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("ascii"),
        "query_string": raw_query.encode("ascii"),
        "headers": [
            (str(key).lower().encode("latin-1"), str(value).encode("latin-1"))
            for key, value in headers.items()
        ],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 7700),
    })


def _header_map(items):
    return {str(k).lower(): str(v) for k, v in items}


def _native(render_fn, path, lang=None, **kwargs):
    request = _request(path, lang)
    bound, token = i18n.bind(request)
    try:
        resp = render_fn(request, **kwargs) if kwargs else render_fn(request)
        return resp.status_code, bytes(resp.body), _header_map(resp.headers.items())
    finally:
        i18n.unbind(token)


class _Env(object):
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._stack = None

    def __enter__(self):
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(_env.patched_env(self.kwargs))
        _reset_caches()
        return self

    def __exit__(self, *exc):
        self._stack.close()
        _reset_caches()


class TestModuleEnvironmentScope(unittest.TestCase):
    def test_preexisting_managed_environment_and_config_are_restored(self):
        before = {
            "TURNOS_ENV_FILE": "/managed/turnos.env",
            "DATABASE_URL": "postgresql://managed/database",
            "TEST_DATABASE_URL": "postgresql://managed/test-database",
        }
        config_before = config.ENV_PATH
        self.addCleanup(_reset_caches)
        self.addCleanup(setattr, config, "ENV_PATH", config_before)
        _env.set_env(self, before)
        config.ENV_PATH = "/managed/config.env"
        _reset_caches()
        with _ModuleEnvironmentScope():
            self.assertEqual(
                os.environ["TURNOS_ENV_FILE"],
                _MODULE_ENV["TURNOS_ENV_FILE"])
            self.assertNotIn("DATABASE_URL", os.environ)
            self.assertNotIn("TEST_DATABASE_URL", os.environ)
            self.assertEqual(config.ENV_PATH, "")
        self.assertEqual(
            {key: os.environ.get(key) for key in before}, before)
        self.assertEqual(config.ENV_PATH, "/managed/config.env")


def _password_mode():
    return _Env(TURNOS_AUTH_MODE="password", TURNOS_RENAPER_PROVIDER=None,
                TURNOS_AUTH_METHODS=None, TURNOS_FAKE_RENAPER=None,
                TURNOS_DEMO_MODE=None, LANG_SWITCHER_MODE=None)


def _test_auth_on():
    proxy_test_value = "accounts-jinja-test-proxy"
    return _Env(TURNOS_AUTH_MODE="renaper", TURNOS_RENAPER_PROVIDER="stub",
                TURNOS_AUTH_METHODS=None, TURNOS_FAKE_RENAPER=None,
                TURNOS_DEMO_MODE=None,
                **{"TURNOS_PROXY_TRUST_SECRET": proxy_test_value})


def _renaper_entry():
    return _Env(TURNOS_AUTH_MODE="renaper", TURNOS_RENAPER_PROVIDER="stub",
                TURNOS_AUTH_METHODS="password,renaper", TURNOS_FAKE_RENAPER="1")


def _switcher_on():
    return _Env(LANGUAGES="es,en", LANG_SWITCHER_MODE="secret")


class _Page(object):
    def __init__(self, key, path, render, lang, auth):
        self.key, self.path, self.render = key, path, render
        self.lang, self.auth = lang, auth


# One entry per snapshotted render: Spanish and English, with the password
# entry (`/account`, `/account/reset`) and the local test-auth simulator
# (`/auth/test`, `/auth/sso`), plus the anonymous request without a cookie.
_PAGES = (
    _Page("es/account", "/account", "render_account", "es", "password"),
    _Page("es/account-reset", "/account/reset", "render_reset", "es", "password"),
    _Page("es/auth-test", "/auth/test", "render_test_auth", "es", "test"),
    _Page("es/auth-sso", "/auth/sso", "render_sso", "es", "test"),
    _Page("en/account", "/account", "render_account", "en", "password"),
    _Page("en/account-reset", "/account/reset", "render_reset", "en", "password"),
    _Page("en/auth-test", "/auth/test", "render_test_auth", "en", "test"),
    _Page("en/auth-sso", "/auth/sso", "render_sso", "en", "test"),
    _Page("anon/account", "/account", "render_account", None, "password"),
)


def _pages(lang):
    return [page for page in _PAGES if page.lang == lang]


def _render_page(page):
    mode = _test_auth_on() if page.auth == "test" else _password_mode()
    languages = "es,en" if page.lang == "en" else None
    with _Env(LANGUAGES=languages), mode:
        return _native(getattr(accounts_router, page.render), page.path, page.lang)


def render_snapshot_pages():
    """{page key: body bytes} for tests/snapshots.py (module scope must be set)."""
    return {page.key: _render_page(page)[1] for page in _PAGES}


def _check_page(case, page):
    # Imported here, not at module level: tests/e2e/auth-sso-accessible.spec.mjs
    # loads this file by path to reuse the render helpers, with only the repo
    # root on sys.path, and never reaches a snapshot check.
    import snapshots
    status, body, headers = _render_page(page)
    case.assertEqual(status, 200)
    case.assertEqual(headers.get("content-type"), "text/html; charset=utf-8")
    case.assertEqual(headers.get("cache-control"), "no-store")
    case.assertNotIn("set-cookie", headers)
    html = body.decode("utf-8")
    if page.lang:
        case.assertIn('<html lang="%s"' % page.lang, html)
    snapshots.assert_page(case, "accounts/" + page.key, body)



class TestNativeRoutesNotForwarding(unittest.TestCase):
    """The four HTML GETs (and, by construction, every other route this
    router registers) must be genuine native endpoints: this checks the
    endpoint's OWN module."""

    def test_html_gets_are_not_the_forwarding_adapter(self):
        found = set()
        for route in accounts_router.router.routes:
            path = getattr(route, "path", None)
            if path in _HTML_PATHS:
                found.add(path)
                self.assertNotEqual(
                    getattr(route.endpoint, "__module__", None), "core.module_router",
                    "%s still forwards to accounts.handle()" % path)
        self.assertEqual(found, set(_HTML_PATHS))

    def test_no_route_in_this_router_uses_module_router(self):
        for route in accounts_router.router.routes:
            self.assertNotEqual(
                getattr(route.endpoint, "__module__", None), "core.module_router",
                "%s still forwards through module_router" % getattr(route, "path", None))

    def test_demo_access_is_not_on_the_router(self):
        paths = {getattr(route, "path", None)
                 for route in accounts_router.router.routes}
        self.assertNotIn("/account/demo-access", paths)


class TestJinjaPagesSpanish(unittest.TestCase):
    def test_native_i18n_module_before_simulator(self):
        from app.core import static_fingerprint
        with _test_auth_on():
            status, body, _headers = _native(accounts_router.render_test_auth, "/auth/test", "es")
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        module_url = static_fingerprint.url("platform/i18n.mjs")
        consumer_url = static_fingerprint.url("platform/auth-test.mjs")
        self.assertIn(module_url, html)
        self.assertLess(html.index(module_url), html.index(consumer_url))

    def setUp(self):
        self.addCleanup(_reset_caches)   # after the environment is put back
        _env.set_env(self, LANGUAGES=None)
        _reset_caches()

    def test_pages_match_their_snapshots_in_spanish(self):
        for page in _pages("es"):
            with self.subTest(page=page.key):
                _check_page(self, page)



class TestJinjaPagesEnglish(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_reset_caches)   # after the environment is put back
        _env.set_env(self, LANGUAGES="es,en")
        _reset_caches()

    def test_pages_match_their_snapshots_in_english(self):
        for page in _pages("en"):
            with self.subTest(page=page.key):
                _check_page(self, page)


    def test_english_pages_are_actually_in_english(self):
        with _password_mode():
            _st, body, _h = _native(accounts_router.render_account, "/account", "en")
            html = body.decode("utf-8")
            self.assertIn('<html lang="en"', html)
            self.assertIn("Sign in", html)
            self.assertIn("My appointments", html)
            self.assertIn("My account and appointments", html)
            self.assertNotIn("Mi cuenta y mis turnos", html)
            _st, body, _h = _native(accounts_router.render_reset, "/account/reset", "en")
            html = body.decode("utf-8")
            self.assertIn('<html lang="en"', html)
            self.assertIn("Choose your new password", html)
            self.assertNotIn("Elegí tu contraseña nueva", html)
        with _test_auth_on():
            _env.set_env(self, LANGUAGES="es,en")
            _reset_caches()
            _st, body, _h = _native(accounts_router.render_test_auth, "/auth/test", "en")
            html = body.decode("utf-8")
            self.assertIn('<html lang="en"', html)
            self.assertIn("Local testing only.", html)
            self.assertNotIn("Solo pruebas locales.", html)
            _st, body, _h = _native(accounts_router.render_sso, "/auth/sso", "en")
            html = body.decode("utf-8")
            self.assertIn('<html lang="en"', html)
            self.assertIn("Hello!", html)
            self.assertNotIn("¡Hola!", html)


class TestAuthRedirectAndGateOff(unittest.TestCase):
    # The anonymous request (no `lang` cookie; Spanish is the only enabled
    # language by default) renders the same page as the Spanish /account case.
    def test_anonymous_account_renders_in_password_mode(self):
        anon = [page for page in _PAGES if page.key == "anon/account"][0]
        _check_page(self, anon)
        _status, body, headers = _render_page(anon)
        self.assertNotIn("location", headers)
        spanish = [page for page in _PAGES if page.key == "es/account"][0]
        self.assertEqual(body, _render_page(spanish)[1])


    def test_anonymous_account_redirects_on_renaper_entry(self):
        with _renaper_entry():
            st_n, body_n, hdr_n = _native(accounts_router.render_account, "/account")
            self.assertEqual(st_n, 302)
            self.assertEqual(body_n, b"")
            self.assertEqual(hdr_n.get("location"),
                             "/auth/renaper?next=%2Faccount")
            self.assertEqual(hdr_n.get("content-length"), "0")
            self.assertNotIn("content-type", hdr_n)
            self.assertNotIn("cache-control", hdr_n)
            self.assertNotIn("set-cookie", hdr_n)
            self.assertEqual(set(hdr_n), {"location", "content-length"})

    def test_test_auth_gate_off_json_404(self):
        expected = json.dumps({"error": "test_auth_no_disponible"}).encode("utf-8")
        self.assertEqual(len(expected), 36)
        with _password_mode():
            for path, render in (
                    ("/auth/test", accounts_router.render_test_auth),
                    ("/auth/sso", accounts_router.render_sso)):
                with self.subTest(path=path):
                    st_n, body_n, hdr_n = _native(render, path)
                    self.assertEqual(st_n, 404)
                    self.assertEqual(body_n, expected)
                    self.assertEqual(hdr_n.get("content-type"),
                                     "application/json; charset=utf-8")
                    self.assertEqual(hdr_n.get("cache-control"), "no-store")
                    self.assertEqual(hdr_n.get("content-length"), "36")
                    self.assertNotIn("set-cookie", hdr_n)
                    self.assertEqual(
                        set(hdr_n),
                        {"content-type", "cache-control", "content-length"})


class TestBootstrapCookieAndEscaping(unittest.TestCase):
    def test_bootstrap_set_cookie_presence(self):
        cookie = "st_ses=demo-token; Max-Age=1; Path=/; HttpOnly; SameSite=Lax"
        user = {"dni": "30123456", "nombre": "Sofía", "apellido": "Herrera"}
        with _password_mode(), mock.patch(
                "app.modules.turnos.booking_patient.patient_rows",
                return_value=[]):
            st_n, body_n, hdr_n = _native(
                accounts_router.render_account, "/account",
                bootstrap_user=user, set_cookie=cookie, canonical_path="/account")
        self.assertEqual(st_n, 200)
        self.assertIn("set-cookie", hdr_n)
        self.assertEqual(hdr_n["set-cookie"], cookie)
        self.assertIn("st_ses=", hdr_n["set-cookie"])
        self.assertIn("Path=/", hdr_n["set-cookie"])
        self.assertIn("HttpOnly", hdr_n["set-cookie"])
        self.assertIn("SameSite=Lax", hdr_n["set-cookie"])
        # The bootstrap payload itself is actually embedded, not just the
        # cookie header — an independent check standing in for the retired
        # legacy-body comparison (see module docstring).
        html = body_n.decode("utf-8")
        self.assertIn('"dni":"30123456"', html)
        self.assertIn("Sofía", html)
        self.assertIn('window.__ACCOUNT_CANONICAL_PATH__="/account"', html)

    def test_bootstrap_escapes_script_breakout(self):
        user = {
            "dni": "30123456",
            "nombre": "</script>< Ñandú",
            "apellido": "Herrera",
        }
        with _password_mode(), mock.patch(
                "app.modules.turnos.booking_patient.patient_rows",
                return_value=[{"id": 1}]):
            st_n, body_n, _hn = _native(
                accounts_router.render_account, "/account", bootstrap_user=user)
        self.assertEqual(st_n, 200)
        html = body_n.decode("utf-8")
        marker = "window.__ACCOUNT_BOOTSTRAP__="
        start = html.index(marker) + len(marker)
        end = html.index(";window.__ACCOUNT_CANONICAL_PATH__", start)
        payload = html[start:end]
        self.assertNotIn("<", payload)
        self.assertNotIn("</script>", payload)
        self.assertIn("\\u003c", payload)
        self.assertNotIn("\\\\u003c", payload)
        self.assertIn("Ñandú", payload)

    def test_bootstrap_db_unavailable_falls_back_to_null(self):
        user = {"dni": "30123456", "nombre": "Sofía", "apellido": "Herrera"}
        with _password_mode(), mock.patch(
                "app.modules.turnos.booking_patient.patient_rows",
                side_effect=accounts.store.DbUnavailable("unavailable")):
            st_n, body_n, _hn = _native(
                accounts_router.render_account, "/account", bootstrap_user=user)
        self.assertEqual(st_n, 200)
        self.assertIn(b"window.__ACCOUNT_BOOTSTRAP__=null;", body_n)


class TestLanguageSwitchRedirect(unittest.TestCase):
    def test_all_four_routes_switch_to_en(self):
        with _switcher_on():
            for path in _HTML_PATHS:
                with self.subTest(path=path):
                    request = _request("%s?lang=en" % path)
                    resp = accounts_router.lang_switch_response(request)
                    self.assertIsNotNone(resp)
                    hdr_n = _header_map(resp.headers.items())
                    self.assertEqual(resp.status_code, 302)
                    self.assertEqual(bytes(resp.body), b"")
                    self.assertEqual(hdr_n.get("location"), path)
                    self.assertEqual(
                        hdr_n.get("set-cookie"),
                        "lang=en; Max-Age=31536000; Path=/; SameSite=Lax")
                    self.assertEqual(hdr_n.get("content-length"), "0")
                    self.assertNotIn("content-type", hdr_n)
                    self.assertNotIn("cache-control", hdr_n)


class TestThemedPass(unittest.TestCase):
    def test_branding_style_and_theme_byte_identical(self):
        style = '<style id="marca">:root{--accent:#0a5c36!important;}</style>'
        with _password_mode(), \
                mock.patch("app.core.theme.current", return_value="humanrounds"), \
                mock.patch("app.core.theme._branding_style", return_value=style):
            self.assertEqual(theme.current(), "humanrounds")
            self.assertTrue(theme._branding_style())
            _env.set_env(self, LANGUAGES="es,en")
            _reset_caches()
            for lang in ("es", "en"):
                for path, render in (
                        ("/account", accounts_router.render_account),
                        ("/account/reset", accounts_router.render_reset)):
                    with self.subTest(path=path, lang=lang):
                        st_n, body_n, _hn = _native(render, path, lang)
                        self.assertEqual(st_n, 200)
                        self.assertIn(b'id="marca"', body_n)
                        self.assertIn(b'data-theme="humanrounds"', body_n)
            with _test_auth_on(), \
                    mock.patch("app.core.theme.current", return_value="humanrounds"), \
                    mock.patch("app.core.theme._branding_style", return_value=style):
                _env.set_env(self, LANGUAGES="es,en")
                _reset_caches()
                for lang in ("es", "en"):
                    with self.subTest(path="/auth/test", lang=lang):
                        st_n, body_n, _hn = _native(
                            accounts_router.render_test_auth, "/auth/test", lang)
                        self.assertEqual(st_n, 200)
                        self.assertIn(b'id="marca"', body_n)
                        self.assertIn(b'data-theme="humanrounds"', body_n)
                    with self.subTest(path="/auth/sso", lang=lang):
                        # /auth/sso is rendered with theme_pass=False (and
                        # site_pass=False) by design — accounts.py's own
                        # `render_sso_body` — so no branding/theme markup
                        # is injected here, unlike every other account page.
                        st_n, body_n, _hn = _native(
                            accounts_router.render_sso, "/auth/sso", lang)
                        self.assertEqual(st_n, 200)
                        self.assertNotIn(b'id="marca"', body_n)
                        self.assertNotIn(b'data-theme="humanrounds"', body_n)


class TestDemoBanner(unittest.TestCase):
    def test_banner_on_account_and_reset_absent_on_auth(self):
        with _Env(TURNOS_DEMO_MODE="1", TURNOS_AUTH_MODE="password"):
            for path, render in (
                    ("/account", accounts_router.render_account),
                    ("/account/reset", accounts_router.render_reset)):
                with self.subTest(path=path):
                    st_n, body_n, _hn = _native(render, path)
                    self.assertEqual(st_n, 200)
                    self.assertIn(b"demo-installation", body_n)
        with _test_auth_on(), _Env(TURNOS_DEMO_MODE="1"):
            for path, render in (
                    ("/auth/test", accounts_router.render_test_auth),
                    ("/auth/sso", accounts_router.render_sso)):
                with self.subTest(path=path):
                    st_n, body_n, _hn = _native(render, path)
                    self.assertEqual(st_n, 200)
                    self.assertNotIn(b"demo-installation", body_n)


class TestAccountTemplateConvergence(unittest.TestCase):
    """The native transport is the only one left and must use the
    canonical Jinja templates — no inline HTML copies."""

    def test_inline_html_constants_are_retired(self):
        for name in (
                "_ACCOUNT_HTML", "_RESET_HTML", "_TEST_AUTH_HTML", "_SSO_HTML"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(accounts, name))

    def test_native_account_render_uses_the_canonical_jinja_template(self):
        with _password_mode(), mock.patch(
                "app.core.jinja_env.render",
                wraps=__import__("app.core.jinja_env", fromlist=["render"]).render,
        ) as render:
            status, _body, _headers = _native(
                accounts_router.render_account, "/account")
        self.assertEqual(status, 200)
        render.assert_called_once()
        self.assertEqual(render.call_args.args[0], "account.html")


if __name__ == "__main__":
    unittest.main()
