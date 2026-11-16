"""Pre-launch whole-site Basic Auth middleware for native ASGI mode.

The middleware is the outer request boundary in ``core/middleware_stack.py``. It calls the
same transport-neutral ``site_auth.authorize`` decision used by the dark
stdlib rollback, authorizes on the undecoded ASGI ``raw_path``, and emits the
same fixed challenge bytes with a no-store policy. Missing or malformed
``raw_path`` fails closed. The five third-party and health exemptions remain
owned by ``site_auth._EXENTAS`` and are all served by native routes.
"""
from starlette.datastructures import Headers
from starlette.responses import Response

from app.auth import site_auth


class SiteAuthMiddleware:
    """Pure ASGI (2026-10-25), no BaseHTTPMiddleware: an allowed request goes
    straight to the app, so the body streams and the app runs in the caller's
    own task and context."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # _raw_request_path returns None when the scope omits raw_path OR
        # carries a non-bytes value (raw_path is spec'd as bytes). Passing None
        # to authorize() FAILS CLOSED: no exemption can match (an empty path is
        # not exempt), so the request MUST present valid Basic credentials —
        # and a valid credential still passes. We NEVER stringify a non-bytes
        # raw_path nor fall back to the decoded scope['path'], either of which
        # would let an encoded delimiter reshape a path into an exemption.
        if site_auth.authorize(_raw_request_path(scope),
                               Headers(scope=scope).get("Authorization", "")):
            await self.app(scope, receive, send)
            return
        await _unauthorized_response()(scope, receive, send)


def _raw_request_path(scope):
    """The UNDECODED request path as the server received it — the same bytes
    the legacy `BaseHTTPRequestHandler` sees as `handler.path` — or None when
    the scope omits `raw_path` or carries a non-bytes value.

    raw_path is spec'd as bytes, so it is returned (latin-1 decoded) ONLY when
    it actually IS bytes. A missing raw_path OR any non-bytes value (a
    malformed string, an int, …) returns None instead of being stringified:
    `str("/mcp/")` would hand authorize() a path that cleans to the `/mcp`
    exemption and reopen the bypass this raw-path contract exists to close.

    Authorize on THIS, not the percent-decoded `scope['path']` /
    `request.url.path`: a decoded path lets an encoded delimiter (%2F -> /,
    %3F -> ?) reshape the path after decoding and spuriously match an exemption
    (`/mcp%2F` -> `/mcp/` -> `/mcp`), a bypass legacy never has — legacy
    authorizes the raw `handler.path`, where `%2F` stays literal and never
    matches. `_clean_path` then strips a query and trailing slash on those RAW
    bytes, matching legacy's `handler.path` split exactly.

    Fail CLOSED, not open: None (missing or non-bytes raw_path) never falls
    back to the decoded `scope['path']`. authorize(None, ...) cannot match any
    exemption (an empty path is not exempt), so an unauthenticated request is
    denied while a valid Basic credential still passes. A decoded fallback
    would hand authorize() exactly the path an attacker would need to forge an
    exemption, so it is refused outright. uvicorn (the only server this app
    runs under) always provides raw_path as bytes, so None is unreachable in
    production — this is the belt-and-braces guarantee that no ASGI server can
    reopen the hole."""
    raw = scope.get("raw_path")
    # raw_path is spec'd as bytes; honor it ONLY when it actually is. A missing
    # value OR any non-bytes value (a malformed string, an int, …) collapses to
    # None — NEVER stringified — so authorize() can match no exemption and the
    # gate fails closed on the credential instead.
    if isinstance(raw, bytes):
        return raw.decode("latin-1")
    return None


def _unauthorized_response():
    """The 401 the middleware emits on rejection. Bytes come from site_auth
    (the one source of the 401 bytes); raw_headers is set directly so the
    header names/order match the legacy gate's 401 exactly."""
    body = site_auth.challenge_body()
    response = Response(content=body, status_code=401)
    response.raw_headers = [
        (name.encode("latin-1"), value.encode("latin-1"))
        for name, value in site_auth.challenge_headers()
    ] + [
        (b"Content-Length", str(len(body)).encode("latin-1")),
        # Cache-Control: no-store is set HERE, not in
        # site_auth.challenge_headers() (that list is only WWW-Authenticate
        # and Content-Type). A 401 that gets cached poisons the asset (prod
        # 2026-07-28: a versioned .js 401'd once and Chrome served that 401
        # for a year, creds and all).
        (b"Cache-Control", b"no-store"),
    ]
    return response
