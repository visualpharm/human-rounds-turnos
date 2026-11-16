"""admin_users.py — CRUD de operadores del panel admin.

Dueño de este módulo:
  GET    /api/admin/users              lista los usuarios del panel (dueño + operadores)
  POST   /api/admin/users              da de alta un operador
  PUT    /api/admin/users/<usuario>    edita rol/activo/contraseña/2FA
  DELETE /api/admin/users/<usuario>    desactiva (soft — nunca se borra en
                                          duro: `solicitudes.resuelta_por`
                                          sigue apuntando al usuario)

Todas las rutas exigen sesión con rol 'admin' (admin_auth.require_rol_admin,
que ya responde 401/403 si falta). El dueño (login por env, TURNOS_ADMIN_*)
no vive en la tabla `operadores` y no se puede crear/editar/desactivar desde
acá — sigue siendo el super-admin de emergencia si la base cae.

Rol 'medico' (2026-07-16): cuenta de un profesional que sólo ve sus propios
turnos (doctor_appointments.py) — exige `medico_id` válido contra el catálogo de
médicos (_valid_doctor, GET /api/admin/content/doctors, ya solo_admin así
que quien crea usuarios puede consultarlo). El permiso real (medico → 403 en
todo salvo /api/admin/my-appointments) vive centralizado en
admin_auth.require_admin(permitir_medico=...), no acá.

The native router owns request parsing, authorization, and responses; this
module owns the operator-account domain operations it calls.
"""

import re
import base64
import secrets
import urllib.parse
from datetime import datetime, timezone, timedelta

from app.core import db
from app.core import db_operators
from app.core import db_auth
from app.core import db_catalog
from app.auth import admin_auth
from app.core import i18n
from app.core import usernames
from app.core import permissions
from app.core import text_fold
from app.core.staff_access_email import send_access_link as _send_reset
from app.auth import staff_portal
from app.admin_panel.native_json import ApiError

USER_ACTIONS = frozenset(("invite", "link", "create", "disable", "reset-password", "reset-2fa", "reset-calendar"))

# La regla del nombre de usuario vive en core/usernames.py — la comparten esta
# pantalla y el asistente de instalación.
_username_ok = usernames.valid
# Los cuatro valores que `operadores.rol` (y su CHECK en core/db.py) puede
# guardar; un rol nuevo se guarda en `operator_access.role_slug` sobre esa
# base. Un rol asignable se valida contra la tabla de roles
# (permissions.role_is_assignable); esta tupla es el respaldo de siempre para
# cuando ese almacén no se puede leer — ver `_rol_asignable()`.
_ROLES = ("medico", "admisionista", "reportes", "admin")


def _log_permisos_degradado(donde):
    """Una línea cuando el almacén de permisos no se puede leer y la pantalla
    cae al comportamiento de siempre. Sin identidad de nadie: sólo dónde
    pasó, que es lo que hace falta para darse cuenta de que está degradado."""
    print("[usuarios] permisos ilegibles en %s: se degrada a los roles de "
          "siempre" % donde, flush=True)


def _rol_asignable(rol):
    """True si ese rol se le puede asignar a una cuenta.

    Pregunta por la tabla de roles; si no se puede leer, se degrada a los
    cuatro roles de siempre en vez de bloquear la pantalla de usuarios."""
    try:
        return permissions.role_is_assignable(rol)
    except db.DbUnavailable:  # sólo el almacén ilegible degrada
        _log_permisos_degradado("role_is_assignable")
        return rol in _ROLES


# Pública: admin_account.py valida con ESTA misma regla cuando cada persona
# edita su propio email — una cuenta creada acá y editada allá no puede tener
# dos criterios distintos de qué es un email válido.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EMAIL_RE = EMAIL_RE


def _valid_centers(valor):
    """Normaliza la lista de centros de una cuenta (alcance por centro de
    salud, reunión 2026-07-24). Devuelve (lista, error): lista vacía = sin
    restricción, que es el default y lo que vale para admin/reportes. Los
    slugs se validan contra el catálogo — un slug inventado dejaría la cuenta
    sin ver NADA, que es el peor modo de fallar para un permiso."""
    if valor is None:
        return [], None
    if not isinstance(valor, list):
        return None, i18n.t_or("admin.srv.users.centros_no_lista",
                                "centros debe ser una lista de centros", i18n.active())
    slugs, vistos = [], set()
    try:
        from app.patient_web import catalog
        existentes = {c.get("slug") for c in catalog.centers()}
    except Exception:  # noqa: BLE001 — sin catálogo no inventamos validación
        existentes = None
    for raw in valor:
        s = str(raw or "").strip()
        if not s or s in vistos:
            continue
        if existentes is not None and s not in existentes:
            return None, i18n.t_or("admin.srv.users.centro_no_existe", "El centro «{centro}» no existe.",
                                    i18n.active(), centro=s)
        vistos.add(s)
        slugs.append(s)
    return slugs, None


def _valid_doctor(medico_id):
    """True si `medico_id` existe en el catálogo (webdata/medicos.json +
    overlay del admin) — una cuenta 'medico' tiene que apuntar a un
    profesional real, si no doctor_appointments.py nunca va a poder resolverle
    turnos. Import perezoso (mismo patrón que el resto del módulo)."""
    if not medico_id:
        return False
    try:
        from app.patient_web import catalog
        return any(m.get("id") == medico_id for m in catalog.doctors())
    except Exception:  # noqa: BLE001
        return False


