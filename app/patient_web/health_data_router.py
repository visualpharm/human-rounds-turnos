"""Native ASGI transport for the platform-core public health-data page.

``core.i18n.lang_switch_redirect`` is a pure function (parsed query string +
path in, ``(location, cookie)`` or ``None`` out), as are
``ops.setup.is_fresh_install`` and ``patient_web.health_data.render_data_page``.
Sync work moves off the event loop through
``starlette.concurrency.run_in_threadpool``."""

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import i18n
from app.ops import setup
from app.patient_web import health_data


router = APIRouter()


async def data_response(request: Request):
    if await run_in_threadpool(setup.is_fresh_install):
        return Response(
            content=b"",
            status_code=303,
            headers={"Location": "/setup", "Cache-Control": "no-store"},
        )
    switch = i18n.lang_switch_redirect(request.url.query, request.url.path)
    if switch is not None:
        location, cookie = switch
        return Response(
            content=b"",
            status_code=302,
            headers={
                "Location": location,
                "Set-Cookie": cookie,
                "Content-Length": "0",
            },
        )
    body = await run_in_threadpool(health_data.render_data_page)
    return Response(
        content=body,
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


# Existing public compatibility URL. The transport migration preserves it
# byte-for-byte; it does not introduce a new Spanish-named route family.
router.add_api_route("/datos", data_response, methods=["GET"])
router.add_api_route("/datos/", data_response, methods=["GET"])
