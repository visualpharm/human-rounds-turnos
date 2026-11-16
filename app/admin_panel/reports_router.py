"""reports_router.py — native FastAPI transport for `admin_panel/reports.py`
(native-route migration campaign, family F4, see
docs/roadmap/native-migration-campaign-20260826.md).

The paths below call pure `_*_payload()`/`_*_csv_rows()` domain
functions directly. Native HTTP is the sole reporting transport.

Every path is a plain literal string (no `{...}` template, no regex) and
GET-only, except `POST /api/admin/reports/funnel/steps`, which saves the
step setting of «Recorrido» (`admin_panel/reports_funnel.py`). With no
template there is no "FastAPI matched a template but the inner regex
declined" case to cover with a terminal 404 here.

Auth: every route uses `core.deps.require_admin(solo_admin=True,
permitir_reportes=True)`, hoisted once to the router. `reports` is
`turnos`-owned, so `deps.require_module_enabled("turnos")` stays too.

Response contract parity:
  - JSON routes: `application/json; charset=utf-8`, `Cache-Control:
    no-store`, and gzip negotiation (`core.gzip_bridge`, >=1024 bytes +
    `Accept-Encoding: gzip`).
  - CSV routes: utf-8-sig BOM, `text/csv; charset=utf-8`,
    `Content-Disposition: attachment`, no gzip.
  - DB down (`reports_store.DbUnavailable`): 503 JSON on every route except
    `/reviews` (file-based and DB-independent).
"""

import json

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import access_log, api_body, deps, i18n
from app.admin_panel import api_models, reports
from app.admin_panel import reports_funnel
from app.admin_panel import reports_store as store
from app.admin_panel import reports_api_models as models
from app.core.http_json import gzip_json_response as _json_response
from app.modules.turnos.api_models import AnalyticsVisitsOut

router = APIRouter(dependencies=[
    Depends(deps.require_module_enabled("turnos")),
    Depends(deps.require_admin(solo_admin=True, permitir_reportes=True)),
])


def _days(default):
    return api_body.Query(f"The period in days: 7, 30 or 90; anything else is {default}",
                          {"type": "integer", "enum": list(reports._PERIODS)})


_DAYS = api_body.query_docs(get={"days": _days(reports._DEFAULT_DAYS)})
_DAYS_90 = api_body.query_docs(get={"days": _days(reports._DEFAULT_DAYS_CSV)})
_MONTH = api_body.query_docs(get={"mes": api_body.Query(
    "The calendar month, `YYYY-MM`; anything else is the last complete month")})
_FUNNEL = api_body.query_docs(get={
    "days": _days(reports._DEFAULT_DAYS),
    "center": "Only the requests of this centre (its slug)",
    "service": "Only the requests of this service (its id)",
    "doctor": "Only the requests of this doctor (its id)",
})

def _db_error_response(request):
    return deps.answer_db_unavailable(
        body={"error": i18n.t_or("admin.srv.error.db_no_disponible", "base de datos no disponible", i18n.active())},
        request=request,
    )


def _json_route(request, compute):
    """Run sync database work and translate `DbUnavailable` into the shared
    503 JSON error."""
    try:
        payload = compute()
    except store.DbUnavailable:
        return _db_error_response(request)
    return _json_response(request, payload)


def _csv_route(request, filename, header, compute_rows, identity=None):
    """`identity` is given by a CSV whose rows name patients (a `dni` column):
    every patient in the file is written to the clinical access log
    (`descarga`, target = the route path) BEFORE the file is answered, and a
    file whose rows cannot be written is not given (security review 2026-11, M-3)."""
    try:
        rows = compute_rows()
    except store.DbUnavailable:
        return _db_error_response(request)
    if identity is not None and "dni" in header:
        col = header.index("dni")
        items = [(d, request.url.path) for d in dict.fromkeys(r[col] for r in rows if r[col])]
        try:
            access_log.record_many(identity, "descarga", items)
        except (access_log.AccessLogUnavailable, access_log.AccessLogFailed) as exc:
            if isinstance(exc, access_log.AccessLogUnavailable):
                status, payload = access_log.error_body(exc)
            else:
                status, payload = 500, {"error": "registro_de_accesos_no_disponible"}
            return Response(content=json.dumps(payload), status_code=status,
                            media_type="application/json", headers={"Cache-Control": "no-store"})
    body = reports._csv_bytes(header, rows)
    return Response(
        content=body,
        status_code=200,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/api/admin/reports",
            openapi_extra=api_body.docs(get=(None, models.ReportsOut)) | _DAYS)
async def reports_management(request: Request):
    dias = reports._days_param(request.url.query)
    return await run_in_threadpool(_json_route, request, lambda: reports._reports_payload(dias))


@router.get("/api/admin/reports/requests.csv", openapi_extra=_DAYS_90)
async def reports_requests_csv(request: Request):
    dias = reports._days_param(request.url.query, default=reports._DEFAULT_DAYS_CSV)
    return await run_in_threadpool(
        _csv_route, request, reports._REQUESTS_CSV_FILENAME, reports._REQUESTS_CSV_HEADER,
        lambda: reports._requests_csv_rows(dias),
    )


@router.get("/api/admin/reports/patients",
            openapi_extra=api_body.docs(get=(None, models.PatientsReportOut)) | _DAYS)
async def reports_patients(request: Request):
    dias = reports._days_param(request.url.query)
    return await run_in_threadpool(_json_route, request, lambda: reports._patients_payload(dias))


