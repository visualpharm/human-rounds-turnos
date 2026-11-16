# Tier 5 admin screen — Wiki/knowledge at `/admin/knowledge`

Slice of the progressive admin-section migration (`docs/roadmap/tier5-frontend-design.md`) added the server-routed twin of the legacy `#knowledge` Wiki. Copy the preparations/entrevistas admin-only gate; do not invent a second shell.

## What to copy

- **Role gate:** no `roles` key on the manifest entry → `deps.require_admin(solo_admin=True)`. Medico / admisionista / reportes 403; no session 401; retired `marca` token 401.
- **Module gate:** router-level `Depends(require_module_enabled("turnos"))`. Disable turnos → 404 `{"detail":{"error":"no_encontrado"}}` without unmounting.
- **Shell:** `templates/admin/base.html` + `core.screen_nav.nav_items`. Template loads fingerprinted assets through the **function** form `{{ static('admin-knowledge.js') }}`.
- **Controller:** `admin_content.py` `/api/admin/content/knowledge` (CRUD, properties, DELETE soft-disable, `/restaurar`) stays the only data path.

## Direct vs legacy state

- Direct URL item state is **`?item=<id>`** (`history.pushState` + `popstate`) so Back/Forward and reload keep the open article. The template never assigns `location.hash`.
- Legacy `#knowledge` / `#knowledge/<id>` is unchanged: hashchange, `location.hash =` on create/delete, `pushState` to `#knowledge` on close.

English route `/admin/knowledge`; the hash/DOM id stays `knowledge`.

## Narrow editor dialog (<=900px wrap)

When the knowledge wrap is <=900px and an item is open, the editor is a modal dialog: focus moves into it, Tab stays inside, the covered tree (list, chrome) is `inert`, Escape closes, and focus returns to the triggering row. Desktop split-pane and legacy `#knowledge/<id>` routing stay unchanged. Search and editor fields are 16px.
