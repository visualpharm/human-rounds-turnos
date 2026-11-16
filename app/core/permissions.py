"""core/permissions.py — what each staff account may do in the panel.

One module owns the whole access model (R5/R6, 2026-09-10):

* ``PERMISSIONS`` — the closed catalog. Each id reads as ONE full plain-Spanish
  sentence saying what the person can do; the sentence is the UI label.
* ``BUILTIN_ROLES`` — the presets. ``admin``/``medico``/``admisionista``/
  ``reportes`` reproduce, route by route, the flag-based gate that existed
  before this change (``tests/test_roles_parity.py`` walks every gated route
  and proves it). ``gestor_agendas`` is admission desk plus the doctor editor.
* ``required_permission`` — the route -> permission map the central gate
  (``auth.admin_auth.evaluate_admin_gate``) consults. ``ANY`` means "any
  enabled staff role" (the self-service surfaces: own account, passkeys,
  preferences, whoami, the public catalog lists). A gated route missing from
  the map is ``UNMAPPED``: the gate then falls back to the legacy flags for
  the four legacy roles and denies every other role (fail closed), and the
  parity test fails the build until the route is mapped.
* Storage: ``roles`` (one row per role, keyed by ``slug`` — the value the
  session cookie carries) and ``operator_access`` (per-account role slug when
  it is not one of the four legacy values ``operators.role`` can hold, plus
  the per-user grant/deny overrides). ``operators`` itself is untouched so the
  pre-Alembic shape proof keeps its frozen ``operators_role_check``.

Effective permissions = role preset + granted - denied. The built-in ``admin``
role always holds every permission (a permission added later reaches it
without a data migration) and cannot be edited or disabled; the owner (env
login) always holds every permission without touching the database.
"""

import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_operators
from app.core import i18n
from app.core import schema_model as sm
from app.core import sqlcore
from app.core import text_fold


# ---- catalog ---------------------------------------------------------------

# Display order = this order. Spanish is the canonical fallback; the English
# rendering lives in locales/en/admin.roles.json under admin.roles.perm.<id>.
PERMISSIONS = {
    "solicitudes": "Puede ver las solicitudes de turno y aprobarlas, rechazarlas o reasignarlas.",
    "cola": "Puede ver los pacientes de hoy y marcar quién llegó.",
    "crear_turnos": "Puede buscar o registrar pacientes y darles turnos desde el panel.",
    "pacientes": "Puede abrir la ficha de un paciente y corregir sus datos y su grupo familiar.",
    "historia_clinica": "Puede agregar antecedentes a la historia clínica de un paciente.",
    "atencion_medica": "Puede atender como médico: ver sus turnos, sus pacientes y las entrevistas previas.",
    "todos_los_pacientes": "Puede ver los pacientes de todos los médicos, no sólo los del médico de su cuenta (jefatura de servicio, auditoría). Cada acceso queda registrado.",
    "medicos": "Puede editar los médicos: sus horarios, sus días de atención y sus cierres de agenda.",
    "servicios": "Puede editar los servicios, los centros de salud y las preparaciones para estudios.",
    "reportes": "Puede ver los informes y las estadísticas.",
    "asistente": "Puede editar lo que sabe el asistente y cómo responde, y leer sus conversaciones.",
    "apariencia": "Puede cambiar el aspecto del sitio y la forma de ingresar al panel.",
    "configuracion": "Puede cambiar la configuración del sitio: idiomas, ubicación y claves de servicios externos.",
    "hsi": "Puede revisar y resolver la sincronización con HSI.",
    "sistema": "Puede ver el estado del sistema y la hoja de ruta.",
    "legal": "Puede editar los términos y condiciones y la política de privacidad.",
    "usuarios": "Puede crear usuarios, elegir qué puede hacer cada uno y editar los roles.",
}
ALL = frozenset(PERMISSIONS)

_ADMISION = ("solicitudes", "cola", "crear_turnos", "pacientes")