# ---- helpers -----------------------------------------------------------

def _is_owner(usuario):
    return usuario == admin_auth.owner_usuario()


def _gen_totp_secret():
    """Secreto TOTP base32 nuevo (10 bytes ~= 16 caracteres, mismo largo que
    un secreto típico de Google Authenticator)."""
    return base64.b32encode(secrets.token_bytes(10)).decode("ascii").rstrip("=")


def _otpauth_uri(usuario, secret):
    """otpauth://totp/... — lo que una app de autenticación (Google
    Authenticator, Authy) puede leer a mano (sin QR: no hay libs de imagen acá,
    y no agregamos ninguna sólo para esto — el texto se copia igual)."""
    from app.core import theme
    marca = theme.brand_name()
    label = urllib.parse.quote(f"{marca.replace(' ', '')}:{usuario}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={urllib.parse.quote(marca)}"


def _portal_email_needed(lang):
    return i18n.t_or("admin.srv.users.portal_email_requerido",
                     "Escribí el email con el que entra al portal.", lang)


def _portal_email_in_use(lang):
    return i18n.t_or("admin.srv.users.portal_email_en_uso",
                     "Ese email ya es de otra cuenta activa.", lang)


def _portal_email_taken(email, active, usuario):
    """On a portal-login installation the email is the first-login match:
    two ACTIVE accounts sharing one could never be entered, so an account
    may not become active with an email another active account has."""
    if not staff_portal.enabled() or not email or not active:
        return False
    return db_operators.operator_active_email_taken(email, except_usuario=usuario)


def portal_email_conflict(email):
    """The refusal text when `email` already belongs to an ACTIVE account on a
    portal-login installation, else None. Creating a doctor asks this BEFORE
    anything is written: the invitation would be refused later, and a doctor
    saved without its invitation is what a list pasted twice would leave."""
    if _portal_email_taken(str(email or "").strip(), True, None):
        return _portal_email_in_use(i18n.active())
    return None


def portal_email_missing(email):
    """The refusal text when an invitation has no email on a portal-login
    installation, else None. The email IS the portal match and there is no
    link to hand over instead, so `invite_account` would refuse it after the
    doctor and its account were saved — creating a doctor asks this first."""
    if staff_portal.enabled() and not str(email or "").strip():
        return _portal_email_needed(i18n.active())
    return None


def _portal_without_password(lang):
    return i18n.t_or("admin.srv.users.portal_sin_contrasena",
                     "En esta instalación se entra con la cuenta del portal.", lang)


def _with_2fa_status(item):
    """Agrega el estado operativo que necesita la lista/formulario sin
    exponer el secreto. `requiere_configurar_2fa` es el bloqueo inmediato:
    se limpia solo cuando la persona termina de configurar su nuevo código."""
    out = dict(item)
    status = admin_auth.account_security(
        {"usuario": out.get("usuario"), "rol": out.get("rol") or "medico"},
        ensure_deadline=False,
    )
    out["tiene_2fa"] = bool(status.get("tiene_2fa"))
    out["requiere_configurar_2fa"] = bool(status.get("2fa_vencido"))
    return out


def _owner_item():
    """El dueño como si fuera una fila de la lista — para que el admin vea
    TODOS los que pueden entrar al panel en un solo lugar, aunque el dueño no
    viva en la tabla `operadores`."""
    return _with_2fa_status({
        "usuario": admin_auth.owner_usuario(),
        "rol": "admin",
        "activo": True,
        "tiene_2fa": admin_auth.owner_tiene_totp(),
        "email": admin_auth.owner_email(),
        "creado": None,
        "ultimo_acceso": admin_auth.owner_ultimo_acceso(),
        "es_dueno": True,
    })


# ---- GET /api/admin/users --------------------------------------------

def list_users():
    """Build the user list after the native router has authorized the request."""
    items = [_owner_item()] + [
        dict(_with_2fa_status(it), es_dueno=False) for it in db_operators.operators_list()
    ]
    # Los agregados de permiso por persona y el catálogo de roles salen del
    # almacén de permisos. Si ese almacén no se puede leer, la pantalla se
    # degrada a los cuatro roles de siempre y SIN agregados (responsable del producto,
    # 2026-09-12: el portón degrada, nunca deja al personal afuera) — ver
    # `_rol_asignable()`, misma regla.
    try:
        for item in items[1:]:  # Skip owner (no overrides)
            granted, denied = permissions.get_overrides(item["usuario"])
            item["permisos_agregados"] = granted
            item["permisos_quitados"] = denied
        all_roles = permissions.list_roles(lang=i18n.active())
        enabled_roles = [role for role in all_roles if role.get("enabled", True)]
        role_names = {role["slug"]: role.get("name") or role.get("nombre") for role in all_roles}
        for item in items:
            item["rol_nombre"] = role_names.get(item.get("rol"))
        permisos = permissions.permissions_payload(i18n.active())
    except db.DbUnavailable:
        _log_permisos_degradado("list_users")
        for item in items[1:]:
            item["permisos_agregados"] = []
            item["permisos_quitados"] = []
        enabled_roles = [{"slug": r, "nombre": r} for r in _ROLES]
        permisos = {}
    out = {
        "items": items,
        "roles": enabled_roles,
        "permissions": permisos,
    }
    if staff_portal.enabled():
        # The screen hides password, access-mode and passkey controls; the
        # 2FA controls stay only when our TOTP is switched on.
        out["acceso_portal"] = True
        out["acceso_portal_totp"] = staff_portal.totp_enabled()
    return out


def _operator_conflict(exc):
    messages = {
        "medico_con_usuario": "This doctor already has another user.",
        "usuario_ya_vinculado": "This user is already linked to another doctor.",
        "nombre_ya_existe": "A user with that name already exists.",
    }
    key = str(exc)
    return ApiError({"error": i18n.t_or("admin.srv.users." + key, messages[key], i18n.active())}, status=409)


def create_user(body):
    """Create a user or raise ``ApiError`` for a validation failure."""
    lang = i18n.active()
    usuario = (body.get("usuario") or "").strip()
    # On a portal-login installation nobody has a password of ours: every new
    # account is an invitation by email, and the email is the portal match.
    invitation = body.get("access_mode") == "invite" or staff_portal.enabled()
    password = secrets.token_urlsafe(32) if invitation else str(body.get("password") or "")
    rol = (body.get("rol") or "medico").strip()
    quiere_totp = bool(body.get("totp"))
    forzar_2fa = bool(body.get("forzar_2fa"))
    medico_id = (body.get("medico_id") or "").strip() or None
    email = str(body.get("email") or "").strip() or None
    permisos_agregados = body.get("permisos_agregados", [])
    permisos_quitados = body.get("permisos_quitados", [])

    # Cada error de validación viaja con el campo que lo causó ("field"), para
    # que el panel escriba el mensaje al lado de ESE campo en vez de tirarlo en
    # un cartel flotante lejos de donde está mirando la persona.
    if not _username_ok(usuario):
        raise ApiError({"error": i18n.t_or(
            "admin.srv.users.usuario_formato",
            "Escribí un nombre de usuario de al menos 2 caracteres, sin espacios.",
            lang), "field": "usuario"}, status=400)
    if len(password) < 8:
        raise ApiError({"error": i18n.t_or("admin.srv.users.password_corta",
                                            "La contraseña tiene que tener al menos 8 caracteres.",
                                            lang), "field": "password"}, status=400)
    if _is_owner(usuario):
        raise ApiError({"error": i18n.t_or("admin.srv.users.usuario_ya_existe",
                                            "Ese usuario ya existe.", lang),
                        "field": "usuario"}, status=409)
    if not _rol_asignable(rol):
        raise ApiError({"error": i18n.t_or("admin.srv.users.rol_invalido", "rol inválido", lang),
                        "field": "rol"}, status=400)
    if email and not _EMAIL_RE.match(email):
        raise ApiError({"error": i18n.t_or("admin.srv.users.email_invalido",
                                            "Ingresá un email válido.", lang),
                        "field": "email"}, status=400)
    if rol == "medico":
        if not _valid_doctor(medico_id):
            raise ApiError({"error": i18n.t_or("admin.srv.users.medico_invalido",
                                                "Elegí un médico del catálogo para esta cuenta.",
                                                lang), "field": "medico_id"}, status=400)
    else:
        medico_id = None
    doctor = next(iter(_doctors_by_ids([medico_id])), {}) if medico_id else {}
    email = email or str(doctor.get("email") or "").strip() or None
    if email and not _EMAIL_RE.fullmatch(email):
        raise ApiError({"error": i18n.t_or("admin.srv.users.email_invalido",
            "Ingresá un email válido.", lang), "field": "email"}, status=400)
    if staff_portal.enabled() and not email:
        raise ApiError({"error": _portal_email_needed(lang), "field": "email"}, status=400)
    if _portal_email_taken(email, True, usuario):
        raise ApiError({"error": _portal_email_in_use(lang), "field": "email"}, status=409)
    centros, err = _valid_centers(body.get("centros"))
    if err:
        raise ApiError({"error": err}, status=400)
    # Acotar por centro a un admin no tendría sentido: es quien administra
    # todos. El alcance es para quien trabaja en UN centro.
    if rol == "admin":
        centros = []
    if db_operators.operator_get(usuario):
        raise ApiError({"error": i18n.t_or("admin.srv.users.nombre_ya_existe",
                                            "Ya existe un usuario con ese nombre.", lang)}, status=409)
    if quiere_totp and forzar_2fa:
        raise ApiError({"error": i18n.t_or(
            "admin.srv.users.totp_conflicto",
            "Elegí entre generar el código ahora o pedirle al usuario que lo configure al ingresar.",
            lang)}, status=400)

    # Validate permission overrides
    try:
        granted, denied = permissions.clean_overrides(permisos_agregados, permisos_quitados)
    except permissions.RoleError as e:
        raise ApiError({"error": str(e), "field": e.field or "permisos"}, status=e.status)

    totp_secret = _gen_totp_secret() if quiere_totp else None
    try:
        item = db_operators.operator_create(
            usuario, password, rol, totp_secret, medico_id=medico_id, email=email,
            centros=centros)
    except db_operators.OperatorConflict as exc:
        raise _operator_conflict(exc) from exc

    if doctor and email:
        follow_doctor_email(doctor, email)

    # Set per-user overrides if provided
    if granted or denied:
        permissions.set_overrides(usuario, granted, denied)

    db_auth.admin_security_set_deadline(
        usuario,
        datetime.now(timezone.utc) if forzar_2fa else
        (None if totp_secret else datetime.now(timezone.utc) + timedelta(days=7)),
    )
    out = {"ok": True, "item": dict(_with_2fa_status(item), es_dueno=False)}
    if totp_secret:
        out["totp"] = {"secret": totp_secret, "otpauth_url": _otpauth_uri(usuario, totp_secret)}
    if invitation:
        # With an email the invitation goes out by itself; without one (or
        # when it could not leave) the response carries the one-use link to
        # copy — the random password is never revealed (the product owner, 2026-10-19).
        sent = invite_account(item, doctor, email=email)
        out["email_enviado"] = sent["email_enviado"]
        out["item"] = dict(_with_2fa_status(db_operators.operator_get_public(usuario)), es_dueno=False)
        if sent.get("enlace"):
            out["enlace"] = sent["enlace"]
    return out


def _edit_overrides(body):
    """The cleaned ``(granted, denied)`` lists of a PUT body, or ``(None, None)``
    when it names neither key (the overrides are then left alone)."""
    permisos_agregados = None
    permisos_quitados = None
    if "permisos_agregados" in body or "permisos_quitados" in body:
        permisos_agregados = body.get("permisos_agregados", [])
        permisos_quitados = body.get("permisos_quitados", [])
        try:
            permisos_agregados, permisos_quitados = permissions.clean_overrides(
                permisos_agregados, permisos_quitados)
        except permissions.RoleError as e:
            raise ApiError({"error": str(e), "field": e.field or "permisos"}, status=e.status)
    return permisos_agregados, permisos_quitados


def _edit_email(body, lang):
    """The email to store: ``db_operators.NO_CHANGE`` when the body has no ``email`` key,
    ``None`` to clear it, else the stripped address."""
    email = db_operators.NO_CHANGE
    if "email" in body:
        email = (str(body.get("email") or "").strip() or None)
        if email and not _EMAIL_RE.match(email):
            raise ApiError({"error": i18n.t_or("admin.srv.users.email_invalido",
                                                "Ingresá un email válido.", lang),
                            "field": "email"}, status=400)
    return email


def _edit_medico_id(body, rol, rol_efectivo, existente, lang):
    """The ``medico_id`` to store (``db_operators.NO_CHANGE`` when untouched)."""
    # medico_id: sólo lo tocamos si vino en el body; si el rol EFECTIVO (el
    # nuevo si cambia, si no el que ya tenía) es 'medico', tiene que quedar
    # apuntando a un médico real del catálogo. Cambiar A otro rol lo vacía.
    medico_id = db_operators.NO_CHANGE
    if "medico_id" in body:
        medico_id = (body.get("medico_id") or "").strip() or None
    if rol_efectivo == "medico":
        efectivo_mid = medico_id if medico_id is not db_operators.NO_CHANGE else existente.get("medico_id")
        if not _valid_doctor(efectivo_mid):
            raise ApiError({"error": i18n.t_or("admin.srv.users.medico_invalido",
                                                "Elegí un médico del catálogo para esta cuenta.",
                                                lang), "field": "medico_id"}, status=400)
    elif rol_efectivo == "admin":
        # Un médico que pasa a administrador SIGUE siendo ese médico (el
        # administrador del sitio, 2026-11-02: «al ser médica no me deja
        # agregarle ese perfil»):
        # el vínculo no se toca desde acá — ni al ascender, ni al editar la
        # cuenta desde Usuarios, que manda medico_id vacío para todo rol que
        # no es médico. Sólo el rol 'médico' o «Reparar vínculo» lo cambian.
        medico_id = db_operators.NO_CHANGE
    elif rol is not None:
        medico_id = None  # se está sacando del rol 'medico' (o ya no lo era): no dejar un id colgado
    return medico_id


def _edit_centros(body, rol_efectivo):
    """The center list to store (``db_operators.NO_CHANGE`` when untouched)."""
    centros = db_operators.NO_CHANGE
    if "centros" in body:
        centros, err = _valid_centers(body.get("centros"))
        if err:
            raise ApiError({"error": err}, status=400)
    if rol_efectivo == "admin":
        centros = []   # un admin nunca queda acotado a un centro
    return centros


def _edit_password(body, lang):
    """The new password, or ``None`` for "keep the current one"."""
    password = body.get("password")
    if password is not None:
        password = str(password).strip() or None
        if password is not None and len(password) < 8:
            raise ApiError({"error": i18n.t_or("admin.srv.users.password_corta",
                                                "La contraseña tiene que tener al menos 8 caracteres.",
                                                lang), "field": "password"}, status=400)
    return password


def _edit_totp_secret(totp_action, fuerza_presente, forzar_2fa, lang):
    """``(totp_secret, nuevo_secret)``: the value to store (``db_operators.NO_CHANGE``,
    ``None`` to revoke, or a fresh secret) and the fresh secret alone, which
    the response hands back once."""
    totp_secret = db_operators.NO_CHANGE
    nuevo_secret = None
    if totp_action == "generar":
        nuevo_secret = _gen_totp_secret()
        totp_secret = nuevo_secret
    elif totp_action == "quitar":
        totp_secret = None
    elif totp_action not in (None, ""):
        # 'generar'/'quitar' son valores de protocolo (los manda
        # admin-users.js tal cual) — no se traducen, sólo el resto del
        # mensaje.
        raise ApiError({"error": i18n.t_or("admin.srv.users.totp_invalido",
                                            "totp inválido (usar 'generar' o 'quitar')", lang)},
                        status=400)
    if fuerza_presente and forzar_2fa:
        if totp_action == "generar":
            raise ApiError({"error": i18n.t_or(
                "admin.srv.users.totp_forzar_conflicto",
                "No se puede generar un código y pedir otro nuevo al próximo ingreso.",
                lang)}, status=400)
        # Revoca el código actual antes de bloquear la cuenta. La persona
        # configurará el reemplazo en su propia pantalla Cuenta.
        totp_secret = None
    return totp_secret, nuevo_secret


def _edit_check_lockout(usuario, rol, activo, permisos_agregados, permisos_quitados):
    """Refuse a change that would leave nobody able to manage users."""
    # Check lockout before applying changes that could affect user management
    if rol is not None or activo is not None or permisos_agregados is not None:
        operator_change = {"usuario": usuario}
        if rol is not None:
            operator_change["slug"] = rol
        if activo is not None:
            operator_change["active"] = activo
        if permisos_agregados is not None:
            operator_change["granted"] = permisos_agregados
            operator_change["denied"] = permisos_quitados
        try:
            permissions.assert_user_manager_remains(operator_change=operator_change)
        except permissions.RoleError as e:
            raise ApiError({"error": str(e), "field": e.field or "rol"}, status=e.status)
        except db.DbUnavailable:
            # SOLO el almacén ilegible degrada (responsable del producto, 2026-09-12): sin poder
            # contarlos, la guarda de "que quede alguien que maneje usuarios"
            # no se puede evaluar y vale la regla de siempre, que no la tenía.
            # El dueño (login del entorno) nunca deja de poder entrar, así que
            # esto no puede dejar a la instalación sin quien administre.
            # Cualquier OTRO error sigue siendo un error: no se tapa.
            _log_permisos_degradado("assert_user_manager_remains")


def _edit_set_2fa_deadline(usuario, item, fuerza_presente, forzar_2fa, nuevo_secret, totp_action):
    """Move the "set up 2FA by" deadline that goes with the 2FA keys of the body."""
    if fuerza_presente:
        db_auth.admin_security_set_deadline(
            usuario,
            datetime.now(timezone.utc) if forzar_2fa else
            (None if item.get("tiene_2fa") else datetime.now(timezone.utc) + timedelta(days=7)),
        )
    elif nuevo_secret:
        db_auth.admin_security_set_deadline(usuario, None)
    elif totp_action == "quitar":
        db_auth.admin_security_set_deadline(
            usuario, datetime.now(timezone.utc) + timedelta(days=7)
        )


def _edit_own_credentials_check(usuario, body, identity, lang):
    """Changing the caller's OWN password or authenticator through this screen asks for the
    current password, like «Cuenta» does: a copied session cookie must not be able to swap them
    (security review 2026-11, finding 10). Not asked where the portal vouches for staff."""
    # Compared case-folded and trimmed: a differently spelled path must not skip the check.
    if (not identity or str(identity.get("usuario") or "").strip().casefold() != str(usuario or "").strip().casefold()
            or admin_auth.staff_portal.enabled()):
        return
    touches = body.get("totp") not in (None, "") or str(body.get("password") or "").strip()
    if touches and not admin_auth.verify_current_password(identity, body.get("actual")):
        raise ApiError({"error": i18n.t_or("admin.srv.account.wrong_password",
                                            "La contraseña actual no coincide.", lang),
                        "field": "actual"}, status=400)


def edit_user(usuario, body, identity=None):
    """Edit a user or raise ``ApiError`` for a validation failure."""
    lang = i18n.active()
    if _is_owner(usuario):
        raise ApiError({"error": i18n.t_or("admin.srv.users.dueno_no_editable",
                                            "El usuario dueño no se edita desde acá.", lang)}, status=400)
    existente = db_operators.operator_get(usuario)
    if not existente:
        raise ApiError({"error": i18n.t_or("admin.srv.users.no_encontrado", "no encontrado", lang)},
                        status=404)
    _edit_own_credentials_check(usuario, body, identity, lang)

    rol = body.get("rol")
    if rol is not None and not _rol_asignable(rol):
        raise ApiError({"error": i18n.t_or("admin.srv.users.rol_invalido", "rol inválido", lang),
                        "field": "rol"}, status=400)

    # Extract override fields for later lockout check
    permisos_agregados, permisos_quitados = _edit_overrides(body)
    email = _edit_email(body, lang)
    rol_efectivo = rol if rol is not None else existente.get("rol")
    medico_id = _edit_medico_id(body, rol, rol_efectivo, existente, lang)
    centros = _edit_centros(body, rol_efectivo)

    activo = body.get("activo")
    if activo is not None:
        activo = bool(activo)
    if _portal_email_taken(existente.get("email") if email is db_operators.NO_CHANGE else email,
                           existente.get("activo") if activo is None else activo, usuario):
        raise ApiError({"error": _portal_email_in_use(lang), "field": "email"}, status=409)

    password = _edit_password(body, lang)

    totp_action = (body.get("totp") or "").strip() if body.get("totp") is not None else None
    fuerza_presente = "forzar_2fa" in body
    forzar_2fa = bool(body.get("forzar_2fa"))
    totp_secret, nuevo_secret = _edit_totp_secret(totp_action, fuerza_presente, forzar_2fa, lang)

    _edit_check_lockout(usuario, rol, activo, permisos_agregados, permisos_quitados)

    try:
        item = db_operators.operator_update(usuario, rol=rol, activo=activo, password=password,
                                      totp_secret=totp_secret, medico_id=medico_id,
                                      email=email, centros=centros)
    except db_operators.OperatorConflict as exc:
        raise _operator_conflict(exc) from exc
    if email is not db_operators.NO_CHANGE:
        follow_account_email(item, email)

    # Apply per-user overrides if provided
    if permisos_agregados is not None:
        permissions.set_overrides(usuario, permisos_agregados, permisos_quitados)

    _edit_set_2fa_deadline(usuario, item, fuerza_presente, forzar_2fa, nuevo_secret, totp_action)
    out = {"ok": True, "item": dict(_with_2fa_status(item), es_dueno=False)}
    if nuevo_secret:
        out["totp"] = {"secret": nuevo_secret, "otpauth_url": _otpauth_uri(usuario, nuevo_secret)}
    # Add override data to response
    if permisos_agregados is not None:
        out["item"]["permisos_agregados"] = permisos_agregados
        out["item"]["permisos_quitados"] = permisos_quitados
    return out


def disable_user(usuario):
    lang = i18n.active()
    if _is_owner(usuario):
        raise ApiError({"error": i18n.t_or("admin.srv.users.dueno_no_desactivable",
                                            "El usuario dueño no se puede desactivar.", lang)}, status=400)

    # Check lockout before disabling (same as activo=False). Si el almacén de
    # permisos no se puede leer, la guarda de "que quede alguien que maneje
    # usuarios" no se puede evaluar: se degrada al comportamiento de siempre
    # (desactivar, con el dueño ya protegido arriba) en vez de trabar la
    # pantalla — misma decisión que `_rol_asignable()` y `list_users()`.
    try:
        permissions.assert_user_manager_remains(
            operator_change={"usuario": usuario, "active": False})
    except permissions.RoleError as e:
        raise ApiError({"error": str(e), "field": e.field or "usuario"}, status=e.status)
    except db.DbUnavailable:  # mismo criterio que en `update_user()`
        _log_permisos_degradado("assert_user_manager_remains")

    row = db_operators.operator_deactivate(usuario)
    if not row:
        raise ApiError({"error": i18n.t_or("admin.srv.users.no_encontrado", "no encontrado", lang)},
                        status=404)
    return {"ok": True}


def _doctors_by_ids(ids):
    from app.patient_web import catalog
    wanted = {str(x) for x in ids if str(x).strip()}
    return [m for m in catalog.doctors(incluir_incompletos=True)
            if str(m.get("id") or "") in wanted and not m.get("_borrado")]


# One email per doctor (2026-11-03). The account keeps the address it logs
# in with, is invited at and recovers its password through; the doctor's
# record kept a second copy that nothing else reads and that drifted the
# moment one of the two screens changed it. Every write of either now
# writes both, and the panel shows one field (the doctor's «Email»).
def follow_doctor_email(doctor, email):
    """The account's email just became `email`: the doctor's record follows."""
    email = str(email or "").strip() or None
    if not doctor or not doctor.get("id") or str(doctor.get("email") or "").strip() == (email or ""):
        return
    from app.patient_web import catalog
    db_catalog.catalog_set_doctor_email(doctor, email)
    catalog.invalidate()


def follow_account_email(item, email):
    """`item` (an account) changed its email: its doctor, if any, follows."""
    medico_id = str((item or {}).get("medico_id") or "").strip()
    if not medico_id:
        return
    doctors = _doctors_by_ids([medico_id])
    if doctors:
        follow_doctor_email(doctors[0], email)


def email_conflict_for_doctor(medico_id, email):
    """The refusal text when the doctor's new email cannot become its
    account's (another active account logs in with it on a portal
    installation), else None."""
    email = str(email or "").strip()
    # Only a portal installation can refuse: elsewhere the doctor form asks
    # the accounts table nothing before it saves.
    if not email or not staff_portal.enabled():
        return None
    item = db_operators.operator_by_doctor_id(medico_id)
    if not item or email.lower() == str(item.get("email") or "").strip().lower():
        return None
    if _portal_email_taken(email, item.get("activo") is not False, item["usuario"]):
        return _portal_email_in_use(i18n.active())
    return None


def doctor_email_changed(medico_id, email):
    """The doctor's own record changed its email: its account follows."""
    item = db_operators.operator_by_doctor_id(medico_id)
    email = str(email or "").strip() or None
    if not item or (email or "") == str(item.get("email") or "").strip():
        return
    db_operators.operator_update(item["usuario"], email=email)


def _ascii_word(value):
    plain = text_fold.ascii_fold(str(value or ""))
    return re.sub(r"[^a-z0-9]+", "", plain.lower())


def doctor_username_base(doctor):
    """`nombre.apellido` in plain ASCII — the shape of the doctor accounts
    created by the 2026-10-18 backfill (for example dora.villaverde,
    pedro.actisbernasconi): the first word of the first name, the whole
    surname without spaces. A doctor without a
    name yet (invited empty) gets `medico.nuevo`."""
    pila = str(doctor.get("nombre_pila") or "").split()
    apellido = str(doctor.get("apellido") or "")
    if not (pila and apellido.strip()):
        words = str(doctor.get("nombre") or "").split()
        pila, apellido = words[:1], (words[-1] if len(words) > 1 else "")
    first, last = _ascii_word(pila[0] if pila else ""), _ascii_word(apellido)
    base = ".".join(x for x in (first, last) if x)
    return base if len(base) >= 3 else "medico.nuevo"


def _available_username(base, cur=None):
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", ".", base or "").strip(".").lower()
    clean = clean[:32] or "medico"
    if len(clean) < 3:
        clean = ("med." + clean)[:32]

    def taken(name):
        if _is_owner(name):
            return True
        if cur is None:
            return bool(db_operators.operator_get(name))
        return db_operators.operator_name_taken(cur, name)

    candidate = clean
    n = 2
    while taken(candidate):
        candidate = f"{clean[:35]}{n}"
        n += 1
    return candidate


def provision_doctor_account(doctor, connection=None):
    """The account every doctor has (the product owner, 2026-10-19: «when we create a
    doctor, we should automatically create a user»). Inactive and «sin
    invitar»: nobody can log in until it is invited. Inside `connection`
    when the doctor itself is being created, so both rows commit or neither.
    Returns the public account."""
    email = str(doctor.get("email") or "").strip() or None
    with db_operators.connection_scope(connection) as conn:
        cur = conn.cursor()
        usuario = _available_username(doctor_username_base(doctor), cur=cur)
        return db_operators.operator_create(
            usuario, secrets.token_urlsafe(32), "medico", None,
            medico_id=str(doctor["id"]), email=email, activo=False, connection=conn)


def invite_account(item, medico, email=None):
    """Issue the invitation of an existing doctor account: activate it, mark it
    invited and either email the link (an email is known) or hand the link
    back to copy (no email, or the email could not go out). Never needs a
    field: name, specialty and email are all optional (the product owner, 2026-10-19)."""
    lang = i18n.active()
    email = (str(email or "").strip() or str(item.get("email") or "").strip()
             or str(medico.get("email") or "").strip() or None)
    if email and not _EMAIL_RE.fullmatch(email):
        raise ValueError(i18n.t_or("admin.srv.users.email_invalido", "Ingresá un email válido.", lang))
    portal = staff_portal.enabled()
    if portal and not email:
        # The email IS the portal match; there is no link to hand over instead.
        raise ValueError(_portal_email_needed(lang))
    if _portal_email_taken(email, True, item["usuario"]):
        raise ValueError(_portal_email_in_use(lang))
    item = db_operators.operator_update(item["usuario"], activo=True, invited=True,
                              email=email if email else db_operators.NO_CHANGE)
    # The usual 7-day window to set up 2FA starts with the invitation.
    db_auth.admin_security_ensure_deadline(item["usuario"], bool(item.get("tiene_2fa")))
    if email and medico.get("id"):
        follow_doctor_email(medico, email)
    fila = {"usuario": item["usuario"], "email_enviado": False, "cuenta_estado": item.get("cuenta_estado")}
    if email:
        enviado, _ = _send_reset(item, medico, invitacion=True)
        fila["email_enviado"] = bool(enviado)
        fila["email"] = email
    if not fila["email_enviado"] and not portal:
        # Without an email — or when it could not leave — the admin copies
        # the link and hands it over (WhatsApp, in person).
        from app.core.staff_access_email import access_link
        fila["enlace"] = access_link(item["usuario"], invitacion=True)
    return fila


def bulk_action(action, body):
    """Run one validated action for a selected set of doctors."""
    lang = i18n.active()
    ids = list(dict.fromkeys(str(x).strip() for x in (body.get("medico_ids") or [])
                             if str(x).strip()))[:200]
    if not ids:
        raise ApiError({"error": i18n.t_or("admin.srv.users.elegir_medico",
                                            "Seleccioná al menos un médico.", lang)}, status=400)
    emails = body.get("emails") or {}
    if not isinstance(emails, dict):
        raise ApiError({"error": i18n.t_or("admin.srv.users.emails_invalidos",
            "Ingresá los emails por médico.", lang), "field": "emails"}, status=400)
    link_username = (str(body.get("usuario") or "").strip() if action == "link" else "")
    if action == "link" and (len(ids) != 1 or not link_username):
        raise ApiError({"error": i18n.t_or(
            "admin.srv.users.elegir_usuario",
            "Elegí un usuario existente.", lang)}, status=400)
    if action == "create" and len(ids) != 1:
        raise ApiError({"error": i18n.t_or(
            "admin.srv.users.create_one_doctor",
            "Choose one doctor to create an account.", lang)}, status=400)
    medicos = _doctors_by_ids(ids)
    by_id = {str(m.get("id")): m for m in medicos}
    resultados = []
    for medico_id in ids:
        medico = by_id.get(medico_id)
        if not medico:
            resultados.append({"medico_id": medico_id, "ok": False,
                               "error": i18n.t_or("admin.srv.users.medico_no_encontrado",
                                                   "Médico no encontrado.", lang)})
            continue
        item = db_operators.operator_by_doctor_id(medico_id)
        try:
            if action in ("create", "invite"):
                email = str(emails.get(medico_id, body.get("email")) or
                            (item or {}).get("email") or medico.get("email") or "").strip()
                if email and not _EMAIL_RE.fullmatch(email):
                    raise ValueError(i18n.t_or("admin.srv.users.email_invalido",
                                              "Ingresá un email válido.", lang))
            if action == "link":
                if _is_owner(link_username):
                    raise ValueError(i18n.t_or(
                        "admin.srv.users.dueno_no_vinculable",
                        "El usuario dueño no se puede vincular con un médico.", lang))
                existing = db_operators.operator_get_public(link_username)
                if not existing:
                    raise ValueError(i18n.t_or(
                        "admin.srv.users.usuario_no_encontrado",
                        "Usuario no encontrado.", lang))
                if item and item.get("usuario") != link_username:
                    raise ValueError(i18n.t_or(
                        "admin.srv.users.medico_con_usuario",
                        "Este médico ya tiene otro usuario.", lang))
                linked_doctor = str(existing.get("medico_id") or "")
                if linked_doctor and linked_doctor != medico_id:
                    raise ValueError(i18n.t_or(
                        "admin.srv.users.usuario_ya_vinculado",
                        "Este usuario ya está vinculado con otro médico.", lang))
                item = db_operators.operator_update(
                    link_username, rol="medico", medico_id=medico_id, link_only=True)
                resultados.append({"medico_id": medico_id, "ok": True,
                                   "usuario": item["usuario"]})
            elif action in ("create", "reset-password") and staff_portal.enabled():
                raise ValueError(_portal_without_password(lang))
            elif action == "create":
                # «Usuario y contraseña»: the one explicit, admin-only response
                # that reveals a generated password. Every doctor already has
                # an account (provision_doctor_account); a never-used one gets
                # its password and is switched on here. A doctor from before
                # the backfill still gets a brand-new account.
                password = secrets.token_urlsafe(12)
                if item and item.get("cuenta_estado") != "sin_invitar":
                    # Only the never-used account every doctor gets is handed
                    # a password here; one somebody may already hold is not.
                    raise ValueError(i18n.t_or(
                        "admin.srv.users.medico_con_usuario",
                        "Este médico ya tiene otro usuario.", lang))
                if item:
                    item = db_operators.operator_update(item["usuario"], activo=True, password=password,
                                              invited=True,
                                              email=email if email else db_operators.NO_CHANGE)
                    db_auth.admin_security_ensure_deadline(item["usuario"], bool(item.get("tiene_2fa")))
                    usuario_creado = item["usuario"]
                else:
                    created = create_user({
                        "usuario": str(body.get("usuario") or "").strip(),
                        "password": password, "rol": "medico", "medico_id": medico_id,
                        "email": email,
                    })
                    usuario_creado = created["item"]["usuario"]
                resultados.append({"medico_id": medico_id, "ok": True,
                                   "usuario": usuario_creado,
                                   "password": password})
            elif action == "invite":
                if not item:
                    # A doctor from before the backfill: give it its account now.
                    item = provision_doctor_account(dict(medico, email=email or medico.get("email")))
                fila = dict(invite_account(item, medico, email=email), medico_id=medico_id, ok=True)
                resultados.append(fila)
            elif not item:
                raise ValueError(i18n.t_or("admin.srv.users.sin_usuario",
                                            "Este médico todavía no tiene usuario.", lang))
            elif action == "disable":
                # Misma guarda que Usuarios: el último que maneja usuarios
                # (un médico que también es administrador) no se desactiva.
                disable_user(item["usuario"])
                resultados.append({"medico_id": medico_id, "ok": True,
                                   "usuario": item["usuario"]})
            elif action == "reset-password":
                enviado, error = _send_reset(item, medico)
                if error:
                    raise ValueError(error)
                resultados.append({"medico_id": medico_id, "ok": True,
                                   "email_enviado": enviado, "usuario": item["usuario"]})
            elif action == "reset-2fa":
                db_operators.operator_update(item["usuario"], totp_secret=None)
                db_auth.admin_security_set_deadline(item["usuario"], datetime.now(timezone.utc))
                resultados.append({"medico_id": medico_id, "ok": True,
                                   "usuario": item["usuario"]})
            elif action == "reset-calendar":
                # «Cambiar la dirección del calendario»: the old /feed/doctor/<token>.ics is a 404 from now on,
                # and the doctor's next «Agregar a mi calendario» issues a new one and shows it once
                # (only its hash is stored, so a token rotated here could never be shown).
                db_operators.operator_revoke_feed_token(item["usuario"])
                resultados.append({"medico_id": medico_id, "ok": True,
                                   "usuario": item["usuario"]})
        except ApiError as exc:
            if action != "invite":
                raise
            resultados.append({"medico_id": medico_id, "ok": False, "error": exc.body["error"]})
        except db_operators.OperatorConflict as exc:
            resultados.append({"medico_id": medico_id, "ok": False,
                               "error": _operator_conflict(exc).body["error"]})
        except ValueError as exc:
            resultados.append({"medico_id": medico_id, "ok": False,
                               "error": str(exc)})
    return {
        "ok": True,
        "resultados": resultados,
        "completados": sum(1 for r in resultados if r.get("ok")),
        "errores": sum(1 for r in resultados if not r.get("ok")),
    }
