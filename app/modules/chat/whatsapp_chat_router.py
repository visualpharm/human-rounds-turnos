"""Native ASGI transport for the chat module's WhatsApp webhook.

This router owns only HTTP parsing and response emission. Provider
normalization, authentication, media handling, and message processing remain
in :mod:`modules.chat.whatsapp_chat`, shared with the dark stdlib transport."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import api_body, deps
from app.modules.chat import whatsapp_chat
from app.core.http_json import json_response as _json_response


router = APIRouter(dependencies=[Depends(deps.require_module_enabled("chat"))])
# Meta's webhook verification and the provider's events: their protocol, documented for what this
# route reads of it.
_HANDSHAKE_DOCS = api_body.query_docs(get={
    "hub.mode": "`subscribe`, from Meta's webhook verification",
    "hub.verify_token": "Must equal the installation's webhook secret",
    "hub.challenge": "Answered back as plain text when the token matches",
})
_WEBHOOK_DOCS = api_body.query_docs(post={
    "secret": "The webhook secret, for a provider that cannot send it as `X-Webhook-Secret` or a Bearer header (Meta signs the body instead)"})


def _text_response(value, status):
    return Response(
        content=str(value or "").encode("utf-8"),
        status_code=status,
        media_type="text/plain",
    )


async def _bounded_body(request):
    """Read at most the shared webhook-envelope limit incrementally."""
    if whatsapp_chat._declared_body_too_large(request.headers):
        return None
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > whatsapp_chat.MAX_WEBHOOK_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/api/whatsapp/webhook", openapi_extra=_HANDSHAKE_DOCS)
@router.get("/api/whatsapp/webhook/", openapi_extra=_HANDSHAKE_DOCS)
async def meta_handshake(request: Request):
    if not whatsapp_chat._secret():
        return _json_response({"error": "not found"}, status=404)
    status, text = whatsapp_chat.meta_verification_result(
        parse_qs(request.url.query),
    )
    return _text_response(text, status)


@router.post("/api/whatsapp/webhook", openapi_extra=api_body.docs(post=(None, api_body.OkOut)) | _WEBHOOK_DOCS)
@router.post("/api/whatsapp/webhook/", openapi_extra=api_body.docs(post=(None, api_body.OkOut)) | _WEBHOOK_DOCS)
async def incoming_webhook(request: Request):
    if not whatsapp_chat.webhook_enabled():
        return _json_response({"error": "not found"}, status=404)

    # El body se lee ANTES de autorizar porque la firma de Meta se calcula
    # sobre los bytes crudos — no hay forma de validarla sin tenerlos. Lo que
    # un no autorizado puede hacer con eso ya está acotado por el mismo límite
    # de siempre (`_bounded_body`, 1 MB, corta mientras lee), así que el costo
    # es el de bufferear un sobre, nunca el de procesarlo.
    raw = await _bounded_body(request)
    if raw is None:
        return _json_response({"error": "body demasiado grande"}, status=413)

    query = parse_qs(request.url.query)
    if not (whatsapp_chat._secret_ok_headers(request.headers, query)
            or whatsapp_chat.meta_signature_ok(raw, request.headers)):
        return _json_response({"error": "no_autorizado"}, status=401)

    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:  # noqa: BLE001 - providers must not retry malformed events
        return _json_response({"ok": True})

    forwarded = bool(request.headers.get(whatsapp_chat.FORWARD_HEADER))
    await run_in_threadpool(whatsapp_chat.process_webhook_payload, body, forwarded)
    return _json_response({"ok": True})
