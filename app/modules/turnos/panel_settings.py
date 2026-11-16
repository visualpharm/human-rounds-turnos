"""panel_settings.py — three switches of the panel's Settings that the turnos router serves: the site's
visual theme, the patient sign-in mode and the anti-bot verification (Turnstile).

Split out of booking_requests.py (TASK-60, 2026-11-06): they never belonged to a booking. Pure domain
functions — typed args in, `(status, payload)` out; the owner-only gate is checked by the router before.
"""

from app.modules.turnos import booking_requests_store as store
from app.core import theme
from app.core import i18n


def _db_unavailable():
    """The `(status, payload)` of a database that does not answer, through `core.deps.answer_db_unavailable`."""
    from app.core import deps
    return deps.answer_db_unavailable(lambda status: (status, {"error": i18n.t_or(
        "admin.srv.error.db_no_disponible", "base de datos no disponible", i18n.active())}))


# ---- site visual theme (admin Settings) ---------------------------------------
#
# Tier 4 slice 8 (third batch, native migration): `_xxx_core()` below is the
# PURE domain function (typed args in, `(status, payload)` out, no response
# writer touched). `booking_requests_router.py`'s native `async def` endpoint
# calls the same `_xxx_core()` after resolving the identical
# `admin_auth.require_admin` role gate through `core.deps.require_admin`
# (same flags, same central gate, no ad-hoc auth).

def _theme_get_core():
    """GET /api/admin/theme — active theme + theme catalog (owner only,
    gate already checked by the caller)."""
    return 200, {
        "tema": theme.current(),
        "themes": [theme.THEMES[k] for k in ("pinamar", "humanrounds")],
    }


def _theme_set_core(body):
    """POST /api/admin/theme {tema} — changes the site theme (owner only,
    gate already checked by the caller)."""
    tid = str((body or {}).get("tema") or "").strip().lower()
    if tid not in theme.THEMES:
        return 400, {"error": i18n.t_or("admin.srv.settings.theme_invalido", "tema inválido", i18n.active())}
    try:
        theme.save(tid)
    except store.DbUnavailable:
        return _db_unavailable()
    return 200, {"ok": True, "tema": tid}


# ---- patient sign-in mode (admin Settings) -------------------------------------

def _auth_mode_get_core():
    """GET /api/admin/auth-mode — sign-in mode + catalog (owner only, gate
    already checked by the caller)."""
    from app.auth import mode as auth_mode
    from app.cuentas import accounts, auth_renaper
    return 200, {
        "auth_mode": auth_mode.current(),
        "modes": [auth_mode.MODES[k] for k in ("password", "renaper")],
        # The RENAPER agreement isn't in place yet: with a "real" provider,
        # the public login still shows the password option even if RENAPER
        # is selected.
        "renaper_provider": accounts._renaper_provider(),
        # Whether a RENAPER sign-in can actually start (the IDP credentials
        # from Ajustes › Integraciones, or the fake): without it the panel
        # shows one «Sin configurar» line instead of the card (2026-11-08).
        "renaper_conectado": auth_renaper.configurado(),
    }


def _auth_mode_set_core(body):
    """POST /api/admin/auth-mode {auth_mode} — changes the sign-in mode
    (owner only, gate already checked by the caller)."""
    from app.auth import mode as auth_mode
    mid = str((body or {}).get("auth_mode") or "").strip().lower()
    if mid not in auth_mode.MODES:
        return 400, {"error": i18n.t_or("admin.srv.settings.authmode_invalido", "modo de ingreso inválido", i18n.active())}
    try:
        auth_mode.save(mid)
    except store.DbUnavailable:
        return _db_unavailable()
    return 200, {"ok": True, "auth_mode": mid}


# ---- anti-bot verification (admin Settings) -------------------------------

def _turnstile_get_core(headers):
    """GET /api/admin/turnstile status from explicit request headers."""
    from app.auth import turnstile
    return 200, {
        "on": turnstile.encendido(),
        "options": [turnstile.OPCIONES[k] for k in ("on", "off")],
        # To explain in the panel why the gate might not be running even
        # though it shows "Enabled": with no secret there's nothing to
        # verify, and it turns itself off on a dev host.
        "configurado": bool(turnstile._secret()),
        "host_dev": turnstile.host_de_dev_from(headers),
        "forzado_off_env": turnstile._apagado(),
    }


def _turnstile_set_core(body):
    """POST /api/admin/turnstile {on} — turns the gate on/off (owner only,
    gate already checked by the caller)."""
    val = (body or {}).get("on")
    if not isinstance(val, bool):
        return 400, {"error": i18n.t_or("admin.srv.settings.turnstile_falta_on", "falta on (true/false)", i18n.active())}
    from app.auth import turnstile
    try:
        turnstile.guardar(val)
    except store.DbUnavailable:
        return _db_unavailable()
    return 200, {"ok": True, "on": val}
