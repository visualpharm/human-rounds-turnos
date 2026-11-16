"""Synthetic multi-turn runtime regressions; no DB, provider or patient data.

Provider replies are adversarial fixtures, not a claim about live model quality.
Exercise the actual run_turn boundary, persistence, PII path and close behavior.
"""
import copy
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE='/dev/null')
from app.core import ai_policy, ai_register
from app.modules.intake import analysis, dialogue, health_profile, interview_agent as agent


class Identity:
    def scrub_json(self, value):
        return copy.deepcopy(value)

    def scrub(self, value):
        return value

    def scrub_messages(self, messages):
        return copy.deepcopy(messages)

    def restore(self, content):
        return content


class Conversation:
    def __init__(self, owner, profile=None, mode='post_booking', key='synthetic-a'):
        self.messages, self.calls, self.closed = [], [], []
        self.reply = '¿Desde cuándo tenés esto? [pregunta: onset]'
        self.interview = {'id': 101, 'session_key': key, 'dni': 'synthetic-person', 'entry_mode': mode}
        self.stack = ExitStack()
        owner.addCleanup(self.stack.close)
        patches = [
            patch.object(agent, '_env', side_effect=lambda k, default='': '0' if k == 'TURNOS_FAKE_AI' else default),
            patch.object(agent, '_load_history', side_effect=lambda key: copy.deepcopy(self.messages)),
            patch.object(agent, '_save', side_effect=lambda key, message: self.messages.append(copy.deepcopy(message))),
            patch.object(agent, '_close', side_effect=lambda interview: self.closed.append(interview['id'])),
            patch.object(agent, '_especialidad_for', return_value='Traumatología'),
            patch.object(agent.ai_client, 'available', return_value=True),
            patch.object(agent.ai_client, 'call', side_effect=self.call),
            patch.object(agent.ai_health, 'available', return_value=True),
            patch.object(agent.ai_health, 'report'),
            patch.object(agent.pii, 'for_dni', return_value=Identity()),
            patch.object(health_profile, 'leer', return_value=profile or {}),
            patch('app.core.site_identity.get', side_effect=lambda k: '107' if k == 'emergency_number' else 'Hospital de prueba'),
            patch('app.ai.prompts.text', return_value='Gather symptoms. Never diagnose or prescribe.'),
        ]
        for item in patches:
            self.stack.enter_context(item)

    def call(self, body, **kwargs):
        self.calls.append(copy.deepcopy(body))
        if isinstance(self.reply, Exception):
            raise self.reply
        return {'choices': [{'message': {'content': self.reply}}]}

    def send(self, content='', reply=None):
        if reply is not None:
            self.reply = reply
        return agent.run_turn(self.interview, content)

    @property
    def ledger(self):
        return dialogue.state(self.messages)


def prior(**fields):
    return {'datos': health_profile.normalizar(fields), 'updated': None}