# slug -> preset. `name`/`description` are the seeded Spanish defaults; the
# rendered text goes through the users screen's existing catalog keys
# (admin.system.users.role.<slug>.label/.desc) while the stored text is still
# the default, and shows the stored text verbatim once an admin edits it.
BUILTIN_ROLES = {
    "admin": {
        "name": "Administrador",
        "description": "Puede hacer todo: atención, catálogo, usuarios, configuración y reportes.",
        "permissions": tuple(PERMISSIONS),
    },
    "admisionista": {
        "name": "Mesa de admisión",
        "description": "Ve solicitudes y pacientes de hoy. Puede crear pacientes y turnos.",
        "permissions": _ADMISION,
    },
    "gestor_agendas": {
        "name": "Gestor de agendas",
        "description": "Atiende la mesa de admisión y además arma las agendas de los médicos: horarios, días de atención y cierres.",
        "permissions": _ADMISION + ("medicos",),
    },
    "medico": {
        "name": "Médico",
        "description": "Ve sus pacientes de hoy y sus turnos. Revisa sus datos, su foto y sus agendas.",
        "permissions": ("solicitudes", "cola", "crear_turnos", "atencion_medica"),
    },
    "reportes": {
        "name": "Ve reportes y estadísticas",
        "description": "Sólo entra a Reportes: ocupación, tiempos de atención y estadística mensual.",
        "permissions": ("reportes",),
    },
}

# The four values `operators.role` (and its frozen CHECK) can hold. Any other
# slug is stored in `operator_access.role_slug` over the base below.
LEGACY_ROLES = ("admin", "medico", "admisionista", "reportes")
BASE_ROLE_FOR_OTHER_SLUGS = "admisionista"
# Retired session roles: a cookie still carrying one is not a session (401),
# exactly as before this change.
RETIRED_ROLES = frozenset({"operador", "marca"})
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def permission_label(pid, lang=None):
    return i18n.t_or("admin.roles.perm." + pid, PERMISSIONS[pid], lang or i18n.active())


def permissions_payload(lang=None):
    """[{id, label}] in display order, for the users and roles screens."""
    return [{"id": pid, "label": permission_label(pid, lang)} for pid in PERMISSIONS]


# ---- route -> permission ---------------------------------------------------

ANY = "*"             # any enabled staff role
UNMAPPED = None       # not in the map: legacy flags decide, others denied
# A map value may also be a frozenset of permission ids: ANY ONE of them
# admits the route ("cualquiera de"). Always test with `holds`.


def holds(required, held):
    """True when ``held`` satisfies a mapped requirement: ``ANY``, a single
    permission id, or a frozenset of alternatives (any one is enough).
    ``UNMAPPED`` is never held — the caller applies the legacy rule."""
    if required is UNMAPPED:
        return False
    if required == ANY:
        return True
    held = frozenset(held or ())
    if isinstance(required, frozenset):
        return bool(required & held)
    return required in held

