"""analysis.py — fase 3: triage + resumen clínico de la entrevista previa
para el médico (modules/intake/).

interview_agent.run_turn() llama analyze_interview(interview) best-effort
apenas la entrevista cierra ([fin] o el tope de turnos), adentro de un
try/except — el chat del paciente nunca debe fallar por esto (ver el
docstring de interview_agent._close).

UNA sola lectura con Gemini 3 Flash vía OpenRouter (mismo patrón que
ai_referral.py: response_format json_object, temperatura baja, ai_client.call
con el failover primary/backup incluido) + un escáner determinístico de
banderas rojas sobre las respuestas del paciente (redflags.py) que actúa
como PISO de seguridad: el triage final es
`redflags.more_urgent(scan_triage, triage_ia)` — la IA puede escalar, nunca
bajar por debajo de lo que las reglas ya detectaron.

TURNOS_FAKE_AI=1 (o sin API key) -> _deterministic_result(), sin red. La
MISMA función es el fallback cuando la llamada real falla (una sola forma
de construir "algo que el médico pueda leer", dos callers) — nunca se
levanta una excepción hacia interview_agent.

La demo pública conserva ese bloqueo para cualquier dato que escriba un
visitante. Su seed de Sofía Herrera, que contiene datos enteramente
ficticios y curados en código, puede pedir explícitamente UNA lectura real
con `allow_network_when_fake=True`; ningún flujo HTTP expone ese override.

El prompt es una constante en inglés, deliberadamente NO editable desde el
panel (misma razón que los prompts de ocr_dni.py: es lectura estructurada,
no una redacción de cara al paciente/staff — los 3 prompts editables del
módulo intake, si llegan, son la pregunta/orden/receta de una fase
posterior).

Entrevista sin contenido clínico (2026-08-02): si el paciente no respondió
nada, o respondió pero siempre fuera de tema (`interview_agent.
sin_contenido_clinico`), analyze_interview() nunca lo lee como una consulta
resuelta — ni VERDE ni un resumen que suene a "no tiene nada". El escáner de
banderas rojas igual corre sobre lo que el paciente sí escribió, aunque
fuera off-topic: el piso de seguridad no se salta ni en este camino."""

import sys

from app.core import config, pii
from app.core import theme
from app.ai import ai_client

from app.modules.intake import redflags, intake_catalog, interview_agent, health_profile

from app.core.ai_policy import DEFAULT_MODEL
from app.core import log_pii
from app.ai.json_extract import extract_object as _extract_json

_TEMP = 0.15
_CANAL = "interview"


def _env(key, default=""):
    return config.get(key, default)


def _model():
    from app.core import ai_policy
    return ai_policy.model()


def _log(msg):
    print(f"[intake.analysis] {msg}", file=sys.stderr, flush=True)


# ---- lectura de la entrevista ----------------------------------------------

# The assigned doctor's specialty is read the way the interview agent reads it (a post-booking
# interview may have no doctor yet; a catalog that is down never breaks the analysis).
_especialidad_for = interview_agent._especialidad_for


def _transcript(interview):
    try:
        from app.core import db_chat
        clave = (interview or {}).get("session_key") or f"{_CANAL}:{(interview or {}).get('id')}"
        return db_chat.chat_history(_CANAL, clave, 200) or []
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo leer el transcript: {log_pii.exc(e)}")
        return []


def _user_texts(historial):
    from .dialogue import text
    return [text(m.get("content")).strip() for m in (historial or [])
            if m.get("role") == "user" and text(m.get("content")).strip()]


def _qa_lines(historial):
    """'pregunta -> respuesta' por cada intercambio (empareja cada mensaje
    de usuario con la pregunta del asistente que lo precedió)."""
    lines = []
    pregunta = None
    for m in (historial or []):
        role = m.get("role")
        from .dialogue import text
        content = text(m.get("content")).strip()
        if not content:
            continue
        if role == "assistant":
            pregunta = content
        elif role == "user":
            if pregunta:
                lines.append(f"{pregunta} -> {content}")
                pregunta = None
            else:
                lines.append(f"(sin pregunta previa) -> {content}")
    return lines


# ---- camino determinístico (fake / fallback) --------------------------------