class TestConversation(unittest.TestCase):
    def test_acute_injury_opens_complaint_and_does_not_repeat_volunteered_onset(self):
        c = Conversation(self, prior(condiciones=['Hipertensión histórica'], medicacion=['omeprazol histórico']))
        opening = c.send()
        self.assertEqual(opening['texto'], agent._APERTURA)
        self.assertEqual(c.calls, [])
        result = c.send('Ayer me torcí el tobillo izquierdo. Puedo caminar.',
                        '¿Desde cuándo te duele? [pregunta: onset]')
        self.assertNotIn('Desde cuándo', result['texto'])
        self.assertEqual(c.ledger['answered']['onset']['turn'], 1)
        self.assertNotIn('WHAT THIS PATIENT ALREADY TOLD US', c.calls[-1]['messages'][0]['content'])

    def test_chronic_followup_keeps_dated_relevant_test_as_patient_evidence(self):
        c = Conversation(self)
        c.send()
        c.send('Vengo al control de rodilla izquierda. Me hice una resonancia en mayo.',
               '¿Cómo cambió el dolor desde el control? [pregunta: progression]')
        result = c.send('Está igual desde entonces.', '¿Tenés estudios? [pregunta: tests]')
        self.assertNotIn('Tenés estudios', result['texto'])
        self.assertIn('mayo', c.ledger['answered']['tests']['quote'])
        self.assertEqual(c.ledger['answered']['tests']['source'], 'patient_this_interview')

    def test_relevant_medication_is_unconfirmed_not_an_answer(self):
        c = Conversation(self, prior(medicacion=['apixabán referido el año pasado']))
        c.send()
        c.send('Me caí y me duele la muñeca.', '¿Te golpeaste la cabeza? [pregunta: head_impact]')
        self.assertNotIn('medication', c.ledger['answered'])
        c.send('No me golpeé la cabeza.', 'Antes mencionaste apixabán, ¿lo seguís tomando? [pregunta: medication]')
        result = c.send('Ya no lo tomo, lo suspendió mi médica.', '¿Tomás apixabán? [pregunta: medication]')
        self.assertNotIn('Tomás apixabán', result['texto'])
        self.assertIn('Ya no lo tomo', c.ledger['answered']['medication']['quote'])

    def test_changed_visit_reason_starts_new_episode_without_erasing_old_quotes(self):
        c = Conversation(self)
        c.send()
        c.send('Vengo por el hombro, desde ayer.', '¿Podés moverlo? [pregunta: function]')
        c.send('Ahora vengo por la rodilla izquierda.', '¿Desde cuándo te molesta? [pregunta: onset]')
        self.assertEqual(c.ledger['epoch'], 1)
        self.assertNotIn('onset', c.ledger['answered'])
        self.assertIn('hombro', c.ledger['statements'][0]['quote'])
        self.assertIn('rodilla', c.ledger['corrections'][-1]['quote'])

    def test_correction_wins_in_current_ledger_without_mutating_old_profile(self):
        profile = prior(antecedentes=['Rodilla derecha, referido en un control anterior'])
        original = copy.deepcopy(profile)
        c = Conversation(self, profile)
        c.send()
        c.send('Me duele la rodilla derecha.', '¿Desde cuándo? [pregunta: onset]')
        c.send('Me equivoqué: es la rodilla izquierda, desde ayer.', '¿Qué lado? [pregunta: location]')
        self.assertIn('izquierda', c.ledger['answered']['location']['quote'])
        self.assertEqual(profile, original)
        self.assertEqual(len(c.ledger['corrections']), 1)

    def test_urgent_fingers_precede_unavailable_model_and_turn_limit(self):
        for mode in ('post_booking', 'qr'):
            with self.subTest(mode=mode):
                c = Conversation(self, mode=mode)
                c.send()
                with patch.object(agent.ai_client, 'available', return_value=False), patch.object(agent, '_max_turns', return_value=0):
                    result = c.send('Después del golpe tengo los dedos azules y no siento la mano.')
                self.assertTrue(result['fin'])
                self.assertEqual(c.calls, [])
                self.assertNotIn('?', result['texto'])
                self.assertIn('personal' if mode == 'qr' else 'No esperes al turno', result['texto'])

    def test_refusal_and_unknown_are_not_negatives_or_repeated(self):
        for answer, expected in [('Prefiero no responder.', 'declined'), ('No sé.', 'unknown')]:
            with self.subTest(answer=answer):
                c = Conversation(self)
                c.send()
                c.send('Vengo a un control.', '¿Qué medicación tomás? [pregunta: medication]')
                result = c.send(answer, '¿Qué medicamentos tomás? [pregunta: medication]')
                self.assertEqual(c.ledger['answered']['medication']['disposition'], expected)
                self.assertNotIn('medicamentos', result['texto'])

    def test_user_can_stop_before_three_answers_without_loop(self):
        c = Conversation(self)
        c.send()
        result = c.send('Prefiero terminar.')
        self.assertTrue(result['fin'])
        self.assertEqual(c.calls, [])
        self.assertNotIn('suficiente', result['texto'])

    def test_repeated_question_loop_stops_without_false_completion(self):
        c = Conversation(self)
        c.send()
        c.send('Desde ayer me duele el tobillo.', '¿Desde cuándo? [pregunta: onset]')
        result = c.send('Nada más.', '¿Desde cuándo empezó? [pregunta: onset]')
        self.assertTrue(result['fin'])
        self.assertIn('revisar', result['texto'])
        self.assertNotIn('suficiente', result['texto'])

    def test_completion_accepts_correction_before_patient_finishes(self):
        c = Conversation(self)
        c.send()
        c.send('Me torcí el tobillo ayer.', 'Entendí que fue ayer. ¿Querés agregar o corregir algo más? [pregunta: additional]')
        c.send('Me equivoqué, fue anteayer.', 'Gracias por corregirlo. El médico lo revisa durante la consulta. [fin]')
        self.assertEqual(c.closed, [101])
        self.assertIn('anteayer', c.ledger['corrections'][-1]['quote'])

    def test_timeout_and_empty_reply_remain_incomplete(self):
        for reply in (TimeoutError('synthetic timeout'), ''):
            c = Conversation(self)
            c.send()
            result = c.send('Me duele el tobillo.', reply)
            self.assertFalse(result['fin'])
            self.assertEqual(c.closed, [])
            self.assertEqual(result['texto'], agent._DISCULPA)
            self.assertEqual(c.messages[-1]['intake_outcome'], 'incomplete')

    def test_truncated_model_response_cannot_close_as_complete(self):
        c = Conversation(self)
        c.send()
        with patch.object(agent.ai_client, 'call', return_value={
                'choices': [{'finish_reason': 'length', 'message': {'content': 'Gracias. [fin]'}}]}):
            result = c.send('Vengo por dolor de tobillo.')
        self.assertFalse(result['fin'])
        self.assertEqual(c.messages[-1]['intake_outcome'], 'incomplete')

    def test_context_is_local_and_provider_gets_no_application_metadata(self):
        c = Conversation(self)
        c.send()
        c.send('Me duele el tobillo.', '¿Desde cuándo? [pregunta: onset]')
        self.assertTrue(all(set(m) == {'role', 'content'} for m in c.calls[-1]['messages']))
        unrelated = dialogue.state([{'role': 'user', 'content': 'Control de visión.'}])
        self.assertNotIn('tobillo', str(unrelated))
        self.assertEqual(unrelated['answered'], {})

    def test_new_contact_details_are_scrubbed_in_ledger_and_history(self):
        from app.core.pii import Scrubber
        c = Conversation(self, prior(antecedentes=['Contacto anterior old@example.invalid']))
        c.send()
        with patch.object(agent.pii, 'for_dni', return_value=Scrubber()):
            c.send('Me duele la rodilla. Mi correo es synthetic@example.invalid y mi teléfono 1155551234.',
                   '¿Desde cuándo te duele? [pregunta: onset]')
        sent = str(c.calls[-1]['messages'])
        for private in ('synthetic@example.invalid', 'old@example.invalid', '1155551234'):
            self.assertNotIn(private, sent)
        self.assertIn('synthetic@example.invalid', c.messages[-2]['content'])

    def test_generic_mentions_allow_specific_clarification(self):
        for statement, question, topic in [
            ('Me duele la rodilla.', '¿Qué lado te duele?', 'laterality'),
            ('Tomo un medicamento.', '¿Qué dosis tomás?', 'medication_dose'),
            ('Me hice una resonancia.', '¿Cuándo te hiciste la resonancia?', 'test_date'),
            ('Me hicieron una radiografía.', '¿Tenés el informe?', 'test_report'),
        ]:
            with self.subTest(topic=topic):
                c = Conversation(self)
                c.send()
                result = c.send(statement, question)
                self.assertEqual(result['texto'], question)
                self.assertNotIn(topic, c.ledger['answered'])

    def test_request_for_clarification_does_not_become_an_answer(self):
        c = Conversation(self)
        c.send()
        c.send('Me duele la rodilla.', '¿Desde cuándo? [pregunta: onset]')
        result = c.send('¿A qué te referís?', '¿Desde cuándo comenzó el dolor? [pregunta: onset]')
        self.assertIn('Desde cuándo', result['texto'])
        self.assertNotIn('onset', c.ledger['answered'])

    def test_negated_stop_does_not_close(self):
        c = Conversation(self)
        c.send()
        result = c.send('No quiero terminar, quiero seguir.', '¿Qué te trae a la consulta?')
        self.assertFalse(result['fin'])
        self.assertEqual(c.closed, [])

    def test_positive_urgent_clause_survives_negated_other_symptom(self):
        self.assertTrue(dialogue.urgent_now('No tengo fiebre, pero los dedos están azules y no siento la mano.'))
        self.assertTrue(dialogue.urgent_now('No tengo sensibilidad en la mano.'))
        self.assertTrue(dialogue.urgent_now('No tengo fiebre y no puedo respirar estando sentado.'))
        self.assertFalse(dialogue.urgent_now('No me desmayé.'))
        self.assertTrue(dialogue.urgent_now('No me desmayé, tengo los dedos azules.'))
        self.assertTrue(dialogue.urgent_now('El año pasado me caí, ahora no puedo respirar estando sentado.'))

    def test_booking_date_is_not_symptom_onset(self):
        c = Conversation(self)
        c.send()
        result = c.send('Me duele la rodilla. Ayer pedí el turno.', '¿Desde cuándo te duele la rodilla? [pregunta: onset]')
        self.assertIn('Desde cuándo', result['texto'])
        self.assertNotIn('onset', c.ledger['answered'])

    def test_historical_emergency_room_mention_is_not_urgent(self):
        c = Conversation(self)
        c.send()
        c.send('Me revisaron ayer por la rodilla.', 'El médico revisará lo que contaste de la atención en guardia. Gracias. [fin]')
        self.assertEqual(c.messages[-1]['intake_outcome'], 'complete')
        self.assertFalse(agent._es_cierre_de_emergencia('Si empeora, andá a la guardia. [fin]'))

    def test_conditional_transport_advice_does_not_erase_urgent_direction(self):
        c = Conversation(self)
        c.send()
        result = c.send('La mano se ve violeta y está helada.',
                        'Andá a la guardia ahora. Si no podés trasladarte, llamá al 107. [fin]')
        self.assertTrue(result['fin'])
        self.assertEqual(c.messages[-1]['intake_outcome'], 'urgent')

    def test_other_drug_or_body_part_can_be_clarified(self):
        c = Conversation(self)
        c.send()
        c.send('Me caí.', '¿Qué medicación tomás? [pregunta: medication]')
        question = '¿Seguís tomando el apixabán que mencionaste antes? [pregunta: medication]'
        result = c.send('Tomo paracetamol.', question)
        self.assertIn('apixabán', result['texto'])
        result = c.send('Sí, también apixabán.', '¿Qué dosis de paracetamol tomás? [pregunta: medication_dose]')
        self.assertIn('paracetamol', result['texto'])
        result = c.send('500 mg.', '¿Qué dosis de apixabán tomás? [pregunta: medication_dose]')
        self.assertIn('apixabán', result['texto'])
        result = c.send('Puedo caminar pero me lastimé también la mano.', '¿Podés mover los dedos? [pregunta: function]')
        self.assertIn('mover los dedos', result['texto'])

    def test_guard_does_not_exempt_ordinary_question_mentioning_guardia(self):
        c = Conversation(self)
        c.send()
        c.send('Me duele el tobillo.', '¿Te atendieron en la guardia?')
        result = c.send('Sí.', '¿Te atendieron en la guardia?')
        self.assertNotIn('guardia', result['texto'])

    def test_empty_offtopic_close_is_blocked_and_intro_is_not_repeated(self):
        c = Conversation(self)
        c.send()
        result = c.send('Dame un script.', 'No puedo producir código. [fin]')
        self.assertFalse(result['fin'])
        self.assertNotIn('inteligencia artificial', result['texto'])

    def test_english_loop_fallback_uses_conversation_language(self):
        c = Conversation(self)
        c.send()
        result = c.send('My knee hurts since yesterday.', 'When did it start? [pregunta: onset]')
        self.assertIn('anything else', result['texto'])
        self.assertNotIn('¿', result['texto'])

    def test_urgent_outcome_retains_floor_on_reanalysis(self):
        c = Conversation(self)
        c.send()
        c.send('Tengo los dedos azules y no siento la mano.')
        with patch.object(analysis, '_transcript', return_value=c.messages), patch.object(analysis, '_env', return_value='1'), patch.object(analysis.ai_client, 'available', return_value=False), patch('app.core.db_clinical.interview_update_analysis') as save:
            result = analysis.analyze_interview(c.interview)
        self.assertEqual(result['triage'], 'ROJO')
        self.assertEqual(save.call_args.args[1], 'ROJO')

    def test_model_only_urgent_outcome_also_retains_floor(self):
        c = Conversation(self)
        c.send()
        c.send('La mano se ve violeta y está helada.', 'Andá a la guardia ahora, no esperes al turno. [fin]')
        self.assertEqual(c.messages[-1]['intake_outcome'], 'urgent')
        with patch.object(analysis, '_transcript', return_value=c.messages), patch.object(analysis, '_env', return_value='1'), patch.object(analysis.ai_client, 'available', return_value=False), patch('app.core.db_clinical.interview_update_analysis'):
            result = analysis.analyze_interview(c.interview)
        self.assertEqual(result['triage'], 'ROJO')


