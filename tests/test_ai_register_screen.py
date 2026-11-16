"""Real ASGI routing/auth/exports with a synthetic, non-network dossier."""
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import _env
from fastapi.testclient import TestClient
from app.core import ai_register, config, db, i18n
from app.core import db_ai
from app.auth import admin_auth
from app import main as application


def payload():
    return {'características': ai_register.inventory(),
            'estado_actual': {'pending_notifications': 1},
            'modificaciones': [{'id': 7, 'function_id': 'referral', 'kind': 'model',
                               'actor': 'synthetic-admin', 'changed_at': datetime.now(timezone.utc),
                               'substantial': True, 'status': 'pending', 'details': {}}]}


class TestAIRegisterScreen(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {'TURNOS_ADMIN_PASS': 'test-register-pass',
                            'TURNOS_FAKE_AI': '1', 'TURNOS_SKIP_HSI': '1',
                            'TURNOS_ENV_FILE': '/dev/null', 'DATABASE_URL': ''})
        self.overrides = patch.object(config, '_db_overrides', return_value={})
        self.overrides.start()
        self.addCleanup(self.overrides.stop)
        self.store = patch.object(ai_register, 'dossier', side_effect=payload)
        self.store.start()
        self.addCleanup(self.store.stop)
        self.client = TestClient(application.app)
        self.cookies = {'st_admin': admin_auth._mint_token('synthetic-admin', 'admin')}

    def test_register_renders_table_engine_and_notification_form(self):
        response = self.client.get('/admin/ai-register', cookies=self.cookies)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('id="air-table"', response.text)
        self.assertIn('platform/admin-table.mjs', response.text)
        self.assertIn('data-change-id="7"', response.text)
        self.assertIn('href="/admin/ai-register" data-nav="ai-register"', response.text)
        self.assertNotIn('cumplimiento legal', response.text)

    def test_pending_change_is_marked_on_its_row_never_counted(self):
        # No counter above the table (the product owner, 2026-10-14; one screen header
        # 2026-11-08): the pending change says so on its own row.
        text = self.client.get('/admin/ai-register', cookies=self.cookies).text
        self.assertNotIn('data-air-pending', text)
        self.assertIn('data-change-id="7"', text)
        self.assertIn('Pendiente de notificación', text)

    def test_what_every_row_shares_is_said_once_and_changes_read_as_words(self):
        # The product owner, 02.11: what repeats on every row goes outside the brackets.
        # The shared model, risk and state sit once above the table; a cell
        # names only a difference. The printable dossier stays row by row.
        text = self.client.get('/admin/ai-register', cookies=self.cookies).text
        self.assertEqual(text.count('class="air-shared"'), 1)
        self.assertNotIn('<td data-label="Riesgo autoevaluado">alto</td>', text)
        self.assertIn('<td data-label="Riesgo autoevaluado">limitado</td>', text)
        self.assertNotIn('cloud_shared', text)
        # The change line: the function's name, what changed, the day — no keys.
        self.assertIn('Lectura de órdenes y prácticas solicitadas: cambio de modelo · synthetic-admin', text)
        self.assertNotIn('referral / model', text)
        printable = self.client.get('/admin/ai-register/print', cookies=self.cookies).text
        self.assertNotIn('class="air-shared"', printable)
        self.assertIn('<td data-label="Riesgo autoevaluado">alto</td>', printable)
        self.assertNotIn('cloud_shared', printable)

    def test_a_column_every_row_shares_is_not_drawn_and_notice_reads_as_text(self):
        # The demo: one model and one state for every function, so those
        # headers over blank cells repeat the line above the table. A fallback
        # model then reads under the function's name; «Aviso previo» is the
        # sentence the person reads, never the catalog key.
        from app.admin_panel import ai_register_screen
        rows = [{'model': 'm-1', 'provider': {'host': 'h'}, 'risk_level': 'alto',
                 'runtime_state': 'simulada', 'fallback_model': 'm-2' if i == 0 else None}
                for i in range(3)]
        self.assertEqual(ai_register_screen.shared_values(rows)['hide'],
                         ('model', 'risk_level', 'runtime_state'))
        rows[1]['risk_level'] = 'limitado'
        self.assertEqual(ai_register_screen.shared_values(rows)['hide'], ('model', 'runtime_state'))
        rows[2]['provider'] = {'host': 'otro'}
        self.assertEqual(ai_register_screen.shared_values(rows)['hide'], ('runtime_state',))
        text = self.client.get('/admin/ai-register', cookies=self.cookies).text
        self.assertNotIn('ai.notice.', text)
        self.assertNotIn('order upload', text)
        self.assertIn(ai_register.notice('ai.notice.order'), text)

    def test_change_line_says_the_day_and_who_in_words(self):
        from app.admin_panel import ai_register_screen
        lines = ai_register_screen.change_lines([{
            'function_id': 'case_chat', 'kind': 'functionality', 'actor': 'system:observed',
            'changed_at': datetime(2020, 11, 7, 10, 0, tzinfo=timezone.utc)}])
        self.assertEqual(lines[0]['function_name'], 'Preguntas sobre un caso sin turnos y síntesis para la mesa')
        self.assertEqual(lines[0]['kind_text'], 'función nueva o cambiada')
        self.assertEqual(lines[0]['actor_text'], 'detectado por el sistema')
        self.assertEqual(lines[0]['day'], '7 de noviembre de 2020')

    def test_every_register_route_is_admin_only(self):
        for url in ('/admin/ai-register', '/admin/ai-register/print',
                    '/api/admin/ai-register', '/api/admin/ai-register/export'):
            for role in ('medico', 'admisionista', 'reportes'):
                with self.subTest(url=url, role=role):
                    cookies = {'st_admin': admin_auth._mint_token('synthetic-staff', role)}
                    self.assertEqual(self.client.get(url, cookies=cookies).status_code, 403)
        self.assertEqual(self.client.post('/api/admin/ai-register/notify', json={}).status_code, 401)

    def test_export_has_decree_fields_and_print_opens_details(self):
        response = self.client.get('/api/admin/ai-register/export', cookies=self.cookies)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'características', 'estado_actual', 'modificaciones'})
        self.assertIn('attachment', response.headers['content-disposition'])
        printable = self.client.get('/admin/ai-register/print', cookies=self.cookies)
        self.assertEqual(printable.status_code, 200)
        self.assertIn('<details open>', printable.text)
        self.assertNotIn('id="air-download"', printable.text)

    def test_notification_records_the_authenticated_actor(self):
        with patch.object(db_ai, 'ai_register_notify', return_value=True) as notify:
            response = self.client.post('/api/admin/ai-register/notify', cookies=self.cookies,
                                        json={'id': 7, 'reference': 'EX-2026-1234', 'date': '2026-10-14'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(notify.call_args.args, (7, 'EX-2026-1234', '2026-10-14', 'synthetic-admin'))

    def test_unavailable_history_does_not_claim_zero_pending(self):
        self.store.stop()
        with patch.object(ai_register, 'dossier', side_effect=db.DbUnavailable):
            response = self.client.get('/admin/ai-register', cookies=self.cookies)
        self.assertEqual(response.status_code, 503)

