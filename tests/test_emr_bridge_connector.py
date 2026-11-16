"""Conformance checks for the public Core-side EMR bridge boundary.

These tests use a loopback HTTP server only.  They prove that Core sends a
normalized, idempotent envelope and never needs an HSI endpoint or credential
when the Enterprise connector is selected.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.connectors import EmrConflict, EmrRejected, ProviderUnavailable  # noqa: E402
from app.connectors.emr_bridge.adapter import EnterpriseBridgeConnector  # noqa: E402


class TestEnterpriseBridgeConnector(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calls = self.calls

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                calls.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                              "body": json.loads(self.rfile.read(length) or b"{}")})
                if self.path == "/v1/intents/conflict":
                    self.send_response(409)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"checkpoint changed","remote":{"id":"9"}}')
                    return
                if self.path == "/v1/intents/rejected":
                    self.send_response(422)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"slot unavailable","code":"slot_unavailable","retryable":false}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if self.path == "/v1/intents":
                    self.wfile.write(b'{"result":{"status":"confirmed","appointment_id":"7","patient_id":"9","scheduled_at":"2026-08-30T09:00:00-03:00"}}')
                else:
                    self.wfile.write(b'{"observations":{"schedules":[],"appointments":[],"patients":[]}}')

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 1)
        self.addCleanup(self.server.shutdown)

    def _connector(self):
        values = {"EMR_BRIDGE_URL": f"http://127.0.0.1:{self.server.server_port}",
                  "EMR_BRIDGE_TOKEN": "test-bridge-token"}
        patcher = mock.patch("app.connectors.emr_bridge.adapter.config.get",
                             side_effect=lambda key, default="": values.get(key, default))
        patcher.start()
        self.addCleanup(patcher.stop)
        return EnterpriseBridgeConnector()

    def test_intent_envelope_carries_only_normalized_payload_and_idempotency_key(self):
        result = self._connector().execute_intent(
            "create", {"solicitud_id": 123, "dni": "20111222", "nombre": "Ana",
                       "apellido": "Paz", "nacimiento": "1980-01-02", "sexo": "X",
                       "especialidad": "Cardiology",
                       "when": "2026-08-30T09:00:00-03:00"}, "core-key-1")
        self.assertEqual(result["appointment_id"], "7")
        self.assertEqual(self.calls[0]["path"], "/v1/intents")
        self.assertEqual(self.calls[0]["authorization"], "Bearer test-bridge-token")
        self.assertEqual(self.calls[0]["body"], {
            "operation": "create", "idempotency_key": "core-key-1",
            "payload": {"local_request_id": "123", "patient": {
                "national_id": "20111222", "first_name": "Ana", "last_name": "Paz",
                "birth_date": "1980-01-02", "gender": "X"},
                "specialty": "Cardiology", "scheduled_at": "2026-08-30T09:00:00-03:00",
                "professional_name": None, "overbook": False},
        })

    def test_remote_checkpoint_conflict_is_normalized_for_the_core_journal(self):
        connector = self._connector()
        with self.assertRaises(EmrConflict) as raised:
            connector._request("POST", "/v1/intents/conflict", {})
        self.assertEqual(raised.exception.remote, {"id": "9"})

    def test_deterministic_rejection_is_not_reported_as_a_transport_outage(self):
        connector = self._connector()
        with self.assertRaises(EmrRejected) as raised:
            connector._request("POST", "/v1/intents/rejected", {})
        self.assertEqual(raised.exception.code, "slot_unavailable")

    def test_raw_or_malformed_bridge_observations_fail_closed(self):
        connector = self._connector()
        with mock.patch.object(connector, "_request", return_value={
            "observations": {"schedules": [], "appointments": [{
                "id": "7", "patient": "9", "starts": "2026-08-30 09:00",
            }], "patients": []},
        }):
            with self.assertRaises(ProviderUnavailable):
                connector.pull_observations("2026-08-30", "2026-08-31", [])

    def test_missing_bridge_configuration_never_attempts_a_default_endpoint(self):
        with mock.patch("app.connectors.emr_bridge.adapter.config.get", return_value=""), \
             mock.patch("app.connectors.emr_bridge.adapter.urllib.request.urlopen") as urlopen:
            with self.assertRaises(ProviderUnavailable):
                EnterpriseBridgeConnector().execute_intent("create", {}, "key")
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
