"""patient_site_router.py — Native ASGI router for `patient_web/site_*.py`'s public
patient-facing surface (family F2 of
docs/roadmap/native-migration-campaign-20260826.md).

`patient_web/site_*.py`'s page/data functions (`_page_doctors`, `_page_service`,
`_services_enriched`, ...) are pure — they take parsed arguments and return
bytes/dicts, never touching `send_response`/`wfile` themselves. This file
calls those functions directly and builds the `starlette.Response` itself;
blocking calls go through `starlette.concurrency.run_in_threadpool`.

Request-derived values cross into ``patient_web/site_*.py`` explicitly: query
text, the resolved session user, and the effective authentication mode.
Request-aware account helpers receive the real Starlette ``Request``. No
duck-typed ``{path, headers}`` compatibility object exists on these routes.

Every endpoint repeats the same two-step prelude: a valid `?lang=` switch
answers first (`core.i18n.lang_switch_redirect`, a pure function — see that
module's own docstring), even when `turnos`
is disabled; only then does the module-enabled gate apply
(`core.deps.require_module_enabled("turnos")`, already shim-free — see
`availability_directory_router.py`, the first native route to depend on it).
The lang switch is checked before the gate.

`patient_web/site_*.py` IS a `turnos`-owned route module (`modules/turnos/
module.json`'s `routes`), so this router still needs the module-contract
adapter's enable/disable parity described above — unlike platform-core files
such as `branding_router.py`/`core/site_identity_router.py`. The router is
mounted automatically by `app_modules.mount_module_routers()` through this
file's `router_attr` manifest entry — no central `app.py` edit."""

import urllib.parse

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.core import api_body
from app.core import deps
from app.core import gzip_bridge
from app.core import i18n
from app.cuentas import accounts
from app.patient_web import (
    api_models,
    site_availability,
    site_catalog,
    site_centers,
    site_listing,
    site_news,
    site_page_centers,
    site_page_doctors,
    site_page_service,
    site_preparation,
    site_shell,
    site_studies,
)
from app.core.http_json import gzip_json_response as _json_response
from app.core.terminal_response import redirect_response as _redirect_response

router = APIRouter()
_require_turnos = deps.require_module_enabled("turnos")


def _html_response(request: Request, body: bytes) -> Response:
    out, encoding = gzip_bridge.body_for(request, body)
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(out)),
    }
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=out, status_code=200, headers=headers)


def _not_found_response(request: Request) -> Response:
    """The branded 404 page (error_pages, noindex) with a real 404 status —
    for an id that does not exist, instead of a redirect to a section page,
    which Google reads as a soft 404."""
    from app.patient_web import error_pages
    out, encoding = gzip_bridge.body_for(request, error_pages.not_found_body())
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(out)),
        "Cache-Control": "no-store",
    }
    if encoding:
        headers["Content-Encoding"] = encoding
        headers["Vary"] = "Accept-Encoding"
    return Response(content=out, status_code=404, headers=headers)


async def _prelude(request: Request):
    """Shared prelude every route below runs first: a valid ``?lang=``
    switch answers immediately (even with `turnos` disabled), otherwise the
    module-enabled gate applies. Returns a `Response` to send now, or
    `None` to continue into the route's own logic."""
    switch = i18n.lang_switch_redirect(request.url.query, request.url.path)
    if switch is not None:
        location, cookie = switch
        return Response(
            content=b"", status_code=302,
            headers={
                "Location": location,
                "Set-Cookie": cookie,
                "Content-Length": "0",
            },
        )
    await _require_turnos()
    return None


# ---------------------------------------------------------------- de-linking
# The private doctor directory, private clinics, health-insurance funds, the
# legacy analytics panel and the WebTurno SPA aren't part of Pinamar Turnos
# (Hospital de Pinamar only) — always redirect home. The former on-disk
# implementations (app.html, panel.html, directorio.html) are deleted; these
# routes stay as 302s so old bookmarks keep landing on `/`.

