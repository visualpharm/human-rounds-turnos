"""host_redirect_middleware.py — the canonical-host redirect as an ASGI
middleware using ``core.host_redirect.target()`` as its transport-neutral
decision.

It runs before every router, so the redirect contract never depends on which
family owns a path.

Composition order: mounted as the OUTERMOST user middleware in core/middleware_stack.py
(the LAST add_middleware call — Starlette's add_middleware inserts at position 0),
so the redirect runs before the site-auth gate. A vanity-host request must
redirect to the canonical origin, not answer 401 for a host it will never
serve.

The redirect is method-preserving (308): Location, Cache-Control: no-store,
and an empty body (Content-Length: 0). Path+query are forwarded to target()
from the raw request-target (undecoded raw_path plus the
query string), falling back to the decoded scope path only when an ASGI
server omits raw_path. The fallback is safe here, unlike site_auth's: the
path never gates anything for the FIXED_TARGETS hosts (any path redirects to
the one fixed page) and for OLD_HOSTS it is only echoed into the canonical
Location, where a decoded path still points at the same resource.
"""
from starlette.datastructures import Headers
from starlette.responses import Response

from app.core import host_redirect


class HostRedirectMiddleware:
    """Pure ASGI (2026-10-25), no BaseHTTPMiddleware: a request that needs no
    redirect goes straight to the app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        location = host_redirect.target(Headers(scope=scope).get("Host"),
                                        _request_target(scope))
        if not location:
            await self.app(scope, receive, send)
            return
        response = Response(status_code=308, content=b"",
                            headers={"Location": location,
                                     "Cache-Control": "no-store"})
        await response(scope, receive, send)


def _request_target(scope):
    """The request path+query: raw_path when the server provides it (uvicorn
    always does), decoded path otherwise."""
    raw = scope.get("raw_path")
    if isinstance(raw, bytes):
        path = raw.decode("latin-1")
    else:
        path = str(scope.get("path") or "/")
    query = scope.get("query_string") or b""
    if query:
        path += "?" + query.decode("latin-1")
    return path