class TestRouting(unittest.TestCase):
    def test_interview_reasoning_budget_is_explicit_bounded_and_audited(self):
        settings = {'INTAKE_INTERVIEW_MAX_TOKENS': '2048', 'INTAKE_INTERVIEW_REASONING_EFFORT': 'low'}
        with patch.object(agent, '_env', side_effect=lambda key, default='': settings.get(key, default)):
            self.assertEqual(agent._inference_options(), {
                'temperature': 0.2, 'max_tokens': 2048, 'reasoning': {'effort': 'low', 'exclude': True}})
        events = ai_register.setting_events({}, settings)
        self.assertEqual([e['function_id'] for e in events], ['interview', 'interview'])
        with patch.object(agent, '_env', return_value='invalid'):
            self.assertEqual(agent._inference_options(), {'temperature': 0.2, 'max_tokens': 500})

    def test_interview_override_does_not_change_analysis_or_other_functions(self):
        with patch.object(ai_policy.config, 'get', side_effect=lambda key, default=None: {
                'INTAKE_INTERVIEW_MODEL': 'tests/stronger', 'TURNOS_AI_MODEL': 'tests/baseline'}.get(key, default)):
            self.assertEqual(agent._model(), 'tests/stronger')
            self.assertEqual(ai_policy.model('triage'), 'tests/baseline')
            self.assertEqual(ai_policy.model('booking_chat'), 'tests/baseline')

    def test_interview_override_change_is_audited_only_for_interview(self):
        events = ai_register.setting_events({}, {'INTAKE_INTERVIEW_MODEL': 'tests/stronger'})
        self.assertEqual([event['function_id'] for event in events], ['interview'])
        self.assertTrue(events[0]['substantial'])

    def test_opening_does_not_read_historical_profile(self):
        with patch.object(health_profile, 'prompt_block') as profile, patch('app.ai.prompts.text', return_value='base'), patch.object(agent, '_especialidad_for', return_value=None), patch('app.core.site_identity.get', return_value='test'):
            prompt = agent._system_prompt({'dni': 'synthetic-person'})
        profile.assert_not_called()
        self.assertIn('Start with the current chief complaint', prompt)

    def test_booking_context_uses_wire_fields_and_enforces_patient_scope(self):
        row = {'dni': 'synthetic-person', 'servicio_id': 'traumatologia', 'turno_dt': '2026-10-25T10:00:00', 'nacimiento': '1980-05-01', 'sexo': 'F'}
        with patch('app.core.db_booking.get_booking_request', return_value=row):
            context = agent._booking_context({'dni': 'synthetic-person', 'request_id': 101})
            other = agent._booking_context({'dni': 'someone-else', 'request_id': 101})
        self.assertIn('traumatologia', context)
        self.assertIn('age_years_from_booking', context)
        self.assertNotIn('1980-05-01', context)
        self.assertNotIn('traumatologia', other)


