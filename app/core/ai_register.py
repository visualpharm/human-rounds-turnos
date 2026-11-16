"""Product AI facts, not a certification or a provincial registry submission.

No domain imports, network calls, patient data or credentials. Runtime state
comes from the same config/model policy the inference callers use.
"""
from __future__ import annotations

import ast
import json
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from app.core import ai_policy, config, i18n, paths

_ACTOR = ContextVar('ai_register_actor', default='system')


@contextmanager
def actor(identity):
    token = _ACTOR.set(str((identity or {}).get('usuario') or 'system'))
    try:
        yield
    finally:
        _ACTOR.reset(token)


def fingerprint(value):
    raw = str(value or '').encode('utf-8')
    return {'sha256': sha256(raw).hexdigest(), 'length': len(raw)}


# Deliberately bump the owning entry when its behavior changes. Never hash
# source files or interpreter-dependent AST representations for this field.
FUNCTIONALITY_VERSIONS = {
    'referral': 1, 'interview': 2, 'triage': 3, 'health_profile': 2,
    'consult_documents': 2, 'chart_summary': 1, 'lab_reading': 1,
    'lab_upload': 1, 'booking_chat': 2, 'chat_memory': 1, 'mcp_assistant': 2,
    'dni_reading': 1, 'translation': 2, 'prompt_translation': 2,
    'knowledge_properties': 1, 'health_probe': 1, 'voice_transcription': 2,
    'case_chat': 1,
}

def _function(id_, purpose, data, oversight, notice, surface, prompts=(), exception=False,
              clinical=True, switches=()):
    return {'id': id_, 'functionality_version': FUNCTIONALITY_VERSIONS[id_], 'purpose': purpose, 'data_categories': list(data),
            'pii_handling': ('Texto seudonimizado/restaurado con core/pii.py cuando corresponde. Excepción deliberada: imagen/PDF completo; puede contener identidad.'
                             if exception else 'Texto seudonimizado/restaurado con core/pii.py; esfuerzo razonable.'),
            'human_oversight': oversight, 'risk_level': 'alto' if clinical else 'limitado',
            'risk_rationale': ('Contenido clínico o identidad: alto hasta evaluación del hospital.'
                               if clinical else 'Contenido institucional/técnico; sin decisiones individuales.'),
            'notice': {'key': notice, 'surface': surface},
            'kill_switch': ['TURNOS_AI_DISABLED=1', 'TURNOS_FAKE_AI=1 (flujos normales; semilla ficticia controlada puede exceptuar)', *switches],
            'first_deployed_date': None, 'first_deployed_evidence': 'No verificada por instalación.',
            'last_substantial_change_date': None, 'prompt_ids': list(prompts)}


