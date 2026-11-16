"""admin_patients_router.py — native ASGI implementation of the patient
roster (create/detail/edit/archive), dependents, and the digitized
clinical-history import the admin console exposes.

Native-migration split (docs/roadmap/native-migration-campaign-20260826.md,
family F3): `admin_patients.py` was split into pure domain functions
(`list_patients`, `patient_detail`, `create_patient`, `edit_patient`,
`archive_patient`, `dependent_save`, `dependent_delete`,
`save_clinical_history` — take parsed arguments, return data or raise
`admin_panel.native_json.ApiError`). The handler-shaped transport is retired.
This router calls those domain functions directly — no
`module_router.route_via_handle`, no `LegacyHandlerShim` — FastAPI parses
the request, `starlette.concurrency.run_in_threadpool` runs the
(synchronous, psycopg-calling) domain function, and `admin_panel.
native_json` builds the byte-identical `Response`.

`admin_patients` IS `turnos`-owned (`modules/turnos/module.json`'s `routes`),
so `Depends(deps.require_module_enabled("turnos"))` gates this router
exactly as the pre-migration adapter did.

Auth matches the legacy role flags per path exactly (`admin_auth.
require_admin(handler, ...)` calls in `admin_patients.py`'s own `_list`/
`_detail`/etc.): the roster list alone additionally allows `permitir_medico`;
every other route stays `permitir_admisionista`-only except clinical-history
(`solo_admin`). `/api/admin/patients` GET and POST share ONE route object
(see below) but different flags — `native_patients_collection` runs the
superset gate (`permitir_medico=True`) and then re-applies `admin_auth.
require_admin`'s own documented narrowing (`rol == "medico" and not
permitir_medico -> 403 rol_no_autorizado`, auth/admin_auth.py) for every
method except GET, so a médico session gets the SAME 403 on POST it always
got.

Same FIVE path templates the pre-migration adapter mounted — one route
object per template, dispatching on method internally, matching the
architecture census's route-object accounting (not one object per method):

  GET,POST            /api/admin/patients
  GET,PUT,DELETE       /api/admin/patients/{dni}
  POST                /api/admin/patients/{dni}/dependents
  DELETE              /api/admin/patients/{dni}/dependents/{dni_dep}
  POST                /api/admin/patients/{dni}/clinical-history

Terminal contract: every `{dni}`/`{dni_dep}` path segment above is
re-validated against the SAME `\\d{7,9}` shape `admin_patients.py`'s own
`_DNI_RE` (and the equivalent path regexes `_ITEM_RE`/`_DEPS_RE`/`_DEP_RE`/
`_HIST_RE`) already enforce — a non-numeric or wrong-length id is a path
FastAPI's route template matched but the module's own inner regex declined,
so it returns the shared terminal 404 (`native_json.terminal_not_found()`),
same terminal-404 behavior preserved from before the native cutover.
"""

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.admin_panel import admin_patients, api_models
from app.admin_panel.native_json import ApiError, json_response, terminal_not_found
from app.core import access_log, api_body, db, deps, permissions, subject_export, tz
from app.core import db_accounts

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


def _valid_dni(value: str) -> bool:
    return bool(admin_patients._DNI_RE.match(value))


async def _dni(dni: str) -> str:
    """The `{dni}` of the path, or the terminal 404 — after the gate, before the body."""
    if not _valid_dni(dni):
        raise deps.EarlyResponse(terminal_not_found())
    return dni


