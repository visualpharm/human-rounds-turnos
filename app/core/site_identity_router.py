"""Native routes for the canonical public identity pages.

The routes delegate HTML rendering to ``site_identity.render_page``.
Language-switch requests are resolved through ``i18n.resolve_lang_switch()``
— a pure domain function — built directly from the request's own URL.

``/llms.txt``/``/robots.txt`` (family W2c of
docs/roadmap/native-migration-campaign-20260826.md) reuse the SAME
``render_page()`` — its ``is_text`` branch already produces the exact bytes
served, no re-render. Language comes from ``i18n.active()``
— the contextvar ``core.i18n_middleware.I18nMiddleware`` (mounted app-wide in
``app.py``) already binds from this same request BEFORE any router runs,
via the identical ``resolve_lang()`` the retired dark transport called — so
this is not a second resolution path, just reading what is already bound.
``handler=None`` is safe here: ``render_page``'s only other use of it,
``i18n.inject_bootstrap``, no-ops whenever the bootstrap placeholder isn't in
the text (never true for a plain-text file), exactly the same call shape
this file's own ``_render()`` already uses for the HTML pages above.
"""

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from app.core import gzip_bridge
from app.core import i18n
from app.core import site_identity

router = APIRouter()

# Public path -> canonical template name under templates/.
_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/about.html": "about.html",
    "/privacy.html": "privacy.html",
    "/terms.html": "terms.html",
}


async def _maybe_lang_switch(request: Request):
    """Return the redirect Response for a `?lang=<code>` switch request, or
    None when this request isn't one — built straight from `request.url`,
    no handler/shim object involved.

    Stays `async def` (even though nothing here needs to await) because
    this helper is imported and awaited by OTHER families' routers too
    (patient_web/health_data_router.py, cuentas/accounts_router.py,
    modules/intake/printable_router.py, modules/intake/intake_admin_router.py)
    — changing its calling convention would break all four call sites."""
    result = i18n.resolve_lang_switch(request.url.query, request.url.path)
    if result is None:
        return None
    location, set_cookie = result
    return Response(
        status_code=302,
        headers={"Location": location, "Set-Cookie": set_cookie,
                 "Content-Length": "0"},
    )


def _render(template_name: str) -> Response:
    """Render through the canonical Jinja source."""
    lang = i18n.active()
    result = site_identity.render_page(template_name, lang=lang)
    if result is None:  # The route inventory names only shipped templates.
        return Response(status_code=404)
    body, _ctype = result
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _make_endpoint(template_name):
    async def _endpoint(request: Request):
        switch = await _maybe_lang_switch(request)
        if switch is not None:
            return switch
        return _render(template_name)
    return _endpoint


for _path, _template in _ROUTES.items():
    router.add_api_route(_path, _make_endpoint(_template), methods=["GET"])


# ---- plain-text public files (llms.txt, robots.txt) ------------------------

_TEXT_FILES = {
    "/llms.txt": "llms.txt",
    "/robots.txt": "robots.txt",
}


def _make_text_endpoint(filename):
    async def _endpoint(request: Request):
        lang = i18n.active()
        result = await run_in_threadpool(site_identity.render_page, filename, lang=lang)
        if result is None:
            return Response(status_code=404, headers={"Content-Length": "0"})
        body, ctype = result
        body, encoding = gzip_bridge.body_for(request, body)
        headers = {"Content-Type": ctype, "Cache-Control": "no-store",
                   "Content-Length": str(len(body))}
        if encoding:
            headers["Content-Encoding"] = encoding
            headers["Vary"] = "Accept-Encoding"
        return Response(content=body, media_type=None, headers=headers)
    return _endpoint


for _path, _filename in _TEXT_FILES.items():
    router.add_api_route(_path, _make_text_endpoint(_filename), methods=["GET"])
