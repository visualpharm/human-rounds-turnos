"""test_site_auth_middleware.py — the ASGI middleware for the pre-launch Basic
Auth (auth/site_auth_middleware.py) as the "one common boundary" for EVERY
native FastAPI route.

Three coverage levels, read outside-in:

  1. TestRealAppNativeRoute / TestMiddlewareWiredAsCommonBoundary — against
     the REAL `app` from app.py (the Tier 4 composition), via
     fastapi.testclient. Proves the middleware is mounted AS the outermost
     boundary and that a native route (/api/admin/whoami) is gated like a
     legacy one: no credentials -> gate 401; valid credentials -> the gate
     opens and the route runs (here it returns admin_auth's JSON 401, clearly
     distinct from the gate's text/plain 401).
  2. TestSiteAuthMiddlewareMinimalApp — a small FastAPI with ONLY the
     middleware and probe routes, via TestClient. Full adversarial coverage of
     the gate at its own level: gate inactive opens everything; gate armed
     gates / with no credentials (exact 401), opens it with valid credentials,
     rejects bad credentials / corrupt header / non-Basic scheme, and the FIVE
     anonymous exemptions are reachable with no credentials.
  3. TestUnauthorizedResponseBytes — calls _unauthorized_response() directly
     and asserts the EXACT wire bytes (status, body, raw_headers with
     title-case names and order WWW-Authenticate/Content-Type/Content-Length).
     Without going through httpx's case-insensitive normalization — this is the
     fidelity test against the legacy 401.

FAKE credentials (demo/s3cr3t) — no real credential is ever accessed; the gate
reads env at runtime and these tests set their own values.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_site_auth_middleware.py"
"""
import base64
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
from app.core import static_fingerprint  # noqa: E402
config.ENV_PATH = ""  # do not depend on the checkout's real .env

from app.auth import site_auth  # noqa: E402
from app.auth import site_auth_middleware  # noqa: E402
from fastapi.responses import PlainTextResponse  # noqa: E402

_USER = "demo"
_PASS = "s3cr3t"
_BODY_TEXT = "Autenticación requerida"


def _basic(user, pwd):
    raw = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
    return "Basic " + raw


class _GateEnvMixin(unittest.TestCase):
    """Set/unset the gate's env pair with cleanup, without touching real values."""

    def _armar(self, user=_USER, pwd=_PASS):
        _env.set_env(self, {site_auth._USER_ENV: user, site_auth._PASS_ENV: pwd})

    def _desarmar(self):
        _env.set_env(self, {site_auth._USER_ENV: None, site_auth._PASS_ENV: None})


# ---- level 3: exact wire bytes (no httpx normalization) --------------------

class TestUnauthorizedResponseBytes(_GateEnvMixin):
    """_unauthorized_response() is what the middleware returns on reject.
    Asserting its attrs directly = fidelity to the legacy 401, without httpx
    normalizing header names to lowercase."""

    def test_status_y_body(self):
        self._armar()
        r = site_auth_middleware._unauthorized_response()
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.body, _BODY_TEXT.encode("utf-8"))

    def test_raw_headers_nombres_title_case_y_orden(self):
        # legacy (BaseHTTPRequestHandler.send_header) emits names as passed
        # (title-case); the middleware sets raw_headers directly to emit the
        # SAME. Order: WWW-Authenticate, Content-Type, Content-Length,
        # Cache-Control — the last one added ASGI-side to mirror the no-store
        # server.Handler.end_headers() injects on every >=400
        # (see _unauthorized_response).
        self._armar()
        r = site_auth_middleware._unauthorized_response()
        names = [k.decode("latin-1") for k, _ in r.raw_headers]
        self.assertEqual(
            names,
            ["WWW-Authenticate", "Content-Type", "Content-Length", "Cache-Control"],
        )

    def test_cache_control_no_store_presente(self):
        # cache-policy parity with the legacy 401 (end_headers sends no-store
        # on every >=400); without this a middleware 401 would be cached and
        # poison the asset, same as the legacy 401 before the 2026-07-28 fix.
        self._armar()
        r = site_auth_middleware._unauthorized_response()
        d = {k.decode("latin-1"): v.decode("latin-1") for k, v in r.raw_headers}
        self.assertEqual(d["Cache-Control"], "no-store")

    def test_realm_y_content_type_exactos(self):
        self._armar()
        r = site_auth_middleware._unauthorized_response()
        d = {k.decode("latin-1"): v.decode("latin-1") for k, v in r.raw_headers}
        self.assertEqual(d["Content-Type"], "text/plain; charset=utf-8")
        self.assertTrue(d["WWW-Authenticate"].startswith('Basic realm="'))
        self.assertTrue(d["WWW-Authenticate"].endswith('"'))

    def test_content_length_coincide_con_el_body(self):
        self._armar()
        r = site_auth_middleware._unauthorized_response()
        d = {k.decode("latin-1"): v.decode("latin-1") for k, v in r.raw_headers}
        self.assertEqual(int(d["Content-Length"]), len(r.body))

    def test_no_agrega_headers_de_servidor(self):
        # uvicorn runs with server_header/date_header off in proxy mode, so
        # neither a native nor a forwarded 401 carries Server/Date. The
        # middleware does not add them either.
        self._armar()
        r = site_auth_middleware._unauthorized_response()
        names = {k.decode("latin-1").lower() for k, _ in r.raw_headers}
        self.assertNotIn("server", names)
        self.assertNotIn("date", names)


