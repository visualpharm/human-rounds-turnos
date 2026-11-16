"""Native ASGI ownership of the canonical availability directory endpoint.

The retired legacy handler used to serve the dark transport and URL-rewrite
compatibility; this direct response path preserves the legacy JSON,
cache and gzip contract without proxying through ``server.py``.
"""

import json

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.core import api_body, deps, gzip_bridge
from app.patient_web import api_models, availability_directory


router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


def specialties_response(request):
    body = json.dumps(availability_directory.especialidades_tree(), ensure_ascii=False).encode("utf-8")
    body, encoding = gzip_bridge.body_for(request, body)
    headers = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=body, status_code=200, headers=headers)


@router.get("/api/specialties", openapi_extra=api_body.docs(get=(None, api_models.SpecialtiesOut)))
async def specialties(request: Request):
    return specialties_response(request)
