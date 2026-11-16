"""test_pack_gap_decisions.py — the demo-vs-prod pack-gap DECISION gate.

2026-08-08 (tracking card 2026-08-07-human-rounds-audit-demo-vs-prod-data-pack-
gaps-one-codebase-). The class of bug this guards: a file that exists only in
the Pinamar chain silently does not exist for the demo chain — the engine
degrades instead of crashing, so the gap ships as a missing section on
demo.humanrounds.org and nobody notices (the empty assistant wiki,
2026-08-06, was the first instance). The rule this test enforces: every such
gap gets ONE explicit recorded decision — (a) demo content added, (b) made
composable, or (c) documented as by-design degradation — in
scripts/pack_gap_report.py's DECIDED registry, mirrored for humans in
docs/architecture/data-packs.md's "Demo-vs-prod pack expectations" table.

Three assertions, all against the live pack tree:
  1. No UNDECIDED gap: pack_gap_report.compute_gaps() finds nothing that is
     neither OFFLINE nor DECIDED. Adding a new Pinamar-chain-only file makes
     this fail until someone records its decision.
  2. No STALE decision: every DECIDED entry still corresponds to a current
     gap. Fixing a gap (e.g. adding the demo file) without pruning its
     registry entry fails here — the registry never rots into folklore.
  3. Doc mirror: every DECIDED name appears in data-packs.md's expectations
     section, so the human-readable table can't drift from the enforced one.

The report script mutates os.environ at import (TURNOS_CONFIG_LOCK, pops
TURNOS_WEBDATA_DIR) — run it in a SUBPROCESS so this test never leaks that
into the shared unittest process.
"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PROBE = r"""
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location(
    "pack_gap_report", os.path.join(%r, "scripts", "pack_gap_report.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
g = mod.compute_gaps()
print(json.dumps({
    "undecided": mod.undecided_gaps(g),
    "decided": sorted(mod.DECIDED),
    "offline": sorted(mod.OFFLINE),
    "gap_names": sorted(set(
        g["missing_demo"] + g["missing_prod"]
        + [n for _, n in g["empties"]] + [n for n, _ in g["divergent"]])),
}))
""" % (ROOT,)


class TestPackGapDecisions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ)
        env.pop("TURNOS_WEBDATA_DIR", None)
        if not env.get("TURNOS_PRIVATE_PACKS_DIR"):
            raise unittest.SkipTest(
                "external private Pinamar pack is not configured")
        out = subprocess.run(
            [sys.executable, "-c", _PROBE], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, f"probe falló: {out.stderr[-2000:]}"
        cls.data = json.loads(out.stdout)

    def test_sin_gaps_sin_decision(self):
        self.assertEqual(
            self.data["undecided"], [],
            "Gap demo-vs-prod SIN decisión registrada. Registrá (a)/(b)/(c) "
            "en pack_gap_report.DECIDED y espejalo en la tabla de "
            "docs/architecture/data-packs.md, o arreglá el gap: "
            f"{self.data['undecided']}")

    def test_sin_decisiones_obsoletas(self):
        stale = [n for n in self.data["decided"]
                 if n not in self.data["gap_names"]]
        self.assertEqual(
            stale, [],
            "Entradas de DECIDED cuyo gap ya no existe — podá el registro y "
            f"la tabla de data-packs.md: {stale}")

    def test_tabla_del_doc_espeja_el_registro(self):
        with open(os.path.join(
                ROOT, "docs", "architecture", "data-packs.md"),
                encoding="utf-8") as fh:
            doc = fh.read()
        marker = "## Demo-vs-prod pack expectations"
        self.assertIn(marker, doc,
                      "data-packs.md perdió la sección de expectativas")
        section = doc[doc.index(marker):]
        missing = [n for n in self.data["decided"]
                   if f"`{n}`" not in section]
        self.assertEqual(
            missing, [],
            "Decisiones sin fila en la tabla de expectativas de "
            f"data-packs.md: {missing}")


if __name__ == "__main__":
    unittest.main()
