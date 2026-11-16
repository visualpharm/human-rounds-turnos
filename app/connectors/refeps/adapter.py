"""ABC-conforming adapter for the refeps connector.

This is what `connectors.adapter_for("license_lookup")` instantiates: the
admin's order-verification check (`modules/turnos/booking_requests.py:
_admin_refeps`) reaches `refeps.buscar_matricula()` through here since the
registry rewire. Result SHAPING (`refeps.mejor_match()`/`simplify()`) stays
a direct call in that consumer — it ranks REFEPS records against the name on
the order, which is record-format knowledge, not a capability method.
"""
from __future__ import annotations

from app.connectors import LicenseLookupConnector, ProviderUnavailable
from . import refeps as _impl


class RefepsConnector(LicenseLookupConnector):
    """Wraps refeps.py's buscar_matricula(). refeps.RefepsError (network/
    parse failure) is translated to the framework's generic
    ProviderUnavailable so a caller going through the ABC only needs to
    handle one exception type; refeps.py's own callers keep catching
    RefepsError directly, unaffected."""

    def search_by_license(self, license_number):
        try:
            return _impl.buscar_matricula(license_number)
        except _impl.RefepsError as exc:
            raise ProviderUnavailable(str(exc)) from exc