# Matched against the ROUTE TEMPLATE (FastAPI's `route.path`), longest prefix
# first, on a segment boundary. Keep it grouped by permission.
_PREFIXES = {
    # self-service: every staff role reaches its own account and preferences
    "/admin/account": ANY,
    "/api/admin/account": ANY,
    "/api/admin/passkeys": ANY,
    "/api/admin/preferences": ANY,
    "/api/admin/whoami": ANY,
    # the desk's HSI extension (guide, ZIP): wherever «Cargar en HSI» shows
    "/api/admin/hsi-extension": ANY,
    # «Descargar» of a small table: the SCREEN it comes from then decides
    # with its own permission (admin_panel/table_export.py)
    "/api/admin/table-export": ANY,
    # solicitudes
    "/admin/solicitudes": "solicitudes",
    "/api/admin/requests": "solicitudes",
    "/api/admin/order": "solicitudes",
    # cola
    "/admin/cola": "cola",
    "/api/admin/day-queue": "cola",
    "/api/admin/requests/{req_id}/attendance": "cola",
    # crear_turnos
    "/api/admin/appointment-slots": "crear_turnos",
    "/api/admin/appointments": "crear_turnos",
    "/api/admin/refeps": "crear_turnos",
    "/api/admin/patients": "crear_turnos",
    # a new temporary password: the same hands that create the patient (TASK-50)
    "/api/admin/patients/{dni}/temp-password": "crear_turnos",
    # pacientes
    "/admin/pacientes": "pacientes",
    "/api/admin/patients/{dni}": "pacientes",
    # the desk's decision on a document-number claim (TASK-68): same hands as creating a patient
    "/api/admin/dni-claims": "crear_turnos",
    "/api/admin/dni-claims/{claim_id}/resolve": "crear_turnos",
    # historia_clinica
    "/api/admin/patients/{dni}/clinical-history": "historia_clinica",
    # atencion_medica
    "/admin/consult": "atencion_medica",
    "/admin/onboarding": "atencion_medica",
    "/admin/my-schedules": "atencion_medica",
    "/api/admin/onboarding": "atencion_medica",
    "/api/admin/doctor-profile": "atencion_medica",
    "/admin/mis-turnos": "atencion_medica",
    "/admin/my-patients": "atencion_medica",
    "/api/admin/doctor-patients": "atencion_medica",
    "/api/admin/intake/alerts": "atencion_medica",
    "/api/admin/intake/consult": "atencion_medica",
    "/api/admin/intake/door": "atencion_medica",
    "/api/admin/intake/my-settings": "atencion_medica",
    "/api/admin/modules": "atencion_medica",
    "/api/admin/my-appointments": "atencion_medica",
    "/api/admin/patient-summary": "atencion_medica",
    "/api/mi/foto": "atencion_medica",
    # medicos
    "/admin/doctors": "medicos",
    "/api/admin/doctors/{doctor}": "medicos",
    # «Cargar lista»: the same hands as creating one doctor (2026-11-03)
    "/api/admin/doctors/import": "medicos",
    # servicios
    "/admin/services": "servicios",
    "/admin/centers": "servicios",
    "/admin/preparations": "servicios",
    # reportes
    "/admin/reportes": "reportes",
    "/api/admin/reports": "reportes",
    "/api/admin/costs": "reportes",
    # Métricas de la lista de Médicos (próximo día, horas/semana, turnos
    # ofrecidos, ocupación — sin pacientes): datos de agenda, así que las lee
    # quien arma agendas O quien ve reportes (hallazgo K8, 2026-10-23).
    "/api/admin/doctors/stats": frozenset({"medicos", "reportes"}),
    # asistente
    "/admin/knowledge": "asistente",
    "/admin/prompts": "asistente",
    "/admin/chats": "asistente",
    "/admin/entrevistas": "asistente",
    "/api/admin/prompts": "asistente",
    "/api/admin/intake/settings": "asistente",
    # apariencia
    "/admin/appearance": "apariencia",
    "/api/admin/theme": "apariencia",
    "/api/admin/branding": "apariencia",
    "/api/admin/auth-mode": "apariencia",
    "/api/admin/turnstile": "apariencia",
    "/api/admin/chat-voice": "apariencia",
    # configuracion
    "/admin/settings": "configuracion",
    "/api/admin/settings": "configuracion",
    # hsi
    "/admin/hsi": "hsi",
    "/api/admin/hsi-sync": "hsi",
    "/api/admin/requests/{req_id}/retry-hsi": "hsi",
    # sistema
    "/admin/system": "sistema",
    "/api/admin/system": "sistema",
    # The state of HSI (mode, reason, institution numbers) is read by «Sistema»
    # and by the `/admin/hsi` screen: either permission opens it.
    "/api/admin/system/hsi": frozenset({"sistema", "hsi"}),
    "/admin/plan": "sistema",
    # legal
    "/admin/legal": "legal",
    "/api/admin/legal": "legal",
    # usuarios
    "/admin/users": "usuarios",
    "/api/admin/users": "usuarios",
    "/admin/roles": "usuarios",
    "/api/admin/roles": "usuarios",
    "/api/admin/permissions": "usuarios",
    # who opened a patient's clinical data (K10): the one who decides who
    # may access what audits it
    "/api/admin/patients/{dni}/access-log": "usuarios",
    # the copy of ALL a person's data (Ley 25.326 art. 14): same hands
    "/api/admin/patients/{dni}/data-export": "usuarios",
}
_PREFIX_ORDER = sorted(_PREFIXES, key=len, reverse=True)

