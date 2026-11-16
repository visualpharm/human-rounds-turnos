"""HSI-to-Core translations kept wholly on the legacy adapter side."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re

from app.connectors import ProviderUnavailable
from app.core import text_fold


_ART = timezone(timedelta(hours=-3))
_STATE_FROM_HSI = {
    1: "scheduled", 2: "scheduled", 3: "no_show", 4: "cancelled",
    5: "attended", 7: "blocked", 9: "no_show",
}
_STATE_TO_HSI = {
    "scheduled": 1, "cancelled": 4, "blocked": 7,
    "no_show": 3, "attended": 5,
}


def _when(day, hour):
    day, hour = text_fold.clean(day), text_fold.clean(hour)
    if not day or not hour:
        return None
    try:
        return datetime.fromisoformat(f"{day}T{hour[:8]}").replace(tzinfo=_ART).isoformat()
    except ValueError:
        return None


def _parts(when):
    try:
        parsed = datetime.fromisoformat(str(when).replace("Z", "+00:00")).astimezone(_ART)
    except (TypeError, ValueError):
        return None, None
    return parsed.date().isoformat(), parsed.strftime("%H:%M:%S")


def patient_from_hsi(raw, patient_id=None):
    raw = raw or {}
    person = raw.get("person") or raw
    return {
        "id": text_fold.clean(patient_id if patient_id is not None else raw.get("id")),
        "document_number": text_fold.clean(person.get("identificationNumber")),
        "given_name": text_fold.clean(person.get("firstName")),
        "family_name": text_fold.clean(person.get("lastName")),
    }


def schedule_from_hsi(diary, detail=None):
    diary, detail = diary or {}, detail or {}
    return {
        "id": text_fold.clean(diary.get("diary_id") or diary.get("id")),
        "professional_id": text_fold.clean(diary.get("professional_id")),
        "specialty": text_fold.clean(diary.get("specialty")),
        "professional_name": text_fold.clean(diary.get("professional")),
    }


def appointment_from_hsi(raw):
    raw = raw or {}
    schedule = raw.get("_sana_diary") or {}
    patient_raw = raw.get("patient") or {}
    patient_id = patient_raw.get("id") or raw.get("patientId")
    state = _STATE_FROM_HSI.get(raw.get("appointmentStateId"))
    if state is None:
        raise ProviderUnavailable("HSI appointment has an unsupported lifecycle state")
    return {
        "id": text_fold.clean(raw.get("id")),
        "schedule_id": text_fold.clean(raw.get("diaryId") or schedule.get("diary_id")),
        "patient_id": text_fold.clean(patient_id),
        "scheduled_at": _when(raw.get("date"), raw.get("hour")),
        "state": state,
        "patient": patient_from_hsi(patient_raw, patient_id),
    }


def checkpoint_from_hsi(raw):
    observed = appointment_from_hsi(raw)
    return {
        "appointment_id": observed["id"],
        "schedule_id": observed["schedule_id"],
        "patient_id": observed["patient_id"],
        "scheduled_at": observed["scheduled_at"],
        "state": observed["state"],
        "version": text_fold.clean((raw or {}).get("updatedOn")),
    }


def checkpoint_to_hsi(checkpoint):
    """Translate the Core checkpoint back only at the HSI client call edge."""
    checkpoint = checkpoint or {}
    state = checkpoint.get("state")
    if state not in _STATE_TO_HSI:
        raise ProviderUnavailable("EMR checkpoint has an unsupported lifecycle state")
    day, hour = _parts(checkpoint.get("scheduled_at"))
    def wire_id(name):
        value = checkpoint.get(name)
        if type(value) is int:
            parsed = value
        elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            parsed = int(value)
        else:
            raise ProviderUnavailable("EMR checkpoint has invalid HSI identity")
        if parsed <= 0:
            raise ProviderUnavailable("EMR checkpoint has invalid HSI identity")
        return parsed
    appointment_id = wire_id("appointment_id")
    diary_id = wire_id("schedule_id")
    patient_id = wire_id("patient_id")
    return {
        "id": appointment_id,
        "diaryId": diary_id,
        "patientId": patient_id,
        "date": day,
        "hour": hour,
        "appointmentStateId": _STATE_TO_HSI[state],
        "updatedOn": checkpoint.get("version"),
    }


def result_from_hsi(result):
    result = dict(result or {})
    if result.get("skipped"):
        return {"skipped": True}
    checkpoint = result.get("checkpoint")
    normalized = checkpoint_from_hsi(checkpoint) if checkpoint else None
    return {
        "estado": result.get("estado"),
        "appointment_id": text_fold.clean(result.get("appointment_id")),
        "patient_id": text_fold.clean(result.get("patient_id")),
        "actual_turno_dt": text_fold.clean(result.get("actual_turno_dt")),
        "requested_turno_dt": text_fold.clean(result.get("requested_turno_dt")),
        "deduplicated": bool(result.get("deduplicated")),
        "slot_changed": bool(result.get("slot_changed")),
        "sobreturno": bool(result.get("sobreturno")),
        **({"checkpoint": normalized} if normalized else {}),
    }