@router.get("/api/admin/reports/login-panel",
            openapi_extra=api_body.docs(get=(None, models.LoginPanelReportOut)) | _DAYS)
async def reports_login_panel(request: Request):
    dias = reports._days_param(request.url.query)
    return await run_in_threadpool(_json_route, request, lambda: reports._login_panel_payload(dias))


@router.get("/api/admin/reports/visits",
            openapi_extra=api_body.docs(get=(None, AnalyticsVisitsOut)) | _DAYS)
async def reports_visits(request: Request):
    from app.modules.turnos import analytics_report
    dias = reports._days_param(request.url.query)
    return await run_in_threadpool(_json_route, request, lambda: analytics_report.payload(dias))


@router.get("/api/admin/reports/searches",
            openapi_extra=api_body.docs(get=(None, models.SearchesReportOut)) | _DAYS)
async def reports_searches(request: Request):
    dias = reports._days_param(request.url.query)
    return await run_in_threadpool(_json_route, request, lambda: reports._searches_full_payload(dias))


@router.get("/api/admin/reports/reviews",
            openapi_extra=api_body.docs(get=(None, models.ReviewsReportOut)))
async def reports_reviews(request: Request):
    # File-based (resenas-analisis.json / centros-resenas.json), so it never
    # touches the DB and does not need a DbUnavailable branch.
    return await run_in_threadpool(lambda: _json_response(request, reports._reviews_payload()))


@router.get("/api/admin/reports/cost-recovery",
            openapi_extra=api_body.docs(get=(None, models.CostRecoveryReportOut)) | _DAYS_90)
async def reports_cost_recovery(request: Request):
    dias = reports._days_param(request.url.query, default=reports._DEFAULT_DAYS_CSV)
    return await run_in_threadpool(_json_route, request, lambda: reports._cost_recovery_payload(dias))


@router.get("/api/admin/reports/cost-recovery.csv", openapi_extra=_DAYS_90)
async def reports_cost_recovery_csv(request: Request):
    identity = await deps.check_admin(request, solo_admin=True, permitir_reportes=True)
    dias = reports._days_param(request.url.query, default=reports._DEFAULT_DAYS_CSV)
    return await run_in_threadpool(
        _csv_route, request, reports._COST_RECOVERY_CSV_FILENAME, reports._COST_RECOVERY_CSV_HEADER,
        lambda: reports._cost_recovery_csv_rows(dias), identity,
    )


@router.get("/api/admin/reports/sumar",
            openapi_extra=api_body.docs(get=(None, models.SumarReportOut)) | _DAYS_90)
async def reports_sumar(request: Request):
    dias = reports._days_param(request.url.query, default=reports._DEFAULT_DAYS_CSV)
    return await run_in_threadpool(_json_route, request, lambda: reports._sumar_payload(dias))


@router.get("/api/admin/reports/sumar.csv", openapi_extra=_DAYS_90)
async def reports_sumar_csv(request: Request):
    identity = await deps.check_admin(request, solo_admin=True, permitir_reportes=True)
    dias = reports._days_param(request.url.query, default=reports._DEFAULT_DAYS_CSV)
    return await run_in_threadpool(
        _csv_route, request, reports._SUMAR_CSV_FILENAME, reports._SUMAR_CSV_HEADER,
        lambda: reports._sumar_csv_rows(dias), identity,
    )


@router.get("/api/admin/reports/statistics",
            openapi_extra=api_body.docs(get=(None, models.StatisticsReportOut)) | _MONTH)
async def reports_statistics(request: Request):
    year, month = reports._month_param(request.url.query)
    return await run_in_threadpool(_json_route, request, lambda: reports._statistics_payload(year, month))


@router.get("/api/admin/reports/statistics.csv", openapi_extra=_MONTH)
async def reports_statistics_csv(request: Request):
    year, month = reports._month_param(request.url.query)
    return await run_in_threadpool(
        _csv_route, request, reports._STATISTICS_CSV_FILENAME, reports._STATISTICS_CSV_HEADER,
        lambda: reports._statistics_csv_rows(year, month),
    )


@router.get("/api/admin/reports/capacity",
            openapi_extra=api_body.docs(get=(None, models.CapacityReportOut)))
async def reports_capacity(request: Request):
    return await run_in_threadpool(_json_route, request, lambda: reports._capacity_payload())


@router.get("/api/admin/reports/funnel",
            openapi_extra=api_body.docs(get=(None, models.PatientPathReportOut)) | _FUNNEL)
async def reports_patient_path(request: Request):
    dias, centro, servicio, medico = reports_funnel.params(request.url.query)
    return await run_in_threadpool(
        _json_route, request, lambda: reports_funnel.payload(dias, centro, servicio, medico))


@router.post("/api/admin/reports/funnel/steps",
             openapi_extra=api_body.docs(post=(api_models.FunnelStepsIn, models.PatientPathStepsOut)))
async def reports_patient_path_steps(
        request: Request,
        # A malformed body reads as None (a 400 below, never echoed); a decoded one is checked against the model.
        body: api_body.RawRead = Depends(api_body.raw(None, api_models.FunnelStepsIn))):
    """Saves the order and on/off of the «Recorrido» steps — the one write of this router."""
    steps = body.value.get("steps") if isinstance(body.value, dict) else None

    def save():
        try:
            saved = reports_funnel.save_steps(steps)
        except ValueError as exc:
            return _json_response(request, {"error": str(exc)}, status=400)
        except store.DbUnavailable:
            return _db_error_response(request)
        return _json_response(request, {"pasos_config": reports_funnel.payload_config(saved)})

    return await run_in_threadpool(save)