# Catalog editors share one route per shape; the URL type decides.
_CONTENT_TYPES = {
    "doctors": "medicos",
    "services": "servicios",
    "centers": "servicios",
    "preparations": "servicios",
    "knowledge": "asistente",
}
_PHOTO_ENTITIES = {"doctors": "medicos", "centers": "servicios"}


def required_permission(method, template, path=None, role=None):
    """The permission a request needs, ``ANY``, or ``UNMAPPED``.

    ``template`` is the matched route template; ``path`` the concrete path
    (only the catalog-editor and photo routes read a path segment)."""
    template = str(template or "")
    concrete = str(path or template).split("?", 1)[0]
    # The router/domain still enforce the session's linked doctor id. This
    # grants the doctor's own editor, never the general catalog capability.
    if role == 'medico' and (
            concrete == '/admin/doctors'
            or (method == 'PUT' and concrete.startswith('/api/admin/content/doctors/'))
            or (template == '/api/admin/{entity}/{eid}/photo' and concrete.startswith('/api/admin/doctors/'))):
        return 'atencion_medica'
    method = str(method or "GET").upper()
    if template.startswith("/api/admin/content/{tipo}"):
        parts = concrete.strip("/").split("/")
        tipo = parts[3] if len(parts) > 3 else ""
        needed = _CONTENT_TYPES.get(tipo)
        if needed is None:
            return UNMAPPED
        # The catalog LIST is read by every staff screen (pickers, filters);
        # the knowledge base is not public.
        if method == "GET" and len(parts) == 4 and tipo != "knowledge":
            return ANY
        return needed
    if template == "/api/admin/{entity}/{eid}/photo":
        parts = concrete.strip("/").split("/")
        return _PHOTO_ENTITIES.get(parts[2] if len(parts) > 2 else "", UNMAPPED)
    for prefix in _PREFIX_ORDER:
        if template == prefix or template.startswith(prefix + "/"):
            return _PREFIXES[prefix]
    return UNMAPPED


def legacy_denial(rol, solo_admin=False, permitir_medico=False,
                  permitir_admisionista=False, permitir_reportes=False):
    """The flag-based decision the gate made before roles were editable, for
    the four legacy roles: ``None`` = allowed, else the error body it sent.
    Used only for UNMAPPED routes and to keep the same error text when both
    systems deny."""
    if rol == "reportes" and not permitir_reportes:
        return "rol_no_autorizado"
    if solo_admin and rol != "admin" and not (rol == "reportes" and permitir_reportes):
        return "solo_admin"
    if rol == "medico" and not permitir_medico:
        return "rol_no_autorizado"
    if rol == "admisionista" and not permitir_admisionista:
        return "rol_no_autorizado"
    if rol not in LEGACY_ROLES:
        return "rol_no_autorizado"
    return None


# ---- effective access --------------------------------------------------------

def effective(slug, role_permissions, granted=(), denied=()):
    """Role preset + granted - denied, restricted to the known catalog."""
    base = ALL if slug == "admin" else frozenset(role_permissions or ()) & ALL
    return frozenset((base | (frozenset(granted or ()) & ALL)) - frozenset(denied or ()))


# Built once: `access_for` runs on every panel request (see `sqlcore.Prepared`). The parameters are bound
# values, so a None username or slug is `= NULL` and finds nothing, as the hand-written SQL did.
_ACCESS_OPERATOR = sqlcore.prepare(
    sa.select(sm.operators.c.role, sm.operators.c.active, sm.operator_access.c.role_slug,
              sm.operator_access.c.granted, sm.operator_access.c.denied,
              ).select_from(sm.operators.outerjoin(
                  sm.operator_access, sm.operator_access.c.username == sm.operators.c.username,
              )).where(sm.operators.c.username == sa.bindparam("usuario", type_=sa.Text)))
