"""test_client_errors.py — the staff screens' browser errors
(`templates/admin/base.html` -> `POST /api/admin/client-errors` ->
`core.error_tracking.capture_browser_error`).

Pins: only a signed-in operator reports; a report full of personal data
leaves (stdout line and the Sentry event) without any of it; the screen and
the script file stay readable so the error is still diagnosable; a burst is
capped per operator; the reporter lives in the staff layout only.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_client_errors.py"
"""

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="client-errors-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.admin_panel import client_errors_router  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import error_tracking  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_PII = ("María Fernanda Quiroga", "30111222", "30.111.222", "2254 41-5566",
        "maria.quiroga@example.com", "tok_9f8e7d6c5b4a39281706")

_REPORT = {
    "kind": "error",
    "message": ("TypeError: no se pudo leer paciente 30111222 maria.quiroga@example.com "
                "tel 2254 41-5566 en https://pinamar.example/admin/pacientes?dni=30.111.222"),
    "source": "https://pinamar.example/static/admin-app.js?v=abc",
    "line": 120, "col": 7,
    "stack": ("TypeError: x\n"
              "    at renderFila (https://pinamar.example/static/admin-app.js?dni=30111222:120:7)\n"
              "    at https://pinamar.example/admin/pacientes/30111222/tok_9f8e7d6c5b4a39281706:3:1"),
    "screen": "/admin/pacientes/30111222",
}


def _cookies(role="admin", usuario="client-errors-test"):
    return {"st_admin": admin_auth._mint_token(usuario, role)}


class TestBrowserEvent(unittest.TestCase):
    def test_nothing_personal_leaves_in_the_event(self):
        dumped = json.dumps(error_tracking.scrub_event(error_tracking.browser_event(_REPORT)),
                            ensure_ascii=False)
        for value in _PII:
            self.assertNotIn(value, dumped)
        for query in ("?dni=", "?v=", "dni="):
            self.assertNotIn(query, dumped)

    def test_the_error_is_still_diagnosable(self):
        event = error_tracking.scrub_event(error_tracking.browser_event(_REPORT))
        value = event["exception"]["values"][0]
        self.assertEqual(value["type"], "BrowserError")
        self.assertIn("TypeError: no se pudo leer paciente", value["value"])
        top = value["stacktrace"]["frames"][-1]
        self.assertEqual(top["filename"], "https://pinamar.example/static/admin-app.js")
        self.assertEqual((top["function"], top["lineno"], top["colno"]), ("renderFila", 120, 7))
        self.assertEqual(event["transaction"], "/admin/pacientes/:id")

    def test_source_line_without_a_stack(self):
        event = error_tracking.browser_event({"message": "boom", "source": "/static/x.js?v=1",
                                              "line": "9", "col": "x"})
        frame = event["exception"]["values"][0]["stacktrace"]["frames"][0]
        self.assertEqual((frame["filename"], frame["lineno"]), ("/static/x.js", 9))
        self.assertNotIn("colno", frame)

    def test_garbage_is_an_event_not_an_exception(self):
        for report in (None, [], "texto", {"message": None, "stack": 12}):
            with self.subTest(report=report):
                self.assertEqual(error_tracking.browser_event(report)["level"], "error")


class TestCaptureBrowserError(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_SENTRY_DSN=None)

    def test_without_dsn_logs_a_scrubbed_line_and_sends_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(error_tracking.capture_browser_error(_REPORT))
        line = out.getvalue()
        self.assertIn("[client-error] /admin/pacientes/:id", line)
        for value in _PII:
            self.assertNotIn(value, line)

    def test_with_dsn_the_sdk_gets_the_scrubbed_event(self):
        _env.set_env(self, TURNOS_SENTRY_DSN="https://k@sentry.invalid/1")
        fake = mock.MagicMock()
        fake.capture_event.return_value = "evt"
        with mock.patch.dict(sys.modules, {"sentry_sdk": fake}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(error_tracking.capture_browser_error(_REPORT))
        dumped = json.dumps(fake.capture_event.call_args.args[0], ensure_ascii=False)
        for value in _PII:
            self.assertNotIn(value, dumped)


class TestClientErrorsRoute(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)
        client_errors_router._recent.clear()
        self.addCleanup(client_errors_router._recent.clear)

    def test_anonymous_cannot_report(self):
        with mock.patch.object(error_tracking, "capture_browser_error") as capture:
            response = self.client.post("/api/admin/client-errors", json=_REPORT)
        self.assertEqual(response.status_code, 401)
        capture.assert_not_called()

    def test_every_panel_role_reports(self):
        for role in ("admin", "admisionista", "medico", "reportes"):
            with self.subTest(role=role), \
                    mock.patch.object(error_tracking, "capture_browser_error") as capture:
                response = self.client.post("/api/admin/client-errors", json=_REPORT,
                                            cookies=_cookies(role, "u-" + role))
                self.assertEqual(response.status_code, 204)
                capture.assert_called_once_with(_REPORT)

    def test_a_burst_is_capped_per_operator(self):
        with mock.patch.object(error_tracking, "capture_browser_error") as capture:
            for _ in range(client_errors_router._PER_MINUTE + 5):
                self.assertEqual(self.client.post("/api/admin/client-errors", json=_REPORT,
                                                  cookies=_cookies()).status_code, 204)
        self.assertEqual(capture.call_count, client_errors_router._PER_MINUTE)

    def test_oversized_and_malformed_bodies(self):
        with mock.patch.object(error_tracking, "capture_browser_error") as capture:
            big = self.client.post("/api/admin/client-errors", cookies=_cookies(),
                                   content=b"x" * (client_errors_router._MAX_BODY + 1),
                                   headers={"Content-Type": "application/json"})
            bad = self.client.post("/api/admin/client-errors", cookies=_cookies(),
                                   content=b"{no json", headers={"Content-Type": "application/json"})
        self.assertEqual(big.status_code, 413)
        self.assertEqual(bad.status_code, 204)
        capture.assert_not_called()


class TestReporterLivesInTheStaffLayoutOnly(unittest.TestCase):
    def _read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_admin_base_reports_errors_and_rejections_without_the_query(self):
        html = self._read("templates", "admin", "base.html")
        self.assertIn("/api/admin/client-errors", html)
        self.assertIn('addEventListener("error"', html)
        self.assertIn('addEventListener("unhandledrejection"', html)
        self.assertIn("screen: location.pathname", html)
        self.assertNotIn("location.href", html)
        self.assertNotIn("location.search", html)

    def test_patient_site_does_not_report(self):
        self.assertNotIn("/api/admin/client-errors", self._read("templates", "index.html"))


if __name__ == "__main__":
    unittest.main()
