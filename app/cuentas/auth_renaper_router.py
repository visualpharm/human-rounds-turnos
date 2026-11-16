"""auth_renaper_router.py — native ASGI transport for the RENAPER/Mi
Argentina federated-login entry points.

Real `async def` endpoints. Both endpoints call the domain
decisions in `cuentas.auth_renaper` (`start_flow_decision` and
`callback_decision`), keeping the state cookie, anti-lockout gate, rate limit,
DNI extraction, and session minting independent of HTTP response mechanics.

Cookie/header reads use `request.headers`/`request.cookies` directly. The one
truly synchronous surface —
the real (non-fake) IDP token/userinfo round trip in `_claims_reales()`,
plus `db_accounts.login_renaper`/`db_accounts.create_session` — moves off the event loop via
`run_in_threadpool`.

  GET /auth/renaper            start the flow
  GET /auth/renaper/callback   return leg from the IDP
"""

import urllib.parse

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.cuentas import accounts
from app.cuentas import auth_renaper

router = APIRouter()


def _client_ip(request: Request) -> str:
    remote = request.client.host if request.client else None
    return auth_renaper._client_ip_from(request.headers.get("X-Forwarded-For"), remote)


def _redirect_response(location, cookies=()):
    headers = [
        (b"location", location.encode("latin-1")),
        (b"content-length", b"0"),
        (b"cache-control", b"no-store"),
    ]
    for cookie in cookies:
        headers.append((b"set-cookie", cookie.encode("latin-1")))
    response = Response(status_code=302, content=b"")
    response.raw_headers = headers
    return response


def _error_response(titulo, detalle):
    body = auth_renaper._pagina_error_body(titulo, detalle)
    response = Response(content=body, status_code=400)
    response.raw_headers = [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"content-length", str(len(body)).encode("latin-1")),
        (b"cache-control", b"no-store"),
        (b"set-cookie", auth_renaper._clear_estado().encode("latin-1")),
    ]
    return response


@router.get("/auth/renaper")
async def get_auth_renaper(request: Request):
    qs = urllib.parse.parse_qs(request.url.query)
    ip = _client_ip(request)
    decision = await run_in_threadpool(auth_renaper.start_flow_decision, qs, ip)
    if decision[0] == "login_only":
        return _redirect_response("/account?mode=login")
    if decision[0] == "rate_limited":
        return _error_response("Demasiados intentos", "Probá de nuevo en un rato.")
    _, cookie_value, url = decision
    return _redirect_response(url, cookies=[auth_renaper._set_estado(cookie_value)])


@router.get("/auth/renaper/callback")
async def get_auth_renaper_callback(request: Request):
    qs = urllib.parse.parse_qs(request.url.query)
    state_cookie_value = request.cookies.get(auth_renaper.STATE_COOKIE)
    decision = await run_in_threadpool(auth_renaper.callback_decision, state_cookie_value, qs)
    if decision[0] == "error":
        _, titulo, detalle = decision
        return _error_response(titulo, detalle)
    _, token, next_path = decision
    return _redirect_response(
        next_path, cookies=[accounts._set_cookie(token), auth_renaper._clear_estado()])