FUNCTIONS = (
    _function('referral', 'Lectura de órdenes y prácticas solicitadas',
              ('imagen/PDF de orden', 'datos de salud', 'identidad y firma en el documento'),
              'Paciente verifica la lectura; admisión aprueba/rechaza/deriva en Solicitudes. La IA no decide.',
              'ai.notice.order', 'order upload / booking order step', ('orden_analisis', 'orden_estudios'), True),
    _function('interview', 'Entrevista previa y orientación ante urgencias',
              ('síntomas', 'antecedentes', 'conversación', 'foto opcional'),
              'Paciente aporta respuestas; médico revisa transcript en Consulta; no confirma turnos ni indica tratamiento.',
              'ai.notice.interview', 'interview start', ('entrevista_preguntas',), True,
              switches=('INTAKE_ENABLED (valor heredado, OFF por defecto; los overrides explícitos por médico ganan)', 'module intake off')),
    _function('triage', 'Resumen y prioridad sugerida de la entrevista',
              ('transcript seudonimizado', 'especialidad', 'catálogo clínico'),
              'Médico revisa en Consulta antes de confirmar; piso determinista de señales urgentes; sin cambios de turno.',
              'ai.notice.clinical', 'doctor consultation', switches=('module intake off',)),
    _function('health_profile', 'Borrador de memoria de salud relatada por el paciente',
              ('transcript', 'medicación relatada', 'antecedentes relatados'),
              'Propuesta separada por entrevista, visible en Consulta. Sólo Aplicar propuesta y la confirmación explícita del médico guardan el perfil revisado; la próxima entrevista usa sólo el confirmado.',
              'ai.notice.clinical', 'doctor consultation', switches=('module intake off',)),
    _function('consult_documents', 'Redacción de receta y orden a partir de selección humana',
              ('selección de diagnósticos', 'medicación', 'estudios', 'notas seudonimizadas'),
              'Sólo POST Confirmar del médico/admin genera documentos; revisión humana antes de firmar/usar. No escribe HSI.',
              'ai.notice.clinical', 'doctor consultation', ('entrevista_receta', 'entrevista_orden'),
              switches=('restore entrevista_receta / entrevista_orden', 'module intake off')),
    _function('chart_summary', 'Resumen de historia disponible para el profesional',
              ('historia clínica', 'laboratorio', 'entrevistas', 'órdenes', 'series numéricas'),
              'Profesional contrasta fuentes en Resumen/Consulta; guarda caché derivada, nunca una evolución clínica o decisión de turno.',
              'ai.notice.clinical', 'staff patient summary', ('resumen_paciente',),
              switches=('TURNOS_SUMMARY_PREGEN=0 (sólo daemon)',)),
    _function('lab_reading', 'Lectura de laboratorio y filtro de resultados sensibles',
              ('informes clínicos', 'valores', 'texto OCR seudonimizado'),
              'Paciente consulta al profesional; datos sensibles se remiten al médico. Sólo caché derivada, no diagnóstico ni escritura clínica.',
              'ai.notice.lab', 'patient laboratory', ('laboratorio_paciente', 'laboratorio_sensible')),
    _function('lab_upload', 'Transcripción de laboratorio aportado por el paciente',
              ('imagen/PDF completo', 'identidad y datos de salud en el documento'),
              'Paciente sube el archivo; médico verifica su procedencia. Se marca aportado por paciente, sin verificar.',
              'ai.notice.lab', 'patient laboratory upload', exception=True),
    _function('booking_chat', 'Asistente de servicios y solicitudes de turno por web y WhatsApp',
              ('mensajes', 'resultados de herramientas', 'datos administrativos', 'motivo de consulta'),
              'Paciente confirma el servicio, paciente y horario exactos antes de crear la solicitud. Reglas deterministas del servicio pueden confirmarla automáticamente; la IA nunca aprueba, rechaza ni cancela solicitudes pendientes.',
              'ai.notice.chat', 'web chat before composer / WhatsApp first reply', ('chat_sistema', 'chat_anonimo')),
    _function('chat_memory', 'Resumen y compactación de conversaciones',
              ('conversación seudonimizada', 'resúmenes previos'),
              'Memoria de conversación, no historia clínica. El paciente corrige y el operador revisa; no cambia turnos.',
              'ai.notice.chat', 'web chat / WhatsApp'),
    _function('mcp_assistant', 'Herramientas para un asistente externo conectado por MCP',
              ('identidad de cuenta/personas a cargo por token autorizado', 'datos de herramientas autorizadas', 'órdenes adjuntas', 'laboratorio'),
              'Cuenta autoriza conexión; el paciente confirma el horario exacto mediante el enlace de la herramienta. La IA externa nunca aporta autorización humana con un argumento. Cancelar sólo ofrece enlace humano.',
              'ai.notice.mcp', 'account assistant connection'),
    _function('dni_reading', 'Autocompletar el formulario desde foto del DNI',
              ('foto completa del DNI', 'nombre', 'domicilio', 'fecha de nacimiento'),
              'Usuario revisa y envía el formulario; OCR no autentica ni guarda la foto.',
              'ai.notice.dni', 'identity photo before upload', exception=True),
    _function('translation', 'Traducción de contenido institucional',
              ('textos institucionales', 'idioma de destino'),
              'Admin corrige traducciones; original sirve ante fallas. No decide sobre pacientes.',
              'ai.notice.translation', 'admin translation editor'),
    _function('prompt_translation', 'Traducción de instrucciones editables',
              ('instrucciones institucionales', 'idioma de edición'),
              'Admin guarda/restaura instrucciones y revisa diferencias; no se publican instrucciones del modelo sin su acción.',
              'ai.notice.prompt', 'admin prompt editor'),
    _function('knowledge_properties', 'Propuesta de propiedades de la wiki',
              ('artículo institucional', 'categorías', 'propiedades editoriales'),
              'Admin solicita propuesta, edita y guarda el artículo; propuesta no publica por sí sola.',
              'ai.notice.knowledge', 'admin knowledge editor'),
    _function('health_probe', 'Comprobación técnica de disponibilidad de IA',
              ('mensaje técnico sin datos personales',),
              'Admin ejecuta Probar; sonda no procesa pacientes ni cambia decisiones.',
              'ai.notice.probe', 'settings inference integration', clinical=False),
    _function('voice_transcription', 'Dictado por voz para chat y entrevista',
              ('audio de voz completo', 'síntomas o datos de identidad que se pronuncien'),
              'Paciente dicta y envía; puede revisar el texto. Enviar durante la grabación solicita enviarlo tras transcribir. No confirma turnos ni escribe una evolución clínica.',
              'ai.notice.voice', 'shared composer before microphone capture',
              switches=('TURNOS_VOICE_ENABLED=0', 'module chat off')),
    _function('case_chat', 'Preguntas sobre un caso sin turnos y síntesis para la mesa',
              ('conversación seudonimizada', 'motivo de consulta', 'datos de salud'),
              'La mesa lee la síntesis y, si hace falta, la conversación entera (queda en el registro de accesos) y decide si da un sobreturno. Un piso determinista indica guardia o 107 ante señales de urgencia; la IA no da ni niega turnos.',
              'ai.notice.case', 'booking no-slots step, first assistant message', ('caso_preguntas', 'caso_resumen'),
              switches=('module intake off', 'INTAKE_CASE_MAX_QUESTIONS')),
)

