"""lab_results_router.py — native-route migration (F8, 2026-08-26): the
FastAPI `router` twin of `lab_results.py` — the patient-facing "Resultados de
laboratorio" view. A real native endpoint calling the domain
functions (`lab_results.informes_de`, `lab_results._clinico_href`).

`lab_results` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router, same as every sibling in this family.

  GET /api/account/lab-results
  GET /account/lab-results/open   302 to the provider's signed lab page
                                  (`lab_link.py`; 404 when no key is set)

Identity is patient-session auth (`cuentas.account_deps.optional_account`,
i.e. `accounts.current_user`), NOT staff auth — `core.deps.require_admin` does
not apply here. A request with no valid session cookie 401s with this
family's own `{"error": "auth_requerida"}`. Three routes keep their own read
because something answers before the session there: the «open» button (404
when no key is set), the upload and the delete (FastAPI's 422 for a missing
file or a non-numeric id — on a portal installation reading the session can
create or bind the account, so it must not move ahead of that)."""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.core import api_body, deps
from app.cuentas import account_deps, accounts, claim_gate
from app.ai import api_models
from app.ai import lab_link
from app.ai import lab_results
from app.core.http_json import json_response as _json_response
from app.core.terminal_response import json_not_found_response, redirect_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])

# Room for the multipart framing around a file at the cap (boundaries, headers, the DNI field).
_MULTIPART_OVERHEAD = 1024 * 1024


@router.get("/api/account/lab-results", openapi_extra=api_body.docs(get=(None, api_models.LabResultsOut)))
async def account_lab_results(request: Request, user: dict | None = Depends(account_deps.optional_account)):
    if not user:
        return _json_response({"error": "auth_requerida"}, status=401)
    if await run_in_threadpool(claim_gate.hidden, user.get("dni")):
        # An account the desk has not confirmed sees no clinical data (TASK-68): the same answer as a
        # patient with no reports, so the screen needs no new state.
        informes, estados, series, pendiente = [], {}, [], 0
    else:
        informes, estados, series, pendiente = await run_in_threadpool(
            lab_results.informes_de, user.get("dni"))
    clinico_href = await run_in_threadpool(lab_results._clinico_href)
    return _json_response({
        "informes": informes,
        "series": series,
        "analisis_pendiente": pendiente,
        "fuentes_estado": estados,
        "clinico_href": clinico_href,
        "laboratorio_href": lab_link.OPEN_PATH if lab_link.enabled() else None,
    })


@router.get("/account/lab-results/open")
async def account_lab_results_open(request: Request):
    """The button «Ver resultados del laboratorio»: signs the provider's URL
    for the DNI of the logged-in account — never one from the request — and
    answers 302. No key configured = the route does not exist."""
    if not lab_link.enabled():
        return json_not_found_response()
    user = await run_in_threadpool(accounts.current_user, request)
    if not user:
        destino = accounts.auth_redirect_target(None, lab_link.OPEN_PATH) or "/account#labs"
        return _no_store(redirect_response(destino))
    if await run_in_threadpool(claim_gate.hidden, user.get("dni")):
        return json_not_found_response()
    url = lab_link.signed_url(user.get("dni"))
    if not url:
        return json_not_found_response()
    return _no_store(redirect_response(url))


def _no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/api/account/lab-results/upload",
             openapi_extra=api_body.docs(post=(api_body.RawBody(
                 "The result file (`archivo`: image or PDF) and, for a dependent, `patient_dni`",
                 "multipart/form-data"), api_models.LabUploadOut)) | deps.PATIENT_SESSION_DOCS)
async def account_lab_results_upload(request: Request):
    """El paciente sube un resultado propio (multipart). Misma puerta que el
    GET: identidad de la sesión, nunca un DNI del cuerpo — `patient_dni` sólo
    puede ser una persona a cargo ya vinculada a esa cuenta."""
    # The route declares no File/Form parameter on purpose: FastAPI reads a declared multipart
    # body BEFORE any dependency or the handler runs, and Starlette spools a big part to disk, so
    # an anonymous 25 MiB upload was written out before the 401 (security review 2026-11,
    # finding 17). The module gate (a router dependency) and the session now answer first, then
    # the declared length, and only then the form is parsed here.
    user = await run_in_threadpool(accounts.current_user, request)
    if not user:
        return _json_response({"error": "auth_requerida"}, status=401)
    try:
        declared = int(request.headers.get("content-length") or "")
    except ValueError:
        return _json_response({"error": "longitud_requerida"}, status=411)
    if declared > lab_results.MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD:
        return _json_response({"error": "archivo_muy_pesado"}, status=413)
    try:
        form = await request.form(max_files=1, max_fields=8)
    except Exception:  # noqa: BLE001 — not a multipart body: the same answer as a missing file
        form = None
    try:
        archivo = form.get("archivo") if form is not None else None
        if archivo is None or not hasattr(archivo, "read"):
            return JSONResponse({"detail": [{"type": "missing", "loc": ["body", "archivo"],
                                             "msg": "Field required", "input": None}]}, status_code=422)
        patient_dni = str(form.get("patient_dni") or "")
        # One byte past the cap is enough for subir_resultado to refuse it: the
        # rest of an oversized file is never copied into memory (review 2026-11, L-1).
        datos = await archivo.read(lab_results.MAX_UPLOAD_BYTES + 1)
    finally:
        if form is not None:
            await form.close()
    status, body = await run_in_threadpool(
        lab_results.subir_resultado, user, datos,
        archivo.content_type, archivo.filename, patient_dni)
    return _json_response(body, status=status)


@router.delete("/api/account/lab-results/upload/{upload_id}",
               openapi_extra=api_body.docs(delete=(None, api_body.OkOut)) | deps.PATIENT_SESSION_DOCS)
async def account_lab_results_upload_delete(request: Request, upload_id: int):
    # Read here, as in the upload: a non-numeric id is FastAPI's 422 before the session.
    user = await run_in_threadpool(accounts.current_user, request)
    if not user:
        return _json_response({"error": "auth_requerida"}, status=401)
    status, body = await run_in_threadpool(
        lab_results.borrar_resultado, user, upload_id)
    return _json_response(body, status=status)
