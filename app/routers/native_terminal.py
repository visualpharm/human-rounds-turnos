"""Native ASGI owner for the final dynamic/static compatibility boundary."""

import email.utils

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from app.core import static_fingerprint
from app.core.terminal_response import json_not_found_response
from app.patient_web import availability_pages
from app.patient_web import error_pages
from app.patient_web import info_pages
from app.services import static_assets


router = APIRouter()


# Retired documents that must never come back as a static miss-turned-hit:
# a stale admin.html baked into an old image (or left behind by an unpacked
# overlay next to the static root) would otherwise be served again by the
# generic file fallback below. The dormant WebTurno/directory shells' JS
# helpers are the same class: a dropped-back ui-core.js / specialty-browser.js
# (or their pre-rename names) must not become a public static hit.
_RETIRED_STATIC_PATHS = frozenset({
    "/admin-content.js",
    "/admin-content.js/",
    "/static/admin-content.js",
    "/static/admin-content.js/",
    "/admin-queue.js",
    "/admin-cola.js",
    "/admin-cola.js/",
    "/admin.html",
    "/ui-core.js",
    "/specialty-browser.js",
    "/stz-ui.js",
    "/rubricador.js",
})


def _error_body(status):
    return static_assets.http_error_body(status)


def _html_response(body, status, *, cache_control):
    return Response(
        content=body,
        status_code=status,
        media_type=None,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": cache_control,
        },
    )


def _json_not_found():
    return json_not_found_response()


def _method_not_implemented(method):
    body = static_assets.http_error_body(501, f"Unsupported method ({method!r})")
    return Response(
        content=body,
        status_code=501,
        media_type=None,
        headers={
            "Content-Type": "text/html;charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        },
    )


def _static_response(result, *, head, request, conditional=True):
    if conditional and static_assets.request_is_not_modified(
        result,
        request.headers.get("if-modified-since"),
        request.headers.get("if-none-match"),
    ):
        result.close()
        return Response(
            content=b"",
            status_code=304,
            media_type=None,
            headers={"Cache-Control": result.cache_control},
        )
    headers = {
        "Content-Type": result.content_type,
        "Content-Length": str(result.size),
        "Cache-Control": result.cache_control,
    }
    if conditional:
        headers["Last-Modified"] = email.utils.formatdate(
            result.mtime,
            usegmt=True,
        )
    if head:
        result.close()
        return Response(content=b"", status_code=200, media_type=None, headers=headers)
    response = StreamingResponse(
        result.chunks(),
        status_code=200,
        media_type=None,
        headers=headers,
    )
    return response


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def terminal(full_path: str, request: Request):
    """Run only after every exact platform and module route has declined."""
    path = request.url.path
    method = request.method
    if path.startswith("/api/"):
        return _json_not_found()
    if path in _RETIRED_STATIC_PATHS:
        # Explicit no-store deny: the legacy admin shell's source is deleted,
        # and this guard keeps a stale on-disk artifact from resurrecting it.
        return _json_not_found()
    if static_fingerprint.is_legacy_asset_alias(path):
        if method == "GET":
            redirect_target = static_fingerprint.legacy_asset_redirect(path)
            if redirect_target is not None:
                return Response(
                    content=b"",
                    status_code=301,
                    media_type=None,
                    headers={
                        "Location": redirect_target,
                        "Content-Length": "0",
                        "Cache-Control": "no-store",
                    },
                )
        # Valid aliases are GET-only redirects; unavailable aliases and every
        # other method stay an ordinary no-store miss rather than exposing a
        # raw module-owned file through the generic static fallback.
        return Response(
            content=b"",
            status_code=404,
            media_type=None,
            headers={"Content-Length": "0", "Cache-Control": "no-store"},
        )
    if method == "GET":
        # Information pages from the knowledge packs (/<slug>, type "pagina")
        # win over the one-segment specialty pages of availability_pages:
        # an editorial slug is explicit, a specialty slug is derived.
        dynamic = info_pages.match("GET", path) or availability_pages.match("GET", path)
        if dynamic is not None:
            return _html_response(dynamic.body, dynamic.status, cache_control="no-cache")
    if method not in {"GET", "HEAD"}:
        if method in {"POST", "PUT", "DELETE"}:
            return _json_not_found()
        return _method_not_implemented(method)
    query = request.scope.get("query_string", b"").decode("latin-1")
    fingerprint = static_fingerprint.transport_target(path)
    fingerprint_name = static_fingerprint._resolve(path) if fingerprint is not None else None
    static_path = path
    if fingerprint is not None:
        static_path, content_type, cache_control = fingerprint
        query = ""
    result = static_assets.resolve_static_request(static_path, query)
    if isinstance(result, static_assets.StaticFile):
        if fingerprint is not None:
            if fingerprint_name and fingerprint_name.startswith("modules/") and not static_fingerprint.open_module_asset_matches_url(
                fingerprint_name, path, result.fd,
            ):
                result.close()
                return _html_response(_error_body(404), 404, cache_control="no-store")
            if content_type is not None:
                result.content_type = content_type
            result.cache_control = cache_control
        return _static_response(
            result,
            head=(method == "HEAD"),
            request=request,
            conditional=(fingerprint is None),
        )
    if isinstance(result, static_assets.StaticDenied):
        body = _error_body(403)
        return Response(
            content=body,
            status_code=403,
            media_type=None,
            headers={
                "Content-Type": "text/html;charset=utf-8",
                "Content-Length": str(len(body)),
                "Cache-Control": "no-store",
            },
        )
    try:
        body = error_pages.not_found_body()
    except Exception:  # noqa: BLE001
        body = b"404 - pagina no encontrada"
    return _html_response(body, 404, cache_control="no-store")