async def _redirect_home(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    return _redirect_response("/")


# ------------------------------------------------------------- catalog pages

async def _doctors_page(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    body = await run_in_threadpool(site_page_doctors._page_doctors)
    return _html_response(request, body)


async def _doctor_page(request: Request, mid: str):
    early = await _prelude(request)
    if early is not None:
        return early
    mid = mid.strip("/").lower()
    body = await run_in_threadpool(site_page_doctors._page_doctor, mid)
    if body is None:
        return _redirect_response("/doctors")
    return _html_response(request, body)


async def _cluster_page(request: Request, cid: str):
    early = await _prelude(request)
    if early is not None:
        return early
    cid = cid.strip("/").lower()
    body = await run_in_threadpool(site_page_doctors._page_cluster, cid)
    if body is None:
        return _redirect_response("/")
    return _html_response(request, body)


def _study_page_sync(rest: str):
    parts = rest.split("/", 1)
    if len(parts) != 2:
        return None
    return site_studies._page_study(parts[0].lower(), parts[1])


async def _study_page(request: Request, rest: str):
    early = await _prelude(request)
    if early is not None:
        return early
    rest = rest.strip("/")
    body = await run_in_threadpool(_study_page_sync, rest)
    if body is None:
        return _redirect_response("/d/diagnostico")
    return _html_response(request, body)


async def _centers_page(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    body = await run_in_threadpool(site_page_centers._page_centers)
    return _html_response(request, body)


async def _center_page(request: Request, slug: str):
    early = await _prelude(request)
    if early is not None:
        return early
    slug = slug.strip("/").lower()
    # A center whose one page is an informational page (Zoonosis → /zoonosis,
    # site_centers._center_pages) moves there for good.
    pagina = await run_in_threadpool(lambda: site_centers._center_pages().get(slug))
    if pagina:
        return Response(content=b"", status_code=301,
                        headers={"Location": pagina, "Content-Length": "0"})
    body = await run_in_threadpool(site_page_centers._page_center, slug)
    if body is None:
        return _redirect_response("/centers")
    return _html_response(request, body)


async def _preparation_page(request: Request, pid: str):
    early = await _prelude(request)
    if early is not None:
        return early
    pid = pid.strip("/").lower()
    body = await run_in_threadpool(site_preparation._page_preparation, pid)
    if body is None:
        return _not_found_response(request)
    return _html_response(request, body)


async def _news_page(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    body = await run_in_threadpool(site_news._page_news)
    return _html_response(request, body)


async def _news_pedidos_page(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    body = await run_in_threadpool(site_news._page_news, True)
    return _html_response(request, body)


# 2026-08-27 hospital meeting — informational pages reached from search
# results (telemedicina, zoonosis, donar sangre). Same shape as the other
# catalog pages: the pure `patient_web/site_*.py._page_*` builders, threadpooled.

async def _telemedicine_page(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    body = await run_in_threadpool(site_news._page_telemedicine)
    return _html_response(request, body)


async def _zoonosis_page(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    body = await run_in_threadpool(site_news._page_zoonosis)
    return _html_response(request, body)


async def _blood_donation_page(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    body = await run_in_threadpool(site_news._page_blood_donation)
    return _html_response(request, body)


# --------------------------------------------------------------- data APIs

async def _api_services(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    data = await run_in_threadpool(site_listing._services_enriched)
    return _json_response(request, {"servicios": data})


async def _api_doctors(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    data = await run_in_threadpool(site_listing._doctors_enriched)
    return _json_response(request, {"medicos": data})


async def _api_centers(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    data = await run_in_threadpool(site_centers._centers_api)
    return _json_response(request, data)


async def _api_directory(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    data = await run_in_threadpool(site_listing._directory_data)
    return _json_response(request, data)


async def _api_links(request: Request):
    early = await _prelude(request)
    if early is not None:
        return early
    data = await run_in_threadpool(site_centers._links)
    return _json_response(request, {"enlaces": data})


def _service_slots_sync(sid, hold_token, doctor, preferred):
    slots = _service_slots_only(sid, hold_token, doctor, preferred)
    if slots.get("medicos"):
        return slots
    # Empty grid: the wizard says when turnos appear again and whom to call
    # (the reference-deployment counterpart, 2026-10-17) — computed here, from the same schedules.
    sv = site_catalog._service_by_id(sid)
    info = site_availability._no_slots_info(sv, doctor or preferred) if sv else None
    return dict(slots, sin_turnos=info) if info else slots


def _service_slots_only(sid, hold_token, doctor, preferred):
    sv = site_catalog._service_by_id(sid)
    if sv and doctor:
        return site_availability._appointments_for_doctor(
            sv, doctor, exclude_token=hold_token)
    if sv and preferred:
        return site_availability._appointments_for_service_preferred(
            sv, preferred, exclude_token=hold_token)
    if sv:
        return site_availability._appointments_for_service(
            sv, exclude_token=hold_token)
    return {}


async def _api_service_slots(request: Request, sid: str):
    early = await _prelude(request)
    if early is not None:
        return early
    sid = sid.strip("/")
    # ?hold=<token>: the current request's own hold — lets the appointment
    # THIS client already has taken still show (soft-lock, 2026-07-16).
    qs = urllib.parse.parse_qs(request.url.query)
    hold_token = (qs.get("hold") or [None])[0]
    doctor = (qs.get("medico") or [None])[0]
    preferred = (qs.get("prefer_medico") or [None])[0]
    own = await run_in_threadpool(
        site_availability.own_hold_tokens, hold_token, request)
    slots = await run_in_threadpool(
        _service_slots_sync, sid, own or None, doctor, preferred)
    return _json_response(request, slots)


# ---------------------------------------------------------------- booking

def _service_appointment_sync(sid, query_string, current_user, auth_mode):
    return site_page_service._page_service_appointment(
        sid,
        query_string=query_string,
        current_user=current_user,
        auth_mode=auth_mode,
    )


async def _service_page(request: Request, rest: str):
    early = await _prelude(request)
    if early is not None:
        return early
    rest = rest.strip("/")
    # The signed-in patient: the wizard needs it, and the info page too, so
    # the PAMI note only speaks to a PAMI member (hr-patient review #5).
    current_user = await run_in_threadpool(accounts.current_user, request)
    if rest.endswith("/book"):
        # /service/<id>/book = the wizard (upload the referral / choose a
        # time). Walk-in or changed mode → to the info page.
        sid = rest[: -len("/book")].strip("/")
        client = request.client
        client_host = client.host if client else ""
        auth_mode = await run_in_threadpool(
            accounts.auth_mode, request.headers, client_host)
        body = await run_in_threadpool(
            _service_appointment_sync, sid, request.url.query,
            current_user, auth_mode)
        if body is None:
            has_service = await run_in_threadpool(
                site_catalog._service_by_id, sid)
            return _redirect_response(f"/service/{sid}" if has_service else "/")
        return _html_response(request, body)
    body = await run_in_threadpool(site_page_service._page_service, rest, current_user)
    if body is None:
        return _redirect_response("/")
    return _html_response(request, body)


async def _account_redirect(request: Request, target: str):
    """Redirect to `target` behind the renaper gate: anonymous + renaper mode skips the account page entirely
    and goes straight to the login (2026-08-03, see `_account_or_renaper`)."""
    early = await _prelude(request)
    if early is not None:
        return early
    current_user = await run_in_threadpool(accounts.current_user, request)
    location = await run_in_threadpool(
        site_shell._account_or_renaper, current_user, target)
    return _redirect_response(location)


async def _my_requests_redirect(request: Request):
    # Design-loop "Mis Turnos" (2026-07-19): the page lives INSIDE the
    # account dashboard (rail + column, #turnos tab).
    return await _account_redirect(request, "/account#turnos")


async def _request_redirect(request: Request, rest: str):
    # The standalone detail page was retired (the product owner, 2026-08-01) — old
    # bookmarks/emails always resolve to /my-requests regardless of id, same
    # renaper gate as /my-requests itself.
    return await _account_redirect(request, "/my-requests")


_LANG = api_body.query_docs(get={"lang": api_body.LANG_SWITCH})
router.add_api_route("/api/services", _api_services, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.ServicesOut)) | _LANG)
router.add_api_route("/api/doctors", _api_doctors, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.DoctorsOut)) | _LANG)
router.add_api_route("/api/centers", _api_centers, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.CentersOut)) | _LANG)
router.add_api_route("/api/directory", _api_directory, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.DirectoryOut)) | _LANG)
router.add_api_route("/api/links", _api_links, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.LinksOut)) | _LANG)
router.add_api_route(
    "/api/service-slots/{sid:path}", _api_service_slots, methods=["GET"],
    openapi_extra=api_body.docs(get=(None, api_models.ServiceSlotsOut)) | api_body.query_docs(get={
        "lang": api_body.LANG_SWITCH,
        "hold": "This visitor's own slot hold: the slot it holds still shows",
        "medico": "Only this doctor's slots (its id)",
        "prefer_medico": "This doctor's slots first, then the rest of the service's (its id)",
    }))
router.add_api_route("/service/{rest:path}", _service_page, methods=["GET"])
router.add_api_route("/my-requests", _my_requests_redirect, methods=["GET"])
router.add_api_route("/my-requests/", _my_requests_redirect, methods=["GET"])
router.add_api_route("/request/{rest:path}", _request_redirect, methods=["GET"])

# Slice 8 closing batch — de-linking redirects.
router.add_api_route("/app", _redirect_home, methods=["GET"])
router.add_api_route("/app/", _redirect_home, methods=["GET"])
router.add_api_route("/app.html", _redirect_home, methods=["GET"])
router.add_api_route("/clinicas", _redirect_home, methods=["GET"])
router.add_api_route("/clinicas/", _redirect_home, methods=["GET"])
router.add_api_route("/directorio", _redirect_home, methods=["GET"])
router.add_api_route("/directorio/", _redirect_home, methods=["GET"])
router.add_api_route("/directorio.html", _redirect_home, methods=["GET"])
router.add_api_route("/obras-sociales", _redirect_home, methods=["GET"])
router.add_api_route("/obras-sociales/", _redirect_home, methods=["GET"])
router.add_api_route("/panel", _redirect_home, methods=["GET"])
router.add_api_route("/panel/", _redirect_home, methods=["GET"])
router.add_api_route("/panel.html", _redirect_home, methods=["GET"])
router.add_api_route("/clinica/{rest:path}", _redirect_home, methods=["GET"])
router.add_api_route("/cobertura/{rest:path}", _redirect_home, methods=["GET"])

# Slice 8 closing batch — catalog/directory pages.
router.add_api_route("/doctors", _doctors_page, methods=["GET"])
router.add_api_route("/doctors/", _doctors_page, methods=["GET"])
router.add_api_route("/doctor/{mid:path}", _doctor_page, methods=["GET"])
router.add_api_route("/d/{cid:path}", _cluster_page, methods=["GET"])
router.add_api_route("/study/{rest:path}", _study_page, methods=["GET"])
router.add_api_route("/preparation/{pid:path}", _preparation_page, methods=["GET"])
router.add_api_route("/centers", _centers_page, methods=["GET"])
router.add_api_route("/centers/", _centers_page, methods=["GET"])
router.add_api_route("/center/{slug:path}", _center_page, methods=["GET"])
router.add_api_route("/news", _news_page, methods=["GET"])
router.add_api_route("/news/", _news_page, methods=["GET"])
router.add_api_route("/news/pedidos", _news_pedidos_page, methods=["GET"])
router.add_api_route("/news/pedidos/", _news_pedidos_page, methods=["GET"])
# 2026-08-27 hospital meeting — informational search results (no booking
# affordance): telemedicina/zoonosis/donar sangre.
router.add_api_route("/telemedicine", _telemedicine_page, methods=["GET"])
router.add_api_route("/telemedicine/", _telemedicine_page, methods=["GET"])
router.add_api_route("/zoonosis", _zoonosis_page, methods=["GET"])
router.add_api_route("/zoonosis/", _zoonosis_page, methods=["GET"])
router.add_api_route("/blood-donation", _blood_donation_page, methods=["GET"])
router.add_api_route("/blood-donation/", _blood_donation_page, methods=["GET"])
