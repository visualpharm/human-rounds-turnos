"""ai_costs_router.py — native-route migration (F8, 2026-08-26): the FastAPI
`router` twin of `ai_costs.py` — the admin "Costos de IA" report. It calls
`ai_costs._report_data()`, a pure function.

`ai_costs` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET /api/admin/costs[?days=7|30|90]

Staff auth via `core.deps.require_admin(solo_admin=True, permitir_reportes=
True)` — the exact same role flags `admin_auth.require_admin` always got
from `ai_costs._report()`, same "reportes" role gate every Reportes-family
router in this repo already established (`reports_router.py`). A request
with no session cookie 401s."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.ai import ai_costs, api_models
from app.core import api_body, deps
from app.core.http_json import json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


@router.get("/api/admin/costs", openapi_extra=api_body.docs(
    get=(None, api_models.AiCostsOut)) | api_body.query_docs(get={"days": api_body.Query(
    "The period in days: 7, 30 or 90; anything else is 30", {"type": "integer", "enum": [7, 30, 90]})}))
async def admin_costs(
    request: Request,
    identity: dict = Depends(deps.require_admin(solo_admin=True, permitir_reportes=True)),
    # Text, parsed leniently below; `query_docs` above documents the numbers it takes.
    days: str | None = Query(None, include_in_schema=False),
):
    dias = ai_costs._normalize_days(days)
    from app.core import db
    try:
        data = await run_in_threadpool(ai_costs._report_data, dias)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return _json_response(data)
