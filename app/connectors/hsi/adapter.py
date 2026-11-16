"""Legacy HSI wire adapter.

All HSI-specific HTTP, credentials and field mappings stay in this package.
The Core journal calls the generic ``EmrSyncConnector`` methods below; this
adapter is the temporary compatibility implementation until an Enterprise
bridge is selected explicitly.
"""
from __future__ import annotations

from datetime import date, datetime, time
import re

from app.connectors import EmrConflict, EmrSyncConnector, ProviderUnavailable
from . import normalization


def _impl_module():
    """Late bind the legacy wire module so existing fake/cassette seams keep
    working while Core has no import edge to it."""
    from . import hsi_client
    return hsi_client


def _resolver_module():
    """Late bind the package's own conflict resolver the same way.

    The resolver is HSI-specific vendor behavior and stays inside
    ``connectors/hsi/`` (hsi.md, "Core to Enterprise boundary"); Core reaches
    it only through ``run_conflict_resolution``/``conflict_panel``/
    ``apply_conflict_resolution`` below.  Late binding keeps the
    module-attribute monkeypatch seams (test_hsi_resolver.py,
    test_admin_hsi_screen.py) visible through the delegation.
    """
    from . import hsi_resolver
    return hsi_resolver


class HsiConnector(EmrSyncConnector):
    """Wraps hsi_client.push_turno() — see that function's docstring for the
    full successful-booking result shape and TURNOS_SKIP_HSI's degradation."""

    def external_clinical_history(self, identifier):
        impl = _impl_module()
        if not impl.config.external_history_enabled():
            return {"available": False, "reason": "flag_off", "items": []}
        try:
            client = impl.HsiClient()
            patient_id = client.find_patient(identifier)
            if patient_id is None:
                # An absent/ambiguous identity is not evidence of empty history.
                return {"available": False, "reason": "error", "items": []}
            return client.external_clinical_history(patient_id)
        except Exception as exc:
            return impl.HsiClient.external_failure(exc)

    def push_appointment(self, dni, first_name, last_name, birth_date_iso,
                          gender, specialty, when_iso, **kwargs):
        return normalization.result_from_hsi(_impl_module().push_turno(
            dni, first_name, last_name, birth_date_iso, gender, specialty,
            when_iso, **kwargs))

    def execute_intent(self, operation, payload, idempotency_key):
        impl = _impl_module()
        try:
            if operation == "create":
                return self.push_appointment(
                    payload["dni"], payload["nombre"], payload["apellido"],
                    payload.get("nacimiento"), payload.get("sexo"),
                    payload.get("especialidad") or payload.get("servicio_id"),
                    payload["when"], medico=payload.get("medico"),
                    sobreturno=bool(payload.get("sobreturno")),
                )
            client = impl.HsiClient()
            client.login()
            checkpoint = normalization.checkpoint_to_hsi(payload.get("checkpoint"))
            if operation == "cancel":
                return normalization.result_from_hsi(
                    client.cancel_appointment(payload["appointment_id"], checkpoint))
            if operation == "reassign":
                return normalization.result_from_hsi(
                    client.reassign_appointment(payload["appointment_id"], checkpoint, payload["when"]))
            if operation == "attendance":
                return normalization.result_from_hsi(
                    client.mark_attendance(payload["appointment_id"], checkpoint, payload.get("asistencia")))
            raise ValueError(f"unsupported EMR operation: {operation}")
        except getattr(impl, "HsiConflict", ()) as exc:
            remote = getattr(exc, "remote", None)
            raise EmrConflict(
                str(exc),
                remote=normalization.appointment_from_hsi(remote) if remote else None,
            ) from exc

    def observe_intent(self, operation, payload):
        """Positively observe a legacy move without issuing update-date.

        HSI has no intent-key lookup. Its diary read can establish the exact
        target state, but an absent row is only uncertainty: the old worker
        could still finish its remote write after this read.
        """
        if operation != "reassign" or not isinstance(payload, dict):
            return None
        prior = payload.get("checkpoint")
        if not isinstance(prior, dict):
            return None
        appointment_id = str(payload.get("appointment_id") or "").strip()
        schedule_id = str(prior.get("schedule_id") or "").strip()
        patient_id = str(prior.get("patient_id") or "").strip()
        day, hour = normalization._parts(payload.get("when"))
        if (not all((appointment_id, schedule_id, patient_id, day, hour))
                or str(prior.get("appointment_id") or "").strip() != appointment_id):
            return None
        client = _impl_module().HsiClient()
        client.login()
        rows = client.appointments_for_diary(schedule_id, day, day)
        matches = [raw for raw in rows if str(raw.get("id") or "").strip() == appointment_id]
        if len(matches) != 1:
            return None
        raw = dict(matches[0])
        raw_day, raw_hour = raw.get("date"), raw.get("hour")
        if (not isinstance(raw_day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_day)
                or not isinstance(raw_hour, str) or not re.fullmatch(r"\d{2}:\d{2}:\d{2}", raw_hour)):
            return None
        try:
            date.fromisoformat(raw_day)
            time.fromisoformat(raw_hour)
        except ValueError:
            return None
        if type(raw.get("appointmentStateId")) is not int:
            return None
        nested = raw.get("patient")
        if nested is not None and not isinstance(nested, dict):
            return None
        if (isinstance(nested, dict) and nested.get("id") is not None
                and raw.get("patientId") is not None
                and str(nested["id"]) != str(raw["patientId"])):
            return None
        raw.setdefault("diaryId", schedule_id)  # HSI diary route may omit it.
        try:
            checkpoint = normalization.checkpoint_from_hsi(raw)
        except ProviderUnavailable:
            return None
        try:
            observed = datetime.fromisoformat(checkpoint["scheduled_at"])
            target = datetime.fromisoformat(f"{day}T{hour}-03:00")
        except (TypeError, ValueError):
            return None
        if (checkpoint["appointment_id"] != appointment_id
                or checkpoint["schedule_id"] != schedule_id
                or checkpoint["patient_id"] != patient_id
                or checkpoint["state"] != "scheduled"
                or observed != target):
            return None
        return {"estado": "ok", "appointment_id": appointment_id,
                "patient_id": patient_id,
                "actual_turno_dt": checkpoint["scheduled_at"],
                "checkpoint": checkpoint}

    def pull_observations(self, from_date, to_date, known_patient_ids):
        client = _impl_module().HsiClient()
        client.login()
        schedules = []
        for diary in client.list_diaries():
            schedules.append(normalization.schedule_from_hsi(
                diary, client.diary_detail(diary["diary_id"])))
        patients = []
        for patient_id in known_patient_ids:
            patient = client.patient_basicdata(patient_id)
            if patient:
                patients.append(normalization.patient_from_hsi(patient, patient_id))
        return {"schedules": schedules,
                "appointments": [normalization.appointment_from_hsi(item)
                                 for item in client.list_appointments(from_date, to_date)],
                "patients": patients}

    def supports_auto_resolution(self):
        return True

    def run_conflict_resolution(self, now=None):
        return _resolver_module().resolver_pass(now=now)

    def conflict_panel(self, limit=60):
        return _resolver_module().estado_panel(limit=limit)

    def apply_conflict_resolution(self, action, conflict_ids,
                                  resolved_by="operador", now=None):
        return _resolver_module().aplicar_resolucion(
            action, conflict_ids, resuelto_por=resolved_by, now=now)
