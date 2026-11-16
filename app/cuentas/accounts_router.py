"""Native Accounts routes — native-route migration campaign F6
(docs/roadmap/native-migration-campaign-20260826.md).

Every endpoint below is a genuine `async def` FastAPI operation: it parses
the request with FastAPI/Starlette primitives itself, calls one of
`accounts.py`'s extracted pure `_*_core` domain functions (sync DB work
through `run_in_threadpool`), and builds the `Response` from the
`cuentas.native_result.Result` that function returns. Native request-aware domain helpers
receive the real Starlette ``Request``; the router creates no request clone.

Accounts is platform-core rather than a Turnos module, so these paths have
no module-enable gate.
"""
import json

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth import admin_auth, turnstile
from app.cuentas import account_deps, accounts, api_models
from app.cuentas.native_result import Result
from app.core import api_body, deps, i18n
from app.core.proxy_trust import client_host as _client_host
from app.core.terminal_response import redirect_response as _redirect_response

router = APIRouter()

_TEST_AUTH_UNAVAILABLE = {"error": "test_auth_no_disponible"}


def _html_response(body, set_cookie=None):
    headers = {
        "Cache-Control": "no-store",
        "Content-Length": str(len(body)),
    }
    if set_cookie:
        headers["Set-Cookie"] = set_cookie
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers=headers,
    )


def _json_404_test_auth():
    body = json.dumps(_TEST_AUTH_UNAVAILABLE).encode("utf-8")
    return Response(
        content=body,
        status_code=404,
        media_type="application/json; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(len(body)),
        },
    )


def lang_switch_response(request: Request):
    """Build the language-switch response for a real native request."""
    switch = i18n.lang_switch_redirect(request.url.query, request.url.path)
    if switch is None:
        return None
    location, cookie = switch
    return Response(
        content=b"",
        status_code=302,
        headers={
            "Location": location,
            "Set-Cookie": cookie,
            "Content-Length": "0",
        },
    )


def render_account(request: Request, bootstrap_user=None, set_cookie=None,
                   canonical_path=None):
    if bootstrap_user is None:
        current_user = accounts.current_user(request)
        destino = accounts.auth_redirect_target(current_user, "/account")
        if destino:
            return _redirect_response(destino)
    body = accounts.render_account_body(
        request, bootstrap_user=bootstrap_user, canonical_path=canonical_path)
    return _html_response(body, set_cookie=set_cookie)


def render_reset(request: Request):
    if accounts.portal_entry_closed() is not None:
        # No password to reset where patients come in through the portal.
        return _redirect_response("/account")
    return _html_response(accounts.render_reset_body(request))


def render_test_auth(request: Request):
    if not accounts._test_auth_enabled(request.headers, _client_host(request)):
        return _json_404_test_auth()
    return _html_response(accounts.render_test_auth_body(request))


def render_sso(request: Request):
    if not accounts._test_auth_enabled(request.headers, _client_host(request)):
        return _json_404_test_auth()
    return _html_response(accounts.render_sso_body(request))


def _make_html_endpoint(render_fn):
    async def _html_endpoint(request: Request):
        switch = lang_switch_response(request)
        if switch is not None:
            return switch
        # render_fn can hit the DB (account_bootstrap -> booking_requests
        # .patient_rows) — never block the event loop for it.
        return await run_in_threadpool(render_fn, request)
    return _html_endpoint


# Route registration order is a tested invariant (test_app_composition.py's
# fixture snapshot compares registration order, not just membership) — the
# HTML and JSON routes are therefore registered interleaved, at the bottom
# of this file, in the SAME order as the pre-migration router.


# ---- JSON API: shared helpers ------------------------------------------------

def _login_guarded(fn, *args, **kwargs):
    """Custom guard for login endpoint: returns patient-facing translated error."""
    try:
        return fn(*args, **kwargs)
    except accounts.store.DbUnavailable:
        msg = i18n.t_or("account.form.error.database_unavailable",
                        "Base de datos no disponible.", i18n.active())
        return deps.answer_db_unavailable(lambda status: Result.json({"error": msg}, status=status))


# ---- JSON API: no body ------------------------------------------------------

async def _config_endpoint(request: Request):
    result = await run_in_threadpool(
        accounts._config_core, request.headers, _client_host(request), request)
    return result.to_response()


