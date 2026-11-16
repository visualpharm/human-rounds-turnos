"""Synthetic, offline contract for optional external clinical history."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch, Mock

from app.connectors import EmrSyncConnector
from app.connectors.hsi.hsi_client import HsiClient
from app.core import managed_secrets
from app.core import db_clinical
from app.core import db_operators


class ClientTests(unittest.TestCase):
    def client(self):
        with patch('app.connectors.hsi.hsi_client.config.get', side_effect=lambda k, default=None: default):
            client = HsiClient('https://synthetic.invalid', 'synthetic', 'synthetic')
        client.token = 'synthetic'
        return client

    def test_off_makes_no_http_call(self):
        with patch('app.connectors.hsi.hsi_client.config.get', return_value='0'), patch('urllib.request.urlopen') as http:
            for method, args in [('external_clinical_history', (7,)), ('patient_location', ('99000001',)), ('federated_patient', ('7', '1'))]:
                self.assertEqual(getattr(self.client(), method)(*args), {'available': False, 'reason': 'flag_off', 'items': []})
            http.assert_not_called()

    def test_http_failures_and_logs_are_phi_free(self):
        for status, reason in [(403, 'forbidden'), (404, 'not_deployed'), (500, 'error')]:
            for method, args in [('external_clinical_history', (7,)), ('patient_location', ('99000001',)), ('federated_patient', ('7', '1'))]:
                with self.subTest(status=status, method=method), self.assertNoLogs(level='DEBUG'), patch('app.connectors.hsi.hsi_client.config.get', return_value='1'), patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('url', status, 'secret-name', {}, io.BytesIO(b'PHI-99000001'))), patch('sys.stderr', new_callable=io.StringIO) as logs:
                    result = getattr(self.client(), method)(*args)
                    self.assertEqual(result, {'available': False, 'reason': reason, 'items': []})
                    self.assertNotIn('99000001', logs.getvalue())
                    self.assertNotIn('secret-name', logs.getvalue())

    def test_timeout_and_malformed_response_degrade(self):
        with patch('app.connectors.hsi.hsi_client.config.get', return_value='1'):
            for value in [TimeoutError('PHI'), 'not json', [3, 'bad'], None]:
                with self.subTest(value=value):
                    client = self.client()
                    with patch.object(client, '_get', side_effect=value if isinstance(value, Exception) else None, return_value=value):
                        self.assertFalse(client.external_clinical_history(7)['available'])

    def test_raw_items_preserved_and_paths_encoded(self):
        with patch('app.connectors.hsi.hsi_client.config.get', return_value='1'):
            client = self.client()
            raw = {'unknownVendorField': {'value': 'synthetic'}}
            with patch.object(client, '_get', return_value=[raw]) as get:
                result = client.external_clinical_history(7)
                self.assertEqual(result, {'available': True, 'reason': '', 'items': [raw]})
                self.assertEqual(get.call_args.args[0], '/api/institutions/1/patient/7/outpatient/consultations/getExternalClinicalHistoryList')
            with patch.object(client, '_get', return_value=raw) as get:
                self.assertEqual(client.patient_location('99&x')['items'], [raw])
                self.assertEqual(get.call_args.args[1], {'identifier': '99&x'})
                client.federated_patient('a&b', 'c/d')
                self.assertEqual(get.call_args.args[1], {'subject': 'a&b', 'custodian': 'c/d'})
            with patch.object(client, '_get', return_value=[]):
                self.assertTrue(client.external_clinical_history(7)['available'])

    def test_success_uses_fake_http_get_and_bearer_only(self):
        response = Mock()
        response.read.return_value = b'[{"unknown":"synthetic"}]'
        response.headers.get_all.return_value = []
        with patch('app.connectors.hsi.hsi_client.config.get', return_value='1'), patch('urllib.request.urlopen', return_value=response) as http:
            self.assertEqual(self.client().external_clinical_history(7)['items'], [{'unknown': 'synthetic'}])
            request = http.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic')
            self.assertEqual(http.call_args.kwargs['timeout'], 30)

    def test_default_boundary_and_public_flag(self):
        class Unsupported(EmrSyncConnector):
            def push_appointment(self, *args, **kwargs):
                return {'skipped': True}
        self.assertEqual(Unsupported().external_clinical_history('99000001'), {'available': False, 'reason': 'not_supported', 'items': []})
        from app.connectors.emr_bridge.adapter import EnterpriseBridgeConnector
        self.assertIs(EnterpriseBridgeConnector.external_clinical_history, EmrSyncConnector.external_clinical_history)
        self.assertTrue(managed_secrets.is_runtime_key('TURNOS_HSI_EXTERNAL_HISTORY'))
        self.assertEqual(managed_secrets.classify_key('TURNOS_HSI_EXTERNAL_HISTORY'), 'public')

    def test_adapter_finds_only_existing_patient_and_degrades_search_failures(self):
        from app.connectors.hsi.adapter import HsiConnector
        with patch('app.connectors.hsi.hsi_client.config.get', return_value='1'), patch('app.connectors.hsi.hsi_client.HsiClient.find_patient', return_value=7) as find, patch('app.connectors.hsi.hsi_client.HsiClient.ensure_patient') as create, patch('app.connectors.hsi.hsi_client.HsiClient.external_clinical_history', return_value={'available': True, 'reason': '', 'items': []}) as history:
            self.assertTrue(HsiConnector().external_clinical_history('99000001')['available'])
            find.assert_called_once_with('99000001')
            history.assert_called_once_with(7)
            create.assert_not_called()
            find.side_effect = urllib.error.HTTPError('url', 403, 'PHI', {}, io.BytesIO())
            self.addCleanup(find.side_effect.close)
            self.assertEqual(HsiConnector().external_clinical_history('99000001')['reason'], 'forbidden')

    def test_find_is_read_only_and_rejects_ambiguous_identity(self):
        client = self.client()
        with patch.object(client, '_get', return_value=[7]) as get, patch.object(client, '_post') as post:
            self.assertEqual(client.find_patient('99000001'), 7)
            self.assertEqual(get.call_args.args[0], '/api/patient/minimalsearch')
            get.return_value = [7, 8]
            self.assertIsNone(client.find_patient('99000001'))
            get.return_value = []
            self.assertIsNone(client.find_patient('99000001'))
            post.assert_not_called()


class ServerTests(unittest.TestCase):
    def setUp(self):
        from app.modules.turnos import external_history
        self.domain = external_history
        self.domain._cache.clear()
        self.flag = patch.object(self.domain.config, 'get', return_value='1').start()
        from app.core import access_log
        self.own = patch.object(db_clinical, 'doctor_has_patient', return_value=True).start()
        patch.object(db_operators, 'operator_doctor_id', return_value='doctor-1').start()
        self.audit = patch.object(db_clinical, 'insert_clinical_access').start()
        self.connector = Mock()
        self.connector.external_clinical_history.return_value = {'available': True, 'reason': '', 'items': [{'unknown': 'synthetic'}]}
        self.adapter = patch.object(self.domain.connectors, 'adapter_for', return_value=self.connector).start()
        self.addCleanup(patch.stopall)

    def test_off_never_resolves_connector(self):
        self.flag.return_value = '0'
        self.assertEqual(self.domain.read({'usuario': 'doc', 'rol': 'medico'}, '99000001')['reason'], 'flag_off')
        self.adapter.assert_not_called()

    def test_roles_patient_scope_and_cache_is_per_user(self):
        for role in ['admisionista', 'operador', 'reportes']:
            self.assertEqual(self.domain.read({'usuario': 'desk', 'rol': role}, '99000001')['reason'], 'forbidden')
        self.audit.assert_not_called()
        # Not this doctor's patient: refused, and the refusal is logged.
        self.own.return_value = False
        self.assertEqual(self.domain.read({'usuario': 'doc', 'rol': 'medico'}, '99000001')['reason'], 'forbidden')
        self.own.assert_called_with('doctor-1', '99000001')
        self.connector.external_clinical_history.assert_not_called()
        self.assertEqual(self.audit.call_args.args[:3], ('doc', 'medico', 'historia_externa'))
        self.assertFalse(self.audit.call_args.kwargs['allowed'])
        self.own.return_value = True
        for user in ['doc', 'doc', 'admin']:
            self.domain.read({'usuario': user, 'rol': 'medico' if user == 'doc' else 'admin'}, '99000001')
        self.assertEqual(self.connector.external_clinical_history.call_count, 2)
        # Every read is logged, cached ones too; the log never holds the DNI.
        self.assertEqual(self.audit.call_count, 4)
        self.assertTrue(self.audit.call_args.kwargs['allowed'])
        self.assertNotIn('99000001', repr(self.audit.call_args_list))
        # The log can't be written: the read does not happen.
        self.audit.side_effect = RuntimeError('db down')
        self.domain._cache.clear()
        self.assertEqual(self.domain.read({'usuario': 'admin', 'rol': 'admin'}, '99000001')['reason'], 'error')
        self.assertEqual(self.connector.external_clinical_history.call_count, 2)

    def test_no_connector_errors_and_cache_expiry(self):
        identity = {'usuario': 'admin', 'rol': 'admin'}
        self.adapter.return_value = None
        self.assertEqual(self.domain.read(identity, '99000001')['reason'], 'not_supported')
        self.adapter.return_value = self.connector
        self.connector.external_clinical_history.side_effect = TimeoutError('PHI')
        self.assertEqual(self.domain.read(identity, '99000001')['reason'], 'error')
        self.connector.external_clinical_history.side_effect = None
        with patch.object(self.domain.time, 'monotonic', return_value=0):
            self.domain.read(identity, '99000001')
        with patch.object(self.domain.time, 'monotonic', return_value=301):
            self.domain.read(identity, '99000001')
        self.assertEqual(self.connector.external_clinical_history.call_count, 3)

    def test_cache_is_bounded_and_flag_off_discards_old_content(self):
        identity = {'usuario': 'admin', 'rol': 'admin'}
        with patch.object(self.domain, '_MAX_CACHE', 2):
            for dni in ['99000001', '99000002', '99000003']:
                self.domain.read(identity, dni)
            self.assertEqual(len(self.domain._cache), 2)
        self.flag.return_value = '0'
        self.domain.read(identity, '99000003')
        self.assertFalse(self.domain._cache)

    def test_bad_connector_envelope_degrades(self):
        for result in [None, {}, {'available': True, 'items': [], 'reason': 'unexpected'}, {'available': True, 'items': [3], 'reason': ''}]:
            self.connector.external_clinical_history.return_value = result
            self.assertEqual(self.domain.read({'usuario': 'admin', 'rol': 'admin'}, '99000001')['reason'], 'error')

    def test_ai_payload_ignores_external_history(self):
        from app.ai import chart_summary
        marker = 'EXTERNAL-PHI-MUST-STAY-HOME'
        payload = chart_summary._datos_prompt(
            [{'servicio': 'synthetic', 'external_history': [marker]}],
            [{'resumen': 'synthetic', 'external_history': [marker]}],
            [{'texto': 'local'}, {'sistema': 'hsi_external', 'texto': marker}], [])
        self.assertNotIn(marker, payload)
        self.assertNotIn('external_history', payload)
        self.assertIn('local', payload)

    def test_native_endpoint_get_only_and_module_and_role_gates(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.modules.turnos import external_history_router
        app = FastAPI()
        app.include_router(external_history_router.router)
        dependencies = external_history_router.patient_external_history.__defaults__
        role_gate = dependencies[0].dependency
        module_gate = external_history_router.router.dependencies[0].dependency
        app.dependency_overrides[role_gate] = lambda: {'usuario': 'admin', 'rol': 'admin'}
        app.dependency_overrides[module_gate] = lambda: None
        with TestClient(app) as client:
            response = client.get('/api/admin/patient-external-history?dni=99000001')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(client.post('/api/admin/patient-external-history?dni=99000001').status_code, 405)
            self.assertEqual(client.get('/api/admin/patient-external-history?dni=bad').status_code, 400)
            app.dependency_overrides[role_gate] = lambda: {'usuario': 'desk', 'rol': 'admisionista'}
            self.assertEqual(client.get('/api/admin/patient-external-history?dni=99000001').status_code, 403)
            from fastapi import HTTPException
            def disabled():
                raise HTTPException(404)
            app.dependency_overrides[module_gate] = disabled
            self.assertEqual(client.get('/api/admin/patient-external-history?dni=99000001').status_code, 404)
