#!/usr/bin/env python3
"""scripts/pack_gap_report.py — demo-vs-prod data-pack gap report.

One codebase ships two sites from the same main: prod
(pinamar.humanrounds.org, PACK_CHAIN=global,ar,ar-b,ar-b-pinamar) and the
public demo (demo.humanrounds.org, PACK_CHAIN=global,example-town). Nothing
forces the two chains to stay in step: a file added to Pinamar's pack, or a
new field the engine starts reading off servicios/medicos items, silently
doesn't exist for the demo — the engine degrades instead of crashing, so the
gap shows up as a missing section on the demo, never as an error.

This script diffs what the two chains actually RESOLVE — the same semantics
data_packs applies at runtime: compose() for the registered composable files
(especialidades/clusters/conocimiento), whole-file override for everything
else — and prints a Markdown gap report:

  - entries one chain resolves and the other doesn't (files AND directories)
  - JSON files resolving to empty content
  - divergent structure: top-level keys and per-item fields present in one
    chain's resolved file and absent from the other's, with coverage counts

Locality CONTENT is expected to differ (different town, different ids and
names); this report flags structural gaps only, never content differences.

Usage:
  python3 scripts/pack_gap_report.py                 # Markdown to stdout
  python3 scripts/pack_gap_report.py --fail-on-gaps  # exit 1 on any real gap
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Env must beat any PACK_CHAIN saved from the Settings panel (DB override),
# and legacy single-dir mode would hide the chain entirely.
os.environ["TURNOS_CONFIG_LOCK"] = "1"
os.environ.pop("TURNOS_WEBDATA_DIR", None)

from app.core import data_packs  # noqa: E402

DEMO_CHAIN = "global,example-town"
PROD_CHAIN = "global,ar,ar-b,ar-b-pinamar"

SKIP = {"pack.json", "README.md", "INVENTORY.json"}

# Offline research/build artifacts: nothing reads these at request time, so
# their absence from a chain is expected, not a runtime gap. Still listed,
# tagged, and excluded from --fail-on-gaps.
OFFLINE = {
    "brila-generation-results.json",
    "centros-brila-info",
    "featured-medic-gpt-sheet.png",
    "fuentes",
    "ordenes-demo",
    "photos-cache",
}

# Recorded decisions for every KNOWN gap (2026-08-08 audit — tracking card
# 2026-08-07-human-rounds-audit-demo-vs-prod-data-pack-gaps-one-codebase-).
# Every entry here is a gap someone LOOKED AT and decided: (a) demo content
# added, (b) made composable, or (c) documented as by-design degradation.
# Still listed and tagged in the report, but excluded from --fail-on-gaps —
# so the guard trips ONLY on a gap nobody has decided yet. The human-readable
# mirror of this registry is the "Demo-vs-prod pack expectations" table in
# docs/architecture/data-packs.md (test_pack_gap_decisions.py asserts both
# stay in sync, and that no entry here goes stale once its gap disappears).
DECIDED = {
    # --- by-design degradation: country/province plumbing with same-semantics
    # hardcoded fallbacks (the demo is an AR-flavored fiction too)
    "country.json": "(c) format/tz/patient_site fall back to the same AR literals",
    "endpoints.json": "(c) PUCO/REFEPS/RENAPER connectors keep their hardcoded national URLs",
    "msd-institutions.json": "(c) MiSalud Digital is PBA/Pinamar-only; connector map moot for the demo",
    "msd_turnos.json": "(c) booking wizard falls back to solicitud-con-orden (msd_data.py:6)",
    # --- by-design degradation: surfaces that don't apply to the demo town
    "clinicas.json": "(c) demo medicos.json has zero `clinica` values — private-clinics surface structurally N/A",
    "clinic-maps-urls.json": "(c) same as clinicas.json",
    "estudios-preparacion-meta.json": "(c) the not-offered veto is LOCAL data by module design (remedies.py docstring); demo has no veto. Flagged: the overlay key is Pinamar-named, and the generator's _meta.no_ofrecidos_actualmente in the demo base file is unread",
    "centros-galeria.json": "(c) gallery fold simply not drawn (site_centers._centers_gallery); gallery is a separate enrichment workstream even for Pinamar",
    "resenas-analisis.json": "(c) demo deliberately uses reports.py _reviews()'s 'curado' fallback over centros-resenas.json — no fabricated analysis for a fictional town",
    "centers": "(c) L5 per-center override packs are per-town operational data (hospital-pinamar's lab note)",
    # --- by-design: per-town photo pipeline artifacts (generated sprites,
    # scraped/AI source photos). The demo has its own pipeline (fotos-medicos/
    # + seed_demo_all.py); a third town gets neither for free — flagged in
    # docs/architecture/data-packs.md, not a runtime gap.
    "photos.json": "(c) per-town photo-pipeline artifact",
    "doctor-photos": "(c) per-town photo-pipeline artifact",
    "doctor-photo-sprite.json": "(c) per-town photo-pipeline artifact",
    "doctor-photo-sprite.webp": "(c) per-town photo-pipeline artifact",
    "medico-photos": "(c) per-town photo-pipeline artifact",
    "medico-fotos.json": "(c) per-town photo-pipeline artifact",
    "medico-photo-sprite.json": "(c) per-town photo-pipeline artifact",
    "medico-photo-sprite.webp": "(c) per-town photo-pipeline artifact",
    "featured-medic-sprite.json": "(c) per-town photo-pipeline artifact",
    "featured-medic-sprite.webp": "(c) per-town photo-pipeline artifact",
    "fotos-medicos": "(c) example-town's AI-synthetic doctor photos; Pinamar has its own pipeline",
    # --- decided divergences (optional fields / locality content asymmetry;
    # the engine tolerates every one of these fields being absent)
    "centros-info.json": "(c) enrichment fields (facts/more/photo_credit/sources vs credit) are optional per centre",
    "conocimiento.json": "(c) the public info pages (slug/page/blocks, /vacunacion, /pension-por-discapacidad…) are national content in the ar packs; the demo town has none and its /<slug> answers 404 by design",
    "centros-salud.json": "(c) location_note is an optional per-centre field",
    "medicos.json": "(c) entrevista_habilitada is a per-doctor OVERRIDE (absent = module default); fuente_nombre/nota/rol optional",
    "servicios.json": "(c) horario_nota/nota/apertura_mismo_dia are optional per-service fields",
    "estudios-preparacion.json": "(c) ACCEPTED FOR NOW: demo's 30 entries still use the old `preparacion` schema; prep_model.py maps it to `antes` only, so avisar/no_hace_falta/despues/resumen sections don't render for demo studies. Content migration backlogged — the generator deliberately never rewrites prep text",
    "centros-resenas.json": "(a) fictional demo reviews added 2026-08-08; `maps` deliberately absent (no real Google ficha for a fictional town)",
}


def _is_empty(data):
    if data is None:
        return True
    if isinstance(data, (list, dict)) and not data:
        return True
    if isinstance(data, dict):
        return all(v in (None, "", [], {}) for v in data.values())
    return False


def _item_fields(items):
    """field -> how many items carry it, over a list of dicts."""
    out = {}
    for it in items:
        if isinstance(it, dict):
            for k in it:
                out[k] = out.get(k, 0) + 1
    return out


def _analyze(data):
    """(top-level keys | None, {list key: {field: count}}, item count)."""
    if isinstance(data, list):
        return None, {"": _item_fields(data)}, len(data)
    if isinstance(data, dict):
        # An id-keyed map (slug -> record, e.g. centros-info.json): the keys
        # ARE locality content, so comparing them across towns is noise.
        # Compare the records' field coverage instead. `_`-prefixed keys are
        # metadata (`_note`), not records.
        records = {k: v for k, v in data.items() if not k.startswith("_")}
        if len(records) > 1 and all(isinstance(v, dict) for v in records.values()):
            return None, {"": _item_fields(records.values())}, len(records)
        lists = {}
        count = None
        for k, v in data.items():
            if isinstance(v, list) and any(isinstance(x, dict) for x in v):
                lists[k] = _item_fields(v)
                count = max(count or 0, len(v))
        return set(data), lists, count
    return None, {}, None


def chain_view(chain_str):
    """name -> entry dict for every top-level pack entry the chain can see,
    resolved with the real runtime semantics."""
    os.environ["PACK_CHAIN"] = chain_str
    data_packs.invalidate()
    chain = data_packs.active_chain()
    if not chain:
        raise SystemExit(f"chain {chain_str!r} resolved to no scaffolded packs")

    entries = {}
    for pack_id in chain:
        # Public levels resolve in-tree; the production town resolves through
        # TURNOS_PRIVATE_PACKS_DIR after its boot-time inventory admission.
        pack_dir = Path(data_packs.pack_dir(pack_id))
        for p in sorted(pack_dir.iterdir()):
            if p.name in SKIP or p.name.startswith("."):
                continue
            e = entries.setdefault(p.name, {"sources": [], "dir": p.is_dir()})
            e["sources"].append(pack_id)

    for name, e in entries.items():
        if e["dir"]:
            # Directories aren't resolve_path()-able (isfile check); files
            # inside resolve per-file. Winner for counting = highest pack.
            winner = e["sources"][-1]
            root = Path(data_packs.pack_dir(winner)) / name
            e["winner"] = winner
            e["count"] = sum(1 for f in root.rglob("*") if f.is_file())
        elif name.endswith(".json"):
            if data_packs.is_composable(name):
                items = data_packs.compose(name)
                # Effective contributors: a pack declaring "replace" for this
                # file hides every ancestor below it (data_packs.compose()).
                sources = list(e["sources"])
                for pid in reversed(sources):
                    m = data_packs.manifest(pid) or {}
                    if (m.get("compose") or {}).get(name) == "replace":
                        sources = sources[sources.index(pid):]
                        break
                e["winner"] = "composed:" + "+".join(sources)
                e["count"] = len(items)
                e["top"] = None
                e["lists"] = {"": _item_fields(items)}
                e["empty"] = not items
            else:
                path = data_packs.resolve_path(name)
                e["winner"] = Path(path).parent.name
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError) as err:
                    e["error"] = str(err)
                    continue
                e["top"], e["lists"], e["count"] = _analyze(data)
                e["empty"] = _is_empty(data)
        else:
            e["winner"] = e["sources"][-1]
            e["count"] = None
    return chain, entries


def _fmt_side(e):
    if e is None:
        return "—"
    if e.get("error"):
        return f"unreadable ({e['error']})"
    src = e.get("winner") or "+".join(e["sources"])
    if e["dir"]:
        return f"{e['count']} files ({src})"
    if e.get("count") is not None:
        return f"{e['count']} items ({src})"
    return f"present ({src})"


def _diff_fields(a, b):
    """Fields in a and never in b, rendered with a's coverage counts."""
    missing = sorted(set(a) - set(b))
    return ", ".join(f"`{f}` ({a[f]})" for f in missing), missing