# ---- _raw_request_path: raw bytes + fail-closed on a missing raw_path -------
# TestClient (httpx/starlette) ALWAYS populates scope['raw_path'], so the three
# guarantees the docstring makes can only be exercised by hand-building the
# scope and calling dispatch directly: (1) a scope WITHOUT raw_path returns
# None — never falling back to the decoded scope['path']; (2) an ARMED gate on
# that None FAILS CLOSED — the 401 challenge, downstream never reached; (3) a
# valid Basic credential on that None still passes (fail-closed, not fail-dead).
# Plus the encoded raw-vs-decoded contrast that is the reason the middleware
# authorizes on raw bytes at all.

class TestRawRequestPath(_GateEnvMixin):
    """_raw_request_path is the RAW-path / fail-closed contract under dispatch.
    None of these go through TestClient: TestClient always sets raw_path, so the
    missing-raw_path regressions need a hand-built scope + a direct dispatch."""

    def _scope(self, path="/", headers=None):
        """A complete ASGI http scope that OMITS raw_path — the case TestClient
        cannot produce. `path` (the decoded path) is settable so a no-fallback
        regression can prove the gate never consults it."""
        return {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "root_path": "",
            "path": path,
            "query_string": b"",
            "headers": headers or [],
        }

    def _dispatch(self, scope, call_next):
        """Run the REAL middleware (as an ASGI app) on a hand-built scope.
        `call_next(request)` is the downstream route: it is wrapped as the
        inner ASGI app. Returns an object with `status_code` and `body`, read
        from what the middleware actually sent."""
        import asyncio
        from types import SimpleNamespace
        from starlette.requests import Request

        async def inner(scope, receive, send):
            response = await call_next(Request(scope, receive))
            await response(scope, receive, send)

        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        mw = site_auth_middleware.SiteAuthMiddleware(inner)
        asyncio.run(mw(scope, receive, send))
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent
                        if m["type"] == "http.response.body")
        return SimpleNamespace(status_code=start["status"], body=body)

    # -- (1) a scope without raw_path returns None --
    def test_scope_sin_raw_path_devuelve_none_sin_caer_al_decodificado(self):
        # the ASGI spec makes raw_path optional (uvicorn never omits it); the
        # function returns None, NEVER falling back to the decoded scope['path']
        # an attacker would need to forge an exemption.
        self.assertIsNone(site_auth_middleware._raw_request_path({}))
        scope = {"type": "http", "path": "/mcp", "headers": []}  # decoded exempt path present
        self.assertIsNone(site_auth_middleware._raw_request_path(scope))

    # -- (2) armed gate + missing raw_path + unauthenticated -> 401, no downstream --
    def test_armado_raw_path_ausente_sin_credenciales_falla_cerrado(self):
        # the decoded scope['path'] is an EXEMPT route ("/mcp"); a decoded
        # fallback would match it and reach downstream. The raw-path contract
        # returns None, authorize(None,...) matches no exemption, and the
        # unauthenticated request gets the 401 challenge — downstream never run.
        self._armar()
        scope = self._scope(path="/mcp", headers=[])

        async def _must_not_reach(request):
            raise AssertionError(
                "downstream reached — the gate should block a missing-raw_path "
                "request before any router")

        r = self._dispatch(scope, _must_not_reach)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.body, _BODY_TEXT.encode("utf-8"))

    # -- (3) valid Basic credential + missing raw_path -> reaches downstream --
    def test_armado_raw_path_ausente_con_credenciales_validas_pasa(self):
        # None matches no exemption, but a VALID credential still passes —
        # fail CLOSED, not fail-dead. Downstream is reached.
        self._armar()
        header = _basic(_USER, _PASS).encode("latin-1")
        scope = self._scope(path="/", headers=[(b"authorization", header)])
        reached = {"n": 0}

        async def _ok(request):
            reached["n"] += 1
            return PlainTextResponse("downstream", status_code=200)

        r = self._dispatch(scope, _ok)
        self.assertEqual(reached["n"], 1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.body, b"downstream")

    # -- a non-bytes raw_path is refused outright, never stringified --
    def test_raw_path_no_bytes_devuelve_none_no_se_stringifica(self):
        # raw_path is spec'd as bytes. A missing value OR any non-bytes value
        # (a malformed string, an int) returns None — NEVER stringified. The
        # danger of stringifying: str("/mcp/") would clean to the /mcp
        # exemption and reopen the bypass the raw-path contract closes.
        self.assertIsNone(site_auth_middleware._raw_request_path({}))
        self.assertIsNone(site_auth_middleware._raw_request_path({"raw_path": None}))
        self.assertIsNone(site_auth_middleware._raw_request_path({"raw_path": "/mcp/"}))
        self.assertIsNone(site_auth_middleware._raw_request_path({"raw_path": 123}))

    # -- armed gate + malformed string raw_path "/mcp/" + unauth -> 401 --
    def test_raw_path_string_mcp_sin_credenciales_falla_cerrado_401(self):
        # A malformed string raw_path equal to "/mcp/" — which _clean_path
        # WOULD collapse to the /mcp exemption if authorize() ever saw it —
        # must fail closed: non-bytes -> None -> authorize(None, "") matches no
        # exemption -> the exact 401 challenge, downstream never reached. The
        # decoded scope['path'] is also "/mcp", to prove the gate consults
        # neither the stringified raw_path nor the decoded fallback.
        self._armar()
        scope = self._scope(path="/mcp", headers=[])
        scope["raw_path"] = "/mcp/"

        async def _must_not_reach(request):
            raise AssertionError(
                "downstream reached — a malformed string raw_path must fail "
                "closed before any router")

        r = self._dispatch(scope, _must_not_reach)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.body, _BODY_TEXT.encode("utf-8"))

    # -- valid Basic credential + malformed string raw_path -> still passes --
    def test_raw_path_string_mcp_con_credenciales_validas_pasa(self):
        # Fail CLOSED, not fail-dead: the malformed string raw_path -> None ->
        # no exemption, but a VALID Basic credential still opens the gate and
        # reaches downstream.
        self._armar()
        header = _basic(_USER, _PASS).encode("latin-1")
        scope = self._scope(path="/mcp", headers=[(b"authorization", header)])
        scope["raw_path"] = "/mcp/"
        reached = {"n": 0}

        async def _ok(request):
            reached["n"] += 1
            return PlainTextResponse("downstream", status_code=200)

        r = self._dispatch(scope, _ok)
        self.assertEqual(reached["n"], 1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.body, b"downstream")

    # -- the encoded raw-vs-decoded contrast (why raw bytes) --
    def test_bytes_crudos_no_se_exentan_vs_decodificado_si(self):
        # The same conceptual path, two representations. The RAW encoded bytes
        # b"/mcp%2F" decode latin-1 to the LITERAL "/mcp%2F" (%2F is not a path
        # separator), which is NOT the /mcp exemption -> denied with no creds.
        # The DECODED "/mcp/" cleans to "/mcp" and IS the exemption -> allowed
        # with no creds. That opposite decision on the same request IS the
        # bypass authorizing on raw bytes closes.
        self._armar()
        raw = site_auth_middleware._raw_request_path({"raw_path": b"/mcp%2F"})
        self.assertEqual(raw, "/mcp%2F")                                    # literal %2F kept
        self.assertNotIn(site_auth._clean_path(raw), site_auth._EXENTAS)    # raw -> not exempt
        self.assertEqual(site_auth._clean_path("/mcp/"), "/mcp")            # decoded -> /mcp
        self.assertIn(site_auth._clean_path("/mcp/"), site_auth._EXENTAS)   # decoded -> exempt
        # ONE contrast: same no-credential request, raw vs decoded, opposite
        # decisions.
        self.assertNotEqual(
            bool(site_auth.authorize(raw, "")),        # raw bytes -> denied
            bool(site_auth.authorize("/mcp/", "")),    # decoded   -> allowed
        )


