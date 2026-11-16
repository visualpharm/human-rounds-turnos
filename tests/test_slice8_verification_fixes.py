"""Offline incident proofs supplementing the real-Postgres acceptance gates."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.core import ai_register, db
from app.core import db_booking
from app.core import db_ai
from app.notifications import confirmations


class TestVerificationFixes(unittest.TestCase):
    def test_inventory_is_an_independent_extensible_list(self):
        with patch('app.core.config.get', side_effect=lambda key, default=None: default):
            rows = ai_register.inventory()
        extended = rows + [dict(rows[0], id='synthetic-new-function')]
        self.assertEqual(len(extended), len(ai_register.FUNCTIONS) + 1)
        rows[0]['data_categories'].append('synthetic')
        self.assertNotIn('synthetic', ai_register.FUNCTIONS[0]['data_categories'])

    def test_explicit_patient_no_uses_the_own_booking_operation(self):
        from app.modules.turnos import booking_patient
        from app.modules.chat import chat_tools
        model_cancel = Mock()
        with patch.object(db_booking, 'mark_confirmation') as record, \
             patch.object(confirmations, '_when', return_value=''), \
             patch.object(booking_patient, 'cancel_request', return_value=(200, {'ok': True})) as cancel, \
             patch.dict(chat_tools.TOOL_FNS, {'cancel_request': model_cancel}):
            text = confirmations.record({'id': 71, 'dni': '99000001'}, 'no', canal='email')
        record.assert_called_once_with(71, 'no', 'email')
        cancel.assert_called_once_with('99000001', 71)
        model_cancel.assert_not_called()
        self.assertIn('queda disponible', text)

    def test_notification_requires_a_human_actor_before_connecting(self):
        with patch.object(db, '_connect') as connect:
            for actor in (None, '', '   '):
                with self.subTest(actor=actor), self.assertRaisesRegex(ValueError, 'invalid_actor'):
                    db_ai.ai_register_notify(71, 'EX-2026-1234', '2026-10-14', actor)
        connect.assert_not_called()

    def test_patient_no_cancels_and_releases_the_future_slot(self):
        from app.modules.turnos import booking_requests, waitlist
        from app.modules.turnos import booking_patient
        row = {'id': 71, 'dni': '99000001', 'estado': 'aprobada',
               'servicio_id': 'synthetic', 'centro_slug': 'synthetic-center',
               'turno_dt': datetime(2099, 1, 1, tzinfo=timezone.utc)}
        def cancel(sid, actor, **_hsi_flags):
            row['estado'] = 'cancelada'
            return dict(row)
        with patch.object(db_booking, 'mark_confirmation'), \
             patch.object(confirmations, '_when', return_value=''), \
             patch.object(booking_requests.store, 'get_booking_request', return_value=row), \
             patch.object(booking_requests.store, 'cancel', side_effect=cancel) as writer, \
             patch.object(booking_patient, '_public_detail', side_effect=lambda item: item), \
             patch.object(waitlist, 'opt_out') as opt_out, \
             patch.object(waitlist, 'release_slot') as release:
            message = confirmations.record(row, 'no', canal='whatsapp')
        writer.assert_called_once()
        self.assertEqual(writer.call_args.args, (71,))
        self.assertEqual(writer.call_args.kwargs['actor'], 'paciente')
        self.assertEqual(row['estado'], 'cancelada')
        opt_out.assert_called_once_with(71)
        release.assert_called_once_with('synthetic', 'synthetic-center', row['turno_dt'])
        self.assertIn('queda disponible', message)