def compute_gaps(demo_chain=DEMO_CHAIN, prod_chain=PROD_CHAIN):
    """The report's whole analysis, as data — so test_pack_gap_decisions.py
    can assert on it in-process instead of scraping the Markdown."""
    data_packs.verify_required_packs()
    demo_ids, demo = chain_view(demo_chain)
    prod_ids, prod = chain_view(prod_chain)

    names = sorted(set(demo) | set(prod))
    missing_demo, missing_prod, empties, divergent, ok = [], [], [], [], []

    for name in names:
        d, p = demo.get(name), prod.get(name)
        if d is None:
            missing_demo.append(name)
            continue
        if p is None:
            missing_prod.append(name)
            continue
        row_gaps = []
        for side, e in (("demo", d), ("prod", p)):
            if e.get("empty"):
                empties.append((side, name))
                row_gaps.append("empty")
        if name.endswith(".json") and not d.get("error") and not p.get("error"):
            gaps = []
            dt, pt = d.get("top"), p.get("top")
            if dt is not None and pt is not None:
                only_p = sorted(pt - dt)
                only_d = sorted(dt - pt)
                if only_p:
                    gaps.append("top-level keys only in prod: "
                                + ", ".join(f"`{k}`" for k in only_p))
                if only_d:
                    gaps.append("top-level keys only in demo: "
                                + ", ".join(f"`{k}`" for k in only_d))
            for lk in sorted(set(d.get("lists") or {}) & set(p.get("lists") or {})):
                da, pa = d["lists"][lk], p["lists"][lk]
                label = f"`{lk}` " if lk else ""
                txt, miss = _diff_fields(pa, da)
                if miss:
                    gaps.append(f"{label}item fields only in prod: {txt}")
                txt, miss = _diff_fields(da, pa)
                if miss:
                    gaps.append(f"{label}item fields only in demo: {txt}")
            if gaps:
                divergent.append((name, gaps))
                row_gaps.append("divergent")
        if not row_gaps:
            ok.append(name)

    return {
        "demo_ids": demo_ids, "prod_ids": prod_ids,
        "demo": demo, "prod": prod,
        "missing_demo": missing_demo, "missing_prod": missing_prod,
        "empties": empties, "divergent": divergent, "ok": ok,
    }


