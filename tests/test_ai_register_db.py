"""Atomic audit persistence and notification evidence on a disposable DB."""
import unittest
from unittest.mock import patch

import _dbtest
from app.core import ai_register, config, db
from app.core import db_catalog
from app.core import db_chat
from app.core import db_ai
import _env  # noqa: E402

_database = _dbtest.PerModuleDb('sana_turnos_test_ai_register')


def setUpModule():
    _database.set_up()


def tearDownModule():
    _database.tear_down()


class TestAIRegisterDatabase(unittest.TestCase):
    def setUp(self):
        if not _database.have_db:
            self.skipTest(_database.skip_reason)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE ai_register_changes, ai_prompts, ai_prompts_history, catalog')
        config._reset_cache_para_tests()

    def test_settings_change_and_audit_commit_together_without_values(self):
        with ai_register.actor({'usuario': 'synthetic-admin'}):
            config.guardar({'TURNOS_AI_MODEL': 'synthetic/model'})
        rows = db_ai.ai_register_list_changes()
        rows = [r for r in rows if r['kind'] != 'snapshot']
        self.assertEqual(len(rows), len(ai_register.FUNCTIONS) - 2)
        self.assertTrue(all(row['status'] == 'pending' for row in rows))
        self.assertTrue(all(row['actor'] == 'synthetic-admin' for row in rows))
        self.assertNotIn('synthetic/model', str(rows))

    def test_failed_audit_rolls_back_settings_write(self):
        with patch.object(db_ai, 'ai_register_append', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                config.guardar({'TURNOS_AI_MODEL': 'synthetic/model'})
        self.assertIsNone(db_catalog.catalog_get('config', 'settings'))

    def test_settings_snapshot_prevents_second_observed_change(self):
        ai_register.observe_inventory(ai_register.inventory())
        with ai_register.actor({'usuario':'synthetic-admin'}):
            config.guardar({'TURNOS_AI_MODEL':'synthetic/new-model','TURNOS_AI_DISABLED':'1'})
        before = [c for c in db_ai.ai_register_list_changes() if c['kind'] != 'snapshot']
        ai_register.dossier()
        ai_register.observe_inventory(ai_register.inventory())
        after = [c for c in db_ai.ai_register_list_changes() if c['kind'] != 'snapshot']
        self.assertEqual(before,after)
        self.assertTrue(all(c['actor']=='synthetic-admin' for c in after))

    def test_failed_snapshot_rolls_back_the_settings_and_event(self):
        with patch.object(ai_register,'observe_inventory',side_effect=RuntimeError('snapshot failure')):
            with self.assertRaises(RuntimeError):
                config.guardar({'TURNOS_AI_MODEL':'synthetic/new-model'})
        self.assertIsNone(db_catalog.catalog_get('config','settings'))
        self.assertEqual(db_ai.ai_register_list_changes(),[])

    def test_disclosure_and_proposals_never_enter_llm_history(self):
        db_chat.chat_save_message('whatsapp','synthetic',{'role':'system','content':'AI disclosure delivered v1'})
        for _ in range(205):
            db_chat.chat_save_message('whatsapp','synthetic',{'role':'user','content':'Synthetic text'})
        db_chat.chat_save_message('whatsapp','synthetic',{'role':'booking_proposal','args':{'service_id':'synthetic'}})
        db_chat.chat_save_message('whatsapp','synthetic',{'role':'health_profile_proposal','datos':{'alergias':['Synthetic']}})
        self.assertTrue(db_chat.chat_disclosure_sent('whatsapp','synthetic'))
        history = db_chat.chat_history('whatsapp','synthetic',300)
        multi = db_chat.chat_history_multi([('whatsapp','synthetic')],300)
        self.assertEqual(len(history),205)
        self.assertTrue(all(m['role']=='user' for m in history))
        self.assertTrue(all(m['contenido']['role']=='user' for m in multi))

    def test_prompt_changes_store_only_hash_and_length_in_same_transaction(self):
        text = 'Synthetic instruction; not patient data'
        db_ai.prompt_save('orden_analisis', text, None, True, usuario='synthetic-admin')
        row = db_ai.ai_register_list_changes()[0]
        self.assertEqual(row['kind'], 'prompt')
        self.assertEqual(row['details']['after'], ai_register.fingerprint(text))
        self.assertNotIn(text, str(row))
        self.assertTrue(db_ai.ai_register_notify(row['id'], 'EX-2026-1234', '2026-10-14', 'synthetic-admin'))
        notified = db_ai.ai_register_list_changes()[0]
        self.assertEqual(notified['status'], 'notified')
        self.assertEqual(notified['notification_reference'], 'EX-2026-1234')
        self.assertFalse(db_ai.ai_register_notify(row['id'], 'EX-2026-1234', '2026-10-14', 'synthetic-admin'))

    def test_observation_baseline_then_data_scope_change_is_idempotent(self):
        rows = ai_register.inventory()
        ai_register.observe_inventory(rows)
        ai_register.observe_inventory(rows)
        self.assertEqual(len(db_ai.ai_register_list_changes()), len(rows))
        rows[0]['data_categories'].append('synthetic new category')
        ai_register.observe_inventory(rows)
        changes = [c for c in db_ai.ai_register_list_changes() if c['kind'] == 'data_scope']
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['status'], 'pending')

    def test_new_function_after_baseline_requires_notification(self):
        rows = ai_register.inventory()
        ai_register.observe_inventory(rows)
        new_row = dict(rows[0], id='synthetic-new-function')
        ai_register.observe_inventory(rows + [new_row])
        events = [c for c in db_ai.ai_register_list_changes() if c['function_id'] == new_row['id']]
        self.assertTrue(any(c['substantial'] and c['status'] == 'pending' for c in events))

    def test_asgi_register_export_notification_and_system_row_use_real_ledger(self):
        # Browser-free E2E: real manifest routes and disposable PostgreSQL.
        import os
        from datetime import datetime
        from fastapi.testclient import TestClient
        from app.auth import admin_auth
        from app.admin_panel import system_status
        from app.core import tz
        from app import main as application

        with _env.patched_env({'TURNOS_ADMIN_USER': 'synthetic-admin',
                                    'TURNOS_ADMIN_PASS': 'synthetic-test-password',
                                    'TURNOS_ENV_FILE': '/dev/null', 'TURNOS_FAKE_AI': '1'}):
            client = TestClient(application.app)
            self.addCleanup(client.close)
            cookies = {'st_admin': admin_auth._mint_token('synthetic-admin', 'admin')}
            # A register GET never creates its own startup baseline.
            ai_register.observe_inventory(ai_register.inventory())
            page = client.get('/admin/ai-register', cookies=cookies)
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="air-table"', page.text)
            baseline = client.get('/api/admin/ai-register/export', cookies=cookies)
            self.assertEqual(baseline.status_code, 200)
            self.assertEqual(baseline.json()['estado_actual']['pending_notifications'], 0)
            with ai_register.actor({'usuario': 'synthetic-admin'}):
                config.guardar({'TURNOS_AI_MODEL': 'synthetic/new-model'})
            exported = client.get('/api/admin/ai-register/export', cookies=cookies).json()
            self.assertEqual(exported['características'][0]['model'], 'synthetic/new-model')
            self.assertGreater(exported['estado_actual']['pending_notifications'], 0)
            change = next(c for c in exported['modificaciones'] if c['status'] == 'pending')
            before_count = exported['estado_actual']['pending_notifications']
            today = datetime.now(tz.tzinfo()).date().isoformat()
            recorded = client.post('/api/admin/ai-register/notify', cookies=cookies,
                                   json={'id': change['id'], 'reference': 'EX-2026-1234', 'date': today})
            self.assertEqual(recorded.status_code, 200)
            after = client.get('/api/admin/ai-register/export', cookies=cookies).json()
            self.assertEqual(after['estado_actual']['pending_notifications'], before_count - 1)
            saved = next(c for c in after['modificaciones'] if c['id'] == change['id'])
            self.assertEqual(saved['notified_by'], 'synthetic-admin')
            self.assertEqual(saved['status'], 'notified')
            row = system_status._cluster_ai_register()
            self.assertEqual(len(row['items']), 1)
            self.assertIn(str(before_count - 1), row['items'][0]['valor'])
            printable = client.get('/admin/ai-register/print', cookies=cookies)
            self.assertEqual(printable.status_code, 200)
            self.assertIn('EX-2026-1234', printable.text)
            self.assertIn('<details open>', printable.text)
