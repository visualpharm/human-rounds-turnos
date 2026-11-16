"""interview_patient_router.py — native FastAPI router for
`interview_patient.py`'s six paths (family W2b of
docs/roadmap/native-migration-campaign-20260826.md), a SEPARATE file on
purpose.

Why a sibling file instead of adding `router = APIRouter()` straight into
`interview_patient.py`: the domain module imports no FastAPI, and
`module.json`'s router_attr route entry names this file by its OWN dotted
path (see docs/architecture/turnos-manifest.md, "Routes").

Every endpoint calls the pure domain functions (`resolve_entrevista_page`/
`interview_shell_body`/`interview_shell_embed_html`/`interview_shell_full_
html`/`resolve_qr_page`/`qr_page_html`/`qr_start_payload`/`load_interview`/
`transcript_payload`/`message_payload`/`reopen_payload`).

  GET  /entrevista/{sid}
  GET  /api/intake/t/{token}
  GET  /e/{code}
  POST /api/intake/t/{token}/message
  POST /api/intake/t/{token}/reopen
  POST /api/intake/qr/{code}/start

Rate limiting (`interview_patient._allowed`/`_client_ip`) and session
resolution (`booking_requests._get_user`) are module-level
functions — `_client_ip`/`_get_user` only ever
call `.headers.get(...)` on the object they're given, and a Starlette
`Request`'s `.headers` already duck-types that the same way (same
established convention as `ai/lab_results_router.py`'s own docstring, and
`proxy_trust.real_peer`'s ASGI branch, which never touches `.client_address`
at all when `TURNOS_ASGI` is on). `_allowed`'s per-IP sliding window (`_HITS`)
is genuine shared process state — every request in the process hits the
SAME dict.

`{sid}`/`{token}`/`{code}` are FastAPI's loosest path converter (any
non-slash string), but the legacy inner regexes were stricter (`\\d+` for
`sid`, `[A-Za-z0-9_-]+` for `token`, `[A-Za-z0-9_-]{1,64}` for `code`) — a
shape FastAPI matches but the inner regex would have declined gets the
migration campaign's shared terminal-404 contract (brief step 3), same
convention `modules/turnos/appointment_confirm_router.py` already
established for its own case."""

import urllib.parse

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core import api_body, db, deps
from app.core.proxy_trust import client_host as _client_host
from app.modules.intake import api_models, interview_patient
from app.modules.intake._native_support import (
    html_response, json_response, redirect_response, terminal_not_found_response,
)

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("intake"))])



async def _entrevista_page(request: Request, sid: str):
    if not sid.isdigit():
        return terminal_not_found_response()

    from app.modules.turnos import booking_requests

    qs = urllib.parse.parse_qs(request.url.query)
    link_token = (qs.get("t") or [""])[0].strip()
    usuario = None
    if not link_token:
        usuario = await run_in_threadpool(booking_requests._get_user, request)

    try:
        interview = await run_in_threadpool(
            interview_patient.resolve_entrevista_page, sid, link_token, usuario)
    except interview_patient.InterviewPageError as exc:
        html = await run_in_threadpool(
            interview_patient.denied_html, exc.mensaje, exc.href, exc.label)
        return html_response(request, html, status=exc.status)
    except interview_patient.InterviewRedirect as exc:
        return redirect_response(exc.location)
    except db.DbUnavailable:
        html = await run_in_threadpool(
            interview_patient.denied_html,
            "La base de datos no está disponible ahora.", "/my-requests", "Mis turnos")
        return deps.answer_db_unavailable(lambda status: html_response(request, html, status=status))

    embed = "embed=1" in (request.url.query or "")
    body = await run_in_threadpool(interview_patient.interview_shell_body, interview)
    if embed:
        document = await run_in_threadpool(interview_patient.interview_shell_embed_html, body)
        return html_response(request, document)
    html = await run_in_threadpool(interview_patient.interview_shell_full_html, body)
    return html_response(request, html)


async def _qr_page(request: Request, code: str):
    if not interview_patient.DOOR_CODE_SHAPE_RE.match(code):
        return terminal_not_found_response()

    from app.modules.turnos import booking_requests

    usuario = await run_in_threadpool(booking_requests._get_user, request)
    try:
        body = await run_in_threadpool(interview_patient.resolve_qr_page, code, usuario)
    except interview_patient.InterviewPageError as exc:
        html = await run_in_threadpool(
            interview_patient.denied_html, exc.mensaje, exc.href, exc.label)
        return html_response(request, html, status=exc.status)
    except db.DbUnavailable:
        html = await run_in_threadpool(
            interview_patient.denied_html,
            "La base de datos no está disponible ahora.", "/", "Ir al inicio")
        return deps.answer_db_unavailable(lambda status: html_response(request, html, status=status))

    html = await run_in_threadpool(interview_patient.qr_page_html, body)
    return html_response(request, html)


async def _qr_start(
        request: Request, code: str,
        pending: api_body.PendingBody = Depends(api_body.deferred(api_models.BODY, api_models.QrStartIn))):
    if not interview_patient.DOOR_CODE_SHAPE_RE.match(code):
        return terminal_not_found_response()

    ip = await run_in_threadpool(
        interview_patient._client_ip, request.headers, _client_host(request))
    if not await run_in_threadpool(interview_patient._allowed, ip):
        return json_response(
            {"error": "limite", "mensaje": "Probá de nuevo en un rato."}, status=429)

    body = await pending.read()

    try:
        payload = await api_body.run(
            body, interview_patient.qr_start_payload, code, body.value,
            expected=(interview_patient.InterviewApiError,))
    except interview_patient.InterviewApiError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _transcript(request: Request, token: str):
    if not interview_patient.TOKEN_SHAPE_RE.match(token):
        return terminal_not_found_response()
    try:
        interview = await run_in_threadpool(interview_patient.load_interview, token)
    except interview_patient.InterviewApiError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()

    ip = await run_in_threadpool(
        interview_patient._client_ip, request.headers, _client_host(request))
    allowed = await run_in_threadpool(interview_patient._allowed, ip)
    payload = await run_in_threadpool(interview_patient.transcript_payload, interview, allowed)
    return json_response(payload)


