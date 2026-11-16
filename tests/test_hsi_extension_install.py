"""test_hsi_extension_install.py — where the desk installs its HSI extension.

La contraparte técnica (2026-10-27): «nunca entendí cómo… llegábamos a esa pantalla». Every
screen that offers «Cargar en HSI» says, while the computer lacks the
extension, where to get it: the store page once the item is published, else
the ZIP from extensions/dist; the guide PDF in both cases. Only on the sites
the extension runs on.

Correr: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_hsi_extension_install.py
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import hsi_extension, site_identity  # noqa: E402

_ITEM = os.path.join(ROOT, "extensions", "hsi-humanizador", "store", "item.json")


def _item():
    with open(_ITEM, encoding="utf-8") as f:
        return json.load(f)


class InstallTestCase(unittest.TestCase):
    def _install(self, origen, visibility=None):
        real = hsi_extension._read_item

        def leido():
            manifest, item, shared = real()
            if visibility is not None:
                item = dict(item, visibility=visibility)
            return manifest, item, shared

        with mock.patch.object(site_identity, "base_url", return_value=origen), \
                mock.patch.object(hsi_extension, "_read_item", leido):
            return hsi_extension.install()

    def test_published_points_to_the_store_and_the_guide(self):
        tienda = _item()["store_url"]
        for origen in ("https://pinamar.humanrounds.org", "https://demo.humanrounds.org",
                       "https://salud.pinamar.gob.ar"):
            self.assertEqual(self._install(origen, "unlisted"),
                             {"tienda": tienda, "zip": None,
                              "guia": "/api/admin/hsi-extension/guide.pdf"}, origen)
        self.assertEqual(self._install("https://demo.humanrounds.org", "public")["tienda"], tienda)

    def test_not_published_points_to_the_zip_and_the_guide(self):
        for visibility in ("private", "testers", ""):
            self.assertEqual(self._install("https://demo.humanrounds.org", visibility),
                             {"tienda": None, "zip": "/api/admin/hsi-extension/extension.zip",
                              "guia": "/api/admin/hsi-extension/guide.pdf"}, visibility)

    def test_the_item_today_is_published(self):
        # the shipped item.json — the store page is what the desk gets now
        self.assertIn(_item().get("visibility"), ("unlisted", "public"))
        self.assertEqual(self._install("https://demo.humanrounds.org")["tienda"], _item()["store_url"])

    def test_a_site_the_extension_does_not_run_on_gets_nothing(self):
        self.assertIsNone(self._install("https://otra-ciudad.example", "unlisted"))
        self.assertIsNone(self._install("https://otra-ciudad.example", "private"))

    def test_a_file_missing_from_the_image_is_never_linked(self):
        with tempfile.TemporaryDirectory() as vacio, \
                mock.patch.object(hsi_extension, "_DIST_DIR", vacio):
            self.assertIsNone(hsi_extension.file_path("guide.pdf"))
            # published: the store alone still installs it
            self.assertEqual(self._install("https://demo.humanrounds.org", "unlisted"),
                             {"tienda": _item()["store_url"], "zip": None, "guia": None})
            # not published and no ZIP: nothing to offer
            self.assertIsNone(self._install("https://demo.humanrounds.org", "private"))

    def test_the_files_are_the_ones_in_dist(self):
        for name in hsi_extension.FILES:
            path = hsi_extension.file_path(name)
            self.assertTrue(path and os.path.getsize(path) > 1000, name)
        self.assertIsNone(hsi_extension.file_path("../manifest.json"))
        self.assertIsNone(hsi_extension.file_path("hsi-humanizador.sha256"))

    def test_store_url_keeps_drawing_the_button_on_the_extension_sites(self):
        # «Cargar en HSI» is drawn where the extension runs, published or not
        with mock.patch.object(site_identity, "base_url", return_value="https://demo.humanrounds.org"):
            self.assertEqual(hsi_extension.store_url(), _item()["store_url"])
        with mock.patch.object(site_identity, "base_url", return_value="https://otra-ciudad.example"):
            self.assertIsNone(hsi_extension.store_url())


if __name__ == "__main__":
    unittest.main()
