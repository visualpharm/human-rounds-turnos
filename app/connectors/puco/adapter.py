"""ABC-conforming adapter for the puco connector.

This is what `connectors.adapter_for("insurance_lookup")` instantiates: the
registration POST's server-side coverage attach (`cuentas/accounts.py:
_norm_coverage`) reads `cached()` through here since the registry rewire.
The form's own `POST /api/puco` lookup (`puco_router.py` → `puco.handle()`,
mounted via modules/turnos/module.json) is this connector's OWN transport
endpoint and keeps calling the module directly — a connector asking the
registry about itself would be circular.
"""
from __future__ import annotations

from app.connectors import InsuranceLookupConnector
from . import puco as _impl


class PucoConnector(InsuranceLookupConnector):
    """Wraps puco.py's fake → WS131 → public-GWT-RPC chain. That chain lives
    ENTIRELY inside puco.py's own consultar() — it is one connector's own
    internal degradation ladder, not a choice between multiple connectors."""

    def available(self):
        return _impl.disponible()

    def lookup(self, national_id):
        return _impl.consultar(national_id)

    def cached(self, national_id):
        # puco.py's 15-minute in-memory cache of the last lookup for this DNI
        # (positives and "no figura" alike) — never a network call.
        return _impl.cacheada(national_id)
