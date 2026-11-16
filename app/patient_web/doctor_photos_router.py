"""doctor_photos_router.py — Tier 4 slice 8 closing batch (docs/roadmap/
tier4-replatform-design.md's own honest inventory): the FastAPI `router`
twin of `doctor_photos.py` — the admin-uploaded doctor/center profile photo.

Family W2c of docs/roadmap/native-migration-campaign-20260826.md: real
`async def` endpoints. Each endpoint calls the pure domain functions
`doctor_photos.py` extracted (`photo_bytes`, `preview_payload`,
`upload_payload`, `remove_payload`). `run_in_threadpool`
wraps every domain call: these do synchronous catalog/DB reads and file
I/O. The admin endpoints' auth is `Depends(core.deps.require_admin())` (no
role flags). The public `GET /{entity}-photo/{eid}` endpoint carries no
auth.

`doctor_photos` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router.

  GET/POST/DELETE /api/admin/{entity}/{id}/photo   entity in doctors|centers
  GET              /{entity}-photo/{id}             entity in doctor|center
  GET/POST/DELETE  /api/mi/foto                     a 'medico' session's own photo

Both path templates mix a literal segment with a `{...}` converter
(`{entity}-photo/{id}`) — verified against Starlette's own `compile_path()`
that this matches correctly (`entity`/`eid` resolve as expected, not the
whole segment being swallowed). FastAPI's `{entity}` converter matches ANY
non-slash segment, wider than `doctor_photos._ITEM_RE`/`_PUBLIC_RE`'s own
literal-set alternation (`doctors|centers` / `doctor|center`) — an `entity`
that matches neither alternative gets the SHARED terminal 404
(`core.terminal_response.TerminalNotFound` -> `{"error": "not found"}`)."""

import json
import io
from PIL import Image

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.core import db
from app.core import db_operators
from app.core import api_body, deps
from app.core.terminal_response import TerminalNotFound
from app.patient_web import api_models, doctor_photos
from app.core.http_json import json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])

_ADMIN_ENTITIES = ("doctors", "centers")
_PUBLIC_ENTITIES = doctor_photos._ENTITY_BY_PUBLIC  # {"doctor": "doctors", "center": "centers"}


def _declined():
    # The shared terminal 404.
    raise TerminalNotFound()


def _require_admin_entity(entity: str) -> str:
    """Dependency, NOT an inline check in the endpoint body: FastAPI
    resolves a path operation's dependencies in declaration order and
    short-circuits on the first exception (verified empirically), so
    declaring this ahead of `Depends(deps.require_admin())` keeps the
    order: an unknown entity must never reach
    `admin_auth.require_admin`; an inline check in the function body would run
    AFTER `Depends(deps.require_admin())` already raised 401/403, which is
    backwards."""
    if entity not in _ADMIN_ENTITIES:
        _declined()
    return entity




async def _run_guarded(fn, *args):
    """Runs a `doctor_photos` domain function off the event loop, turning
    `DoctorPhotoError`/`db.DbUnavailable` into the SAME response shape
    `_db_guard`/the `except DoctorPhotoError` branches build on the legacy
    transport — returns `(result, error_response)`, exactly one non-None."""
    try:
        return await run_in_threadpool(fn, *args), None
    except doctor_photos.DoctorPhotoError as exc:
        return None, _json_response(exc.body, status=exc.status)
    except db.DbUnavailable:
        return None, deps.answer_db_unavailable()


async def get_public_photo(entity: str, eid: str):
    ent = _PUBLIC_ENTITIES.get(entity)
    if ent is None:
        _declined()
    result, error = await _run_guarded(doctor_photos.photo_bytes, eid, ent)
    if error is not None:
        return error
    data, mime = result
    return Response(
        content=data, status_code=200, media_type=None,
        headers={"Content-Type": mime,
                 "Content-Length": str(len(data)),
                 "Cache-Control": "public, max-age=31536000, immutable"})


