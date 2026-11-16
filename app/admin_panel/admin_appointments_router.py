"""admin_appointments_router.py — native ASGI implementation of the front-
desk/phone walk-in booking (creates an already-APPROVED request) and the
slots-plus-overbooking API the operator's calendar view reads.

Native-migration split (docs/roadmap/native-migration-campaign-20260826.md,
family F3): `admin_appointments.py` was split into pure domain functions
(`slots_data`, `create_appointment` — take parsed arguments, return data or
raise `admin_panel.native_json.ApiError`). This router calls those domain functions directly: FastAPI parses
the request, `starlette.concurrency.run_in_threadpool` runs the
(synchronous, psycopg-calling) domain function, and `admin_panel.
native_json` builds the byte-identical `Response`.

`admin_appointments` IS `turnos`-owned, so `Depends(deps.
require_module_enabled("turnos"))` gates this router exactly as the
pre-migration adapter did.

Both paths share the same role gate as the former transport
(`permitir_admisionista=True`, `permitir_medico=True`):

  POST /api/admin/appointments
  GET  /api/admin/appointment-slots

Neither path has a path parameter, so there is no inner-regex-decline case
to reproduce as a terminal 404 (unlike `admin_patients_router.py`'s numeric
DNI segments)."""

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.admin_panel import admin_appointments, api_models
from app.admin_panel.native_json import ApiError, json_response
from app.core import api_body, deps, hsi_extension

router = APIRouter(
    dependencies=[Depends(deps.require_module_enabled("turnos"))],
)

_require_desk = Depends(deps.require_admin(permitir_admisionista=True, permitir_medico=True))


async def _own_doctor(request: Request, identity: dict = _require_desk):
    """A doctor's session books with its own doctor: its name, or the refusal, ahead of the body."""
    try:
        return await run_in_threadpool(admin_appointments._own_doctor_name, identity)
    except ApiError as err:
        raise deps.EarlyResponse(json_response(request, err.body, status=err.status)) from None


@router.post("/api/admin/appointments", openapi_extra=api_body.docs(
    post=(api_models.DeskAppointmentIn, api_models.DeskAppointmentOut)))
async def native_appointments_create(
        request: Request, identity: dict = _require_desk, propio: str | None = Depends(_own_doctor),
        body: api_body.Body = Depends(api_body.body(api_models.ADMIN_GZIP, api_models.DeskAppointmentIn))):
    try:
        result = await api_body.run(
            body, admin_appointments.create_appointment, identity, propio, body.value,
            expected=(ApiError,))
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


# Registered AFTER the POST on purpose: the pinned composition inventory
# (tests/tier4_composition_inventory.json) preserves the pre-extraction
# registration order, which is the routing contract.
@router.get("/api/admin/appointment-slots",
            openapi_extra=api_body.docs(get=(None, api_models.AppointmentSlotsOut)) | api_body.query_docs(get={
    "servicio_id": "The service (required: an unknown one is a 400)",
    "medico": "Only this doctor's free slots (its name); a doctor's session is its own",
    "respaldo": api_body.Query("`servicio`: a doctor with no slots of their own here gets the service's",
                               {"type": "string", "enum": ["servicio"]}),
    "derivacion": api_body.Query("`1`: the slots of a service the request is referred to, even at a centre outside the session's", {"type": "string", "enum": ["1"]}),
}))
async def native_appointment_slots(request: Request, identity: dict = _require_desk):
    qs = parse_qs(request.url.query)
    try:
        result = await run_in_threadpool(admin_appointments.slots_data, identity, qs)
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


# The desk's HSI extension, for the computer that does not have it yet
# (`core.hsi_extension.install()`): the guide PDF and, while the store page
# is not published, the ZIP loaded by hand. Every desk role may read them —
# the line that links here shows wherever «Cargar en HSI» can show.
@router.get("/api/admin/hsi-extension/{name}")
async def native_hsi_extension_file(name: str, identity: dict = _require_desk):
    path = hsi_extension.file_path(name)
    if not path:
        return Response(status_code=404)
    download = hsi_extension.FILES[name][2]
    body = await run_in_threadpool(_read_bytes, path)
    return Response(content=body, status_code=200, headers={
        "Content-Type": hsi_extension.FILES[name][1],
        "Cache-Control": "no-store",
        "Content-Disposition": 'attachment; filename="%s"' % download,
    })


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()
