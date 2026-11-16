"""account_deps.py — the signed-in patient as a FastAPI dependency.

A route that needs the patient's account declares it in its signature instead of
reading the session by hand:

    async def _update_endpoint(request: Request, usuario: dict = Depends(account_deps.current_account)):

  - `current_account` — the account, or the 401 `{"error": "no_autenticado"}` the patient account
    routes have always answered without one (raised as `core.deps.PatientNotSignedIn`, rendered by
    the handler `deps.install_gate_handlers` registers with the staff gates' own);
  - `optional_account` — the account or None, for a route an anonymous visitor may use too, or one that
    answers the anonymous visitor in its own way (the lab results' `{"error": "auth_requerida"}`).

Both read the session exactly as before: `accounts.current_user(request)`, off the event loop. It never
raises (an unreachable store or a bad token is «no account»), so a dependency answers what the hand-written
read answered. A dependency runs before the route body, after the router's own gates (the module gate), so
only a route that read the session FIRST moved here. Where patients sign in through the portal, reading the
session can create or bind their account, so it must not move ahead of an answer that came before it: a
route that reads it after another step (the language switch of a page, the body-size check of a chat turn)
keeps its own read, and so does `/api/account/me`, which answers 503 on a database outage where these
answer 401. Neither is for a route with typed parameters or a declared body: FastAPI validates those after
the dependencies, so the session would be read before their 422 (and `current_account` would replace it).

`PATIENT_SESSION` is the `st_ses` cookie as an OpenAPI security scheme (`patientSession`): it puts the
requirement on the documented operations and decides nothing. On an installation with the patient portal
login the account comes from the portal's gateway, not from the cookie (`current_user`).
"""
from fastapi import Depends, Request
from fastapi.security import APIKeyCookie
from starlette.concurrency import run_in_threadpool

from app.core import deps
from app.cuentas import accounts

PATIENT_SESSION = APIKeyCookie(
    name=accounts.COOKIE_NAME,
    scheme_name="patientSession",
    description="The patient's session cookie, set by `POST /api/account/login`. Where patients sign in "
                "through the municipal portal the identity comes from the portal's gateway instead.",
    auto_error=False,
)


async def optional_account(request: Request, _session: str | None = Depends(PATIENT_SESSION)):
    """The signed-in patient's account, or None."""
    return await run_in_threadpool(accounts.current_user, request)


async def current_account(usuario: dict | None = Depends(optional_account)):
    """The signed-in patient's account; without one the route answers 401 before its body runs."""
    if not usuario:
        raise deps.PatientNotSignedIn()
    return usuario
