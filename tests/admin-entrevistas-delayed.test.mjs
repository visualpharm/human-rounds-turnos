import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { makeFormatGate } from "./fixtures/entrevistas-format-gate.mjs";
import { installDom, seed } from "./fixtures/entrevistas-dom.mjs";
import { stays } from "./helpers/wait.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PHONE = JSON.parse(readFileSync(join(ROOT, "packs", "ar", "country.json"), "utf8")).phone;
const MODULE = pathToFileURL(join(ROOT, "app", "modules", "intake", "admin-entrevistas.mjs")).href;
const GATED = pathToFileURL(join(ROOT, "tests/fixtures/gated-format.mjs")).href;

test("a delayed format import holds the canonical module; preview paints after release", async () => {
  const world = installDom();
  const gate = makeFormatGate();
  globalThis.__entrevistasFormatGate = gate;
  globalThis.SANA_COUNTRY_PHONE = PHONE;
  globalThis.window.SANA_STATIC = {
    "platform/format.mjs": `${GATED}?entrevistas-delayed=${Date.now()}`,
    "platform/admin-session.mjs": pathToFileURL(join(ROOT, "static/platform/admin-session.mjs")).href,
    "platform/i18n.mjs": pathToFileURL(join(ROOT, "static/platform/i18n.mjs")).href,
    "platform/admin-filter-controls.mjs": pathToFileURL(join(ROOT, "static/platform/admin-filter-controls.mjs")).href,
  };
  globalThis.window.sanaImportStatic = (name) => import(globalThis.window.SANA_STATIC[name]);
  try {
    const pending = import(`${MODULE}?entrevistas-delayed=${Date.now()}`);
    let settled = false;
    pending.then(() => { settled = true; }, () => {});
    await stays(() => settled === false, "the canonical module stays pending while format is held", 30);
    assert.equal(settled, false, "the canonical module must stay pending while format is held");
    assert.equal(typeof globalThis.SanaEntrevistas, "undefined");
    assert.equal(typeof globalThis.sanaRouteEntrevistas, "undefined");
    assert.equal(world.host.innerHTML, "");
    gate.release();
    const api = await pending;
    await api.preview(world.host, seed());
    assert.match(world.host.innerHTML, /Ana Alvarez/);
    assert.equal(typeof globalThis.window.SanaEntrevistas, "undefined");
  } finally {
    gate.release();
    world.restore();
  }
});