# Exact runtime inference sites, including shared wrapper callers. A new site
# must declare its purpose, not just reuse a filename's existing registration.
CALL_SITES = {
    'modules/chat/voice.py:_json_request': ('voice_transcription',),
    'modules/chat/voice.py:_fal_upload_audio': ('voice_transcription',),
    'modules/chat/voice.py:_fal_transcribe': ('voice_transcription',),
    'modules/chat/voice.py:transcribe_result': ('voice_transcription',),
    'scripts/translate_locales.py:_call': ('translation',),
    'ai/lab_upload.py:leer_resultado': ('lab_upload',),
    'ai/prompts.py:_call_text': ('prompt_translation',),
    'ai/prompts.py:translate_paragraphs': ('prompt_translation',),
    'ai/chart_summary.py:generate': ('chart_summary',),
    'ai/ai_health.py:probe': ('health_probe',),
    'ai/ai_referral.py:_call_openrouter': ('referral', 'dni_reading', 'lab_upload', 'health_probe'),
    'ai/ai_referral.py:analyze_referral': ('referral',),
    'ai/ai_referral.py:_read_once': ('referral', 'lab_upload'),
    'ai/ai_referral.py:analyze_referral_3pass': ('referral',),
    'ai/ai_referral.py:read_studies': ('referral',),
    'ai/lab_analysis.py:_llamar_ia': ('lab_reading',),
    'core/translate.py:_call_openrouter': ('translation',),
    'core/translate.py:_machine_translate': ('translation',),
    'core/translate.py:_machine_translate_batch': ('translation',),
    'modules/chat/chat_agent.py:_call_chat': ('booking_chat',),
    'modules/chat/chat_agent.py:_resumir_sesion': ('chat_memory',),
    'modules/chat/chat_agent.py:_compactar': ('chat_memory',),
    'modules/intake/health_profile.py:merge_from_interview': ('health_profile',),
    'modules/intake/interview_agent.py:_model_reply': ('interview', 'case_chat'),
    'modules/intake/case_chat.py:_ai_summary': ('case_chat',),
    'modules/intake/analysis.py:_call_openrouter': ('triage',),
    'modules/intake/analysis.py:analyze_interview': ('triage',),
    'modules/intake/consult_compose.py:_ai_rewrite': ('consult_documents',),
    'cuentas/ocr_dni.py:_leer': ('dni_reading',),
    'admin_panel/admin_content.py:knowledge_properties': ('knowledge_properties',),
}

# the application package's own prefix of a module name (`app.` once the code is a package)
_APP_PREFIX = __name__[: -len("core.ai_register")]


def _in_app(module):
    """`app.ai.ai_client` -> `ai.ai_client`: a module name inside the application package."""
    if module and _APP_PREFIX and module.startswith(_APP_PREFIX):
        return module[len(_APP_PREFIX):]
    return module


