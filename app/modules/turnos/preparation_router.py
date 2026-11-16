"""preparation_router.py — Native ASGI implementation of `preparation.py`'s
two GET routes — the "Subí tu orden" referral pre-check (study-preparation
matching).

Real `async def` endpoints: `preparation._upload_order_page(qs)` and
`preparation._studies()` were ALREADY pure (parsed args in, bytes/data out
— no `handler` involved), so this router calls them directly through
`starlette.concurrency.run_in_threadpool` and builds the `Response` by hand
with the native byte contract shared through `core.gzip_bridge.body_for()`
(HTML: `text/html; charset=utf-8`, no
`Cache-Control`) and `handler._send_json()` (JSON: `application/json;
charset=utf-8`, `Cache-Control: no-store`), including transparent gzip.
`preparation` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET /upload-order              the pre-check page
  GET /api/preparation-studies   the study-preparation catalog, no auth

Only these two — only GET is served despite the manifest's
`["GET", "POST"]` methods list (the actual order upload/AI reading lives in
`booking_requests.py`). Both paths are literal (no path params), so there's
no "FastAPI matched but the module declined" case."""

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import api_body
from app.core import deps
from app.core import gzip_bridge
from app.modules.turnos import api_models, preparation
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


def _html_response(request, body):
    """Native HTML with transparent gzip when the client accepts it."""
    body, encoding = gzip_bridge.body_for(request, body)
    headers = {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body))}
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, status_code=200, media_type=None, headers=headers)


async def _upload_order(request: Request):
    qs = parse_qs(request.url.query)
    body = await run_in_threadpool(preparation._upload_order_page, qs)
    return _html_response(request, body)


async def _preparation_studies(request: Request):
    estudios = await run_in_threadpool(preparation._studies)
    return _json_response(request, {"estudios": estudios})


router.add_api_route("/upload-order", _upload_order, methods=["GET"])
router.add_api_route("/api/preparation-studies", _preparation_studies, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.PreparationStudiesOut)))
