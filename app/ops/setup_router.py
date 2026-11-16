"""Native ASGI transport for the first-run installer wizard.

Real `async def` endpoints. Every endpoint calls `ops.setup`'s canonical
page/process functions; no legacy dispatcher or handler write protocol
exists in the call graph.

``setup._state()``/``setup._is_secure()``/``setup._propose_country()`` read
the native ``Request`` directly. POST bodies are normalized through the
canonical ``setup._parse_form_multi()`` helper.

Fresh-install gate: this router owns EXACTLY `/setup` and `/setup/<sub>` —
`setup.is_fresh_install()` runs first and, when the install is already
configured, this route returns the EXACT SAME 404 body/headers
the setup contract requires (never the rich native-terminal error page — a
configured installation's `/setup` is gone, not "moved"). One dynamic
`/setup/{sub:path}` route (plus a plain `/setup` route for the welcome step)
keeps the step dispatch explicit, so an unrecognized sub-step also gets that
same 404 instead of the terminal's page."""

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from app.ops import setup

router = APIRouter()

_NOT_FOUND_BODY = b"<!doctype html><title>404</title><p>No encontrado.</p>"


def _not_found_response():
    """Return the setup contract's minimal 404 response."""
    return Response(
        content=_NOT_FOUND_BODY,
        status_code=404,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(_NOT_FOUND_BODY)),
        },
    )


def _html_response(body, *, status=200, set_cookie=None):
    data = body.encode("utf-8")
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(data)),
        "Cache-Control": "no-store",
    }
    if set_cookie:
        headers["Set-Cookie"] = set_cookie
    return Response(content=data, status_code=status, headers=headers)


def _redirect_response(location, *, set_cookie=None):
    headers = {"Location": location, "Content-Length": "0", "Cache-Control": "no-store"}
    if set_cookie:
        headers["Set-Cookie"] = set_cookie
    return Response(status_code=303, headers=headers)


async def _form_first_values(request: Request):
    """Decode a form as a dict of single (first) values."""
    raw = await request.body()
    parsed = setup._parse_form_multi(raw)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


async def _form_multi_values(request: Request):
    """Same shape `setup._read_form_multi()` returns: dict of LISTS."""
    raw = await request.body()
    return setup._parse_form_multi(raw)


# ---- GET steps --------------------------------------------------------------

async def _get_welcome(request: Request):
    return _html_response(setup.page_welcome())


async def _get_location(request: Request):
    state = await run_in_threadpool(setup._state, request)
    proposed = await run_in_threadpool(setup._propose_country, request)
    values, note = setup._location_values_and_note(state, proposed)
    return _html_response(setup.page_location(values, proposed_note=note))


async def _get_packs(request: Request):
    state = await run_in_threadpool(setup._state, request)
    return _html_response(setup.page_packs(state))


async def _get_language(request: Request):
    state = await run_in_threadpool(setup._state, request)
    return _html_response(setup.page_language(state))


async def _get_admin(request: Request):
    return _html_response(setup.page_admin())


async def _get_apikeys(request: Request):
    return _html_response(setup.page_apikeys())


async def _get_backups(request: Request):
    from app.core import backup_setup
    state = await run_in_threadpool(setup._state, request)
    if not backup_setup.public(state.get("backup_stage")):
        state["backup_stage"] = backup_setup.begin()
    secure = await run_in_threadpool(setup._is_secure, request)
    return _html_response(setup.page_backups(state), set_cookie=setup._state_cookie(state, secure))


_GET_STEPS = {
    "": _get_welcome,
    "location": _get_location,
    "packs": _get_packs,
    "language": _get_language,
    "admin": _get_admin,
    "apikeys": _get_apikeys,
    "backups": _get_backups,
}


# ---- POST steps ---------------------------------------------------------

async def _post_location(request: Request):
    form = await _form_first_values(request)
    result = await run_in_threadpool(setup.process_location, form)
    if result[0] == "error":
        _, message, values = result
        return _html_response(setup.page_location(values, error=message))
    _, updates = result
    state = await run_in_threadpool(setup._state, request)
    state.update(updates)
    secure = await run_in_threadpool(setup._is_secure, request)
    return _redirect_response("/setup/packs", set_cookie=setup._state_cookie(state, secure))


