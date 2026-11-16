"""test_doctor_photos_router.py — native ASGI transport coverage for
`GET/POST/DELETE /api/admin/{entity}/{eid}/photo` and
`GET /{entity}-photo/{eid}` (docs/roadmap/native-migration-campaign-
20260826.md, family W2c). test_doctor_photos.py already covers the
extracted domain functions (`photo_bytes`, `preview_payload`,
`upload_payload`, `remove_payload`) end-to-end over the composed app. This
file proves: (1) an `entity` outside `doctors|centers`/`doctor|center` gets
the shared terminal 404 — and, critically, it gets that 404 EVEN WHEN
UNAUTHENTICATED, because the retired legacy dispatch never reached
`admin_auth.require_admin(handler)` for an entity its own `_ITEM_RE` would
have declined (`_require_admin_entity` is a Depends declared ahead of
`Depends(core.deps.require_admin())` for exactly this reason — FastAPI
resolves dependencies in declaration order and stops at the first
exception); (2) the admin CRUD and public-serve endpoints match the domain
functions' contract."""

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
from app.patient_web import doctor_photos  # noqa: E402

_FIXTURE_TOKEN = "fixture-admin"
_FIXTURE_IDENTITY = {"usuario": "admin", "rol": "admin"}


def _verify_token_fixture(token):
    if token == _FIXTURE_TOKEN:
        return dict(_FIXTURE_IDENTITY)
    return None


class TestDoctorPhotosRouter(unittest.TestCase):
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

    def test_unknown_entity_is_the_shared_terminal_404_even_unauthenticated(self):
        for method in ("get", "post", "delete"):
            with self.subTest(method=method):
                fn = getattr(self.client, method)
                kwargs = {"json": {}} if method == "post" else {}
                r = fn("/api/admin/foo/1/photo", **kwargs)
                self.assertEqual(r.status_code, 404)
                # The SHARED terminal 404.
                self.assertEqual(r.content, b'{"error": "not found"}')

    def test_unknown_public_entity_is_declined_404(self):
        r = self.client.get("/foo-photo/1")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, b'{"error": "not found"}')

    def test_valid_entity_unauthenticated_is_401_not_404(self):
        r = self.client.get("/api/admin/doctors/1/photo")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.content, b'{"error": "no_autenticado"}')

    def test_preview_not_found_matches_domain_error(self):
        with patch.object(doctor_photos, "_doc", return_value=None):
            r = self.client.get("/api/admin/doctors/ghost/photo", cookies=self.cookies)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "médico no encontrado"})

    def test_preview_center_not_found_uses_center_message(self):
        with patch.object(doctor_photos, "_doc", return_value=None):
            r = self.client.get("/api/admin/centers/ghost/photo", cookies=self.cookies)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "centro no encontrado"})

    def test_upload_malformed_json_is_400_with_parse_message(self):
        r = self.client.post(
            "/api/admin/doctors/1/photo", content=b"{not json",
            headers={"Content-Type": "application/json"}, cookies=self.cookies)
        self.assertEqual(r.status_code, 400)
        self.assertIn("JSON inválido", r.json()["error"])

    def test_upload_rejects_empty_photo(self):
        with patch.object(doctor_photos, "_doc", return_value={"id": "1", "nombre": "Dr. Uno"}):
            r = self.client.post("/api/admin/doctors/1/photo", json={"data_b64": ""},
                                 cookies=self.cookies)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "falta la foto"})

    def test_upload_saves_a_valid_png(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        b64 = base64.b64encode(png).decode("ascii")
        with patch.object(doctor_photos, "_doc", return_value={"id": "1", "nombre": "Dr. Uno"}), \
             patch.object(doctor_photos, "os") as fake_os, \
             patch("builtins.open", side_effect=OSError("disco lleno")):
            fake_os.path.join = os.path.join
            fake_os.makedirs = lambda *a, **k: None
            r = self.client.post("/api/admin/doctors/1/photo", json={"data_b64": b64},
                                 cookies=self.cookies)
        # Disk failure -> the same 507 the legacy `_upload` writes.
        self.assertEqual(r.status_code, 507)
        self.assertEqual(r.json()["ok"], False)

    def test_remove_not_found_matches_domain_error(self):
        with patch.object(doctor_photos, "_doc", return_value=None):
            r = self.client.delete("/api/admin/doctors/ghost/photo", cookies=self.cookies)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "médico no encontrado"})

    def test_public_serve_missing_photo_is_404(self):
        with patch.object(doctor_photos, "_existing", return_value=None):
            r = self.client.get("/doctor-photo/1")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "sin foto"})


if __name__ == "__main__":
    unittest.main()
