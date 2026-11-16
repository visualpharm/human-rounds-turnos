"""Native compatibility rewrite for historical API and calendar-feed URLs.

Page aliases return explicit redirects from ``legacy_urls_router``. API and
feed aliases are different: historically they changed only the request path
and then continued through the canonical endpoint with the original method,
query string, headers, and body. This small ASGI middleware preserves exactly
that pass-through contract without recreating an HTTP handler or dispatcher.
"""

from app.core import legacy_urls


class LegacyUrlRewriteMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("raw_path")
        if isinstance(raw_path, bytes):
            try:
                old_path = raw_path.decode("ascii")
            except UnicodeDecodeError:
                old_path = None
        else:
            old_path = scope.get("path")

        new_path = legacy_urls._new_api_path(old_path) if old_path else None
        if new_path is not None:
            scope = dict(scope)
            scope["path"] = new_path
            scope["raw_path"] = new_path.encode("ascii")

        await self.app(scope, receive, send)
