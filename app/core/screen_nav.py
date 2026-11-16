"""core/screen_nav.py — the ONE shared builder of the server-routed admin
screens' sidebar navigation (`templates/admin/base.html` loops its
`[{id, href, label}, ...]` output as `nav_items`).

Originally extracted from the six byte-identical `_nav_items(rol)`
duplicates carried by the first direct-screen modules. All later screens,
including the platform-core System screen, consume this same helper. The
original extraction remains behavior-frozen by tests/test_admin_screen_nav.py;
the golden payload now also pins each platform screen's direct path.

Platform-core ONLY (`core.app_modules` + `core.i18n`): the helper is
importable from platform-side screens (admin_panel/) and module-side
screens (modules/intake/) alike without either direction gaining a
private dependency on the other — tests/test_admin_screen_nav.py
asserts the import allowlist by parsing this file's AST, so a future
`modules.*` or sibling-screen import here fails the build, not just a
review.
"""

from app.core import app_modules
from app.core import i18n


# The reviewed admin design presents these as stable visual chunks. The first
# direct-screen implementation accidentally emitted registry order instead
# (platform settings first, care screens later), flattening the information
# architecture. Keep the order here so every direct screen and role agrees.
# Account is intentionally outside the named groups and always last.
_NAV_GROUPS = (
    ("care", "admin.shell.nav.group.care", "Atención", (
        "solicitudes", "cola", "mis-turnos", "mis-pacientes", "my-schedules", "pacientes",
    )),
    ("organization", "admin.shell.nav.group.organization", "Organización", (
        "medicos", "servicios", "centros", "preparaciones",
    )),
    # Everything the patient-facing assistant runs on, in one place (the product owner,
    # 2026-09-05): what it knows (wiki), how it is told to behave (prompts),
    # what patients actually asked (chats).
    ("chat", "admin.shell.nav.group.chat", "Chat", (
        "knowledge", "prompts", "chats",
    )),
    ("system", "admin.shell.nav.group.system", "Sistema", (
        "reportes", "plan", "configuracion", "settings", "legal",
        "entrevistas", "sistema", "hsi", "usuarios", "roles",
    )),
    ("account", None, None, ("cuenta",)),
)
_NAV_POSITION = {
    section_id: (group_index, section_index)
    for group_index, (_group, _key, _label, section_ids) in enumerate(_NAV_GROUPS)
    for section_index, section_id in enumerate(section_ids)
}
_NAV_GROUP_META = {
    section_id: (group, key, label)
    for group, key, label, section_ids in _NAV_GROUPS
    for section_id in section_ids
}


def has_section(section_id, rol="admin", permissions=None):
    """Whether the live shared admin contract exposes ``section_id``.

    Screens use this instead of importing feature or connector internals. It
    keeps the sidebar, direct-route gate, and contextual platform copy on the
    same source of truth when a module or institutional connector changes at
    runtime.

    When `permissions` is provided, section visibility is gated by permission.
    """
    return any(
        (entry if isinstance(entry, str) else entry.get("id")) == section_id
        for entry in app_modules.sections_payload_for_role(rol, permissions=permissions)
    )


def nav_items(rol, permissions=None):
    """Visible, grouped sidebar destinations for ``rol``.

    The source is `app_modules.sections_payload_for_role(rol)`, shared with
    the route/module contract.  A module section with neither ``label`` nor
    ``label_key`` is intentionally contextual rather than navigational: it
    keeps its direct route and role gate, but does not leak its internal id
    into the menu.  ``consulta`` is the concrete case — doctors enter that
    workspace from a patient or queue row, never from an empty standalone
    sidebar destination.

    Order, href shape and label resolution are part of the frozen contract:
    every entry resolves to a direct screen.  A missing path is a broken
    screen declaration, not permission to link an operator to the retired
    hash shell.

    When `permissions` is provided, section visibility is gated by permission.
    """
    items = []
    for entry in app_modules.sections_payload_for_role(rol, permissions=permissions):
        if isinstance(entry, str):
            sid = entry
            key = "admin.shell.nav." + sid.replace("-", "_")
            label = i18n.t_or(key, sid, i18n.active())
            href = app_modules.platform_admin_screen_path(sid)
        else:
            sid = entry["id"]
            screen = entry.get("screen")
            if not (entry.get("label_key") or entry.get("label")):
                continue
            if entry.get("label_key"):
                label = i18n.t_or(entry["label_key"], entry.get("label") or sid, i18n.active())
            else:
                label = entry.get("label") or sid
            href = screen["path"] if screen else None
            # The screen stays open to the role (direct link, has_section);
            # only its menu entry goes (manifest `nav_hidden_for`).
            if rol in (entry.get("nav_hidden_for") or ()):
                continue
        # Doctors retain this screen/API for their scoped patient workflows;
        # only its sidebar destination is removed, even with explicit grants.
        if rol == "medico" and sid == "solicitudes":
            continue
        if not href:
            raise ValueError("admin section %r has no direct screen" % sid)
        items.append({"id": sid, "href": href, "label": label})

    # A future labelled section belongs under Sistema by default, rather than
    # creating a fourth visible heading or pushing Account away from the end.
    # (El índice es el de "system": cuando se agregó el grupo "chat" en
    # 2026-09-05 este 2 quedó apuntando a Chat sin que nadie lo viera.)
    fallback_group = 3
    fallback_position = (fallback_group, len(_NAV_GROUPS[fallback_group][3]))
    items.sort(key=lambda item: _NAV_POSITION.get(
        item["id"], fallback_position,
    ))
    for item in items:
        group, key, fallback = _NAV_GROUP_META.get(
            item["id"], _NAV_GROUPS[fallback_group][:3],
        )
        item["group"] = group
        item["group_label"] = i18n.t_or(key, fallback, i18n.active()) if key else None
    return items
