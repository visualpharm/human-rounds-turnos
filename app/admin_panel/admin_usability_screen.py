"""Owner-managed anonymous findings, admin-readable including the demo cookie."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.admin_panel import api_models
from app.admin_panel import ai_screens_api_models as screen_models
from app.auth import admin_auth
from app.core import api_body, deps, screen_nav, tz
from app.modules.turnos import study, study_store, study_router

_GATES = [Depends(deps.require_module_enabled("turnos")), Depends(study_router.require_study)]
screen_router = APIRouter(dependencies=_GATES)
api_router = APIRouter(dependencies=_GATES)


async def owner(identity=Depends(deps.require_admin(solo_admin=True))):
    if identity.get("usuario") != admin_auth.owner_usuario() or admin_auth._es_cuenta_demo(identity):
        raise HTTPException(403, detail={"error": "owner_required"})
    return identity


def period(sessions):
    """First and last study day in the installation timezone, ISO dates."""
    days = sorted(s["started_at"].astimezone(tz.tzinfo()).date().isoformat()
                  for s in sessions if s.get("started_at"))
    return {"first": days[0], "last": days[-1]} if days else None


@screen_router.get("/admin/usability")
async def screen(identity=Depends(deps.require_admin_screen(solo_admin=True))):
    return study_router.page("admin/usability.html", active_section="usability",
                             nav_items=screen_nav.nav_items(identity.get("rol")))


@api_router.get("/api/admin/usability", openapi_extra=api_body.docs(get=(None, screen_models.UsabilityReportOut)))
async def report(identity=Depends(deps.require_admin(solo_admin=True))):
    from fastapi.encoders import jsonable_encoder
    data = await study_router.call(study_store.report)
    data["editable"] = (identity.get("usuario") == admin_auth.owner_usuario()
                        and not admin_auth._es_cuenta_demo(identity))
    data["definition"] = study.definition()
    data["period"] = period(data.get("sessions") or [])
    return JSONResponse(jsonable_encoder(data), headers=study_router.HEADERS)


@api_router.post("/api/admin/usability/analyze",
                 openapi_extra=api_body.docs(post=(api_models.UsabilityAnalyzeIn, screen_models.UsabilityAnalysisOut)))
async def analyze(identity=Depends(owner),
                  _payload: dict = Depends(study_router.study_body(api_models.UsabilityAnalyzeIn))):
    count = await study_router.call(study_store.run_analysis)
    return JSONResponse({"candidates": count}, headers=study_router.HEADERS)


@api_router.post("/api/admin/usability/findings",
                 openapi_extra=api_body.docs(post=(api_models.UsabilityFindingIn, screen_models.UsabilityFindingCreatedOut, 201)))
async def add(identity=Depends(owner),
              payload: dict = Depends(study_router.study_body(api_models.UsabilityFindingIn))):
    fid = await study_router.call(study_store.add_finding, payload, identity["usuario"])
    return JSONResponse({"id": fid, "number": f"U-{fid}"}, status_code=201, headers=study_router.HEADERS)


@api_router.post("/api/admin/usability/findings/{finding_id}",
                 openapi_extra=api_body.docs(post=(api_models.UsabilityFindingUpdateIn, screen_models.UsabilityFindingUpdatedOut)))
async def update(finding_id: int, request: Request, identity=Depends(owner)):
    # Read here, not as a dependency: FastAPI validates the typed `finding_id` after the dependencies, so a
    # dependency would answer a malformed body ahead of the 422 of a malformed id.
    payload = await study_router.body(request)
    api_body.watch(request, payload, api_models.UsabilityFindingUpdateIn)
    await study_router.call(study_store.update_finding, finding_id, payload, identity["usuario"])
    return JSONResponse({"id": finding_id}, headers=study_router.HEADERS)