def scan_call_sites(root):
    """`file:function` of every inference call under the project `root`: the application's
    files named relative to its code directory (core/paths.py CODE_DIR), so a key reads
    `modules/intake/analysis.py:...` whatever the layout; tooling relative to `root`."""
    found = set()
    code_root = Path(root) / paths.CODE_DIR
    code_directories = ('ai', 'core', 'modules', 'cuentas', 'admin_panel', 'auth', 'ops', 'connectors')
    tool_directories = ('scripts', 'migrations', 'alembic', 'extensions')
    files = list(code_root.glob('*.py'))
    for directory in code_directories:
        files.extend((code_root / directory).rglob('*.py'))
    for directory in tool_directories:
        files.extend((Path(root) / directory).rglob('*.py'))

    def key_of(path):
        base = code_root if path.is_relative_to(code_root) else Path(root)
        return path.relative_to(base).as_posix()

    for path in files:
        tree = ast.parse(path.read_text())
        aliases = {'ai_client':'ai_client', 'ai_referral':'ai_referral', 'voice':'voice'}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    if imported.name in ('ai_client', 'ai_referral', 'voice'):
                        aliases[imported.asname or imported.name] = imported.name
                    elif _in_app(node.module) in ('ai.ai_client', 'ai.ai_referral', 'modules.chat.voice'):
                        aliases[imported.asname or imported.name] = node.module.split('.')[-1] + '.' + imported.name
            elif isinstance(node, ast.Import):
                for imported in node.names:
                    if _in_app(imported.name) in ('ai.ai_client', 'ai.ai_referral', 'modules.chat.voice'):
                        aliases[imported.asname or imported.name] = imported.name.split('.')[-1]
        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.scope = []
            def visit_FunctionDef(self, node):
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_Call(self, node):
                name = ast.unparse(node.func)
                for alias, target in sorted(aliases.items(), key=lambda item: -len(item[0])):
                    if name == alias or name.startswith(alias + '.'):
                        name = target + name[len(alias):]
                        break
                is_voice = (name in ('voice._fal_transcribe', 'voice._fal_upload_audio', 'voice._json_request') or
                            (key_of(path) == "modules/chat/voice.py" and
                             name in ('_fal_transcribe', '_fal_upload_audio', '_json_request', 'urllib.request.urlopen')))
                if is_voice or name in ('ai_client.call', 'ai_client._post', 'ai_referral._call_openrouter',
                                        'ai_referral._read_once', '_call_openrouter', '_call_text', '_read_once'):
                    found.add(key_of(path) + ':' + '.'.join(self.scope))
                self.generic_visit(node)
        Visitor().visit(tree)
    return found


def inventory(voice_model=None):
    from app.core import app_modules
    profile = str(config.get('AI_INFERENCE_PROFILE') or 'cloud_shared').strip().lower()
    host = 'openrouter.ai' if profile == 'cloud_shared' else urlsplit(
        str(config.get('AI_INFERENCE_BASE_URL') or '')).hostname
    rows = deepcopy(list(FUNCTIONS))
    for row in rows:
        row['provider'] = {'profile': profile, 'host': host,
                           'downstream': 'No verificado; ruteado por proveedor' if profile == 'cloud_shared' else 'Configurado por instalación'}
        row['model'] = ai_policy.model(row['id'])
        row['fallback_model'] = ai_policy.fallback_model() if row['id'] in ('referral', 'lab_upload', 'dni_reading') else None
        free_models = [m for m in (row['model'], row['fallback_model']) if m and ':free' in m]
        row['retention_risk'] = (_RETENTION_FREE + ', '.join(free_models)
                                 if free_models and profile == 'cloud_shared' else 'Retención y proveedor final no verificados; confirmar por instalación.')
        if row['id'] in ('translation', 'prompt_translation', 'health_probe'):
            row['pii_handling'] = 'Contenido institucional/público o técnico, no datos de pacientes. Se envía sin el filtro de identidad para conservar contactos y ejemplos.'
        owner = ('intake' if row['id'] in ('interview', 'triage', 'health_profile', 'consult_documents')
                 else 'chat' if row['id'] in ('booking_chat', 'chat_memory', 'mcp_assistant', 'voice_transcription')
                 else None if row['id'] in ('translation', 'health_probe') else 'turnos')
        row['enabled'] = not ai_policy.disabled() and (owner is None or app_modules.is_enabled(owner))
        if owner == 'intake':
            row['intake_default_enabled'] = str(config.get('INTAKE_ENABLED', '0')).strip() != '0'
            row['doctor_override_state'] = 'No verificado por médico; override gana sobre el valor heredado'
        row['runtime_state'] = ('deshabilitada' if not row['enabled'] else
                                'simulada' if config.get('TURNOS_FAKE_AI') == '1' else 'habilitada; disponibilidad no verificada')
        if row['id'] == 'mcp_assistant':
            readers = dict(row['provider'], model=row['model'])
            row['provider'] = {'profile': None, 'host': None, 'product_readers': readers,
                               'external_assistant': 'No verificado; elegido por el cliente conectado'}
            row['model'] = None
            row['enabled'] = app_modules.is_enabled('chat')
            row['runtime_state'] = ('herramientas habilitadas; IA externa no verificada' if row['enabled'] else 'deshabilitada')
            row['kill_switch'] = ['module chat off', 'TURNOS_AI_DISABLED=1 (sólo lectores IA del producto)']
            row['pii_handling'] = ('La conexión autorizada por token entrega datos de herramientas al cliente externo, '
                                   'incluida identidad mediante whoami. No se aplica pii.py a esa respuesta al cliente. '
                                   'Los lectores IA internos conservan el filtro/las excepciones documentadas.')
        if row['id'] == 'voice_transcription':
            row['model'] = voice_model or ai_policy.voice_model()
            row['provider'] = {'profile': 'fal_queue', 'host': urlsplit(ai_policy.VOICE_QUEUE_URL).hostname,
                               'upload_host': urlsplit(ai_policy.VOICE_UPLOAD_URL).hostname}
            row['enabled'] = ai_policy.voice_enabled() and app_modules.is_enabled('chat')
            row['runtime_state'] = 'deshabilitada' if not row['enabled'] else 'simulada' if config.get('TURNOS_FAKE_AI') == '1' else 'habilitada; disponibilidad no verificada'
            row['pii_handling'] = ('Excepción de transcripción: audio completo al proveedor/CDN Fal; '
                                   'no se puede redactar la voz antes de reconocerla. El texto del chat posterior sí usa pii.py.')
    return rows


