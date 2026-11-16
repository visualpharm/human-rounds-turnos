"""test_branding_router.py — native ASGI transport coverage for
`GET/POST /api/admin/branding` and `POST/DELETE /api/admin/branding/logo`
(docs/roadmap/native-migration-campaign-20260826.md, family W2c). Logo GET
coverage (`/logo.png`, `/logo`) is already in test_branding_logo_asgi.py;
test_branding.py covers the extracted domain functions (`state_payload`,
`apply_color`, `apply_logo_upload`, `apply_logo_delete`) and the role gate.
This file pins the FastAPI router's own status/body contract, using the
full composed app so the shared `AdminGateError` renderer
(`core.deps.render_admin_gate_error`) is registered — a gate failure on the
native path (`Depends(core.deps.require_admin(...))`) renders the same flat
`{"error": ...}` body the retired legacy transport used to write."""

import base64
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import admin_auth  # noqa: E402
from app.patient_web import branding  # noqa: E402

_FIXTURE_TOKEN = "fixture-admin"
_FIXTURE_IDENTITY = {"usuario": "admin", "rol": "admin"}


def _verify_token_fixture(token):
    if token == _FIXTURE_TOKEN:
        return dict(_FIXTURE_IDENTITY)
    return None


class TestBrandingRouter(unittest.TestCase):
    def setUp(self):
        self._verify_patch = patch.object(admin_auth, "_verify_token", side_effect=_verify_token_fixture)
        self._verify_patch.start()
        self.addCleanup(self._verify_patch.stop)
        self._security_patch = patch.object(
            admin_auth, "account_security",
            return_value={"tiene_2fa": True, "2fa_deadline": None, "2fa_vencido": False},
        )
        self._security_patch.start()
        self.addCleanup(self._security_patch.stop)

        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)
        self.cookies = {"st_admin": _FIXTURE_TOKEN}

    def test_unauthenticated_get_state_matches_dark_transport_flat_body(self):
        # Both transports write the SAME flat `{"error": ...}` shape:
        # the legacy path writes it directly via admin_auth._json(), the
        # native path raises core.deps.AdminGateError, rendered by the
        # exception handler app.py registers — byte-identical either way.
        r = self.client.get("/api/admin/branding")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.content, b'{"error": "no_autenticado"}')

    def test_get_state_returns_current_payload(self):
        with patch.object(branding, "current", return_value={"color": "#0e8b50", "logo": False}), \
             patch("app.core.theme.current", return_value="pinamar"):
            r = self.client.get("/api/admin/branding", cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")
        body = r.json()
        self.assertEqual(body["color"], "#0e8b50")
        self.assertEqual(body["derivados"]["accent"], "#0e8b50")

    def test_save_invalid_color_matches_domain_error(self):
        r = self.client.post("/api/admin/branding", json={"color": "not-a-color"},
                             cookies=self.cookies)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "color inválido"})

    def test_save_malformed_json_is_treated_as_empty_body(self):
        # Same leniency `branding.apply_color`'s caller uses on both
        # transports: a body that fails to parse is treated as `{}`, which
        # clears the color rather than erroring.
        with patch.object(branding, "save_color", return_value=None) as save:
            r = self.client.post(
                "/api/admin/branding", content=b"{not json",
                headers={"Content-Type": "application/json"}, cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "color": None, "derivados": None})
        save.assert_called_once_with(None)

    def test_upload_logo_rejects_empty_payload(self):
        r = self.client.post("/api/admin/branding/logo", json={"data_b64": ""},
                             cookies=self.cookies)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "archivo vacío"})

    def test_upload_logo_saves_a_valid_png(self):
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        b64 = base64.b64encode(png).decode("ascii")
        with patch.object(branding, "save_logo", return_value="png") as save:
            r = self.client.post("/api/admin/branding/logo", json={"data_b64": b64},
                                 cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "logo": True})
        save.assert_called_once_with(png)

    def test_delete_logo_matches_domain_function(self):
        with patch.object(branding, "remove_logo") as remove:
            r = self.client.delete("/api/admin/branding/logo", cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "logo": False})
        remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
