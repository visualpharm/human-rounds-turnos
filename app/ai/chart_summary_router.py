"""chart_summary_router.py — native-route migration (F8, 2026-08-26): the
FastAPI `router` twin of `chart_summary.py` — the one path that serves
"Resumen del paciente" (the pre-visit AI chart summary a doctor reads before
seeing a patient). Real native endpoints calling the domain functions
(`chart_summary.resolve_medico_id`, `chart_summary.generate`).

`chart_summary` IS `turnos`-owned (`modules/turnos/module.json`'s `routes`),
so this router needs `Depends(deps.require_module_enabled("turnos"))`, same
as every other router in this family.

  GET/POST /api/admin/patient-summary

Staff auth via `core.deps.require_admin(permitir_medico=True)` — the exact
same role flag `admin_auth.require_admin` always got from `chart_summary.
handle()`. A 'medico' role always gets their OWN médico_id (query/body param
ignored, same criterion `resolve_medico_id` encodes); admin/operador supply
one explicitly."""

import json

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.ai import api_models, chart_summary
from app.core import access_log, api_body
from app.core import db
from app.core import deps
from app.core.http_json import json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


def _posted(cuerpo):
    return str(cuerpo.get("dni") or "").strip(), str(cuerpo.get("medico") or "").strip()


def _loose(raw):
    """A body that does not decode reads as `{}`."""
    try:
        return json.loads(raw or b"{}")
    except (ValueError, TypeError):
        return {}


async def _patient_summary(identity: dict, dni, medico_param, force):
    if not dni:
        return _json_response({"error": "falta dni"}, status=400)

    try:
        medico_id = chart_summary.resolve_medico_id(identity, medico_param)
    except chart_summary.PatientSummaryError as exc:
        return _json_response(exc.body, status=exc.status)

    # K10: sólo el paciente propio, y cada lectura (o intento) queda en el
    # registro de accesos — antes un médico pedía el resumen de cualquier DNI.
    try:
        await run_in_threadpool(access_log.check, identity, "resumen_ia", dni, medico_id)
    except (access_log.AccessDenied, access_log.AccessLogUnavailable) as exc:
        status, body = access_log.error_body(exc)
        return _json_response(body, status=status)

    try:
        out = await run_in_threadpool(chart_summary.generate, dni, medico_id, force)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    if not out:
        return _json_response({"error": "no se pudo generar"}, status=500)
    return _json_response(out)


async def patient_summary(
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_medico=True)),
    body: api_body.Body = Depends(api_body.lenient(
        api_models.BODY, api_models.PatientSummaryIn, _loose, methods={"POST"})),
    # One route object serves GET and POST: `query_docs` below documents these on GET alone.
    dni: str | None = Query(None, include_in_schema=False),
    medico: str | None = Query(None, include_in_schema=False),
    refresh: str | None = Query(None, include_in_schema=False),
):
    if request.method == "GET":
        return await _patient_summary(identity, (dni or "").strip(), (medico or "").strip(),
                                      (refresh or "") == "1")
    posted_dni, posted_medico = api_body.call(body, _posted, body.value)
    return await _patient_summary(identity, posted_dni, posted_medico, True)


# ONE route object for both methods (`methods=["GET", "POST"]` on a single
# `add_api_route`, not two `@router.get`/`@router.post` decorators) — that
# keeps `route_objects` unchanged from the adapter-backed baseline, which
# mounted GET+POST as a single Starlette route too.
router.add_api_route(
    "/api/admin/patient-summary", patient_summary, methods=["GET", "POST"],
    openapi_extra=api_body.docs(get=(None, api_models.PatientSummaryOut),
                                post=(api_models.PatientSummaryIn, api_models.PatientSummaryOut))
    | api_body.query_docs(get={
        "dni": "The patient's DNI",
        "medico": "For an admin: the doctor the summary is for (its id); a doctor's session is its own",
        "refresh": api_body.Query("`1`: generate it again instead of the stored one",
                                  {"type": "string", "enum": ["1"]}),
    }))