# Display layer. The dossier (export, fingerprints, the printed sheet's
# canonical text) stays in Spanish exactly as the code above writes it; the
# panel shows it through the catalog. Each Spanish text maps to its catalog
# key, es.json holds the same Spanish (a test pins the two equal) and en.json
# the English. A purpose is `ai.function.<id>`, the oversight sentence
# `ai.oversight.<id>`; the retention warning is a prefix plus the model list.
_RETENTION_FREE = ('Modelo :free: retención/uso para entrenamiento y proveedor final no verificados. '
                   'Puede recibir imágenes completas con identidad; verificar política antes de habilitar. Modelos: ')

TEXT_KEYS = {
    'imagen/PDF de orden': 'ai.data.order_file',
    'datos de salud': 'ai.data.health_data',
    'identidad y firma en el documento': 'ai.data.document_identity_signature',
    'síntomas': 'ai.data.symptoms',
    'antecedentes': 'ai.data.history',
    'conversación': 'ai.data.conversation',
    'foto opcional': 'ai.data.optional_photo',
    'transcript seudonimizado': 'ai.data.transcript_pseudonymized',
    'especialidad': 'ai.data.specialty',
    'catálogo clínico': 'ai.data.clinical_catalog',
    'transcript': 'ai.data.transcript',
    'medicación relatada': 'ai.data.reported_medication',
    'antecedentes relatados': 'ai.data.reported_history',
    'selección de diagnósticos': 'ai.data.diagnosis_selection',
    'medicación': 'ai.data.medication',
    'estudios': 'ai.data.studies',
    'notas seudonimizadas': 'ai.data.notes_pseudonymized',
    'historia clínica': 'ai.data.clinical_record',
    'laboratorio': 'ai.data.laboratory',
    'entrevistas': 'ai.data.interviews',
    'órdenes': 'ai.data.referrals',
    'series numéricas': 'ai.data.numeric_series',
    'informes clínicos': 'ai.data.clinical_reports',
    'valores': 'ai.data.values',
    'texto OCR seudonimizado': 'ai.data.ocr_pseudonymized',
    'imagen/PDF completo': 'ai.data.full_file',
    'identidad y datos de salud en el documento': 'ai.data.document_identity_health',
    'mensajes': 'ai.data.messages',
    'resultados de herramientas': 'ai.data.tool_results',
    'datos administrativos': 'ai.data.administrative_data',
    'motivo de consulta': 'ai.data.visit_reason',
    'conversación seudonimizada': 'ai.data.conversation_pseudonymized',
    'resúmenes previos': 'ai.data.previous_summaries',
    'identidad de cuenta/personas a cargo por token autorizado': 'ai.data.account_identity',
    'datos de herramientas autorizadas': 'ai.data.authorized_tool_data',
    'órdenes adjuntas': 'ai.data.attached_referrals',
    'foto completa del DNI': 'ai.data.id_photo',
    'nombre': 'ai.data.name',
    'domicilio': 'ai.data.address',
    'fecha de nacimiento': 'ai.data.birth_date',
    'textos institucionales': 'ai.data.institutional_texts',
    'idioma de destino': 'ai.data.target_language',
    'instrucciones institucionales': 'ai.data.institutional_instructions',
    'idioma de edición': 'ai.data.editing_language',
    'artículo institucional': 'ai.data.institutional_article',
    'categorías': 'ai.data.categories',
    'propiedades editoriales': 'ai.data.editorial_properties',
    'mensaje técnico sin datos personales': 'ai.data.technical_message',
    'audio de voz completo': 'ai.data.full_audio',
    'síntomas o datos de identidad que se pronuncien': 'ai.data.spoken_identity',
    'TURNOS_FAKE_AI=1 (flujos normales; semilla ficticia controlada puede exceptuar)': 'ai.kill.fake_ai',
    'INTAKE_ENABLED (valor heredado, OFF por defecto; los overrides explícitos por médico ganan)': 'ai.kill.intake_enabled',
    'TURNOS_SUMMARY_PREGEN=0 (sólo daemon)': 'ai.kill.summary_pregen',
    'TURNOS_AI_DISABLED=1 (sólo lectores IA del producto)': 'ai.kill.ai_disabled_readers',
    'Texto seudonimizado/restaurado con core/pii.py cuando corresponde. Excepción deliberada: imagen/PDF completo; puede contener identidad.': 'ai.pii.exception',
    'Texto seudonimizado/restaurado con core/pii.py; esfuerzo razonable.': 'ai.pii.standard',
    'Contenido institucional/público o técnico, no datos de pacientes. Se envía sin el filtro de identidad para conservar contactos y ejemplos.': 'ai.pii.institutional',
    ('La conexión autorizada por token entrega datos de herramientas al cliente externo, '
     'incluida identidad mediante whoami. No se aplica pii.py a esa respuesta al cliente. '
     'Los lectores IA internos conservan el filtro/las excepciones documentadas.'): 'ai.pii.mcp',
    ('Excepción de transcripción: audio completo al proveedor/CDN Fal; '
     'no se puede redactar la voz antes de reconocerla. El texto del chat posterior sí usa pii.py.'): 'ai.pii.voice',
    'alto': 'ai.risk.high',
    'limitado': 'ai.risk.limited',
    'Contenido clínico o identidad: alto hasta evaluación del hospital.': 'ai.risk.rationale_clinical',
    'Contenido institucional/técnico; sin decisiones individuales.': 'ai.risk.rationale_institutional',
    'Retención y proveedor final no verificados; confirmar por instalación.': 'ai.retention.unverified',
    'deshabilitada': 'ai.state.disabled',
    'simulada': 'ai.state.simulated',
    'habilitada; disponibilidad no verificada': 'ai.state.enabled',
    'herramientas habilitadas; IA externa no verificada': 'ai.state.tools_enabled',
}
RETENTION_FREE_KEY = 'ai.retention.free'


