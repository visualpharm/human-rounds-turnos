"""health.py — `/health` (liveness) + `/ready` (readiness), Tier 4 slice 7
(docs/roadmap/tier4-replatform-design.md, "`/health` + `/ready` + graceful
shutdown + optional Sentry").

`app.py`'s FastAPI `/health`/`/ready` routes call the two functions below
directly. There is one readiness check and one native transport.

`/health` is process-up liveness only — no DB, no imports beyond stdlib,
can never hang or 500 (matches slice 0's original promise in `app.py`).
`/ready` reuses `system_status.probe_db()` (the SAME "SELECT 1 + schema +
migrations at head" check the admin «Sistema» panel already runs) instead
of a second implementation of "is Postgres reachable" — timeout-guarded by
`db._connect()`'s own `connect_timeout=5`, same never-hang/never-500
philosophy as every other probe in `system_status.py`.

**Exempted from the pre-launch Basic Auth gate** (`site_auth.py` — see its
`_EXENTAS`): an orchestrator health check cannot be expected to carry the
shared site credential. `app.py` mounts both routes before the native
terminal, and the site-auth middleware explicitly exempts them."""

from app.admin_panel import system_status
from app.core import version


def liveness():
    """Process up, plus which build answers (`core/version.py`: the short
    git commit, or "unknown") — so a deploy can be checked from outside.
    Exactly these two keys: no environment, no host, no error text (responsable del producto
    2026-10-23; pinned by `test_health_ready.TestLivenessUnit`)."""
    return {"status": "ok", "version": version.short()}


def readiness():
    """(ok, body) — `ok` False means the caller must answer 503, not 200."""
    conectado, tabla, al_dia, detalle = system_status.probe_db()
    if conectado and tabla and al_dia:
        return True, {"status": "ok"}
    return False, {"status": "unavailable", "detail": detalle or "Base de datos no disponible."}
