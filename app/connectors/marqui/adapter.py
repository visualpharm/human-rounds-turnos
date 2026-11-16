"""ABC-conforming adapter for the marqui connector.

See connectors/__init__.py's ClinicalHistoryLookupConnector for the shape
and degradation contract.
"""
from __future__ import annotations

from app.connectors import ClinicalHistoryLookupConnector
from . import marqui as _impl


class MarquiConnector(ClinicalHistoryLookupConnector):
    """Wraps marqui.py's lookup_by_dni() — real mode raises
    ProviderUnavailable until an access path exists; TURNOS_FAKE_MARQUI=1
    serves synthetic demo histories — see marqui.py's module docstring for why."""

    def lookup_by_dni(self, dni):
        return _impl.lookup_by_dni(dni)
