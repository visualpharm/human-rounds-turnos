"""test_security_headers.py — browser security headers on every response and
HEAD answered by the route that answers GET, against the REAL `app` from
app.py.

The regressions this pins (audit of the reference deployment, 2026-10-11):

  1. No HTML page — `/admin/login` included — carried
     Strict-Transport-Security, X-Frame-Options / frame-ancestors,
     X-Content-Type-Options or Referrer-Policy, so the panel's login could be
     framed by any site and a first plain-http visit was never pinned to https.
  2. `HEAD /` answered 403 and `HEAD /admin/login` 404 while the same GET
     answered 200: `fastapi.routing.APIRoute` does not add HEAD to a GET
     route, so the request fell through to the native terminal, which treated
     the path as a static file. Uptime monitors and link checkers that probe
     with HEAD saw a site that was up as down.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_security_headers.py"
"""
import asyncio
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # do not depend on the checkout's real .env

from app import main as app_module  # noqa: E402
from app.core import head_as_get_middleware  # noqa: E402
from app.core import security_headers_middleware  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_HOST = "turnos.example.org"


def _client(scheme="https", host=_HOST):
    return TestClient(app_module.app, base_url="%s://%s" % (scheme, host),
                      follow_redirects=False)


class EveryResponseCarriesTheHeadersTest(unittest.TestCase):
    """The four headers, on a page, on the panel login and on JSON."""

    def _assert_baseline(self, resp):
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("Referrer-Policy"),
                         "strict-origin-when-cross-origin")

    def test_home_page(self):
        resp = _client().get("/")
        self._assert_baseline(resp)
        self.assertEqual(resp.headers.get("Content-Security-Policy"),
                         "frame-ancestors 'self'")
        self.assertEqual(resp.headers.get("Strict-Transport-Security"),
                         "max-age=31536000")

    def test_admin_login(self):
        resp = _client().get("/admin/login")
        self.assertEqual(resp.status_code, 200)
        self._assert_baseline(resp)
        self.assertEqual(resp.headers.get("Content-Security-Policy"),
                         "frame-ancestors 'self'")
        self.assertEqual(resp.headers.get("Strict-Transport-Security"),
                         "max-age=31536000")

    def test_json_and_not_found(self):
        for path in ("/health", "/api/no-existe", "/no-existe"):
            with self.subTest(path=path):
                self._assert_baseline(_client().get(path))

    def test_canonical_host_redirect(self):
        # The outermost boundary answers this one itself: the headers must be
        # added outside it, not only around the routers.
        resp = _client(host="salud.brieflysites.com").get("/")
        self.assertEqual(resp.status_code, 308)
        self._assert_baseline(resp)

    def test_each_header_is_sent_once(self):
        resp = _client().get("/")
        for name in ("x-content-type-options", "x-frame-options",
                     "referrer-policy", "strict-transport-security",
                     "content-security-policy"):
            with self.subTest(header=name):
                self.assertEqual(len(resp.headers.get_list(name)), 1)


