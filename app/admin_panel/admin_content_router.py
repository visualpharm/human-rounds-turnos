"""admin_content_router.py — native FastAPI router for `admin_content.py`:
CRUD on the editable catalog (centers/services/doctors/preparations/
knowledge). Docs/roadmap/native-migration-campaign-20260826.md, family F5.

`admin_content` IS `turnos`-owned (`modules/turnos/module.json`'s `routes`),
so this router keeps the module-contract adapter's enable/disable parity:
`Depends(deps.require_module_enabled("turnos"))`.

Each endpoint replicates
the retired transport's structural validation BEFORE the auth gate (an
unrecognized `tipo` never reaches the auth gate; it returns 404) — see
`_internal_tipo()` below, and the shared terminal 404
(`_native_support.not_found_response()`) for the "FastAPI matched the path
template but the legacy regex declined it" cases:
  - `GET/POST /api/admin/content/{tipo}` — `tipo` outside `TYPE_MAP`.
  - `POST /api/admin/content/{tipo}/{sub}` — anything except the ONE
    literal `knowledge/properties` combination. Other two-segment action
    paths are not part of this route family's native contract.
  - `PUT/DELETE /api/admin/content/{tipo}/{item_id}` and
    `POST .../{tipo}/{item_id}/restaurar` — `tipo` outside `TYPE_MAP`.

`GET`+`POST` on `/api/admin/content/{tipo}` share ONE route object, and
`PUT`+`DELETE` on `/api/admin/content/{tipo}/{item_id}` share ONE route
object (branching on `request.method`) — same topology as the
adapted-legacy registration this replaces; the census's route-object count
is a Starlette-route count, not a method count.

Auth runs through `core.deps.require_admin` (via `_native_support.gate`,
role flags computed the SAME way `admin_content.list_role_flags()`/
`.edit_role_flags()` already do, or the constant `solo_admin=True` every
write branch but `_edit` uses). DB work runs in a threadpool; `db.
DbUnavailable` maps to the established 503 database-unavailable contract.
The handler-shaped transport has been retired; these are the only HTTP
entrypoints and they call the domain functions directly.

  GET    /api/admin/content/{tipo}
  POST   /api/admin/content/{tipo}
  POST   /api/admin/content/{tipo}/{sub}            (knowledge/properties)
  POST   /api/admin/content/{tipo}/{item_id}/restaurar
  PUT    /api/admin/content/{tipo}/{item_id}
  DELETE /api/admin/content/{tipo}/{item_id}
"""

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.admin_panel import admin_content, api_models, doctor_import
from app.admin_panel.native_json import ApiError as RosterError
from app.admin_panel._native_support import ApiError, gate, json_response, not_found_response
from app.core import api_body, db
from app.core import deps

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])

def _internal_tipo(tipo):
    """`TYPE_MAP[tipo]` or `None` — `None` means the legacy regex would
    have declined this path (structural 404, no auth check at all)."""
    return admin_content.TYPE_MAP.get(tipo)


