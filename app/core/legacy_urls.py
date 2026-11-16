"""legacy_urls.py — compat de las URLs viejas (español) tras el rename a
inglés del 2026-07-17 (ver rename map en la sesión que lo hizo).

Las páginas antiguas siguen con 301 mediante `core.legacy_urls_router`.
Este módulo conserva las funciones puras que traducen páginas y el inventario
histórico API/feed; no muta requests ni implementa transporte HTTP.

Varias rutas viejas no sólo mudaron de prefijo: el ÚLTIMO segmento
(verbo/tipo) también se tradujo (aprobar->approve, foto->photo, etc.) — esos
casos tienen su propia función de reescritura en vez de vivir en las tablas
genéricas de prefijo, porque un prefix-replace ciego dejaría el segmento
viejo pegado a la ruta nueva.

Los aliases API/feed no están montados en la topología nativa; sus mapeos
quedan explícitos y testeados para una futura decisión de compatibilidad.
"""

# ---------------------------------------------------------------- páginas

# Match EXACTO de path (sin barra final) -> 301.
_PAGES_EXACT = {
    "/medicos": "/doctors",
    "/centros": "/centers",
    "/novedades": "/news",
    "/mis-solicitudes": "/my-requests",
    "/cuenta/reset": "/account/reset",
    "/cuenta": "/account",
    "/preparacion": "/upload-order",
    "/acerca.html": "/about.html",
    "/privacidad.html": "/privacy.html",
    "/terminos.html": "/terms.html",
}

# JS renombrados (2026-07-29, rename a inglés — ver docs/rename-map.md §2):
# match EXACTO -> 301, mismo mecanismo que _PAGES_EXACT (un <script src>
# sigue el 301 sin problema). Sin esto, un admin.html cacheado por Cloudflare
# hasta 4h pide el nombre viejo y el archivo ya no existe -> 404 duro.
_PAGES_EXACT.update({
    "/admin-tabla.js": "/admin-table.mjs",
})

# /services never was a page, but people type it and links in the wild point
# at it (demo review 2026-11-15: it answered 404). The studies section is the
# page the header calls «Estudios». english-route-guard: allow legacy
_PAGES_EXACT.update({
    "/services": "/d/diagnostico",
})

# These aliases target root admin bundles that now have content-hash URLs.
# An old cached shell must redirect to the CURRENT immutable URL, not to a
# second unversioned shape that can drift underneath the old query string.
# Prefijo viejo -> prefijo nuevo (el resto del path, <id>/<slug>/<token>,
# se preserva tal cual) -> 301.
_PAGES_PREFIX = [
    ("/medico/", "/doctor/"),
    ("/centro/", "/center/"),
    ("/estudio/", "/study/"),
    ("/solicitud/", "/request/"),
    ("/turno/oferta/", "/appointment/offer/"),
]


def _rewrite_servicio_page(path):
    """/servicio/<id> -> /service/<id>; /servicio/<id>/turno -> /service/<id>/book."""
    if not path.startswith("/servicio/"):
        return None
    rest = path[len("/servicio/"):]
    if rest.endswith("/turno"):
        return "/service/" + rest[: -len("/turno")] + "/book"
    return "/service/" + rest


def _new_page_path(path):
    """path viejo (sin query) -> path nuevo si matchea una página vieja, o None."""
    rewritten = _rewrite_servicio_page(path)
    if rewritten is not None:
        return rewritten
    stripped = path.rstrip("/") or "/"
    new = _PAGES_EXACT.get(stripped)
    if new is not None:
        return new
    for old, new in _PAGES_PREFIX:
        if stripped.startswith(old):
            return new + stripped[len(old):]
    return None


# ---------------------------------------------------------------- API / feeds

# Match EXACTO de path -> nuevo (sin cola dinámica).
_API_EXACT = {
    "/api/servicios": "/api/services",
    "/api/medicos": "/api/doctors",
    "/api/centros": "/api/centers",
    "/api/directorio": "/api/directory",
    "/api/solicitudes": "/api/requests",
    "/api/analizar-orden": "/api/analyze-order",
    "/api/admin/tema": "/api/admin/theme",
    "/api/admin/solicitudes": "/api/admin/requests",
    "/api/admin/usuarios": "/api/admin/users",
    "/api/admin/mis-turnos": "/api/admin/my-appointments",
    "/api/admin/mis-turnos/feed": "/api/admin/my-appointments/feed",
    "/api/admin/medicos/stats": "/api/admin/doctors/stats",
    "/api/admin/costos": "/api/admin/costs",
    "/api/buscar-log": "/api/search-log",
    "/api/ia-estado": "/api/ai-status",
    "/api/calendario/feed": "/api/calendar/feed",
    "/api/turno-hold": "/api/slot-hold",
    # Rename residual del 2026-07-30 (ver docs/rename-map.md §4) — estos ya
    # tenían el prefijo en inglés, sólo cambió el último segmento.
    "/api/preparaciones": "/api/preparation-studies",
    "/api/especialidades": "/api/specialties",
    "/api/admin/hsi-sync/conflictos": "/api/admin/hsi-sync/conflicts",
    "/api/admin/reports/resenas": "/api/admin/reports/reviews",
    "/api/admin/reports/recupero": "/api/admin/reports/cost-recovery",
    "/api/admin/reports/recupero.csv": "/api/admin/reports/cost-recovery.csv",
    "/api/admin/reports/estadistica": "/api/admin/reports/statistics",
    "/api/admin/reports/estadistica.csv": "/api/admin/reports/statistics.csv",
    "/api/admin/reports/capacidad": "/api/admin/reports/capacity",
}

