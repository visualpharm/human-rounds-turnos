"""Focused coverage for duplicate referral-analysis caching."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.ai import ai_referral  # noqa: E402
from app.ai import orden_analysis_cache as cache  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402


def _result(model="test-model", cost=0.42):
    return {
        "verdict": "parece_correcta", "resumen": "Orden legible.",
        "campos": {"especialidad": {"valor": "Cardiología", "coincide": True},
                   "practicas": {"valor": ["Electrocardiograma"]},
                   "dni": {"valor": None}, "paciente": {"valor": None}},
        "documento": {"es_orden": True, "tipo": None}, "pases": 1,
        "confianza": 0.9, "modelo": model, "costo_usd": cost,
    }


class TestOrderAnalysisCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root_patch = patch.object(cache, "_root", return_value=self.tmp.name)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_key_is_deterministic_and_context_bound(self):
        a = cache.key_for(b"same bytes", "image/png", "cardio", "contract-a")
        self.assertEqual(a, cache.key_for(b"same bytes", "image/png", "cardio", "contract-a"))
        self.assertNotEqual(a, cache.key_for(b"same bytes", "image/png", "eco", "contract-a"))
        self.assertNotEqual(a, cache.key_for(b"same bytes", "image/png", "cardio", "contract-b"))

    def test_private_cache_round_trip_detaches_result_and_zeroes_cost(self):
        key = cache.key_for(b"document", "image/png", "cardio", "contract")
        original = _result()
        self.assertTrue(cache.put(key, original))
        original["campos"]["practicas"]["valor"].append("Mutated")

        got = cache.get(key)
        self.assertEqual(got["campos"]["practicas"]["valor"], ["Electrocardiograma"])
        self.assertEqual(got["costo_usd"], 0.0)
        self.assertEqual(oct(os.stat(os.path.join(self.tmp.name, key + ".json")).st_mode & 0o777), "0o600")

    def test_expired_or_provider_failure_results_are_not_reused(self):
        key = cache.key_for(b"document", "image/png", "cardio", "contract")
        with patch.object(cache, "_ttl_seconds", return_value=60):
            self.assertTrue(cache.put(key, _result(), now=100))
            os.utime(os.path.join(self.tmp.name, key + ".json"), (100, 100))
            self.assertIsNone(cache.get(key, now=161))
        self.assertFalse(cache.put(key, _result(model="fake")))
        failed = _result()
        failed["verdict"] = "pendiente_ia"
        self.assertFalse(cache.put(key, failed))

    def test_duplicate_upload_reuses_analysis_but_gets_fresh_pending_storage(self):
        original_orders = booking_requests.REFERRALS_DIR
        booking_requests.REFERRALS_DIR = os.path.join(self.tmp.name, "orders")
        self.addCleanup(setattr, booking_requests, "REFERRALS_DIR", original_orders)
        calls = []

        def analyze(*_args):
            calls.append(True)
            return _result()

        with patch.object(ai_referral, "analysis_cache_contract", return_value="test-contract"), \
             patch.object(ai_referral, "analyze_referral_3pass", side_effect=analyze), \
             patch("app.ai.ai_health.available", return_value=True), \
             patch("app.ai.ai_costs.record"):
            first = booking_referrals.analyze_referral_bytes(b"same document", "image/png", "orden.png", "cardio", "Cardiología")
            second = booking_referrals.analyze_referral_bytes(b"same document", "image/png", "orden.png", "cardio", "Cardiología")

        self.assertEqual(len(calls), 1)
        self.assertNotEqual(first[0], second[0])
        self.assertNotEqual(first[1]["costo_usd"], second[1]["costo_usd"])
        self.assertTrue(os.path.exists(booking_referrals._load_pending(first[0])["orden_path"]))
        self.assertTrue(os.path.exists(booking_referrals._load_pending(second[0])["orden_path"]))
