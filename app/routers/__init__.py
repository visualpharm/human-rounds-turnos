"""routers/ — route composition for the ASGI composition root (Tier 4
composition extraction, docs/roadmap/tier4-replatform-design.md).

Three members, mounted by app.py in EXACTLY this order (Starlette matches by
registration order, so the order IS the routing contract — pinned
mechanically by tests/test_app_composition.py against the pre-extraction
inventory):

  1. `health.py`     — /health and /ready (liveness/readiness, no deps).
  2. `platform.py`   — the ordered tuple of platform-core routers (auth,
                       accounts, admin/settings sections...). Module-owned
                       routers are NOT here: app.py mounts those via
                       `app_modules.mount_module_routers()` right after.
  3. `native_terminal.py` — the dynamic/static terminal route, ALWAYS last.

Dependency direction (enforced by tests/test_domain_dependency_direction.py):
a router file may import domain `*_router` modules, core infra and
`services.*` — never `app` or `server` (the composition roots are this
package's consumers).
"""
