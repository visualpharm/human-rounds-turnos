"""legacy_history.py - what the two legacy digitized-history connectors share (2026-10-28).

`connectors/alepo/alepo.py` and `connectors/marqui/marqui.py` were the same 118 lines but for three things:
the system's name, the fake-mode variable and the sample file next to the module. Those three stay in each
module (with the account of the system, its era and why real mode is not configured); the rest lives here:
the `TURNOS_FAKE_<SYSTEM>=1` switch (read at every call), the stamp and the date order, the deterministic
template for a DNI the sample file does not know, `ProviderUnavailable` in real mode and the command-line
mock. `tests/test_legacy_history_contract.py` pins what each system answers.

One of the two systems ("Alepo" and "Marqui", per reference-deployment counterpart at the
2026-07-08 hospital meeting) that older paper clinical history was digitized
into, as scanned PDFs linked by DNI. Neither an access path (API? DB export?
file share?) nor credentials exist yet for the REAL system — the reference-deployment counterpart has not
identified how these records are reachable. Real mode therefore stays an
honest "not configured" raise: when the reference-deployment counterpart identifies the access path,
replace `_lookup_real` with the real client and keep the same public shape
(`lookup_by_dni`).

What DOES exist is a fake mode for the demo site and tests (the framework's
standard `TURNOS_FAKE_*` pattern — docs/connectors.md's PR checklist requires
every connector to be testable without its real provider):

    TURNOS_FAKE_<SYSTEM>=1  →  lookup_by_dni() serves sample_historias.json,
                            fully synthetic OCR-style patients keyed by the
                            demo DNIs of data/test-identities.json. Unknown
                            DNI → a deterministic template history (hash-
                            picked from the file's "plantillas", dates kept
                            inside this system's coverage era), so any
                            patient on the demo site has something on file.

Entry shape (both modes must keep it): list of dicts with `fecha` (ISO),
`tipo` (evolucion|laboratorio|receta|epicrisis|interconsulta|imagen|
vacunacion), `servicio`, `profesional`, `texto` (OCR-style), optional
`valores` (canonical lab keys) / `medicamentos`, `fuente`, `calidad_ocr`,
plus `sistema` stamped by this module.
"""
from __future__ import annotations

import hashlib
import json
import os

from app.connectors import ProviderUnavailable


class LegacySystem:
    """One legacy system: its name, its fake-mode variable and its sample file."""

    def __init__(self, sistema, fake_env, sample_path):
        self.sistema = sistema
        self.fake_env = fake_env
        self.sample_path = sample_path
        self._cache = None

    def _sample_data(self):
        if self._cache is None:
            with open(self.sample_path, encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    def fake_enabled(self):
        return (os.environ.get(self.fake_env) or "").strip() == "1"

    def _stamp(self, entries):
        out = []
        for e in entries:
            e = dict(e)
            e["sistema"] = self.sistema
            out.append(e)
        return sorted(out, key=lambda e: e.get("fecha") or "")

    @staticmethod
    def _template_for(dni, data):
        """Deterministic fallback for a DNI the sample file doesn't know: pick a
        plantilla by DNI hash so the demo never shows an empty prior history,
        and the same DNI always gets the same one."""
        plantillas = data.get("plantillas") or {}
        if not plantillas:
            return []
        keys = sorted(plantillas.keys())
        h = int(hashlib.sha256(str(dni).encode()).hexdigest(), 16)
        return plantillas[keys[h % len(keys)]] or []

    def _lookup_fake(self, dni):
        data = self._sample_data()
        dni = str(dni or "").strip()
        entries = (data.get("pacientes") or {}).get(dni)
        if entries is None:
            entries = self._template_for(dni, data)
        return self._stamp(entries)

    def _lookup_real(self, dni):
        raise ProviderUnavailable(
            f"{self.sistema}: no access path configured yet — needs reference-deployment counterpart to "
            "identify how these records are reachable"
        )

    def lookup_by_dni(self, dni):
        """[{...}] prior-history entries for this DNI (may be empty — a real
        "nothing on file"), or ProviderUnavailable when the system can't be
        queried. See connectors/__init__.py's ClinicalHistoryLookupConnector."""
        if self.fake_enabled():
            return self._lookup_fake(dni)
        return self._lookup_real(dni)

    def main(self, argv):
        if len(argv) != 1:
            print(f"uso: python3 -m connectors.{self.sistema}.{self.sistema} <dni>")
            return 2
        os.environ.setdefault(self.fake_env, "1")  # the CLI is a mock-data tool
        print(json.dumps(self.lookup_by_dni(argv[0]), ensure_ascii=False, indent=2))
        return 0
