"""middleware_stack.py — the ordered user-middleware composition (extracted
from app.py when the admin Origin check joined the stack, 2026-10-27, so the
composition root stays under its line budget).

ORDER MATTERS: Starlette's add_middleware inserts at position 0, so the FIRST
call below is the INNERMOST middleware and the LAST is the OUTERMOST. The
order is pinned by tests/test_pure_asgi_middlewares.py
(`test_the_mount_order_is_unchanged`).
"""
from app.admin_panel import admin_navigation_middleware
from app.auth import site_auth_middleware
from app.core import admin_csrf_middleware, head_as_get_middleware, host_redirect_middleware
from app.core import i18n_middleware, legacy_urls_middleware, security_headers_middleware


def install(app):
    """Add every user middleware to ``app``, innermost first."""
    # Innermost (added first): only the router sees a HEAD rewritten as GET.
    app.add_middleware(head_as_get_middleware.HeadAsGetMiddleware)
    # Inside the legacy rewrite: the admin Origin check sees the canonical path,
    # so a historical alias of an admin endpoint cannot sidestep it.
    app.add_middleware(admin_csrf_middleware.AdminCsrfMiddleware)
    app.add_middleware(legacy_urls_middleware.LegacyUrlRewriteMiddleware)
    app.add_middleware(i18n_middleware.I18nMiddleware)
    app.add_middleware(admin_navigation_middleware.AdminLastScreenMiddleware)
    # Site-auth is added AFTER i18n on purpose: the LAST add_middleware call is
    # the OUTERMOST user middleware — this one runs before i18n binds, so the
    # gate is hit before any router; only the five anonymous exceptions
    # (site_auth._EXENTAS) skip it.
    app.add_middleware(site_auth_middleware.SiteAuthMiddleware)
    # Host-redirect is added after site-auth so it runs before it: a vanity-host
    # request gets the canonical-host 308 (core/host_redirect.py) instead of a
    # site-auth challenge for a host it will never serve.
    app.add_middleware(host_redirect_middleware.HostRedirectMiddleware)
    # Outermost (added last): the 308 and the 401 above carry the headers too.
    app.add_middleware(security_headers_middleware.SecurityHeadersMiddleware)
