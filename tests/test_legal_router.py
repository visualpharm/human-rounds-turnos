"""test_legal_router.py — native ASGI transport coverage for
`GET /api/admin/legal` and `GET/PUT/DELETE /api/admin/legal/{doc}`
(docs/roadmap/native-migration-campaign-20260826.md, family W2c).
test_legal.py/test_legal_db.py already cover the extracted domain functions
(`list_payload`, `item_payload`, `save_payload`, `delete_payload`) against a
real/patched DB. This file pins the FastAPI router's own status/body
contract, including the ordering invariant carried over from the pre-split
legacy dispatch: an unknown `{doc}`
404s BEFORE a PUT's body is ever parsed, and the auth gate always runs
before that doc-validity check (this is a domain-level 404, not the
shared terminal "not found" shape — `{doc}` is a plain
single literal path segment, no wider-than-legacy FastAPI match exists
here)."""

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
from app.core import db  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.patient_web import legal  # noqa: E402

_FIXTURE_TOKEN = "fixture-admin"
_FIXTURE_IDENTITY = {"usuario": "admin", "rol": "admin"}


def _verify_token_fixture(token):
    if token == _FIXTURE_TOKEN:
        return dict(_FIXTURE_IDENTITY)
    return None


class TestLegalRouter(unittest.TestCase):
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

    def test_unauthenticated_requests_get_the_flat_gate_body(self):
        for method, path in (("GET", "/api/admin/legal"),
                             ("GET", "/api/admin/legal/terminos"),
                             ("PUT", "/api/admin/legal/terminos"),
                             ("DELETE", "/api/admin/legal/terminos")):
            with self.subTest(method=method, path=path):
                r = self.client.request(method, path, json={} if method == "PUT" else None)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.content, b'{"error": "no_autenticado"}')

    def test_unauthenticated_bad_doc_is_still_401_not_404(self):
        # Auth runs BEFORE the doc-validity check, same order as the
        # pre-split handle() — an unknown doc never leaks past the gate.
        r = self.client.get("/api/admin/legal/bogus")
        self.assertEqual(r.status_code, 401)

    def test_list_returns_both_documents(self):
        with patch.object(db_catalog, "legal_get", return_value=None):
            r = self.client.get("/api/admin/legal", cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        ids = [item["id"] for item in r.json()["items"]]
        self.assertEqual(sorted(ids), ["privacidad", "terminos"])

    def test_unknown_doc_404s_before_a_put_body_is_parsed(self):
        r = self.client.put("/api/admin/legal/bogus", content=b"{not json",
                            headers={"Content-Type": "application/json"},
                            cookies=self.cookies)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "no existe ese documento"})

    def test_put_rejects_empty_body(self):
        r = self.client.put("/api/admin/legal/terminos", json={"cuerpo": "   "},
                            cookies=self.cookies)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "el texto no puede quedar vacío"})

    def test_put_saves_and_returns_the_item(self):
        with patch.object(db_catalog, "legal_save") as save, \
             patch.object(db_catalog, "legal_get", return_value=None):
            r = self.client.put("/api/admin/legal/terminos", json={"cuerpo": "Nuevo texto legal."},
                                cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        save.assert_called_once_with("terminos", "Nuevo texto legal.", usuario="admin")

    def test_put_db_unavailable_is_503(self):
        with patch.object(db_catalog, "legal_save", side_effect=db.DbUnavailable("down")):
            r = self.client.put("/api/admin/legal/terminos", json={"cuerpo": "Nuevo texto legal."},
                                cookies=self.cookies)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"error": "base de datos no disponible"})

    def test_delete_delegates_to_the_domain_function(self):
        with patch.object(db_catalog, "legal_delete") as delete:
            r = self.client.delete("/api/admin/legal/privacidad", cookies=self.cookies)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        delete.assert_called_once_with("privacidad")


if __name__ == "__main__":
    unittest.main()
