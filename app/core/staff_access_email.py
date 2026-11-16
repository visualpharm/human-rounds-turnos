"""The staff access email (invitation / password reset) lives in platform core.

Both senders are domain packages that may not import each other
(test_domain_dependency_direction): `admin_panel.admin_users` sends it when an
operator is created or invited from the panel, `auth.password_reset` sends it
from the neutral recovery request. The link and the copy are the same email,
so the function lives once here, next to the rest of platform core.
"""
import urllib.parse

from app.core import db_auth
from app.core import i18n
from app.core import site_identity
from app.core import theme


# An invitation travels further than a password reset — it is often copied
# into a WhatsApp chat and opened days later — so its one-use link lives a
# week; a reset link keeps the 24 hours (the product owner, 2026-10-19: invite with no
# required field, the link is the fallback when there is no email).
INVITE_HOURS = 24 * 7
RESET_HOURS = 24


def access_link(usuario, invitacion=False):
    """A fresh one-use «choose your password» link for this account. Issuing
    one retires any previous unused link of the same account."""
    token = db_auth.admin_reset_token_create(usuario, horas=INVITE_HOURS if invitacion else RESET_HOURS)
    return site_identity.base_url() + "/admin/reset-password?token=" + urllib.parse.quote(token)


def send_access_link(item, medico, invitacion=False):
    """`site_identity.base_url(handler=None)` never actually reads its
    `handler` argument (it just returns `seo.origin()` — the public origin
    is pinned by env, never derived from a client-controlled Host header),
    so this stays a pure function: no handler to plumb through."""
    from app.notifications import mail
    lang = i18n.active()
    email = (item.get("email") or medico.get("email") or "").strip()
    if not email:
        return False, i18n.t_or("admin.srv.users.falta_email_medico", "Falta el email del médico.", lang)
    brand = theme.brand_name()
    from app.auth import staff_portal
    if staff_portal.enabled():
        if not invitacion:
            # No password exists on a portal installation; nothing to reset.
            return False, i18n.t_or("admin.srv.users.portal_sin_contrasena",
                                     "En esta instalación se entra con la cuenta del portal.", lang)
        nombre = (medico.get("nombre") or "").strip()
        subject = i18n.t_or("admin.srv.users.email.invite_subject", "Tu acceso a {marca}", lang, marca=brand)
        text = i18n.t_or(
            "admin.srv.users.email.invite_portal_body",
            "Hola{nombre},\n\nYa podés acceder al panel de {marca}. Entrá en {panel} con tu cuenta "
            "del portal, la de este email ({email}), con tu identidad validada.\n",
            lang, nombre=(" " + nombre) if nombre else "", marca=brand,
            panel=site_identity.base_url() + "/admin", email=email)
        try:
            return mail.send(email, subject, text), None
        except Exception:  # provider/config failures never undo account creation
            return False, None
    link = access_link(item["usuario"], invitacion=invitacion)
    if invitacion:
        subject = i18n.t_or("admin.srv.users.email.invite_subject", "Tu acceso a {marca}", lang, marca=brand)
        nombre = (medico.get("nombre") or "").strip()
        if nombre:
            text = i18n.t_or(
                "admin.srv.users.email.invite_body",
                "Hola {nombre},\n\nYa podés acceder al panel de {marca}. Elegí tu contraseña con este enlace, "
                "válido por 7 días:\n\n{link}\n\nUsuario: {usuario}\n",
                lang, nombre=nombre, marca=brand, link=link, usuario=item["usuario"])
        else:
            text = i18n.t_or(
                "admin.srv.users.email.invite_body_anon",
                "Hola,\n\nYa podés acceder al panel de {marca}. Elegí tu contraseña con este enlace, "
                "válido por 7 días:\n\n{link}\n\nUsuario: {usuario}\n",
                lang, marca=brand, link=link, usuario=item["usuario"])
    else:
        subject = i18n.t_or("admin.srv.users.email.reset_subject", "Restablecer contraseña de {marca}",
                             lang, marca=brand)
        text = i18n.t_or(
            "admin.srv.users.email.reset_body",
            "Se solicitó restablecer tu contraseña del panel de {marca}. Elegí una nueva con este enlace, "
            "válido por 24 horas:\n\n{link}\n",
            lang, marca=brand, link=link)
    try:
        return mail.send(email, subject, text), None
    except Exception:  # provider/config failures never undo account creation
        return False, None
