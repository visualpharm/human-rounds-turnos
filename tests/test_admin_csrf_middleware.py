"""test_admin_csrf_middleware.py — the fail-closed Origin / Sec-Fetch-Site
check on state-changing admin requests (core/admin_csrf_middleware.py),
both as a pure decision and through the REAL `app` from app.py.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_admin_csrf_middleware.py
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(DATABASE_URL="",  # boot without Postgres
                      TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1",
                      TURNOS_ADMIN_PASS="csrf-middleware-fixture-pass")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # do not depend on the checkout's real .env

from app.core import admin_csrf_middleware as csrf  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _verdict(method, path, **headers):
    return csrf.verdict(method, path, {k.replace("_", "-").lower(): v for k, v in headers.items()})[0]


class _NoPublicOrigin(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN="")
        self.enterContext(patch.object(config, "dotenv_values", return_value={}))


class VerdictTestCase(_NoPublicOrigin):
    """The transport-neutral decision."""

    def test_no_headers_passes(self):
        self.assertTrue(_verdict("POST", "/api/admin/login", host="turnos.pinamar.gob.ar"))
        self.assertTrue(_verdict("DELETE", "/api/admin/users/x", host="turnos.pinamar.gob.ar"))

    def test_same_origin_passes(self):
        self.assertTrue(_verdict("POST", "/api/admin/login", host="turnos.pinamar.gob.ar",
                                 origin="https://turnos.pinamar.gob.ar",
                                 sec_fetch_site="same-origin"))
        # Case and the default port of the scheme never matter.
        self.assertTrue(_verdict("POST", "/api/admin/login", host="Turnos.Pinamar.gob.ar",
                                 origin="https://turnos.pinamar.gob.ar:443"))
        self.assertTrue(_verdict("POST", "/api/admin/login", host="localhost:7700",
                                 origin="http://localhost:7700"))
        # Behind the proxy: Host is the public name, X-Forwarded-Proto says
        # https, Origin carries the explicit default port.
        self.assertTrue(_verdict("POST", "/admin/solicitudes", host="pinamar.humanrounds.org",
                                 origin="https://pinamar.humanrounds.org:443",
                                 x_forwarded_proto="https"))

    def test_cross_site_fetch_metadata_is_denied(self):
        allowed, reason = csrf.verdict("POST", "/api/admin/requests/1/approve",
                                       {"host": "turnos.pinamar.gob.ar",
                                        "sec-fetch-site": "cross-site",
                                        "origin": "https://turnos.pinamar.gob.ar"})
        self.assertFalse(allowed)
        self.assertEqual(reason, "sec_fetch_site_cross_site")

    def test_mismatched_origin_is_denied(self):
        self.assertFalse(_verdict("POST", "/api/admin/login", host="turnos.pinamar.gob.ar",
                                  origin="https://evil.example"))
        self.assertFalse(_verdict("PUT", "/api/admin/settings", host="localhost:7700",
                                  origin="http://localhost:7701"))
        self.assertFalse(_verdict("POST", "/api/admin/login", host="turnos.pinamar.gob.ar",
                                  origin="null"))
        self.assertFalse(_verdict("POST", "/api/admin/login", host="turnos.pinamar.gob.ar",
                                  origin="not a url"))
        # An Origin with no Host to compare against fails closed.
        self.assertFalse(_verdict("POST", "/api/admin/login",
                                  origin="https://turnos.pinamar.gob.ar"))

    def test_scope_is_admin_mutations_only(self):
        hostile = dict(host="turnos.pinamar.gob.ar", origin="https://evil.example",
                       sec_fetch_site="cross-site")
        # Reads are never blocked (the cookie's SameSite=Lax covers navigation).
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertTrue(_verdict(method, "/api/admin/requests", **hostile))
            self.assertTrue(_verdict(method, "/admin/solicitudes", **hostile))
        # The patient site and the webhooks are deliberately cross-origin.
        for path in ("/api/whatsapp/webhook", "/api/requests", "/api/account/register",
                     "/api/chat", "/api/offer/t/accept", "/mcp", "/", "/administracion"):
            self.assertTrue(_verdict("POST", path, **hostile), path)
        for path in ("/admin", "/admin/", "/admin/solicitudes", "/api/admin/login",
                     "/api/admin/requests/1/approve", "/api/admin-x"):
            self.assertFalse(_verdict("POST", path, **hostile), path)


class PatientLoginCsrfTestCase(_NoPublicOrigin):
    """Security review 2026-11, item 18: the patient login takes JSON only, from its own site."""

    def test_a_plain_text_or_form_login_is_refused(self):
        for kind in ("text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded",
                     "multipart/form-data; boundary=x", ""):
            with self.subTest(kind=kind):
                self.assertFalse(_verdict("POST", "/api/account/login", host="h.example",
                                          content_type=kind))

    def test_json_from_its_own_site_or_a_script_passes(self):
        self.assertTrue(_verdict("POST", "/api/account/login", host="h.example",
                                 content_type="application/json"))
        self.assertTrue(_verdict("POST", "/api/account/login", host="h.example",
                                 content_type="application/json; charset=utf-8",
                                 origin="https://h.example", sec_fetch_site="same-origin"))

    def test_a_cross_site_json_login_is_refused(self):
        self.assertFalse(_verdict("POST", "/api/account/login", host="h.example",
                                  content_type="application/json", origin="https://evil.example"))
        self.assertFalse(_verdict("POST", "/api/account/login", host="h.example",
                                  content_type="application/json", sec_fetch_site="cross-site"))


class PublicOriginTestCase(unittest.TestCase):
    """Behind the municipal portal gateway the Host the app sees may be an
    internal name; the browser's Origin is the installation's public one."""

    def _with(self, value, env=True):
        _env.set_env(self, TURNOS_PUBLIC_ORIGIN=value if env else "")
        self.enterContext(patch.object(config, "dotenv_values",
                                       return_value={} if env else {"TURNOS_PUBLIC_ORIGIN": value}))

    def test_public_origin_passes_whatever_the_host(self):
        self._with("https://salud.pinamar.gob.ar")
        self.assertTrue(_verdict("POST", "/api/admin/requests/1/approve",
                                 host="app.railway.internal:8080",
                                 origin="https://salud.pinamar.gob.ar",
                                 sec_fetch_site="same-origin"))
        self.assertTrue(_verdict("POST", "/api/admin/requests/1/approve",
                                 host="app.railway.internal:8080",
                                 origin="https://Salud.Pinamar.gob.ar:443"))

    def test_public_origin_read_from_the_env_file_too(self):
        self._with("https://salud.pinamar.gob.ar", env=False)
        self.assertTrue(_verdict("POST", "/api/admin/logout", host="app.internal",
                                 origin="https://salud.pinamar.gob.ar"))

    def test_other_origins_still_denied(self):
        self._with("https://salud.pinamar.gob.ar")
        for origin in ("http://salud.pinamar.gob.ar", "https://salud.pinamar.gob.ar:8443",
                       "https://evil.example", "https://pinamar.gob.ar"):
            self.assertFalse(_verdict("POST", "/api/admin/logout", host="app.internal",
                                      origin=origin), origin)
        # The public origin never excuses a cross-site fetch.
        self.assertFalse(_verdict("POST", "/api/admin/logout", host="app.internal",
                                  origin="https://salud.pinamar.gob.ar",
                                  sec_fetch_site="cross-site"))

    def test_unusable_public_origin_is_ignored(self):
        for value in ("salud.pinamar.gob.ar", "ftp://salud.pinamar.gob.ar", "https://"):
            with self.subTest(value=value):
                with _env.patched_env(TURNOS_PUBLIC_ORIGIN=value):
                    self.assertFalse(_verdict("POST", "/api/admin/logout", host="app.internal",
                                              origin="https://salud.pinamar.gob.ar"))


class RealAppTestCase(_NoPublicOrigin):
    """Through app.py's composition: the 403 shape and the untouched routes."""

    def setUp(self):
        super().setUp()
        self.client = TestClient(app_module.app, base_url="http://testserver",
                                 follow_redirects=False)
        self.addCleanup(self.client.close)
        self.cookies = {"st_admin": admin_auth._mint_token("t", "admin")}

    def _post(self, path, **headers):
        self.client.cookies.update(self.cookies)
        return self.client.post(path, content=b"{}",
                                headers={"Content-Type": "application/json", **headers})

    def test_cross_site_post_is_403_json_no_store(self):
        resp = self._post("/api/admin/logout", **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(resp.headers["cache-control"], "no-store")
        self.assertEqual(resp.json(), {"error": "origen_no_permitido"})

    def test_mismatched_origin_post_is_403(self):
        resp = self._post("/api/admin/logout", Origin="https://evil.example")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "origen_no_permitido"})

    def test_same_origin_and_headerless_posts_reach_the_route(self):
        same = self._post("/api/admin/logout", Origin="http://testserver",
                          **{"Sec-Fetch-Site": "same-origin"})
        bare = self._post("/api/admin/logout")
        for resp in (same, bare):
            self.assertNotEqual(resp.status_code, 403)
            self.assertNotIn(b"origen_no_permitido", resp.content)

    def test_cross_site_get_and_patient_posts_are_untouched(self):
        hostile = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
        self.client.cookies.update(self.cookies)
        get = self.client.get("/api/admin/whoami", headers=hostile)
        self.assertNotIn(b"origen_no_permitido", get.content)
        webhook = self.client.post("/api/whatsapp/webhook", content=b"{}", headers=hostile)
        self.assertNotIn(b"origen_no_permitido", webhook.content)

    def test_check_runs_inside_the_legacy_rewrite(self):
        # user_middleware is outermost first: the check must come after the
        # rewrite so a legacy alias of an admin endpoint is matched canonical.
        names = [m.cls.__name__ for m in app_module.app.user_middleware]
        self.assertIn("AdminCsrfMiddleware", names)
        self.assertGreater(names.index("AdminCsrfMiddleware"),
                           names.index("LegacyUrlRewriteMiddleware"))


if __name__ == "__main__":
    unittest.main()