# ---- level 2: middleware against a small FastAPI (TestClient) --------------

def _build_minimal_app():
    """A FastAPI with ONLY SiteAuthMiddleware and probe routes: one protected
    route (/) and one at each of the five exempt paths. Isolates the gate
    logic from any dependency on the real routers."""
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI()
    app.add_middleware(site_auth_middleware.SiteAuthMiddleware)

    @app.get("/", response_class=PlainTextResponse)
    async def _root():
        return PlainTextResponse("ok", status_code=200)

    for path in site_auth._EXENTAS:
        # Each exempt route needs its own path closure; without this all of
        # them would capture the loop's last value.
        def _make(p):
            async def _h():
                return PlainTextResponse("exenta:" + p, status_code=200)
            return _h
        app.add_api_route(path, _make(path), methods=["GET"])

    return app


class TestSiteAuthMiddlewareMinimalApp(_GateEnvMixin):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls.app = _build_minimal_app()
        cls.client = TestClient(cls.app)

    # -- gate inactive: degrades open (local dev) --
    def test_gate_inactivo_deja_pasar_sin_credenciales(self):
        self._desarmar()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "ok")

    # -- gate armed: the protected route --
    def test_gate_armado_sin_credenciales_es_401(self):
        self._armar()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 401)

    def test_gate_armado_401_body_es_el_texto_fijo(self):
        self._armar()
        r = self.client.get("/")
        self.assertEqual(r.text, _BODY_TEXT)

    def test_gate_armado_401_tiene_realm_basic(self):
        self._armar()
        r = self.client.get("/")
        auth = r.headers.get("www-authenticate", "")
        self.assertTrue(auth.startswith('Basic realm="'), auth)

    def test_gate_armado_401_content_type_text_plain(self):
        self._armar()
        r = self.client.get("/")
        self.assertEqual(r.headers.get("content-type"), "text/plain; charset=utf-8")

    def test_gate_armado_401_cache_control_no_store(self):
        # cache-policy parity with the legacy 401 (end_headers no-store)
        self._armar()
        r = self.client.get("/")
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_gate_armado_credenciales_validas_abre(self):
        self._armar()
        r = self.client.get("/", headers={"Authorization": _basic(_USER, _PASS)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "ok")

    def test_gate_armado_password_mala_es_401(self):
        self._armar()
        r = self.client.get("/", headers={"Authorization": _basic(_USER, "mala")})
        self.assertEqual(r.status_code, 401)

    def test_gate_armado_usuario_malo_es_401(self):
        self._armar()
        r = self.client.get("/", headers={"Authorization": _basic("otro", _PASS)})
        self.assertEqual(r.status_code, 401)

    def test_gate_armado_esquema_bearer_es_401(self):
        self._armar()
        r = self.client.get("/", headers={"Authorization": "Bearer un-token"})
        self.assertEqual(r.status_code, 401)

    def test_gate_armado_base64_corrupto_es_401(self):
        self._armar()
        r = self.client.get("/", headers={"Authorization": "Basic no-es-base64!!!"})
        self.assertEqual(r.status_code, 401)

    def test_gate_armado_head_tambien_tapado(self):
        # the middleware runs for every method, HEAD included
        self._armar()
        r = self.client.head("/")
        self.assertEqual(r.status_code, 401)

    # -- the five anonymous exemptions are reachable with no credentials --
    def test_gate_armado_exentas_alcanzables_sin_credenciales(self):
        self._armar()
        for path in site_auth._EXENTAS:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, f"{path} debe ser anónima")
                self.assertTrue(r.text.startswith("exenta:"))

    def test_gate_armado_exenta_con_query_string_alcanzable(self):
        self._armar()
        r = self.client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=x")
        self.assertEqual(r.status_code, 200)

    def test_gate_armado_prefijo_de_exenta_NO_es_exenta(self):
        # a prefix of an exempt route does not inherit the exemption
        self._armar()
        r = self.client.get("/api/whatsapp/webhookzzz")
        # no route registered here -> 404, BUT the gate runs first: not exempt
        # and no credentials -> 401 (the gate wins over the 404)
        self.assertEqual(r.status_code, 401)

    def test_solo_esas_cinco_son_exentas(self):
        # pinning: if someone adds/removes an exemption, this test shouts
        self.assertEqual(set(site_auth._EXENTAS),
                         {"/api/whatsapp/webhook", "/mcp", "/api/chat-transcripts",
                          "/health", "/ready", "/api/analytics/events", "/api/analytics/schedule",
                          # the assistant's OAuth sign-in (2026-11-13)
                          "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
                          "/.well-known/oauth-authorization-server",
                          "/.well-known/oauth-authorization-server/mcp",
                          "/.well-known/openid-configuration", "/oauth/register", "/oauth/authorize",
                          "/oauth/token"})


