#!/usr/bin/env python3
"""scripts/record_cassette.py — MANUAL, human-run cassette recorder.

Not wired into any test, hook, or CI job — see Area 3 in
docs/roadmap/tier3-harness-design.md: "Recording is a deliberate, manual,
occasional act — never part of any automated rung." Run it on a machine that
can actually reach the connector's real endpoint (Tailscale to `hsi-lab`, the
local HSI dev container, etc.), then **read the diff before committing** — the
PII scrub (pii.py + the connector's own fields.py denylist) is a best-effort
net, not a guarantee, same honest limit pii.py itself documents.

Usage (HSI, the only connector wired up so far — PUCO/RENAPER/Meta repeat this
same shape, slice 6):

    python3 scripts/record_cassette.py --connector hsi --scenario push_turno_ok \\
        --dni 99887766 --nombre JUAN --apellido PRUEBA --nacimiento 1980-05-05 \\
        --sexo M --especialidad Cardiología --when 2026-07-09T09:00:00

Requires HSI_URL (or .env's default localhost:8280) to actually be reachable
— TURNOS_SKIP_HSI must NOT be set, or push_turno() will short-circuit before
ever calling the network and nothing will be recorded.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CASSETTES_DIR = os.path.join(ROOT, "tests", "cassettes")
if CASSETTES_DIR not in sys.path:
    sys.path.insert(0, CASSETTES_DIR)


def _record_hsi(args):
    from app.connectors.hsi import hsi_client
    import _vcr  # noqa

    with _vcr.cassette(
        "hsi", args.scenario,
        source_note="Recorded live against HSI_URL=%s by scripts/record_cassette.py"
                     % os.environ.get("HSI_URL", "http://localhost:8280"),
    ):
        result = hsi_client.push_turno(
            args.dni, args.nombre, args.apellido, args.nacimiento, args.sexo,
            args.especialidad, args.when, medico=args.medico,
            sobreturno=args.sobreturno,
        )
    print("push_turno() ->", result)


_CONNECTORS = {"hsi": _record_hsi}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connector", required=True, choices=sorted(_CONNECTORS))
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--dni", default="99887766")
    ap.add_argument("--nombre", default="JUAN")
    ap.add_argument("--apellido", default="PRUEBA")
    ap.add_argument("--nacimiento", default="1980-05-05")
    ap.add_argument("--sexo", default="M")
    ap.add_argument("--especialidad", default="Cardiología")
    ap.add_argument("--when", default=None,
                    help="ISO datetime; default = tomorrow 09:00")
    ap.add_argument("--medico", default=None)
    ap.add_argument("--sobreturno", action="store_true")
    args = ap.parse_args()

    if os.environ.get("CI"):
        sys.exit("record_cassette.py must never run in CI — this is a manual, "
                 "human-reviewed act (Area 3, docs/roadmap/tier3-harness-design.md)")
    if not args.when:
        from datetime import datetime, timedelta
        args.when = (datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
                     + timedelta(days=1)).isoformat(timespec="seconds")

    os.environ["TURNOS_CASSETTE_MODE"] = "record"
    os.environ.pop("TURNOS_SKIP_HSI", None)
    _CONNECTORS[args.connector](args)
    print("Wrote tests/cassettes/%s/%s.json — READ THE DIFF before committing."
          % (args.connector, args.scenario))


if __name__ == "__main__":
    main()