# Prefijo viejo -> prefijo nuevo, cola (id/slug/token/extensión) intacta —
# sólo para los casos donde ningún segmento de la cola cambia de palabra.
_API_PREFIX = [
    ("/api/servicio-turnos/", "/api/service-slots/"),
    ("/api/admin/orden/", "/api/admin/order/"),
    ("/api/admin/usuarios/", "/api/admin/users/"),
    ("/api/turno-hold/", "/api/slot-hold/"),
    ("/api/turno/", "/api/appointment/"),
    ("/medico-foto/", "/doctor-photo/"),
]

_FEED_PREFIX = [
    ("/feed/turnos/", "/feed/appointments/"),
    ("/feed/medico/", "/feed/doctor/"),
]

# /api/cuenta/<resto> -> /api/account/<resto-traducido>. Todas las rutas de
# cuenta son estáticas (sin id dinámico en el path), así que un dict alcanza.
_CUENTA_SUFFIX = {
    "yo": "me",
    "dependientes": "dependents",
    "dependientes/eliminar": "dependents/delete",
    "registro": "register",
    "salir": "logout",
    "recuperar": "recover",
    "actualizar": "update",
    "otp/enviar": "otp/send",
    "otp/verificar": "otp/verify",
    "foto": "photo",
    "mcp/regenerar": "mcp/regenerate",
    "mcp": "mcp",
    "config": "config",
    "login": "login",
    "reset": "reset",
    "reset_otp": "reset_otp",
}


def _rewrite_cuenta_api(path):
    prefix = "/api/cuenta/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    new_rest = _CUENTA_SUFFIX.get(rest)
    if new_rest is None:
        return None
    return "/api/account/" + new_rest


def _rewrite_with_tail(path, old, new, tails, bare=True):
    """`old<rest>` -> `new<rest>` with the closing segment of `rest` renamed through `tails` ({old tail: new
    tail}). None when `path` does not start with `old`; a `rest` that ends in none of the tails keeps its
    ending (`bare=False`: it is not this family's path and gives None)."""
    if not path.startswith(old):
        return None
    rest = path[len(old):]
    for old_tail, new_tail in tails.items():
        if rest.endswith(old_tail):
            return new + rest[: -len(old_tail)] + new_tail
    return new + rest if bare else None


def _rewrite_solicitudes_api(path):
    """/api/solicitudes/<id>[/cancelar|/lista-espera] -> /api/requests/<id>[/cancel|/waitlist]."""
    return _rewrite_with_tail(path, "/api/solicitudes/", "/api/requests/",
                              {"/cancelar": "/cancel", "/lista-espera": "/waitlist"})


def _rewrite_admin_solicitudes_api(path):
    """/api/admin/solicitudes/<id>[/aprobar|/rechazar] -> /api/admin/requests/<id>[/approve|/reject]."""
    return _rewrite_with_tail(path, "/api/admin/solicitudes/", "/api/admin/requests/",
                              {"/aprobar": "/approve", "/rechazar": "/reject"})


# tipo de catalog.py (español, valor de URL vieja) -> segmento inglés nuevo
# (mismo mapeo que admin_content.TYPE_MAP, invertido).
_TIPO_LEGACY = {"centros": "centers", "servicios": "services", "medicos": "doctors"}


def _rewrite_contenido_api(path):
    """/api/admin/contenido/<tipo-esp>[/<id>[/restaurar]] ->
    /api/admin/content/<tipo-en>[/<id>[/restaurar]]."""
    prefix = "/api/admin/contenido/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    tipo_old, _, tail = rest.partition("/")
    tipo_new = _TIPO_LEGACY.get(tipo_old, tipo_old)
    return "/api/admin/content/" + tipo_new + ("/" + tail if tail else "")


def _rewrite_medicos_admin_api(path):
    """/api/admin/medicos/<id>/turnos -> /api/admin/doctors/<id>/appointments;
    /api/admin/medicos/<id>/foto -> /api/admin/doctors/<id>/photo."""
    return _rewrite_with_tail(path, "/api/admin/medicos/", "/api/admin/doctors/",
                              {"/turnos": "/appointments", "/foto": "/photo"})


def _rewrite_reportes_api(path):
    prefix = "/api/admin/reportes"
    if path == prefix:
        return "/api/admin/reports"
    if path == prefix + "/solicitudes.csv":
        return "/api/admin/reports/requests.csv"
    if path == prefix + "/pacientes":
        return "/api/admin/reports/patients"
    if path.startswith(prefix + "/"):
        return "/api/admin/reports/" + path[len(prefix) + 1:]
    return None


def _rewrite_oferta_api(path):
    """/api/oferta/<t>/aceptar|rechazar -> /api/offer/<t>/accept|reject."""
    return _rewrite_with_tail(path, "/api/oferta/", "/api/offer/",
                              {"/aceptar": "/accept", "/rechazar": "/reject"}, bare=False)


_API_REWRITERS = (
    _rewrite_cuenta_api,
    _rewrite_solicitudes_api,
    _rewrite_admin_solicitudes_api,
    _rewrite_contenido_api,
    _rewrite_medicos_admin_api,
    _rewrite_reportes_api,
    _rewrite_oferta_api,
)


def _new_api_path(path):
    """path viejo -> path nuevo si matchea una API/feed vieja, o None."""
    new = _API_EXACT.get(path)
    if new is not None:
        return new
    for fn in _API_REWRITERS:
        new = fn(path)
        if new is not None:
            return new
    for old, new in _API_PREFIX:
        if path.startswith(old):
            return new + path[len(old):]
    for old, new in _FEED_PREFIX:
        if path.startswith(old):
            return new + path[len(old):]
    return None