async def _portal_entry_endpoint(request: Request, nxt: str | None = Query(
        None, alias="next", description="The page to come back to after the portal's sign-in; only a path "
                                        "of this site is followed")):
    # Off the event loop: the gateway check may resolve the gateway's DNS name.
    location = await run_in_threadpool(accounts.portal_entry_location, request, nxt)
    return Response(content=b"", status_code=303, headers={
        "Location": location, "Cache-Control": "no-store", "Content-Length": "0"})


async def _portal_only():
    """Close a native patient entry where patients come in only through the
    portal (accounts.portal_entry_closed) — before the body is even read: a
    route dependency, so it runs ahead of the body in the signature."""
    closed = accounts.portal_entry_closed()
    if closed is not None:
        raise deps.EarlyResponse(closed.to_response())


_PORTAL_ONLY = [Depends(_portal_only)]


async def _me_endpoint(request: Request):
    result = await run_in_threadpool(accounts._guarded, accounts._me_core, request)
    return result.to_response()


async def _dependents_endpoint(
        request: Request, usuario: dict = Depends(account_deps.current_account),
        body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.DependentIn, methods={"POST"}))):
    if request.method == "GET":
        result = await run_in_threadpool(accounts._guarded, accounts._dependents_core, usuario)
        return result.to_response()
    # POST — add a dependent.
    result = await api_body.run(
        body, accounts._guarded, accounts._add_dependent_core, usuario, body.value)
    return result.to_response()


async def _logout_endpoint(request: Request):
    result = await run_in_threadpool(accounts._logout_core, request)
    response = result.to_response()
    # «Cerrar sesión» ends every session of this browser: a staff member who signed in through
    # the patient login screen holds the panel's cookie too (TASK-55).
    if admin_auth._parse_cookie_token(request.headers.get("cookie")) is not None:
        staff = await run_in_threadpool(admin_auth.logout_result, request.headers)
        response.headers.append("Set-Cookie", staff.set_cookie)
    return response


async def _random_test_identity_endpoint(request: Request):
    result = await run_in_threadpool(
        accounts._random_test_identity_core, request.headers, _client_host(request))
    return result.to_response()


# ---- JSON API: needs the parsed body ----------------------------------------

async def _register_endpoint(
        request: Request, body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.RegisterIn))):
    ip = turnstile.client_ip_from(request.headers, _client_host(request))
    result = await api_body.run(body, accounts._guarded, accounts._register_core, body.value, ip)
    return result.to_response()


async def _login_endpoint(
        request: Request, body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.LoginIn))):
    client_host = _client_host(request)
    ip = turnstile.client_ip_from(request.headers, client_host)
    result = await api_body.run(
        body, _login_guarded, accounts._login_core,
        request.headers, client_host, body.value, ip)
    return result.to_response()


async def _update_endpoint(
        usuario: dict = Depends(account_deps.current_account),
        body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.UpdateIn))):
    result = await api_body.run(
        body, accounts._guarded, accounts._update_core, usuario, body.value)
    return result.to_response()


async def _delete_dependent_endpoint(
        usuario: dict = Depends(account_deps.current_account),
        body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.DependentRefIn))):
    result = await api_body.run(
        body, accounts._guarded, accounts._delete_dependent_core, usuario, body.value)
    return result.to_response()


async def _test_identities_endpoint(
        request: Request,
        body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.TestIdentityIn, methods={"POST"}))):
    if request.method == "GET":
        result = await run_in_threadpool(
            accounts._test_identities_core, request.headers, _client_host(request))
        return result.to_response()
    # POST — save/edit a synthetic identity.
    result = await api_body.run(
        body, accounts._save_test_identity_core,
        request.headers, _client_host(request), body.value)
    return result.to_response()


async def _test_login_endpoint(
        request: Request, body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.TestLoginIn))):
    client_host = _client_host(request)
    ip = turnstile.client_ip_from(request.headers, client_host)
    result = await api_body.run(
        body, accounts._login_test_identity_core,
        request.headers, client_host, body.value, ip)
    return result.to_response()


async def _recover_endpoint(body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.RecoverIn))):
    result = await api_body.run(body, accounts._guarded, accounts._recover_core, body.value)
    return result.to_response()


async def _reset_endpoint(body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.ResetIn))):
    result = await api_body.run(body, accounts._guarded, accounts._reset_core, body.value)
    return result.to_response()


async def _reset_otp_endpoint(
        request: Request, body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.ResetOtpIn))):
    ip = turnstile.client_ip_from(request.headers, _client_host(request))
    result = await api_body.run(body, accounts._guarded, accounts._reset_otp_core, body.value, ip)
    return result.to_response()


