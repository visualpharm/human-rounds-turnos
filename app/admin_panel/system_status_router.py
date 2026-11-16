"""Native FastAPI transport for the administrator system-status API.

The route is platform core, so it is mounted unconditionally and protected by
the administrator-only dependency. It passes the native request directly to
the status reader and owns JSON serialization and gzip negotiation.
"""

import json

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import api_body, deps, gzip_bridge
from app.admin_panel import hsi_status, reports_api_models as models, system_status

router = APIRouter()


def _json_response(request, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    body, encoding = gzip_bridge.body_for(request, body)
    headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, status_code=200, headers=headers)


def _status_response(request):
    return _json_response(request, system_status.status(request))


def _hsi_response(request):
    """How a turno reaches HSI on this installation, why, and each center's
    institution number: what `/admin/hsi` shows before (or instead of) the
    journal. Read-only — the registry, two settings, the extension's files,
    the catalog; never a socket (`admin_panel/hsi_status.py`)."""
    return _json_response(request, hsi_status.snapshot())


@router.get("/api/admin/system", dependencies=[Depends(deps.require_admin(solo_admin=True))],
            openapi_extra=api_body.docs(get=(None, models.SystemStatusOut)))
async def system(request: Request):
    return await run_in_threadpool(_status_response, request)


@router.get("/api/admin/system/hsi", dependencies=[Depends(deps.require_admin(solo_admin=True))],
            openapi_extra=api_body.docs(get=(None, models.HsiStatusOut)))
async def system_hsi(request: Request):
    return await run_in_threadpool(_hsi_response, request)