async def _content_root(
        tipo: str, request: Request,
        pending: api_body.PendingBody = Depends(api_body.deferred(api_models.ADMIN, api_models.ContentItemIn, methods={"POST"}))):
    internal = _internal_tipo(tipo)
    if internal is None:
        return not_found_response()
    if request.method == "GET":
        try:
            identity = await gate(request, **admin_content.list_role_flags(internal))
        except ApiError as exc:
            return json_response(exc.payload, exc.status)
        try:
            payload = await run_in_threadpool(admin_content.list_payload, internal, identity)
        except db.DbUnavailable:
            return deps.answer_db_unavailable()
        return json_response(payload)
    # POST
    try:
        await gate(request, solo_admin=True)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    body = await pending.read()
    try:
        result = await api_body.run(
            body, admin_content.create_item, internal, body.value,
            expected=(admin_content.ContentError,))
    except admin_content.ContentError as exc:
        return json_response(exc.payload, status=exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(result)


async def _content_sub_action(
        tipo: str, sub: str, request: Request,
        pending: api_body.PendingBody = Depends(api_body.deferred(api_models.ADMIN, api_models.KnowledgePropertiesIn))):
    if (tipo, sub) not in (("knowledge", "properties"), ("knowledge", "golden")):
        return not_found_response()
    try:
        await gate(request, solo_admin=True)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    if sub == "golden":
        # "Probar conversaciones" on the wiki screen: the golden set against
        # the LIVE composed wiki (pack chain + admin overlay), no AI call.
        from app.patient_web import knowledge_golden
        return json_response(await run_in_threadpool(knowledge_golden.run))
    body = await pending.read()
    try:
        result = await api_body.run(
            body, admin_content.knowledge_properties, body.value,
            expected=(admin_content.ContentError,))
    except admin_content.ContentError as exc:
        return json_response(exc.payload, status=exc.status)
    return json_response(result)


async def _restore_content(tipo: str, item_id: str, request: Request):
    internal = _internal_tipo(tipo)
    if internal is None:
        return not_found_response()
    try:
        await gate(request, solo_admin=True)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        result = await run_in_threadpool(admin_content.restore_item, internal, item_id)
    except admin_content.ContentError as exc:
        return json_response(exc.payload, status=exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(result)


async def _content_item(
        tipo: str, item_id: str, request: Request,
        pending: api_body.PendingBody = Depends(api_body.deferred(api_models.ADMIN, api_models.ContentItemIn, methods={"PUT"}))):
    internal = _internal_tipo(tipo)
    if internal is None:
        return not_found_response()
    if request.method == "PUT":
        try:
            identity = await gate(request, permitir_medico=internal == "medicos", **admin_content.edit_role_flags(internal))
        except ApiError as exc:
            return json_response(exc.payload, exc.status)
        body = await pending.read()
        try:
            result = await api_body.run(
                body, admin_content.edit_item, internal, item_id, body.value, identity,
                expected=(admin_content.ContentError,))
        except admin_content.ContentError as exc:
            return json_response(exc.payload, status=exc.status)
        except db.DbUnavailable:
            return deps.answer_db_unavailable()
        return json_response(result)
    # DELETE
    try:
        await gate(request, solo_admin=True)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        result = await run_in_threadpool(admin_content.delete_item, internal, item_id)
    except admin_content.ContentError as exc:
        return json_response(exc.payload, status=exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(result)


async def _doctors_import(
        request: Request,
        dry_run: str = Query("true", description="`false` imports the list; anything else (the default) only "
                                                 "previews it"),
        filename: str = Query("", description="The file's name: its extension says how to read it (`.csv`, "
                                              "`.tsv`, `.txt`, `.xlsx`)")):
    """«Cargar lista» in Médicos: a raw CSV/TSV/TXT/XLSX body (not JSON), read with a hard
    size cap; `dry_run` defaults to the preview. Same hands as creating one doctor."""
    try:
        await gate(request, solo_admin=True)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        declared = int(request.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        declared = 0
    chunks, size = [], 0
    if declared <= doctor_import.MAX_BYTES:
        async for chunk in request.stream():
            size += len(chunk)
            if size > doctor_import.MAX_BYTES:
                break
            chunks.append(chunk)
    if declared > doctor_import.MAX_BYTES or size > doctor_import.MAX_BYTES:
        return json_response({"error": doctor_import.oversize_message()}, 413)
    try:
        result = await run_in_threadpool(
            doctor_import.import_file, b"".join(chunks), filename[:200], dry_run.lower() != "false")
    except RosterError as err:
        return json_response(err.body, err.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(result)


# Before the {tipo} routes: a fixed path, its own permission (core.permissions).
router.add_api_route(
    "/api/admin/doctors/import", _doctors_import, methods=["POST"],
    openapi_extra=api_body.docs(post=(api_body.RawBody(
        "The list of doctors as a file, sent as it is (at most 2 MB): CSV, TSV or TXT in UTF-8, or Excel "
        ".xlsx — told apart by `filename`", "text/csv", "application/octet-stream"), api_models.DoctorImportOut))
    | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/content/{tipo}", _content_root, methods=["GET", "POST"],
                     openapi_extra=api_body.docs(get=(None, api_models.ContentListOut),
                                                 post=(api_models.ContentItemIn, api_models.ContentItemOut))
                     | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/content/{tipo}/{sub}", _content_sub_action, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.KnowledgePropertiesIn,
                                                       api_models.KnowledgeActionOut)))
router.add_api_route("/api/admin/content/{tipo}/{item_id}/restaurar", _restore_content, methods=["POST"],
                     openapi_extra=api_body.docs(post=(None, api_models.ContentItemOut)) | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/content/{tipo}/{item_id}", _content_item, methods=["PUT", "DELETE"],
                     openapi_extra=api_body.docs(put=(api_models.ContentItemIn, api_models.ContentItemOut),
                                                 delete=(None, api_models.ContentDeletedOut))
                     | deps.STAFF_GATE_DOCS)
