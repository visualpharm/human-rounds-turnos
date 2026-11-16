"""routers/health.py — liveness/readiness endpoints (Tier 4 slices 0/7,
extracted from app.py in the composition extraction slice)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core import api_body, api_models
from app.core import health as health_check

router = APIRouter()


@router.api_route("/health", methods=["GET", "HEAD"],
                  openapi_extra=api_body.docs(get=(None, api_models.HealthOut)))
async def _health_endpoint():
    """Minimal liveness (slice 0) — only "the process is up", no
    dependencies.

    Tier 4 slice 8 (orchestrator finding): `@router.get(...)` only
    registers GET — unlike `starlette.routing.Route` (which auto-adds HEAD
    to any GET route), `fastapi.routing.APIRoute` does NOT, so a
    `HEAD /health` (infra/orchestrator probes that skip the body) fell
    through to the former proxy catch-all (which declared HEAD) and on to
    legacy, which had no
    such route → 404 where the same GET already returned 200.
    `methods=["GET", "HEAD"]` on `api_route` closes that gap explicitly;
    FastAPI already drops the response body on HEAD by itself (same
    `JSONResponse`, identical headers, zero bytes)."""
    return JSONResponse(health_check.liveness())


@router.api_route("/ready", methods=["GET", "HEAD"],
                  openapi_extra=api_body.docs(get=(None, api_models.ReadyOut)))
async def _ready_endpoint():
    """Readiness (slice 7) — reuses health.readiness(), the SAME function
    dark mode calls from its own dispatch (health.py's handle()) — one
    check, not one per serving mode. `methods=["GET", "HEAD"]`: same
    finding and same reason as `_health_endpoint` above."""
    ok, body = health_check.readiness()
    return JSONResponse(body, status_code=200 if ok else 503)
