"""Native ASGI transport for the chat module's stateless MCP server.

Every endpoint runs its sync work through ``starlette.concurrency.run_in_threadpool``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import api_body
from app.core import deps
from app.core import site_identity
from app.cuentas import account_deps
from app.modules.chat import api_models, mcp_api


router = APIRouter(dependencies=[Depends(deps.require_module_enabled("chat"))])


def _response(result: mcp_api.McpResult):
    return Response(
        content=result.body,
        status_code=result.status,
        media_type=None,
        headers={key: value for key, value in result.headers},
    )


async def _bounded_body(request):
    if mcp_api.declared_body_too_large(request.headers):
        return None
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > mcp_api.MAX_MCP_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.api_route("/mcp", methods=["GET", "POST", "DELETE"])
@router.api_route("/mcp/", methods=["GET", "POST", "DELETE"])
async def mcp_endpoint(request: Request):
    if await run_in_threadpool(mcp_api.connection_closed):
        # Closed installation (salud): the path answers what a path that does not exist
        # answers, byte for byte, for every method — before the body is read or a token is
        # looked at, so a token issued before the closure calls nothing either.
        from app.routers import native_terminal
        return await native_terminal.terminal(request.url.path.lstrip("/"), request)
    raw = b""
    if request.method == "POST":
        raw = await _bounded_body(request)
        if raw is None:
            return _response(mcp_api.reject_body_too_large())
    result = await run_in_threadpool(
        mcp_api.process_mcp_request, request.method, request.headers, raw)
    return _response(result)


async def _account_result(request: Request, usuario, regenerate: bool):
    """`mcp_api.process_account_request` answers the anonymous visitor (None) its own 401."""
    base_url = site_identity.base_url(request)
    return await run_in_threadpool(
        mcp_api.process_account_request, usuario, base_url, regenerate)


_CONNECTION_DOCS = api_body.docs(get=(None, api_models.McpConnectionOut),
                                 post=(None, api_models.McpConnectionOut))


@router.get("/api/account/mcp", openapi_extra=_CONNECTION_DOCS)
@router.get("/api/account/mcp/", openapi_extra=_CONNECTION_DOCS)
async def account_connection(request: Request, usuario: dict | None = Depends(account_deps.optional_account)):
    return _response(await _account_result(request, usuario, False))


@router.post("/api/account/mcp/regenerate", openapi_extra=_CONNECTION_DOCS)
@router.post("/api/account/mcp/regenerate/", openapi_extra=_CONNECTION_DOCS)
async def regenerate_account_connection(request: Request,
                                        usuario: dict | None = Depends(account_deps.optional_account)):
    return _response(await _account_result(request, usuario, True))


_REVOKE_DOCS = api_body.docs(post=(None, api_models.McpRevokedOut))


@router.post("/api/account/mcp/revoke", openapi_extra=_REVOKE_DOCS)
@router.post("/api/account/mcp/revoke/", openapi_extra=_REVOKE_DOCS)
async def revoke_account_connection(usuario: dict | None = Depends(account_deps.optional_account)):
    return _response(await run_in_threadpool(mcp_api.process_account_revoke, usuario))
