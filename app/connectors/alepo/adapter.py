"""ABC-conforming adapter for the alepo connector.

See connectors/__init__.py's ClinicalHistoryLookupConnector for the shape
and degradation contract.
"""
from __future__ import annotations

from app.connectors import ClinicalHistoryLookupConnector
from . import alepo as _impl


class AlepoConnector(ClinicalHistoryLookupConnector):
    """Wraps alepo.py's lookup_by_dni() — real mode raises
    ProviderUnavailable until an access path exists; TURNOS_FAKE_ALEPO=1
    serves synthetic demo histories — see alepo.py's module docstring for why."""

    def lookup_by_dni(self, dni):
        return _impl.lookup_by_dni(dni)