def display(text, lang=None):
    """The catalog's wording for one canonical Spanish register text; a text
    the register does not know (a model id, an env name) is shown as is."""
    lang = lang or i18n.active()
    if text in TEXT_KEYS:
        return i18n.t_or(TEXT_KEYS[text], text, lang)
    if isinstance(text, str) and text.startswith(_RETENTION_FREE):
        return i18n.t_or(RETENTION_FREE_KEY, text, lang, models=text[len(_RETENTION_FREE):])
    return text


def localized(rows, lang=None):
    """The rows as the panel shows them in the visitor's language."""
    lang = lang or i18n.active()
    out = []
    for source in rows:
        row = dict(source)
        row['purpose'] = i18n.t_or('ai.function.' + row['id'], row['purpose'], lang)
        row['human_oversight'] = i18n.t_or('ai.oversight.' + row['id'], row['human_oversight'], lang)
        for field in ('pii_handling', 'risk_level', 'risk_rationale', 'retention_risk', 'runtime_state'):
            row[field] = display(row[field], lang)
        for field in ('data_categories', 'kill_switch'):
            row[field] = [display(item, lang) for item in row[field]]
        out.append(row)
    return out


def notice(key):
    errors = {'ai.register.unavailable': 'Registro no disponible; pendientes no verificados',
              'ai.register.invalid_notification': 'No se pudo registrar. Revisá la referencia y la fecha.'}
    fallback = NOTICES[key][0] if key in NOTICES else errors.get(key, 'Registro no disponible')
    return i18n.t_or(key, fallback, i18n.active())