def _fallback_brief(scan):
    if scan["flags"]:
        return [{"priority": i + 1, "point": f"{f['label']} — {f['because']}"}
                for i, f in enumerate(scan["flags"][:6])]
    return [{"priority": 1, "point": "Evaluación automática incompleta. Revisión clínica necesaria; "
                                      "las reglas no descartan una urgencia."}]


def _deterministic_result(user_texts, ai_model):
    """Sin red: usado con TURNOS_FAKE_AI=1 Y como fallback cuando la
    llamada real al modelo falla — una sola función, dos callers (ver el
    docstring del módulo)."""
    scan = redflags.evaluate(user_texts)
    triage = scan["triage"] or "AMARILLO"
    summary = user_texts[0][:120] if user_texts else "Sin síntomas relatados"
    return {
        "triage": triage,
        "summary": summary,
        "brief": [{"priority": 0, "point": "Evaluación automática incompleta; revisar en la consulta."}]
                 + _fallback_brief(scan),
        "differential": [],
        "suggested_exams": redflags.exams_for(scan["flags"]),
        "red_flags": scan["flags"],
        "ai_model": ai_model,
    }


# ---- prompt + lectura real ---------------------------------------------------

_PROMPT_TMPL = """You are a specialist doing waiting-room triage, about to hand a colleague \
a brief before they see the patient. You read the patient's own pre-consult chat transcript \
below and prepare that brief. You never talked to the patient directly — everything you know \
is in the transcript.

PATIENT TRANSCRIPT (question -> answer):
{transcript}

DETERMINISTIC RED-FLAG SCAN (rule-based, over the patient's own words). You may escalate the \
triage ABOVE what these already found, but you must NEVER go below it:
{scan_block}

AVAILABLE EXAM IDS (use ONLY these ids in suggested_exams — id = label):
{exams_block}

AVAILABLE DIAGNOSIS IDS (use ONLY these ids in differential — id = label):
{dx_block}

Return ONLY a valid JSON object, no extra text, with exactly this shape:
{{
  "one_liner": "a short Spanish sentence (<=140 chars) a colleague can read in a few seconds, summarizing why the patient is here",
  "triage": "ROJO | NARANJA | AMARILLO | VERDE",
  "brief": [{{"priority": 1, "point": "most clinically important first"}}],
  "differential": [{{"id": "<diagnosis id from the list above>", "prob": 0.0}}],
  "suggested_exams": ["<exam id from the list above>"]
}}

"brief" is a reverse-pyramid Spanish clinical brief (whatever puts the patient at risk comes \
first, then the rest), at most 6 points. "differential" is a probability-ordered differential \
diagnosis using ONLY the ids listed above (probabilities between 0 and 1, roughly summing to \
1 but that is not strict). Every string VALUE in the JSON must be written in Spanish. Be \
concise and clinical."""


def _scan_block(scan):
    if not scan["flags"]:
        return "(ninguna detectada por las reglas)"
    return "\n".join(f"- [{scan['triage']}] {f['label']} ({f['because']})" for f in scan["flags"])


def _catalog_block(items, label_key="label"):
    if not items:
        return "(sin catálogo disponible)"
    return "\n".join(f"{it.get('id')} = {it.get(label_key)}" for it in items)


def _build_prompt(qa_lines, scan, exams, diagnoses):
    return _PROMPT_TMPL.format(
        transcript="\n".join(qa_lines) if qa_lines else "(sin respuestas)",
        scan_block=_scan_block(scan),
        exams_block=_catalog_block(exams),
        dx_block=_catalog_block(diagnoses),
    )


def _coerce(obj, exam_ids, dx_ids):
    triage = str((obj or {}).get("triage") or "").strip().upper()
    if triage not in redflags.TRIAGE_ORDER:
        triage = None

    one_liner = str((obj or {}).get("one_liner") or "").strip()[:140]

    brief = []
    raw_brief = (obj or {}).get("brief")
    if isinstance(raw_brief, list):
        for i, b in enumerate(raw_brief):
            if not isinstance(b, dict):
                continue
            point = str(b.get("point") or "").strip()
            if not point:
                continue
            try:
                priority = int(b.get("priority"))
            except (TypeError, ValueError):
                priority = i + 1
            brief.append({"priority": priority, "point": point})
        brief.sort(key=lambda x: x["priority"])
        brief = brief[:6]

    differential = []
    raw_diff = (obj or {}).get("differential")
    if isinstance(raw_diff, list):
        for d in raw_diff:
            if not isinstance(d, dict):
                continue
            did = str(d.get("id") or "").strip()
            if did not in dx_ids:
                continue
            try:
                prob = float(d.get("prob"))
            except (TypeError, ValueError):
                prob = 0.0
            prob = max(0.0, min(1.0, prob))
            if prob <= 0:
                continue
            differential.append({"id": did, "prob": prob})
        differential.sort(key=lambda x: -x["prob"])

    suggested_exams = []
    raw_exams = (obj or {}).get("suggested_exams")
    if isinstance(raw_exams, list):
        for e in raw_exams:
            eid = str(e or "").strip()
            if eid in exam_ids and eid not in suggested_exams:
                suggested_exams.append(eid)

    return {"one_liner": one_liner, "triage": triage, "brief": brief,
            "differential": differential, "suggested_exams": suggested_exams}


