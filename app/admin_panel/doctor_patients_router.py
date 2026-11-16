"""Native FastAPI router for a doctor's own longitudinal patient roster.

`doctor_patients` IS `turnos`-owned, so `Depends(deps.
require_module_enabled("turnos"))` gates this router exactly as every
sibling router does.

The endpoint gates with `permitir_medico=True` and calls the value-only
`build_payload()` domain function directly.

  GET /api/admin/doctor-patients[?medico=<id>]
"""

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.admin_panel import api_models, doctor_patients
from app.admin_panel._native_support import ApiError, gate, json_response
from app.core import api_body, db, deps

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


@router.get("/api/admin/doctor-patients", openapi_extra=api_body.docs(
    get=(None, api_models.DoctorPatientsOut)) | deps.STAFF_GATE_DOCS)
async def get_doctor_patients(request: Request, medico: str = Query(
        "", description="For an admin: whose patients (the doctor's id); a doctor's session is its own")):
    try:
        identity = await gate(request, permitir_medico=True)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        payload = await run_in_threadpool(doctor_patients.build_payload, identity, medico)
    except doctor_patients.DoctorPatientsError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    except db.DbUnavailable:
        # Incluye access_log.AccessLogUnavailable: sin registro, no hay lista.
        return deps.answer_db_unavailable()
    return json_response(payload)
