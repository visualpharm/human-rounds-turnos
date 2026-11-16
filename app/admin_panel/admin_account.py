"""Self-service account security for the Pinamar Turnos admin panel.

Routes:
  GET  /api/admin/account
  POST /api/admin/account/profile
  POST /api/admin/account/password
  POST /api/admin/account/2fa/start
  POST /api/admin/account/2fa/confirm
  POST /api/admin/account/2fa/remove

Every admin role can manage its own email, password and 2FA. The
environment-backed owner stores editable overrides in Postgres but keeps the
env credentials as break-glass fallback when Postgres is unavailable.

Editing your own account lives HERE and only here (2026-07-27): the users CRUD
(`admin_users.py`) is one admin acting on somebody else's account, and it
changes a password without asking for the current one — fine for a colleague
who forgot theirs, wrong for yourself. The owner row in Usuarios therefore
links to this page instead of opening an editor that could not change anything.
"""

import base64
import secrets
import urllib.parse
from datetime import datetime, timezone, timedelta

from itsdangerous import BadData, URLSafeTimedSerializer

from app.auth import admin_auth
from app.admin_panel import admin_users
from app.core import i18n
from app.core import db_operators
from app.core import db_auth
from app.core.status_error import MessageStatusError

_SETUP_TOKEN_SALT = "st-totp-setup-v1"


def _secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _otpauth(usuario, secret):
    # La marca la decide el tema activo (theme.brand_name()), igual que en
    # admin_users._otpauth_uri() — nunca un literal: lo que el operador ve en
    # Google Authenticator es el nombre público de la instalación, no el
    # nombre interno del repo.
    from app.core import theme
    marca = theme.brand_name()
    label = urllib.parse.quote(f"{marca.replace(' ', '')}:{usuario}")
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={urllib.parse.quote(marca)}&algorithm=SHA1&digits=6&period=30"
    )


def _setup_token(usuario, secret):
    serializer = URLSafeTimedSerializer(
        admin_auth._signing_key(), salt=_SETUP_TOKEN_SALT
    )
    return serializer.dumps({"u": usuario, "s": secret})


def _read_setup_token(token, usuario):
    try:
        serializer = URLSafeTimedSerializer(
            admin_auth._signing_key(), salt=_SETUP_TOKEN_SALT
        )
        data = serializer.loads(str(token or ""), max_age=600)
        if data.get("u") != usuario:
            return None
        return data.get("s") or None
    except (BadData, TypeError, ValueError):
        return None


def _set_totp(usuario, secret):
    if usuario == admin_auth.owner_usuario():
        db_auth.admin_security_owner_update(usuario, totp_secret=secret)
    else:
        db_operators.operator_update(usuario, totp_secret=secret)
    deadline = None if secret else datetime.now(timezone.utc) + timedelta(days=7)
    db_auth.admin_security_set_deadline(usuario, deadline)


def _set_email(usuario, email):
    if usuario == admin_auth.owner_usuario():
        db_auth.admin_security_owner_update(usuario, email=email)
    else:
        item = db_operators.operator_update(usuario, email=email)
        # A doctor has one email: its record follows its account.
        admin_users.follow_account_email(item, email)


def _change_password(usuario, password):
    if usuario == admin_auth.owner_usuario():
        db_auth.admin_security_owner_update(usuario, password=password)
    else:
        db_operators.operator_update(usuario, password=password)


# ============================================================ domain

class AccountError(MessageStatusError):
    """A 400 the caller turns into `{"error": str(exc)}` at `.status`."""


def account_status(identity):
    """Return the authenticated account's security status."""
    status = admin_auth.account_security(identity, ensure_deadline=True)
    out = {
        "usuario": identity["usuario"],
        "rol": identity["rol"],
        "email": admin_auth.account_email(identity["usuario"]),
        "es_dueno": identity["usuario"] == admin_auth.owner_usuario(),
        **status,
    }
    if admin_auth.staff_portal.enabled():
        # No password or passkey of ours on this installation: the screen
        # hides those sections.
        out["acceso_portal"] = True
    return out


def update_profile(identity, body):
    """Update the authenticated account's profile."""
    email = str((body or {}).get("email") or "").strip() or None
    if email and not admin_users.EMAIL_RE.match(email):
        raise AccountError(400, "Ingresá un email válido.")
    _set_email(identity["usuario"], email)
    return {"ok": True, "email": email}


