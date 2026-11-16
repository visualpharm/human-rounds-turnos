"""branding_router.py — Tier 4 slice 8 closing batch (docs/roadmap/
tier4-replatform-design.md's own honest inventory): the FastAPI `router` for
`branding.py` — the custom-logo/color admin section's API half, PLUS the
native `GET /logo.png`/`GET /logo` logo-fallback endpoints (slice 9).

Platform-core (`branding` is in `PLATFORM_ROUTES`) — mounted directly in
`app.py`, unconditionally, same as `admin_users_router.py`.

Native-migration campaign, family W2c (docs/roadmap/native-migration-
campaign-20260826.md): the admin API half is real `async def` FastAPI
endpoints. Each endpoint calls the pure domain functions
`branding.py` extracted (`state_payload`, `apply_color`, `apply_logo_upload`,
`apply_logo_delete`). Auth
is `Depends(core.deps.require_admin(solo_admin=True, permitir_marca=True))`.

  GET/POST         /api/admin/branding
  POST/DELETE       /api/admin/branding/logo

`GET /logo.png` and `GET /logo` are served natively here, with the
three-way fallback chain `branding.default_logo_path()`/`custom_logo_payload()`
model: admin upload -> repo's shipped `logo.png` -> nothing (see the design
doc's logo-parity section): the custom-logo response carries the CSP/nosniff/
no-cache headers (`branding.CUSTOM_LOGO_HEADERS`), the shipped default is
served with `image/png` + one-day cache (`branding.STATIC_IMAGE_CACHE`) AND
the conditional-GET contract (`If-Modified-Since` -> 304), a missing default is a no-store 404, and
`/logo` (no extension) has NO shipped default — only an admin upload
answers it, exactly as the legacy static fallback never resolved "/logo" to
a file.

The shipped default is served from a plain `Response`, NOT
`fastapi.responses.FileResponse`: the legacy `SimpleHTTPRequestHandler`
sends only Content-Type/Content-Length/Last-Modified (+ the Cache-Control
its `end_headers` override adds) and honors `If-Modified-Since` -> 304.
`FileResponse` would ADD two headers the prod response never carried — an
`etag` and `accept-ranges` (Starlette computes both from the stat), plus an
`If-None-Match`/range negotiation path — so the demo (`TURNOS_ASGI=1`) would
serve a measurably different response than prod. The plain-Response path
here reproduces the legacy response header-for-header and no closer.

The 304 contract is `send_head`'s exactly: `If-None-Match` on the request
suppresses the `If-Modified-Since` check (the stdlib has no ETag to compare,
so it re-serves 200); only an IMS that parses to a naive (read as UTC) or
UTC datetime is compared, a non-UTC offset is left un-compared (-> 200), and
ill-formed values are ignored (-> 200). The 304 response itself carries the
same one-day `Cache-Control` as the 200 but NOT `Last-Modified` —
`send_head`'s NOT_MODIFIED branch sends only the status line, and
`Last-Modified` is the 200 branch's header. (It is precisely the ETag
`FileResponse` would add that would make `If-None-Match` meaningful — and
the legacy response never sent one.)"""

import datetime
import json
import os
from email.utils import formatdate, parsedate_to_datetime

from fastapi import APIRouter, Depends, Request, Response

from app.patient_web import api_models, branding
from app.core import api_body, deps
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter()

_ADMIN_MARCA = Depends(deps.require_admin(solo_admin=True, permitir_marca=True))


_BODY = api_body.BodyContract(message=lambda reason: f"JSON inválido: {reason}",
                              respond=_json_response)
"""Only for a crash on a body that does not match its model: a body that does not
decode reads as `{}` here."""


def _lenient(raw):
    """Same `{}`-on-failure leniency as `handler._read_json()`'s existing
    call sites in `branding.py`."""
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:  # noqa: BLE001
        return {}


async def _branding_state(
        request: Request, identity: dict = _ADMIN_MARCA,
        body: api_body.Body = Depends(api_body.lenient(_BODY, api_models.BrandingColorIn, _lenient, methods={"POST"}))):
    """GET/POST /api/admin/branding — ONE route object for both methods
    (`route_objects` counts Starlette routes, not method rows: dispatching
    on `request.method` inside a single endpoint keeps this router's
    contribution unchanged, same pattern as `cuentas/account_photo_router.
    py`'s `_photo_endpoint`)."""
    if request.method == "GET":
        return _json_response(request, branding.state_payload())
    # POST — the only other method registered on this route.
    try:
        result = api_body.call(body, branding.apply_color, body.value,
                               expected=(branding.BrandingError,))
    except branding.BrandingError as exc:
        return _json_response(request, exc.body, status=exc.status)
    return _json_response(request, result)


async def _branding_logo(
        request: Request, identity: dict = _ADMIN_MARCA,
        body: api_body.Body = Depends(api_body.lenient(_BODY, api_models.BrandingLogoIn, _lenient, methods={"POST"}))):
    """POST/DELETE /api/admin/branding/logo — ONE route object, same
    single-endpoint-dispatches-on-method pattern as `_branding_state`
    above."""
    if request.method == "DELETE":
        return _json_response(request, branding.apply_logo_delete())
    # POST — the only other method registered on this route.
    try:
        result = api_body.call(body, branding.apply_logo_upload, body.value,
                               expected=(branding.BrandingError,))
    except branding.BrandingError as exc:
        return _json_response(request, exc.body, status=exc.status)
    return _json_response(request, result)


router.add_api_route("/api/admin/branding", _branding_state, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.BrandingStateOut),
                                                 post=(api_models.BrandingColorIn,
                                                       api_models.BrandingColorOut)))
