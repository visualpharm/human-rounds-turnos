"""chat_api.py — ON-SITE appointment chat (Pinamar Turnos): the floating widget
(static/platform/chat-widget.mjs) runs the same multi-turn engine as WhatsApp
(chat_agent.py) over the shared tool registry (chat_tools.py).

Native routes owned by this module through ``chat_api_router.py``:
  POST /api/chat              {mensaje, adjunto?} -> {respuesta, autenticado,
                               opciones?, login?, whatsapp?}
  POST /api/chat/stream       same input, `text/event-stream` output: the
                               finished answer revealed in small `chunk`
                               events, then one `done` event with the exact
                               body /api/chat would have sent (the product owner,
                               2026-08-02 — see `_message_stream` for why
                               this is progressive REVEAL, not live model
                               token streaming). While the turn is being
                               computed (can take 60-120s), periodic
                               `estado` events keep the connection busy so
                               Cloudflare doesn't kill it as idle (the product owner,
                               2026-08-04 — hit live on prod).
  GET  /api/chat/whatsapp     {numero, link} — the code-less WhatsApp link the
                               widget shows from the moment the panel opens
                               (404 when no WhatsApp line is configured)
  GET  /api/chat/history      {mensajes:[{rol,texto,canal}]} — the current
                               session's transcript, fused with any linked
                               WhatsApp turns (2026-08-03, "one conversation
                               per patient"); [] when not logged in
  POST /api/chat/new          «Nuevo chat» (2026-09-11): drops a new-session
                               boundary marker on the account's web clave so
                               the fused thread starts fresh after the button;
                               {"ok": false} anonymous, never an error
  GET  /api/chat-transcripts  export for the nightly prompt-learning job
                               (see "Ronda 2026-07-23" below)

Identity: the session (cookie `st_ses`, accounts.current_user) — logged in,
full chat (search + appointments + book/cancel + read a referral); anonymous,
read-only (search services and view schedules) — never books appointments on
anyone's behalf without a session (chat_agent.ANONYMOUS_TOOLS). The persisted
conversation `clave` is "dni:<dni>" with a session, or "ip:<ip>" without one —
same criterion as ai_cooldown: with no account, the only stable identity is
the IP.

Handoff to WhatsApp (the product owner, 2026-07-23): every response includes a `wa.me`
link with a handoff code (chat_handoff_create, get-or-create — one code per
conversation, not one per message) ONLY if `TURNOS_WA_NUMERO` is configured;
the widget shows it as a text link (ui-rules: never a button). The code
always transfers HISTORY; if this session is logged in (`clave` is
"dni:<dni>...") redeeming it on WhatsApp also authorizes booking from that
phone for a while — never for an anonymous ("ip:<ip>") session. See
db_chat.user_by_handoff_phone / whatsapp_chat.py (the product owner, 2026-08-02).
Since 2026-08-02 the control is ALWAYS on screen: the widget asks
`GET /api/chat/whatsapp` when the panel opens and shows the code-less link
(`_handoff_abierto`), which the first answer's `whatsapp` then upgrades to the
code-bearing one.  Nothing to hand over until there is history to hand over.

Own per-IP rate limit (TURNOS_CHAT_PER_HOUR, default 20) — in-memory sliding
window, same pattern as abuse_guard.py but in a separate bucket (chat doesn't
compete for quota with referral analysis).

Ronda 2026-07-23 (the product owner's feedback on the first real chat):
  - The response uses `chat_agent.chat_full` instead of `chat`: besides
    `respuesta` it also returns `opciones` (list, omitted if empty — the
    widget renders them as chips) and `login` (bool, omitted if false — the
    widget offers "Iniciar sesión" without leaving the panel).
  - Optional `adjunto: {filename, mime, data_b64}` in the body: the person
    can upload the referral's photo/PDF INSIDE the chat. Logged in: it's
    analyzed with chat_tools.analizar_orden_para (same per-identity cooldown
    as WhatsApp/MCP) and the result is narrated into the text the engine
    sees (chat_tools.narrar_adjunto, the same narration whatsapp_chat.py
    uses — don't triplicate the logic). Anonymous with an attachment: NO AI
    analysis is spent — it replies asking to log in first (`login: true`)
    and the engine is never called.
  - History adoption on login (the product owner: "when I log in, it should pick it up
    from there"): on an authenticated request, if the "dni:<dni>"
    conversation doesn't have history yet but the anonymous "ip:<ip>" one
    (same web channel) does, it's copied once (db_chat.chat_adopt_history)
    BEFORE calling the engine — best-effort, never breaks the response.
  - `GET /api/chat-transcripts?hours=24` — export for the nightly
    prompt-learning job. Auth: `Authorization: Bearer <TURNOS_CHAT_EXPORT_TOKEN>`
    (env; WITHOUT it the route doesn't exist — same fail-closed pattern as
    the WhatsApp webhook secret). site_auth._EXENTAS also excludes it from
    the pre-launch Basic Auth (it has its own Bearer).
"""

