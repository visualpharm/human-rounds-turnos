"""test_f5_native_routers.py — offline parity tests for the family F5 native
routers (docs/roadmap/native-migration-campaign-20260826.md):
settings_admin_router, admin_preferences_router, admin_content_router,
admin_account_router, doctor_patients_router.

No managed DB needed: `DATABASE_URL=""` makes any `db.*` call raise
`db.DbUnavailable`, which each router maps to the same 503 the legacy
`_db_guard`/`handle()` already answered with — that IS one of the branches
under test, not something worked around.

The auth-failure body shape is the one behavior these tests exist to pin:
`core.deps.require_admin` raises `HTTPException(detail=<flat dict>)`, which
FastAPI's default handler would wrap as `{"detail": <flat dict>}` — fine for
the HTML `admin_*_screen.py` page routes already on that pattern, but wrong
for these JSON API endpoints, whose frontend parses `response.json().error`
at the top level. `admin_panel._native_support.gate()` unwraps it back to
the legacy flat shape; `test_auth_failure_is_flat_not_wrapped_in_detail`
locks that in.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_f5_native_routers.py"
"""

import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="f5-native-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _cookies(role="admin", usuario="f5-native-test"):
    return {"st_admin": admin_auth._mint_token(usuario, role)}


class F5NativeRoutersTestCase(unittest.TestCase):
    def setUp(self):
        # `setdefault("DATABASE_URL", "")` above is a no-op whenever the run has
        # a database configured, and the 503 tests passed only after another
        # module had left a dead URL behind — pin it per test instead.
        _env.set_env(self, DATABASE_URL="")
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    # ---- the design decision this whole family's auth wiring hinges on ----

    def test_auth_failure_is_flat_not_wrapped_in_detail(self):
        """A real JSON API 401/403 must stay `{"error": ...}` at the top
        level — never FastAPI's default `{"detail": {"error": ...}}`
        envelope — because the admin-panel frontend parses it flat."""
        for path, method in (
            ("/api/admin/settings", "get"),
            ("/api/admin/preferences", "get"),
            ("/api/admin/account", "get"),
            ("/api/admin/doctor-patients", "get"),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 401)
                body = response.json()
                self.assertNotIn("detail", body)
                self.assertIn("error", body)

    # ---- settings_admin_router ----

    def test_settings_get_and_probe_unknown_integration(self):
        get = self.client.get("/api/admin/settings", cookies=_cookies())
        self.assertEqual(get.status_code, 200)
        self.assertEqual(get.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(get.headers["cache-control"], "no-store")
        self.assertIn("api_keys", get.json())

        probe = self.client.post(
            "/api/admin/settings/probar", json={"integracion": "no-existe"}, cookies=_cookies(),
        )
        self.assertEqual(probe.status_code, 200)
        self.assertEqual(probe.json()["ok"], False)

        bad_group = self.client.post(
            "/api/admin/settings", json={"grupo": "no-existe", "valores": {}}, cookies=_cookies(),
        )
        self.assertEqual(bad_group.status_code, 400)
        self.assertIn("error", bad_group.json())

    # ---- admin_preferences_router ----

    def test_preferences_db_unavailable_is_503(self):
        response = self.client.get("/api/admin/preferences", cookies=_cookies())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "base de datos no disponible"})

    # ---- admin_content_router ----

    def test_content_invalid_tipo_is_the_shared_terminal_404(self):
        for path, method in (
            ("/api/admin/content/no-existe", "get"),
            ("/api/admin/content/no-existe", "post"),
            ("/api/admin/content/no-existe/some-id", "put"),
            ("/api/admin/content/no-existe/some-id", "delete"),
            ("/api/admin/content/no-existe/some-id/restaurar", "post"),
            ("/api/admin/content/services/not-properties", "post"),
        ):
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(path, cookies=_cookies())
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.content, b'{"error": "not found"}')
                self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_content_invalid_tipo_404s_before_the_auth_gate(self):
        """No session cookie at all — still a plain 404, not 401: the legacy
        regex decline happens before `admin_auth.require_admin` ever runs."""
        response = self.client.get("/api/admin/content/no-existe")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b'{"error": "not found"}')

    def test_content_valid_tipo_db_unavailable_is_503(self):
        # catalog.admin_list() merges in DB-backed overrides, so a valid
        # `tipo` still needs Postgres — same 503 `_db_guard` already sends.
        response = self.client.get("/api/admin/content/services", cookies=_cookies())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "base de datos no disponible"})

    # ---- admin_account_router ----

    def test_account_get_owner_status(self):
        response = self.client.get("/api/admin/account", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["usuario"], "f5-native-test")
        self.assertEqual(body["rol"], "admin")

    def test_account_password_change_wrong_current_is_400(self):
        response = self.client.post(
            "/api/admin/account/password",
            json={"actual": "wrong", "nueva": "a-new-password"},
            cookies=_cookies(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "La contraseña actual no coincide."})

    # ---- doctor_patients_router ----

    def test_doctor_patients_admin_without_medico_returns_picker(self):
        response = self.client.get("/api/admin/doctor-patients", cookies=_cookies())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["medico_id"])
        self.assertEqual(body["items"], [])
        self.assertIn("medicos", body)

    # Note: a "medico" role always looks up its own `medico_id` via
    # `db_operators.operator_doctor_id()` FIRST — with no DB configured that raises
    # `db.DbUnavailable` uncaught, same as the legacy `_list()` (which never
    # wraps that specific call either) — an equally-uncaught 500 on both
    # transports, not a clean parity case to assert byte-for-byte offline.


if __name__ == "__main__":
    unittest.main()