# ---- level 1: against the REAL `app` (Tier 4 composition) ------------------

class TestMiddlewareWiredAsCommonBoundary(unittest.TestCase):
    """The middleware is mounted on the real app and is the outermost boundary
    (runs before i18n) — the structural assertion of 'one common boundary'."""

    @classmethod
    def setUpClass(cls):
        from app import main as app_module
        cls.app = app_module.app

    def test_site_auth_middleware_esta_montado(self):
        names = [str(m) for m in self.app.user_middleware]
        self.assertIn("Middleware(SiteAuthMiddleware)", names)

    def test_es_el_user_middleware_mas_externo(self):
        # add_middleware(insert(0)) -> the last added lands at index 0 =
        # outermost. Only the canonical-host redirect sits OUTSIDE the gate
        # (the host redirect must precede the site-auth challenge —
        # a vanity host redirects instead of being challenged); the gate
        # stays the outermost boundary for every request this app SERVES,
        # still ahead of i18n.
        # The security-headers wrapper is outside both on purpose: it never
        # answers a request, it only adds headers to whatever the gate or the
        # redirect answers (test_security_headers.py).
        names = [str(m) for m in self.app.user_middleware]
        self.assertEqual(names[0], "Middleware(SecurityHeadersMiddleware)")
        self.assertEqual(names[1], "Middleware(HostRedirectMiddleware)")
        self.assertEqual(names[2], "Middleware(SiteAuthMiddleware)")
        self.assertIn("Middleware(I18nMiddleware)", names[3:])