import hmac
import re
from dataclasses import dataclass
from urllib.parse import quote
from app.core import config
from app.core import rate_window
from app.core import ids as _ids

_HITS = {}
_HOUR = 3600
_MAX_MESSAGE = 2000
_MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
# A base64-encoded 8 MiB attachment needs roughly 10.7 MiB before the small
# JSON envelope. Native streaming reads incrementally and fails closed above
# this explicit ceiling instead of buffering an unbounded body.
MAX_CHAT_STREAM_BODY_BYTES = 12 * 1024 * 1024
_MAX_TRANSCRIPTS_HOURS = 24 * 30
_SESSION_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _env(key, default=""):
    return config.get(key, default)


def _per_hour():
    try:
        return int(_env("TURNOS_CHAT_PER_HOUR") or 20)
    except ValueError:
        return 20


def _allowed(ip):
    return rate_window.allow(_HITS, ip, _per_hour(), _HOUR)


def _ip_ceiling_per_hour():
    try:
        return int(_env("TURNOS_CHAT_IP_CEILING_PER_HOUR") or 200)
    except ValueError:
        return 200


def _within_limits(ip, headers):
    """The hourly chat limit, counted per BROWSER (`ip|X-Turnos-Client`, see
    abuse_guard.identity) so the people behind one hospital gateway do not use
    up each other's messages; a generous ceiling per IP is what stops a script
    that rotates ids. A request with no id keeps the strict per-IP bucket."""
    from app.ai import abuse_guard
    key = abuse_guard.identity(ip, headers)
    if not _allowed(key):
        return False
    if key == ip:
        return True
    ceiling = _ip_ceiling_per_hour()
    if ceiling <= 0:  # 0 = no ceiling, like the other limits
        return True
    return rate_window.allow(_HITS, f"ceiling|{ip}", ceiling, _HOUR)


@dataclass(frozen=True)
class ChatTurnInput:
    """Transport-resolved inputs for one chat turn."""

    usuario: dict | None
    ip: str
    body: dict | None = None
    json_error: str | None = None
    headers: object = None


def _handoff(clave):
    """{"numero","link"} if TURNOS_WA_NUMERO is configured and a code could
    be issued/reused for this conversation; None if not (no env, or the DB
    is down — this never breaks the chat's response)."""
    numero = _env("TURNOS_WA_NUMERO")
    if not numero:
        return None
    try:
        from app.core import db_chat
        code = db_chat.chat_handoff_create(clave)
    except Exception:  # noqa: BLE001
        return None
    texto_wa = f"Hola, vengo del chat de la web. Mi código es {code}"
    return {"numero": numero, "codigo": code,
            "link": f"https://wa.me/{numero}?text={quote(texto_wa)}"}


def _handoff_abierto():
    """{"numero","link"} with NO handoff code — the link the widget shows from
    the moment the panel opens (the product owner, 2026-08-02: the switch to WhatsApp is
    always there, not only after the assistant has answered).

    Deliberately code-less: a code exists to carry the HISTORY of a
    conversation, and someone who has not written anything yet has none to
    carry.  Issuing one on every open would also write a row per idle
    visitor.  As soon as there IS history, `_handoff` upgrades this link with
    the code in the response to POST /api/chat and the widget swaps it in."""
    numero = _env("TURNOS_WA_NUMERO")
    if not numero:
        return None
    texto_wa = "Hola, vengo del chat de la web."
    return {"numero": numero,
            "link": f"https://wa.me/{numero}?text={quote(texto_wa)}"}


def _valid_attachment(body):
    a = body.get("adjunto")
    return a if isinstance(a, dict) and a.get("data_b64") else None


