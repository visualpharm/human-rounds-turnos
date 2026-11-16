"""Synthetic, offline regressions for the independent slice 8 review."""
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core import ai_register, config, db, translate
from app.core import db_accounts
from app.core import db_clinical
from app.core import db_chat
from app.core import db_catalog
from app.core import db_ai
from app.modules.chat import chat_tools, voice, whatsapp_chat
from app.modules.intake import analysis, health_profile, consult_admin


class TestReviewFixes(unittest.TestCase):
    def test_settings_write_updates_snapshot_in_its_transaction(self):
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {'doc':{}}
        with patch.object(db,'_connect',return_value=conn), \
             patch.object(db_catalog,'catalog_upsert'), patch.object(db_ai,'ai_register_append'), \
             patch.object(ai_register,'observe_inventory') as observe:
            config.guardar({'TURNOS_AI_MODEL':'synthetic/model'})
        self.assertIs(observe.call_args.kwargs['connection'],conn)
        self.assertFalse(observe.call_args.kwargs['record_changes'])
        self.assertEqual(observe.call_args.args[0][0]['model'],'synthetic/model')

    def test_compliance_documents_config_cache_ttl(self):
        text = Path('docs/compliance/runtime-controls.md').read_text()
        self.assertIn('3 seconds',text)
        self.assertIn('TURNOS_AI_DISABLED',text)

    def test_triage_scrubs_identity_before_provider_and_restores_response(self):
        text = 'Me llamo Ana Sintetica, DNI 99000001. Tomo medicación y me duele la cabeza.'
        def provider(prompt, model):
            self.assertNotIn('Ana Sintetica', prompt)
            self.assertNotIn('99000001', prompt)
            return {'choices': [{'message': {'content': json.dumps({
                'triage': 'AMARILLO', 'one_liner': '[nombre 1] consulta',
                'brief': [{'priority': 1, 'point': 'Síntomas referidos para revisión clínica.'}]})}}]}
        with ExitStack() as stack:
            stack.enter_context(patch.object(analysis, '_transcript', return_value=[{'role':'user','content':text}]))
            stack.enter_context(patch.object(analysis, '_env', return_value='0'))
            stack.enter_context(patch.object(analysis.ai_client, 'available', return_value=True))
            stack.enter_context(patch('app.core.db_accounts.get_user', return_value={'nombre':'Ana','apellido':'Sintetica','dni':'99000001'}))
            stack.enter_context(patch.object(analysis, '_especialidad_for', return_value='synthetic'))
            stack.enter_context(patch.object(analysis.intake_catalog, 'exams', return_value=[]))
            stack.enter_context(patch.object(analysis.intake_catalog, 'diagnoses', return_value=[]))
            stack.enter_context(patch.object(analysis, '_persist'))
            stack.enter_context(patch.object(analysis, '_record_cost'))
            stack.enter_context(patch.object(health_profile, 'merge_from_interview', return_value={}))
            stack.enter_context(patch.object(analysis, '_call_openrouter', side_effect=provider))
            result = analysis.analyze_interview({'id':7,'dni':'99000001'})
        self.assertEqual(result['summary'], 'Ana Sintetica consulta')
        row = next(r for r in ai_register.FUNCTIONS if r['id'] == 'triage')
        # the text names the module inside the code package and is a catalog key (ai.pii.*)
        self.assertIn('core/pii.py', row['pii_handling'])
        from app.patient_web import legal
        self.assertIn('entrevista', legal._PRIVACIDAD.lower())
        self.assertIn('seudonimiz', legal._PRIVACIDAD.lower())

    def test_institutional_translation_keeps_public_contacts(self):
        source = 'Hospital: 02254 123456, contacto@hospital.example'
        def echo(body, **kwargs):
            self.assertIn('02254 123456',body['messages'][-1]['content'])
            self.assertIn('contacto@hospital.example',body['messages'][-1]['content'])
            return {'choices':[{'message':{'content':body['messages'][-1]['content']}}]}
        with patch.object(translate.ai_client, 'available', return_value=True), \
             patch.object(translate.ai_client, 'call', side_effect=echo), \
             patch.object(translate, '_fake', return_value=False):
            self.assertEqual(translate._call_openrouter(source, 'en'), source)
            self.assertEqual(translate._machine_translate_batch([source], 'en'), [source])
            from app.ai import prompts
            self.assertEqual(prompts._call_text('Translate institutional instructions',source),source)
            from scripts import translate_locales
            out = translate_locales._call('en',{'contact':{'es':source,'en':source}})
            self.assertEqual(out['contact']['es'],source)

    def test_poisoned_cached_translation_is_removed_without_deleting_admin_override(self):
        rows = {'bad':{'content':'Call [teléfono 1] or [email 1]', 'source_hash':translate._hash('source')},
                'edited':{'content':'Quoted [email 1]', 'override':True}}
        translate.invalidate_rows()
        with patch.object(db_catalog, 'translations_all', return_value=rows), \
             patch.object(db_catalog, 'translations_drop_pii_placeholders', create=True) as cleanup:
            self.assertEqual(translate.cached_text('bad', 'source', 'en', 'es'), 'source')
            self.assertEqual(translate.cached_text('edited', 'source', 'en', 'es'), 'Quoted [email 1]')
            translate.cached_text('bad', 'source', 'en', 'es')
        cleanup.assert_called_once_with('en')
        translate.invalidate_rows()

    def test_dossier_reads_do_not_observe_sources_or_write(self):
        snapshots = [{'kind':'snapshot','function_id':r['id'],'substantial':False,'status':'recorded'} for r in ai_register.FUNCTIONS]
        with patch.object(db_ai, 'ai_register_list_changes', return_value=snapshots), \
             patch.object(ai_register, 'observe_inventory') as observe, \
             patch.object(Path, 'read_text', side_effect=AssertionError('source read')):
            ai_register.dossier()
        observe.assert_not_called()

    def test_functionality_version_is_human_maintained_not_source_hash(self):
        rows = ai_register.inventory()
        self.assertTrue(all(isinstance(r.get('functionality_version'), int) for r in rows))
        with patch.object(Path, 'read_text', side_effect=AssertionError('unrelated edit')):
            first = ai_register.inventory_state(rows[0])
            second = ai_register.inventory_state(rows[0])
        self.assertEqual(first['functionality'], second['functionality'])

    def test_fake_voice_still_offers_microphone_without_provider_key(self):
        with patch.object(config, 'get', side_effect=lambda k, default=None: {'TURNOS_FAKE_AI':'1','FAL_KEY':''}.get(k, default)):
            self.assertTrue(voice.public_status_payload()['disponible'])

    def test_free_image_fallback_retention_risk_is_explicit(self):
        rows = ai_register.inventory()
        for fid in ('referral','dni_reading','lab_upload'):
            row = next(r for r in rows if r['id'] == fid)
            self.assertIn(':free', row['retention_risk'])
        self.assertIn('retention_risk', Path('templates/admin/ai-register.html').read_text())

    def test_whatsapp_notice_uses_unbounded_metadata_outside_llm_history(self):
        with patch('app.core.db_chat.chat_disclosure_sent', return_value=True, create=True) as seen, \
             patch('app.core.db_chat.chat_history', return_value=[]), \
             patch('app.core.db_chat.user_by_phone', return_value=None), \
             patch('app.core.db_chat.user_by_handoff_phone', return_value=None), \
             patch.object(whatsapp_chat, '_procesar_handoff', return_value=(None,None)), \
             patch('app.notifications.otp_send.enviar_texto', return_value=True) as send:
            whatsapp_chat._procesar_mensaje({'from':'synthetic','body':'hola'})
        seen.assert_called_once_with('whatsapp','tel:synthetic')
        self.assertNotIn(ai_register.notice('ai.notice.chat'), send.call_args.args[1])

    def test_profile_proposal_is_stored_separately_never_in_confirmed_profile(self):
        proposal = health_profile.normalizar({'alergias':['alergia sintética']})
        with patch.object(health_profile, 'leer', return_value={}), \
             patch.object(health_profile.ai_client, 'available', return_value=True), \
             patch.object(health_profile.ai_client, 'call', return_value={'choices':[{'message':{'content':json.dumps(proposal)}}]}), \
             patch.object(config,'get', side_effect=lambda k, default=None: '0' if k == 'TURNOS_FAKE_AI' else default), \
             patch.object(db_accounts, 'get_user', return_value={}), \
             patch.object(db_chat, 'chat_save_message') as save_proposal, \
             patch.object(db_clinical, 'health_profile_save') as save_confirmed:
            health_profile.merge_from_interview({'id':7,'dni':'99000001'},['P: tengo alergia sintética'])
        save_confirmed.assert_not_called()
        save_proposal.assert_called_once_with('interview','interview:7',{'role':'health_profile_proposal','datos':proposal})

    def test_prompt_audit_does_not_call_cached_translation_saved_text(self):
        conn = MagicMock(); conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [{'text_en':'canonical English','text_es':'traducción cacheada','edited':False}, None, {'key':'orden_analisis'}]
        with patch.object(db, '_connect', return_value=conn), patch.object(db_ai, 'ai_register_append') as append:
            db_ai.prompt_save('orden_analisis','new English','Instrucción española',True,historial=True)
        event = append.call_args.args[0][0]
        self.assertNotEqual(event['details']['before_source'], 'saved text')
        self.assertEqual(event['details']['after'], ai_register.fingerprint('Instrucción española'))
        self.assertNotIn('traducción cacheada', str(event))

    def test_booking_requires_transport_confirmation_not_model_argument(self):
        service = {'id':'synthetic','modo':'solicitud'}
        with patch('app.patient_web.site_catalog._service_by_id', return_value=service), \
             patch('app.patient_web.site_availability._offers_appointments', return_value=True), \
             patch('app.modules.turnos.booking_create.create_request') as create, \
             patch.object(chat_tools,'_base_url',return_value='https://synthetic.example'):
            out = chat_tools._tool_request({'dni':'99000001'}, {'service_id':'synthetic','date':'2099-01-01','time':'10:00','confirmed':True}, chat_tools.ToolInvocation())
        create.assert_not_called()
        self.assertTrue(out['confirmation_required'])
        self.assertTrue(out['href'].startswith('https://'))

    def test_confirmed_patient_reply_books_auto_and_same_day_via_rules(self):
        intent = {'service_id':'synthetic','date':'2099-01-01','time':'10:00'}
        from time import time
        for same_day in (False, True):
            service = {'id':'synthetic','modo':'walkin' if same_day else 'solicitud','auto_confirmar':not same_day}
            with self.subTest(same_day=same_day), \
                 patch('app.patient_web.site_catalog._service_by_id',return_value=service), \
                 patch('app.patient_web.site_availability._offers_appointments',return_value=True), \
                 patch('app.patient_web.site_availability._opens_same_day',return_value=same_day), \
                 patch.object(chat_tools,'_slots_de',return_value=[{'dia':'2099-01-01','hora':'10:00'}]), \
                 patch.object(chat_tools.chat_store,'active_booking_request_by_slot',return_value=None), \
                 patch.object(chat_tools.chat_store,'chat_metadata_get',return_value={'role':'booking_proposal'}), \
                 patch.object(chat_tools.chat_store,'chat_take_booking_proposal',return_value={'args':intent,'patient_dni':'99000001','created':time()}), \
                 patch('app.modules.turnos.booking_create.create_request',return_value=(200,{'ok':True,'id':7,'estado':'aprobada'})) as create:
                out = chat_tools.confirm_booking_reply({'dni':'99000001'},'Confirmar','web','synthetic')
            self.assertEqual(out['estado'],'aprobada')
            self.assertTrue(create.call_args.kwargs['allow_auto_confirmation'])

    def test_proposal_does_not_enter_confirmed_interview_memory(self):
        with patch.object(db_clinical,'health_profile_get',return_value={'datos':{'alergias':['Confirmed allergy']}}), \
             patch.object(db_chat,'chat_metadata_get',return_value={'datos':{'alergias':['Proposed allergy']}}) as proposal:
            text = health_profile.prompt_block('99000001')
        self.assertIn('Confirmed allergy',text)
        self.assertNotIn('Proposed allergy',text)
        proposal.assert_not_called()

    def test_mcp_all_nested_hrefs_are_absolute(self):
        from app.modules.chat import mcp_api
        with patch.object(chat_tools,'_base_url',return_value='https://synthetic.example'):
            out = mcp_api.absolute_hrefs({'resultados':[{'href':'/service/synthetic','turnos':[{'href':'/service/synthetic/book?slot=2099-01-01T10:00'}]}], 'directorio_href':'/doctors'})
        self.assertEqual(out['directorio_href'],'https://synthetic.example/doctors')
        self.assertEqual(out['resultados'][0]['turnos'][0]['href'],'https://synthetic.example/service/synthetic/book?slot=2099-01-01T10:00')

    def test_same_day_service_is_offered_as_bookable(self):
        service = {'id':'synthetic','modo':'walkin','apertura_mismo_dia':True}
        with patch('app.patient_web.site_availability._offers_appointments',return_value=True):
            row = chat_tools._info_servicio(service,{})
        self.assertTrue(row['se_pide_por_aca'])

    def test_even_confirmed_reply_never_approves_an_existing_pending_request(self):
        intent = {'service_id':'synthetic','date':'2099-01-01','time':'10:00'}
        with patch('app.patient_web.site_catalog._service_by_id',return_value={'id':'synthetic','modo':'solicitud','auto_confirmar':True}), \
             patch('app.patient_web.site_availability._offers_appointments',return_value=True), \
             patch.object(chat_tools.chat_store,'active_booking_request_by_slot',return_value={'id':7,'estado':'pendiente'}), \
             patch('app.modules.turnos.booking_create.create_request') as create:
            out = chat_tools._tool_request({'dni':'99000001'},intent,chat_tools.ToolInvocation(confirmed_booking=intent))
        self.assertEqual(out['estado'],'pendiente')
        self.assertTrue(out['recuperada'])
        create.assert_not_called()
