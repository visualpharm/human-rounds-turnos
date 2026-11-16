"""Persist proven successful direct-screen visits as server preferences."""

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import Request

from app.admin_panel import admin_navigation
from app.auth import admin_auth


class AdminLastScreenMiddleware:
    """Best-effort record of an authenticated HTML navigation, after success.

    API calls, failed/forbidden requests, query strings, and arbitrary paths
    cannot modify ``nav.last_screen``.  Authentication is verified before the
    route runs but persistence waits for its response, so an unauthorised or
    stale-module page cannot poison a subsequent login destination.

    Pure ASGI (2026-10-25): the record is written when the response START
    arrives (status and content type are known there), before that start
    reaches the client — the same moment the former ``BaseHTTPMiddleware``
    persisted it, without a second task around the route.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        path = request.url.path
        identity = None
        if request.method == "GET" and path.startswith("/admin/"):
            token = admin_auth.request_credential(request)
            identity = await run_in_threadpool(admin_auth._verify_token, token)
        if not identity:
            await self.app(scope, receive, send)
            return

        async def send_after_record(message):
            if message["type"] == "http.response.start":
                await _record(identity, path, message)
            await send(message)

        await self.app(scope, receive, send_after_record)


async def _record(identity, path, start):
    if start["status"] != 200:
        return
    headers = Headers(raw=list(start.get("headers") or []))
    if "text/html" not in headers.get("content-type", ""):
        return
    role = str(identity.get("rol") or "")
    if path not in admin_navigation.nav_paths(role):
        return
    try:
        await run_in_threadpool(
            admin_navigation.admin_preferences.save_item,
            identity["usuario"], admin_navigation.NAV_LAST_SCREEN, path,
        )
    except Exception:
        # Navigation must remain available during a preference DB outage.
        pass