def _session_id(body):
    value = str(body.get("session_id") or "").strip()
    return value if _SESSION_RE.fullmatch(value) else ""


def compute_output(turn: ChatTurnInput):
    """Compute POST /api/chat from explicit transport-resolved input.

    Hace TODO el trabajo (rate limit, adjunto, motor) y devuelve (status, salida) —
    status=None en el camino feliz (200 implícito), o el status HTTP de un
    corte temprano (429/400). Both native response paths call this same
    operation; it never receives or emulates an HTTP request object.
    """
    usuario = turn.usuario
    ip = turn.ip
    if not _within_limits(ip, turn.headers):
        from app.core import i18n
        from app.modules.chat import chat_agent
        # «Esperá un rato» is never the whole answer to an emergency, and
        # both lines speak the language the patient wrote in.
        cuerpo = turn.body if isinstance(turn.body, dict) else {}
        escrito = str(cuerpo.get("mensaje") or "")
        urgente = chat_agent.emergency_reply_for(escrito, i18n.active())
        idioma = (urgente and chat_agent.chat_language.detect(escrito)) or i18n.active()
        mensaje = i18n.t_or(
            "assistant.rate_limited",
            "Hiciste varios mensajes seguidos. Esperá un rato y volvé a "
            "escribir.", idioma)
        return 429, {
            "error": "limite",
            "mensaje": (urgente + "\n\n" + mensaje) if urgente else mensaje,
        }

    if turn.json_error is not None:
        return 400, {"error": f"JSON inválido: {turn.json_error}"}
    body = turn.body or {}

    texto = str(body.get("mensaje") or "").strip()
    adjunto = _valid_attachment(body)
    if not texto and not adjunto:
        return 400, {"error": "falta el mensaje"}
    texto = texto[:_MAX_MESSAGE]

    session_id = _session_id(body)
    if usuario:
        dni = _ids.dni(usuario.get("dni"))
        clave_base = f"dni:{dni}"
    else:
        clave_base = f"ip:{ip}"
    clave = f"{clave_base}:chat:{session_id}" if session_id else clave_base

    if usuario:
        try:
            from app.core import db_chat
            anon_base = f"ip:{ip}"
            anon_clave = (f"{anon_base}:chat:{session_id}"
                          if session_id else anon_base)
            db_chat.chat_adopt_history("web", anon_clave, clave)
        except Exception:  # noqa: BLE001 — best-effort, never breaks the chat
            pass

    subir_otra_orden = False
    if adjunto:
        from app.modules.turnos import booking_referrals
        try:
            orden_bytes, orden_mime, orden_filename = booking_referrals._decode_referral(adjunto)
        except ValueError as e:
            return 400, {"error": str(e)}
        if len(orden_bytes) > _MAX_ATTACHMENT_BYTES:
            return 400, {"error": "el archivo es muy pesado"}
        from app.modules.chat import chat_tools
        resultado = chat_tools.analizar_orden_para(clave, orden_bytes, orden_mime, orden_filename)
        if resultado.get("error") == "enfriamiento":
            return None, {"respuesta": resultado.get("mensaje"), "autenticado": bool(usuario)}
        subir_otra_orden = resultado.get("veredicto") == "documento_equivocado"
        texto = chat_tools.narrar_adjunto(texto, resultado)

        if not usuario:
            from app.modules.chat import chat_agent
            orientacion = chat_tools.orientar_orden_anonima(resultado)
            respuesta = orientacion["texto"]
            try:
                chat_agent.record_exchange(
                    clave, "web", texto or "(orden adjunta)", respuesta)
            except Exception:  # noqa: BLE001 — persistence never blocks chat
                pass
            salida = {
                "respuesta": respuesta,
                "autenticado": False,
                "enlaces": chat_tools.enlaces_para(
                    texto, respuesta, orientacion.get("tools") or []),
            }
            if session_id:
                salida["session_id"] = session_id
            if subir_otra_orden or orientacion.get("subir_otra_orden"):
                salida["subir_otra_orden"] = True
            handoff = _handoff(clave)
            if handoff:
                salida["whatsapp"] = handoff
            return None, salida

    from app.modules.chat import chat_agent
    resultado_chat = chat_agent.chat_full(usuario, texto or "(mensaje vacío)", clave=clave, canal="web")

    salida = {"respuesta": resultado_chat["texto"], "autenticado": bool(usuario)}
    if session_id:
        salida["session_id"] = session_id
    # The model's [opciones: ...] marker (2026-08-01, shared component round):
    # the widget renders these as tappable buttons under the assistant's
    # message via chat-ui.js's crearLista().addOpciones — same shared
    # component the pre-consult interview uses. WhatsApp keeps rendering the
    # same marker as a numbered text list (chat_agent.chat()).
    opciones = resultado_chat.get("opciones") or []
    if usuario:
        opciones = _sin_opcion_de_login(opciones)
    if opciones:
        salida["opciones"] = opciones
    if resultado_chat.get("enlaces"):
        salida["enlaces"] = resultado_chat["enlaces"]
    if resultado_chat.get("fuentes"):
        salida["fuentes"] = resultado_chat["fuentes"]
    if resultado_chat.get("titulo"):
        salida["titulo"] = resultado_chat["titulo"]
    if resultado_chat.get("turnos"):
        salida["turnos"] = resultado_chat["turnos"]
    if resultado_chat.get("mas_horarios_href"):
        salida["mas_horarios_href"] = resultado_chat["mas_horarios_href"]
    # A model marker can be stale or mistaken.  An authenticated visitor must
    # never be offered "Iniciar sesión" again.
    if resultado_chat.get("login") and not usuario:
        salida["login"] = True
    if subir_otra_orden:
        salida["subir_otra_orden"] = True
    handoff = _handoff(clave)
    if handoff:
        salida["whatsapp"] = handoff
    return None, salida


