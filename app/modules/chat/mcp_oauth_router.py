"""Native ASGI routes of the MCP OAuth flow (logic and rationale: ``mcp_oauth``)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from app.core import deps, ids, site_identity
from app.core.terminal_response import redirect_response
from app.cuentas import account_deps, accounts
from app.modules.chat import mcp_api, mcp_oauth

# Protocol endpoints (RFC 8414/9728/7591/6749) described by OAuth, not by the JSON schema.
router = APIRouter(dependencies=[Depends(deps.require_module_enabled("chat"))], include_in_schema=False)

_NO_STORE = {"Cache-Control": "no-store"}
_MAX_BODY = 16 * 1024


def _json(status: int, body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=_NO_STORE)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource(request: Request):
    if mcp_api.connection_closed():
        return _json(404, {"error": "not_found"})
    return _json(200, mcp_oauth.protected_resource_metadata(site_identity.base_url(request)))


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
@router.get("/.well-known/openid-configuration")
async def authorization_server(request: Request):
    if mcp_api.connection_closed():
        return _json(404, {"error": "not_found"})
    return _json(200, mcp_oauth.authorization_server_metadata(site_identity.base_url(request)))


async def _body(request: Request) -> bytes:
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > _MAX_BODY:
            return b""
    return raw


async def _form(request: Request) -> dict:
    parsed = parse_qs((await _body(request)).decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}


@router.post("/oauth/register")
async def register(request: Request):
    try:
        data = json.loads((await _body(request)) or b"{}")
    except ValueError:
        data = None
    if mcp_api.connection_closed():
        return _json(404, {"error": "not_found"})
    status, body = mcp_oauth.register(data)
    return _json(status, body)


def _page(request: Request, status: int, **ctx) -> Response:
    return Response(accounts.render_oauth_consent_body(request, **ctx), status_code=status,
                    media_type="text/html; charset=utf-8", headers=_NO_STORE)


@router.get("/oauth/authorize")
async def authorize(request: Request, usuario: dict | None = Depends(account_deps.optional_account)):
    params = dict(request.query_params)
    if mcp_api.connection_closed():
        return _page(request, 400, error="closed")
    req, reason = mcp_oauth.check_authorize(params)
    if req is None:
        return _page(request, 400, error=reason)
    if not usuario:
        here = "/oauth/authorize?" + urlencode(params)
        portal = accounts.auth_redirect_target(None, here)
        return redirect_response(portal or "/account?" + urlencode({"mode": "login", "next": here}))
    dni = ids.dni(usuario.get("dni"))
    return _page(request, 200, error="", name=req["name"], host=mcp_oauth.redirect_host(req["redirect_uri"]),
                 consent=mcp_oauth.consent_token(req, dni))


@router.post("/oauth/authorize")
async def authorize_decision(request: Request, usuario: dict | None = Depends(account_deps.optional_account)):
    form = await _form(request)
    if mcp_api.connection_closed():
        return _page(request, 400, error="closed")
    if not usuario:
        return _page(request, 401, error="session")
    allow = form.get("decision") == "allow"
    target = mcp_oauth.decide(form.get("consent", ""), ids.dni(usuario.get("dni")), allow)
    if not target:
        return _page(request, 400, error="consent")
    return redirect_response(target)


def _issue(dni: str):
    from app.core import db_accounts
    return db_accounts.mcp_token_regenerate(dni)


@router.post("/oauth/token")
async def token(request: Request):
    form = await _form(request)
    if mcp_api.connection_closed():
        return _json(400, {"error": "unauthorized_client"})
    try:
        status, body = await run_in_threadpool(mcp_oauth.exchange, form, _issue)
    except deps.DB_UNAVAILABLE_ERRORS:
        return deps.answer_db_unavailable(lambda s: _json(s, {"error": "temporarily_unavailable"}))
    return _json(status, body)
