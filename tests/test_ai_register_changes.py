"""Offline write-path regressions alongside the disposable-DB audit tests."""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.core import ai_register, db
from app.core import db_ai
from app.admin_panel import settings_admin
from app.admin_panel import system_status


class TestAuditWritePaths(unittest.TestCase):
    def test_system_register_is_one_row_with_pending_count_or_unavailable(self):
        for count in (0, 3):
            with self.subTest(count=count), patch.object(ai_register, 'dossier', return_value={
                    'estado_actual': {'pending_notifications': count}}):
                cluster = system_status._cluster_ai_register()
                self.assertEqual(cluster['clave'], 'ai-register')
                self.assertEqual(len(cluster['items']), 1)
                self.assertEqual(cluster['items'][0]['estado'], 'warn' if count else 'ok')
                self.assertTrue(cluster['items'][0]['ai_register'])
                self.assertIn(str(count), cluster['items'][0]['valor'])
        with patch.object(ai_register, 'dossier', side_effect=db.DbUnavailable):
            cluster = system_status._cluster_ai_register()
        self.assertEqual(len(cluster['items']), 1)
        self.assertEqual(cluster['items'][0]['estado'], 'warn')
        self.assertIn('no verificados', cluster['items'][0]['valor'])

    def test_boolean_kill_switch_setting_is_normalized_and_bad_values_are_rejected(self):
        for raw, expected in ((True, '1'), (False, '0')):
            with self.subTest(raw=raw), patch('app.core.config.get', return_value=None), \
                 patch('app.core.config.guardar') as saved, \
                 patch.object(settings_admin, '_api_keys_payload', return_value=[]):
                _, error = settings_admin._guardar_api_keys({'TURNOS_AI_DISABLED':raw}, [])
                self.assertIsNone(error)
                saved.assert_called_once_with({'TURNOS_AI_DISABLED':expected})
        with patch('app.core.config.guardar') as saved:
            _, error = settings_admin._guardar_api_keys({'TURNOS_AI_DISABLED':'maybe'}, [])
        self.assertTrue(error)
        saved.assert_not_called()

    def test_second_edited_prompt_save_archives_raw_database_column_names(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        stamp = datetime(2026, 10, 14, tzinfo=timezone.utc)
        cur.fetchone.side_effect = [
            {'text_en':'old synthetic text', 'edited':True},
            {'text_en':'old synthetic text', 'text_es':'texto sintético',
             'updated':stamp, 'updated_by':'synthetic-admin'},
            {'key':'orden_analisis','text_en':'new synthetic text','edited':True},
        ]
        with patch.object(db, '_connect', return_value=conn), patch.object(db_ai, 'ai_register_append') as append:
            db_ai.prompt_save('orden_analisis', 'new synthetic text', None, True, usuario='synthetic-admin')
        append.assert_called_once()
        event = append.call_args.args[0][0]
        self.assertEqual(event['actor'], 'synthetic-admin')
        self.assertEqual(event['details']['after'], ai_register.fingerprint('new synthetic text'))
        self.assertNotIn('new synthetic text', str(event))

    def test_doctor_switch_logs_minimal_scope_without_doctor_identifier(self):
        with ai_register.actor({'usuario':'synthetic-admin'}):
            events = ai_register.doctor_enabled_events('synthetic-doctor', False, True)
        self.assertEqual(len(events), 4)
        self.assertTrue(all(e['actor'] == 'synthetic-admin' and e['kind'] == 'enabled' for e in events))
        self.assertNotIn('synthetic-doctor', str(events))
        self.assertEqual(ai_register.doctor_enabled_events('synthetic-doctor', True, True), [])
