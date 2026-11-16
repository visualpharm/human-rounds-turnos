"""«Contanos tu caso» as a short chat (the product owner, 2026-11-07).

With no free slot the patient leaves their case and the desk decides what to
offer (the reference-deployment counterpart, 19.10: a slot in a week, telemedicine, spontaneous demand, an
exceptional extra slot). Instead of a blank box the patient answers the
opening question (with taps) and one to three clarifying ones — the
interview engine (`interview_agent.run_turn`, entry_mode 'caso') asks them,
with the interview's emergency floor (guardia / 107) first and a hard cap
in code.

Lifecycle:
  start()   a fresh token and the opening question; `{"disponible": False}`
            when no model can answer, and the wizard keeps the plain box.
  message() one patient answer → the next question, or the close.
  finish()  the summary for the desk (what they want, urgency, why, key
            facts): one AI reading over the scrubbed transcript, a
            deterministic one without AI; an emergency in the chat always
            reads «emergencia». Stored as conversation metadata.
  bind()    called by the request creation (intake hook `case_chat_bind`):
            the summary and the transcript move into
            `booking_requests.case_chat`, the anonymous rows go.

The chat lives in `chat_messages` (channel 'caso', key 'caso:<token>') until
its request is sent; nothing identifying leaves for the model unless the
patient typed it, and what they typed goes through `core/pii.py`.
"""

import json
import re
import secrets

from app.ai import ai_client
from app.ai import ai_health
from app.core import config
from app.core import log_pii
from app.core import pii
from . import dialogue
from . import interview_agent

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")
URGENCIAS = ("emergencia", "alta", "media", "baja")
MAX_NOTE = 1000
_MAX_HECHOS = 4
_MAX_FIELD = 300


class CaseError(Exception):
    def __init__(self, status, body):
        super().__init__(body.get("error"))
        self.status = status
        self.body = body


def _fake():
    return config.get("TURNOS_FAKE_AI", "") == "1"


def available():
    """Whether a model can ask the questions right now."""
    return _fake() or (ai_client.available() and ai_health.available())


def _clave(token):
    return f"{interview_agent.CANAL_CASO}:{token}"


def _interview(token, usuario=None):
    # The DNI of a signed-in patient only seeds the PII scrubber
    # (pii.for_dni learns their name, phone and email); the case prompt
    # never reads an account.
    return {"entry_mode": "caso", "session_key": _clave(token),
            "dni": (usuario or {}).get("dni")}


def _history(token):
    if not TOKEN_RE.match(str(token or "")):
        raise CaseError(404, {"error": "no_encontrado"})
    historial = interview_agent._load_history(_clave(token))
    if not historial:
        raise CaseError(404, {"error": "no_encontrado"})
    return historial


def _closed(historial):
    ultimo = next((m for m in reversed(historial) if m.get("role") == "assistant"), None)
    return bool(ultimo and interview_agent._FIN_RE.search(dialogue.text(ultimo.get("content"))))


def start(usuario=None):
    if not available():
        return {"disponible": False}
    token = secrets.token_urlsafe(24)
    turno = interview_agent.run_turn(_interview(token, usuario), "")
    return {"disponible": True, "token": token, **turno}


def message(token, texto, usuario=None):
    historial = _history(token)
    if _closed(historial):
        raise CaseError(409, {"error": "caso_cerrado"})
    texto = str(texto or "").strip()
    if not texto:
        raise CaseError(400, {"error": "vacio"})
    return interview_agent.run_turn(_interview(token, usuario), texto)


# ---- the transcript and its summary ------------------------------------------

def transcript(historial):
    """[{"role": "paciente"|"asistente", "texto"}] as the patient saw it:
    no [fin] / [opciones: …] markers, the tap options kept on the question."""
    out = []
    for m in historial or []:
        if m.get("role") == "user":
            texto = dialogue.text(m.get("content")).strip()
            if texto:
                out.append({"role": "paciente", "texto": texto})
        elif m.get("role") == "assistant":
            texto, opciones = interview_agent.sin_marcadores(m.get("content"))
            if texto:
                out.append({"role": "asistente", "texto": texto, **({"opciones": opciones} if opciones else {})})
    return out


def _emergency(historial):
    for m in historial or []:
        if m.get("role") == "assistant" and m.get("intake_outcome") == "urgent":
            return True
        if m.get("role") == "user" and dialogue.urgent_now(m.get("content")):
            return True
    return False


_TIMING = (
    ("alta", r"\b(?:esta semana|hoy|manana|ya mismo|cuanto antes|urgente|this week|today|asap)\b"),
    ("media", r"\b(?:este mes|proximo mes|en unas semanas|this month)\b"),
    ("baja", r"\b(?:puede esperar|no es urgente|sin apuro|can wait|no rush)\b"),
)


def _urgency_from_words(palabras):
    """The patient's own timing answer, when they gave one (a tapped option
    or their words); None when they never said."""
    for texto in reversed(palabras):
        value = dialogue.norm(texto)
        for nivel, patron in _TIMING:
            if re.search(patron, value):
                return nivel
    return None


