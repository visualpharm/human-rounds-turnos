"""test_reports_system_native_router.py — offline native-router tests
(native-route migration campaign, family F4,
docs/roadmap/native-migration-campaign-20260826.md):
`admin_panel/reports_router.py` and `admin_panel/system_status_router.py`
through the real ASGI app, pinning status/body of the pure
`_*_payload()`/`_*_csv_rows()` functions (reports.py) directly.

No managed Postgres needed: `reports_store` raises `DbUnavailable`
immediately with `DATABASE_URL=""` (no connection attempt, no timeout), and
`system_status.status()` degrades every external probe gracefully with no
key configured (Mailgun/OpenRouter both short-circuit before any network
call) — see `test_system_status.py`'s own docstring for the same discipline.
`/api/admin/reports/reviews` is the one reports sub-report with a genuinely
offline 200 happy path (file-based, never touches the DB); every OTHER
reports sub-report is exercised here only through its DB-down 503 branch —
their real-data 200 shape already has full coverage against a real database
in test_reports.py/test_reports_nuevos.py/test_reports_insights.py, which
this file does not duplicate.

Run:  TURNOS_FAKE_AI=1 DATABASE_URL="" python3 -m unittest \
      test.test_reports_system_native_router -v
"""

import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="reports-native-router-fixture-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # don't depend on the real checkout's .env

from app.auth import admin_auth  # noqa: E402
from app import main as app_module  # noqa: E402
from app.admin_panel import reports  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _cookie_jar(usuario, rol):
    """`st_admin` session cookie shaped for `TestClient(...).get(path,
    cookies=...)`, minted the way admin_auth._mint_token() is used elsewhere
    in this suite (see test_reports.py's own `_cookie()`)."""
    return {"st_admin": admin_auth._mint_token(usuario, rol)}


class ReportsNativeRouterTestCase(unittest.TestCase):
    """GET /api/admin/reports/* through the real ASGI app, including native
    auth, DB-unavailable, and file-backed response contracts."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL="", TURNOS_ENV_FILE="/dev/null")
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        native = self.client.get("/api/admin/reports")
        self.assertEqual(native.status_code, 401)
        # Flat failure body — reports_router keeps the unwrapped
        # `{"error": ...}` shape, unlike the `{"detail": {...}}` wrap
        # `core.deps.require_admin`-gated routers produce.
        self.assertEqual(native.json(), {"error": "no_autenticado"})

    def test_medico_role_403s(self):
        native = self.client.get("/api/admin/reports", cookies=_cookie_jar("t", "medico"))
        self.assertEqual(native.status_code, 403)

    def test_reportes_role_is_admitted_same_as_admin(self):
        """`permitir_reportes=True` is the ONE solo_admin exemption the
        router must honor (report-design contract, see reports_router.py's
        own docstring) — proven here by reaching the db-down 503 branch,
        not 403."""
        native = self.client.get("/api/admin/reports", cookies=_cookie_jar("t", "reportes"))
        self.assertEqual(native.status_code, 503)

    def test_db_down_503_error_body(self):
        """DbUnavailable returns the shared JSON error for JSON and CSV
        endpoints alike (a CSV route's error response is JSON)."""
        for path in ("/api/admin/reports", "/api/admin/reports/patients",
                     "/api/admin/reports/requests.csv", "/api/admin/reports/cost-recovery",
                     "/api/admin/reports/sumar", "/api/admin/reports/statistics"):
            with self.subTest(path=path):
                native = self.client.get(path, cookies=_cookie_jar("t", "admin"))
                self.assertEqual(native.status_code, 503)
                self.assertEqual(native.headers["content-type"], "application/json; charset=utf-8")
                self.assertEqual(native.headers["cache-control"], "no-store")
                self.assertEqual(native.json(), {"error": "base de datos no disponible"})

    def test_reviews_offline_200_payload(self):
        """The one reports sub-report with a real, deterministic, DB-free
        200 body — file-based (resenas-analisis.json/centros-resenas.json)
        — asserted on its ACTUAL success payload, not just the db-down
        branch."""
        native = self.client.get("/api/admin/reports/reviews", cookies=_cookie_jar("t", "admin"))
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(native.headers["cache-control"], "no-store")
        # And equal to the pure domain function directly — proving the
        # transport is a thin envelope around it, not a second copy.
        self.assertEqual(native.json(), reports._reviews_payload())

    def test_days_param_reads_the_native_query_string(self):
        """`_days_param(query)` is shared by every caller — this proves the
        native call site (`request.url.query`) still parses `?days=`
        correctly, using the db-down 503 branch as the observable proxy for
        "the endpoint ran with the parsed period", same technique
        test_reports.py's own `test_dias_invalido_cae_al_default` uses."""
        native = self.client.get("/api/admin/reports/patients?days=90",
                                  cookies=_cookie_jar("t", "admin"))
        self.assertEqual(native.status_code, 503)  # got past parsing, hit the DB guard
        native_bad = self.client.get("/api/admin/reports/patients?days=not-a-number",
                                      cookies=_cookie_jar("t", "admin"))
        self.assertEqual(native_bad.status_code, 503)  # invalid value falls back, never 500


class SystemStatusNativeRouterTestCase(unittest.TestCase):
    """GET /api/admin/system via the real ASGI app. Fully deterministic
    offline: no MAILGUN_API_KEY/OPENROUTER key configured means
    `_cluster_email`/`_cluster_openrouter` both short-circuit before any
    network call (same discipline test_system_status.py's module docstring
    documents for its own tests)."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL="", TURNOS_ENV_FILE="/dev/null")
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        self.client = TestClient(app_module.app)

    def test_no_session_401s(self):
        native = self.client.get("/api/admin/system")
        self.assertEqual(native.status_code, 401)

    def test_reportes_role_403s_unlike_reports_router(self):
        """system_status is solo_admin with NO permitir_reportes opt-in —
        the one role-gate difference from reports_router.py, worth pinning
        down explicitly so a future edit can't quietly merge the two
        gates."""
        native = self.client.get("/api/admin/system", cookies=_cookie_jar("t", "reportes"))
        self.assertEqual(native.status_code, 403)

    def test_admin_session_200(self):
        # Fix the Host header explicitly: `_cluster_domain()` reads it, and
        # httpx's TestClient always sends a real `Host: testserver`.
        native = self.client.get(
            "/api/admin/system", cookies=_cookie_jar("t", "admin"),
            headers={"Host": "turnos.pinamar.gob.ar"},
        )
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(native.headers["cache-control"], "no-store")
        # The HSI card is always there (2026-11-10, HSI visible in the
        # panel): with no connector configured it names that state instead
        # of disappearing — the same 11-cluster count
        # test_system_status.py's own native-transport proof pins.
        clusters = native.json()["clusters"]
        self.assertEqual(len(clusters), 11)
        self.assertIn("hsi", [c["clave"] for c in clusters])


if __name__ == "__main__":
    unittest.main()
