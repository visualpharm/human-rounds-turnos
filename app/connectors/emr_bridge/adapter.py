"""Core-side client for the public EMR Bridge Protocol v1.

It is deliberately stdlib-only.  The bridge owns all vendor credentials and
has no database access to Human Rounds; Core owns the journal and acknowledg-
ment after a successful response.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.connectors import EmrConflict, EmrRejected, EmrSyncConnector, ProviderUnavailable
from app.core import config
from app.core import text_fold
from app.modules.turnos import emr_observations


_ATTENDANCE = {"presente": "attended", "ausente": "no_show",
               "attended": "attended", "no_show": "no_show"}


def _checkpoint(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProviderUnavailable("EMR bridge checkpoint must be an object")
    allowed = ("appointment_id", "schedule_id", "patient_id",
               "scheduled_at", "state", "version")
    projected = {key: value[key] for key in allowed if key in value}
    state = projected.get("state")
    if state is not None and state not in emr_observations.APPOINTMENT_STATES:
        raise ProviderUnavailable("EMR bridge checkpoint has an unknown state")
    return projected


def _public_intent(operation, payload):
    """Translate the Core journal record to the documented public contract."""
    payload = payload if isinstance(payload, dict) else {}
    local_request_id = text_fold.clean(payload.get("solicitud_id"))
    if not local_request_id:
        raise ProviderUnavailable("EMR intent omitted local_request_id")
    if operation == "create":
        return {
            "local_request_id": local_request_id,
            "patient": {
                "national_id": text_fold.clean(payload.get("dni")),
                "first_name": text_fold.clean(payload.get("nombre")),
                "last_name": text_fold.clean(payload.get("apellido")),
                "birth_date": text_fold.clean(payload.get("nacimiento")),
                "gender": text_fold.clean(payload.get("sexo")),
            },
            "specialty": text_fold.clean(payload.get("especialidad") or payload.get("servicio_id")),
            "scheduled_at": text_fold.clean(payload.get("when")),
            "professional_name": text_fold.clean(payload.get("medico")),
            "overbook": bool(payload.get("sobreturno")),
        }
    base = {
        "local_request_id": local_request_id,
        "appointment_id": text_fold.clean(payload.get("appointment_id")),
        "checkpoint": _checkpoint(payload.get("checkpoint")),
    }
    if not base["appointment_id"]:
        raise ProviderUnavailable(f"EMR {operation} intent omitted appointment_id")
    if operation == "cancel":
        return base
    if operation == "reassign":
        scheduled_at = text_fold.clean(payload.get("when"))
        if not scheduled_at:
            raise ProviderUnavailable("EMR reassign intent omitted scheduled_at")
        return {**base, "scheduled_at": scheduled_at}
    if operation == "attendance":
        attendance = _ATTENDANCE.get(str(payload.get("asistencia") or ""))
        if not attendance:
            raise ProviderUnavailable("EMR attendance intent has an invalid state")
        return {**base, "attendance": attendance}
    raise ProviderUnavailable(f"EMR connector does not support {operation}")


def _public_remote(value):
    if not isinstance(value, dict):
        return None
    allowed = ("id", "schedule_id", "patient_id", "scheduled_at", "state", "reason")
    return {key: value[key] for key in allowed if key in value}


def _core_result(value):
    """Validate a bridge result and project it to the Core journal shape."""
    if not isinstance(value, dict) or value.get("status") != "confirmed":
        raise ProviderUnavailable("EMR bridge returned an invalid intent result")
    appointment_id = text_fold.clean(value.get("appointment_id"))
    if not appointment_id:
        raise ProviderUnavailable("EMR bridge result omitted appointment_id")
    checkpoint = _checkpoint(value.get("checkpoint"))
    return {
        "estado": "ok",
        "appointment_id": appointment_id,
        "patient_id": text_fold.clean(value.get("patient_id")),
        "actual_turno_dt": text_fold.clean(value.get("scheduled_at")),
        **({"checkpoint": checkpoint} if checkpoint is not None else {}),
    }


class EnterpriseBridgeConnector(EmrSyncConnector):
    """HTTP implementation of ``docs/architecture/emr-bridge-protocol.md``."""

    protocol_version = "v1"

    def degrades_when_unconfigured(self):
        """Unlike the legacy compatibility adapter, bridge configuration is
        all-or-nothing and must never enter retry/backoff without an endpoint."""
        return True

    def _base_url(self):
        # No localhost/default endpoint: an absent bridge is an explicit
        # degraded connector, never a retrying accidental live integration.
        return (config.get("EMR_BRIDGE_URL", default="") or "").rstrip("/")

    def _request(self, method, path, body=None):
        base = self._base_url()
        token = config.get("EMR_BRIDGE_TOKEN", default="") or ""
        if not base or not token:
            raise ProviderUnavailable("EMR bridge is not configured")
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            base + path, data=data, method=method,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}",
                     **({"Content-Type": "application/json"} if data is not None else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8", "replace")
            finally:
                exc.close()
            try:
                detail = json.loads(raw)
            except ValueError:
                detail = {}
            if exc.code == 409:
                raise EmrConflict(detail.get("error") or "remote checkpoint conflict",
                                  remote=_public_remote(detail.get("remote"))) from exc
            if exc.code == 422:
                raise EmrRejected(
                    detail.get("error") or "remote EMR rejected the intent",
                    code=detail.get("code") or "rejected",
                    remote=_public_remote(detail.get("remote")),
                ) from exc
            raise ProviderUnavailable(detail.get("error") or f"EMR bridge HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            raise ProviderUnavailable(f"EMR bridge unavailable: {exc}") from exc
        try:
            decoded = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise ProviderUnavailable("EMR bridge returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ProviderUnavailable("EMR bridge returned an invalid response")
        return decoded

    def push_appointment(self, dni, first_name, last_name, birth_date_iso,
                         gender, specialty, when_iso, **kwargs):
        return self.execute_intent("create", {
            "dni": dni, "nombre": first_name, "apellido": last_name,
            "nacimiento": birth_date_iso, "sexo": gender,
            "especialidad": specialty, "when": when_iso,
            "medico": kwargs.get("medico"), "sobreturno": bool(kwargs.get("sobreturno")),
        }, kwargs.get("idempotency_key") or "legacy-create")

    def execute_intent(self, operation, payload, idempotency_key):
        response = self._request("POST", "/v1/intents", {
            "operation": operation, "idempotency_key": idempotency_key,
            "payload": _public_intent(operation, payload),
        })
        result = response.get("result")
        return _core_result(result)

    def pull_observations(self, from_date, to_date, known_patient_ids):
        response = self._request("POST", "/v1/observations", {
            "from_date": from_date, "to_date": to_date,
            "known_patient_ids": [str(v) for v in known_patient_ids],
        })
        return emr_observations.bundle(response.get("observations"))

    def probe_intent(self, idempotency_key):
        """Return the bridge's prior result for a durable Core intent.

        Normalized to the ``EmrSyncConnector.probe_intent`` contract:
        ``{"found": True, "result": {...}}`` with the same normalized result
        shape ``execute_intent`` returned, or ``{"found": False}`` when the
        bridge never executed the key.  A probe response that carries
        neither shape is a protocol violation and degrades like any other
        unavailable answer — the journal then falls through to a keyed
        re-send, which the protocol makes safe by contract.
        """
        response = self._request("POST", "/v1/intents/probe", {"idempotency_key": idempotency_key})
        result = response.get("result")
        if response.get("found") is True and isinstance(result, dict):
            return {"found": True, "result": _core_result(result)}
        if response.get("found") is False:
            return {"found": False}
        raise ProviderUnavailable("EMR bridge probe response is not v1-shaped")

    def health(self):
        """Deployment-facing bridge health, with the same auth boundary."""
        return self._request("GET", "/v1/health")

    def capabilities(self):
        """Declared bridge operations; callers must fail closed if absent."""
        return self._request("GET", "/v1/capabilities")

    def supports_auto_resolution(self):
        # v1 deliberately carries durable intents only.  Vendor-specific
        # "find another diary" behavior cannot be inferred across EMRs.
        return False
