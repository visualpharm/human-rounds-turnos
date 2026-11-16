"""sitemap_router.py — Tier 4 slice 8 closing batch (docs/roadmap/
tier4-replatform-design.md's own honest inventory): the FastAPI `router`
twin of `sitemap.py` — `/sitemap.xml`.

Native-migration campaign, family W2c (docs/roadmap/native-migration-
campaign-20260826.md): a real `async def` endpoint.
Calls `sitemap.xml()` and builds the response with the shared
gzip-if-accepted contract from `gzip_bridge.body_for()`.

`sitemap` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET /sitemap.xml   public, no auth"""

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.core import deps
from app.core import gzip_bridge
from app.patient_web import sitemap

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


@router.get("/sitemap.xml")
async def get_sitemap(request: Request):
    body = await run_in_threadpool(sitemap.xml)
    body, encoding = gzip_bridge.body_for(request, body)
    headers = {"Content-Type": "application/xml; charset=utf-8",
               "Cache-Control": "public, max-age=3600",
               "Content-Length": str(len(body))}
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, media_type=None, headers=headers)
