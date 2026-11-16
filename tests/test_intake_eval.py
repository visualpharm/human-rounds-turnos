"""The comparison must stay offline by default and bounded before HTTP."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from scripts import eval_intake_dialogue as evaluation


class TestSyntheticComparison(unittest.TestCase):
    def test_default_exports_fixtures_without_provider_calls(self):
        output = io.StringIO()
        with patch('sys.argv', ['eval_intake_dialogue']), contextlib.redirect_stdout(output), patch('app.ai.ai_client.call') as call:
            evaluation.main()
        call.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['network_calls'], 0)

    def test_budget_requires_explicit_positive_bounded_authorization(self):
        for amount in ('0', '-1', '5.01', 'NaN', 'Infinity'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                evaluation.Budget(amount, evaluation.MODELS)

    def test_budget_reserves_before_request_and_never_refunds_uncertain_attempt(self):
        model = 'google/gemini-4-argon'
        budget = evaluation.Budget('0.10', [model])
        body = {'model': model, 'max_tokens': 2048, 'messages': [{'role': 'user', 'content': 'Synthetic only.'}]}
        budget.reserve(body)
        self.assertEqual(budget.attempts, 1)
        self.assertFalse(body['provider']['allow_fallbacks'])
        self.assertEqual(body['provider']['max_price']['completion'], 10)
        with self.assertRaises(ValueError):
            budget.reserve(body)
        self.assertEqual(budget.attempts, 1)

    def test_input_and_output_limits_reject_before_reservation(self):
        model = 'google/gemini-4-argon'
        budget = evaluation.Budget('5', [model])
        for body in [
            {'model': model, 'max_tokens': 2048, 'messages': [{'content': 'x' * 24000}]},
            {'model': model, 'max_tokens': 4096, 'messages': []},
            {'model': model, 'max_tokens': 2048, 'messages': [{'content': [{'type': 'image_url'}]}]},
        ]:
            with self.assertRaises(ValueError):
                budget.reserve(body)
        self.assertEqual(budget.attempts, 0)

    def test_synthetic_harness_uses_real_turn_path_without_records(self):
        def fixture(body, **kwargs):
            return {'model': body['model'], 'usage': {'prompt_tokens': 0, 'completion_tokens': 0},
                    'choices': [{'finish_reason': 'stop', 'message': {'content': '¿Querés agregar o corregir algo más? [pregunta: additional]'}}]}
        with patch('app.ai.ai_client.call', side_effect=fixture):
            results = [evaluation.evaluate(next(iter(evaluation.MODELS)), case) for case in evaluation.CASES]
        self.assertTrue(all(r['checks']['one_question'] for r in results))
        self.assertTrue(all(r['checks']['no_provider_failure'] for r in results))
        urgent = next(r for r in results if r['case'] == 'urgent')
        self.assertEqual(urgent['provider_calls'], 0)
        self.assertTrue(urgent['checks']['urgent_closed'])