class TestRealAppNativeRoute(_GateEnvMixin):
    """End-to-end against the REAL `app`: a NATIVE route (/api/admin/whoami,
    mounted before the catch-all) sits behind the gate like a legacy one.
    whoami with no session responds with its OWN JSON 401 (admin_auth) —
    clearly distinguishable from the gate's text/plain 401, which proves the
    gate opened and the request reached the router."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import main as app_module
        cls.client = TestClient(app_module.app)

    def _gate_401(self, r):
        """True if the response IS site_auth's gate 401 (not admin_auth's or
        any other)."""
        return (r.status_code == 401
                and r.text == _BODY_TEXT
                and r.headers.get("content-type") == "text/plain; charset=utf-8")

    def test_gate_desarmado_el_route_corre_sin_basic(self):
        self._desarmar()
        r = self.client.get("/api/admin/whoami")
        self.assertFalse(self._gate_401(r),
                         "with the gate inactive the route must not see the site gate 401")

    def test_gate_armado_sin_credenciales_tapa_el_route_nativo(self):
        # this is the whole point: a native route, without the middleware,
        # would sail past. With the boundary -> gate 401.
        self._armar()
        r = self.client.get("/api/admin/whoami")
        self.assertTrue(self._gate_401(r), r.text)

    def test_gate_armado_realm_presente_en_el_route_nativo(self):
        self._armar()
        r = self.client.get("/api/admin/whoami")
        self.assertTrue(r.headers.get("www-authenticate", "").startswith('Basic realm="'))

    def test_gate_armado_credenciales_validas_abren_el_route_nativo(self):
        # the gate opens -> whoami runs -> with no admin session it returns its
        # JSON 401, DISTINCT from the gate's text/plain 401.
        self._armar()
        r = self.client.get("/api/admin/whoami",
                            headers={"Authorization": _basic(_USER, _PASS)})
        self.assertEqual(r.status_code, 401)  # whoami no session -> admin_auth 401
        self.assertFalse(self._gate_401(r),
                         "with valid credentials the gate opens; the 401 here is admin_auth's")
        self.assertNotEqual(r.text, _BODY_TEXT)

    def test_gate_armado_health_sigue_anonimo_en_el_app_real(self):
        # /health is native AND exempt: the gate does not block it even armed.
        self._armar()
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")


# ---- level 1b: real-app bypass matrix --------------------------------------
# The FOUR requested paths (/, /about.html, /api/services, /logo.png) are
# NATIVE in the real app — served by an exact FastAPI router before the
# terminal (see
# patient_site_router.py / branding_router.py / site_identity_router.py). With
# no credentials -> the middleware gates them with the EXACT gate 401 BEFORE
# any handler runs; with valid credentials -> the gate opens and each reaches
# its real downstream (200). A separate static-only terminal probe uses
# The fingerprinted native queue through the final terminal. Anonymous access must
# stop at the gate; authenticated access must serve the file natively.


class TestRealAppBypassMatrix(_GateEnvMixin):
    """Bypass matrix against the REAL `app`. The four requested paths are
    native: EXACT gate 401 with no credentials, downstream 200 with valid
    credentials. A separate fingerprinted native queue terminal probe proves
    gate-before-terminal after proxy retirement.
    Encoded-delimiter paths prove the gate
    authorizes on the RAW path (not decoded). The five exemptions, anonymous.
    Synthetic: the real legacy server is never started."""

    _CUATRO_NATIVOS = ("/", "/about.html", "/api/services", "/logo.png")
    # Encoded-delimiter probes against exempt routes. On the RAW path each
    # stays literal and does NOT match the exemption; a decoded path would
    # reshape into the exemption (the bypass the raw-path fix closes).
    _ENCODED_PROBES = ("/mcp%2F", "/mcp%3Fevil", "/api/whatsapp/webhook%2Fx")

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import main as app_module
        cls.client = TestClient(app_module.app)

    def _gate_401(self, r):
        """True if the response IS the gate 401 (status + body + challenge
        headers + cache policy)."""
        return (r.status_code == 401
                and r.text == _BODY_TEXT
                and r.headers.get("content-type") == "text/plain; charset=utf-8"
                and r.headers.get("cache-control") == "no-store")

    def _gate_challenge(self, r):
        """The gate challenge without looking at the body (for HEAD, which
        carries no body by spec)."""
        return (r.status_code == 401
                and r.headers.get("content-type") == "text/plain; charset=utf-8"
                and r.headers.get("www-authenticate", "").startswith('Basic realm="')
                and r.headers.get("cache-control") == "no-store")

    # -- the four requested paths, all native --
    def test_cuatro_paths_sin_credenciales_tapados_con_challenge_exacto(self):
        self._armar()
        for path in self._CUATRO_NATIVOS:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertTrue(self._gate_401(r),
                                f"{path}: status={r.status_code} body={r.text[:60]!r}")

    def test_cuatro_paths_con_credenciales_alcanzan_downstream_200(self):
        self._armar()
        for path in self._CUATRO_NATIVOS:
            with self.subTest(path=path):
                r = self.client.get(path, headers={"Authorization": _basic(_USER, _PASS)})
                self.assertEqual(r.status_code, 200, f"{path}: status={r.status_code}")
                self.assertFalse(self._gate_401(r), f"{path}: the gate must not fire")

    # -- encoded-delimiter parity: raw path, not decoded (the fix) --
    def test_encoded_delimiters_no_se_exentan_sin_credenciales(self):
        # Armed + unauth: the middleware authorizes on scope['raw_path']
        # (e.g. b'/mcp%2F'), where %2F/%3F stay literal -> NOT an exempt path
        # -> gate 401. A DECODED path would reshape /mcp%2F -> /mcp/ -> /mcp
        # and walk past the gate; this asserts it does not — the end-to-end
        # proof that the middleware uses the RAW path.
        self._armar()
        for path in self._ENCODED_PROBES:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertTrue(self._gate_401(r),
                                f"{path}: status={r.status_code} body={r.text[:60]!r}")

    def test_authenticated_encoded_mcp_slash_reaches_native_handler(self):
        # Valid creds -> the gate opens on the credential (the RAW path is not
        # exempt, but the credential is valid). Routing decodes %2F only after
        # that gate and the slash-compatibility route returns the native 405.
        # Together with the test above this proves raw-path authorization is
        # still first and native routing cannot create an anonymous bypass.
        self._armar()
        r = self.client.get("/mcp%2F",
                            headers={"Authorization": _basic(_USER, _PASS)})
        self.assertEqual(r.status_code, 405)
        self.assertEqual(r.headers.get("allow"), "POST")
        self.assertFalse(self._gate_401(r))

    # -- separate terminal probe: proves gate-before-terminal --
    def test_terminal_without_credentials_is_blocked_before_resolution(self):
        # The native queue is static-only. A clean 401 proves the middleware
        # blocks it before the native terminal resolves the checkout file.
        self._armar()
        r = self.client.get(static_fingerprint.url("platform/admin-queue.mjs"))
        self.assertTrue(self._gate_401(r), r.text)

    def test_terminal_with_credentials_serves_without_proxying(self):
        self._armar()
        r = self.client.get(static_fingerprint.url("platform/admin-queue.mjs"),
                            headers={"Authorization": _basic(_USER, _PASS)})
        self.assertEqual(r.status_code, 200)
        with open(os.path.join(ROOT, "static/platform/admin-queue.mjs"), "rb") as source:
            self.assertEqual(r.content, source.read())

    # -- the five exemptions, anonymous, against the real app --
    # All five exempt routes are native: the middleware lets them through and
    # each reaches its own handler without touching the catch-all.

    def test_exentas_nativas_anonimas(self):
        # /health, /ready are native: anonymous, their own handler, never
        # touching the proxy/catch-all.
        self._armar()
        for path in ("/health", "/ready"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertFalse(self._gate_401(r), f"{path} debe ser anónima")
                self.assertNotEqual(r.text, "legacy-proxied")

    def test_exenta_chat_transcripts_nativa_anonima_llega_a_su_handler(self):
        # /api/chat-transcripts is NATIVE (modules/chat/chat_api_router.py:46),
        # it does NOT fall to the catch-all: exempt from the Basic gate -> the
        # middleware lets it through -> reaches its OWN handler
        # (chat_api._transcripts). With no TURNOS_CHAT_EXPORT_TOKEN configured,
        # that returns False -> a 404
        # (its own token gate, deterministic). Triple assertion: NOT the site
        # gate 401 (exemption), NOT "legacy-proxied" (never touched forward ->
        # native), and its own handler responds 404 (own behavior preserved).
        self._armar()
        r = self.client.get("/api/chat-transcripts")
        self.assertFalse(self._gate_401(r), "exempt: the gate must not fire")
        self.assertNotEqual(r.text, "legacy-proxied", "native: does not fall to the catch-all")
        self.assertEqual(r.status_code, 404, "its own handler responds 404 without the token")

    def test_anonymous_mcp_exemption_reaches_its_native_handler(self):
        # Stateless GET /mcp is the native handler's exact 405, not the Basic
        # challenge and not the legacy proxy.
        self._armar()
        r = self.client.get("/mcp")
        self.assertFalse(self._gate_401(r))
        self.assertEqual(r.status_code, 405)
        self.assertEqual(r.headers.get("allow"), "POST")
        self.assertEqual(
            r.json(),
            {"error": "método no soportado (servidor MCP sin estado: sólo POST)"},
        )

    def test_anonymous_whatsapp_exemption_reaches_its_native_handler(self):
        # Missing webhook configuration is the native handler's ordinary API
        # 404, not the Basic-auth challenge and not the legacy proxy.
        self._armar()
        r = self.client.get("/api/whatsapp/webhook")
        self.assertFalse(self._gate_401(r))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "not found"})

    # -- non-GET methods are gated before the native terminal --
    def test_head_sobre_ruta_protegida_tapado_antes_del_catchall(self):
        # HEAD carries no body by spec -> assert the challenge (status +
        # headers), not the body.
        self._armar()
        r = self.client.head("/about.html")
        self.assertTrue(self._gate_challenge(r))

    def test_post_sobre_ruta_protegida_tapado_antes_del_catchall(self):
        self._armar()
        r = self.client.post("/")
        self.assertTrue(self._gate_401(r), r.text)


if __name__ == "__main__":
    unittest.main()
