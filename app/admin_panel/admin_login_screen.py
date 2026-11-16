"""Server-routed platform-core Admin login screen.

This is the direct ``/admin/login`` twin of the legacy ``/admin`` hash-routed
gate (username, password, TOTP, Turnstile, passkeys, and the side panel). It
renders 200 without authentication and reuses the existing login gate markup,
styles, and controller. After a successful password or passkey login, the
controller honors a validated explicit ``data-next`` first and otherwise
navigates to the role-aware direct landing read from the identity the login
API returned (``admin``/``admisionista`` → ``/admin/solicitudes``, ``medico``
→ ``/admin/cola``, ``reportes`` → ``/admin/reportes``; a retired or unknown
role fails safe on the gate and navigates nowhere) — never through the legacy
``/admin`` hash shell.

The screen owns only the unauthenticated shell and the login form; the actual
authentication POST stays behind the existing ``/api/admin/login`` API
(``auth.admin_auth_router``), fetched client-side. An explicit visit starts a fresh sign-in and clears any previous identity,
including stale and demo cookies; this remains a public entry point.

When a CONFIGURED database is unreachable, the screen renders the outage
card instead of the form (the product owner, 2026-09-15: "if the database is not
available, why put the input fields for the user?"). Typing credentials into
an outage can only end in "usuario o contraseña incorrectos" — an active lie
about perfectly good credentials. Without DATABASE_URL configured (local
shells, DB-less unit tests) the form still renders: there is nothing whose
unavailability to report, and the env-owner emergency login remains the
documented path.

Direct state: ``/admin/login`` or ``/admin/login?next=<target>`` where
``<target>`` must be a same-origin admin path (validated server-side before
render). Protocol-relative, encoded/double-encoded, backslash, control-
character, traversal, userinfo, scheme, and non-admin targets are rejected.
Only ``/admin`` and direct ``/admin/<screen>`` paths are accepted. A bounded
query string may carry a direct screen's own state (for example ``?doc=`` or
``?interview=``); fragments are rejected because they never reach the server.
Legacy hash routes like ``#/admin#section`` are not preserved (the contract is
forward-only: new installs get direct URLs, existing ``/admin`` hash navigation
continues to work).
"""
from html import escape
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from app.admin_panel import admin_navigation
from app.core import i18n
from app.core import jinja_env
from app.core import theme
from app.core import static_fingerprint

_TEMPLATE = "admin/login.html"

router = APIRouter()

def _is_valid_admin_next(path: str) -> bool:
    """Validate that a redirect target is a same-origin admin path.

    Args:
        path: The redirect path from ?next=

    Returns:
        True if the path is a valid admin target, False otherwise.
    """
    return admin_navigation.canonical_admin_target(path) is not None


def _db_is_down() -> bool:
    """True sólo cuando hay una base CONFIGURADA y no responde (SELECT 1).

    Sin DATABASE_URL devuelve False: no hay nada que reportar y el formulario
    del gate sigue siendo el camino (login del dueño por env incluido). Es la
    misma sonda que /ready (system_status.probe_db), reducida a la parte de
    conectividad — el gate no debe opinar sobre migraciones.
    """
    from app.core import config
    if not (config.get("DATABASE_URL") or "").strip():
        return False
    try:
        from app.admin_panel import system_status
        conectado, _tabla, _al_dia, _detalle = system_status.probe_db()
        return not conectado
    except Exception:  # noqa: BLE001 — la sonda nunca rompe el render
        return False


def _retry_target(safe_next: str) -> str:
    # urlencode-parity with the gate's 303 redirect (%2F slashes), so the
    # retry target is byte-identical to what ?next= validation re-parses.
    return "/admin/login?next=" + urlquote(safe_next or "/admin", safe="")


