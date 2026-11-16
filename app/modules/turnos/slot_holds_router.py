"""slot_holds_router.py — Native ASGI implementation of `slot_holds.py`'s two
paths — the soft-lock a patient's browser takes on a slot while filling out
the wizard (`POST /api/slot-hold`) and its explicit release (`DELETE
/api/slot-hold/<token>`, "Tier 1.4 integrity guards" territory: this is the
same race-safety surface `test_integrity_races.py` exercises, unrelated to
this migration).

Real `async def` endpoints: the request is parsed natively (path param, raw
body), the blocking domain call (`slot_holds.create_hold`/`release_hold`,
which may hit Postgres via `_ttl_for`/`try_take`/`db_slots.hold_release`) runs
through `starlette.concurrency.run_in_threadpool`, and the `Response` is
built with `core.gzip_bridge.body_for()` (Content-Type, Cache-Control,
Content-Length, transparent gzip).

`slot_holds` IS a `turnos`-owned route module, so
`Depends(deps.require_module_enabled("turnos"))` applies, same as its
siblings. The `/api/slot-hold/{token}` path template stays a PLAIN
single-segment converter (not `{token:path}`) — a multi-segment or
empty-segment DELETE never matches this route. The only "declined" shape left (a token that's
pure whitespace, e.g. a URL-decoded `%20`) isn't a 404 at all —
`_delete_slot_hold` answers it with its own 400 "falta el token" JSON, so
there's no terminal-404 case here."""

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core import api_body, deps
from app.modules.turnos import api_models, slot_holds
from app.core.http_json import gzip_json_response as _json_response

router = APIRouter(dependencies=[Depends(deps.require_module_enabled("turnos"))])


async def _create_slot_hold(request: Request,
        body: api_body.RawRead = Depends(api_body.raw(api_models.WITH_REASON, api_models.SlotHoldIn))):

    def _call():
        from app.ai import abuse_guard
        client = request.client
        ip = abuse_guard.client_ip(
            request.headers, client.host if client else "")
        seen = {}

        def user():
            if "u" not in seen:
                seen["u"] = slot_holds._session_user(request)
            return seen["u"]

        return slot_holds.create_hold(
            ip, body.raw,
            lambda: slot_holds._ttl_for(user()),
            owner_for=user)

    status, payload = await api_body.run(body, _call)
    return _json_response(request, payload, status=status)


async def _delete_slot_hold(token: str, request: Request):
    token = token.strip()
    if not token:
        return _json_response(request, {"error": "falta el token"}, status=400)
    status, payload = await run_in_threadpool(
        lambda: slot_holds.release_hold(token, slot_holds._session_user(request)))
    return _json_response(request, payload, status=status)


router.add_api_route("/api/slot-hold", _create_slot_hold, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.SlotHoldIn, api_models.SlotHoldOut)))
router.add_api_route("/api/slot-hold/{token}", _delete_slot_hold, methods=["DELETE"],
                     openapi_extra=api_body.docs(delete=(None, api_body.OkOut)))
