"""connectors/puco/puco_router.py — native FastAPI router for the PUCO
coverage lookup by DNI (docs/roadmap/native-migration-campaign-20260826.md,
family F9).

Real ``async def`` endpoint. Calls ``puco.procesar_consulta()``, the
pure domain function, off the event loop via
``run_in_threadpool`` (the SISA network call/cache lookups it performs are
blocking).

`connectors.puco.puco` IS `turnos`-owned (manifest module id
`connectors.puco.puco`), so `Depends(deps.require_module_enabled("turnos"))`
gates the router — unchanged from the legacy adapter.

  POST /api/puco   public, no auth (fair-use + abuse_guard from inside
                    procesar_consulta(), unchanged)"""

from fastapi import APIRouter, Depends, Request

from app.ai import abuse_guard
from app.core import api_body, deps
from app.connectors.puco import puco
from app.core.http_json import json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


class PucoIn(api_body.BodyModel):
    dni: str | None = None


class PucoOut(api_body.ResponseModel):
    """`obra_social` repeats the first insurer for the old cached JS."""
    disponible: bool
    obras_sociales: list[dict] | None = None
    obra_social: dict | None = None


_BODY = api_body.BodyContract(
    message=lambda _reason: "JSON inválido",
    respond=lambda _request, payload, status: _json_response(payload, status=status))
"""The plain «JSON inválido» this route always answered (mismo contrato que
handler._read_json())."""


@router.post("/api/puco", openapi_extra=api_body.docs(post=(PucoIn, PucoOut)))
async def api_puco(request: Request, body: api_body.Body = Depends(api_body.body(_BODY, PucoIn))):
    client = request.client
    ip = abuse_guard.client_ip(request.headers, client.host if client else "")

    def _consulta():
        return puco.procesar_consulta(body.value.get("dni"), ip)

    status, payload = await api_body.run(body, _consulta)
    return _json_response(payload, status)
