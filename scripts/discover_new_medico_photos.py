#!/usr/bin/env python3
"""Find official portrait sources for newly imported clinicians.

The inexpensive discovery pass calls Colegio de Medicos Distrito IX directly;
it makes no LLM, image-generation, or web-search requests.  It considers only
doctors added since the current roster's parent commit, excludes demo and
non-person rows, and produces an auditable review file.  Only exact/high
confidence matches with an actual official portrait are eligible to update
``medico-fotos.json`` in the configured private town pack.

Usage:
  python3 scripts/discover_new_medico_photos.py --discover
  python3 scripts/discover_new_medico_photos.py --apply data/medico-photos-src/discovery-*.json

``--apply`` only records a provenance-backed Colmed9 source.  It does not
download, generate, crop, or publish any portrait; use the existing
``build_medico_photos.py`` pipeline afterwards.  That keeps external photo
discovery separate from the paid image-edit batch.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import sitedata  # noqa: E402

MEDICOS = Path(sitedata.path("medicos.json"))
FOTOS = Path(sitedata.path("medico-fotos.json"))  # movido 2026-07-30
OUT_DIR = ROOT / "data" / "medico-photos-src"
COLMED9 = Path.home() / "projects" / "salud-pinamar" / "scripts" / "colmed9.py"

# These rows aren't individual real people, so must never acquire a portrait.
NON_PERSON_IDS = {"enfermeria", "tecnico-a"}
NON_MD_SPECIALTIES = {
    "enfermería", "kinesiología", "nutrición", "obstetricia", "odontología",
    "psicología", "psicología infantil", "terapia ocupacional", "lic. psicomotricidad",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _doctors_from_parent() -> set[str]:
    """IDs before the latest committed roster import, with a safe empty
    fallback. Uses MEDICOS' path RELATIVE TO ROOT at HEAD (resolved via
    sitedata.path, so it tracks medicos.json wherever the active pack chain
    currently has it through the configured private town pack; webdata/
    before) — not a hardcoded webdata/ literal, which would silently return
    an empty set (== "treat every current doctor as new") for every commit
    after the file's pack-chain location last changed."""
    rel = MEDICOS.relative_to(ROOT).as_posix()
    try:
        raw = subprocess.check_output(
            ["git", "show", f"HEAD^:{rel}"], cwd=ROOT, text=True,
        )
    except subprocess.CalledProcessError:
        return set()
    doc = json.loads(raw)
    return {m["id"] for m in doc.get("medicos", doc)}


def _load_colmed9():
    if not COLMED9.is_file():
        raise SystemExit(f"Missing authoritative Colmed9 helper: {COLMED9}")
    spec = importlib.util.spec_from_file_location("colmed9", COLMED9)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _candidates(all_unmapped: bool) -> list[dict]:
    roster = _load_json(MEDICOS).get("medicos", [])
    mapped = {k for k in _load_json(FOTOS) if not k.startswith("_")}
    old = _doctors_from_parent()
    selected = []
    for doctor in roster:
        ident = doctor["id"]
        if ident in mapped or ident in NON_PERSON_IDS or ident.startswith("demo-"):
            continue
        if doctor.get("especialidad", "").casefold() in NON_MD_SPECIALTIES:
            continue
        if not all_unmapped and ident in old:
            continue
        selected.append(doctor)
    return selected


def _name_parts(doctor: dict) -> tuple[str, str]:
    """Return (surname, given names) from either v2 or imported roster rows."""
    if doctor.get("apellido") and doctor.get("nombre_pila"):
        return doctor["apellido"], doctor["nombre_pila"]
    # The official XLSX import currently has only ``nombre``.  Its people are
    # written Given-name(s) Surname, so the last token is the least-assumptive
    # Colmed9 lookup key.  Ambiguous compound surnames are left for review.
    tokens = doctor["nombre"].split()
    return tokens[-1], " ".join(tokens[:-1])


def discover(all_unmapped: bool) -> Path:
    colmed9 = _load_colmed9()
    results = []
    for doctor in _candidates(all_unmapped):
        surname, given = _name_parts(doctor)
        result = colmed9.lookup(surname, given, want_photo=True)
        match = result.get("match") or {}
        # High confidence is an exact/same-first-name identity match.  Specialty
        # is deliberately retained for human review rather than guessed from a
        # differently named taxonomy.
        eligible = result.get("confidence") == "high" and bool(match.get("has_photo"))
        results.append({
            "id": doctor["id"],
            "nombre": doctor["nombre"],
            "especialidad_sana": doctor.get("especialidad"),
            "eligible": eligible,
            "confidence": result.get("confidence"),
            "match": match if result.get("found") else None,
            "namesakes": result.get("namesakes", []),
        })
        state = "eligible" if eligible else result.get("confidence", "none")
        print(f"{doctor['id']}: {state}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"discovery-{stamp}.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Colegio de Médicos Distrito IX",
        "criteria": "new roster IDs; high-confidence name match; official portrait exists",
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return out


def apply(path: Path) -> None:
    report = _load_json(path)
    photos = _load_json(FOTOS)
    added = []
    for item in report["results"]:
        match = item.get("match") or {}
        if not item.get("eligible") or item["id"] in photos:
            continue
        photos[item["id"]] = {
            "colmed9": match["foto_matricula"],
            "_prov": (
                "Colegio de Médicos Distrito IX; high-confidence name match; "
                f"official photo verified {match.get('verify_url', '')}"
            ),
        }
        added.append(item["id"])
    FOTOS.write_text(json.dumps(photos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("added " + (", ".join(added) if added else "no eligible photo sources"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--all-unmapped", action="store_true")
    parser.add_argument("--apply", type=Path)
    args = parser.parse_args()
    if args.discover:
        discover(args.all_unmapped)
    if args.apply:
        apply(args.apply)
    if not args.discover and not args.apply:
        parser.error("choose --discover and/or --apply REPORT.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
