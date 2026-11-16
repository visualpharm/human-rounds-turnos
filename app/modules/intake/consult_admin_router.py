"""consult_admin_router.py — native FastAPI router for `consult_admin.py`'s
two paths (family W2b of docs/roadmap/native-migration-campaign-20260826.md).

Both endpoints call the pure domain functions `consult_payload`/
`confirm_payload`, plus the shared `guard_own_interview_or_404` scoping check.

Staff auth via `modules.intake._native_support.gate()` (manual call to
`core.deps.require_admin(permitir_medico=True)`, the role flag
`admin_auth.require_admin` takes) — see that helper's own docstring for why it's called manually
rather than as a `Depends` default (these two routes have no `?lang=` switch
to reorder around, but the family's other routers do, so every W2b router
uses the same manual-call convention for consistency and drift-safety).

`{cid}` is FastAPI's loosest path converter (any non-slash string), but
the ids are digits only (`_CONSULT_RE`/`_CONFIRM_RE`) —
a non-numeric `cid` declines with the shared terminal-404
contract (migration brief step 3), same convention
`modules/turnos/appointment_confirm_router.py` already established.

  GET  /api/admin/intake/consult/{cid}
  POST /api/admin/intake/consult/{cid}/confirm"""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core import api_body, db, deps, native_gate
from app.modules.intake import api_models, consult_admin
from app.modules.intake._native_support import (
    ApiError, gate, json_response, terminal_not_found_response,
)

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("intake"))])



async def _consult(request: Request, cid: str):
    # Shape checked BEFORE auth: `_CONSULT_RE` (digits-only) never reaches
    # `admin_auth.require_admin` for a shape it doesn't recognize, so an
    # unauthenticated request with a malformed `cid` still 404s, never 401s.
    if not cid.isdigit():
        return terminal_not_found_response()
    try:
        identity = await gate(request, permitir_medico=True)
    except ApiError as exc:
        return json_response(exc.payload, exc.status)
    try:
        payload = await run_in_threadpool(consult_admin.consult_payload, identity, int(cid))
    except consult_admin.ConsultAdminError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


async def _consult_id(cid: str) -> str:
    """Same ordering rationale as `_consult` above: the shape before the gate."""
    if not cid.isdigit():
        raise deps.EarlyResponse(terminal_not_found_response())
    return cid


def _lenient(raw):
    """A body that does not decode is read as `{}` here (the domain then asks
    for what is missing), never a 400."""
    try:
        return api_body.decode(raw)
    except (ValueError, UnicodeDecodeError):
        return {}


async def _confirm(
        cid: str = Depends(_consult_id),
        identity: dict = Depends(native_gate.staff(permitir_medico=True)),
        body: api_body.Body = Depends(api_body.lenient(api_models.BODY, api_models.ConsultConfirmIn, _lenient))):
    try:
        payload = await api_body.run(
            body, consult_admin.confirm_payload, identity, int(cid), body.value,
            expected=(consult_admin.ConsultAdminError,))
    except consult_admin.ConsultAdminError as exc:
        return json_response(exc.body, exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return json_response(payload)


router.add_api_route("/api/admin/intake/consult/{cid}", _consult, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.ConsultOut)) | deps.STAFF_GATE_DOCS)
router.add_api_route("/api/admin/intake/consult/{cid}/confirm", _confirm, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.ConsultConfirmIn,
                                                       api_models.ConsultConfirmOut)) | deps.STAFF_GATE_DOCS)