async def _post_packs(request: Request):
    form = await _form_first_values(request)
    state = await run_in_threadpool(setup._state, request)
    result = await run_in_threadpool(setup.process_packs, form, state)
    if result[0] == "error":
        return _html_response(setup.page_packs(state, error=result[1]))
    state.update(result[1])
    secure = await run_in_threadpool(setup._is_secure, request)
    return _redirect_response("/setup/language", set_cookie=setup._state_cookie(state, secure))


async def _post_language(request: Request):
    parsed = await _form_multi_values(request)
    result = await run_in_threadpool(setup.process_language, parsed)
    if result[0] == "error":
        state = await run_in_threadpool(setup._state, request)
        return _html_response(setup.page_language(state, error=result[1]))
    _, updates = result
    state = await run_in_threadpool(setup._state, request)
    state.update(updates)
    secure = await run_in_threadpool(setup._is_secure, request)
    return _redirect_response("/setup/admin", set_cookie=setup._state_cookie(state, secure))


async def _post_admin(request: Request):
    form = await _form_first_values(request)
    result = await run_in_threadpool(setup.process_admin, form)
    if result[0] == "error":
        _, message, values = result
        return _html_response(setup.page_admin(values=values, error=message))
    _, updates = result
    state = await run_in_threadpool(setup._state, request)
    if "admin_totp_secret" not in updates:
        state.pop("admin_totp_secret", None)
    state.update(updates)
    secure = await run_in_threadpool(setup._is_secure, request)
    return _redirect_response("/setup/backups", set_cookie=setup._state_cookie(state, secure))


async def _post_backups(request: Request):
    from app.core import backup_lock, backup_setup, i18n
    form = await _form_first_values(request)
    state = await run_in_threadpool(setup._state, request)
    try:
        token, result = await run_in_threadpool(backup_setup.update, state.get("backup_stage"), form)
    except backup_lock.BackupWriteForbidden:
        return _html_response(setup.page_backups(state, error=i18n.t_or(
            "backup.forbidden", backup_lock.FORBIDDEN_ES, i18n.active())))
    except Exception:
        return _html_response(setup.page_backups(state, error=i18n.t_or(
            "backup.step_error", "Revisá el destino y la clave guardada. Si la conexión falla, comprobá las credenciales.", i18n.active())))
    state["backup_stage"] = token
    secure = await run_in_threadpool(setup._is_secure, request)
    cookie = setup._state_cookie(state, secure)
    if result.get("continue"):
        return _redirect_response("/setup/apikeys", set_cookie=cookie)
    return _html_response(setup.page_backups(state, reveal=result if "recovery_key" in result else None,
                                           probed=result.get("probed", False)), set_cookie=cookie)


async def _post_apikeys(request: Request):
    form = await _form_first_values(request)
    state = await run_in_threadpool(setup._state, request)
    result = await run_in_threadpool(setup.process_apikeys, form, state)
    if result[0] == "error":
        return _html_response(setup.page_apikeys(error=result[1]))
    _, username, totp_required = result
    secure = await run_in_threadpool(setup._is_secure, request)
    return _html_response(
        setup.page_done(username, totp_required),
        set_cookie=setup._clear_cookie(secure),
    )


_POST_STEPS = {
    "location": _post_location,
    "packs": _post_packs,
    "language": _post_language,
    "admin": _post_admin,
    "apikeys": _post_apikeys,
    "backups": _post_backups,
}


# ---- dispatch -------------------------------------------------------------

async def _dispatch(sub: str, request: Request):
    # Normalize trailing slashes so every setup step has one behavior.
    sub = sub.rstrip("/")
    fresh = await run_in_threadpool(setup.is_fresh_install)
    if not fresh:
        return _not_found_response()
    # Only GET/POST are setup operations; any other method is the same 404
    # as an unrecognized sub-step.
    if request.method not in ("GET", "POST"):
        return _not_found_response()
    table = _GET_STEPS if request.method == "GET" else _POST_STEPS
    fn = table.get(sub)
    if fn is None:
        return _not_found_response()
    return await fn(request)


@router.api_route("/setup", methods=["GET", "POST", "PUT", "DELETE"])
async def setup_root(request: Request):
    return await _dispatch("", request)


@router.api_route("/setup/{sub:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def setup_step(sub: str, request: Request):
    return await _dispatch(sub, request)
