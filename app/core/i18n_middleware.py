"""i18n_middleware.py — Tier 4 slice 2: el `ContextVar` bind/reset de
`i18n.py` como middleware ASGI, EXACTAMENTE el mismo mecanismo que
`routing.dispatch()` ya usa para cada request legacy (`i18n.bind(handler)`
al entrar, `i18n.unbind(token)` en el `finally`) — ver
docs/roadmap/tier4-replatform-design.md, "i18n → Jinja2 + jinja2-fragments
(slice 2)", "Request-scoped language survives unchanged as a mechanism,
moves to middleware".

Un `starlette.Request` ya duck-tipa lo que `i18n.resolve_lang(handler)`
necesita (`.headers.get("Cookie")`/`.headers.get("Accept-Language")`, ambos
case-insensitive igual que el `Headers`-como-`email.message.Message` que
usa el handler legacy) — `i18n.bind(request)` recibe el `Request` de Starlette directo.

Montada UNA vez, global, en `app.py` — no sólo para las páginas de este
slice: cualquier router FastAPI futuro (slice 3 en adelante) hereda
`i18n.active()` correcto sin volver a pensar el bind. El catch-all de
reenvío a legacy no se entera de esto (no importa `i18n` en absoluto) ni
necesita enterarse: el `ContextVar` vive en el `contextvars` de ESTE
proceso/tarea async, el legacy server real corre en su propio hilo con su
propio bind vía `routing.dispatch()` — cero interferencia entre los dos.

ASGI puro (2026-10-25), no `BaseHTTPMiddleware`: el app corre en la MISMA
tarea y el MISMO contexto que el bind, y el idioma sigue ligado hasta el
último byte de la respuesta (también un `StreamingResponse`), no sólo hasta
que el handler devuelve el objeto respuesta.
"""

from starlette.requests import Request

from app.core import i18n


class I18nMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        _lang, token = i18n.bind(Request(scope, receive))
        try:
            await self.app(scope, receive, send)
        finally:
            i18n.unbind(token)
