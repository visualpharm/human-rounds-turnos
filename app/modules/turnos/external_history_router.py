"""Manifest-owned GET only; no core-to-vendor or domain-to-domain imports."""
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core import api_body, deps
from . import api_models, external_history

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


@router.get("/api/admin/patient-external-history",
            openapi_extra=api_body.docs(get=(None, api_models.ExternalHistoryOut)))
async def patient_external_history(
    identity: dict = Depends(deps.require_admin(permitir_medico=True)),
    dni: str = Query("", description="The patient's DNI, 6 to 10 digits (anything else is a 400)"),
):
    dni = dni.strip()
    if not re.fullmatch(r"[0-9]{6,10}", dni):
        return JSONResponse({"error": "invalid_dni"}, status_code=400,
                            headers={"Cache-Control": "no-store"})
    result = await run_in_threadpool(external_history.read, identity, dni)
    status = 403 if result["reason"] == "forbidden" else 200
    return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})
