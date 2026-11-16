"""account_photo_router.py — native FastAPI twin of `account_photo.py` — the
patient's own profile photo (upload, Gravatar fallback). Native-route
migration campaign F6 (docs/roadmap/native-migration-campaign-20260826.md):
a genuine `async def` endpoint that parses the request itself, calls
`account_photo.py`'s extracted `_*_core` domain functions (sync DB/disk work
through `run_in_threadpool`), and builds the `Response` from the returned
`cuentas.native_result.Result`. This router is the only HTTP path to
these `_*_core` functions.

`account_photo` IS `turnos`-owned, so `Depends(deps.require_module_enabled(
"turnos"))` gates the router, same as before.

  GET/POST/DELETE /api/account/photo   (GET also answers ?probe=1)

Identity is always the session's owner (`account_photo._resolve_identity` ->
`accounts.current_user`, cookie header only) — no session cookie
401s."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.cuentas import account_photo, api_models
from app.core import api_body, deps

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


async def _photo_identity(request: Request):
    """The session owner's `(dni, email)`, or its 401 before anything else is read."""
    dni, email, error = await run_in_threadpool(account_photo._resolve_identity, request)
    if error is not None:
        raise deps.EarlyResponse(error.to_response())
    return dni, email


async def _photo_endpoint(
        request: Request, identity: tuple = Depends(_photo_identity),
        body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.PhotoIn, methods={"POST"})),
        # One route object serves the three methods: `query_docs` below documents `probe` on GET alone.
        probe: str | None = Query(None, include_in_schema=False)):
    dni, email = identity
    if request.method == "GET":
        if probe:
            result = await run_in_threadpool(account_photo._probe_core, dni, email)
        else:
            result = await run_in_threadpool(account_photo._serve_core, dni, email)
        return result.to_response()

    if request.method == "POST":
        result = await api_body.run(body, account_photo._upload_core, dni, body.value)
        return result.to_response()

    # DELETE — the only other method registered on this route.
    result = await run_in_threadpool(account_photo._remove_core, dni)
    return result.to_response()


router.add_api_route(
    "/api/account/photo", _photo_endpoint, methods=["GET", "POST", "DELETE"],
    openapi_extra=api_body.docs(post=(api_models.PhotoIn, api_models.OkOut),
                                delete=(None, api_models.OkOut)) | api_body.query_docs(get={
        "probe": "Any value: where the avatar comes from (`upload`, `gravatar`, `none`) as JSON, instead of "
                 "the image"}) | deps.PATIENT_SESSION_DOCS)
