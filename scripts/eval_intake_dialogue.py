#!/usr/bin/env python3
"""Synthetic-only intake comparison through the actual patient run_turn path.

Default is a no-network corpus/plan export. --live is for an already approved
runtime with its existing injected configuration; this script provisions no
credentials, changes no selectors, and never reads/writes patient records.
Do not run --live where metered inference spending is not authorized.
"""
import argparse
import copy
from decimal import Decimal
import json
from pathlib import Path
import statistics
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Public GET /api/v1/models verified 2026-10-21. These are catalog listings,
# not proof that the deployment key's existing guardrails allow each model.
# Prices are USD / million tokens; use API prices over a cached model card.
MODELS = {
    'google/gemini-3-flash-preview': {'input': '0.5', 'output': '3', 'max_tokens': 500, 'effort': ''},
    'google/gemini-3.8-flash': {'input': '0.825', 'output': '4.125', 'max_tokens': 2048, 'effort': 'low'},
    'google/gemini-4-argon': {'input': '2', 'output': '10', 'max_tokens': 2048, 'effort': 'low'},
}
MAX_INPUT_UNITS = 24000  # UTF-8 bytes + message framing reserve, not chars/4.
MAX_ATTEMPTS = 99       # 10 cases, 33 scripted turns, 3 models; includes retries.
MAX_USD = Decimal('5')
CASES = [
    {'id': 'acute_injury', 'profile': {'condiciones': ['Antecedente digestivo remoto'], 'medicacion': ['omeprazol referido anteriormente']},
     'turns': ['Ayer me torcí el tobillo izquierdo al bajar un escalón. Puedo caminar.', 'No me golpeé la cabeza.', 'No tomo medicación ni tengo alergias.', 'Nada más.']},
    {'id': 'chronic_followup', 'turns': ['Vengo al control de rodilla izquierda. Me hice una resonancia en mayo.', 'El dolor sigue igual que en el control anterior.', 'Tengo el informe para la consulta.', 'Nada más.']},
    {'id': 'medication_safety', 'profile': {'medicacion': ['apixabán referido anteriormente']},
     'turns': ['Me caí y me duele la muñeca.', 'No me golpeé la cabeza.', 'Tomo paracetamol. También sigo con apixabán.', 'La dosis la reviso con mi médica.', 'Nada más.']},
    {'id': 'changed_reason', 'turns': ['Había pedido turno por el hombro.', 'Ahora vengo por la rodilla izquierda.', 'Me duele desde ayer y puedo caminar.', 'Nada más.']},
    {'id': 'stale_conflict', 'profile': {'antecedentes': ['Rodilla derecha en un control anterior']},
     'turns': ['Me duele la rodilla izquierda.', 'No es la derecha, es la izquierda.', 'Desde hace dos días.', 'Nada más.']},
    {'id': 'urgent', 'turns': ['Después del golpe tengo los dedos azules y no siento la mano.']},
    {'id': 'urgent_paraphrase', 'turns': ['Me golpeé el brazo y ahora la mano se ve violeta y está helada.']},
    {'id': 'refusal', 'turns': ['Vengo por dolor de hombro.', 'Prefiero no responder eso.', 'Prefiero terminar.']},
    {'id': 'correction_completion', 'turns': ['Me torcí el tobillo ayer.', 'Me equivoqué, fue anteayer.', 'Puedo caminar.', 'Nada más.']},
    {'id': 'already_answered', 'turns': ['Me duele la rodilla izquierda desde ayer. Puedo caminar. No tomo medicación ni tengo alergias.', 'Ya te lo respondí.', 'Nada más.']},
]