class HstsOnlyOverHttpsTest(unittest.TestCase):
    """Pinning https on a plain-http install (localhost, a LAN box) would lock
    its own users out; the proxy's `X-Forwarded-Proto` is the same signal the
    session cookie's `Secure` flag already trusts."""

    def test_plain_http_sends_no_hsts(self):
        resp = _client(scheme="http").get("/")
        self.assertIsNone(resp.headers.get("Strict-Transport-Security"))
        self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_forwarded_https_sends_hsts(self):
        resp = _client(scheme="http").get(
            "/", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(resp.headers.get("Strict-Transport-Security"),
                         "max-age=31536000")

    def test_forwarded_http_wins_over_the_socket_scheme(self):
        resp = _client(scheme="http").get(
            "/", headers={"X-Forwarded-Proto": "http, https"})
        self.assertIsNone(resp.headers.get("Strict-Transport-Security"))


class ARouteKeepsItsOwnPolicyTest(unittest.TestCase):
    """A route that already sets a stricter policy (the custom logo and the
    referral viewer send their own CSP) is never overridden or duplicated."""

    def _run(self, own_headers):
        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": own_headers})
            await send({"type": "http.response.body", "body": b"x"})

        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {"type": "http", "method": "GET", "path": "/", "scheme": "https",
                 "headers": []}
        asyncio.run(security_headers_middleware.SecurityHeadersMiddleware(inner)(
            scope, receive, send))
        return sent[0]["headers"]

    def test_existing_values_survive(self):
        headers = self._run([
            (b"content-security-policy", b"default-src 'none'; sandbox"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
        ])
        values = {}
        for name, value in headers:
            values.setdefault(name, []).append(value)
        self.assertEqual(values[b"content-security-policy"],
                         [b"default-src 'none'; sandbox"])
        self.assertEqual(values[b"x-frame-options"], [b"DENY"])
        self.assertEqual(values[b"referrer-policy"], [b"no-referrer"])
        self.assertEqual(values[b"x-content-type-options"], [b"nosniff"])
        self.assertEqual(values[b"strict-transport-security"],
                         [b"max-age=31536000"])


class HeadIsAnsweredLikeGetTest(unittest.TestCase):
    """HEAD = the GET response without its body, for every GET-only route."""

    def test_home_page(self):
        get = _client().get("/")
        head = _client().head("/")
        self.assertEqual(get.status_code, 200)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head.headers.get("Content-Type"),
                         get.headers.get("Content-Type"))
        self.assertEqual(head.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_admin_login(self):
        head = _client().head("/admin/login")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")

    def test_content_length_describes_the_get_body(self):
        get = _client().get("/admin/login")
        head = _client().head("/admin/login")
        if get.headers.get("Content-Length") is not None:
            self.assertEqual(head.headers.get("Content-Length"),
                             get.headers.get("Content-Length"))

    def test_routes_that_declare_head_are_untouched(self):
        for path in ("/health", "/robots.txt"):
            with self.subTest(path=path):
                get = _client().get(path)
                head = _client().head(path)
                self.assertEqual(head.status_code, get.status_code)
                self.assertEqual(head.content, b"")

    def test_missing_page_stays_missing(self):
        self.assertEqual(_client().head("/no-existe").status_code, 404)

    def test_a_post_only_route_is_not_run_by_head(self):
        # No GET owner → nothing to rewrite: HEAD keeps reaching the terminal.
        get = _client().get("/api/admin/login")
        head = _client().head("/api/admin/login")
        self.assertEqual(head.status_code, get.status_code)
        self.assertNotEqual(head.status_code, 200)


class HeadRewriteDecisionTest(unittest.TestCase):
    """The decision itself, on the real route table."""

    def _scope(self, method, path):
        return {"type": "http", "method": method, "path": path,
                "root_path": "", "headers": [], "query_string": b"",
                "app": app_module.app, "path_params": {}}

    def test_get_only_route_is_rewritten(self):
        self.assertTrue(head_as_get_middleware.get_owner_declines_head(
            self._scope("HEAD", "/admin/login")))

    def test_explicit_head_route_is_not(self):
        self.assertFalse(head_as_get_middleware.get_owner_declines_head(
            self._scope("HEAD", "/health")))

    def test_terminal_keeps_its_own_head(self):
        self.assertFalse(head_as_get_middleware.get_owner_declines_head(
            self._scope("HEAD", "/no-existe")))


class ImageDeclaresItsHealthcheckTest(unittest.TestCase):
    """Without a HEALTHCHECK the orchestrator reports the container's health
    as unknown and swaps containers blind. The image has no curl or wget, so
    the probe must run on what the image does have."""

    def test_dockerfile_healthcheck_probes_health_with_python(self):
        with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as f:
            text = f.read()
        lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        code = "\n".join(lines)
        self.assertEqual(code.count("HEALTHCHECK"), 1)
        check = code[code.index("HEALTHCHECK"):].split("\nCMD", 1)[0]
        self.assertIn('CMD ["python3", "/app/scripts/healthcheck.py"]', check)
        self.assertNotIn("curl", check)
        self.assertNotIn("wget", check)
        with open(os.path.join(ROOT, "scripts", "healthcheck.py"), encoding="utf-8") as f:
            probe = f.read()
        self.assertIn("/health", probe)
        self.assertIn('os.environ.get("PORT"', probe)

    def test_probe_follows_the_bind_family(self):
        # Railway binds `::` and refuses 127.0.0.1 there (2026-10-19);
        # Coolify binds 0.0.0.0 and has no IPv6 loopback listener.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "healthcheck", os.path.join(ROOT, "scripts", "healthcheck.py"))
        hc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hc)
        self.assertEqual(hc.candidates("0.0.0.0"), ["127.0.0.1", "[::1]"])
        self.assertEqual(hc.candidates("::"), ["[::1]", "127.0.0.1"])
        self.assertEqual(hc.candidates(None), ["127.0.0.1", "[::1]"])
        self.assertEqual(hc.candidates("10.0.0.5"), ["10.0.0.5"])
        self.assertEqual(hc.candidates("fd12::5"), ["[fd12::5]"])

    def test_probe_answers_on_an_ipv6_only_listener(self):
        import http.server
        import socket
        import subprocess
        import threading

        class V6(http.server.ThreadingHTTPServer):
            address_family = socket.AF_INET6

            def server_bind(self):
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                super().server_bind()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/health" else 404)
                self.end_headers()

            def log_message(self, *a):
                pass

        try:
            srv = V6(("::1", 0), H)
        except OSError:
            self.skipTest("no IPv6 loopback here")
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            env = dict(os.environ, HOST="::", PORT=str(srv.server_address[1]))
            rc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "healthcheck.py")],
                                env=env, timeout=30).returncode
            self.assertEqual(rc, 0)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
