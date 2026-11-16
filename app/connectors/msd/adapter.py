"""ABC-conforming adapter for the msd connector.

This is what `connectors.adapter_for("slot_source")` instantiates (msd is the
flagged installation default): patient_web/site_*.py's "Cuándo" wizard step and
the listing's next-appointment cell reach `msd_data.turnos_for_servicio()`
through here since the registry rewire. The MSD-only fuzzy doctor match
(`msd_data.proximo_own_anywhere`/`proximo_for_medico`, a doctor-page detail
with no ABC analogue) stays a direct call in patient_web/site_*.py — a documented
residual, a harmless no-op for an installation without the MSD dump.
"""
from __future__ import annotations

from app.connectors import SlotSourceConnector
from . import msd_data as _impl


class MsdConnector(SlotSourceConnector):
    """Wraps msd_data.py's read-only bridge to the LaunchAgent-refreshed
    Mi Salud Digital slot dump (data/msd_turnos.json)."""

    def slots_for_service(self, servicio):
        return _impl.turnos_for_servicio(servicio)
