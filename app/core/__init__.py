"""core/ — platform infra shared by every domain package and module:
database (`db`), settings (`config`), request-scoped dependency plumbing
(`deps`), the module manifest loader/mounter (`app_modules`) and its
FastAPI router (`app_modules_router`), timezone conversion (`tz`), the
data-pack resolver (`sitedata`, `data_packs`), site identity
(`site_identity` + `_router`), theming (`theme`), the shared formatter
(`format`), versioned/fingerprinted static assets (`static_fingerprint`),
the Jinja environment (`jinja_env`), internationalization (`i18n`,
`i18n_middleware`, `pack_l10n`, `translate`), gzip response bridging
(`gzip_bridge`), reverse-proxy trust (`proxy_trust`), health checks
(`health`), error tracking (`error_tracking`), PII redaction for external
AI calls (`pii`), legacy URL redirects (`legacy_urls`) and host-based
redirects (`host_redirect`).

Tier 2 batch K (docs/roadmap/tier2-physical-modularization-plan.md): the
LAST physical-modularization batch — the highest-blast-radius package,
imported by nearly every other file in the repo. Not a domain package and
not a module: this is platform-core, never disabled at runtime.
Deliberately minimal: no re-export facade. Importers use explicit
submodules:

    from core import db                      # Postgres connection pool, schema, migrations
    from core import config                  # settings resolution
    from core import deps                    # request-scoped dependency plumbing (ASGI)
    from core import app_modules             # module.json manifest loader/mounter
    from core import app_modules_router      # FastAPI router for module registry (/api/admin/modules, /api/nav)
    from core import theme                   # active theme resolution
    from core import tz                      # installation-timezone datetime conversion
    from core import clock                   # the one "now" of the application (tests freeze it)
    from core import sitedata                # data-pack path resolution
    from core import site_identity           # site identity (name, brand, contact)
    from core import site_identity_router    # FastAPI twin
    from core import data_packs              # layered data-pack chain resolution
    from core import format                  # one shared human-readable formatter
    from core import static_fingerprint      # content-hash-fingerprinted static assets
    from core import jinja_env               # shared Jinja environment
    from core import i18n                    # request-scoped language + UI string catalogs
    from core import i18n_middleware         # ASGI language-binding middleware
    from core import pack_l10n               # which pack fields are translatable
    from core import translate               # translation backend
    from core import gzip_bridge             # gzip response bridging
    from core import proxy_trust             # reverse-proxy trust / real client IP
    from core import health                  # /healthz and friends
    from core import error_tracking          # error tracking sink
    from core import pii                     # PII redaction before external AI calls
    from core import legacy_urls             # legacy URL redirects
    from core import host_redirect           # host-based redirects
"""
