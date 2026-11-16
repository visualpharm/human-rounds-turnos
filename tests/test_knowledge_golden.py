"""Golden conversations against the wiki (patient_web/knowledge_golden.py).

Every pack that ships `conversaciones-doradas.json` promises that its
questions keep retrieving its articles. A wiki edit that breaks one of them
fails here, not in a patient's chat. Runs on the public chain
(global -> ar -> ar-b) with no DB overlay so the verdict depends only on the
repo's own content.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.patient_web import knowledge_golden  # noqa: E402
import _env  # noqa: E402

_CHAIN = ("global", "ar", "ar-b")


def _identidad(tipo, base, incluir_borrados=False, strict=False):
    meta = {it.get(catalog.id_key(tipo)): {"_origen": "json", "_borrado": False} for it in base}
    return list(base), meta


class TestGoldenConversations(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self._reset)  # registered first: runs once the env is back
        _env.set_env(self, PACK_CHAIN=",".join(_CHAIN), TURNOS_WEBDATA_DIR=None)
        self._patch = patch.object(catalog, "merge_with_overlay", _identidad)
        self._patch.start()
        self._reset()
        self.assertEqual(tuple(data_packs.active_chain()), _CHAIN)

    def tearDown(self):
        self._patch.stop()

    @staticmethod
    def _reset():
        config._reset_cache_para_tests()
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        data_packs.invalidate()

    def test_set_is_composed_across_the_chain(self):
        ids = [c["id"] for c in knowledge_golden.conversations()]
        self.assertGreaterEqual(len(ids), 10, ids)
        self.assertEqual(len(ids), len(set(ids)), "duplicate golden ids")
        self.assertIn("guardia-dolor-pecho", ids)   # global
        self.assertIn("vacunas-gratis", ids)        # ar
        self.assertIn("dengue-vacuna", ids)         # ar-b

    def test_every_golden_conversation_still_retrieves_its_article(self):
        out = knowledge_golden.run()
        failures = [r for r in out["results"] if not r["ok"]]
        self.assertEqual(failures, [], "\n".join(
            f"{r['id']}: found={r['found']} missing_articles={r['missing_articles']} "
            f"missing_facts={r['missing_facts']}" for r in failures))
        self.assertEqual(out["passed"], out["total"])

    def test_a_broken_wiki_is_reported_not_hidden(self):
        with patch.object(knowledge_golden.knowledge, "rag_context",
                          return_value={"texto": "", "fuentes": []}):
            out = knowledge_golden.run()
        self.assertEqual(out["passed"], 0)
        self.assertTrue(all(r["missing_articles"] for r in out["results"]))


if __name__ == "__main__":
    unittest.main()
