"""Phase 6b: the otp_transport connectors (waha, meta) — these wrap
otp_send.py IN PLACE (one file, not split per-connector — see
connectors/waha/adapter.py's docstring). Tier 2 batch B moved it as a whole
file into notifications/otp_send.py, a domain-package relocation, not the
per-connector split this note argues against. Tests confirm otp_send.py's
module object stays a single object every importer shares, and that both
adapters degrade to False without configuration, same as today.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.notifications import otp_send  # noqa: E402


class TestOtpConnectorManifests(unittest.TestCase):
    def test_both_manifests_declare_otp_transport(self):
        from app import connectors
        waha = connectors.load_manifest("waha")
        meta = connectors.load_manifest("meta")
        self.assertEqual(waha["capability"], connectors.OTP_TRANSPORT)
        self.assertEqual(meta["capability"], connectors.OTP_TRANSPORT)

    def test_otp_send_module_object_is_unchanged_by_this_slice(self):
        # otp_send.py was NOT split per-connector — no shim, no
        # sys.modules replacement. Its own __file__ should live under
        # notifications/ (Tier 2 batch B), never under connectors/.
        self.assertNotIn("connectors", otp_send.__file__)
        self.assertIn("notifications", otp_send.__file__)


class TestWahaAdapter(unittest.TestCase):
    def test_send_degrades_to_false_without_configuration(self):
        from app.connectors.waha.adapter import WahaConnector
        conn = WahaConnector()
        self.assertFalse(conn.send("5492255111222", "hola"))

    def test_send_delegates_to_otp_send_and_is_monkeypatchable(self):
        from app.connectors.waha.adapter import WahaConnector
        original = otp_send._enviar_whatsapp
        try:
            otp_send._enviar_whatsapp = lambda dest, txt: True
            conn = WahaConnector()
            self.assertTrue(conn.send("5492255111222", "hola"))
        finally:
            otp_send._enviar_whatsapp = original


class TestMetaAdapter(unittest.TestCase):
    def test_send_degrades_to_false_without_configuration(self):
        from app.connectors.meta.adapter import MetaConnector
        conn = MetaConnector()
        self.assertFalse(conn.send("5492255111222", "hola"))

    def test_send_delegates_to_otp_send_and_is_monkeypatchable(self):
        from app.connectors.meta.adapter import MetaConnector
        original = otp_send._enviar_meta
        try:
            otp_send._enviar_meta = lambda dest, txt: True
            conn = MetaConnector()
            self.assertTrue(conn.send("5492255111222", "hola"))
        finally:
            otp_send._enviar_meta = original


if __name__ == "__main__":
    unittest.main()
