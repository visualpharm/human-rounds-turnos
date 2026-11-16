"""test_avatar_thumb.py — the doctor page's own portrait thumbnail.

Proves: the thumbnail is cut at SIZE from the doctor's portrait, its URL is
named after the thumbnail bytes (identical source re-copy = same URL), a
stale hash still gets bytes (not a 404), malformed names are refused, and
the router serves the immutable-cache contract."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from PIL import Image  # noqa: E402

from app.patient_web import avatar_thumb  # noqa: E402


class TestAvatarThumb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "dr-x.webp")
        Image.new("RGB", (600, 800), (200, 30, 30)).save(self.path, "WEBP")
        avatar_thumb._cache.clear()
        loader = lambda mid: (  # noqa: E731
            (f"f|{os.stat(self.path).st_mtime_ns}", lambda: avatar_thumb._open(self.path))
            if mid == "dr-x" else None)
        p = patch.object(avatar_thumb, "_source", side_effect=loader)
        p.start()
        self.addCleanup(p.stop)

    def test_thumbnail_is_drawn_size_and_content_named(self):
        url = avatar_thumb.url("dr-x")
        self.assertRegex(url, r"^/doctor-thumb/dr-x\.[0-9a-f]{16}\.webp$")
        data, current = avatar_thumb.thumb_bytes(url[len("/doctor-thumb/"):-len(".webp")])
        self.assertTrue(current)
        from io import BytesIO
        im = Image.open(BytesIO(data))
        self.assertEqual(im.size, (avatar_thumb.SIZE, avatar_thumb.SIZE))
        self.assertLess(len(data), 20_000)

    def test_identical_recopy_keeps_the_url(self):
        first = avatar_thumb.url("dr-x")
        # a re-copy moves the mtime (the cache key) but not the bytes
        before = os.stat(self.path)
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
        self.assertNotEqual(os.stat(self.path).st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(avatar_thumb.url("dr-x"), first)

    def test_stale_hash_still_serves_bytes(self):
        r = avatar_thumb.thumb_bytes("dr-x.0000000000000000")
        self.assertIsNotNone(r)
        self.assertFalse(r[1])

    def test_unknown_or_malformed_names_are_refused(self):
        self.assertIsNone(avatar_thumb.url("nobody"))
        self.assertIsNone(avatar_thumb.thumb_bytes("../etc.0000000000000000"))
        self.assertIsNone(avatar_thumb.thumb_bytes("dr-x"))


class TestAvatarThumbRouter(unittest.TestCase):
    def setUp(self):
        from app import main as app_module
        from fastapi.testclient import TestClient
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)

    def test_malformed_name_is_terminal_404(self):
        r = self.client.get("/doctor-thumb/not-a-thumb.webp")
        self.assertEqual(r.status_code, 404)

    def test_current_thumb_is_immutable(self):
        with patch.object(avatar_thumb, "thumb_bytes", return_value=(b"webp", True)):
            r = self.client.get("/doctor-thumb/dr-x.0123456789abcdef.webp")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/webp")
        self.assertIn("immutable", r.headers["cache-control"])

    def test_stale_thumb_gets_short_cache(self):
        with patch.object(avatar_thumb, "thumb_bytes", return_value=(b"webp", False)):
            r = self.client.get("/doctor-thumb/dr-x.0123456789abcdef.webp")
        self.assertEqual(r.headers["cache-control"], "public, max-age=300")


if __name__ == "__main__":
    unittest.main()
