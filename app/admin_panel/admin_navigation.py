"""Server-owned direct navigation for authenticated admin staff.

The retired hash shell kept ``nav.last_screen`` in a browser preference and
trusted client code to restore it after login.  Direct screens have one safe
authority instead: ``GET /admin`` validates an explicit continuation against
the *current* role navigation, then considers a server preference containing
only a bare direct pathname, and finally selects a role default.  A disabled
module, changed role, query string, or former hash route can never be revived
from storage.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from app.admin_panel import admin_preferences
from app.core import db
from app.core import db_operators
from app.core import permissions
from app.core import screen_nav


NAV_LAST_SCREEN = "nav.last_screen"

# A continuation can carry a direct screen's selection state, but never an
# arbitrary parameter (in particular never another ``next``).  The server
# screen still validates each value when it consumes it; this boundary merely
# keeps login return navigation same-origin and canonical.
_DIRECT_PATH = re.compile(r"^/admin/[a-zA-Z0-9_-]+$")
# Legacy static defaults; see default_path_for_role() for permission-aware resolution
_LEGACY_ROLES = frozenset({"admin", "admisionista", "medico", "reportes"})
_PREFERRED_SCREEN = {
    "admin": "solicitudes",
    "admisionista": "solicitudes",
    "gestor_agendas": "solicitudes",
    "medico": "cola",
    "reportes": "reportes",
}

# Direct-screen state is intentionally path-owned.  This is not a generic
# "admin query string" allowlist: accepting ``?doc`` on Users (or ``?next``
# anywhere) would smuggle unrelated state across the authenticated boundary.
# Values are bounded wire identifiers; each owning controller performs its
# domain-level lookup/canonicalization once loaded.
_PATH_QUERY_RULES = {
    "/admin/roles": {
        "role": re.compile(r"^[a-z0-9_-]{1,80}$"),
        "new": re.compile(r"^1$"),
    },
    "/admin/account": {"setup": re.compile(r"^2fa$")},
    "/admin/legal": {"doc": re.compile(r"^[a-z0-9_-]{1,80}$")},
    "/admin/settings": {
        "field": re.compile(r"^[A-Za-z0-9_.:-]{1,120}$"),
        "view": re.compile(r"^[a-z0-9_-]{1,80}$"),
    },
    "/admin/users": {
        "user": re.compile(r"^[A-Za-z0-9_.@-]{1,120}$"),
        "new": re.compile(r"^1$"),
    },
    "/admin/consult": {"interview": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
    "/admin/my-patients": {"doctor": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
    "/admin/pacientes": {
        "patient": re.compile(r"^[0-9]{7,16}$"),
        "new": re.compile(r"^1$"),
    },
    "/admin/reportes": {"view": re.compile(r"^[a-z0-9_-]{1,80}$")},
    "/admin/plan": {"view": re.compile(r"^technical$")},
    "/admin/preparations": {"item": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
    "/admin/services": {"item": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
    "/admin/centers": {"item": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
    "/admin/doctors": {"item": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
    "/admin/knowledge": {"item": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
    "/admin/prompts": {"prompt": re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")},
}


def _decode_bounded(value: str) -> str:
    decoded = value
    for _ in range(3):
        decoded = unquote(decoded)
    return decoded


def canonical_admin_target(value: object) -> str | None:
    """Return a safe canonical local admin continuation, or ``None``.

    This syntactic validator is deliberately role-agnostic so the public
    login page can avoid echoing hostile input.  ``target_for_role`` applies
    the authoritative, current role and module checks before any redirect.
    """
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    decoded = _decode_bounded(value)
    if ("#" in value or "#" in decoded or "\\" in value or "\\" in decoded
            or "//" in value or "//" in decoded or "@" in decoded
            or ".." in decoded or any(ord(char) < 0x20 for char in decoded)):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme or parts.netloc or parts.fragment:
        return None
    if parts.path == "/admin":
        return "/admin" if not parts.query else None
    if not _DIRECT_PATH.fullmatch(parts.path):
        return None
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if len(pairs) > 8 or len(parts.query) > 256:
        return None
    allowed = _PATH_QUERY_RULES.get(parts.path, {})
    seen = set()
    for key, item in pairs:
        pattern = allowed.get(key)
        if (not pattern or key in seen or not item or len(item) > 160
                or not pattern.fullmatch(item)
                or any(ord(char) < 0x20 for char in key + item)):
            return None
        seen.add(key)
    query = urlencode(pairs, quote_via=quote, safe="-._~")
    return parts.path + ("?" + query if query else "")


def _role_ok(role: str) -> bool:
    """¿Ese rol existe y está habilitado?

    Si el almacén de roles no se puede leer, se degrada a los presets de
    fábrica: entrar al panel y ver la navegación no puede depender de que la
    tabla de roles esté disponible (misma decisión que el portón, 2026-09-12)."""
    # Un rol de fábrica vale por definición: preguntarle a la tabla gasta una
    # consulta por request para confirmar algo que ya está en el código. Sólo
    # un rol creado a mano necesita el almacén.
    if role in permissions.BUILTIN_ROLES:
        return True
    try:
        return permissions.role_is_assignable(role)
    except db.DbUnavailable:  # sólo el almacén ilegible degrada
        return False


def nav_paths(role: str, user_perms: frozenset = None) -> frozenset[str]:
    """Bare direct paths currently visible to ``role`` from shared nav.

    When ``user_perms`` is provided (a frozenset of permission ids), uses
    permission-aware navigation; otherwise uses role-based navigation.
    """
    # Check if role is valid/enabled
    if not _role_ok(role):
        return frozenset()
    # If permissions provided, use them for visibility; otherwise use role
    return frozenset(
        item["href"]
        for item in screen_nav.nav_items(role, permissions=user_perms)
    )


def target_for_role(role: str, value: object, user_perms: frozenset = None) -> str | None:
    """Role-safe explicit continuation, including the consultation deep link.

    When ``user_perms`` is provided, uses permission-aware validation.
    """
    canonical = canonical_admin_target(value)
    if not canonical or canonical in {"/admin", "/admin/login"}:
        return None
    path, _, _query = canonical.partition("?")
    if path in nav_paths(role, user_perms):
        return canonical
    # Consultation is intentionally a direct detail route rather than a
    # sidebar item. It remains a post-login continuation for roles that hold
    # the atencion_medica permission (or legacy medico/admin).
    if path == "/admin/consult" or (path == '/admin/doctors' and role == 'medico'):
        if user_perms is not None:
            return canonical if "atencion_medica" in user_perms else None
        if role in {"admin", "medico"}:
            return canonical
    return None


def saved_path_for_role(role: str, value: object, user_perms: frozenset = None) -> str | None:
    """A stored last screen is strictly a bare current navigation pathname.

    When ``user_perms`` is provided, uses permission-aware validation.
    """
    if not isinstance(value, str) or "?" in value or "#" in value:
        return None
    return value if value in nav_paths(role, user_perms) else None


def default_path_for_role(role: str, user_perms: frozenset = None) -> str | None:
    """Choose the current role's preferred visible screen, then account.

    For custom roles: prefer /admin/solicitudes if visible, else first visible
    item, else /admin/account. When ``user_perms`` is provided, uses
    permission-aware navigation.
    """
    if not _role_ok(role):
        return None
    items = screen_nav.nav_items(role, permissions=user_perms)
    preferred = _PREFERRED_SCREEN.get(role)
    # Try the preferred screen for this role
    if preferred:
        for item in items:
            if item["id"] == preferred:
                return item["href"]
    # Fall back to account if available
    for item in items:
        if item["href"] == "/admin/account":
            return item["href"]
    # Finally, return first visible item
    return items[0]["href"] if items else None


def resolve_target(identity: dict, explicit_next: object = "") -> str | None:
    """Resolve a direct post-login path without trusting stale client state.

    Computes permissions from the database for permission-aware navigation.
    Falls back to role-only behavior on any database failure.
    """
    role = str(identity.get("rol") or "")
    if not _role_ok(role):
        return None

    if role == 'medico':
        try:
            operator = db_operators.operator_get(identity.get('usuario'))
        except db.DbUnavailable:
            operator = None
        if operator and operator.get('medico_id') and not operator.get('onboarding_completed_at'):
            return '/admin/onboarding'

    # Try to get user permissions from database; fall back to role-only
    user_perms = None
    try:
        access_info = permissions.access_for(identity.get("usuario"), role)
        if access_info.get("state") == "ok":
            user_perms = access_info.get("permissions")
    except Exception:
        # Database error or user not found; fall back to role-only behavior
        pass

    supplied = str(explicit_next or "")
    # /admin is the login form's no-continuation sentinel.  Any other supplied
    # value is either a valid role-safe continuation or an invalid/tampered
    # request; the latter must *not* resurrect an old preference.
    if supplied and supplied != "/admin":
        explicit = target_for_role(role, supplied, user_perms)
        return explicit or default_path_for_role(role, user_perms)
    try:
        stored = admin_preferences.get_items(identity["usuario"]).get(NAV_LAST_SCREEN)
    except Exception:  # Preferences improve return navigation, never login availability.
        stored = None
    return saved_path_for_role(role, stored, user_perms) or default_path_for_role(role, user_perms)
