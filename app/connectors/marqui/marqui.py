"""Marqui — legacy digitized-PDF clinical history system (Pinamar), 1996-2016.

The shared account (what the system is, why real mode is not configured, the fake mode, the entry
shape) lives in `connectors/legacy_history.py`. Here: the fake-mode switch `TURNOS_FAKE_MARQUI=1`
and the sample file next to this module.

CLI (mock script for demos/debugging):
    python3 -m connectors.marqui.marqui <dni>   → prints the entries as JSON
"""
from __future__ import annotations

import os

from app.connectors import legacy_history

_SISTEMA = "marqui"
_FAKE_ENV = "TURNOS_FAKE_MARQUI"
_SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "sample_historias.json")

_SYSTEM = legacy_history.LegacySystem(_SISTEMA, _FAKE_ENV, _SAMPLE)


def lookup_by_dni(dni):
    """[{...}] prior-history entries for this DNI (may be empty — a real
    "nothing on file"), or ProviderUnavailable when the system can't be
    queried. See connectors/__init__.py's ClinicalHistoryLookupConnector."""
    return _SYSTEM.lookup_by_dni(dni)


def main(argv):
    return _SYSTEM.main(argv)


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
