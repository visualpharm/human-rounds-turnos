"""Broad FastAPI templates preserve the dark terminal's rejected-id body."""

import unittest
from unittest import mock
import _env  # noqa: E402


class TestModuleRouterFallthrough(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _env.set_env(cls, {
            "DATABASE_URL": None,
            "TEST_DATABASE_URL": None,
            "TURNOS_ENV_FILE": "/dev/null",
            "TURNOS_CONFIG_LOCK": "1",
            "TURNOS_FAKE_AI": "1",
            "TURNOS_SKIP_HSI": "1",
        })

    def test_template_match_rejected_by_inner_regex_uses_terminal_contract(self):
        from fastapi.testclient import TestClient
        from app import main as app

        with (
            mock.patch("app.ops.web_runtime.start_background_services"),
            mock.patch("app.ops.web_runtime.stop_app_modules"),
            TestClient(app.app) as client,
        ):
            response = client.get("/api/requests/not-a-number")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b'{"error": "not found"}')
        self.assertEqual(
            response.headers["content-type"],
            "application/json; charset=utf-8",
        )
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_module_decline_and_native_terminal_share_exact_json_response(self):
        from app.core.terminal_response import json_not_found_response
        from app.routers.native_terminal import _json_not_found

        expected = json_not_found_response()
        observed = _json_not_found()
        self.assertEqual(observed.status_code, expected.status_code)
        self.assertEqual(observed.body, expected.body)
        self.assertEqual(dict(observed.headers), dict(expected.headers))

    def test_passkeys_unauthenticated_keeps_the_flat_legacy_401(self):
        """The passkeys router is native, and a REAL unauthenticated request
        answers 401 with the flat legacy body — that live contract is what is
        pinned."""
        from fastapi.testclient import TestClient
        from app import main as app

        with (
            mock.patch("app.ops.web_runtime.start_background_services"),
            mock.patch("app.ops.web_runtime.stop_app_modules"),
            TestClient(app.app) as client,
        ):
            observed = client.get("/api/admin/passkeys")

        self.assertEqual(observed.status_code, 401)
        self.assertEqual(observed.content, b'{"error": "no_autenticado"}')
        self.assertEqual(observed.headers["content-type"],
                          "application/json; charset=utf-8")
        self.assertEqual(observed.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