# Progressive reveal for POST /api/chat/stream (the product owner, 2026-08-02: "stream the
# AI responses instead of showing everything at once"). NOT token-by-token
# generation streaming — chat_agent._run_turn's answer goes through several
# safety/quality passes AFTER the model responds (jurisdiction limiting,
# date localization, marker stripping) that only make sense on the COMPLETE
# text, so nothing is sent to the client until the real, final answer exists
# server-side. This reveals that finished text in small chunks over a live
# connection instead of one blob, which is what fixes the "wall of text"
# complaint without risking a not-yet-safety-checked partial answer reaching
# the screen.
_STREAM_CHUNK = 24          # chars per SSE event
_STREAM_DELAY = 0.02        # seconds between events

# The product owner, 2026-08-04: `compute_output` is the part that can take 60-120s (the
# model call, plus tool calls like an agenda search) — with nothing written
# to the socket while it runs, Cloudflare kills the connection as idle
# (~100s) before the worker ever finishes, and the widget's `leerEventos`
# ends without a `done` event ("No pudimos conectar" even though the backend
# would have answered fine — hit live on prod). The native router runs it in
# the threadpool and keeps the SSE connection busy with `estado` events.
_ESTADO_POLL = 4.0          # seconds between estado ticks / worker join polls
# (key, castellano) — the wait labels a patient stares at for the 60-120s a
# turn takes. Resolved per REQUEST, never at import: a module-level tuple of
# rendered strings freezes the first visitor's language for everyone (defect
# class 1, docs/architecture/i18n.md) and, before 2026-09-11, was plain
# Spanish for all ten languages.
_ESTADO_CLAVES = (
    ("assistant.wait.reading", "Leyendo tu consulta…"),
    ("assistant.wait.agenda", "Buscando en la agenda…"),
    ("assistant.wait.availability", "Revisando disponibilidad…"),
    ("assistant.wait.answer", "Preparando la respuesta…"),
)


def estado_etiquetas(lang=None):
    """The streaming wait labels in the visitor's language."""
    from app.core import i18n
    lang = lang or i18n.active()
    return tuple(i18n.t_or(clave, castellano, lang)
                 for clave, castellano in _ESTADO_CLAVES)


# Every spelling of "sign in" this installation can render, plus the
# hand-written ones the model has actually produced. An option chip is dropped
# when it CONTAINS one of these: the model writes "Ir para Ingresar" and
# "Ver Mis solicitudes", not bare labels.
_LOGIN_LABEL_KEYS = ("nav.sign_in", "chat.sign_in", "account.form.sign_in")
_LOGIN_LABELS_FIJAS = ("ingresar", "iniciar sesion", "iniciar sesión",
                       "log in", "login", "sign in")


