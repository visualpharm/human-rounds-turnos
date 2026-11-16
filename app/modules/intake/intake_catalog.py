"""intake_catalog.py — estudios/diagnósticos/medicamentos de la entrevista
previa (modules/intake/).

Tres catálogos chicos, base pack (packs/global/intake-estudios.json,
intake-diagnosticos.json, intake-medicamentos.json), resueltos SIEMPRE vía
sitedata.path() (nunca un os.path.join a mano — ver docs/architecture/
data-packs.md, es un archivo entero por ahora, no compuesto: el día que una
localidad quiera ampliarlo, gana con su propio archivo vía path()/override,
igual que estudios-preparacion.json).

Cada entrada trae `especialidades: [str]` — vacía = genérica (aparece para
cualquier especialidad), no vacía = sólo para esas especialidades. El
filtro compara normalizado (sin acentos, minúsculas — misma idea que
ai_referral._norm) porque el catálogo especialidades.json usa nombres con
acento ("Cardiología") y estas listas usan el slug ascii ("cardiologia").

Nunca lanza: un archivo faltante o corrupto devuelve listas vacías (el
médico ve un catálogo vacío, no una consulta rota) — mismo contrato que
catalog._load_json, pero degradando a [] en vez de None.
"""

import os
import json

from app.core import sitedata
from app.core import text_fold

_ESTUDIOS_FILE = "intake-estudios.json"
_DIAGNOSTICOS_FILE = "intake-diagnosticos.json"
_MEDICAMENTOS_FILE = "intake-medicamentos.json"

# path -> (mtime, data) — cache keyed by the RESOLVED path, not the bare
# filename (see data-packs.md's "Cache-key contract": a filename-only key
# can't tell "the file the global pack ships" apart from an override).
_CACHE = {}


def _load(fname):
    path = sitedata.path(fname)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        data = {}
    _CACHE[path] = (mtime, data)
    return data


def _matches(especialidades, especialidad):
    """True si la entrada es genérica (especialidades vacía) o si
    `especialidad` normalizada aparece en su lista (también normalizada).
    `especialidad=None` (aún sin médico asignado, o no se filtra) trae
    SOLO las entradas genéricas."""
    tags = [t for t in (especialidades or []) if str(t or "").strip()]
    if not tags:
        return True
    if not especialidad:
        return False
    # minúsculas, sin acentos: 'Cardiología' y 'cardiologia' son la misma especialidad
    target = text_fold.plain(especialidad).strip()
    return any(text_fold.plain(t).strip() == target for t in tags)


def _filtered(fname, list_key, especialidad):
    data = _load(fname)
    items = data.get(list_key)
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)
            and _matches(it.get("especialidades"), especialidad)]


def exams(especialidad=None):
    """Estudios visibles para `especialidad` (genéricos + los tageados con
    esa especialidad); sin `especialidad`, sólo los genéricos."""
    return _filtered(_ESTUDIOS_FILE, "estudios", especialidad)


def diagnoses(especialidad=None):
    """Diagnósticos visibles para `especialidad`, mismo filtro que exams()."""
    return _filtered(_DIAGNOSTICOS_FILE, "diagnosticos", especialidad)


def medications(especialidad=None):
    """Medicamentos visibles para `especialidad`, mismo filtro que exams()."""
    return _filtered(_MEDICAMENTOS_FILE, "medicamentos", especialidad)


def frequencies():
    """Lista compartida de frecuencias típicas (intake-medicamentos.json's
    `frecuencias`) para el desplegable de la receta."""
    data = _load(_MEDICAMENTOS_FILE)
    freqs = data.get("frecuencias")
    return [str(f) for f in freqs] if isinstance(freqs, list) else []