async def _read_json_lenient_or_400(request: Request, entity="doctors"):
    """Same contract as the legacy `handler._read_json()` call site in
    `doctor_photos._upload`: a malformed body is a 400 with the parse
    exception's message, NOT the `{}`-fallback other routers in this
    campaign use. Returns `(body, error_response)`, exactly one non-None."""
    limit = doctor_photos.MAX_BYTES if entity == "doctors" else 5 * 1024 * 1024
    # Base64 plus a bounded allowance for JSON keys/data-URI metadata. Count
    # streamed bytes as well: chunked clients need not send Content-Length.
    body_limit = 4 * ((limit + 2) // 3) + 64 * 1024
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > body_limit:
            return None, _json_response({"error": f"La foto supera los {limit // (1024 * 1024)} MB. Elegí una más liviana."}, status=413)
        raw.extend(chunk)
    try:
        return (json.loads(raw.decode("utf-8")) if raw else {}), None
    except Exception as e:  # noqa: BLE001
        return None, _json_response({"error": f"JSON inválido: {e}"}, status=400)


def _original_preview(eid):
    """Private original preview; decoding stays off the event loop."""
    from app.core import photo_guard
    result = doctor_photos.original_bytes(eid)
    try:
        if photo_guard.sniff(result) == 'heic':
            # Browsers cannot reliably display HEIC; only this authenticated
            # preview is converted. The private original stays unchanged.
            image = photo_guard.decode(result, max_bytes=doctor_photos.MAX_BYTES, max_pixels=50_000_000)
            image.thumbnail((1600, 1600))
            preview = io.BytesIO()
            image.save(preview, format='JPEG', quality=90)
            return preview.getvalue(), 'image/jpeg'
        with Image.open(io.BytesIO(result)) as image:
            return result, Image.MIME.get(image.format, 'application/octet-stream')
    except photo_guard.PhotoRejected as exc:
        raise doctor_photos.DoctorPhotoError(exc.status, exc.error) from exc
    except (OSError, ValueError, EOFError, SyntaxError) as exc:
        raise doctor_photos.DoctorPhotoError(400, {"error": "No se pudo leer el original. Volvé a subir la foto."}) from exc


async def _admin_photo_item(eid: str, request: Request,
                             entity: str = Depends(_require_admin_entity),
                             identity: dict = Depends(deps.require_admin(permitir_medico=True)),
                             # Documented on GET alone by `query_docs` below (one route object, three methods).
                             original: str | None = Query(None, include_in_schema=False)):
    """GET/POST/DELETE /api/admin/{entity}/{eid}/photo — ONE route object
    for all three methods (`route_objects` counts Starlette routes, not
    method rows: dispatching on `request.method` inside a single endpoint
    keeps this router's contribution unchanged, same pattern as
    `cuentas/account_photo_router.py`'s `_photo_endpoint`). `entity`'s
    validity is a Depends (see `_require_admin_entity`), not an inline
    check, so it is decided before `Depends(deps.require_admin())` runs."""
    if identity.get('rol') == 'medico':
        try:
            own = await run_in_threadpool(db_operators.operator_doctor_id, identity['usuario'])
        except db.DbUnavailable:
            return deps.answer_db_unavailable()
        if entity != 'doctors' or not own or own != eid:
            return _json_response({'error': 'no_encontrado'}, status=404)
    if request.method == "GET":
        if original == '1':
            if entity != 'doctors':
                return _json_response({'error': 'no_encontrado'}, status=404)
            result, error = await _run_guarded(_original_preview, eid)
            if error is not None:
                return error
            data, media_type = result
            return Response(data, media_type=media_type, headers={
                'Cache-Control': 'no-store',
                'Content-Disposition': 'inline', 'X-Content-Type-Options': 'nosniff'})
        result, error = await _run_guarded(doctor_photos.preview_payload, eid, entity)
        if error is not None:
            return error
        return _json_response(result)

    if request.method == "DELETE":
        result, error = await _run_guarded(doctor_photos.remove_payload, eid, entity)
        if error is not None:
            return error
        return _json_response(result)

    # POST — the only other method registered on this route.
    body, error = await _read_json_lenient_or_400(request, entity)
    if error is not None:
        return error
    api_body.watch(request, body, api_models.PhotoUploadIn)
    result, error = await _run_guarded(doctor_photos.upload_payload, eid, body, entity)
    if error is not None:
        return error
    return _json_response(result)


# Registration order matches the pre-migration router exactly
# (test_app_composition.py pins registration order): the admin route was
# registered before the public one there too.
router.add_api_route("/api/admin/{entity}/{eid}/photo", _admin_photo_item,
                      methods=["GET", "POST", "DELETE"],
                      openapi_extra=api_body.docs(get=(None, api_models.EntityPhotoOut),
                                                  post=(api_models.PhotoUploadIn, api_models.DoctorPhotoSavedOut),
                                                  delete=(None, api_body.OkOut))
                      | api_body.query_docs(get={"original": api_body.Query(
                          "`1`: the doctor's uploaded original image itself, instead of the photo's state as JSON",
                          {"type": "string", "enum": ["1"]})}))
async def _mine_photo(request: Request,
                       identity: dict = Depends(deps.require_admin(permitir_medico=True))):
    """GET/POST/DELETE /api/mi/foto — the CURRENT 'medico' session managing
    its OWN portrait. The doctor id comes from the session operator row
    (`db_operators.operator_doctor_id`), never from the URL or the body — same
    identity rule as `doctor_photos._mine_identity` on the legacy
    transport, same response shapes (403 `solo_medico` for a non-medico
    staff session, 400 `cuenta_sin_medico` when the account maps to no
    doctor), then the same pure payload functions as the admin route."""
    if identity.get("rol") != "medico":
        return _json_response({"error": "solo_medico"}, status=403)
    try:
        mid = await run_in_threadpool(db_operators.operator_doctor_id, identity["usuario"])
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    if not mid:
        return _json_response({"error": "cuenta_sin_medico"}, status=400)

    if request.method == "GET":
        result, error = await _run_guarded(doctor_photos.preview_payload, mid, "doctors")
        if error is not None:
            return error
        return _json_response(result)

    if request.method == "DELETE":
        result, error = await _run_guarded(doctor_photos.remove_payload, mid, "doctors")
        if error is not None:
            return error
        return _json_response(result)

    # POST — the only other method registered on this route.
    body, error = await _read_json_lenient_or_400(request)
    if error is not None:
        return error
    api_body.watch(request, body, api_models.PhotoUploadIn)
    result, error = await _run_guarded(doctor_photos.upload_payload, mid, body, "doctors")
    if error is not None:
        return error
    return _json_response(result)


router.add_api_route("/{entity}-photo/{eid}", get_public_photo, methods=["GET"])
router.add_api_route("/api/mi/foto", _mine_photo, methods=["GET", "POST", "DELETE"],
                      openapi_extra=api_body.docs(get=(None, api_models.DoctorPhotoOut),
                                                  post=(api_models.PhotoUploadIn, api_models.DoctorPhotoSavedOut),
                                                  delete=(None, api_body.OkOut)))
