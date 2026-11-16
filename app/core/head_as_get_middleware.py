"""head_as_get_middleware.py — HEAD is answered by the route that answers GET.

`fastapi.routing.APIRoute` does not add HEAD to a GET route (unlike
`starlette.routing.Route`), so a `HEAD /` skipped the page's own route and
fell through to the native terminal, which read the path as a static file:
403 for `/`, 404 for `/admin/login`, while the same GET answered 200. Uptime
monitors, link checkers and crawlers that probe with HEAD saw a working site
as down (found live 2026-10-11; `routers/health.py` had closed the same gap
for `/health` and `/ready` by declaring HEAD one route at a time).

This closes it for the whole route table at once, without touching any route:
when the route that owns the path for GET declines HEAD, the request is run
as that GET and its body is dropped — status and headers (Content-Length
included) are the GET's, which is what HEAD means. A route that declares HEAD
itself (`/health`, the terminal's static files) is left exactly as it was,
and a path with no GET owner still reaches the terminal as HEAD.

Mounted as the INNERMOST user middleware in core/middleware_stack.py (the FIRST add_middleware
call): every outer boundary — host redirect, site-auth, the panel's
last-screen memory — still sees the real method, so a HEAD never records a
visited screen; only the router sees GET.
"""
from starlette.routing import Match


class HeadAsGetMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") != "HEAD"
                or not get_owner_declines_head(scope)):
            await self.app(scope, receive, send)
            return
        body_sent = False

        async def send_without_body(message):
            nonlocal body_sent
            if message["type"] != "http.response.body":
                await send(message)
            elif not body_sent:
                body_sent = True
                await send({"type": "http.response.body", "body": b"",
                            "more_body": False})

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


def get_owner_declines_head(scope):
    """True when the first route that fully matches this path for GET does
    not accept HEAD — the one case where the HEAD would be answered by a
    different, later route than the GET."""
    app = scope.get("app")
    routes = getattr(getattr(app, "router", None), "routes", None) or ()
    as_get = {**scope, "method": "GET"}
    for route in routes:
        if route.matches(as_get)[0] is Match.FULL:
            return route.matches(scope)[0] is not Match.FULL
    return False
