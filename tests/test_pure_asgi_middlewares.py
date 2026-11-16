"""The four request middlewares are pure ASGI, not `BaseHTTPMiddleware`.

`BaseHTTPMiddleware` runs the app in a second task behind an in-memory stream:
the known costs are streamed bodies and client disconnects crossing that
stream, background work tied to it, and ContextVars living in two tasks. The
host redirect, the site-auth gate, the panel's last-screen memory and the
language binder never needed any of it, so they speak ASGI directly (like the
security-headers and HEAD middlewares already did).

Two levels:

  1. TestMiddlewareShape — the structure: none of the middlewares the real app
     mounts derives from BaseHTTPMiddleware.
  2. The behaviour, driven through raw ASGI messages (no TestClient, so the
     exact `http.response.start` / `http.response.body` sequence is visible):
     lifespan and websocket scopes pass through untouched; a deny answers
     without touching the app; the language stays bound until the LAST byte of
     a streamed body; the last-screen record is written before the response
     start reaches the client and only for a 200 HTML page of the user's own
     menu.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_pure_asgi_middlewares.py"
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # do not depend on the checkout's real .env

from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from app.admin_panel import admin_navigation  # noqa: E402
from app.admin_panel import admin_navigation_middleware  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.auth import site_auth  # noqa: E402
from app.auth import site_auth_middleware  # noqa: E402
from app.core import host_redirect  # noqa: E402
from app.core import host_redirect_middleware  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import i18n_middleware  # noqa: E402

FOUR = (
    ("host redirect", host_redirect_middleware.HostRedirectMiddleware),
    ("site auth", site_auth_middleware.SiteAuthMiddleware),
    ("admin last screen", admin_navigation_middleware.AdminLastScreenMiddleware),
    ("i18n", i18n_middleware.I18nMiddleware),
)


def _scope(path="/", method="GET", headers=None, query=b"", raw_path=True):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "path": path,
        "query_string": query,
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1"))
                    for k, v in (headers or {}).items()],
    }
    if raw_path:
        scope["raw_path"] = path.encode("latin-1")
    return scope


def _run(app, scope, body=b""):
    """Drive `app` with one request; return every message it sent, in order."""
    sent = []
    requests = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if requests:
            return requests.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _start(sent):
    return next(m for m in sent if m["type"] == "http.response.start")


def _body(sent):
    return b"".join(m.get("body", b"") for m in sent
                    if m["type"] == "http.response.body")


def _html_app(status=200, content_type=b"text/html; charset=utf-8", chunks=(b"<p>ok</p>",),
              log=None):
    async def app(scope, receive, send):
        if log is not None:
            log.append("app:start")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type)]})
        for index, chunk in enumerate(chunks):
            await send({"type": "http.response.body", "body": chunk,
                        "more_body": index < len(chunks) - 1})
    return app


class TestMiddlewareShape(unittest.TestCase):
    def test_none_of_the_four_is_a_basehttpmiddleware(self):
        for label, cls in FOUR:
            with self.subTest(middleware=label):
                self.assertFalse(issubclass(cls, BaseHTTPMiddleware))
                self.assertFalse(hasattr(cls, "dispatch"))

    def test_the_real_app_mounts_no_basehttpmiddleware_at_all(self):
        from app import main as composition
        mounted = [m.cls for m in composition.app.user_middleware]
        self.assertGreaterEqual(len(mounted), 7)
        for cls in mounted:
            with self.subTest(middleware=cls.__name__):
                self.assertFalse(issubclass(cls, BaseHTTPMiddleware))

    def test_the_mount_order_is_unchanged(self):
        from app import main as composition
        # AdminCsrfMiddleware (2026-10-27) sits inside the legacy rewrite on
        # purpose: the admin Origin check must see the canonical path, so a
        # historical alias of an admin endpoint cannot sidestep it.
        self.assertEqual(
            [m.cls.__name__ for m in composition.app.user_middleware],
            ["SecurityHeadersMiddleware", "HostRedirectMiddleware",
             "SiteAuthMiddleware", "AdminLastScreenMiddleware",
             "I18nMiddleware", "LegacyUrlRewriteMiddleware",
             "AdminCsrfMiddleware", "HeadAsGetMiddleware"])


class TestNonHttpScopesPassThrough(unittest.TestCase):
    def test_lifespan_and_websocket_reach_the_app_untouched(self):
        for label, cls in FOUR:
            for kind in ("lifespan", "websocket"):
                with self.subTest(middleware=label, scope=kind):
                    seen = []

                    async def inner(scope, receive, send):
                        seen.append((scope["type"], receive, send))

                    middleware = cls(inner)
                    scope = {"type": kind}

                    async def receive():
                        return {}

                    async def send(message):
                        return None

                    asyncio.run(middleware(scope, receive, send))
                    self.assertEqual(seen, [(kind, receive, send)])


class TestHostRedirect(unittest.TestCase):
    def test_a_redirected_host_gets_the_308_and_the_app_never_runs(self):
        with mock.patch.object(host_redirect, "target",
                               return_value="https://canonical.example/x?y=1") as target:
            app = mock.Mock(side_effect=AssertionError("app must not run"))
            sent = _run(host_redirect_middleware.HostRedirectMiddleware(app),
                        _scope("/x", headers={"host": "old.example"}, query=b"y=1"))
        target.assert_called_once_with("old.example", "/x?y=1")
        start = _start(sent)
        self.assertEqual(start["status"], 308)
        headers = {bytes(k).lower(): bytes(v) for k, v in start["headers"]}
        self.assertEqual(headers[b"location"], b"https://canonical.example/x?y=1")
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertEqual(headers[b"content-length"], b"0")
        self.assertEqual(_body(sent), b"")

    def test_an_unredirected_host_goes_straight_to_the_app(self):
        with mock.patch.object(host_redirect, "target", return_value=None):
            sent = _run(host_redirect_middleware.HostRedirectMiddleware(
                _html_app(chunks=(b"a", b"b"))), _scope("/x"))
        self.assertEqual(_start(sent)["status"], 200)
        self.assertEqual(_body(sent), b"ab")


class TestSiteAuth(unittest.TestCase):
    def test_a_denied_request_gets_the_fixed_401_and_the_app_never_runs(self):
        with mock.patch.object(site_auth, "authorize", return_value=False):
            app = mock.Mock(side_effect=AssertionError("app must not run"))
            sent = _run(site_auth_middleware.SiteAuthMiddleware(app), _scope("/admin"))
        start = _start(sent)
        self.assertEqual(start["status"], 401)
        headers = [(bytes(k).lower(), bytes(v)) for k, v in start["headers"]]
        self.assertIn((b"cache-control", b"no-store"), headers)
        self.assertEqual(_body(sent), site_auth.challenge_body())

    def test_an_allowed_request_streams_the_app_body_and_keeps_the_request_body(self):
        read = []

        async def echo(scope, receive, send):
            message = await receive()
            read.append(message["body"])
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"one", "more_body": True})
            await send({"type": "http.response.body", "body": b"two", "more_body": False})

        with mock.patch.object(site_auth, "authorize", return_value=True) as authorize:
            sent = _run(site_auth_middleware.SiteAuthMiddleware(echo),
                        _scope("/api/x", method="POST", headers={"authorization": "Basic eDp5"}),
                        body=b"payload")
        authorize.assert_called_once_with("/api/x", "Basic eDp5")
        self.assertEqual(read, [b"payload"])
        self.assertEqual([m.get("body") for m in sent
                          if m["type"] == "http.response.body"], [b"one", b"two"])

    def test_an_app_error_propagates_instead_of_being_swallowed(self):
        async def boom(scope, receive, send):
            raise RuntimeError("route failed")

        with mock.patch.object(site_auth, "authorize", return_value=True):
            with self.assertRaises(RuntimeError):
                _run(site_auth_middleware.SiteAuthMiddleware(boom), _scope("/"))


class TestI18n(unittest.TestCase):
    def setUp(self):
        # An installation that offers English: Accept-Language decides.
        patcher = mock.patch.object(i18n, "enabled_languages",
                                    return_value=["es", "en"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_language_stays_bound_until_the_last_streamed_byte(self):
        seen = []

        async def streaming(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            seen.append(i18n.active())
            await send({"type": "http.response.body", "body": b"a", "more_body": True})
            await asyncio.sleep(0)
            seen.append(i18n.active())
            await send({"type": "http.response.body", "body": b"b", "more_body": False})

        before = i18n.active()
        _run(i18n_middleware.I18nMiddleware(streaming),
             _scope("/", headers={"accept-language": "en"}))
        self.assertEqual(seen, ["en", "en"])
        self.assertEqual(i18n.active(), before)

    def test_the_language_is_unbound_when_the_app_raises(self):
        async def boom(scope, receive, send):
            self.assertEqual(i18n.active(), "en")
            raise RuntimeError("route failed")

        before = i18n.active()
        with self.assertRaises(RuntimeError):
            _run(i18n_middleware.I18nMiddleware(boom),
                 _scope("/", headers={"accept-language": "en"}))
        self.assertEqual(i18n.active(), before)


class TestAdminLastScreen(unittest.TestCase):
    IDENTITY = {"usuario": "ana", "rol": "admin"}

    def _drive(self, app, path="/admin/cola", method="GET", identity=IDENTITY,
               nav=("/admin/cola",), save=None):
        log = []
        save = save or mock.Mock(side_effect=lambda *a: log.append("save"))
        scope = _scope(path, method=method)
        sent = []

        async def send(message):
            log.append(message["type"])
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        with mock.patch.object(admin_auth, "request_credential", return_value="tok"), \
                mock.patch.object(admin_auth, "_verify_token", return_value=identity) as verify, \
                mock.patch.object(admin_navigation, "nav_paths", return_value=list(nav)), \
                mock.patch.object(admin_navigation.admin_preferences, "save_item", save):
            asyncio.run(admin_navigation_middleware.AdminLastScreenMiddleware(app)(
                scope, receive, send))
        return log, sent, save, verify

    def test_a_200_html_page_of_the_menu_is_recorded_before_the_start_is_sent(self):
        log, sent, save, _ = self._drive(_html_app())
        save.assert_called_once_with("ana", admin_navigation.NAV_LAST_SCREEN, "/admin/cola")
        self.assertEqual(log[:2], ["save", "http.response.start"])
        self.assertEqual(_body(sent), b"<p>ok</p>")

    def test_only_a_200_html_get_on_the_users_own_menu_is_recorded(self):
        cases = {
            "json": dict(app=_html_app(content_type=b"application/json")),
            "not found": dict(app=_html_app(status=404)),
            "redirect": dict(app=_html_app(status=302)),
            "post": dict(app=_html_app(), method="POST"),
            "head": dict(app=_html_app(), method="HEAD"),
            "outside the menu": dict(app=_html_app(), nav=("/admin/otra",)),
            "not under /admin/": dict(app=_html_app(), path="/cuenta"),
            "no identity": dict(app=_html_app(), identity=None),
        }
        for label, kwargs in cases.items():
            with self.subTest(case=label):
                _log, sent, save, _ = self._drive(**kwargs)
                save.assert_not_called()
                self.assertIn("http.response.start", [m["type"] for m in sent])

    def test_identity_is_not_even_looked_up_outside_admin_get(self):
        _log, _sent, _save, verify = self._drive(_html_app(), path="/cuenta")
        verify.assert_not_called()

    def test_a_preferences_outage_never_breaks_the_page(self):
        broken = mock.Mock(side_effect=RuntimeError("db down"))
        _log, sent, save, _ = self._drive(_html_app(), save=broken)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(_start(sent)["status"], 200)
        self.assertEqual(_body(sent), b"<p>ok</p>")

    def test_an_app_error_propagates_and_records_nothing(self):
        async def boom(scope, receive, send):
            raise RuntimeError("route failed")

        save = mock.Mock()
        with self.assertRaises(RuntimeError):
            self._drive(boom, save=save)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
