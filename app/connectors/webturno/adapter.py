"""ABC-conforming adapter for the optional WebTurno slot source."""
from __future__ import annotations

from datetime import datetime

from app.connectors import SlotSourceConnector, ProviderUnavailable
from . import webturno as _impl


class WebturnoConnector(SlotSourceConnector):
    """Wraps webturno.py's Webturno client. `servicio` here is expected to
    carry `{"clinic_token": ..., "idme": ...}` (WebTurno's own identifiers —
    a different shape than msd_data's Sana-servicio-keyed lookup, since
    WebTurno has no concept of a Sana servicio id)."""

    def slots_for_service(self, servicio):
        if not isinstance(servicio, dict) or not servicio.get("clinic_token"):
            return {}
        try:
            wt = _impl.Webturno()
            slots = wt.slots(servicio["clinic_token"], servicio.get("idme"))
        except _impl.WebturnoError as exc:
            raise ProviderUnavailable(str(exc)) from exc
        turnos = []
        for slot in slots or []:
            raw = str(slot.get("fecha") or "").strip()
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            turnos.append({
                "dia": parsed.date().isoformat(),
                "hora": parsed.strftime("%H:%M"),
                "idtur": slot.get("idtur"),
            })
        if not turnos:
            return {}
        return {"medicos": [{
            "medico": str(servicio.get("medico") or ""),
            "medico_id": str(servicio.get("idme") or ""),
            "turnos": turnos,
        }]}