class Budget:
    """Serial pre-request reservation; uncertain/failed attempts are not refunded.

    This limits this process, not the shared account's other callers. Monetary
    bounds rely on the provider honoring advertised max_tokens/max_price.
    Existing account guardrails stay in force and are never altered here.
    """
    def __init__(self, usd, models):
        self.limit = Decimal(str(usd))
        if not self.limit.is_finite() or not 0 < self.limit <= MAX_USD:
            raise ValueError('Authorize a positive budget no greater than USD 5.')
        self.models = set(models)
        self.reserved = Decimal('0')
        self.attempts = 0

    def reserve(self, body):
        model = body.get('model')
        if model not in self.models or model not in MODELS:
            raise ValueError('Model outside the authorized comparison.')
        spec = MODELS[model]
        if body.get('max_tokens') != spec['max_tokens'] or body.get('tools') or body.get('plugins'):
            raise ValueError('Unexpected generation budget or external tools.')
        messages = body.get('messages') or []
        if any(not isinstance(m.get('content'), str) for m in messages):
            raise ValueError('Comparison accepts synthetic text only.')
        units = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 256 * len(messages)
        if units > MAX_INPUT_UNITS:
            raise ValueError('Input exceeds the conservative comparison token bound.')
        # Reserve the FULL cap, not an optimistic estimate or reported usage.
        ceiling = (Decimal(MAX_INPUT_UNITS) * Decimal(spec['input'])
                   + Decimal(spec['max_tokens'] + 256) * Decimal(spec['output'])) / 1000000
        if self.attempts >= MAX_ATTEMPTS or self.reserved + ceiling > self.limit:
            raise ValueError('Authorized comparison budget exhausted before request.')
        self.attempts += 1
        self.reserved += ceiling
        body['provider'] = {'allow_fallbacks': False, 'require_parameters': True,
                            'max_price': {'prompt': float(spec['input']), 'completion': float(spec['output']), 'request': 0}}


