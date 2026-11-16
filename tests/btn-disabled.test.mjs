// A disabled button looks inactive, from ONE rule (human-rounds.css) over
// ONE token trio (tokens.css) — 2026-10-24: «Crear turno» disabled looked the
// same as an enabled one. No component paints its own :disabled for a button
// that rule already covers.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { patientSiteSource } from "./helpers/patient-site-source.mjs";

const leer = (n) => (n === "patient_web/site_*.py" ? patientSiteSource()
  : readFileSync(new URL("../" + n, import.meta.url), "utf8"));

const CLASES = [".btn", ".stz-enter", ".stz-cta", ".wz-cta-lg", ".sc-cta", ".sc-scan-btn", ".queue-seg>button"];

test("tokens.css defines the inactive-button trio once", () => {
  const css = leer("static/root/tokens.css");
  for (const t of ["--btn-off-bg", "--btn-off-fg", "--btn-off-bd"]) {
    assert.equal(css.split(t + ":").length - 1, 1, t + " must be defined exactly once");
  }
});

test("human-rounds.css has the one inactive rule, with hover/press neutralised and not-allowed", () => {
  const css = leer("static/root/human-rounds.css");
  const i = css.indexOf("Botón INACTIVO");
  assert.ok(i > 0, "the inactive-button rule is missing");
  const regla = css.slice(i, i + 2200);
  for (const c of CLASES) assert.ok(regla.includes(c), c + " not covered by the inactive rule");
  assert.match(regla, /\[aria-disabled="true"\]/);
  assert.match(regla, /:is\(:hover,:active\)/);
  assert.match(regla, /cursor:not-allowed/);
  assert.match(regla, /background-image:none/);
  assert.match(regla, /transform:none/);
});

test("no component repaints :disabled for a covered button", () => {
  const fuentes = ["patient_web/site_*.py", "app/modules/turnos/service.mjs", "static/platform/account-form.mjs", "static/platform/admin-queue.css", "static/root/admin-direct.css", "static/root/admin-gate.css"];
  for (const f of fuentes) {
    const txt = leer(f);
    for (const c of CLASES) {
      const re = new RegExp(c.replace(".", "\\.") + "[^{,]*:disabled\\s*\\{");
      assert.ok(!re.test(txt), f + " repaints " + c + ":disabled — the shared rule owns it");
    }
  }
});
