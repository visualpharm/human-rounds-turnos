"""api_models.py — Pydantic models of the chat module's JSON API
(`chat_api_router.py`, `voice_router.py`, `mcp_api_router.py`'s account
routes).

The request bodies are what the chat widget sends. They document the API
(`/openapi.json`, admin-only) and let `core.api_body` tell a body the widget
never sends from one it does; the chat still gets the body as it came and
keeps every answer it gave — see `core/api_body.py`.

The success answers only document the API: the handlers still build their
own `Response`, byte for byte as before. The admin dictation model's body
and answer (`ChatVoiceIn`, `ChatVoiceOut`) live in `voice_router.py`. The
WhatsApp webhook answers `core.api_body.OkOut`; the stream
(`POST /api/chat/stream`) and Meta's subscription check answer no JSON
document and are not described.
"""

from app.core.api_body import BodyModel, ResponseModel


class ChatAttachmentIn(BodyModel):
    """A photo or PDF of the referral, sent inside the chat (base64, a data URL allowed)."""
    filename: str | None = None
    mime: str | None = None
    data_b64: str | None = None


class ChatMessageIn(BodyModel):
    """One turn of the patient: the text, the conversation it continues (none starts one) and, optionally,
    the referral."""
    mensaje: str | None = None
    session_id: str | None = None
    adjunto: ChatAttachmentIn | None = None


class VoiceAudioIn(BodyModel):
    mime: str | None = None
    data_b64: str | None = None


class VoiceIn(BodyModel):
    """A recorded message to transcribe (base64, a data URL allowed). The audio may also come as `mime`
    and `data_b64` at the top level."""
    audio: VoiceAudioIn | None = None
    mime: str | None = None
    data_b64: str | None = None


class ChatWhatsappLink(ResponseModel):
    """The switch to WhatsApp (`chat_api._handoff_abierto` / `_handoff`): the
    number and a wa.me link with the greeting; `codigo` only once the
    conversation has history to carry over."""
    numero: str
    link: str
    codigo: str | None = None


class ChatTurnOut(ResponseModel):
    """One turn of the site's chat (`chat_api.compute_output`): the assistant's
    answer, whether the visitor is signed in, and, when the turn has them, the
    widget's extras — quick replies (`opciones`), app-owned links (`enlaces`,
    `{label, href}`), the help pages the answer rests on (`fuentes`), the
    slots the tools returned (`turnos`) and the link to more of them, the
    sign-in button (`login`, anonymous only), «upload another referral»
    (`subir_otra_orden`) and the WhatsApp switch with this conversation's
    code."""
    respuesta: str | None
    autenticado: bool
    session_id: str | None = None
    titulo: str | None = None
    opciones: list[str] | None = None
    enlaces: list[dict] | None = None
    fuentes: list[dict] | None = None
    turnos: list[dict] | None = None
    mas_horarios_href: str | None = None
    login: bool | None = None
    subir_otra_orden: bool | None = None
    whatsapp: ChatWhatsappLink | None = None


class ChatMessage(ResponseModel):
    """A turn of the current session as the widget shows it: who said it
    (`user` / `assistant`), the text, and the channel it was said on."""
    rol: str
    texto: str
    canal: str


class ChatHistoryOut(ResponseModel):
    """The signed-in patient's current conversation, web and WhatsApp fused;
    empty for an anonymous visitor."""
    mensajes: list[ChatMessage]


class ChatTranscriptMessage(ResponseModel):
    """A stored message: its role, the message as the engine keeps it
    (`{role, content, ...}`, a tool call or a session marker included) and
    when it was written."""
    rol: str
    contenido: dict
    created: str


class ChatTranscript(ResponseModel):
    """One conversation with activity in the window: its channel and key, and
    its messages in order."""
    canal: str
    clave: str
    mensajes: list[ChatTranscriptMessage]


class ChatTranscriptsOut(ResponseModel):
    """The conversations of the last `hours` for the nightly export (Bearer
    `TURNOS_CHAT_EXPORT_TOKEN`)."""
    conversaciones: list[ChatTranscript]


class ChatVoiceStatusOut(ResponseModel):
    """Whether the chat's microphone is offered."""
    disponible: bool


class ChatTranscriptionOut(ResponseModel):
    """The dictated message, as text."""
    texto: str


class ChatVoiceAdminOut(ResponseModel):
    """The dictation setting as the panel shows it: the model in use, whether
    dictation works here (a Fal key or the simulated AI) and the default."""
    model: str
    fal_configurado: bool
    default_model: str


class McpRevokedOut(ResponseModel):
    """The patient's MCP token was switched off; a new one is issued on the next visit."""
    ok: bool
    revoked: bool


class McpConnectionOut(ResponseModel):
    """The patient's MCP connection: the server's URL, the token in the clear
    only when it was just created (null afterwards) and its last four
    characters."""
    ok: bool
    url: str
    token: str | None
    hint: str | None
    last_used: str | None = None
