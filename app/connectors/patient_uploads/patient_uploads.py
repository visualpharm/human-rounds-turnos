"""Read the lab results a patient uploaded from their own account.

Same normalized record shape the Alepo/Marqui/local_history adapters return,
so the chart summary and the patient's own lab view consume it with no
special case — plus one flag those never carry, `tercero: true`: the file
came from the patient, not from a health system, and every surface that shows
it has to say so.

A row whose transcription is still pending (`sin_lectura`, no AI available
when it was uploaded) is still returned, with empty text: the patient has to
see the result they just uploaded appear in their account, and the reading
catches up later. The database being unreachable degrades to
ProviderUnavailable, per the connector contract — a doctor's screen keeps
working with its other sources.
"""

from app.connectors import ProviderUnavailable

_SYSTEM = "patient_uploads"
_LABEL = "Aportado por el paciente"


def lookup_by_dni(dni):
    dni = "".join(c for c in str(dni or "") if c.isdigit())
    if not dni:
        return []
    try:
        from app.core import db_clinical
        filas = db_clinical.patient_lab_uploads_by_dni(dni)
    except Exception as exc:  # noqa: BLE001 — connector degradation contract
        raise ProviderUnavailable(f"patient_uploads: database unavailable: {exc}") from exc
    return _normalize(filas)


def entry_for(fila):
    """The normalized record of ONE stored row — the exact entry the chart
    summary and the lab view hash into a report key (`lab_analysis.report_hash`).
    Deleting an upload uses it to find the cached AI reading that belongs to it."""
    return _normalize([fila])[0]


def _normalize(filas):
    out = []
    for fila in filas or []:
        texto = str(fila.get("texto") or "").strip()
        fecha = str(fila.get("fecha") or "")[:10] or None
        out.append({
            "id": fila.get("id"),
            "tipo": "laboratorio",
            "fecha": fecha,
            "texto": texto,
            "sin_lectura": bool(fila.get("sin_lectura")),
            "servicio": _LABEL,
            "sistema": _SYSTEM,
            "sistema_label": _LABEL,
            "tercero": True,
        })
    out.sort(key=lambda row: str(row.get("fecha") or ""))
    return out
