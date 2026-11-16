// TASK-58 (26.10): on the demo, walk-in services (curaciones, vacunación,
// demanda espontánea…) carried the generic sign «No necesitás turno. Se
// atiende por orden de llegada.» as their schedule: no hours, no «Abierto
// ahora». A persona at 22:30 on a Sunday could not tell whether the centre
// was open. Every walk-in of the demo pack has hours the page can read.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as H from "../static/platform/hours.mjs";

const servicios = JSON.parse(readFileSync(new URL("../packs/example-town/servicios.json", import.meta.url), "utf8")).servicios;

test("the demo service's own hours read as a whole week", () => {
  // The seed overlays the demo service's `horario` (scripts/seed_demo_all.py);
  // it is a PARSED string: «Todos los dias de 8 a 20 h» parsed to nothing and
  // the service page lost its opening hours.
  const seed = readFileSync(new URL("../scripts/seed_demo_all.py", import.meta.url), "utf8");
  const horario = /^_HORARIO_DEMO = "([^"]+)"$/m.exec(seed);
  assert.ok(horario, "the seed declares _HORARIO_DEMO as a plain string");
  const semana = H.parse(horario[1]);
  assert.deepEqual(Object.keys(semana || {}), ["0", "1", "2", "3", "4", "5", "6"]);
  for (const tramos of Object.values(semana)) assert.deepEqual(tramos, [{ start: 480, end: 1200 }]);
});

test("every walk-in service of the demo pack has readable hours", () => {
  const walkins = servicios.filter((s) => String(s.modo || "").startsWith("walkin"));
  assert.ok(walkins.length > 10);
  // A 24-hour desk says so in words; every other walk-in has days and hours.
  const sinHoras = walkins.filter((s) => {
    if (/24 horas/.test(s.horario || "")) return false;
    const p = H.parse(s.horario || "");
    return !p || !Object.keys(p).length;
  }).map((s) => s.id + ": " + s.horario);
  assert.deepEqual(sinHoras, []);
});
