"""Native ASGI transport for the on-site booking chat.

``/api/chat``, ``/api/chat/whatsapp``, ``/api/chat/history``,
``/api/chat/new`` and ``/api/chat-transcripts`` (family W2a of
docs/roadmap/native-migration-campaign-20260826.md):
each is a real ``async def`` endpoint parsing the request natively and
calling ``chat_api.py``'s extracted ``history_payload()``/``transcripts_result()``/
``compute_output()``/``_handoff_abierto()``/``new_session_payload()`` domain
functions directly
(sync DB/model work off the event loop via ``run_in_threadpool``).
``POST /api/chat/stream`` owns a real ``StreamingResponse`` so keepalives and
answer chunks reach the client progressively. Business logic remains
single-sourced in ``chat_api.compute_output`` for both response paths.

The session is the dependency ``cuentas.account_deps.optional_account``
(``get_history``, ``new_session``); a chat turn reads it itself, in
``_turn_input``, after the body-size check of ``/api/chat/stream``.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse

from app.core import api_body, deps
from app.core.terminal_response import json_not_found_response
from app.cuentas import account_deps, accounts
from app.auth import turnstile
from app.modules.chat import api_models, chat_api
from app.modules.chat._native_support import json_response as _native_json_response


router = APIRouter(dependencies=[Depends(deps.require_module_enabled("chat"))])
_ACTIVE_STREAM_TASKS = set()
_TURN_DOCS = api_body.docs(post=(api_models.ChatMessageIn, None))


def _watch(request, turn):
    if turn.json_error is None:
        api_body.watch(request, turn.body, api_models.ChatMessageIn)


def _turn_input(request: Request, raw: bytes) -> chat_api.ChatTurnInput:
    try:
        usuario = accounts.current_user(request)
    except Exception:  # noqa: BLE001 - an unavailable session store is anonymous
        usuario = None
    client = request.client
    client_host = client.host if client else ""
    ip = turnstile.client_ip_from(request.headers, client_host) or client_host
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(body, dict):
            raise ValueError("se esperaba un objeto JSON")
    except Exception as exc:  # noqa: BLE001 - preserve the 400 JSON contract
        return chat_api.ChatTurnInput(usuario, ip, json_error=str(exc), headers=request.headers)
    return chat_api.ChatTurnInput(usuario, ip, body=body, headers=request.headers)


def _json_response(payload, status):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Response(
        content=body,
        status_code=status,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


def _event(event, payload):
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


async def _bounded_body(request):
    try:
        declared = int(request.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > chat_api.MAX_CHAT_STREAM_BODY_BYTES:
        return None
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > chat_api.MAX_CHAT_STREAM_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _track(task):
    _ACTIVE_STREAM_TASKS.add(task)

    def _finished(done):
        _ACTIVE_STREAM_TASKS.discard(done)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(_finished)
    return task


async def _reveal(output):
    text = str(output.get("respuesta") or "")
    for start in range(0, len(text), chat_api._STREAM_CHUNK):
        yield _event(
            "chunk", {"texto": text[start:start + chat_api._STREAM_CHUNK]},
        )
        if chat_api._STREAM_DELAY:
            await asyncio.sleep(chat_api._STREAM_DELAY)
    yield _event("done", output)


async def _slow_stream(task):
    try:
        step = 0
        etiquetas = chat_api.estado_etiquetas()
        while not task.done():
            label = etiquetas[min(step, len(etiquetas) - 1)]
            yield _event("estado", {"mensaje": label})
            step += 1
            await asyncio.wait({task}, timeout=chat_api._ESTADO_POLL)

        try:
            status, output = task.result()
        except Exception:  # noqa: BLE001 - headers are already committed
            yield _event(
                "error",
                {"error": "excepcion", "mensaje": "Ocurrió un error. Probá de nuevo."},
            )
            return
        if status:
            yield _event(
                "error",
                {
                    "error": output.get("error", "error"),
                    "mensaje": output.get("mensaje", ""),
                },
            )
            return
        async for chunk in _reveal(output):
            yield chunk
    finally:
        if task.done():
            _ACTIVE_STREAM_TASKS.discard(task)


def _stream_response(events):
    return StreamingResponse(
        events,
        status_code=200,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "close",
        },
    )


@router.post("/api/chat/stream", openapi_extra=_TURN_DOCS)
@router.post("/api/chat/stream/", openapi_extra=_TURN_DOCS)
async def stream_chat(request: Request):
    body = await _bounded_body(request)
    if body is None:
        return _json_response({"error": "body demasiado grande"}, 413)

    turn = await run_in_threadpool(_turn_input, request, body)
    _watch(request, turn)
    task = _track(asyncio.create_task(
        run_in_threadpool(chat_api.compute_output, turn)))
    done, _pending = await asyncio.wait({task}, timeout=chat_api._ESTADO_POLL)
    if not done:
        return _stream_response(_slow_stream(task))

    _ACTIVE_STREAM_TASKS.discard(task)
    status, output = task.result()
    if status:
        return _json_response(output, status)
    return _stream_response(_reveal(output))


async def send_message(request: Request):
    """POST /api/chat through the shared typed chat-turn operation."""
    body = await request.body()
    turn = await run_in_threadpool(_turn_input, request, body)
    _watch(request, turn)
    status, salida = await run_in_threadpool(chat_api.compute_output, turn)
    return _native_json_response(request, salida, status or 200)


async def get_whatsapp(request: Request):
    """GET /api/chat/whatsapp — the code-less WhatsApp link. With no
    ``TURNOS_WA_NUMERO`` configured the legacy ``_whatsapp`` returned False
    (declined) and the dispatcher fell to the terminal 404 — deployed main
    serves exactly those bytes, so the native route reproduces them."""
    datos = await run_in_threadpool(chat_api._handoff_abierto)
    if not datos:
        return json_not_found_response()
    return _native_json_response(request, datos)


async def get_history(
        request: Request, usuario: dict | None = Depends(account_deps.optional_account),
        session_id: str | None = Query(None, description=(
            "The conversation to show (the id the chat gave it); the signed-in patient's own otherwise"))):
    """GET /api/chat/history?session_id=... — identity ALWAYS from the
    session (``account_deps.optional_account``), never from the query."""
    payload = await run_in_threadpool(chat_api.history_payload, usuario, session_id or "")
    return _native_json_response(request, payload)


async def new_session(request: Request, usuario: dict | None = Depends(account_deps.optional_account)):
    """POST /api/chat/new — the widget's «Nuevo chat» button. Same wiring as
    ``get_history``: identity ALWAYS from the session, body ignored. An
    anonymous visitor is a no-op {"ok": false} with 200, never an error
    (the client posts fire-and-forget and never reads the answer)."""
    payload = await run_in_threadpool(chat_api.new_session_payload, usuario)
    return _native_json_response(request, payload)


async def get_transcripts(
        request: Request,
        # Text, parsed leniently by the domain; `query_docs` below documents the number it takes.
        hours: str | None = Query(None, include_in_schema=False)):
    """GET /api/chat-transcripts?hours=24 — without
    ``TURNOS_CHAT_EXPORT_TOKEN`` configured the route does NOT EXIST: it
    answers with the terminal 404 bytes (see ``get_whatsapp``); with it configured, ``chat_api.transcripts_result``
    owns the Bearer check."""
    token = chat_api._export_token()
    if not token:
        return json_not_found_response()
    hours_raw = hours or "24"
    auth = request.headers.get("Authorization") or ""
    status, payload = await run_in_threadpool(
        chat_api.transcripts_result, token, auth, hours_raw)
    return _native_json_response(request, payload, status)


router.add_api_route("/api/chat", send_message, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.ChatMessageIn, api_models.ChatTurnOut)))
router.add_api_route("/api/chat/whatsapp", get_whatsapp, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.ChatWhatsappLink)))
router.add_api_route("/api/chat/history", get_history, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.ChatHistoryOut))
                     | deps.PATIENT_SESSION_OPTIONAL_DOCS)
router.add_api_route("/api/chat/new", new_session, methods=["POST"],
                     openapi_extra=api_body.docs(post=(None, api_body.OkOut))
                     | deps.PATIENT_SESSION_OPTIONAL_DOCS)
router.add_api_route("/api/chat-transcripts", get_transcripts, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.ChatTranscriptsOut))
                     | api_body.query_docs(get={"hours": api_body.Query(
                         "How many hours back, 1 to the export's cap; anything else is 24", {"type": "integer"})}))
