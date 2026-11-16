"""Installation-owned venue and local-knowledge portability contracts."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import _patient_site_source
from app.patient_web import knowledge
from app.patient_web import site_page_centers


def _article(identifier, jurisdiction, title):
    return {
        "id": identifier,
        "type": "articulo",
        "title": title,
        "jurisdiction": jurisdiction,
        "status": "publicado",
        "content": "vacunacion informacion",
        "tags": ["vacunacion"],
        "source": {},
    }


class TestInstallationOwnedVenues(unittest.TestCase):
    def test_valid_https_destination_comes_from_site_identity(self):
        center = {"slug": "health-center"}
        with patch("app.core.site_identity.get", return_value={
                "Community Hall": "https://maps.example.invalid/hall"}):
            self.assertEqual(
                site_page_centers._venue_href("Community Hall", center),
                "https://maps.example.invalid/hall")

    def test_missing_or_unsafe_destination_falls_back_to_center(self):
        center = {"slug": "health-center"}
        for venues in ({}, {"Community Hall": "javascript:alert(1)"}, [], None):
            with self.subTest(venues=venues), \
                    patch("app.core.site_identity.get", return_value=venues):
                self.assertEqual(
                    site_page_centers._venue_href("Community Hall", center),
                    "/center/health-center")


class TestInstallationOwnedKnowledgeTerms(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._localities = os.path.join(self._tmp.name, "localities.json")
        with open(self._localities, "w", encoding="utf-8") as output:
            json.dump({"localities": ["North Hamlet", "River Bend"]}, output)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, query):
        articles = [
            _article("local", "l4", "Local vaccination"),
            _article("province", "l3", "Provincial vaccination"),
        ]
        with patch("app.patient_web.catalog.knowledge", return_value=articles), \
                patch("app.core.site_identity.get", return_value="Sample City"), \
                patch("app.core.data_packs.resolve_path", return_value=self._localities):
            return knowledge.rag_context(query, 8)

    def test_active_city_and_nearby_locality_prefer_local_articles(self):
        for query in ("vaccination Sample City", "vaccination River Bend"):
            with self.subTest(query=query):
                result = self._run(query)
                self.assertEqual(
                    [source["titulo"] for source in result["fuentes"]],
                    ["Local vaccination"])

    def test_another_installation_name_is_not_a_local_trigger(self):
        result = self._run("vaccination Pinamar")
        self.assertEqual(
            [source["titulo"] for source in result["fuentes"]],
            ["Local vaccination", "Provincial vaccination"])

    def test_engine_sources_do_not_embed_a_venue_or_locality_allowlist(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app/patient_web/knowledge.py"), encoding="utf-8") as source:
            knowledge_text = source.read()
        for text in (_patient_site_source.source(), knowledge_text):
            self.assertNotIn("_VENUES_MAPS", text)
            self.assertNotIn("Centro Cultural de Ostende", text)
        with open(os.path.join(root, "app/patient_web/knowledge.py"),
                  encoding="utf-8") as source:
            generic_block = source.read().split("_GENERIC =", 1)[1].split("}", 1)[0]
        for locality in ("pinamar", "ostende", "valeria", "carilo"):
            self.assertNotIn(locality, generic_block.lower())


if __name__ == "__main__":
    unittest.main()