def change_password(identity, body):
    """Change the authenticated account's password."""
    current = str((body or {}).get("actual") or "")
    new = str((body or {}).get("nueva") or "")
    usuario = str((body or {}).get("usuario") or identity["usuario"]).strip()
    rename = usuario != identity["usuario"]
    def fail(key, fallback, status=400):
        raise AccountError(status, i18n.t_or("admin.srv.account." + key, fallback, i18n.active()))
    if rename:
        if (identity["usuario"].casefold() == admin_auth.owner_usuario().casefold()
                or usuario.casefold() == admin_auth.owner_usuario().casefold()):
            fail("owner_fixed", "El usuario del dueño no se puede cambiar.")
        if not admin_users._username_ok(usuario):
            fail("invalid_username", "Ingresá un usuario de 2 a 120 caracteres, sin espacios.")
    if not admin_auth.verify_current_password(identity, current):
        fail("wrong_password", "La contraseña actual no coincide.")
    if (new or not rename) and len(new) < 8:
        fail("short_password", "La contraseña nueva tiene que tener al menos 8 caracteres.")
    if rename:
        try:
            db_operators.operator_rename(identity["usuario"], usuario, password=new or None,
                               current_password=current)
        except db_operators.OperatorConflict as exc:
            if str(exc) == "credenciales":
                fail("wrong_password", "La contraseña actual no coincide.")
            fail("username_taken", "Ese usuario ya está en uso.", 409)
        return {"ok": True, "usuario": usuario}
    _change_password(identity["usuario"], new)
    return {"ok": True}


def _qr_svg(url):
    """Inline SVG of the QR (segno — pure Python, no native deps), or None
    when segno isn't installed. Same helper the intake door poster uses; the
    panel falls back to the key in plain text, never to a dead end.

    The QR is built HERE and not in the browser on purpose: it encodes the
    otpauth URL, which carries the shared secret. Building it server-side
    keeps the secret inside the same authenticated response instead of
    handing it to a third-party image service."""
    try:
        import segno
    except ImportError:
        return None
    import io
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    # Dark on a transparent background: the panel puts it on a white plate of
    # its own, so the QR reads the same in the light and the dark theme.
    # ``omitsize`` makes Segno emit a viewBox instead of fixed width/height.
    # Without it the browser changing ``width: 270px`` to a responsive CSS
    # width only shrinks the SVG viewport; the QR path keeps its 270-unit
    # coordinate space and gets clipped instead of scaled.
    qr.save(buf, kind="svg", xmldecl=False, svgns=True, scale=6,
            dark="#111111", light=None, omitsize=True)
    return buf.getvalue().decode("utf-8")


def totp_start(identity, body=None):
    """Start a new TOTP enrollment for the authenticated account.

    An account that already has an authenticator proves the current password first: a copied
    session cookie must not be able to swap the second factor for its own (security review
    2026-11, L-1). The first enrollment, and an installation where the portal vouches for staff
    (no password of ours), ask for nothing."""
    try:
        has_totp = (not admin_auth.staff_portal.enabled()
                    and bool(admin_auth.account_totp_secret(identity["usuario"], strict=True)))
    except Exception:  # noqa: BLE001 — an unreadable store is a refusal, never «first enrolment»
        raise AccountError(503, i18n.t_or("admin.srv.account.store_unavailable",
                                          "No se pudo leer la cuenta. Probá de nuevo en un momento.",
                                          i18n.active()))
    if (has_totp
            and not admin_auth.verify_current_password(
                identity, body.get("actual") if isinstance(body, dict) else None)):
        raise AccountError(400, i18n.t_or("admin.srv.account.wrong_password",
                                          "La contraseña actual no coincide.", i18n.active()))
    secret = _secret()
    url = _otpauth(identity["usuario"], secret)
    return {
        "ok": True,
        "secret": secret,
        "otpauth_url": url,
        "qr_svg": _qr_svg(url),
        "setup_token": _setup_token(identity["usuario"], secret),
        "expires_in": 600,
    }


def totp_confirm(identity, body):
    """Confirm a TOTP enrollment for the authenticated account."""
    secret = _read_setup_token((body or {}).get("setup_token"), identity["usuario"])
    if not secret:
        raise AccountError(400, "La configuración venció. Empezá de nuevo.")
    if not admin_auth._totp_valido((body or {}).get("codigo"), secret):
        raise AccountError(400, "El código no coincide.")
    _set_totp(identity["usuario"], secret)
    return {"ok": True}


def totp_remove(identity, body):
    """Remove TOTP after re-authenticating with the account password.

    Quitar 2FA pide la contraseña y NADA MÁS (2026-07-27, responsable del producto: "pedir todo
    junto es brutal — la gente la saca justamente porque no tiene el código").
    OWASP pide reautenticar con UN factor ya enrolado antes de tocar la
    configuración de MFA, no con todos; GitHub lo dice como un O: "enter your
    password OR perform 2FA once more". Exigir password Y código es la
    combinación que deja afuera al único caso que importa — el teléfono
    perdido —, y ahí la salida sería pedirle a un admin que la desvincule,
    que es MENOS seguro que aceptar la contraseña de la propia persona."""
    if not admin_auth.verify_current_password(identity, (body or {}).get("actual")):
        raise AccountError(400, "La contraseña actual no coincide.")
    _set_totp(identity["usuario"], None)
    return {
        "ok": True,
        "2fa_deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    }