_ACCESS_ROLE = sqlcore.prepare(
    sa.select(sm.roles.c.enabled, sm.roles.c.permissions).where(
        sm.roles.c.slug == sa.bindparam("slug", type_=sa.Text)))


def access_for(usuario, token_rol):
    """Current role and effective permissions of a signed-in staff account.

    Returns ``{"slug", "state", "permissions"}`` with ``state`` one of
    ``ok``/``unknown``/``disabled``. The account's CURRENT role comes from the
    database (a role change applies on the next request, not at the next
    login); a session with no operator row keeps its signed role with no
    overrides. Raises on any database failure — the gate turns that into a
    503, never into an allow."""
    with db._connect() as conn, conn.cursor() as cur:
        row = _ACCESS_OPERATOR.run(cur, usuario=usuario).fetchone()
        if row and not row["active"]:
            # A deactivated account ends its open sessions on the very next
            # request (prod finding 2026-10-19: a doctor disabled from Médicos
            # kept using the panel until the cookie expired).
            return {"slug": row["role"], "state": "inactive", "permissions": frozenset()}
        if row:
            slug = row["role_slug"] or row["role"]
            granted, denied = row["granted"] or [], row["denied"] or []
        else:
            slug, granted, denied = token_rol, [], []
        role = _ACCESS_ROLE.run(cur, slug=slug).fetchone()
    if role is None:
        return {"slug": slug, "state": "unknown", "permissions": frozenset()}
    if not role["enabled"]:
        return {"slug": slug, "state": "disabled", "permissions": frozenset()}
    return {"slug": slug, "state": "ok",
            "permissions": effective(slug, role["permissions"], granted, denied)}


# ---- roles storage -----------------------------------------------------------

class RoleError(ValueError):
    """A validation failure with a wire code, a Spanish message and a field."""

    def __init__(self, code, message, field=None, status=400):
        super().__init__(message)
        self.code, self.field, self.status = code, field, status


def _role_public(row, lang=None, users=0):
    lang = lang or i18n.active()
    slug = row["slug"]
    preset = BUILTIN_ROLES.get(slug) if row["builtin"] else None
    name, description = row["name"], row["description"]
    if preset and name == preset["name"]:
        name = i18n.t_or("admin.system.users.role.%s.label" % slug, name, lang)
    if preset and description == preset["description"]:
        description = i18n.t_or("admin.system.users.role.%s.desc" % slug, description, lang)
    perms = ALL if slug == "admin" else frozenset(row["permissions"] or ()) & ALL
    return {
        "slug": slug,
        "name": name,
        "description": description,
        "builtin": bool(row["builtin"]),
        "enabled": bool(row["enabled"]),
        "locked": slug == "admin",
        "permissions": [p for p in PERMISSIONS if p in perms],
        "users": users,
    }


def _role_user_counts(cur):
    o, a = sm.operators, sm.operator_access
    slug = sa.func.coalesce(a.c.role_slug, o.c.role)
    rows = sqlcore.run(cur, sa.select(slug.label("slug"), sa.func.count().label("n"))
                       .select_from(o.outerjoin(a, a.c.username == o.c.username)).group_by(slug)).fetchall()
    return {r["slug"]: r["n"] for r in rows}


def list_roles(enabled_only=False, lang=None):
    """Built-ins first in preset order, then custom roles by name."""
    order = {slug: i for i, slug in enumerate(BUILTIN_ROLES)}
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(sm.roles)).fetchall()
        counts = _role_user_counts(cur)
    rows = [r for r in rows if r["enabled"] or not enabled_only]
    rows.sort(key=lambda r: (order.get(r["slug"], len(order)), r["name"].lower()))
    return [_role_public(r, lang, counts.get(r["slug"], 0)) for r in rows]


def get_role(slug, lang=None):
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sm.roles).where(sm.roles.c.slug == slug)).fetchone()
        counts = _role_user_counts(cur) if row else {}
    return _role_public(row, lang, counts.get(slug, 0)) if row else None


def role_is_assignable(slug):
    """True when `slug` names an ENABLED role (the users form only offers those)."""
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        return False
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sm.roles.c.enabled).where(sm.roles.c.slug == slug)).fetchone()
    return bool(row and row["enabled"])