async def native_patients_collection(
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_admisionista=True, permitir_medico=True)),
    # Decoded below, after the médico refusal.
    pending: api_body.PendingBody = Depends(api_body.deferred(
        api_models.ADMIN_GZIP, api_models.PatientIn, methods={"POST"})),
):
    if request.method == "GET":
        qs = parse_qs(request.url.query)
        result = await run_in_threadpool(admin_patients.list_patients, identity, qs)
        return json_response(request, result)
    # POST /api/admin/patients: the legacy `_create()` gates with
    # `permitir_admisionista=True` only (no `permitir_medico`) — replicate
    # `admin_auth.require_admin`'s own rejection for a médico session here.
    if identity.get("rol") == "medico":
        return json_response(request, {"error": "rol_no_autorizado"}, status=403)
    body = await pending.read()
    try:
        result = await api_body.run(
            body, admin_patients.create_patient, body.value, expected=(ApiError,))
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def native_patients_item(
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_admisionista=True)),
    dni: str = Depends(_dni),
    body: api_body.Body = Depends(api_body.body(api_models.ADMIN_GZIP, api_models.PatientIn, methods={"PUT"})),
    # One route object serves GET, PUT and DELETE: `query_docs` below documents it on DELETE alone.
    cancelar_turnos: str | None = Query(None, include_in_schema=False),
):
    if request.method == "GET":
        # K10: abrir la ficha queda en el registro de accesos. Es pantalla de
        # la mesa (va por rol); un médico al que le dieron «pacientes» sólo
        # abre la de los suyos. Una ficha que no existe es 404 sin fila; la
        # que existe no sale de acá hasta que su fila quedó escrita.
        try:
            result = await run_in_threadpool(admin_patients.patient_detail, dni)
            await run_in_threadpool(access_log.check, identity, "ficha", dni, None, False)
        except (access_log.AccessDenied, access_log.AccessLogUnavailable) as exc:
            status, body = access_log.error_body(exc)
            return json_response(request, body, status=status)
        except ApiError as err:
            return json_response(request, err.body, status=err.status)
        if isinstance(result, dict):
            # La ficha ofrece «Quién abrió sus datos» sólo a quien puede verlo.
            result["ver_accesos"] = await run_in_threadpool(_holds, identity, "usuarios")
            result["turnos"] = await run_in_threadpool(
                admin_patients.patient_turnos, dni, identity)
        return json_response(request, result)
    if request.method == "DELETE":
        try:
            result = await run_in_threadpool(
                admin_patients.archive_patient, dni, identity,
                cancelar_turnos == "1", request)
        except ApiError as err:
            return json_response(request, err.body, status=err.status)
        return json_response(request, result)
    # PUT
    try:
        # Editing someone's identity (the desk filling in a missing name, a
        # corrected phone) is written to the access log first, like a read: a
        # row that can't be written refuses the edit. A person who doesn't
        # exist is a 404 with no row.
        if not await run_in_threadpool(db_accounts.get_user, dni):
            raise ApiError({"error": "Paciente no encontrado."}, status=404)
        await run_in_threadpool(access_log.record, identity, "edicion", dni)
        result = await api_body.run(
            body, admin_patients.edit_patient, dni, body.value, expected=(ApiError,))
    except access_log.AccessLogUnavailable as exc:
        status, err_body = access_log.error_body(exc)
        return json_response(request, err_body, status=status)
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def native_patients_temp_password(
    dni: str,
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_admisionista=True)),
):
    """TASK-50: the desk gives a patient a new temporary password. Same hands
    as creating a patient (admin, admisión — never a doctor). The action is
    written to the access log first: a row that can't be written refuses it,
    as for a read."""
    if not _valid_dni(dni):
        return terminal_not_found()
    # The same refusal POST /api/admin/patients gives a médico session.
    if identity.get("rol") == "medico":
        return json_response(request, {"error": "rol_no_autorizado"}, status=403)
    try:
        if not await run_in_threadpool(db_accounts.get_user, dni):
            raise ApiError({"error": "Paciente no encontrado."}, status=404)
        await run_in_threadpool(access_log.record, identity, "contrasena", dni)
        result = await run_in_threadpool(admin_patients.temp_password, dni)
    except access_log.AccessLogUnavailable as exc:
        status, body = access_log.error_body(exc)
        return json_response(request, body, status=status)
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def native_patients_dependent_save(
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_admisionista=True)),
    dni: str = Depends(_dni),
    body: api_body.Body = Depends(api_body.body(api_models.ADMIN_GZIP, api_models.PatientDependentIn)),
):
    try:
        result = await api_body.run(
            body, admin_patients.dependent_save, dni, body.value, expected=(ApiError,))
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def native_patients_dependent_delete(
    dni: str,
    dni_dep: str,
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_admisionista=True)),
):
    if not (_valid_dni(dni) and _valid_dni(dni_dep)):
        return terminal_not_found()
    try:
        result = await run_in_threadpool(admin_patients.dependent_delete, dni, dni_dep)
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def native_patients_clinical_history(
    request: Request,
    identity: dict = Depends(deps.require_admin(solo_admin=True)),
    dni: str = Depends(_dni),
    body: api_body.Body = Depends(api_body.body(api_models.ADMIN_GZIP, api_models.ClinicalHistoryIn)),
):
    try:
        result = await api_body.run(
            body, admin_patients.save_clinical_history, dni, body.value, identity, expected=(ApiError,))
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    return json_response(request, result)


