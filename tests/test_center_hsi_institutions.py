"""scripts/fixes/center_hsi_institutions.py: the number lands in the overlay row
of a center the panel edited, the rest of its doc untouched; a pack-only center
is left to the pack unless asked; a dry run writes nothing."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.fixes import center_hsi_institutions as fix  # noqa: E402
from app.core import db_catalog  # noqa: E402

CENTERS = {
    "hospital": {"slug": "hospital", "name": "Hospital", "address": "Calle 1"},
    "caps-a": {"slug": "caps-a", "name": "CAPS A", "hsi_institution": "1068"},
    "caps-b": {"slug": "caps-b", "name": "CAPS B"},
}


class PlanTest(unittest.TestCase):
    def test_each_center_gets_its_action(self):
        numbers = {"hospital": "1086", "caps-a": "1068", "caps-b": "1073",
                   "no-such": "1", "hospital-x": "Hospital"}
        out = dict((s, a) for s, _, a in fix.plan(numbers, CENTERS, {"hospital"}))
        self.assertEqual(out, {"hospital": "escribir", "caps-a": "ya_esta", "caps-b": "falta_en_el_pack",
                               "no-such": "no_existe", "hospital-x": "numero_invalido"})

    def test_pack_items_writes_the_pack_only_center_too(self):
        out = fix.plan({"caps-b": " 10 73 "}, CENTERS, set(), pack_items=True)
        self.assertEqual(out, [("caps-b", "1073", "escribir")])


class RunTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump({"hospital": "1086", "caps-b": "1073"}, f)
        self.addCleanup(os.remove, self.path)
        row = {"id": "hospital", "doc": {"slug": "hospital", "name": "Hospital", "address": "Calle 1"},
               "borrado": False}
        items = [dict(c, _origen="json") for c in CENTERS.values()]
        patches = [
            mock.patch.object(fix.catalog, "admin_list", return_value=items),
            mock.patch.object(fix.catalog, "invalidate"),
            mock.patch.object(db_catalog, "catalog_list", return_value=[row]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.upsert = mock.patch.object(db_catalog, "catalog_upsert").start()
        self.addCleanup(mock.patch.stopall)

    def test_dry_run_writes_nothing(self):
        lines = []
        fix.run(self.path, emit=lines.append)
        self.upsert.assert_not_called()
        self.assertIn("simulacion", lines[-1])

    def test_apply_keeps_the_overlay_doc_and_adds_the_number(self):
        fix.run(self.path, apply=True, emit=lambda _l: None)
        self.upsert.assert_called_once()
        tipo, slug, doc = self.upsert.call_args.args
        self.assertEqual((tipo, slug), ("centros", "hospital"))
        self.assertEqual(doc["hsi_institution"], "1086")
        self.assertEqual(doc["address"], "Calle 1")
        self.assertFalse(self.upsert.call_args.kwargs["borrado"])


if __name__ == "__main__":
    unittest.main()