def _card_html(title: str, paragraphs, button=None) -> bytes:
    """Una tarjeta del gate sin formulario: mismo look que el gate. Los
    `paragraphs` llegan ya escapados (HTML); título y `button` — (texto,
    href) o None — se escapan acá."""
    brand = escape(theme.brand_name())
    lines = "\n".join(f"  <p>{p}</p>" for p in paragraphs)
    btn = ""
    if button:
        btn = (f'\n  <div class="gate-btns"><a class="btn btn-ok" '
               f'href="{escape(button[1])}">{escape(button[0])}</a></div>')
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acceso administrativo · {brand}</title>
<meta name="robots" content="noindex,nofollow">
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<link rel="stylesheet" href="{static_fingerprint.url('admin-gate.css')}">
</head><body>
<div class="gate-wrap">
 <div class="gate">
  <div class="gate-brand">
    <span class="adm-brand">Admin de <b>Turnos</b></span>
  </div>
  <h2>{escape(title)}</h2>
{lines}{btn}
 </div>
</div>
</body></html>""".encode()


def _outage_html(safe_next: str) -> bytes:
    """Tarjeta de corte del gate: mismo look que el gate, cero formulario. Si la base
    responde pero el arranque la rechazó (una revisión de Alembic que este código no
    conoce, `db.schema_refusal()`), la tarjeta lo dice y copia el mensaje entero: es lo
    que quien instala necesita para resolverlo, y el panel no abre sin base."""
    refusal = None
    try:
        from app.core import db
        refusal = db.schema_refusal()
    except Exception:  # noqa: BLE001 — la tarjeta nunca rompe el render
        refusal = None
    if refusal:
        return _card_html(
            "El sistema no está disponible",
            ["La base de datos fue actualizada por una versión más nueva del sistema y esta "
             "no puede usarla. No es un problema de tu usuario ni de tu contraseña.",
             "Avisale a quien instala el sistema: " + escape(refusal)],
            ("Reintentar", _retry_target(safe_next)))
    return _card_html(
        "El sistema no está disponible",
        ["La base de datos no responde. No es un problema de tu usuario ni de tu contraseña.",
         "Volvé a intentar en unos minutos. Si sigue igual, avisale a administración."],
        ("Reintentar", _retry_target(safe_next)))


_VIA_PORTAL = "portal"


def _portal_trip(safe_next: str) -> str:
    """The portal's sign-in that comes back here (`via=portal`) and then to
    `safe_next`; '' when no sign-in URL or public origin is configured."""
    from app.cuentas import portal_identity
    from urllib.parse import quote
    return portal_identity.login_url_back_to(
        "/admin/login?next=" + quote(safe_next, safe="/") + "&via=" + _VIA_PORTAL)


def _portal_card(resolution, safe_next: str, lang: str) -> bytes:
    """What a portal-login installation shows instead of the password form."""
    from app.auth import staff_portal
    if resolution.reason == "not_registered":
        paragraphs = [escape(i18n.t_or(
            "admin.shell.gate.portal_not_registered",
            "Pedile al administrador que te dé de alta con este correo.", lang))]
        if resolution.email:
            paragraphs.append(f"<b>{escape(resolution.email)}</b>")
        return _card_html(i18n.t_or("admin.shell.gate.portal_no_access_title",
                                    "Tu cuenta no tiene acceso al panel", lang), paragraphs)
    if resolution.reason == "unavailable":
        return _card_html(
            i18n.t_or("admin.shell.gate.portal_unavailable_title",
                      "El acceso no está disponible", lang),
            [escape(i18n.t_or("admin.shell.gate.portal_unavailable",
                              "Volvé a intentar en unos minutos. Si sigue igual, avisale a administración.",
                              lang))],
            (i18n.t_or("admin.shell.gate.retry", "Reintentar", lang), _retry_target(safe_next)))
    url = _portal_trip(safe_next)
    return _card_html(
        i18n.t_or("admin.shell.gate.title", "Acceso administrativo", lang),
        [escape(i18n.t_or("admin.shell.gate.portal_sign_in",
                          "Entrá con tu cuenta del portal.", lang))],
        (i18n.t_or("admin.shell.gate.portal_sign_in_button", "Ir al portal", lang), url) if url else None)


@router.get("/admin/login")
async def login_screen(request: Request):
    """Render the admin login gate without authentication.

    The screen is public (no ``Depends(require_admin_screen)``). Query parameter
    ``?next=`` specifies the post-login redirect target; validation happens
    server-side before render and the approved value (or ``/admin`` if omitted
    or invalid) is injected into the login form's ``data-next`` attribute.
    """
    lang = i18n.active()
    # There is no trustworthy intended username until credentials are submitted.
    # Explicitly opening login therefore clears any prior identity (including
    # valid other-user cookies); a successful login always mints its replacement.
    from app.auth import admin_auth
    headers = {"Cache-Control": "no-store"}
    if admin_auth._parse_cookie_token(request.headers.get("cookie")) is not None:
        headers["Set-Cookie"] = admin_auth._clear_cookie_from(request.headers)

    # Validate and sanitize the redirect target
    pairs = list(request.query_params.multi_items())
    # `via=portal` marks the way back from the portal's sign-in (below).
    via_portal = ("via", _VIA_PORTAL) in pairs
    pairs = [p for p in pairs if p != ("via", _VIA_PORTAL)]
    next_param = pairs[0][1] if len(pairs) == 1 and pairs[0][0] == "next" else ""
    safe_next = admin_navigation.canonical_admin_target(next_param)
    if not safe_next:
        safe_next = "/admin"

    from app.auth import staff_portal
    if staff_portal.enabled():
        # The portal is the login: an identified account goes straight in,
        # anyone else gets the one remedy that applies — no password form.
        resolution = await run_in_threadpool(staff_portal.resolve, request)
        if resolution.reason == "ok":
            return Response(status_code=303, headers={"Location": safe_next,
                                                      "Cache-Control": "no-store"})
        # Signed in on the portal = signed in here, with no button (the product owner,
        # m1008550). The portal's session cookie lasts 8 h and only the portal
        # page renews it, so a person still signed in there can arrive with no
        # identity: one trip through the portal's sign-in renews it and comes
        # back. Back with no identity still → the card, never a second trip.
        trip = _portal_trip(safe_next) if resolution.reason == "no_portal" else ""
        if trip and not via_portal:
            return Response(status_code=303, headers={**headers, "Location": trip})
        if resolution.reason != "totp":
            return Response(content=_portal_card(resolution, safe_next, lang),
                            media_type="text/html; charset=utf-8", headers=headers)

    # DB configurada y caída → tarjeta de corte, NO formulario (ver el
    # docstring del módulo): unas credenciales perfectas sólo podían terminar
    # en "usuario o contraseña incorrectos".
    if await run_in_threadpool(_db_is_down):
        return Response(
            content=_outage_html(safe_next),
            media_type="text/html; charset=utf-8",
            headers=headers,
        )

    if staff_portal.enabled():
        # Second factor of a portal-identified account: the same code step
        # the staff password login uses, keyed by the portal email.
        pending = {"identifier": resolution.email}
    else:
        pending = admin_auth.pending_staff(request.headers) if request.query_params.get("staff") == "1" else None
    rendered = jinja_env.render(_TEMPLATE, next_target=safe_next,
                               staff_identifier=pending["identifier"] if pending else "",
                               staff_pending=bool(pending))
    rendered = i18n.inject_html_lang(rendered, lang)
    # Same client-catalog set every admin page ships (admin-panel-y-auth.md:
    # admin pages pass `client_namespaces() + ["admin"]`) — the native
    # admin-login controller's T() calls read admin.shell.* from the client
    # bootstrap.
    namespaces = list(i18n.client_namespaces()) + ["admin"]
    rendered = i18n.inject_bootstrap(rendered, None, lang, namespaces=namespaces)
    body = theme.inject(rendered)

    return Response(
        content=body.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers=headers,
    )
