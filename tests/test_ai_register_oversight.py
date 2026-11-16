"""Incident regressions: model tool calls and extraction cannot make decisions."""
import json
import unittest
from unittest.mock import patch

from app.modules.chat import chat_agent, chat_tools
from app.modules.intake import consult_admin, health_profile
import _env  # noqa: E402


class TestHumanOversight(unittest.TestCase):
    def test_interview_close_never_extracts_or_saves_an_unreviewed_profile(self):
        from app.modules.intake import interview_agent
        with patch('app.core.db_clinical.interview_set_status') as close, \
             patch('app.modules.intake.analysis.analyze_interview') as analyze, \
             patch.object(health_profile, 'merge_from_interview') as extract, \
             patch('app.core.db_clinical.health_profile_save') as save:
            interview_agent._close({'id': 7, 'dni': '99000001'})
        close.assert_called_once_with(7, 'done')
        analyze.assert_called_once_with({'id': 7, 'dni': '99000001'})
        extract.assert_not_called()
        save.assert_not_called()

    def test_model_cannot_dispatch_approval_rejection_reassignment_or_clinical_write(self):
        for name in ('approve_request', 'reject_request', 'reassign_request', 'write_clinical_record'):
            with self.subTest(tool=name), \
                 _env.patched_env({'TURNOS_FAKE_AI': '1', 'TURNOS_FAKE_CHAT_AI': '1'}), \
                 patch.object(chat_agent, '_load_history', return_value=[]), \
                 patch.object(chat_agent, '_save'), \
                 patch.object(chat_agent, '_prompt_with_knowledge', return_value=('Synthetic system', [])), \
                 patch.object(chat_agent, '_memory_block', return_value=''), \
                 patch.object(chat_agent, '_turns_used', return_value=0), \
                 patch.object(chat_agent.ai_client, 'call', side_effect=AssertionError('external call forbidden')):
                from unittest.mock import Mock
                writer = Mock()
                with patch.dict(chat_tools.TOOL_FNS, {name: writer}):
                    result = chat_agent._run_turn({'dni': '99000001'}, 'TOOL:' + name + ':{}', 'dni:synthetic', 'web')
                writer.assert_not_called()
                self.assertTrue(any('error' in item for _name, item in result['tools']))

    def test_tool_cannot_enable_auto_approval_even_if_catalog_changes_after_read(self):
        service = {'id': 'synthetic', 'modo': 'solicitud'}
        with patch('app.patient_web.site_catalog._service_by_id', return_value=service), \
             patch('app.patient_web.site_availability._offers_appointments', return_value=True), \
             patch('app.patient_web.site_availability._opens_same_day', return_value=False), \
             patch('app.modules.turnos.booking_create.create_request', return_value=(200, {'ok': True, 'id': 7})) as create:
            chat_tools.TOOL_FNS['request_appointment']({'dni': '99000001'},
                {'service_id': 'synthetic', 'allow_auto_confirmation': True}, chat_tools.ToolInvocation())
        create.assert_not_called()

    def test_model_cannot_use_auto_confirmation_booking_path(self):
        service = {'id': 'synthetic', 'modo': 'solicitud', 'auto_confirmar': True}
        with patch('app.patient_web.site_catalog._service_by_id', return_value=service), \
             patch('app.patient_web.site_availability._offers_appointments', return_value=True), \
             patch('app.modules.turnos.booking_create.create_request') as create:
            result = chat_tools.TOOL_FNS['request_appointment'](
                {'dni': '99000001'}, {'service_id': 'synthetic'}, chat_tools.ToolInvocation())
        create.assert_not_called()
        self.assertTrue(result['confirmation_required'])

    def test_model_cancellation_only_offers_human_confirmation_link(self):
        with patch('app.modules.turnos.booking_patient.cancel_request') as cancel:
            result = chat_tools.TOOL_FNS['cancel_request'](
                {'dni': '99000001'}, {'id': 71}, chat_tools.ToolInvocation())
        cancel.assert_not_called()
        self.assertEqual(result['confirmation_required'], True)
        self.assertTrue(result['href'].endswith('/request/71'))

    def test_confirmation_result_is_a_truthful_linked_reply_without_another_model_turn(self):
        result = {'confirmation_required':True, 'href':'/request/71',
                  'confirmation_label':'el turno',
                  'nota':'Para cancelar, abrí el turno y confirmá la cancelación.'}
        from unittest.mock import Mock
        with _env.patched_env({'TURNOS_FAKE_AI':'1','TURNOS_FAKE_CHAT_AI':'1'}), \
             patch.object(chat_agent, '_load_history', return_value=[]), \
             patch.object(chat_agent, '_save'), \
             patch.object(chat_agent, '_prompt_with_knowledge', return_value=('Synthetic system', [])), \
             patch.object(chat_agent, '_memory_block', return_value=''), \
             patch.object(chat_agent, '_turns_used', return_value=0), \
             patch.dict(chat_tools.TOOL_FNS, {'cancel_request':Mock(return_value=result)}):
            reply = chat_agent.chat_full({'dni':'99000001'}, 'TOOL:cancel_request:{"id":71}', 'dni:synthetic', 'web')
        self.assertEqual(reply['texto'], result['nota'])
        self.assertIn('/request/71', str(reply['enlaces']))

    def test_extracted_profile_is_draft_until_a_human_saves_it(self):
        output = json.dumps({'medicacion': ['medicación ficticia'],
                             'condiciones': [], 'alergias': [], 'antecedentes': []})
        with patch.object(health_profile, 'leer', return_value={}), \
             patch.object(health_profile.ai_client, 'available', return_value=True), \
             patch.object(health_profile.ai_client, 'call', return_value={
                 'choices': [{'message': {'content': output}}]}), \
             patch('app.core.db_clinical.health_profile_save') as save, \
             patch('app.core.db_chat.chat_save_message'), \
             _env.patched_env({'TURNOS_FAKE_AI': '0'}):
            draft = health_profile.merge_from_interview(
                {'id': 7, 'dni': '99000001'}, ['P: tomo medicación ficticia'])
        save.assert_not_called()
        self.assertEqual(draft['medicacion'], ['medicación ficticia'])

    def test_only_explicit_human_review_saves_profile_during_consult_confirmation(self):
        from contextlib import ExitStack
        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed), ExitStack() as stack:
                stack.enter_context(patch('app.core.db_clinical.interview_get', return_value={'id':7,'dni':'99000001','doctor_id':'synthetic-doctor'}))
                stack.enter_context(patch.object(consult_admin, 'guard_own_interview_or_404'))
                stack.enter_context(patch.object(consult_admin, '_doctor_especialidad', return_value='synthetic'))
                stack.enter_context(patch.object(consult_admin, '_doctor_info', return_value={}))
                stack.enter_context(patch.object(consult_admin, '_paciente_nombre', return_value='Synthetic'))
                stack.enter_context(patch.object(consult_admin.intake_catalog, 'diagnoses', return_value=[]))
                stack.enter_context(patch.object(consult_admin.intake_catalog, 'exams', return_value=[]))
                stack.enter_context(patch.object(consult_admin.consult_compose, 'compose', return_value=({},{})))
                stack.enter_context(patch('app.core.db_clinical.consultation_confirm'))
                saved = stack.enter_context(patch('app.core.db_clinical.health_profile_save'))
                consult_admin.confirm_payload({'usuario':'synthetic-doctor','rol':'medico'}, 7,
                    {'profile_reviewed':reviewed, 'profile':{'alergias':['Synthetic reviewed allergy']},
                     'patient_reviewed':{'nombre':'Synthetic','dni':'99000001'},
                     'choices_reviewed':{'diagnosticos':[],'estudios':[],'medicacion':[],'notas':''}})
                if reviewed:
                    saved.assert_called_once_with('99000001', health_profile.normalizar({'alergias':['Synthetic reviewed allergy']}), 7)
                else:
                    saved.assert_not_called()