def undecided_gaps(g):
    """Names --fail-on-gaps actually trips on: every gap that is neither an
    offline artifact nor covered by a recorded decision in DECIDED."""
    return sorted(set(
        [n for n in g["missing_demo"] + g["missing_prod"]
         if n not in OFFLINE and n not in DECIDED]
        + [n for _, n in g["empties"] if n not in DECIDED]
        + [n for n, _ in g["divergent"] if n not in DECIDED]
    ))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo-chain", default=DEMO_CHAIN)
    ap.add_argument("--prod-chain", default=PROD_CHAIN)
    ap.add_argument("--fail-on-gaps", action="store_true",
                    help="exit 1 if any undecided, non-offline gap is found "
                         "(CI guard; a gap listed in DECIDED has its recorded "
                         "decision and does not fail)")
    args = ap.parse_args()

    g = compute_gaps(args.demo_chain, args.prod_chain)
    demo_ids, prod_ids = g["demo_ids"], g["prod_ids"]
    demo, prod = g["demo"], g["prod"]
    missing_demo, missing_prod = g["missing_demo"], g["missing_prod"]
    empties, divergent, ok = g["empties"], g["divergent"], g["ok"]

    def tag(name):
        if name in OFFLINE:
            return " *(offline artifact)*"
        if name in DECIDED:
            return f" *(decidido: {DECIDED[name]})*"
        return ""

    out = []
    out.append(f"# Demo vs prod data-pack gap report — {date.today().isoformat()}")
    out.append("")
    out.append(f"- demo chain: `{args.demo_chain}` → resolved `{','.join(demo_ids)}`")
    out.append(f"- prod chain: `{args.prod_chain}` → resolved `{','.join(prod_ids)}`")
    out.append("")
    out.append("Generated by `scripts/pack_gap_report.py`. Structural gaps only —")
    out.append("locality content (ids, names, texts) is supposed to differ.")

    out.append("")
    out.append(f"## Missing in the demo chain ({len(missing_demo)})")
    out.append("")
    for name in missing_demo or []:
        out.append(f"- `{name}` — {_fmt_side(prod[name])}{tag(name)}")
    if not missing_demo:
        out.append("- none")

    out.append("")
    out.append(f"## Missing in the prod chain ({len(missing_prod)})")
    out.append("")
    for name in missing_prod or []:
        out.append(f"- `{name}` — {_fmt_side(demo[name])}{tag(name)}")
    if not missing_prod:
        out.append("- none")

    out.append("")
    out.append(f"## Empty ({len(empties)})")
    out.append("")
    for side, name in empties:
        out.append(f"- `{name}` resolves empty in the {side} chain")
    if not empties:
        out.append("- none")

    out.append("")
    out.append(f"## Divergent structure ({len(divergent)})")
    out.append("")
    for name, gaps in divergent:
        out.append(f"### `{name}`{tag(name)}")
        out.append(f"- demo: {_fmt_side(demo[name])} · prod: {_fmt_side(prod[name])}")
        for line in gaps:
            out.append(f"- {line}")
        out.append("")
    if not divergent:
        out.append("- none")

    out.append("")
    out.append(f"## Structurally aligned ({len(ok)})")
    out.append("")
    out.append("| entry | demo | prod |")
    out.append("|---|---|---|")
    for name in ok:
        out.append(f"| `{name}` | {_fmt_side(demo[name])} | {_fmt_side(prod[name])} |")

    print("\n".join(out))

    real_gaps = undecided_gaps(g)
    if args.fail_on_gaps and real_gaps:
        print(f"\n{len(real_gaps)} undecided gap(s) found — record a decision "
              f"in DECIDED (and mirror it in docs/architecture/data-packs.md) "
              f"or fix the gap: {', '.join(real_gaps)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
