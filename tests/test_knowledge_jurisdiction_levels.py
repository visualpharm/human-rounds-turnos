"""test_knowledge_jurisdiction_levels.py — characterization + generalization
of conocimiento.json's `jurisdiction` field (phase 3, 2026-07-30).

Written BEFORE the refactor (superpowers:systematic-debugging /
verification-before-completion discipline: no existing parity test covered
this, per docs/architecture/data-packs.md's Phase 2c remaining list) to
pin down catalog.py's `_KNOWLEDGE_JURISDICTIONS`/knowledge.py's `_WEIGHT` +
`preferred`-keyword ranking EXACTLY as Pinamar's real content already
depends on — hardcoded Spanish jurisdiction names ("pinamar", "provincia",
"nacion", "interno") tied to weights (3, 2, 1, 0) and tie-break preference.

TestCharacterization* classes describe behavior that must NEVER change
(old Spanish values, still the only ones any real content uses). Once they
were green against the pre-refactor code, catalog.py/knowledge.py were
generalized to also accept the new pack-level labels "l2"/"l3"/"l4"
(country/province/town — data_packs.py's chain levels 2/3/4) as ALIASES
that rank identically, so a from-scratch deployment never has to spell a
town or province's Spanish name into its data model. TestGeneralization*
classes prove the new labels rank the same as their legacy equivalent and
mix freely with legacy-tagged content in the same corpus (a deployment can
migrate its jurisdiction values gradually, article by article, without a
flag day).

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_knowledge_jurisdiction_levels.py"
"""

import unittest
from unittest.mock import patch

from app.patient_web import catalog
from app.patient_web import knowledge


def _art(id_, jurisdiction, title="Vacunacion antigripal",
         content="Vacunacion antigripal informacion general sobre vacunas",
         tags=("vacunas",), source=None):
    return {"id": id_, "type": "articulo", "title": title,
            "jurisdiction": jurisdiction, "status": "publicado",
            "content": content, "tags": list(tags), "source": source or {}}


class TestCharacterizationWeightOrder(unittest.TestCase):
    """Four otherwise-identical articles differing only by jurisdiction: the
    weight ladder alone must break the tie, highest-weight first. This is
    the exact behavior Pinamar's real conocimiento.json content relies on
    today (knowledge._WEIGHT) — pinned BEFORE generalizing it."""

    @patch("app.patient_web.catalog.knowledge")
    def test_pinamar_beats_provincia_beats_nacion_beats_interno(self, mocked):
        mocked.return_value = [
            _art("pin", "pinamar"), _art("prov", "provincia"),
            _art("nac", "nacion"), _art("int", "interno"),
        ]
        out = knowledge.rag_context("vacunacion antigripal", 8)
        self.assertEqual([f["jurisdiccion"] for f in out["fuentes"]],
                          ["pinamar", "provincia", "nacion", "interno"])


class TestCharacterizationPreferredKeyword(unittest.TestCase):
    """A query naming the province/nation explicitly prefers that
    jurisdiction's article over an equally-scoring local one — the existing
    behavior test_knowledge.py's test_jurisdiccion_pedida_y_umbral_quitan_
    fuentes_laterales already covers for "provincia"; pinned again here
    alongside the "nacion" and "pinamar" triggers for completeness before
    the refactor touches this code path."""

    @patch("app.patient_web.catalog.knowledge")
    def test_provincia_keyword_prefers_the_provincial_article(self, mocked):
        mocked.return_value = [
            _art("local", "pinamar", title="Vacunas Pinamar", content="vacunas"),
            _art("prov", "provincia", title="Vacunacion bonaerense",
                 content="vacunas de la provincia"),
            _art("weak", "nacion", title="Otros derechos",
                 content="derechos generales", tags=()),
        ]
        out = knowledge.rag_context("¿Dónde consulto las vacunas de la Provincia?")
        self.assertEqual([f["titulo"] for f in out["fuentes"]],
                          ["Vacunacion bonaerense"])

    @patch("app.patient_web.catalog.knowledge")
    def test_nacion_keyword_prefers_the_national_article(self, mocked):
        mocked.return_value = [
            _art("local", "pinamar", title="Vacunas Pinamar", content="vacunas"),
            _art("nac", "nacion", title="Vacunacion nacional",
                 content="vacunas a nivel nacional"),
        ]
        out = knowledge.rag_context("¿Cuál es el calendario nacional de vacunas?")
        self.assertEqual([f["titulo"] for f in out["fuentes"]],
                          ["Vacunacion nacional"])

    @patch("app.patient_web.catalog.knowledge")
    @patch("app.patient_web.knowledge._locality_terms", return_value={"pinamar"})
    def test_pinamar_keyword_prefers_the_local_article(self, _localities, mocked):
        mocked.return_value = [
            _art("prov", "provincia", title="Vacunacion bonaerense",
                 content="vacunas de la provincia"),
            _art("local", "pinamar", title="Vacunas en Pinamar",
                 content="vacunas en el hospital de Pinamar"),
        ]
        out = knowledge.rag_context("¿Dónde me vacuno en Pinamar?")
        self.assertEqual([f["titulo"] for f in out["fuentes"]],
                          ["Vacunas en Pinamar"])


