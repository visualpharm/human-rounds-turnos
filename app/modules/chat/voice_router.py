"""voice_router.py — the FastAPI `router` twin of `voice.py` (family W2a of
docs/roadmap/native-migration-campaign-20260826.md): voice input for the
public chat plus its admin model setting.

Each path is a real `async def` endpoint parsing the request natively and calling
`voice.py`'s extracted `public_status_payload()`/`transcribe_result()`/
`admin_status_payload()` domain functions (plus its already-pure
`save_model()`) directly — sync work (fal.ai calls, ffmpeg, DB) off the
event loop via `run_in_threadpool`.

Admin auth uses `core.deps.require_admin(solo_admin=True)`.

GET and POST on `/api/admin/chat-voice` share ONE route object (`methods=
["GET", "POST"]`, branching on `request.method`), same topology as the
adapted-legacy registration this replaces (the census's route-object count
is a Starlette-route count, not a method count) — same pattern as
`admin_panel/settings_admin_router.py`.

  GET  /api/chat/voice          public capability status
  POST /api/chat/transcribe     {audio} -> {texto}
  GET  /api/admin/chat-voice    model/configuration status (admin)
  POST /api/admin/chat-voice    {model} -> saved (admin)"""

import json
import logging

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth import turnstile
from app.core import ai_register, api_body, deps, i18n, log_pii
from app.modules.chat import api_models, voice
from app.modules.chat._native_support import json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("chat"))])


class ChatVoiceIn(api_body.BodyModel):
    """The transcription model; only `fal-ai/wizper` is accepted."""
    model: str | None = None


class ChatVoiceOut(api_body.ResponseModel):
    ok: bool
    model: str


_BODY = api_body.BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                              respond=json_response)
"""Never answers: this route keeps its own answers to any body (`observe()` only)."""


async def voice_status(request: Request):
    payload = await run_in_threadpool(voice.public_status_payload)
    return json_response(request, payload)


async def transcribe(request: Request):
    client = request.client
    ip = turnstile.client_ip_from(request.headers, client.host if client else "")
    if not await run_in_threadpool(voice._allowed, ip):
        return json_response(
            request,
            {"error": "limite",
             "mensaje": "Grabaste varios mensajes seguidos. Esperá un rato y probá de nuevo."},
            status=429)
    raw = await request.body()
    try:
        body = (json.loads(raw.decode("utf-8")) if raw else {}) or {}
    except ValueError as exc:
        return json_response(request, {"error": str(exc)}, status=400)
    except Exception:  # noqa: BLE001
        return json_response(request, {"error": "audio inválido"}, status=400)
    api_body.watch(request, body, api_models.VoiceIn)
    status, payload = await run_in_threadpool(voice.transcribe_result, body)
    return json_response(request, payload, status)


async def admin_chat_voice(
    request: Request,
    identity: dict = Depends(deps.require_admin(solo_admin=True)),
    observed: api_body.RawRead = Depends(api_body.raw(_BODY, ChatVoiceIn, methods={"POST"})),
):
    if request.method == "GET":
        payload = await run_in_threadpool(voice.admin_status_payload)
        return json_response(request, payload)

    raw = observed.raw
    try:
        body = (json.loads(raw.decode("utf-8")) if raw else {}) or {}
        # A JSON string or number is the body family's 400 (BodyMismatch), not a save that failed.
        chosen = api_body.call(observed, lambda: body.get("model"))
        with ai_register.actor(identity):
            model = await run_in_threadpool(voice.save_model, chosen)
    except ValueError:
        return json_response(request, {"error": "Usá fal-ai/wizper."}, status=400)
    except deps.DB_UNAVAILABLE_ERRORS:
        return deps.answer_db_unavailable(request=request)
    except api_body.BodyMismatch:
        raise
    except Exception as exc:  # noqa: BLE001 — not the database: its own error, no db_unavailable
        logging.getLogger(__name__).error("the dictation model was not saved: %s", log_pii.exc(exc))
        return json_response(request, {"error": i18n.t_or(
            "admin.srv.error.no_guardado", "no se pudo guardar, probá de nuevo", i18n.active())}, status=500)
    return json_response(request, {"ok": True, "model": model})


router.add_api_route("/api/chat/voice", voice_status, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.ChatVoiceStatusOut)))
router.add_api_route("/api/chat/transcribe", transcribe, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.VoiceIn, api_models.ChatTranscriptionOut)))
router.add_api_route("/api/admin/chat-voice", admin_chat_voice, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.ChatVoiceAdminOut),
                                                 post=(ChatVoiceIn, ChatVoiceOut)))
