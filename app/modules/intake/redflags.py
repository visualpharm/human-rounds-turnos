"""redflags.py — escáner determinístico de banderas rojas sobre las
RESPUESTAS DEL PACIENTE de la entrevista previa (modules/intake/).

Este es el PISO de seguridad bajo la lectura de la IA (analysis.py): una
regla acá sólo puede ESCALAR el triage, nunca bajarlo — analysis.py combina
`triage = more_urgent(evaluate(...)['triage'], triage_ia)`. Mismo contrato
que el prototipo hermano <workspace>/sana (specialties/cardiologia/
redflags.mjs), portado genérico (no específico de una especialidad: acá
sólo hay texto libre del chat, no un cuestionario estructurado por
especialidad como en el prototipo).

Cada regla exige que TODOS sus "conceptos" (grupos de frases alternativas)
aparezcan en la MISMA ORACIÓN del texto del paciente — evita falsos
positivos como "tuve fiebre la semana pasada, y ayer se me durmió una
pierna de estar sentado" (fiebre y "se durmió" en oraciones distintas no
arman el cuadro de meningismo). Precisión antes que recall: un ROJO falso
erosiona la confianza del médico en el sistema más rápido que un ROJO
perdido lo hace quedar mal (hay un médico leyendo esto antes de la
consulta, no es la única red de seguridad).
"""

import re

from app.core.text_fold import fold_words as _norm

TRIAGE_ORDER = ["ROJO", "NARANJA", "AMARILLO", "VERDE"]


def more_urgent(a, b):
    """El más urgente de los dos triages (ROJO > NARANJA > AMARILLO >
    VERDE). Tolerante a None/basura: un valor que no está en TRIAGE_ORDER
    se trata como "menos urgente que cualquier valor válido" — si el otro
    también es inválido, devuelve `b` (mismo desempate que el prototipo
    hermano)."""
    ia = TRIAGE_ORDER.index(a) if a in TRIAGE_ORDER else -1
    ib = TRIAGE_ORDER.index(b) if b in TRIAGE_ORDER else -1
    if ia == -1:
        return b
    if ib == -1:
        return a
    return a if ia <= ib else b


def _sentences(text):
    """Oraciones (texto original, recortado) separadas por . ! ? o salto de
    línea — cada regla evalúa DENTRO de una oración, nunca a través de dos.
    Devuelve pares (original, normalizado); split ANTES de normalizar,
    porque _norm() convierte la puntuación en espacios y destruiría los
    separadores si se aplicara primero."""
    raw = [s.strip() for s in re.split(r"[.!?\n]+", str(text or "")) if s.strip()]
    return [(s, _norm(s)) for s in raw]


def _concept_hits(sentence, alternatives):
    """True si alguna frase de `alternatives` aparece en `sentence` (ya
    normalizada). Las alternativas también se normalizan, así la regla se
    escribe en español legible arriba."""
    return any(_norm(alt) in sentence for alt in alternatives)