def _timing_only(texto):
    """A bare timing answer («Esta semana», a tapped option): the urgency
    line already says it, so it is not repeated as a fact."""
    value = dialogue.norm(texto)
    return any(re.fullmatch(r"\W*" + patron + r"\W*", value) for _, patron in _TIMING)


def _fallback_summary(mensajes):
    palabras = [m["texto"] for m in mensajes if m["role"] == "paciente"]
    hechos = [p for p in palabras[1:] if not _timing_only(p)]
    return {
        "quiere": (palabras[0] if palabras else "")[:_MAX_FIELD],
        "urgencia": _urgency_from_words(palabras),
        "por_que": "",
        "hechos": [p[:_MAX_FIELD] for p in hechos[:_MAX_HECHOS]],
        "fuente": "texto",
    }


def _clean_summary(raw):
    """The model's JSON, held to its shape; None when it is unusable."""
    if not isinstance(raw, dict):
        return None
    quiere = str(raw.get("quiere") or "").strip()[:_MAX_FIELD]
    urgencia = str(raw.get("urgencia") or "").strip().lower()
    if not quiere or urgencia not in URGENCIAS[1:]:
        return None
    hechos = raw.get("hechos") if isinstance(raw.get("hechos"), list) else []
    return {
        "quiere": quiere,
        "urgencia": urgencia,
        "por_que": str(raw.get("por_que") or "").strip()[:_MAX_FIELD],
        "hechos": [str(h).strip()[:_MAX_FIELD] for h in hechos if str(h or "").strip()][:_MAX_HECHOS],
        "fuente": "ia",
    }


def _ai_summary(mensajes, usuario=None):
    from app.ai import prompts
    anon = pii.for_dni((usuario or {}).get("dni"))
    lineas = "\n".join(
        ("Paciente: " if m["role"] == "paciente" else "Asistente: ") + m["texto"] for m in mensajes)
    resp = ai_client.call(
        {"model": interview_agent._model(),
         "messages": anon.scrub_messages([
             {"role": "system", "content": prompts.text("caso_resumen")},
             {"role": "user", "content": lineas}]),
         "temperature": 0, "response_format": {"type": "json_object"}},
        timeout=30,
        headers_extra={"X-Title": "Human Rounds - contanos tu caso"})
    ai_health.report(None)
    contenido = (resp["choices"][0]["message"].get("content") or "").strip()
    contenido = re.sub(r"^```(?:json)?|```$", "", contenido).strip()
    return _clean_summary(anon.restore_json(json.loads(contenido)))


def summarize(historial, usuario=None):
    """The desk's summary of one case chat. An emergency anywhere in the
    chat reads «emergencia», whatever the model said."""
    mensajes = transcript(historial)
    resumen = None
    if mensajes and not _fake() and available():
        try:
            resumen = _ai_summary(mensajes, usuario)
        except Exception as e:  # noqa: BLE001 — the deterministic summary stands in
            ai_health.report(e)
            interview_agent._log(f"resumen del caso falló: {log_pii.exc(e)}")
    resumen = resumen or _fallback_summary(mensajes)
    if _emergency(historial):
        resumen["urgencia"] = "emergencia"
    return resumen


def finish(token, usuario=None):
    """Computes (once) and stores the summary of a case chat."""
    historial = _history(token)
    from app.core import db_chat
    guardado = db_chat.chat_metadata_get(interview_agent.CANAL_CASO, _clave(token), "case_summary")
    if guardado and guardado.get("resumen"):
        return {"ok": True}
    resumen = summarize(historial, usuario)
    db_chat.chat_save_message(interview_agent.CANAL_CASO, _clave(token),
                              {"role": "case_summary", "resumen": resumen})
    return {"ok": True}


def patient_words(mensajes):
    """What the patient wrote, as one note (the request's `patient_note`, so
    every older reader of «Su caso» keeps working)."""
    return " · ".join(m["texto"] for m in mensajes if m["role"] == "paciente")[:MAX_NOTE]


def bind(token, request_id, usuario=None):
    """Moves a case chat into its request. Returns the patient's words (the
    request's note), or None when the token names no chat. Never raises:
    the request already exists and stands without its chat."""
    try:
        if not TOKEN_RE.match(str(token or "")):
            return None
        from app.core import db_chat
        clave = _clave(token)
        historial = interview_agent._load_history(clave)
        if not historial:
            return None
        guardado = db_chat.chat_metadata_get(interview_agent.CANAL_CASO, clave, "case_summary") or {}
        mensajes = transcript(historial)
        resumen = guardado.get("resumen") or _fallback_summary(mensajes)
        if _emergency(historial):
            resumen = {**resumen, "urgencia": "emergencia"}
        db_chat.case_chat_set(request_id, {"resumen": resumen, "mensajes": mensajes})
        db_chat.chat_delete_conversation(interview_agent.CANAL_CASO, clave)
        return patient_words(mensajes)
    except Exception as e:  # noqa: BLE001
        interview_agent._log(f"no se pudo guardar el caso en la solicitud {request_id}: {log_pii.exc(e)}")
        return None