router.add_api_route("/api/admin/branding/logo", _branding_logo, methods=["POST", "DELETE"],
                     openapi_extra=api_body.docs(post=(api_models.BrandingLogoIn,
                                                       api_models.BrandingLogoOut),
                                                 delete=(None, api_models.BrandingLogoOut)))


def _custom_logo_response():
    """The admin-uploaded logo as a FastAPI Response, or None when no custom
    logo is set. The CSP/nosniff/no-cache headers come from
    `branding.CUSTOM_LOGO_HEADERS` — the constant the retired `_serve_logo`
    used, so the headers did not change at the cutover."""
    payload = branding.custom_logo_payload()
    if payload is None:
        return None
    data, media_type = payload
    return Response(content=data, media_type=media_type,
                    headers=dict(branding.CUSTOM_LOGO_HEADERS))


def _missing_logo_response():
    """A 404 that, like every legacy error response, is never cached
    (`Handler.end_headers` forces `no-store` on `code >= 400`). Matches the
    legacy `missing-default` contract: the STATUS + the no-store, not a body
    an `<img>` request never inspects."""
    return Response(status_code=404, headers={"Cache-Control": "no-store"})


def _default_logo_response(request: Request):
    """The shipped `logo.png` served the EXACT way the legacy
    `SimpleHTTPRequestHandler.send_head()` served it: a plain 200 Response
    carrying Content-Type/Content-Length (set by `Response` itself from the
    bytes), `Last-Modified`, and the one-day `Cache-Control` — nothing else.
    No ETag, no Accept-Ranges, no range negotiation — those are
    `FileResponse`'s additions the prod response never had (see the module
    docstring).

    The conditional-GET path is a line-for-line mirror of `send_head`'s ONE
    conditional, NOT a re-implementation of the HTTP RFC:

      * `If-None-Match` SUPPRESSES the `If-Modified-Since` check — when the
        request carries an ETag validator the stdlib skips IMS entirely (it
        has no ETag to compare), so the resource is re-served as 200.
      * The IMS value is parsed with `parsedate_to_datetime`; an ill-formed
        value (TypeError/IndexError/OverflowError/ValueError) is IGNORED and
        the resource is re-served as 200 — NOT broadened by a fallback guess.
      * Only a value with NO timezone (read as UTC, per RFC 7231's obsolete
        format) or an explicit UTC timezone is compared. A non-UTC offset is
        NOT compared — the resource is re-served as 200 — matching stdlib,
        which narrows the comparison to UTC rather than normalizing any
        offset to a timestamp.
      * The comparison itself is stdlib's: the file's mtime as a UTC datetime
        with microseconds dropped, `<=` the (UTC) IMS datetime.

    A 304 carries the SAME one-day `Cache-Control` as the 200, but NOT
    `Last-Modified`: `send_head`'s NOT_MODIFIED branch sends only the status
    line + the cache header `end_headers` adds — `Last-Modified` is sent on
    the 200 branch alone.

    Falls to the no-store 404 when there's no shipped default OR the file
    can't be read, mirroring `send_head`'s own 404 on a missing file."""
    shipped = branding.default_logo_path()
    if shipped is None:
        return _missing_logo_response()
    try:
        st = os.stat(shipped)
        with open(shipped, "rb") as f:
            data = f.read()
    except OSError:
        return _missing_logo_response()

    # The one-day image cache Handler.end_headers puts on an unversioned
    # image — reproduced byte-for-byte, on BOTH the 200 and the 304.
    cache_control = branding.STATIC_IMAGE_CACHE

    # Conditional GET — `send_head`'s one and only conditional. If-None-Match
    # suppresses it entirely; only an If-Modified-Since that parses to a
    # naive (read as UTC) or UTC datetime is compared, and a non-UTC offset is
    # left un-compared (-> 200). See the docstring for the full contract.
    if ("if-modified-since" in request.headers
            and "if-none-match" not in request.headers):
        try:
            ims_dt = parsedate_to_datetime(request.headers["if-modified-since"])
        except (TypeError, IndexError, OverflowError, ValueError):
            ims_dt = None
        if ims_dt is not None:
            if ims_dt.tzinfo is None:
                ims_dt = ims_dt.replace(tzinfo=datetime.timezone.utc)
            if ims_dt.tzinfo is datetime.timezone.utc:
                last_modif = datetime.datetime.fromtimestamp(
                    st.st_mtime, datetime.timezone.utc).replace(microsecond=0)
                if last_modif <= ims_dt:
                    # stdlib's NOT_MODIFIED branch: status + cache header,
                    # no Last-Modified (that's the 200 branch only).
                    return Response(
                        status_code=304, headers={"Cache-Control": cache_control})

    last_modified = formatdate(st.st_mtime, usegmt=True)
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": cache_control,
                             "Last-Modified": last_modified})


@router.get("/logo.png")
async def _logo_png(request: Request):
    """Three-way fallback, native: admin upload -> shipped `logo.png` ->
    404. Each leg reproduces the legacy contract (see the module docstring and
    `_default_logo_response`/`_custom_logo_response`/`_missing_logo_response`)."""
    resp = _custom_logo_response()
    if resp is not None:
        return resp
    return _default_logo_response(request)


@router.get("/logo")
async def _logo(request: Request):
    """/logo has NO shipped default — the legacy static fallback serves files
    BY NAME and "/logo" isn't a file, so only an admin upload answers it; with
    none, the same no-store 404 as a missing `/logo.png` default."""
    resp = _custom_logo_response()
    if resp is not None:
        return resp
    return _missing_logo_response()
