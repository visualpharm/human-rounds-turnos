"""app.py — the Tier 4 FastAPI COMPOSITION ROOT, nothing else.

Public front (`0.0.0.0:PORT`) ONLY when `TURNOS_ASGI=1` (see asgi_main.py) —
with the flag off this module is never even imported. Everything this file
used to implement now lives behind two explicit boundaries, and this file
only wires them together, in an order that IS the routing contract
(Starlette matches by registration order; tests/test_app_composition.py pins
the full ordered route inventory against the pre-extraction one):

  1. Site-auth boundary           — auth/site_auth_middleware.py, an outer
                                    user middleware (core/middleware_stack.py
                                    holds the whole ordered stack): runs the
                                    pre-launch whole-site Basic Auth gate
                                    (auth/site_auth.py's authorize()) on
                                    EVERY request, native routes included,
                                    before any router. The five anonymous
                                    exceptions and the 401 challenge come
                                    from site_auth.py — see that module's
                                    docstring for the parity gap this
                                    closes.
  2. i18n middleware              — core/i18n_middleware.py (one bind/reset
                                    of i18n.py's ContextVar per request).
  3. /health, /ready              — routers/health.py.
  4. Platform-core routers        — routers/platform.py's ORDERED tuple.
  5. Module-owned routers         — core/app_modules.mount_module_routers()
                                    (any module.json `router_attr`; each
                                    endpoint self-gates with
                                    require_module_enabled).
  6. Native terminal, LAST        — routers/native_terminal.py; late public
                                    pages, descriptor-held static assets, and
                                    bounded compatibility errors.

Design and risk register: docs/roadmap/tier4-replatform-design.md and
docs/roadmap/asgi-proxy-retirement-plan.md.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core import app_modules, deps as core_deps, middleware_stack, terminal_response
from app.ops import web_runtime
from app.routers import health as health_router
from app.routers import native_terminal
from app.routers import platform as platform_routers


@asynccontextmanager
async def _lifespan(_app):
    """Own web-side services exactly once for each ASGI process lifespan."""
    try:
        web_runtime.start_background_services()
    except BaseException:
        web_runtime.stop_app_modules()
        raise
    try:
        yield
    finally:
        web_runtime.stop_app_modules()


app = FastAPI(
    title="Human Rounds — Turnos",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
)

core_deps.install_gate_handlers(app)
app.add_exception_handler(terminal_response.TerminalNotFound,
                          terminal_response.render_terminal_not_found)

# The user-middleware stack, innermost first (order and why: core/middleware_stack.py).
middleware_stack.install(app)

app.include_router(health_router.router)
for _router in platform_routers.ORDERED:
    app.include_router(_router)
app_modules.mount_module_routers(app)
# The native terminal MUST stay last — every route mounted after it would be
# unreachable (Starlette stops at the first match).
app.include_router(native_terminal.router)
