"""Shared runtime model resolution; never a second inventory-only default."""
from app.core import config

DEFAULT_MODEL = 'google/gemini-3-flash-preview'
DEFAULT_FALLBACK_MODEL = 'google/gemma-4-31b-it:free'
DEFAULT_VOICE_MODEL = 'fal-ai/wizper'
VOICE_QUEUE_URL = 'https://queue.fal.run/'
VOICE_UPLOAD_URL = 'https://rest.alpha.fal.ai/storage/upload/initiate'


def model(function_id=None):
    if function_id == 'interview':
        return (config.get('INTAKE_INTERVIEW_MODEL') or config.get('TURNOS_AI_MODEL')
                or DEFAULT_MODEL)
    if function_id == 'health_profile':
        return (config.get('INTAKE_PROFILE_MODEL') or config.get('INTAKE_ANALYSIS_MODEL')
                or config.get('TURNOS_AI_MODEL') or DEFAULT_MODEL)
    return config.get('TURNOS_AI_MODEL') or DEFAULT_MODEL


def fallback_model():
    value = str(config.get('TURNOS_AI_MODEL_FALLBACK', DEFAULT_FALLBACK_MODEL) or '').strip()
    return '' if value.lower() in ('none', 'off', '0') else value


def disabled():
    return str(config.get('TURNOS_AI_DISABLED', '') or '') == '1'


def voice_model():
    value = str(config.get('TURNOS_VOICE_MODEL') or '').strip()
    model = value if value == DEFAULT_VOICE_MODEL else DEFAULT_VOICE_MODEL
    try:
        from app.core import db_catalog
        row = db_catalog.catalog_get('config', 'chat_voice')
        saved = str(((row or {}).get('doc') or {}).get('model') or '').strip()
        if saved == DEFAULT_VOICE_MODEL:
            model = saved
    except Exception:
        pass
    return model


def voice_enabled():
    return not disabled() and config.get('TURNOS_VOICE_ENABLED', '1') != '0'
