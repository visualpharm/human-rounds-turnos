"""test_host_redirect_middleware.py — the canonical-host 308 as the outermost
ASGI boundary (core/host_redirect_middleware.py), against the REAL `app` from
app.py.

The regression this pins (found live 2026-08-14): in proxy mode the natively
served routes (site_identity's `/`, platform routers) never reached
server.py's host_redirect, so `salud.brieflysites.com/` answered 200 with the
Pinamar homepage instead of the contracted 308 to
pinamar.humanrounds.org/datos — while every legacy-proxied path still
redirected. The middleware must make the redirect host-wide again, for every
method, before the site-auth gate.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_host_redirect_middleware.py"
"""
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
from fastapi.testclient import TestClient  # noqa: E402


def _client(host):
    # base_url sets the Host header TestClient sends; redirects must be
    # returned to the caller, never followed.
    return TestClient(app_module.app, base_url="https://" + host,
                      follow_redirects=False)


class SaludFixedTargetTest(unittest.TestCase):
    """salud.brieflysites.com always lands on the one fixed page."""

    def test_root_redirects_to_datos(self):
        resp = _client("salud.brieflysites.com").get("/")
        self.assertEqual(resp.status_code, 308)
        self.assertEqual(resp.headers["Location"],
                         "https://pinamar.humanrounds.org/datos")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(resp.content, b"")

    def test_any_path_redirects_to_datos(self):
        resp = _client("salud.brieflysites.com").get("/cualquier/cosa?x=1")
        self.assertEqual(resp.status_code, 308)
        self.assertEqual(resp.headers["Location"],
                         "https://pinamar.humanrounds.org/datos")

    def test_post_is_method_preserving_308(self):
        resp = _client("salud.brieflysites.com").post("/", content=b"{}")
        self.assertEqual(resp.status_code, 308)
        self.assertEqual(resp.headers["Location"],
                         "https://pinamar.humanrounds.org/datos")


class OldHostSuffixTest(unittest.TestCase):
    def test_old_host_preserves_path_and_query(self):
        resp = _client("sana-turnos.brieflysites.com").get(
            "/service/ecografia?source=old")
        self.assertEqual(resp.status_code, 308)
        self.assertEqual(
            resp.headers["Location"],
            "https://pinamar.humanrounds.org/service/ecografia?source=old")


class CanonicalHostTest(unittest.TestCase):
    def test_canonical_host_is_not_redirected(self):
        # Whatever `/` answers on the canonical host (200 page, or 401 when
        # the pre-launch gate is armed in the test env), it is never the
        # canonical-host 308.
        resp = _client("pinamar.humanrounds.org").get("/")
        self.assertNotEqual(resp.status_code, 308)


if __name__ == "__main__":
    unittest.main()
