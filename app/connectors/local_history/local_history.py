"""Read a deployment-private clinical-history archive by DNI.

The file uses the normalized Alepo/Marqui demo shape::

    {"schema_version": 1, "patients": {"20456781": [{...}, ...]}}

The JSON is an import source and belongs outside git. Runtime reads the
normalized rows from Postgres first, then falls back to that file for local
development and one-step migration. Missing data is a real empty result;
invalid configuration raises ProviderUnavailable so a doctor's screen keeps
working with its other sources.
"""

import copy
import json
import os

from app.connectors import ProviderUnavailable


_ENV = "TURNOS_CLINICAL_HISTORY_PATH"
_PERSISTENT_DEFAULT = "/app/data/private/clinical-history.json"
_DEFAULT_SYSTEM = "local_history"
_MAX_RECORDS = 1000


def _path():
    configured = (os.environ.get(_ENV) or "").strip()
    if configured:
        return configured
    if os.path.isfile(_PERSISTENT_DEFAULT):
        return _PERSISTENT_DEFAULT
    return ""


def configured():
    """Whether this installation has opted into a private local archive."""
    return bool(_path())


def _load():
    path = _path()
    if not path:
        raise ProviderUnavailable(f"local_history: {_ENV} is not configured")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ProviderUnavailable(f"local_history: cannot read configured archive: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("patients"), dict):
        raise ProviderUnavailable("local_history: archive must contain a patients object")
    return data


def lookup_by_dni(dni):
    dni = "".join(c for c in str(dni or "") if c.isdigit())
    if not dni:
        return []
    try:
        from app.core import db_clinical
        stored = db_clinical.clinical_history_by_dni(dni)
    except Exception as exc:  # noqa: BLE001 — connector degradation contract
        if not configured():
            raise ProviderUnavailable(f"local_history: database unavailable: {exc}") from exc
        stored = []
    if stored:
        return _normalize(stored)
    if not configured():
        return []
    data = _load()
    raw = data["patients"].get(dni) or []
    if not isinstance(raw, list):
        raise ProviderUnavailable("local_history: patient entry must be a list")
    return _normalize(raw)


def _normalize(raw):
    out = []
    for item in raw[:_MAX_RECORDS]:
        if not isinstance(item, dict):
            continue
        row = copy.deepcopy(item)
        row.setdefault("sistema", _DEFAULT_SYSTEM)
        row.setdefault("sistema_label", "Archivo clínico")
        out.append(row)
    out.sort(key=lambda row: str(row.get("fecha") or ""))
    return out