def _call_openrouter(prompt, model, timeout=45):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": _TEMP,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    return ai_client.call(
        body,
        timeout=timeout,
        headers_extra={"X-Title": theme.brand_name() + " - análisis de entrevista previa"},
    )


def _record_cost(cost, model, recognized):
    try:
        from app.ai import ai_costs
        ai_costs.record("analizar_entrevista", cost_usd=cost or 0.0, model=model, recognized=recognized)
    except Exception:  # noqa: BLE001 — analítica, nunca debe romper el análisis
        pass


def _persist(interview, result):
    try:
        from app.core import db_clinical
        # Re-analysis nunca baja un triage ya persistido (escalation-only).
        previo = (interview or {}).get("triage")
        if previo in redflags.TRIAGE_ORDER:
            result["triage"] = redflags.more_urgent(previo, result["triage"])
        db_clinical.interview_update_analysis(
            interview.get("id"), result["triage"], result["summary"], result["brief"],
            result["differential"], result["suggested_exams"], result["red_flags"],
            result["ai_model"],
        )
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo guardar el análisis de la entrevista {interview.get('id')}: {log_pii.exc(e)}")


# ---- entrypoint ---------------------------------------------------------------

def analyze_interview(interview, allow_network_when_fake=False):
    """Corre el análisis de UNA entrevista y lo persiste. Nunca lanza — toda
    falla (IA, catálogo, DB) degrada, en el peor caso, a no persistir nada
    (el `except` de interview_agent._close ya cubre esto también, pero acá
    adentro cada paso tiene su propio best-effort).

    `allow_network_when_fake` existe sólo para analizar el cuestionario
    ficticio, versionado, de la demo. El default False conserva el contrato
    estricto de TURNOS_FAKE_AI para entrevistas reales/de visitantes."""
    try:
        historial = _transcript(interview)
        user_texts = _user_texts(historial)

        # A stop/turn cap/repetition guard is not a medically complete interview.
        # Preserve any urgent floor recorded by the patient-facing loop.
        outcomes = [m.get('intake_outcome') for m in historial if m.get('role') == 'assistant']
        if 'urgent' in outcomes:
            interview = {**interview, 'triage': 'ROJO'}
        if outcomes and outcomes[-1] == 'incomplete':
            result = _deterministic_result(user_texts, 'entrevista_incompleta')
            _persist(interview, result)
            return result

        # Entrevista vacía de contenido clínico: o el paciente no llegó a
        # responder nada, o respondió pero siempre fuera de tema (mismo
        # criterio que interview_agent.sin_contenido_clinico usa para no
        # cerrar de entrada — acá es la otra mitad del mismo bug: si de
        # todos modos se cerró así, ver 2026-08-02, el resumen NUNCA puede
        # leerse como una entrevista normal completa. VERDE + "sin síntomas
        # relatados" sonaba a "todo bien" cuando en realidad no se sabe
        # nada — un VERDE silencioso se hunde al fondo de la cola del
        # médico. Igual corre el escáner de banderas rojas sobre lo que
        # SÍ escribió el paciente (aunque haya sido "fuera de tema"): el
        # piso de seguridad nunca se salta, ni siquiera acá.
        if not user_texts or interview_agent.sin_contenido_clinico(historial):
            scan = redflags.evaluate(user_texts) if user_texts else {"triage": None, "flags": []}
            if user_texts:
                summary = ("El paciente no llegó a contarle nada clínico al "
                           "asistente: la charla se fue del tema hasta que "
                           "la entrevista se cerró.")
            else:
                summary = "El paciente no respondió nada antes de que la entrevista se cerrara."
            result = {
                "triage": scan["triage"] or "AMARILLO",
                "summary": summary,
                "brief": _fallback_brief(scan) if scan["flags"] else [
                    {"priority": 1, "point": "El paciente no aportó información "
                                              "clínica — revisar en la consulta."}],
                "differential": [],
                "suggested_exams": redflags.exams_for(scan["flags"]),
                "red_flags": scan["flags"],
                "ai_model": "sin_contenido_clinico",
            }
            _persist(interview, result)
            return result

        fake = _env("TURNOS_FAKE_AI") == "1" and not allow_network_when_fake
        tiene_api_key = ai_client.available()
        if fake or not tiene_api_key:
            result = _deterministic_result(user_texts, "fake" if fake else "sin_clave_api")
            _persist(interview, result)
            return result

        especialidad = _especialidad_for(interview)
        exams = intake_catalog.exams(especialidad)
        diagnoses = intake_catalog.diagnoses(especialidad)
        exam_ids = {e.get("id") for e in exams}
        dx_ids = {d.get("id") for d in diagnoses}

        scan = redflags.evaluate(user_texts)
        qa_lines = _qa_lines(historial)
        anon = pii.for_dni(interview.get("dni"))
        anon.learn_person({'nombre': interview.get('entered_name'),
                           'apellido': interview.get('entered_surname')})
        prompt = anon.scrub(_build_prompt(qa_lines, scan, exams, diagnoses))
        model = _model()

        try:
            data = _call_openrouter(prompt, model)
        except Exception as e:  # noqa: BLE001 — red/timeout/HTTP: degradar, nunca lanzar
            _log(f"llamada al modelo falló ({interview.get('id')}): {log_pii.exc(e)}")
            result = _deterministic_result(user_texts, "fallback")
            _persist(interview, result)
            return result

        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            result = _deterministic_result(user_texts, "fallback")
            _persist(interview, result)
            return result

        cost = 0.0
        try:
            c = (data.get("usage") or {}).get("cost")
            if c is not None:
                cost = float(c)
        except (AttributeError, TypeError, ValueError):
            cost = 0.0

        parsed = _coerce(_extract_json(anon.restore(content)), exam_ids, dx_ids)

        # A syntactically valid colour alone is not a completed analysis.
        if not parsed['triage'] or not parsed['one_liner'] or not parsed['brief']:
            result = _deterministic_result(user_texts, 'fallback_incompleto')
            result['triage'] = redflags.more_urgent(result['triage'], parsed['triage'])
            _persist(interview, result)
            return result

        ai_triage = parsed["triage"]
        if ai_triage is None:
            # El modelo no devolvió un triage utilizable: nos quedamos con
            # lo que ya sabía el escaneo de reglas, o — si el paciente
            # relató síntomas (llegamos hasta acá, así que los hay) y ni
            # siquiera las reglas dispararon — AMARILLO en vez de VERDE:
            # "sin bandera Y sin lectura de IA" no es lo mismo que "está
            # bien", y un VERDE silencioso podría hundirse al fondo de la
            # cola del médico.
            ai_triage = scan["triage"] or "AMARILLO"
        triage = redflags.more_urgent(scan["triage"], ai_triage)

        fallback_brief = _fallback_brief(scan)
        brief = parsed["brief"] or fallback_brief
        summary = parsed["one_liner"] or (user_texts[0][:140] if user_texts else "")

        suggested_exams, seen = [], set()
        for eid in redflags.exams_for(scan["flags"]) + parsed["suggested_exams"]:
            if eid not in seen:
                seen.add(eid)
                suggested_exams.append(eid)

        result = {
            "triage": triage,
            "summary": summary,
            "brief": brief,
            "differential": parsed["differential"],
            "suggested_exams": suggested_exams,
            "red_flags": scan["flags"],
            "ai_model": model,
        }
        _persist(interview, result)
        from app.modules.intake import health_profile
        health_profile.merge_from_interview(interview, qa_lines)
        _record_cost(cost, model, recognized=bool(parsed["triage"]))
        return result
    except Exception as e:  # noqa: BLE001 — el análisis NUNCA debe tumbar el cierre del chat
        _log(f"analyze_interview falló para {(interview or {}).get('id')}: {log_pii.exc(e)}")
        return None