NOTICES = {
    'ai.notice.voice': ('El dictado usa inteligencia artificial. Tu audio completo se envía al proveedor; puede incluir lo que te identifica.',
                        'Voice dictation uses AI. Your complete audio is sent to the provider and may include identifying details.'),
    'ai.notice.chat': ('Este chat usa inteligencia artificial. Puede equivocarse. Vos confirmás el horario; las reglas del servicio y el equipo de salud gestionan el turno.',
                       'This chat uses AI and can make mistakes. You confirm the slot; service rules and health staff manage the appointment.'),
    'ai.notice.interview': ('Esta entrevista usa inteligencia artificial. El médico lee tus respuestas durante la consulta: esto no la reemplaza ni da consejos médicos.',
                            'This interview uses AI. It does not give medical advice; staff review your answers.'),
    'ai.notice.order': ('La inteligencia artificial lee tu orden. La imagen completa se envía al proveedor; el equipo revisa la solicitud.',
                        'AI reads your referral. The complete image is sent to the provider; staff review the request.'),
    'ai.notice.lab': ('Estos resultados se leen con inteligencia artificial. Consultalos con tu médico. Si subís una imagen, se envía completa al proveedor.',
                      'These results are read using AI. Discuss them with your clinician. Uploaded images are sent in full to the provider.'),
    'ai.notice.clinical': ('Contenido elaborado con inteligencia artificial. Revisá las fuentes antes de confirmar o usarlo en la atención.',
                           'AI-generated content. Review sources before confirming or using it in care.'),
    'ai.notice.mcp': ('El asistente conectado usa inteligencia artificial y recibe tu identidad y los datos de las herramientas que autorizás. Revisá sus propuestas antes de actuar.',
                      'The connected assistant uses AI and receives authorized tool data. Review its proposals before acting.'),
    'ai.notice.dni': ('La inteligencia artificial lee la foto completa del DNI en un proveedor externo. Revisá los datos antes de registrarte.',
                      'AI reads the complete ID photo through an external provider. Review the data before registering.'),
    'ai.notice.translation': ('La inteligencia artificial propone traducciones. Revisalas antes de publicar.', 'AI proposes translations. Review before publishing.'),
    'ai.notice.prompt': ('La inteligencia artificial traduce las instrucciones editadas. Revisá los cambios antes de usarlos.', 'AI translates edited instructions. Review the changes before use.'),
    'ai.notice.knowledge': ('La inteligencia artificial propone propiedades del artículo. Revisalas antes de guardar.', 'AI proposes article properties. Review before saving.'),
    'ai.notice.case': ('Esta conversación usa inteligencia artificial. No da consejos médicos: el equipo del hospital lee tu caso y decide el turno.',
                       'This conversation uses AI. It gives no medical advice: hospital staff read your case and decide the appointment.'),
    'ai.notice.probe': ('Probar envía un mensaje técnico a la inteligencia artificial, sin datos de pacientes.', 'Test sends a technical message to AI without patient data.'),
}


def setting_events(before, after):
    events = []
    fields = {'TURNOS_AI_MODEL': 'model', 'TURNOS_AI_MODEL_FALLBACK': 'model',
              'INTAKE_PROFILE_MODEL': 'model', 'INTAKE_ANALYSIS_MODEL': 'model',
              'INTAKE_INTERVIEW_MODEL': 'model',
              'INTAKE_INTERVIEW_MAX_TOKENS': 'model', 'INTAKE_INTERVIEW_REASONING_EFFORT': 'model',
              'AI_INFERENCE_PROFILE': 'provider', 'AI_INFERENCE_BASE_URL': 'provider',
              'TURNOS_AI_DISABLED': 'enabled', 'TURNOS_FAKE_AI': 'enabled',
              'INTAKE_ENABLED': 'enabled', 'TURNOS_FAKE_CHAT_AI': 'enabled',
              'ENABLED_MODULES': 'enabled', 'TURNOS_SUMMARY_PREGEN': 'enabled'}
    fields.update({'TURNOS_VOICE_MODEL':'model', 'TURNOS_VOICE_ENABLED':'enabled'})
    for key, kind in fields.items():
        if before.get(key) == after.get(key):
            continue
        ids = (('voice_transcription',) if key.startswith('TURNOS_VOICE_') else
               ('interview',) if key.startswith('INTAKE_INTERVIEW_') else
               ('health_profile',) if key == 'INTAKE_PROFILE_MODEL' else tuple(f['id'] for f in FUNCTIONS))
        if key in ('TURNOS_AI_MODEL', 'TURNOS_AI_MODEL_FALLBACK', 'AI_INFERENCE_PROFILE', 'AI_INFERENCE_BASE_URL'):
            ids = tuple(fid for fid in ids if fid not in ('mcp_assistant', 'voice_transcription'))
        if key == 'TURNOS_AI_MODEL_FALLBACK':
            ids = ('referral', 'dni_reading', 'lab_upload')
        if key in ('TURNOS_AI_DISABLED', 'TURNOS_FAKE_AI'):
            ids = tuple(fid for fid in ids if fid != 'mcp_assistant')
        for fid in ids:
            events.append({'function_id': fid, 'kind': kind, 'actor': _ACTOR.get(),
                           'substantial': kind in ('model', 'provider'),
                           'details': {'field': key, 'before': fingerprint(before.get(key)),
                                       'after': fingerprint(after.get(key))}})
    return events


