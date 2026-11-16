"""Native ASGI transport for old-Spanish page compatibility redirects.

Page redirects are explicit router responses. Historical API/feed aliases
retain their distinct rewrite-and-continue behavior in
``core.legacy_urls_middleware``; both surfaces consume the pure mapping
functions in ``core.legacy_urls``.

Every literal old path is registered as its OWN route (`_LITERAL_PATHS`,
the union of `static_fingerprint._LEGACY_ASSET_ALIASES` and
`legacy_urls._PAGES_EXACT`'s keys) plus one `{rest:path}` route per prefix
family (`_PAGES_PREFIX`, and `/servicio/` for `_rewrite_servicio_page`).
A prefix route can be matched with no further match in any table (e.g. a
bare `/servicio/` with no id) — `_resolve()` returns `None` for that, and
the endpoint falls back to `routers.native_terminal`'s OWN 404 HTML body
(`patient_web.error_pages.not_found_body()`) instead of a generic
`HTTPException`, so a matched-but-unrecognized old path still gets the
site's real error page, byte-identical to what the terminal would have
produced if this router hadn't matched first.

Only the PAGE family (human navigation, GET-only, self-contained 301s/404s)
is mounted here. API/feed aliases are transparent pass-throughs owned by the
middleware, so the canonical endpoint remains the sole route implementation.
The page family is verified against ``GET /servicio/refactor-census``."""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.core import legacy_urls
from app.core import static_fingerprint
from app.patient_web import error_pages

router = APIRouter()


def _redirect_301(location, *, no_store):
    headers = {"Location": location, "Content-Length": "0"}
    if no_store:
        headers["Cache-Control"] = "no-store"
    return Response(status_code=301, headers=headers)


def _no_store_miss(status):
    return Response(
        status_code=status,
        headers={"Content-Length": "0", "Cache-Control": "no-store"},
    )


def _terminal_fallback_404():
    """Same body/headers `routers/native_terminal.py`'s own final branch
    would have produced — a prefix route matching here (e.g. a bare
    `/servicio/`) must not regress to a bare FastAPI 404 JSON body."""
    try:
        body = error_pages.not_found_body()
    except Exception:  # noqa: BLE001 — mirrors native_terminal.py's own guard
        body = b"404 - pagina no encontrada"
    return Response(
        content=body,
        status_code=404,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        },
    )


def _resolve(path, query):
    """Same precedence `legacy_urls.handle()`'s GET branch uses, minus the
    API/feed rewrite-and-continue tail (out of scope — see module
    docstring). Returns a `Response`, or `None` when nothing claims this
    exact literal path (only reachable through the prefix routes)."""
    asset_target = static_fingerprint.legacy_asset_redirect(path)
    if asset_target is not None:
        return _redirect_301(asset_target, no_store=True)
    if static_fingerprint.is_legacy_asset_alias(path):
        return _no_store_miss(404)
    new_path = legacy_urls._new_page_path(path)
    if new_path is not None:
        if new_path.startswith("/static/"):
            return _redirect_301(new_path, no_store=True)
        return _redirect_301(new_path + ("?" + query if query else ""), no_store=False)
    return None


def _make_literal_endpoint(path):
    async def _endpoint(request: Request):
        return _resolve(path, request.url.query) or _terminal_fallback_404()
    return _endpoint


def _make_prefix_endpoint(prefix):
    async def _endpoint(rest: str, request: Request):
        return _resolve(prefix + rest, request.url.query) or _terminal_fallback_404()
    return _endpoint


# ---- literal old paths: static_fingerprint's raw-asset aliases + legacy_urls's
# page/JS-rename tables (both dicts are fully populated at import time; some
# filenames appear in both — one route per unique path still runs the FULL
# precedence chain in `_resolve()`, so a duplicate entry changes nothing).
_LITERAL_PATHS = sorted(
    set(static_fingerprint._LEGACY_ASSET_ALIASES) | set(legacy_urls._PAGES_EXACT)
)
for _path in _LITERAL_PATHS:
    router.add_api_route(_path, _make_literal_endpoint(_path), methods=["GET"])
    # Trailing-slash twin: `_new_page_path()` rstrips before its table
    # lookup, so `GET /medicos/` 301s exactly like `/medicos`. Starlette matches
    # literally (and the native terminal's catch-all swallows the request
    # before router-level slash redirection could), so the twin must be
    # registered explicitly — same endpoint, same full `_resolve()` chain
    # with the slashed path, byte-identical outcome.
    if _path != "/" and not _path.endswith("/"):
        router.add_api_route(
            _path + "/", _make_literal_endpoint(_path + "/"), methods=["GET"])

# ---- prefix families: /medico/, /centro/, /estudio/, /solicitud/,
# /turno/oferta/ (legacy_urls._PAGES_PREFIX) plus /servicio/ (its own
# rewriter, _rewrite_servicio_page, reached the same way through
# _new_page_path()).
_PREFIXES = [old for old, _new in legacy_urls._PAGES_PREFIX] + ["/servicio/"]
for _prefix in _PREFIXES:
    router.add_api_route(
        _prefix.rstrip("/") + "/{rest:path}",
        _make_prefix_endpoint(_prefix),
        methods=["GET"],
    )