# Cada regla: concepts = lista de "conceptos" (listas de frases
# alternativas); TODOS los conceptos deben aparecer en la misma oración
# para que la regla dispare. `because` = la oración original (recortada)
# que la disparó, así el médico ve el contexto, no sólo la etiqueta.
_RULES = [
    {
        "code": "dolor_pecho_reposo",
        "label": "Dolor torácico en reposo",
        "triage": "ROJO",
        "exams": ["ecg", "lab-troponina"],
        "concepts": [
            ["dolor"],
            ["pecho", "torax"],
            ["reposo", "sin hacer nada", "estando quieto", "quieto",
             "sentado sin moverme", "acostado sin moverme"],
        ],
    },
    {
        "code": "dolor_pecho_opresivo",
        "label": "Dolor torácico opresivo",
        "triage": "ROJO",
        "exams": ["ecg", "lab-troponina"],
        "concepts": [
            ["opresion", "opresivo", "aprieta", "como un peso", "apretado"],
            ["pecho", "torax"],
        ],
    },
    {
        "code": "falta_aire_reposo",
        "label": "Dificultad para respirar en reposo",
        "triage": "ROJO",
        "exams": ["ecg", "rx-torax"],
        "concepts": [
            ["falta de aire", "me ahogo", "no puedo respirar",
             "cuesta respirar", "dificultad para respirar"],
            ["reposo", "sin hacer nada", "estando quieto",
             "estando sentado", "estando acostado"],
        ],
    },
    {
        "code": "sincope",
        "label": "Desmayo o pérdida de conocimiento",
        "triage": "NARANJA",
        "exams": ["ecg", "ecocardiograma"],
        "concepts": [
            ["desmaye", "me desmaye", "desmayo", "sincope",
             "perdi el conocimiento", "perdida de conocimiento",
             "quede inconsciente"],
        ],
    },
    {
        "code": "sangrado_abundante",
        "label": "Sangrado abundante",
        "triage": "NARANJA",
        "exams": [],
        "concepts": [
            ["sangrado abundante", "sangro mucho", "no para de sangrar",
             "hemorragia", "perdida de sangre importante"],
        ],
    },
    {
        "code": "signos_acv",
        "label": "Posibles signos de ACV (cara, brazo o habla)",
        "triage": "ROJO",
        "exams": [],
        "concepts": [
            ["se me durmio la cara", "boca torcida", "cara caida",
             "un brazo no me responde", "no puedo mover el brazo",
             "no puedo mover un lado", "se me traba la lengua",
             "hablo raro", "arrastro las palabras",
             "no puedo hablar bien"],
        ],
    },
    {
        "code": "ideacion_suicida",
        "label": "Ideación suicida",
        "triage": "ROJO",
        "exams": [],
        "concepts": [
            ["quiero morirme", "me quiero morir", "no quiero vivir",
             "pense en matarme", "quiero matarme", "ganas de morir",
             "no vale la pena vivir"],
        ],
    },
    {
        "code": "fiebre_rigidez_nuca",
        "label": "Fiebre con rigidez de nuca",
        "triage": "NARANJA",
        "exams": [],
        "concepts": [
            ["fiebre"],
            ["rigidez de nuca", "no puedo doblar el cuello", "cuello duro",
             "dolor al mover el cuello"],
        ],
    },
    {
        "code": "dolor_abdominal_severo",
        "label": "Dolor abdominal severo",
        "triage": "NARANJA",
        "exams": ["eco-abdominal"],
        "concepts": [
            ["dolor de panza", "dolor abdominal", "dolor de estomago",
             "dolor en la panza", "dolor en el abdomen"],
            ["fuerte", "insoportable", "terrible", "intenso",
             "no aguanto", "muy fuerte"],
        ],
    },
]


def evaluate(user_texts):
    """Escanea las respuestas del paciente (lista de strings, o un único
    string) y devuelve {"triage": color-o-None, "flags": [...]}. `triage`
    es None si ninguna regla disparó (ver el módulo docstring: acá NO hay
    piso "sintomático = AMARILLO" — eso lo decide analysis.py, que sabe si
    hubo respuestas del paciente en absoluto, no este módulo)."""
    if isinstance(user_texts, str):
        user_texts = [user_texts]
    texto = " . ".join(str(t or "") for t in (user_texts or []) if str(t or "").strip())
    if not texto.strip():
        return {"triage": None, "flags": []}

    sentences = _sentences(texto)  # [(original, normalizado), ...]

    flags = []
    triage = None
    for rule in _RULES:
        because = None
        for original, norm in sentences:
            if all(_concept_hits(norm, alts) for alts in rule["concepts"]):
                because = original
                break
        if because is None:
            continue
        flags.append({
            "code": rule["code"],
            "label": rule["label"],
            "because": because[:200],
        })
        triage = more_urgent(triage, rule["triage"]) if triage else rule["triage"]

    return {"triage": triage, "flags": flags}


def exams_for(flags):
    """Ids de estudios sugeridos por las reglas que dispararon (unión, sin
    duplicados, en orden de aparición) — usado por el camino
    determinístico/fallback de analysis.py."""
    by_code = {r["code"]: r for r in _RULES}
    out, seen = [], set()
    for f in (flags or []):
        rule = by_code.get(f.get("code"))
        for e in (rule or {}).get("exams", []):
            if e not in seen:
                seen.add(e)
                out.append(e)
    return out
