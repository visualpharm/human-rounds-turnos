"""test_site_auth.py — Basic Auth de pre-lanzamiento (site_auth.py): la
mecánica del gate en sí (degrada abierto en dev, exige credenciales exactas
cuando está activo, un header corrupto/mal formado termina en 401 en vez de
reventar) y sus excepciones para `/api/whatsapp/webhook` y `/mcp`
(2026-07-23: ambos tienen su propia auth — el Basic Auth compartido del
sitio no debe taparlos).

Correr:  python3 -m unittest discover -s tests -p "test_site_auth.py"
"""

import base64
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.auth import site_auth  # noqa: E402
from app.core import static_fingerprint  # noqa: E402

_USER = "demo"
_PASS = "s3cr3t"


class RequestValues:
    """Transport-free path and authorization inputs for the policy tests."""

    def __init__(self, path="/", headers=None, authorization=None):
        self.path = path
        h = dict(headers or {})
        if authorization is not None:
            h["Authorization"] = authorization
        self.headers = h


def _authorize_request(values):
    return site_auth.authorize(
        values.path, values.headers.get("Authorization", ""),
    )


def _basic_headers(user, pwd):
    raw = f"{user}:{pwd}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _basic(user, pwd):
    return "Basic " + base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")


# ---- el gate activo/inactivo + las excepciones por ruta ----------------------

class TestGateActivo(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_BASIC_AUTH_USER="demo", TURNOS_BASIC_AUTH_PASS="clave-de-prueba")

    def test_sin_credenciales_401(self):
        request = RequestValues("/")
        self.assertFalse(_authorize_request(request))

    def test_credenciales_correctas_pasan(self):
        request = RequestValues("/", headers=_basic_headers("demo", "clave-de-prueba"))
        self.assertTrue(_authorize_request(request))

    def test_credenciales_incorrectas_401(self):
        request = RequestValues("/", headers=_basic_headers("demo", "mala"))
        self.assertFalse(_authorize_request(request))

    def test_whatsapp_webhook_exento_sin_credenciales(self):
        request = RequestValues("/api/whatsapp/webhook")
        self.assertTrue(_authorize_request(request))

    def test_whatsapp_webhook_exento_con_query_string(self):
        request = RequestValues("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=x")
        self.assertTrue(_authorize_request(request))

    def test_mcp_exento_sin_credenciales(self):
        request = RequestValues("/mcp")
        self.assertTrue(_authorize_request(request))

    # Tier 4 slice 7 (docs/roadmap/tier4-replatform-design.md) — un
    # healthcheck de despliegue no puede cargar la credencial compartida.
    def test_health_exento_sin_credenciales(self):
        request = RequestValues("/health")
        self.assertTrue(_authorize_request(request))

    def test_ready_exento_sin_credenciales(self):
        request = RequestValues("/ready")
        self.assertTrue(_authorize_request(request))

    def test_ruta_no_exenta_sigue_pidiendo_basic_auth(self):
        request = RequestValues("/api/whatsapp/webhookzzz")  # prefijo, no la ruta exacta
        self.assertFalse(_authorize_request(request))

    # /admin NO está exento (se probó exentarlo el 2026-07-27 y se revirtió el
    # mismo día): la página quedaba abierta pero sus ~20 .js seguían detrás del
    # Basic Auth, o sea un panel a medio cargar. Que esté detrás no desloguea a
    # nadie — admin.html distingue este 401 del suyo (ver sesionMurio).
    def test_el_panel_admin_sigue_detras_del_basic_auth(self):
        for path in ("/admin", "/admin/", "/api/admin/whoami", "/api/admin/login",
                     static_fingerprint.url("platform/admin-queue.mjs"), "/human-rounds.css"):
            with self.subTest(path=path):
                request = RequestValues(path)
                self.assertFalse(_authorize_request(request))

    def test_solo_estan_exentas_las_rutas_con_auth_propia(self):
        # Tier 4 slice 7 sumó /health y /ready (ver site_auth.py's docstring
        # y test_health_exento_sin_credenciales/test_ready_exento_sin_credenciales
        # arriba) a las tres exenciones originales.
        self.assertEqual(set(site_auth._EXENTAS),
                         {"/api/whatsapp/webhook", "/mcp", "/api/chat-transcripts",
                          "/health", "/ready", "/api/analytics/events", "/api/analytics/schedule",
                          # the assistant's OAuth sign-in (2026-11-13)
                          "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
                          "/.well-known/oauth-authorization-server",
                          "/.well-known/oauth-authorization-server/mcp",
                          "/.well-known/openid-configuration", "/oauth/register", "/oauth/authorize",
                          "/oauth/token"})


class TestGateInactivo(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_BASIC_AUTH_USER=None, TURNOS_BASIC_AUTH_PASS=None)

    def test_sin_env_todo_pasa(self):
        self.assertTrue(_authorize_request(RequestValues("/")))
        self.assertTrue(_authorize_request(RequestValues("/admin")))


# ---- mecánica fina del gate (activo(), formas del header) --------------------

