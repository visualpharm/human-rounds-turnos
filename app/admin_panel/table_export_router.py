"""table_export_router.py — `POST /api/admin/table-export`: the panel's
«Descargar» of a small table as a CSV file for Excel (`admin_panel/table_export.py`).

Platform-core, mounted in `routers/platform.py`. The central gate admits any
staff session (`permissions._PREFIXES` maps the path to ANY); the screen the
file comes from then decides with ITS permission, the row limit and the
access log (see the domain module). A POST because the body is the visible
table; the read-only demo session may use it (`admin_auth._DEMO_MUTATION_ALLOW`)
— it writes nothing but the access rows.

  POST /api/admin/table-export"""

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.admin_panel import api_models, table_export
from app.admin_panel._native_support import ApiError, gate, json_response, read_json_body
from app.auth import admin_auth
from app.core import access_log, api_body, i18n
from app.core import deps

router = APIRouter()

_ROLE_FLAGS = dict(permitir_medico=True, permitir_admisionista=True, permitir_reportes=True)
_MAX_BODY = 4 * 1024 * 1024


def _too_big():
    return json_response({"error": i18n.t_or(
        "admin.shell.export.error.too_big",
        "La tabla es demasiado grande para descargarla. Elegí un día, un médico o un filtro más chico.",
        i18n.active()), "code": "demasiado_grande"}, status=413)


async def _table_export(request: Request):
    try:
        identity = await gate(request, **_ROLE_FLAGS)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    if int(request.headers.get("content-length") or 0) > _MAX_BODY:
        return _too_big()
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        return _too_big()
    try:
        body = await read_json_body(request)
        api_body.watch(request, body, api_models.TableExportIn)
        req = table_export.parse(body)
    except table_export.ExportError as exc:
        return json_response(exc.body(), status=exc.status)
    except ValueError:
        return json_response(table_export.invalid().body(), status=400)
    held = admin_auth.active_permissions()
    if not table_export.allowed(req["screen"], identity.get("rol"), held):
        return json_response({"error": i18n.t_or(
            "admin.shell.export.error.forbidden", "Tu usuario no puede ver esta pantalla, así que no puede descargarla.",
            i18n.active()), "code": "rol_no_autorizado"}, status=403)
    items = table_export.access_items(req)
    if items:
        try:
            await run_in_threadpool(access_log.record_many, identity, table_export.ACCESS_ACTION, items)
        except access_log.AccessLogUnavailable as exc:
            status, payload = access_log.error_body(exc)
            return json_response(payload, status=status)
    content = table_export.csv_bytes(req["columns"], req["rows"])
    name = table_export.filename(req["title"], req["day"])
    return Response(content=content, status_code=200, headers={
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Length": str(len(content)),
        "Cache-Control": "no-store",
        "Content-Disposition": table_export.content_disposition(name),
    })


router.add_api_route("/api/admin/table-export", _table_export, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.TableExportIn, None)) | deps.STAFF_GATE_DOCS)