def _etiquetas_de_login():
    from app.core import i18n
    labels = set(_LOGIN_LABELS_FIJAS)
    try:
        for lang in i18n.enabled_languages() or []:
            for key in _LOGIN_LABEL_KEYS:
                texto = i18n.raw(key, lang)
                if texto and texto != key:
                    labels.add(texto.casefold())
    except Exception:  # noqa: BLE001 — degrades to the fixed list
        pass
    return labels


def _sin_opcion_de_login(opciones):
    """The option chips minus every "Ingresar" one.

    An authenticated visitor must never be offered a login again — the `login`
    flag was already suppressed for them, but the model's own chip survived
    (leak 8 of the 2026-09-11 simulation: an English reply to a signed-in
    patient closed with `["Ingresar"]`)."""
    labels = _etiquetas_de_login()
    return [op for op in opciones or []
            if not any(label in str(op or "").casefold() for label in labels)]
def history_payload(usuario, session_id_raw=""):
    """Pure body of GET /api/chat/history?session_id=... — the CURRENT
    session's transcript, fused with any WhatsApp turns already linked to
    this account (2026-08-03, "one conversation per patient across web and
    WhatsApp"). `usuario` is the ALREADY-resolved session user (or None —
    identity always comes from the session, never a session_id-supplied
    dni, so an anonymous visitor or an expired session gets an empty list,
    never an error and never someone else's history). `session_id_raw` is
    the raw `session_id` query value before validation (native-migration
    campaign, family W2a)."""
    if not usuario:
        return {"mensajes": []}

    session_id = str(session_id_raw or "").strip()
    if not _SESSION_RE.fullmatch(session_id):
        session_id = ""
    dni = _ids.dni(usuario.get("dni"))
    clave = f"dni:{dni}:chat:{session_id}" if session_id else f"dni:{dni}"

    try:
        from app.modules.chat import chat_agent
        mensajes = chat_agent.thread_for_widget(usuario, "web", clave)
    except Exception:  # noqa: BLE001 — best-effort, same as the rest of the chat
        mensajes = []
    return {"mensajes": mensajes}


def new_session_payload(usuario):
    """Pure body of POST /api/chat/new — the widget's «Nuevo chat» button
    (2026-09-11). Persists a new-session boundary marker (chat_agent's
    NEW_SESSION_MARKER) on the ACCOUNT's web clave "dni:<dni>" — the same
    clave shape history_payload builds when there is no session_id, which
    _conversation_pairs fuses into EVERY widget conversation of this
    account. Without it, a conversation created by the button received the
    whole recent account thread from GET /api/chat/history (the panel
    flashed empty and the old conversation poured back in);
    chat_agent._split_session cuts at the marker from then on, so both the
    widget hydration and the model's context start fresh after it, and the
    session it closes gets summarized by the usual
    _maybe_summarize_closed_session path on the next loaded turn.

    Identity comes from the ALREADY-resolved session user only (same
    contract as history_payload): anonymous gets {"ok": false} and nothing
    is written — never an error (the widget posts fire-and-forget)."""
    if not usuario:
        return {"ok": False}
    dni = _ids.dni(usuario.get("dni"))
    if not dni:
        return {"ok": False}
    try:
        from app.modules.chat import chat_agent
        chat_agent.marcar_sesion_nueva(dni)
    except Exception:  # noqa: BLE001 — best-effort, same as the rest of chat
        return {"ok": False}
    return {"ok": True}


def _export_token():
    return _env("TURNOS_CHAT_EXPORT_TOKEN")


def transcripts_result(token, auth_header, hours_raw):
    """Pure body of GET /api/chat-transcripts?hours=24 once a `token` is
    configured — the router decides the feature-disabled 404 (no
    `TURNOS_CHAT_EXPORT_TOKEN`) BEFORE
    calling this, since that's a transport-level "route doesn't exist"
    decision, not a domain response. Returns (status, payload)."""
    auth = auth_header or ""
    provisto = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not provisto or not hmac.compare_digest(provisto, token):
        return 403, {"error": "no_autorizado"}

    try:
        horas = int(hours_raw)
    except (TypeError, ValueError):
        horas = 24
    horas = max(1, min(horas, _MAX_TRANSCRIPTS_HOURS))

    try:
        from app.core import db_chat
        conversaciones = db_chat.chat_transcripts(horas)
    except Exception:  # noqa: BLE001
        conversaciones = []
    return 200, {"conversaciones": conversaciones}