async def native_dni_claims(
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_admisionista=True)),
):
    """TASK-68: the open claims on a document number, oldest first — who asks, who holds the number now.
    The desk's list; never a doctor's."""
    if identity.get("rol") == "medico":
        return json_response(request, {"error": "rol_no_autorizado"}, status=403)
    try:
        rows = await run_in_threadpool(db_accounts.open_claims)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    items = [{"id": r["id"], "dni": r["dni_claimed"], "estado": r["status"],
              "nombre": r.get("nombre"), "apellido": r.get("apellido"), "contacto": r.get("contacto"),
              "email": r.get("email"), "titular_dni": r.get("holder_dni"),
              "titular_nombre": r.get("holder_nombre"), "titular_apellido": r.get("holder_apellido")}
             for r in rows]
    return json_response(request, {"items": items})


async def native_dni_claim_resolve(
    claim_id: int,
    request: Request,
    identity: dict = Depends(deps.require_admin(permitir_admisionista=True)),
    body: api_body.Body = Depends(api_body.body(api_models.ADMIN_GZIP, api_models.DniClaimResolveIn)),
):
    """TASK-68: approve or reject one claim, in one transaction (`db_accounts.resolve_claim`). The decision is
    written to the access log first (target: the claimed number): a row that can't be written refuses it."""
    if identity.get("rol") == "medico":
        return json_response(request, {"error": "rol_no_autorizado"}, status=403)
    data = body.value or {}
    if not isinstance(data.get("aprobar"), bool):
        return json_response(request, {"error": "aprobar_requerido"}, status=400)
    try:
        number = await run_in_threadpool(db_accounts.open_claim_number, claim_id)
        if number is None:
            return json_response(request, {"error": "reclamo_no_encontrado"}, status=404)
        await run_in_threadpool(access_log.record, identity, "reclamo", number)
        result = await run_in_threadpool(
            db_accounts.resolve_claim, claim_id, data["aprobar"], identity.get("usuario"), data.get("nota") or "")
    except access_log.AccessLogUnavailable as exc:
        status, err_body = access_log.error_body(exc)
        return json_response(request, err_body, status=status)
    except db_accounts.ClaimError as err:
        status = {"no_encontrada": 404, "ya_resuelta": 409, "conflicto": 409}.get(err.code, 400)
        return json_response(request, {"error": err.code}, status=status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return json_response(request, {"ok": True, "estado": result["status"], "dni": result["dni"]})


def _holds(identity, permiso):
    """¿La sesión tiene `permiso`? Misma fuente que el portón central; ante
    cualquier duda, no (sólo decide si la ficha muestra un botón)."""
    if (identity or {}).get("rol") == "admin":
        return True
    try:
        acceso = permissions.access_for(identity.get("usuario"), identity.get("rol"))
    except Exception:  # noqa: BLE001
        return False
    return acceso.get("state") == "ok" and permiso in acceso.get("permissions", ())


async def native_patients_access_log(
    dni: str,
    request: Request,
    identity: dict = Depends(deps.require_admin(solo_admin=True)),
):
    """«Quién vio a este paciente» (K10): el registro de accesos a sus datos
    clínicos, lo más nuevo primero. Sólo el administrador (permiso
    `usuarios`, el que decide quién accede a qué). Mirarlo no se registra:
    no muestra datos clínicos."""
    if not _valid_dni(dni):
        return terminal_not_found()
    try:
        filas = await run_in_threadpool(access_log.history, dni, 500)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    items = []
    for f in filas:
        # Hora de la instalación, nunca el offset que devuelve Postgres.
        ts = f["ts"].astimezone(tz.tzinfo()) if f.get("ts") else None
        items.append({"dia": ts.date().isoformat() if ts else None,
                      "hora": ts.strftime("%H:%M") if ts else None,
                      "usuario": f.get("actor"), "rol": f.get("actor_role"),
                      "accion": f.get("action"), "objeto": f.get("target_id"),
                      "permitido": bool(f.get("allowed"))})
    return json_response(request, {"items": items,
                                   "retencion_dias": access_log.retention_days()})


async def native_patients_data_export(
    dni: str,
    request: Request,
    identity: dict = Depends(deps.require_admin(solo_admin=True)),
    phone: str | None = Query(None, description="Also search the places keyed by this phone"),
    email: str | None = Query(None, description="Also search the places keyed by this email"),
    confirmar_compartido: str | None = Query(
        None, description="`1`: a phone or email another account also uses is this person's",
        json_schema_extra={"enum": ["1"]}),
):
    """«Exportar datos del paciente» (Ley 25.326 art. 14): the zip with ALL of one
    person's data — read only, no secrets, with a manifest of every place
    searched. Only the administrator (permiso `usuarios`). It is a read of all
    the clinical data at once, so it leaves its own access row (`exportacion`)
    before anything is built, and a ficha that doesn't exist is a 404 with no
    row. The optional `phone` / `email` widen the search to the places keyed by
    them; `confirmar_compartido=1` says a phone/email another account also uses
    is this person's."""
    if not _valid_dni(dni):
        return terminal_not_found()
    try:
        await run_in_threadpool(admin_patients.patient_detail, dni)
        await run_in_threadpool(access_log.check, identity, "exportacion", dni, None, False)
        subject = await run_in_threadpool(
            subject_export.resolve_subject, dni, phone, email, confirmar_compartido == "1")
        _manifest, size, zipped = await run_in_threadpool(subject_export.export_to_tempfile, subject)
    except (access_log.AccessDenied, access_log.AccessLogUnavailable) as exc:
        status, body = access_log.error_body(exc)
        return json_response(request, body, status=status)
    except ApiError as err:
        return json_response(request, err.body, status=err.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable(request=request)
    return StreamingResponse(subject_export.iter_and_close(zipped), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="datos-{subject.dni_hash[:8]}.zip"',
        "Content-Length": str(size), "Cache-Control": "no-store"})


router.add_api_route("/api/admin/patients", native_patients_collection, methods=["GET", "POST"],
                      openapi_extra=api_body.docs(get=(None, api_models.PatientListOut),
                                                  post=(api_models.PatientIn, api_models.PatientOut))
                      | api_body.query_docs(get={
                          "q": "Search by DNI, name or a doctor's name; without it, the recent contacts",
                          "limit": api_body.Query("Page size without a search (default 200)", {"type": "integer"}),
                          "offset": api_body.Query("Skip this many first, without a search (default 0)",
                                                   {"type": "integer"}),
                      }))
router.add_api_route("/api/admin/dni-claims", native_dni_claims, methods=["GET"],
                      openapi_extra=api_body.docs(get=(None, api_models.DniClaimsOut)))
router.add_api_route("/api/admin/dni-claims/{claim_id}/resolve", native_dni_claim_resolve, methods=["POST"],
                      openapi_extra=api_body.docs(post=(api_models.DniClaimResolveIn, api_models.DniClaimResolveOut)))
router.add_api_route("/api/admin/patients/{dni}/access-log", native_patients_access_log,
                      methods=["GET"], openapi_extra=api_body.docs(get=(None, api_models.AccessLogOut)))
router.add_api_route("/api/admin/patients/{dni}/data-export", native_patients_data_export,
                      methods=["GET"])
router.add_api_route("/api/admin/patients/{dni}", native_patients_item,
                      methods=["GET", "PUT", "DELETE"],
                      openapi_extra=api_body.docs(get=(None, api_models.PatientDetailOut),
                                                  put=(api_models.PatientIn, api_models.PatientOut),
                                                  delete=(None, api_models.PatientOut))
                      | api_body.query_docs(delete={"cancelar_turnos": api_body.Query(
                          "`1`: also cancel the patient's upcoming appointments", {"type": "string", "enum": ["1"]})}))
router.add_api_route("/api/admin/patients/{dni}/temp-password", native_patients_temp_password,
                      methods=["POST"], openapi_extra=api_body.docs(post=(None, api_models.TempPasswordOut)))
router.add_api_route("/api/admin/patients/{dni}/dependents", native_patients_dependent_save,
                      methods=["POST"], openapi_extra=api_body.docs(
                          post=(api_models.PatientDependentIn, api_models.PatientDependentOut)))
router.add_api_route("/api/admin/patients/{dni}/dependents/{dni_dep}",
                      native_patients_dependent_delete, methods=["DELETE"],
                      openapi_extra=api_body.docs(delete=(None, api_models.DependentDeleteOut)))
router.add_api_route("/api/admin/patients/{dni}/clinical-history",
                      native_patients_clinical_history, methods=["POST"], openapi_extra=api_body.docs(
                          post=(api_models.ClinicalHistoryIn, api_models.ClinicalHistoryOut)))
