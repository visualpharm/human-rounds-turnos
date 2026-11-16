"""prompts_router.py — native-route migration (F8, 2026-08-26): the FastAPI
`router` twin of `prompts.py` — the editable-AI-prompt admin (the seven
prompts a staff user can read/edit/revert/delete). Real native
endpoints calling the domain functions (`prompts.list_prompts`,
`prompts.detail`, `prompts._history_json`, `prompts.save_prompt`,
`prompts.delete_prompt`).

`prompts` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET    /api/admin/prompts
  GET    /api/admin/prompts/{clave}
  GET    /api/admin/prompts/{clave}/traduccion
  GET    /api/admin/prompts/{clave}/historial
  POST   /api/admin/prompts/{clave}/guardar
  DELETE /api/admin/prompts/{clave}

`{clave}` is re-validated against `prompts._BY_KEY` here — an unknown key 404s with the SAME custom body (`{"error": "no
existe ese prompt"}`), not the generic terminal-404 contract (the legacy
inner regex never declines any of these five literal path shapes; every
segment beyond `{clave}` is its OWN distinct FastAPI path template, not a
wildcard, so there is no "matched-then-declined" case here to cover).

Staff auth via `core.deps.require_admin(solo_admin=True)` — the exact same
role flag `admin_auth.require_admin` always got from `prompts.handle()`. A
request with no session cookie 401s."""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.ai import api_models, prompts
from app.core import db
from app.core import api_body, deps
from app.core.http_json import json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])

_ADMIN = Depends(deps.require_admin(solo_admin=True))


def _unknown_prompt():
    return _json_response({"error": "no existe ese prompt"}, status=404)


@router.get("/api/admin/prompts", openapi_extra=api_body.docs(get=(None, api_models.PromptsOut)))
async def prompts_list(identity: dict = _ADMIN):
    try:
        items = await run_in_threadpool(prompts.list_prompts)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return _json_response({"items": items})


async def prompts_clave(clave: str, request: Request, identity: dict = _ADMIN):
    if clave not in prompts._BY_KEY:
        return _unknown_prompt()
    try:
        if request.method == "DELETE":
            result = await run_in_threadpool(prompts.delete_prompt, clave, identity)
        else:
            result = await run_in_threadpool(prompts.detail, clave)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return _json_response(result)


# ONE route object for both methods, same reasoning as chart_summary_router:
# the adapter-backed baseline mounted GET+DELETE on this path as a single
# Starlette route (`methods=["GET", "DELETE"]` in one `add_api_route`), so
# this stays one `add_api_route` too instead of two decorators — otherwise
# `route_objects` would drift from the baseline.
router.add_api_route("/api/admin/prompts/{clave}", prompts_clave, methods=["GET", "DELETE"],
                     openapi_extra=api_body.docs(get=(None, api_models.PromptItem),
                                                 delete=(None, api_models.PromptRestoredOut)))


@router.get("/api/admin/prompts/{clave}/traduccion",
            openapi_extra=api_body.docs(get=(None, api_models.PromptItem)))
async def prompts_traduccion(clave: str, identity: dict = _ADMIN):
    if clave not in prompts._BY_KEY:
        return _unknown_prompt()
    try:
        item = await run_in_threadpool(prompts.detail, clave, True)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return _json_response(item)


@router.get("/api/admin/prompts/{clave}/historial",
            openapi_extra=api_body.docs(get=(None, api_models.PromptHistoryOut)))
async def prompts_historial(clave: str, identity: dict = _ADMIN):
    if clave not in prompts._BY_KEY:
        return _unknown_prompt()
    try:
        items = await run_in_threadpool(prompts._history_json, clave)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return _json_response({"items": items})


class PromptSaveIn(api_body.BodyModel):
    """The prompt's Spanish base text and, optionally, its English version."""
    texto_es: str | None = None
    texto_en: str | None = None


_BODY = api_body.BodyContract(
    message=lambda reason: f"JSON inválido: {reason}",
    respond=lambda _request, payload, status: _json_response(payload, status=status),
    decoder=lambda raw: api_body.decode(raw) or {})
"""The same catch-all `_save()` uses: «JSON inválido: <reason>»."""


async def _known_prompt(clave: str):
    """A prompt the catalog does not name answers its own 404, after the gate and before the body."""
    if clave not in prompts._BY_KEY:
        raise deps.EarlyResponse(_unknown_prompt())
    return clave


@router.post("/api/admin/prompts/{clave}/guardar",
             openapi_extra=api_body.docs(post=(PromptSaveIn, api_models.PromptSavedOut)))
async def prompts_guardar(identity: dict = _ADMIN, clave: str = Depends(_known_prompt),
                          body: api_body.Body = Depends(api_body.body(_BODY, PromptSaveIn))):

    def _save():
        return prompts.save_prompt(clave, identity, texto_es=body.value.get("texto_es"),
                                   texto_en=body.value.get("texto_en"))

    try:
        result = await api_body.run(body, _save, expected=(prompts.PromptSaveError,))
    except prompts.PromptSaveError as exc:
        return _json_response(exc.body, status=exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return _json_response(result)
