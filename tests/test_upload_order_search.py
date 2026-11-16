"""Búsqueda «subir orden» (estudio de usabilidad 2026-10-16): el resultado
«Subí tu orden» → /upload-order existe en toda instalación, aunque el pack no
declare ese enlace. Un pack que sí lo declara conserva su texto y sus palabras."""

import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import i18n  # noqa: E402
from app.patient_web import site_base, site_centers

_PACK_LINK = {"id": "tele", "nombre": "Telemedicina", "href": "/telemedicine", "palabras": ["telemedicina"]}


def _links_with(rows):
    with mock.patch.object(site_base, "_load", return_value={"enlaces": rows}), \
         mock.patch.object(site_base, "_with_overlay", side_effect=lambda _k, base: list(base)):
        return site_centers._links()


class UploadOrderLinkTest(unittest.TestCase):
    def test_builtin_row_is_added_when_the_pack_has_no_such_link(self):
        rows = _links_with([_PACK_LINK])
        built = [r for r in rows if r["href"] == "/upload-order"]
        self.assertEqual(len(built), 1)
        row = built[0]
        self.assertEqual(row["nombre"], "Subí tu orden")
        self.assertEqual(row["accion_href"], "/upload-order")
        for phrase in ("subir orden", "cargar orden", "orden médica", "pedido médico", "derivación"):
            self.assertIn(phrase, row["palabras"])

    def test_builtin_row_is_added_when_the_pack_has_no_links_file(self):
        self.assertEqual([r["href"] for r in _links_with([])], ["/upload-order"])

    def test_pack_link_to_upload_order_wins(self):
        mine = {"id": "orden", "nombre": "Mi orden", "href": "/upload-order", "palabras": ["solo esta"]}
        rows = _links_with([_PACK_LINK, mine])
        self.assertEqual([r["href"] for r in rows].count("/upload-order"), 1)
        self.assertEqual([r for r in rows if r["href"] == "/upload-order"][0]["nombre"], "Mi orden")

    def test_pack_link_with_a_query_string_also_counts(self):
        rows = _links_with([{"id": "o", "nombre": "O", "href": "/upload-order/?x=1"}])
        self.assertEqual(len(rows), 1)

    def test_texts_follow_the_active_language_with_a_spanish_fallback(self):
        with mock.patch.object(i18n, "active", return_value="en"):
            row = [r for r in _links_with([]) if r["href"] == "/upload-order"][0]
        self.assertEqual(row["nombre"], i18n.t_or("nav.upload_order", "Subí tu orden", "en"))
        self.assertTrue(row["descripcion"])
        self.assertTrue(row["accion_texto"])

    def test_untranslated_rows_for_the_sweeps_stay_pack_only(self):
        with mock.patch.object(site_base, "_load", return_value={"enlaces": [_PACK_LINK]}), \
             mock.patch.object(site_base, "_with_overlay", side_effect=lambda _k, base: list(base)):
            self.assertEqual(site_centers._links(localize=False), [_PACK_LINK])

    def test_the_real_matcher_finds_it_for_every_phrase_people_type(self):
        row = [r for r in _links_with([]) if r["href"] == "/upload-order"][0]
        script = (
            "import * as B from './static/platform/search.mjs';"
            "const row = JSON.parse(process.argv[1]);"
            "const campos = (e) => ({ principal: [e.nombre], resto: e.palabras || [], excluidas: [] });"
            "const out = {};"
            "for (const q of JSON.parse(process.argv[2])) out[q] = B.buscar([row], q, campos).length;"
            "console.log(JSON.stringify(out));")
        queries = ["subir orden", "cargar orden", "orden médica", "pedido médico", "derivación", "subir la orden", "referral"]
        done = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(row), json.dumps(queries)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        found = json.loads(done.stdout)
        for q in queries:
            self.assertGreaterEqual(found[q], 1, q)


if __name__ == "__main__":
    unittest.main()