def _clean_permissions(values):
    if not isinstance(values, (list, tuple)):
        raise RoleError("permisos_invalidos", "Elegí qué puede hacer este rol.", "permissions")
    out = [str(v) for v in values]
    unknown = [p for p in out if p not in PERMISSIONS]
    if unknown:
        raise RoleError("permiso_desconocido", "Hay un permiso que no existe.", "permissions")
    return [p for p in PERMISSIONS if p in set(out)]


def _clean_text(value, field, required, limit):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if required and len(text) < 2:
        message = ("Escribí un nombre para el rol." if field == "name"
                   else "Escribí qué hace una persona con este rol.")
        raise RoleError("texto_requerido", message, field)
    if len(text) > limit:
        raise RoleError("texto_largo", "Ese texto es demasiado largo.", field)
    return text


def _slugify(name):
    ascii_name = text_fold.ascii_fold(name)
    base = re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:30] or "rol"
    return "rol_" + base if not base[0].isalpha() or base in BUILTIN_ROLES \
        or base in RETIRED_ROLES else base


def create_role(name, description, permissions):
    name = _clean_text(name, "name", True, 60)
    description = _clean_text(description, "description", True, 300)
    perms = _clean_permissions(permissions)
    with db._connect() as conn, conn.cursor() as cur:
        existing = sqlcore.run(cur, sa.select(sm.roles.c.slug, sa.func.lower(sm.roles.c.name).label("lname"))).fetchall()
        if any(r["lname"] == name.lower() for r in existing):
            raise RoleError("rol_existe", "Ya hay un rol con ese nombre.", "name", 409)
        taken = {r["slug"] for r in existing}
        base = slug = _slugify(name)
        n = 2
        while slug in taken:
            slug = "%s_%d" % (base[:36], n)
            n += 1
        sqlcore.run(cur, sa.insert(sm.roles).values(
            slug=slug, name=name, description=description, builtin=sa.false(), enabled=sa.true(),
            permissions=perms))
    return get_role(slug)


def update_role(slug, body):
    """Edit name/description/permissions/enabled. The built-in admin role is
    locked; a built-in role can be disabled but never renamed away from its
    slug. Refuses any change that would leave no active account able to
    manage users."""
    current = get_role(slug)
    if current is None:
        raise RoleError("no_encontrado", "Ese rol no existe.", None, 404)
    if current["locked"]:
        raise RoleError("rol_bloqueado", "El rol Administrador no se edita: siempre puede hacer todo.")
    values = {}
    new_perms, new_enabled = None, None
    if "name" in body:
        name = _clean_text(body.get("name"), "name", True, 60)
        with db._connect() as conn, conn.cursor() as cur:
            taken = sqlcore.run(cur, sa.select(sa.literal(1)).select_from(sm.roles).where(
                sa.func.lower(sm.roles.c.name) == sa.func.lower(name), sm.roles.c.slug != slug)).fetchone()
            if taken:
                raise RoleError("rol_existe", "Ya hay un rol con ese nombre.", "name", 409)
        values["name"] = name
    if "description" in body:
        values["description"] = _clean_text(body.get("description"), "description", True, 300)
    if "permissions" in body:
        new_perms = _clean_permissions(body.get("permissions"))
        values["permissions"] = new_perms
    if "enabled" in body:
        new_enabled = bool(body.get("enabled"))
        values["enabled"] = new_enabled
    if not values:
        return current
    if new_perms is not None or new_enabled is not None:
        assert_user_manager_remains(role_change={
            "slug": slug, "permissions": new_perms, "enabled": new_enabled})
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.update(sm.roles).where(sm.roles.c.slug == slug).values(
            **values, updated=sa.func.now()))
    return get_role(slug)


