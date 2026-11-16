import unittest
from unittest.mock import patch

from app.patient_web import catalog
from app.patient_web import knowledge


class KnowledgeTest(unittest.TestCase):
    def test_validacion_articulo_y_categoria(self):
        doc, err = catalog.validate("conocimiento", {"id":"x","type":"articulo","title":"X","jurisdiction":"pinamar","content":"texto","tags":["salud"],"status":"publicado","source":{"title":"F","url":"https://argentina.gob.ar/x","agency":"Muni","verified_at":"2026-07-29","review_every_days":30}}, True)
        self.assertIsNone(err); self.assertEqual(doc["id"], "x")
        _, err = catalog.validate("conocimiento", {"id":"x","type":"articulo","title":"X","jurisdiction":"pinamar","content":"x","tags":[],"status":"publicado"}, True)
        self.assertIn("fuente", err)

    @patch("app.patient_web.catalog.knowledge")
    def test_publicado_vencido_y_prioridad_local(self, items):
        items.return_value = [
            {"id":"n","type":"articulo","title":"Vacunas","jurisdiction":"nacion","status":"publicado","content":"vacunas calendario","tags":[],"source":{}},
            {"id":"p","type":"articulo","title":"Vacunas Pinamar","jurisdiction":"pinamar","status":"publicado","content":"vacunas calendario","tags":[],"source":{}},
            {"id":"old","type":"articulo","title":"Vacunas","jurisdiction":"pinamar","status":"publicado","content":"vacunas","tags":[],"valid_to":"2020-01-01"},
        ]
        found = knowledge.rag_context("VACUNAS", 4)
        self.assertEqual(found["fuentes"][0]["titulo"], "Vacunas Pinamar")
        self.assertNotIn("old", found["texto"])

    @patch("app.patient_web.catalog.knowledge")
    def test_stopwords_no_disparan_rag_y_fuentes_deben_ser_oficiales(self, items):
        items.return_value = [
            {"id":"x","type":"articulo","title":"La salud","jurisdiction":"interno",
             "status":"publicado","content":"de la que por una",
             "tags":[],"source":{"url":"https://example.com/no"}},
        ]
        self.assertEqual(knowledge.rag_context("de la que por una")["texto"], "")

    @patch("app.patient_web.catalog.knowledge")
    @patch("app.patient_web.knowledge._locality_terms", return_value={"pinamar"})
    def test_consulta_local_generica_no_arrastra_fuentes_irrelevantes(
            self, _localities, items):
        items.return_value = [
            {"id":"mental","type":"articulo","title":"Salud mental en Pinamar",
             "jurisdiction":"pinamar","status":"publicado",
             "content":"Atención municipal de salud mental","tags":["salud mental"],
             "source":{"url":"https://argentina.gob.ar/saludmental/"}},
            {"id":"vacunas","type":"articulo","title":"Vacunatorios de Pinamar",
             "jurisdiction":"pinamar","status":"publicado",
             "content":"Vacunación y calendario","tags":["vacunas"],
             "source":{"url":"https://argentina.gob.ar/vacunatorios/"}},
        ]
        self.assertEqual(
            knowledge.rag_context("¿Dónde atienden cardiología en Pinamar?"),
            {"texto": "", "fuentes": []})
        found = knowledge.rag_context("¿Dónde puedo vacunarme en Pinamar?")
        self.assertEqual([x["titulo"] for x in found["fuentes"]],
                         ["Vacunatorios de Pinamar"])

    @patch("app.patient_web.catalog.knowledge")
    def test_jurisdiccion_pedida_y_umbral_quitan_fuentes_laterales(self, items):
        items.return_value = [
            {"id":"local","type":"articulo","title":"Vacunas Pinamar",
             "jurisdiction":"pinamar","status":"publicado",
             "content":"vacunas","tags":["vacunas"],"source":{}},
            {"id":"prov","type":"articulo","title":"Vacunación bonaerense",
             "jurisdiction":"provincia","status":"publicado",
             "content":"vacunas de la provincia","tags":["vacunas"],"source":{}},
            {"id":"weak","type":"articulo","title":"Otros derechos",
             "jurisdiction":"nacion","status":"publicado",
             "content":"derechos generales","tags":[],"source":{}},
        ]
        found = knowledge.rag_context(
            "¿Dónde consulto las vacunas de la Provincia?")
        self.assertEqual([x["titulo"] for x in found["fuentes"]],
                         ["Vacunación bonaerense"])
