"""test_knowledge_pack_split.py — safety net for moving conocimiento.json's
23 articles down to the pack level they actually belong to (phase 3b,
2026-08-06): packs/global/ (1, engine-agnostic), packs/ar/ (9, Argentina),
packs/ar-b/ (5, Buenos Aires province), packs/ar-b-pinamar/ (8, Pinamar-only).

`conocimiento.json` is now registered in `data_packs._COMPOSABLE`
(`("articles", "id")`) so `catalog.knowledge()` (via `catalog._base_items_raw`)
composes it across the active pack chain instead of reading ONE file by
whole-file override, the same way `catalog.specialties()` already does for
especialidades.json/clusters.json.

Two guarantees:

(a) TestPinamarChainComposedSetUnchanged — the Pinamar chain
    (global,ar,ar-b,ar-b-pinamar, the default) composes back to EXACTLY the
    same 23 articles it served before the split, byte-for-byte, with ONE
    deliberate, explicitly asserted exception: "guardia-o-turno" moved to
    packs/global/ and was rewritten into a country-neutral form (no
    Argentina-specific phone numbers, jurisdiction "global" instead of
    "nacion", no parent categoria). The golden snapshot
    (tests/golden/conocimiento/pre_split_pinamar_chain.json) is the exact
    pre-split file, taken via `git show` before this slice touched it. It
    lives under tests/golden/, not tests/cassettes/ — it's not a recorded
    network interaction and has no "connector"/"interactions" shape, so
    scripts/audit_cassettes.py (which globs tests/cassettes/<connector>/*.json
    expecting exactly that shape) must never see it.

(b) TestRagContextBothChains — `knowledge.rag_context()` finds the routing
    article for both a symptom query and an explicit "guardia" query, on
    BOTH the Pinamar chain and the demo's example-town chain
    (global,example-town) — proving the demo (which had ZERO wiki articles
    before this slice, since conocimiento.json only ever existed at
    packs/ar-b-pinamar/) now has real assistant knowledge.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_knowledge_pack_split.py"
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.patient_web import catalog       # noqa: E402
from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.patient_web import knowledge      # noqa: E402

_SNAPSHOT_PATH = os.path.join(ROOT, "tests", "golden", "conocimiento", "pre_split_pinamar_chain.json")
_MOVED_AND_REWRITTEN = "guardia-o-turno"
_PINAMAR_CHAIN = ("global", "ar", "ar-b", "ar-b-pinamar")


def _identidad(tipo, base, incluir_borrados=False, strict=False):
    """Same determinism patch as test_golden_pages.py/test_sitedata_dir.py:
    a real Postgres with admin-edited overlay rows would append DB-only ids
    at the end regardless of the base, so tests that want a pure pack-chain
    read (no DB) patch this out."""
    meta = {it.get(catalog.id_key(tipo)): {"_origen": "json", "_borrado": False} for it in base}
    return list(base), meta


def _clear_caches():
    catalog._JSON_CACHE.clear()
    catalog.invalidate()
    data_packs.invalidate()


def _use_pinamar_chain(test_case):
    try:
        data_packs.private_pack_dir("ar-b-pinamar")
    except data_packs.RequiredPackError:
        test_case.skipTest("external private Pinamar pack is not configured")
    _env.set_env(test_case, PACK_CHAIN=",".join(_PINAMAR_CHAIN))
    config._reset_cache_para_tests()
    _clear_caches()
    test_case.assertEqual(data_packs.active_chain(), _PINAMAR_CHAIN)


class _PackEnvMixin:
    """Resets PACK_CHAIN/TURNOS_WEBDATA_DIR and every cache around each
    test so tests don't leak chain state into each other or into the rest
    of the suite (unittest doesn't guarantee file/class ordering)."""

    def setUp(self):
        # Registered before the environment, so they run after it is put back.
        self.addCleanup(_clear_caches)
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, PACK_CHAIN=None, TURNOS_WEBDATA_DIR=None)
        config._reset_cache_para_tests()
        self._merge_patch = self._patch_merge()
        self._merge_patch.start()
        _clear_caches()

    def tearDown(self):
        self._merge_patch.stop()

    @staticmethod
    def _patch_merge():
        from unittest.mock import patch
        return patch.object(catalog, "merge_with_overlay", _identidad)


class TestPinamarChainComposedSetUnchanged(_PackEnvMixin, unittest.TestCase):
    def test_same_23_ids_and_byte_identical_content_except_the_one_rewrite(self):
        _use_pinamar_chain(self)
        with open(_SNAPSHOT_PATH, encoding="utf-8") as f:
            expected_by_id = {a["id"]: a for a in json.load(f)["articles"]}

        composed = catalog.knowledge()
        composed_by_id = {a["id"]: a for a in composed}

        self.assertEqual(set(composed_by_id), set(expected_by_id),
                          "el conjunto de ids del wiki compuesto cambió con la migración")
        self.assertEqual(len(composed), len(expected_by_id),
                          "no debería haber ids duplicados tras componer la chain")

        unchanged_ids = set(expected_by_id) - {_MOVED_AND_REWRITTEN}
        for aid in unchanged_ids:
            self.assertEqual(composed_by_id[aid], expected_by_id[aid],
                              f"el artículo {aid!r} cambió de contenido al moverlo de pack")

    def test_guardia_o_turno_moved_to_global_and_is_country_neutral(self):
        composed_by_id = {a["id"]: a for a in catalog.knowledge()}
        art = composed_by_id[_MOVED_AND_REWRITTEN]
        self.assertIn(catalog.jurisdiction_level(art["jurisdiction"]), ("l1",),
                      "guardia-o-turno debería vivir ahora en el nivel global (l1)")
        self.assertIsNone(art["parent_id"],
                           "ya no debería colgar de una categoría de nación")
        for numero_ar in ("911", "107", "argentina.gob.ar", "Argentina.gob.ar"):
            self.assertNotIn(numero_ar, art["content"],
                              f"el contenido global no debería mencionar {numero_ar!r}")


class TestRagContextBothChains(_PackEnvMixin, unittest.TestCase):
    def _rag_ids(self, query):
        out = knowledge.rag_context(query)
        by_title = {a["title"]: a["id"] for a in catalog.knowledge()}
        return [by_title.get(f["titulo"], f["titulo"]) for f in out["fuentes"]]

    def test_pinamar_chain_finds_the_routing_article(self):
        _use_pinamar_chain(self)
        for query in ("tengo tos y fiebre", "cuándo ir a la guardia"):
            ids = self._rag_ids(query)
            self.assertTrue(ids, f"sin resultados para {query!r} en la chain de Pinamar")
            self.assertTrue(
                any(i in ("guardia-o-turno", "guardia-pinamar") for i in ids),
                f"{query!r} no encontró el artículo de guardia/turno (obtuvo {ids!r})")

    def test_example_town_chain_finds_the_routing_article(self):
        _env.set_env(self, PACK_CHAIN="global,example-town")
        _clear_caches()
        self.assertEqual(data_packs.active_chain(), ("global", "example-town"))
        for query in ("tengo tos y fiebre", "cuándo ir a la guardia"):
            ids = self._rag_ids(query)
            self.assertTrue(ids, f"sin resultados para {query!r} en la chain de example-town")
            self.assertTrue(
                any(i in ("guardia-o-turno", "guardia-ojo-manso") for i in ids),
                f"{query!r} no encontró el artículo de guardia/turno (obtuvo {ids!r})")
            for i in ids:
                self.assertNotIn("pinamar", i,
                                  f"la chain de example-town no debería devolver contenido de Pinamar ({i!r})")


if __name__ == "__main__":
    unittest.main()