async def _message(
        request: Request, token: str,
        pending: api_body.PendingBody = Depends(api_body.deferred(api_models.BODY, api_models.InterviewMessageIn))):
    if not interview_patient.TOKEN_SHAPE_RE.match(token):
        return terminal_not_found_response()

    ip = await run_in_threadpool(
        interview_patient._client_ip, request.headers, _client_host(request))
    if not await run_in_threadpool(interview_patient._allowed, ip):
        return json_response({
            "error": "limite",
            "mensaje": "Hiciste varios mensajes seguidos. Esperá un rato y volvé a escribir.",
        }, status=429)

    try:
        interview = await run_in_threadpool(interview_patient.load_interview, token)
    except interview_patient.InterviewApiError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()

    if (interview.get("status") or "active") != "active":
        return json_response({"error": "entrevista_cerrada"}, status=409)

    body = await pending.read()

    try:
        payload = await api_body.run(
            body, interview_patient.message_payload, interview, body.value,
            expected=(interview_patient.InterviewApiError,))
    except interview_patient.InterviewApiError as exc:
        return json_response(exc.body, exc.status)
    return json_response(payload)


async def _reopen(request: Request, token: str):
    if not interview_patient.TOKEN_SHAPE_RE.match(token):
        return terminal_not_found_response()

    ip = await run_in_threadpool(
        interview_patient._client_ip, request.headers, _client_host(request))
    if not await run_in_threadpool(interview_patient._allowed, ip):
        return json_response(
            {"error": "limite", "mensaje": "Probá de nuevo en un rato."}, status=429)

    try:
        interview = await run_in_threadpool(interview_patient.load_interview, token)
    except interview_patient.InterviewApiError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()

    try:
        payload = await run_in_threadpool(interview_patient.reopen_payload, interview)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _case_guard(request: Request, token: str | None):
    """Shared front of the «Contanos tu caso» routes: token shape, the
    per-IP window of new conversations (a start only — the answers of one
    conversation are capped by code), the patient's session (only to seed
    the PII scrubber). Returns (usuario, error_response)."""
    from app.modules.intake import case_chat
    from app.modules.turnos import booking_requests

    if token is not None and not case_chat.TOKEN_RE.match(token):
        return None, terminal_not_found_response()
    if token is None:
        ip = await run_in_threadpool(
            interview_patient._client_ip, request.headers, _client_host(request))
        if not await run_in_threadpool(interview_patient.allowed_case_start, ip):
            return None, json_response({
                "error": "limite",
                "mensaje": "Hiciste varios mensajes seguidos. Esperá un rato y volvé a escribir.",
            }, status=429)
    usuario = await run_in_threadpool(booking_requests._get_user, request)
    return usuario, None


async def _case_start(request: Request):
    from app.modules.intake import case_chat

    usuario, error = await _case_guard(request, None)
    if error is not None:
        return error
    try:
        payload = await run_in_threadpool(case_chat.start, usuario)
    except db.DbUnavailable:
        return json_response({"disponible": False})
    return json_response(payload)


async def _case_message(
        request: Request, token: str,
        pending: api_body.PendingBody = Depends(api_body.deferred(api_models.BODY, api_models.CaseMessageIn))):
    from app.modules.intake import case_chat

    usuario, error = await _case_guard(request, token)
    if error is not None:
        return error
    body = await pending.read()
    texto = (body.value or {}).get("texto") if isinstance(body.value, dict) else None
    try:
        payload = await api_body.run(body, case_chat.message, token, texto, usuario,
                                     expected=(case_chat.CaseError,))
    except case_chat.CaseError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _case_finish(request: Request, token: str):
    from app.modules.intake import case_chat

    usuario, error = await _case_guard(request, token)
    if error is not None:
        return error
    try:
        payload = await run_in_threadpool(case_chat.finish, token, usuario)
    except case_chat.CaseError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


router.add_api_route("/entrevista/{sid}", _entrevista_page, methods=["GET"])
router.add_api_route("/api/intake/case/start", _case_start, methods=["POST"],
                     openapi_extra=api_body.docs(post=(None, api_models.CaseStartOut)))
router.add_api_route("/api/intake/case/{token}/message", _case_message, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.CaseMessageIn,
                                                       api_models.InterviewMessageOut)))
router.add_api_route("/api/intake/case/{token}/finish", _case_finish, methods=["POST"],
                     openapi_extra=api_body.docs(post=(None, api_body.OkOut)))
router.add_api_route("/api/intake/t/{token}", _transcript, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.InterviewTranscriptOut)))
router.add_api_route("/e/{code}", _qr_page, methods=["GET"])
router.add_api_route("/api/intake/t/{token}/message", _message, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.InterviewMessageIn,
                                                       api_models.InterviewMessageOut)))
router.add_api_route("/api/intake/t/{token}/reopen", _reopen, methods=["POST"],
                     openapi_extra=api_body.docs(post=(None, api_models.InterviewTranscriptOut)))
router.add_api_route("/api/intake/qr/{code}/start", _qr_start, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.QrStartIn, api_models.QrStartOut)))
