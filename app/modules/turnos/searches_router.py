"""searches_router.py — Native ASGI implementation of `searches.py`'s single
route: the client-side search-logging beacon (search.js posts what a
visitor typed, for the "Búsquedas" report).

Real `async def` endpoint: the raw body is read natively, the blocking domain
call (`searches.build_search_log`, which may hit Postgres via
`db_analytics.log_search`) runs through `starlette.concurrency.run_in_threadpool`,
and the `Response` is built natively with the public response contract
(Content-Type/Cache-Control/Content-Length for the JSON error cases, a bare
204 with `Content-Length: 0` and no body for the success case).

`searches` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  POST /api/search-log   public, no auth (a search happens before login)"""

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.auth import turnstile
from app.core import api_body, deps
from app.modules.turnos import api_models, searches
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


def _no_content_response():
    """Bare 204 with an explicit length and no content type or body."""
    return Response(content=b"", status_code=204, media_type=None,
                     headers={"Content-Length": "0"})


async def _search_log(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.WITH_REASON, api_models.SearchLogIn))):

    def _call():
        client = request.client
        ip = turnstile.client_ip_from(
            request.headers, client.host if client else "")
        return searches.build_search_log(ip, body.raw, request.headers.get("user-agent", ""))

    status, payload = await api_body.run(body, _call)
    if payload is None:
        return _no_content_response()
    return _json_response(request, payload, status)


async def _event_batch(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.WITH_REASON, api_models.EventBatchIn))):
    """Always a bare 204: the beacon never learns whether the stream is on, off or full."""

    def _call():
        import json
        from app.modules.turnos import analytics_export
        if len(body.raw or b"") > 4096:
            return None
        client = request.client
        ip = turnstile.client_ip_from(request.headers, client.host if client else "")
        try:
            decoded = json.loads(body.raw.decode("utf-8")) if body.raw else {}
        except Exception:  # noqa: BLE001
            return None
        host = (request.headers.get("host") or "").split(":", 1)[0]
        analytics_export.ingest(decoded, ip=ip, user_agent=request.headers.get("user-agent", ""),
                                own_host=host)
        return None

    await api_body.run(body, _call)
    return _no_content_response()


router.add_api_route("/api/search-log", _search_log, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.SearchLogIn, None, 204)))
router.add_api_route("/api/ev", _event_batch, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.EventBatchIn, None, 204)))