def prompt_events(key, before, after, who=None):
    if before == after:
        return []
    return [{'function_id': f['id'], 'kind': 'prompt', 'actor': who or _ACTOR.get(),
             'substantial': True, 'details': {'prompt_id': key,
                                             'before': fingerprint(before) if before is not None else None,
                                             'after': fingerprint(after) if after is not None else None,
                                             'before_source': 'canonical instructions' if before is not None else 'canonical source; no human override',
                                             'text_language': 'source instructions; Spanish when supplied'}}
            for f in FUNCTIONS if key in f['prompt_ids']]


def doctor_enabled_events(scope, before, after):
    if before == after:
        return []
    return [{'function_id': fid, 'kind': 'enabled', 'actor': _ACTOR.get(), 'substantial': False,
             'details': {'field': 'entrevista_habilitada', 'scope': fingerprint(scope),
                         'before': fingerprint(before), 'after': fingerprint(after)}}
            for fid in ('interview', 'triage', 'health_profile', 'consult_documents')]


def voice_model_events(before, after):
    if before == after:
        return []
    return [{'function_id':'voice_transcription','kind':'model','actor':_ACTOR.get(),'substantial':True,
             'details':{'field':'chat_voice.model','before':fingerprint(before) if before is not None else None,
                        'after':fingerprint(after)}}]


def inventory_state(row):
    return {key: fingerprint(json.dumps(value, sort_keys=True, ensure_ascii=False)) for key, value in {
        'model': [row['model'], row['fallback_model']],
        'provider': ({'external_assistant': 'unknown'} if row['id'] == 'mcp_assistant' else row['provider']),
        'enabled': [row['enabled'], row['runtime_state']],
        'data_scope': row['data_categories'],
        'functionality': row['functionality_version'],
    }.items()}


def observe_inventory(rows, connection=None, record_changes=True):
    """Explicit startup refresh, or atomic write-site snapshot update.

    No source parsing. Observation is evidence of runtime state, not proof of
    deployment time. Write sites already append actor-attributed events and
    update the snapshot in that same transaction to avoid duplicate notices.
    """
    from sqlalchemy import exists, func, select
    from app.core import db, sqlcore
    from app.core import db_ai
    from app.core.schema_model import ai_register_changes as changes
    with (db._connect() if connection is None else nullcontext(connection)) as conn, conn.cursor() as cur:
        sqlcore.run(cur, select(func.pg_advisory_xact_lock(func.hashtext('ai_register:observation'))))
        had_baseline = sqlcore.run(cur, select(exists().where(changes.c.kind == 'snapshot').label('present'))
                                   ).fetchone()['present']
        for row in rows:
            state = inventory_state(row)
            previous = sqlcore.run(cur, select(changes.c.details).where(
                changes.c.function_id == row['id'], changes.c.kind == 'snapshot',
            ).order_by(changes.c.id.desc()).limit(1)).fetchone()
            old = previous['details'] if previous else None
            if old == state:
                continue
            events = []
            if record_changes and not old and had_baseline:
                events.append({'function_id': row['id'], 'kind': 'functionality',
                               'actor': 'system:observed', 'substantial': True,
                               'details': {'source': 'new function observed after initial inventory',
                                           'before': None, 'after': state['functionality']}})
            if record_changes and old:
                for field, value in state.items():
                    if old.get(field) != value:
                        events.append({'function_id': row['id'], 'kind': field,
                                       'actor': 'system:observed', 'substantial': field != 'enabled',
                                       'details': {'source': 'runtime observation; deployment time/actor unknown',
                                                   'before': old.get(field), 'after': value}})
            events.append({'function_id': row['id'], 'kind': 'snapshot',
                           'actor': 'system:observed', 'substantial': False, 'details': state})
            db_ai.ai_register_append(events, connection=conn)


def dossier():
    from app.core import db, tz
    from app.core import db_ai
    rows = inventory()
    changes = db_ai.ai_register_list_changes()
    baselined = {c['function_id'] for c in changes if c['kind'] == 'snapshot'}
    if not {r['id'] for r in rows} <= baselined:
        raise db.DbUnavailable('AI register startup refresh not completed')
    for change in changes:
        for field in ('changed_at', 'notified_at'):
            if change.get(field) is not None:
                change[field] = change[field].astimezone(tz.tzinfo())
    for row in rows:
        latest = next((c for c in changes if c['function_id'] == row['id'] and c['substantial']), None)
        row['last_substantial_change_date'] = latest['changed_at'].isoformat() if latest else None
        row['last_substantial_change_evidence'] = 'Hora de escritura/observación, no prueba de despliegue.'
    return {'características': rows,
            'estado_actual': {'registration': 'No acreditada por el producto',
                             'pending_notifications': sum(c['status'] == 'pending' for c in changes),
                             'human_attestation_only': True},
            'modificaciones': [c for c in changes if c['kind'] != 'snapshot']}