_VALID_SOURCE = {"title": "Fuente", "url": "https://argentina.gob.ar/x",
                  "agency": "Muni", "verified_at": "2026-07-29",
                  "review_every_days": 30}


class TestCharacterizationValidation(unittest.TestCase):
    """catalog.validate('conocimiento', ...) today only accepts the four
    legacy Spanish literals. Non-"interno" jurisdictions require a full
    official `source` (a pre-existing, unrelated rule — supplied here so
    the test isolates the jurisdiction check)."""

    def test_legacy_values_accepted(self):
        for j in ("pinamar", "provincia", "nacion"):
            _, err = catalog.validate("conocimiento", {
                "id": "x", "type": "articulo", "title": "X", "jurisdiction": j,
                "content": "texto", "tags": [], "status": "borrador",
                "source": _VALID_SOURCE}, True)
            self.assertIsNone(err, f"jurisdiction={j!r} debería validar")
        _, err = catalog.validate("conocimiento", {
            "id": "x", "type": "articulo", "title": "X", "jurisdiction": "interno",
            "content": "texto", "tags": [], "status": "borrador"}, True)
        self.assertIsNone(err, "jurisdiction='interno' debería validar sin fuente")


class TestGeneralizationPackLevelAliases(unittest.TestCase):
    """After the refactor: 'l2'/'l3'/'l4' (data_packs.py chain levels
    2/3/4 — country/province/town) are accepted, alias to the SAME weight
    as their legacy Spanish equivalent (nacion=l2, provincia=l3,
    pinamar=l4), and mix freely with legacy-tagged content."""

    def test_validate_accepts_the_new_generic_labels(self):
        for j in ("l2", "l3", "l4"):
            _, err = catalog.validate("conocimiento", {
                "id": "x", "type": "articulo", "title": "X", "jurisdiction": j,
                "content": "texto", "tags": [], "status": "borrador",
                "source": _VALID_SOURCE}, True)
            self.assertIsNone(err, f"jurisdiction={j!r} debería validar")

    @patch("app.patient_web.catalog.knowledge")
    def test_l4_ranks_exactly_like_pinamar(self, mocked):
        mocked.return_value = [
            _art("new", "l4"), _art("legacy", "pinamar"),
            _art("prov", "provincia"), _art("nac", "nacion"),
        ]
        out = knowledge.rag_context("vacunacion antigripal", 8)
        ranked_juris = [f["jurisdiccion"] for f in out["fuentes"]]
        # both l4 and its legacy alias 'pinamar' outrank provincia/nacion --
        # order between the two l4-weighted entries is a stable tie-break
        # (title, both identical here), not asserted; what matters is they
        # both land ahead of provincia/nacion.
        self.assertEqual(set(ranked_juris[:2]), {"l4", "pinamar"})
        self.assertEqual(ranked_juris[2:], ["provincia", "nacion"])

    @patch("app.patient_web.catalog.knowledge")
    def test_mixed_legacy_and_new_labels_in_the_same_corpus(self, mocked):
        mocked.return_value = [
            _art("old_local", "pinamar", title="Vacunas Pinamar", content="vacunas"),
            _art("new_prov", "l3", title="Vacunacion bonaerense",
                 content="vacunas de la provincia"),
            _art("old_weak", "nacion", title="Otros derechos",
                 content="derechos generales", tags=()),
        ]
        out = knowledge.rag_context("¿Dónde consulto las vacunas de la Provincia?")
        self.assertEqual([f["titulo"] for f in out["fuentes"]],
                          ["Vacunacion bonaerense"])

    @patch("app.patient_web.catalog.knowledge")
    def test_l2_keyword_preference_same_as_nacion(self, mocked):
        mocked.return_value = [
            _art("local", "pinamar", title="Vacunas Pinamar", content="vacunas"),
            _art("nac", "l2", title="Vacunacion nacional",
                 content="vacunas a nivel nacional"),
        ]
        out = knowledge.rag_context("¿Cuál es el calendario nacional de vacunas?")
        self.assertEqual([f["titulo"] for f in out["fuentes"]],
                          ["Vacunacion nacional"])


if __name__ == "__main__":
    unittest.main()