async def _otp_send_endpoint(body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.OtpSendIn))):
    result = await api_body.run(body, accounts._guarded, accounts._otp_send_core, body.value)
    return result.to_response()


async def _otp_verify_endpoint(body: api_body.Body = Depends(api_body.body(api_models.BODY, api_models.OtpVerifyIn))):
    result = await api_body.run(body, accounts._guarded, accounts._otp_verify_core, body.value)
    return result.to_response()


# ---- registration: SAME order as the pre-migration router (tested by
# test_app_composition.py's fixture snapshot) --------------------------------

router.add_api_route(
    "/account", _make_html_endpoint(render_account), methods=["GET"])
router.add_api_route("/api/account/config", _config_endpoint, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.AccountConfigOut)))
router.add_api_route("/api/account/me", _me_endpoint, methods=["GET"],
                     openapi_extra=api_body.docs(get=(None, api_models.MeOut)) | deps.PATIENT_SESSION_DOCS)
router.add_api_route(
    "/api/account/dependents", _dependents_endpoint, methods=["GET", "POST"],
    openapi_extra=api_body.docs(get=(None, api_models.DependentsOut),
                                post=(api_models.DependentIn, api_models.DependentOut)))
router.add_api_route("/api/account/register", _register_endpoint, methods=["POST"], dependencies=_PORTAL_ONLY,
                     openapi_extra=api_body.docs(post=(api_models.RegisterIn, api_models.AccountOut)))
router.add_api_route("/api/account/login", _login_endpoint, methods=["POST"], dependencies=_PORTAL_ONLY,
                     openapi_extra=api_body.docs(post=(api_models.LoginIn, api_models.AccountOut)))
router.add_api_route("/api/account/logout", _logout_endpoint, methods=["POST"],
                     openapi_extra=api_body.docs(post=(None, api_models.OkOut)))
router.add_api_route("/api/account/update", _update_endpoint, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.UpdateIn, api_models.AccountOut)))
router.add_api_route(
    "/api/account/dependents/delete", _delete_dependent_endpoint, methods=["POST"],
    openapi_extra=api_body.docs(post=(api_models.DependentRefIn, api_models.OkOut)))

router.add_api_route(
    "/account/reset", _make_html_endpoint(render_reset), methods=["GET"])
router.add_api_route(
    "/account/portal-login", _portal_entry_endpoint, methods=["GET"])
router.add_api_route(
    "/auth/test", _make_html_endpoint(render_test_auth), methods=["GET"])
router.add_api_route(
    "/auth/sso", _make_html_endpoint(render_sso), methods=["GET"])
router.add_api_route(
    "/api/account/test-identities", _test_identities_endpoint, methods=["GET", "POST"],
    openapi_extra=api_body.docs(get=(None, api_models.TestIdentitiesOut),
                                post=(api_models.TestIdentityIn, api_models.TestIdentityOut)))
router.add_api_route(
    "/api/account/test-identities/random", _random_test_identity_endpoint,
    methods=["POST"], openapi_extra=api_body.docs(post=(None, api_models.TestIdentityOut, 201)))
router.add_api_route("/api/account/test-login", _test_login_endpoint, methods=["POST"], dependencies=_PORTAL_ONLY,
                     openapi_extra=api_body.docs(post=(api_models.TestLoginIn, api_models.AccountOut)))
router.add_api_route("/api/account/recover", _recover_endpoint, methods=["POST"], dependencies=_PORTAL_ONLY,
                     openapi_extra=api_body.docs(post=(api_models.RecoverIn, api_models.OkOut)))
router.add_api_route("/api/account/reset", _reset_endpoint, methods=["POST"], dependencies=_PORTAL_ONLY,
                     openapi_extra=api_body.docs(post=(api_models.ResetIn, api_models.AccountOut)))
router.add_api_route("/api/account/reset_otp", _reset_otp_endpoint, methods=["POST"], dependencies=_PORTAL_ONLY,
                     openapi_extra=api_body.docs(post=(api_models.ResetOtpIn, api_models.AccountOut)))
router.add_api_route("/api/account/otp/send", _otp_send_endpoint, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.OtpSendIn, api_models.OkOut)))
router.add_api_route("/api/account/otp/verify", _otp_verify_endpoint, methods=["POST"],
                     openapi_extra=api_body.docs(post=(api_models.OtpVerifyIn, api_models.OkOut)))
