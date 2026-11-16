"""test_avatar_sprite_router.py — native ASGI transport coverage for
`GET /avatar-sprite/{h}.webp` (docs/roadmap/native-migration-campaign-
20260826.md, family W2c). test_avatar_sprite.py already covers
`styles()`/`_build()`/`atlas_bytes()` at the domain level; this file
proves: (1) a hash shape `avatar_sprite._HASH_RE` would decline (not exactly
16 lowercase hex chars) gets the shared terminal 404; (2) a well-formed hash with no atlas on disk gets the
domain-level `{"error": "sprite no encontrado"}` 404 `atlas_bytes()` raises;
(3) a well-formed hash with a real atlas serves the bytes with the
immutable-cache contract."""

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

from app.patient_web import avatar_sprite  # noqa: E402


class TestAvatarSpriteRouter(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)

    def test_non_hex_hash_is_the_shared_terminal_404(self):
        r = self.client.get("/avatar-sprite/not-a-hash.webp")
        self.assertEqual(r.status_code, 404)
        # The SHARED terminal 404 bytes.
        self.assertEqual(r.content, b'{"error": "not found"}')

    def test_wrong_length_hash_is_the_shared_terminal_404(self):
        r = self.client.get("/avatar-sprite/abc123.webp")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, b'{"error": "not found"}')

    def test_valid_hash_with_no_atlas_is_domain_404(self):
        r = self.client.get("/avatar-sprite/0123456789abcdef.webp")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "sprite no encontrado"})
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_valid_hash_serves_bytes_with_immutable_cache(self):
        with patch.object(avatar_sprite, "atlas_bytes", return_value=b"webpbytes"):
            r = self.client.get("/avatar-sprite/0123456789abcdef.webp")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"webpbytes")
        self.assertEqual(r.headers["content-type"], "image/webp")
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")


if __name__ == "__main__":
    unittest.main()
