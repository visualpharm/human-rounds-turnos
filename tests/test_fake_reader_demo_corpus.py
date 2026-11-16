"""The offline reader answers the demo's synthetic orders by what they say
(TASK-52 / TASK-58, 26.10).

On the demo (no AI key) «Subí tu orden» ended in «No reconocimos qué pide la
orden» for every order: the fake reader returned studies only when a service
was already chosen, and then always the same ECG + echocardiogram — three
preparation blocks on an ultrasound. The synthetic orders of
packs/example-town/ordenes-demo carry what they ask for in golden.json
(`reading`); the fake reads that, with or without a service.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_fake_reader_demo_corpus.py
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.ai import ai_referral  # noqa: E402

GOLDEN = os.path.join(ROOT, "packs", "example-town", "ordenes-demo", "golden.json")


class TestFakeReaderDemoCorpus(unittest.TestCase):
    def test_upload_order_without_a_service_reads_what_the_order_asks(self):
        r = ai_referral._fake_result_3pass({"filename": "synthetic-clean-digital-ecografia-1.jpg"})
        self.assertEqual(r["campos"]["practicas"]["valor"], ["Ecografía abdominal completa"])
        self.assertEqual(r["campos"]["especialidad"]["valor"], "Ecografía")

    def test_inside_a_service_the_stub_answers_by_that_service(self):
        r = ai_referral._fake_result_3pass({"filename": "synthetic-clean-digital-ecografia-1.jpg",
                                             "servicio_id": "ecografia-general-anexo",
                                             "especialidad": "Ecografía general"})
        self.assertEqual(r["campos"]["practicas"]["valor"], ["Ecografía general"])
        self.assertNotIn("Electrocardiograma", r["campos"]["practicas"]["valor"])

    def test_a_path_in_the_name_is_the_same_file(self):
        r = ai_referral._fake_result_3pass({"filename": "C:\\fotos\\synthetic-derivacion-neurologia-1.jpg"})
        self.assertEqual(r["campos"]["especialidad"]["valor"], "Neurología")

    def test_every_demo_order_with_a_reading_is_recognised(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            cases = json.load(fh)["cases"]
        leidos = [c for c in cases if c.get("reading")]
        self.assertGreaterEqual(len(leidos), 14)
        for c in leidos:
            r = ai_referral._fake_result_3pass({"filename": c["path"]})
            self.assertEqual(r["campos"]["practicas"]["valor"], c["reading"]["practicas"], c["path"])

    def test_any_other_file_reads_nothing_alone_and_the_service_inside_one(self):
        sin = ai_referral._fake_result_3pass({"filename": "orden.jpg"})
        self.assertEqual(sin["campos"]["practicas"]["valor"], [])
        con = ai_referral._fake_result_3pass({"filename": "orden.jpg", "especialidad": "Cardiología"})
        self.assertEqual(con["campos"]["practicas"]["valor"], ["Cardiología"])


if __name__ == "__main__":
    unittest.main()