class TestIncompleteAnalysis(unittest.TestCase):
    def test_no_key_or_fake_path_never_green(self):
        result = analysis._deterministic_result(['Me duele el tobillo.'], 'fallback')
        self.assertEqual(result['triage'], 'AMARILLO')
        self.assertIn('incompleta', result['brief'][0]['point'])

    def test_incomplete_chat_cannot_receive_model_green(self):
        history = [{'role': 'user', 'content': 'Me duele el tobillo.'},
                   {'role': 'assistant', 'content': 'Terminamos.', 'intake_outcome': 'incomplete'}]
        with patch.object(analysis, '_transcript', return_value=history), patch.object(analysis, '_persist'), patch.object(analysis, '_call_openrouter') as call:
            result = analysis.analyze_interview({'id': 101})
        call.assert_not_called()
        self.assertEqual(result['triage'], 'AMARILLO')
        self.assertEqual(result['ai_model'], 'entrevista_incompleta')

    def test_partial_or_failed_analysis_cannot_receive_green(self):
        history = [{'role': 'user', 'content': 'Me duele el tobillo.'}]
        for response in (TimeoutError('synthetic'), {'choices': [{'message': {'content': '{"triage":"VERDE"}'}}]}):
            with self.subTest(response=type(response).__name__), ExitStack() as stack:
                stack.enter_context(patch.object(analysis, '_transcript', return_value=history))
                stack.enter_context(patch.object(analysis, '_persist'))
                stack.enter_context(patch.object(analysis, '_env', return_value='0'))
                stack.enter_context(patch.object(analysis.ai_client, 'available', return_value=True))
                stack.enter_context(patch.object(analysis, '_especialidad_for', return_value=None))
                stack.enter_context(patch.object(analysis.intake_catalog, 'exams', return_value=[]))
                stack.enter_context(patch.object(analysis.intake_catalog, 'diagnoses', return_value=[]))
                anon = stack.enter_context(patch.object(analysis.pii, 'for_dni'))
                anon.return_value.scrub.side_effect = lambda v: v
                anon.return_value.restore.side_effect = lambda v: v
                stack.enter_context(patch.object(analysis, '_call_openrouter', **({'side_effect': response} if isinstance(response, Exception) else {'return_value': response})))
                result = analysis.analyze_interview({'id': 101, 'dni': 'synthetic-person'})
                self.assertEqual(result['triage'], 'AMARILLO')
                self.assertEqual(result['differential'], [])

    def test_analysis_transcript_excludes_image_bytes(self):
        history = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Foto de mi mano.'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,SYNTHETIC'}}]}]
        self.assertEqual(analysis._user_texts(history), ['Foto de mi mano.'])
        self.assertNotIn('base64', str(analysis._qa_lines(history)))