def evaluate(model, case):
    from app.ai import ai_client, prompts
    from app.core import pii
    from app.modules.intake import health_profile, interview_agent as agent
    history, calls, replies = [], [], []
    real_call = ai_client.call
    spec = MODELS[model]

    def observe(body, **kwargs):
        start = time.monotonic()
        record = {'requested_model': body['model']}
        try:
            result = real_call(body, **kwargs)
            choice = (result.get('choices') or [{}])[0]
            visible = choice.get('message', {}).get('content') or ''
            failure = 'truncated' if choice.get('finish_reason') == 'length' else 'empty' if not visible.strip() else None
            record.update(returned_model=result.get('model'), usage=result.get('usage'), failure=failure,
                          finish_reason=choice.get('finish_reason'),
                          visible_reply=visible)
            return result
        except Exception as error:
            record['failure'] = type(error).__name__
            raise
        finally:
            record['latency_s'] = round(time.monotonic() - start, 3)
            calls.append(record)

    # All persistence and identity/catalog reads are replaced, not just writes.
    # The only permitted external dependency is the existing shared AI client.
    with ExitStack() as stack:
        for context in [
            patch.object(agent, '_load_history', side_effect=lambda key: copy.deepcopy(history)),
            patch.object(agent, '_save', side_effect=lambda key, msg: history.append(copy.deepcopy(msg))),
            patch.object(agent, '_close'), patch.object(agent, '_model', return_value=model),
            patch.object(agent, '_booking_context', return_value='\nSynthetic booked specialty: Traumatología; date: 2026-10-25.'),
            patch.object(agent, '_especialidad_for', return_value='Traumatología'),
            patch.object(agent, '_env', side_effect=lambda key, default='': {
                'TURNOS_FAKE_AI': '0', 'INTAKE_INTERVIEW_MAX_TOKENS': str(spec['max_tokens']),
                'INTAKE_INTERVIEW_REASONING_EFFORT': spec['effort']}.get(key, default)),
            patch.object(agent.ai_health, 'available', return_value=True), patch.object(agent.ai_health, 'report'),
            patch.object(agent.pii, 'for_dni', side_effect=lambda dni: pii.Scrubber()),
            patch.object(health_profile, 'leer', return_value={'datos': health_profile.normalizar(case.get('profile', {})), 'updated': None}),
            patch('app.ai.prompts.text', side_effect=lambda key, **kwargs: prompts.original(key).replace('{hospital}', 'Hospital de prueba').replace('{contexto}', kwargs.get('contexto', ''))),
            patch('app.core.site_identity.get', side_effect=lambda key: '107' if key == 'emergency_number' else 'Hospital de prueba'),
            patch.object(ai_client, 'call', side_effect=observe),
        ]:
            stack.enter_context(context)
        interview = {'id': 0, 'dni': 'synthetic-only', 'session_key': 'synthetic-only', 'entry_mode': 'post_booking'}
        replies.append(agent.run_turn(interview, ''))
        for utterance in case['turns']:
            result = agent.run_turn(interview, utterance)
            replies.append(result)
            if result['fin'] or (calls and calls[-1].get('failure')):
                break
    durations = [call['latency_s'] for call in calls]
    return {'case': case['id'], 'model': model, 'calls': calls,
            'median_latency_s': statistics.median(durations) if durations else None,
            'provider_calls': len(calls), 'transcript': history,
            'requires_human_semantic_review': True,
            'checks': {'one_question': all(reply['texto'].count('?') <= 1 for reply in replies),
                       'no_provider_failure': all(not call['failure'] for call in calls),
                       'scripted_turns_seen': sum(m['role'] == 'user' for m in history),
                       'scripted_turns_total': len(case['turns']),
                       'urgent_closed': (replies[-1]['fin'] and history[-1].get('intake_outcome') == 'urgent')
                                        if case['id'].startswith('urgent') else None}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--models', nargs='+', choices=MODELS, default=list(MODELS))
    parser.add_argument('--budget-usd', default='0', help='Explicit authorized spend cap, at most USD 5; required for --live.')
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({'network_calls': 0, 'models': {m: MODELS[m] for m in args.models}, 'cases': CASES,
                          'limits': {'usd': str(MAX_USD), 'http_attempts': MAX_ATTEMPTS,
                                     'input_units_per_call': MAX_INPUT_UNITS, 'serial': True},
                          'review': ['chief complaint first', 'relevant history only', 'corrections and provenance',
                                     'no repeated answered questions', 'no diagnosis/prescribing',
                                     'urgent escalation', 'uncertainty/refusal', 'editable factual close'],
                          'latency': 'unmeasured until authorized live execution'}, ensure_ascii=False, indent=2))
        return
    from app.ai import ai_client
    budget = Budget(args.budget_usd, args.models)
    if ai_client.profile() != 'cloud_shared' or not ai_client.available():
        raise SystemExit('Existing approved inference runtime unavailable; no calls made.')
    real_post = ai_client._post

    def bounded_post(body, api_key, timeout, headers_extra=None, base_url=None):
        if base_url != ai_client.OPENROUTER_URL:
            raise ValueError('Comparison requires the existing OpenRouter destination.')
        budget.reserve(body)  # Every HTTP attempt, including backup-key retry.
        result = real_post(body, api_key, timeout, headers_extra, base_url)
        usage = result.get('usage') or {}
        if not all(isinstance(usage.get(key), int) for key in ('prompt_tokens', 'completion_tokens')):
            raise ValueError('Missing provider accounting; stop and reconcile before another request.')
        if usage.get('prompt_tokens', 0) > MAX_INPUT_UNITS or usage.get('completion_tokens', 0) > body['max_tokens'] + 256:
            raise ValueError('Provider exceeded the advertised comparison bound; stop and reconcile cost.')
        return result

    # 402/403 do not trigger a backup key/account attempt for this comparison.
    # The current key/account is retained; no new credential is read/provisioned.
    with patch.object(ai_client, '_post', side_effect=bounded_post), patch.object(ai_client, '_KEY_FAILOVER_CODES', frozenset()):
        for model in dict.fromkeys(args.models):
            for case in CASES:
                result = evaluate(model, case)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if any(call.get('failure') for call in result['calls']):
                    raise SystemExit('Comparison stopped after failure/uncertain outcome; no retries.')
    print(json.dumps({'http_attempts': budget.attempts, 'reserved_max_usd': str(budget.reserved)}))


if __name__ == '__main__':
    main()