class _EnvMixin(unittest.TestCase):
    def setUp(self):
        self._desactivar()

    def _activar(self):
        _env.set_env(self, {site_auth._USER_ENV: _USER, site_auth._PASS_ENV: _PASS})

    def _desactivar(self):
        _env.set_env(self, {site_auth._USER_ENV: None, site_auth._PASS_ENV: None})


class TestActivo(_EnvMixin):
    def test_ambas_seteadas(self):
        self._activar()
        self.assertTrue(site_auth.activo())

    def test_falta_una_no_activa(self):
        _env.set_env(self, {site_auth._USER_ENV: _USER, site_auth._PASS_ENV: None})
        self.assertFalse(site_auth.activo())

    def test_ninguna_no_activa(self):
        self._desactivar()
        self.assertFalse(site_auth.activo())


class TestCheck(_EnvMixin):
    def test_inactivo_deja_pasar_sin_401(self):
        self._desactivar()
        request = RequestValues()
        self.assertTrue(_authorize_request(request))

    def test_credenciales_correctas(self):
        self._activar()
        request = RequestValues(authorization=_basic(_USER, _PASS))
        self.assertTrue(_authorize_request(request))

    def test_password_incorrecta_es_401(self):
        self._activar()
        request = RequestValues(authorization=_basic(_USER, "otra"))
        self.assertFalse(_authorize_request(request))
        self.assertIn("WWW-Authenticate", dict(site_auth.challenge_headers()))

    def test_usuario_incorrecto_es_401(self):
        self._activar()
        request = RequestValues(authorization=_basic("otro", _PASS))
        self.assertFalse(_authorize_request(request))

    def test_sin_header_es_401(self):
        self._activar()
        request = RequestValues()
        self.assertFalse(_authorize_request(request))

    def test_base64_corrupto_no_revienta(self):
        self._activar()
        request = RequestValues(authorization="Basic no-es-base64-valido!!!")
        self.assertFalse(_authorize_request(request))

    def test_esquema_no_basic_es_401(self):
        self._activar()
        request = RequestValues(authorization="Bearer un-token-cualquiera")
        self.assertFalse(_authorize_request(request))

    def test_header_sin_dos_puntos_es_401(self):
        # base64 de un texto sin ':' → pwd queda vacío, no matchea
        self._activar()
        raw = base64.b64encode(b"soloelusuario").decode("ascii")
        request = RequestValues(authorization="Basic " + raw)
        self.assertFalse(_authorize_request(request))


# ---- transport-neutral decision (authorize) + exact 401 bytes (challenge) --
# These cover the new API the ASGI middleware (site_auth_middleware.py) calls
# directly: authorize(path, header) and challenge_body()/challenge_headers().
# The decision and challenge bytes are covered at their own level, independent
# of any request or response object.

