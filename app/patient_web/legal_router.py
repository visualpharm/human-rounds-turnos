"""legal_router.py — Tier 4 slice 8 closing batch (docs/roadmap/
tier4-replatform-design.md's own honest inventory): the FastAPI `router` for
`legal.py` — the editable terms/privacy admin section (`#legal`).

Platform-core (`legal` is in `PLATFORM_ROUTES` — "los términos y la
política de privacidad son del sitio, no del módulo de turnos"): mounted
directly in `app.py`, unconditionally, same as `admin_users_router.py`.

Family W2c of docs/roadmap/native-migration-campaign-20260826.md: real
`async def` endpoints. Each endpoint calls the pure domain functions
`legal.py` extracted (`list_payload`, `item_payload`, `save_payload`,
`delete_payload`). Auth is
`Depends(core.deps.require_admin(solo_admin=True))`.

  GET             /api/admin/legal
  GET/PUT/DELETE  /api/admin/legal/{doc}

`{doc}` is validated against `legal._ORIGINALES` ONCE, upfront, for every
method (an unknown doc 404s before
a PUT's body is even parsed): an unrecognized value gets the exact
`{"error": "no existe ese documento"}` 404 body `legal.py` writes for
this case (a domain-level 404, NOT the terminal shape — there is no
FastAPI-matched-but-declined case in this router, `{doc}` is a
plain single literal segment)."""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core import db
from app.core import api_body, deps
from app.patient_web import api_models, legal
from app.core.http_json import json_response as _json_response

router = APIRouter()

_ADMIN_SOLO = Depends(deps.require_admin(solo_admin=True))


_NOT_FOUND = {"error": "no existe ese documento"}


def _require_known_doc(doc):
    if doc not in legal._ORIGINALES:
        return _json_response(_NOT_FOUND, status=404)
    return None


@router.get("/api/admin/legal", openapi_extra=api_body.docs(get=(None, api_models.LegalListOut)))
async def list_legal_docs(identity: dict = _ADMIN_SOLO):
    return _json_response(await run_in_threadpool(legal.list_payload))


async def _known_doc(doc: str):
    error = _require_known_doc(doc)
    if error is not None:
        raise deps.EarlyResponse(error)
    return doc


async def _legal_doc(
        request: Request, identity: dict = _ADMIN_SOLO, doc: str = Depends(_known_doc),
        body: api_body.Body = Depends(api_body.body(api_models.LEGAL, api_models.LegalTextIn, methods={"PUT"}))):
    """GET/PUT/DELETE /api/admin/legal/{doc} — ONE route object for all
    three methods (`route_objects` counts Starlette routes, not method
    rows: dispatching on `request.method` inside a single endpoint keeps
    this router's contribution unchanged, same pattern as `cuentas/
    account_photo_router.py`'s `_photo_endpoint`). `{doc}` is validated
    against `legal._ORIGINALES` ONCE, upfront, for every method (an unknown
    doc 404s before a PUT's body is even parsed)."""
    # `_known_doc` comes ahead of the body in the signature: the 404 before the body.
    if request.method == "GET":
        return _json_response(await run_in_threadpool(legal.item_payload, doc))

    if request.method == "DELETE":
        try:
            result = await run_in_threadpool(legal.delete_payload, doc)
        except legal.LegalError as exc:
            return _json_response(exc.body, status=exc.status)
        except db.DbUnavailable:
            return deps.answer_db_unavailable()
        return _json_response(result)

    # PUT — the only other method registered on this route.

    def _save():
        cuerpo = str((body.value or {}).get("cuerpo") or "")
        return legal.save_payload(doc, cuerpo, (identity or {}).get("usuario"))

    try:
        result = await api_body.run(body, _save, expected=(legal.LegalError,))
    except legal.LegalError as exc:
        return _json_response(exc.body, status=exc.status)
    except db.DbUnavailable:
        return deps.answer_db_unavailable()
    return _json_response(result)


router.add_api_route("/api/admin/legal/{doc}", _legal_doc, methods=["GET", "PUT", "DELETE"],
                     openapi_extra=api_body.docs(get=(None, api_models.LegalDocOut),
                                                 put=(api_models.LegalTextIn, api_models.LegalTextOut),
                                                 delete=(None, api_models.LegalTextOut)))