def delete_role(slug):
    """Only a custom role nobody holds can be deleted; everything else is
    disabled instead (Deshabilitar, never eliminar)."""
    current = get_role(slug)
    if current is None:
        raise RoleError("no_encontrado", "Ese rol no existe.", None, 404)
    if current["builtin"]:
        raise RoleError("rol_de_fabrica", "Los roles de fábrica no se borran: se pueden desactivar.")
    if current["users"]:
        raise RoleError("rol_en_uso", "Hay usuarios con este rol. Cambiales el rol o desactivalo.")
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(sm.roles).where(sm.roles.c.slug == slug, sm.roles.c.builtin == sa.false()))


# ---- per-account overrides -------------------------------------------------

def clean_overrides(granted, denied):
    """Validated (granted, denied) lists; a permission can't be in both."""
    g = _clean_permissions(granted if granted is not None else [])
    d = _clean_permissions(denied if denied is not None else [])
    if set(g) & set(d):
        raise RoleError("override_conflicto", "Un permiso no puede estar agregado y quitado a la vez.",
                        "overrides")
    return g, d


def _snapshot(cur):
    o, a, r = sm.operators, sm.operator_access, sm.roles
    ops = [dict(row) for row in sqlcore.run(cur, sa.select(
        o.c.username, o.c.active, o.c.role, a.c.role_slug, a.c.granted, a.c.denied,
    ).select_from(o.outerjoin(a, a.c.username == o.c.username))).fetchall()]
    roles = {row["slug"]: dict(row) for row in sqlcore.run(
        cur, sa.select(r.c.slug, r.c.enabled, r.c.permissions)).fetchall()}
    return ops, roles


def _manager_count(ops, roles, operator_change=None, role_change=None):
    roles = {k: dict(v) for k, v in roles.items()}
    if role_change:
        target = roles.get(role_change["slug"])
        if target is not None:
            if role_change.get("permissions") is not None:
                target["permissions"] = role_change["permissions"]
            if role_change.get("enabled") is not None:
                target["enabled"] = role_change["enabled"]
    count = 0
    for op in ops:
        state = {"active": op["active"], "slug": op["role_slug"] or op["role"],
                 "granted": op["granted"] or [], "denied": op["denied"] or []}
        if operator_change and operator_change.get("usuario") == op["username"]:
            state.update({k: v for k, v in operator_change.items()
                          if k != "usuario" and v is not None})
        role = roles.get(state["slug"])
        if not state["active"] or not role or not role["enabled"]:
            continue
        if "usuarios" in effective(state["slug"], role["permissions"],
                                   state["granted"], state["denied"]):
            count += 1
    return count


def assert_user_manager_remains(operator_change=None, role_change=None):
    """Refuse a change that takes the last active account able to manage users
    down to none. `operator_change`: {usuario, slug?, granted?, denied?,
    active?}; `role_change`: {slug, permissions?, enabled?}. The owner (env
    login) is not counted: the hospital must not depend on it."""
    with db._connect() as conn, conn.cursor() as cur:
        ops, roles = _snapshot(cur)
    before = _manager_count(ops, roles)
    after = _manager_count(ops, roles, operator_change, role_change)
    if before > 0 and after == 0:
        raise RoleError(
            "ultimo_gestor_usuarios",
            "Tiene que quedar al menos un usuario activo que pueda crear usuarios y editar roles.",
            "overrides" if operator_change else "permissions", 409)


def get_overrides(usuario):
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sm.operator_access.c.granted, sm.operator_access.c.denied).where(
            sm.operator_access.c.username == usuario)).fetchone()
    if not row:
        return [], []
    return list(row["granted"] or []), list(row["denied"] or [])


def set_overrides(usuario, granted, denied, connection=None):
    """Set per-user grant/deny overrides. A permission can't be both granted and denied.
    `connection`: write inside the caller's open transaction (an account created
    together with its overrides — scripts/fixes/staff_transfer.py)."""
    granted, denied = clean_overrides(granted, denied)
    with db_operators.connection_scope(connection) as conn, conn.cursor() as cur:
        t = sm.operator_access
        insert = _pg.insert(t).values(username=usuario, granted=list(granted), denied=list(denied))
        sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.username],
            set_={"granted": insert.excluded.granted, "denied": insert.excluded.denied,
                  "updated": sa.func.now()}))
