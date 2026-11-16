"""Runtime inventory, legal parity and unregistered inference drift."""
import ast
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class TestAIRegister(unittest.TestCase):
    def test_each_function_has_an_individual_provisional_risk_file(self):
        from app.core import ai_register
        paths = ROOT / 'docs/compliance/risk'
        self.assertEqual({path.stem for path in paths.glob('*.md')},
                         {entry['id'] for entry in ai_register.FUNCTIONS})
        for entry in ai_register.FUNCTIONS:
            with self.subTest(function=entry['id']):
                text = (paths / (entry['id'] + '.md')).read_text()
                self.assertIn('**' + entry['risk_level'] + '**', text)
                self.assertIn(entry['notice']['key'], text)
                self.assertIn('municip', text.lower())

    def test_every_inference_call_site_is_registered(self):
        from app.core import ai_register
        found = ai_register.scan_call_sites(ROOT)
        self.assertTrue(found)
        self.assertEqual(set(found), set(ai_register.CALL_SITES))
        ids = {f['id'] for f in ai_register.FUNCTIONS}
        self.assertTrue(all(set(values) <= ids for values in ai_register.CALL_SITES.values()))

    def test_external_hsi_history_is_not_an_ai_data_flow(self):
        from app.core import ai_register
        from app.connectors.hsi.hsi_client import HsiClient
        from app.modules.turnos import external_history

        inventories = []
        with patch.object(external_history, 'read') as read, \
             patch.object(HsiClient, 'external_clinical_history') as history, \
             patch.object(HsiClient, 'patient_location') as location, \
             patch.object(HsiClient, 'federated_patient') as federation:
            for enabled in ('0', '1'):
                with patch('app.core.config.get', side_effect=lambda key, default=None:
                           enabled if key == 'TURNOS_HSI_EXTERNAL_HISTORY' else default):
                    inventories.append(ai_register.inventory())
            read.assert_not_called()
            history.assert_not_called()
            location.assert_not_called()
            federation.assert_not_called()
        self.assertEqual(inventories[0], inventories[1])
        data_flows = json.dumps([
            {'id': row['id'], 'data_categories': row['data_categories']}
            for row in inventories[1]], ensure_ascii=False).lower()
        for marker in ('hsi_external', 'external_history', 'external clinical history',
                       'historia clínica externa', 'historia en otras ciudades'):
            self.assertNotIn(marker, data_flows)
        self.assertFalse(any('external_history' in site
                             for site in ai_register.CALL_SITES))

    def test_runtime_config_and_unknown_deployment_dates_are_honest(self):
        from app.core import ai_register
        with patch('app.core.config.get', side_effect=lambda k, default=None:
                   {'TURNOS_AI_MODEL': 'tests/local-model',
                    'AI_INFERENCE_PROFILE': 'self_hosted_vllm',
                    'AI_INFERENCE_BASE_URL': 'http://127.0.0.1:8999/v1/chat/completions',
                    'TURNOS_AI_DISABLED': '1'}.get(k, default)):
            rows = ai_register.inventory()
        internal = [row for row in rows if row['id'] not in ('mcp_assistant','voice_transcription')]
        self.assertTrue(all(r['model'] == 'tests/local-model' for r in internal))
        self.assertTrue(all(r['provider']['profile'] == 'self_hosted_vllm' for r in internal))
        self.assertTrue(all(not r['enabled'] for r in internal))
        voice = next(r for r in rows if r['id'] == 'voice_transcription')
        self.assertEqual(voice['provider']['profile'], 'fal_queue')
        self.assertFalse(voice['enabled'])
        mcp = next(r for r in rows if r['id'] == 'mcp_assistant')
        self.assertIsNone(mcp['model'])
        self.assertIsNone(mcp['provider']['profile'])
        self.assertEqual(mcp['provider']['product_readers']['profile'], 'self_hosted_vllm')
        self.assertEqual(mcp['provider']['product_readers']['model'], 'tests/local-model')
        self.assertTrue(all(r['first_deployed_date'] is None for r in rows))
        self.assertTrue(all(r['risk_level'] in ('alto', 'limitado') for r in rows))

    def test_both_legal_documents_publish_exact_inventory_even_after_overlay(self):
        from app.core import ai_register
        from app.patient_web import legal
        for doc in legal.DOCS:
            with self.subTest(doc=doc), patch('app.core.db_catalog.legal_get', return_value={
                    'body': '## 5. Inteligencia artificial\n\nSólo usamos un chat.\n\n## 6. Conservación\n\nTexto institucional conservado.'}):
                html = legal.render('{{legal.' + doc + '}}', 'es')
                self.assertNotIn('Sólo usamos un chat.', html)
                self.assertIn('Texto institucional conservado.', html)
                for entry in ai_register.FUNCTIONS:
                    self.assertIn('data-ai-function="' + entry['id'] + '"', html)

    def test_client_never_calls_provider_when_disabled(self):
        from app.ai import ai_client
        with patch('app.core.config.get', side_effect=lambda k, default=None:
                   '1' if k == 'TURNOS_AI_DISABLED' else default), \
             patch.object(ai_client, '_post') as post:
            self.assertFalse(ai_client.available())
            with self.assertRaises(ValueError):
                ai_client.call({'model': 'unused'})
        post.assert_not_called()
