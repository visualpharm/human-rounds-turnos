"""panel_login_router.py — native FastAPI router for the admin login
screen's editable content + login telemetry beacon (docs/roadmap/native-
migration-campaign-20260826.md, family F9).

Real ``async def`` endpoints. Both endpoints call the pure
domain functions extracted onto ``auth.panel_login`` (``contenido_payload``,
``validar_evento``/``registrar_evento``) so the business logic — including
the rate limit, the humor/consejos payload shape and the exact 400 error
messages — is the code in ``auth.panel_login``. Only the DB write runs off the
event loop
(``run_in_threadpool``); everything else here is in-memory.

Platform-core, mounted directly in ``app.py`` same as before.

  GET  /api/login-panel         public, the login screen's content
  POST /api/login-panel/event   public, telemetry beacon"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.auth import api_models, panel_login
from app.auth import turnstile
from app.core import api_body, deps, i18n
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter()


@router.get("/api/login-panel", openapi_extra=api_body.docs(get=(None, api_models.LoginPanelOut)))
async def api_login_panel_contenido(request: Request):
    # El idioma del visitante decide qué memes sirve el panel: castellano
    # → traducidos, cualquier otro → los originales en inglés (responsable del producto,
    # 2026-09-18: el meme en español en la interfaz inglesa es inaceptable).
    return _json_response(request, panel_login.contenido_payload(i18n.resolve_lang(request)))


def _no_content():
    return Response(status_code=204, headers={"Content-Length": "0"})


async def _within_limit(request: Request):
    """The per-IP limit, ahead of the body: an IP over it gets the beacon's silent 204."""
    client = request.client
    ip = turnstile.client_ip_from(request.headers, client.host if client else "")
    if not panel_login.permitido(ip):
        raise deps.EarlyResponse(_no_content())


@router.post("/api/login-panel/event", openapi_extra=api_body.docs(
    post=(api_models.PanelEventIn, None, 204)))
async def api_login_panel_evento(
        request: Request, _limit: None = Depends(_within_limit),
        event: api_body.Body = Depends(api_body.body(api_models.PANEL_EVENT, api_models.PanelEventIn))):
    body = event.value
    try:
        sid, variante, evento, tab, item, ms = panel_login.validar_evento(body)
    except panel_login.EventoInvalido as exc:
        return _json_response(request, {"error": str(exc)}, status=400)

    await run_in_threadpool(
        panel_login.registrar_evento, sid, variante, evento, tab, item, ms)
    return Response(status_code=204, headers={"Content-Length": "0"})