class TestAuthorize(_EnvMixin):
    """authorize(path, authorization_header) — the ONE decision, shared by the
    native ASGI middleware."""

    def test_inactivo_deja_pasar_cualquier_ruta(self):
        self._desactivar()
        for path in ("/", "/admin", "/api/admin/whoami"):
            self.assertTrue(site_auth.authorize(path, ""),
                            f"{path} debe pasar con el gate inactivo")

    def test_activo_ruta_normal_sin_header_false(self):
        self._activar()
        self.assertFalse(site_auth.authorize("/", ""))

    def test_activo_credenciales_correctas_true(self):
        self._activar()
        self.assertTrue(site_auth.authorize("/", _basic(_USER, _PASS)))

    def test_activo_password_incorrecta_false(self):
        self._activar()
        self.assertFalse(site_auth.authorize("/", _basic(_USER, "otra")))

    def test_activo_usuario_incorrecto_false(self):
        self._activar()
        self.assertFalse(site_auth.authorize("/", _basic("otro", _PASS)))

    def test_activo_esquema_no_basic_false(self):
        self._activar()
        self.assertFalse(site_auth.authorize("/", "Bearer un-token"))

    def test_activo_base64_corrupto_false(self):
        self._activar()
        self.assertFalse(site_auth.authorize("/", "Basic no-es-base64!!!"))

    def test_activo_header_sin_dospuntos_false(self):
        # base64 of text with no ':' -> empty pwd, no match
        self._activar()
        raw = base64.b64encode(b"soloelusuario").decode("ascii")
        self.assertFalse(site_auth.authorize("/", "Basic " + raw))

    def test_activo_scheme_basic_case_sensitive_como_legacy(self):
        # legacy was case-sensitive on "Basic " (startswith); authorize
        # preserves that EXACT behavior, it does not relax it.
        self._activar()
        self.assertFalse(site_auth.authorize("/", _basic(_USER, _PASS).lower()))

    def test_las_cinco_exentas_pasan_sin_credenciales(self):
        self._activar()
        for path in site_auth._EXENTAS:
            self.assertTrue(site_auth.authorize(path, ""),
                            f"{path} debe ser anónima (exenta)")

    def test_exenta_con_query_string_pasa(self):
        self._activar()
        self.assertTrue(site_auth.authorize(
            "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=x", ""))

    def test_exenta_con_slash_final_pasa(self):
        self._activar()
        # _clean_path strips the trailing slash -> matches the exempt route
        self.assertTrue(site_auth.authorize("/health/", ""))

    def test_prefijo_de_exenta_NO_es_exenta(self):
        # "/api/whatsapp/webhookzzz" is a prefix, not the exact route -> gated
        self._activar()
        self.assertFalse(site_auth.authorize("/api/whatsapp/webhookzzz", ""))

    def test_path_none_no_revienta(self):
        self._activar()
        # robustness: a missing path does not crash (falls back to "" -> not
        # exempt). No credentials -> False (401); the point is no exception.
        self.assertFalse(site_auth.authorize(None, ""))

    def test_path_none_con_basic_valido_pasa(self):
        # fail-closed for the EXEMPTION, not for credentials: an unknown path
        # (None) matches no exemption, so the gate demands a credential — and a
        # valid Basic credential still passes. This is the missing-raw_path
        # contract the ASGI middleware relies on (see site_auth_middleware).
        self._activar()
        self.assertTrue(site_auth.authorize(None, _basic(_USER, _PASS)))

    # ---- encoded-delimiter parity (raw path contract) ----------------------
    # authorize() MUST be fed the RAW (undecoded) path: an encoded delimiter
    # (%2F -> /, %3F -> ?) reshapes the path only AFTER decoding. On the raw
    # path these stay literal, so they can never turn a protected path into an
    # exemption. The contrast test below pins the distinction: the RAW form is
    # gated, its DECODED equivalent is exempt — so a transport that decodes
    # (request.url.path) would bypass, one that passes raw (handler.path /
    # scope['raw_path']) does not.

    def test_encoded_slash_en_exenta_no_la_matchea(self):
        # RAW /mcp%2F: %2F stays literal -> NOT /mcp -> gated.
        self._activar()
        self.assertFalse(site_auth.authorize("/mcp%2F", ""))

    def test_decoded_equivalent_de_esa_si_es_exenta(self):
        # The DECODED equivalent /mcp/ strips to /mcp -> exempt. This is the
        # exact contrast that makes the raw-path contract load-bearing: if the
        # transport passed a decoded path, /mcp%2F would wrongly pass here.
        self._activar()
        self.assertTrue(site_auth.authorize("/mcp/", ""))

    def test_encoded_question_en_exenta_no_la_matchea(self):
        # RAW /mcp%3Fevil: %3F stays literal, _clean_path only splits a literal
        # '?' -> NOT /mcp -> gated. (Decoded it would be /mcp?evil -> /mcp ->
        # exempt = bypass.)
        self._activar()
        self.assertFalse(site_auth.authorize("/mcp%3Fevil", ""))

    def test_encoded_slash_dentro_de_webhook_no_la_matchea(self):
        self._activar()
        self.assertFalse(site_auth.authorize("/api/whatsapp/webhook%2Fx", ""))

    def test_ruta_protegida_con_encoded_delimiter_sigue_protegida(self):
        # /admin%2F has no exempt match either way; this pins that an encoded
        # delimiter never OPENS a protected path, regardless of transport.
        self._activar()
        self.assertFalse(site_auth.authorize("/admin%2F", ""))


class TestChallenge(_EnvMixin):
    """The EXACT 401 bytes — one source (challenge_body / challenge_headers),
    consumed by the native ASGI middleware."""

    def test_body_es_el_texto_fijo(self):
        self.assertEqual(site_auth.challenge_body(),
                         "Autenticación requerida".encode("utf-8"))

    def test_headers_tienen_realm_y_content_type(self):
        headers = dict(site_auth.challenge_headers())
        self.assertIn("WWW-Authenticate", headers)
        self.assertIn("Content-Type", headers)
        # realm = 'Basic realm="<brand>"' — the browser uses it for the popup
        self.assertTrue(headers["WWW-Authenticate"].startswith('Basic realm="'))
        self.assertTrue(headers["WWW-Authenticate"].endswith('"'))
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")

    def test_headers_no_incluyen_content_length(self):
        # Content-Length is computed by each transport from the body, not here
        names = [k for k, _ in site_auth.challenge_headers()]
        self.assertNotIn("Content-Length", names)

    def test_headers_no_incluyen_cache_control(self):
        # Cache-Control: no-store lives ASGI-side ONLY (_unauthorized_response),
        # NOT here: the legacy transport already adds it via
        # server.Handler.end_headers() on every >=400, so putting it here would
        # duplicate it on the legacy wire. The shared list is only the two
        # headers above.
        names = [k for k, _ in site_auth.challenge_headers()]
        self.assertNotIn("Cache-Control", names)

    def test_orden_headers_es_realm_luego_content_type(self):
        # same order the legacy handler sent (the middleware's raw_headers is
        # built from this list, in this order)
        names = [k for k, _ in site_auth.challenge_headers()]
        self.assertEqual(names, ["WWW-Authenticate", "Content-Type"])

    def test_realm_refleja_la_marca_activa(self):
        from app.core import theme
        headers = dict(site_auth.challenge_headers())
        self.assertIn(theme.brand_name(), headers["WWW-Authenticate"])


if __name__ == "__main__":
    unittest.main()
