"""ai_health_router.py — native-route migration (F8, 2026-08-26): the FastAPI
`router` twin of `ai_health.py` — the public `{"disponible": bool}` capability
probe the chat widget and the DNI-OCR form both poll before showing an
AI-backed card. It calls `ai_health.
available()` directly.

`ai_health` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET /api/ai-status   no auth, always 200, body {"disponible": bool}"""

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.ai import ai_health, api_models
from app.core import api_body, deps
from app.core.http_json import json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


@router.get("/api/ai-status", openapi_extra=api_body.docs(get=(None, api_models.AiStatusOut)))
async def ai_status():
    disponible = await run_in_threadpool(ai_health.available)
    return _json_response({"disponible": disponible})
