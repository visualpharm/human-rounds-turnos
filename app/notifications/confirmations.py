"""confirmations.py — the YES/NO answer to the day-before reminder.

Two doors, one path (`record`): the free-text WhatsApp reply parsed here, and
the one-click link in the reminder email (`appointment_confirm.py`). Whatever
the door, what gets saved and what happens on a "no" are identical.

Hospital meeting 2026-07-24: the WhatsApp reminder asks "¿vas a poder
venir?" and until now that reply got lost — the chat engine read it as just
another sentence. This is where the deterministic path lives: if the
message is a plain yes or no and that person has an appointment waiting on
confirmation, it's saved on the request (`confirmacion`) and answered
without spending a model call.

Two decisions that matter:

- **Only short, unambiguous messages.** "sí", "dale", "no puedo" get
  resolved here; "no sé si voy a llegar, ¿puedo ir más tarde?" does NOT —
  that's a conversation and the engine handles it. The cutoff is by word
  count (`_MAX_PALABRAS`), not by guessing intent: when in doubt, the model
  wins.
- **A "no" frees up the slot.** That's literally what the reminder promises
  ("NO para liberar el turno y que lo aproveche otra persona"), so it gets
  cancelled for real — reusing `booking_requests._cancel`, which already
  notifies the waitlist. If the cancellation fails, the confirmation still
  gets saved: the front desk sees "said they're not coming" even if the
  slot didn't free up on its own.

What the patient SAID (`confirmacion`) and what HAPPENED (`asistencia`) are
deliberately different columns: someone who confirms and doesn't show up is
exactly the case the hospital wants to be able to measure.

Never raises outward: any problem returns None and the message continues on
its normal path to the chat engine.
"""

import re

from app.core import ids as _ids
from app.core import text_fold

# A message longer than this is no longer "yes" or "no": it's a conversation.
_MAX_PALABRAS = 4

_SI = {
    "si", "sii", "siii", "sisi", "s", "ok", "oka", "okey", "okay", "dale",
    "listo", "obvio", "claro", "seguro", "confirmo", "confirmado", "voy",
    "asisto", "perfecto", "buenisimo", "afirmativo", "yes",
}
_NO = {
    "no", "nop", "nono", "n", "negativo", "cancelar", "cancelo", "cancela",
    "anular", "anulo", "imposible",
}
# Two- or three-word phrases that read as a single unit.
_FRASES_SI = {"si voy", "voy a ir", "ahi voy", "si gracias", "si confirmo",
              "cuenten conmigo", "si puedo", "ahi estare", "ahi estoy"}
_FRASES_NO = {"no voy", "no puedo", "no podre", "no llego", "no voy a poder",
              "no voy a ir", "no puedo ir", "no me sirve", "no asisto",
              "cancelar turno", "quiero cancelar", "no gracias"}


def _norm(texto):
    """Lowercase, no accents, no punctuation — "Sí!" and "si" are the same."""
    t = re.sub(r"[^a-zA-Z0-9\s]", " ", text_fold.strip_accents(str(texto or "")).lower())
    return re.sub(r"\s+", " ", t).strip()


def interpret(texto):
    """``"si"`` | ``"no"`` | ``None``. None = not a reply to the reminder,
    let the chat engine handle it."""
    t = _norm(texto)
    if not t:
        return None
    palabras = t.split()
    if len(palabras) > _MAX_PALABRAS:
        return None
    if t in _FRASES_NO:
        return "no"
    if t in _FRASES_SI:
        return "si"
    if len(palabras) == 1:
        if t in _NO:
            return "no"
        if t in _SI:
            return "si"
        return None
    # Two to four words that aren't a known phrase: only resolved if it
    # starts with a no/sí followed by filler ("no gracias", "si dale").
    cabeza, resto = palabras[0], palabras[1:]
    if all(p in _SI or p in _NO or p in ("gracias", "muchas", "ahi", "ok") for p in resto):
        if cabeza in _NO:
            return "no"
        if cabeza in _SI:
            return "si"
    return None


def _cancel(usuario, sid, handler=None):
    """Frees the slot by reusing the patient's own path (same waitlist
    notice). True if it ended up cancelled."""
    try:
        from app.modules.turnos import booking_patient
        dni = _ids.dni((usuario or {}).get("dni")) or None
        status, result = booking_patient.cancel_request(dni, sid)
        return status == 200 and bool(result.get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _when(row):
    try:
        from app.notifications import send as notifications
        return notifications._friendly_appointment_time(row.get("turno_dt")) or ""
    except Exception:  # noqa: BLE001
        return ""


def record(row, valor, canal="whatsapp", handler=None, usuario=None):
    """Saves the patient's yes/no on `row` and does what that answer promises.
    ONE place, because there are two doors into it (2026-08-02): the free-text
    WhatsApp reply (`respond`, below) and the one-click link in the reminder
    email (`appointment_confirm.py`). Returns the patient-facing message, or
    None if it couldn't even be saved.

    A "no" frees the slot for real — the patient was told it would ("NO para
    liberar el turno") — reusing the patient's own cancel path so the waitlist
    gets notified exactly as it does on a manual cancellation. If the
    cancellation fails, the confirmation still stands: the front desk sees
    "said they're not coming" even if the slot didn't free itself."""
    if valor not in ("si", "no") or not row:
        return None
    try:
        from app.core import db_booking
        db_booking.mark_confirmation(row["id"], valor, canal)
    except Exception:  # noqa: BLE001
        return None

    cuando = _when(row)
    if valor == "si":
        return ("Listo, queda confirmado" + (f" para el {cuando}" if cuando else "")
                + ". Te esperamos. Si después no podés venir, avisanos y lo "
                  "liberamos.")

    quien = usuario or {"dni": row.get("solicitante_dni") or row.get("dni")}
    liberado = _cancel(quien, row["id"], handler)
    if liberado:
        return ("Listo, cancelamos tu turno" + (f" del {cuando}" if cuando else "")
                + " y queda disponible para otra persona. Cuando quieras sacar "
                  "otro, avisanos y lo vemos.")
    return ("Anotado que no vas a poder venir. Si necesitás otro turno, "
            "avisanos y lo buscamos.")


def respond(usuario, texto, canal="whatsapp", handler=None):
    """If `texto` is a yes/no to `usuario`'s pending reminder, records it and
    returns the message to reply with. If not, returns None and the message
    continues on to the chat engine."""
    valor = interpret(texto)
    if not valor or not usuario:
        return None
    try:
        from app.core import db_booking
        row = db_booking.booking_request_to_confirm(usuario.get("dni"))
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None  # nothing to confirm: let the engine answer
    return record(row, valor, canal=canal, handler=handler, usuario=usuario)
