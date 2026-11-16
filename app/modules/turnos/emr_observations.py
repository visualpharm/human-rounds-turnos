"""Vendor-neutral EMR observation contract owned by Human Rounds Core.

Connectors translate their wire responses into these small records before they
cross into the durable journal.  Core intentionally knows neither a vendor's
field spelling nor its numeric lifecycle codes.
"""
from __future__ import annotations

from datetime import datetime

from app.connectors import ProviderUnavailable
from app.core import text_fold


APPOINTMENT_STATES = frozenset({
    "scheduled", "cancelled", "blocked", "no_show", "attended",
})


def _timestamp(value):
    value = text_fold.clean(value)
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _object(value, label):
    if not isinstance(value, dict):
        raise ProviderUnavailable(f"EMR observation {label} must be an object")
    return value


def _entity(record, label, fields):
    """An observation with an `id` and the text `fields` (each trimmed, None when blank)."""
    record = _object(record, label)
    entity_id = text_fold.clean(record.get("id"))
    if not entity_id:
        raise ProviderUnavailable(f"EMR {label} observation omitted id")
    return {"id": entity_id, **{field: text_fold.clean(record.get(field)) for field in fields}}


def schedule(record):
    """Validate and project one generic schedule observation."""
    return _entity(record, "schedule", ("professional_id", "specialty", "professional_name"))


def patient(record):
    """Validate and project one generic patient observation."""
    return _entity(record, "patient", ("document_number", "given_name", "family_name"))


def appointment(record):
    """Validate and project one generic appointment observation."""
    record = _object(record, "appointment")
    appointment_id = text_fold.clean(record.get("id"))
    patient_id = text_fold.clean(record.get("patient_id"))
    scheduled_at = _timestamp(record.get("scheduled_at"))
    state = text_fold.clean(record.get("state"))
    if not appointment_id or not patient_id or not scheduled_at:
        raise ProviderUnavailable(
            "EMR appointment observation requires id, patient_id and scheduled_at"
        )
    if state not in APPOINTMENT_STATES:
        raise ProviderUnavailable("EMR appointment observation has unknown state")
    normalized_patient = patient(record.get("patient") or {"id": patient_id})
    if normalized_patient["id"] != patient_id:
        raise ProviderUnavailable("EMR appointment observation has inconsistent patient ids")
    return {
        "id": appointment_id,
        "schedule_id": text_fold.clean(record.get("schedule_id")),
        "patient_id": patient_id,
        "scheduled_at": scheduled_at,
        "state": state,
        "patient": normalized_patient,
    }


def bundle(value):
    """Validate a complete observations response at the connector boundary."""
    value = _object(value, "bundle")
    return {
        "schedules": [schedule(item) for item in (value.get("schedules") or [])],
        "appointments": [appointment(item) for item in (value.get("appointments") or [])],
        "patients": [patient(item) for item in (value.get("patients") or [])],
        **({"cursor": text_fold.clean(value.get("cursor"))} if text_fold.clean(value.get("cursor")) else {}),
    }
