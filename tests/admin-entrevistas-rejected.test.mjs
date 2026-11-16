import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { makeFormatGate } from "./fixtures/entrevistas-format-gate.mjs";
import { installDom } from "./fixtures/entrevistas-dom.mjs";
import { until } from "./helpers/wait.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PHONE = JSON.parse(readFileSync(join(ROOT, "packs", "ar", "country.json"), "utf8")).phone;
const MODULE = pathToFileURL(join(ROOT, "app", "modules", "intake", "admin-entrevistas.mjs")).href;
const TEMPLATE = join(ROOT, "app", "modules", "intake", "admin_entrevistas.html");
const GATED = pathToFileURL(join(ROOT, "tests/fixtures/gated-format.mjs")).href;

test("a rejected format import fails the canonical module without publishing globals", async () => {
  const world = installDom();
  const gate = makeFormatGate();
  globalThis.__entrevistasFormatGate = gate;
  globalThis.SANA_COUNTRY_PHONE = PHONE;
  globalThis.window.SANA_STATIC = {
    "platform/format.mjs": `${GATED}?entrevistas-rejected=${Date.now()}`,
    "platform/admin-session.mjs": pathToFileURL(join(ROOT, "static/platform/admin-session.mjs")).href,
    "platform/i18n.mjs": pathToFileURL(join(ROOT, "static/platform/i18n.mjs")).href,
    "platform/admin-filter-controls.mjs": pathToFileURL(join(ROOT, "static/platform/admin-filter-controls.mjs")).href,
  };
  globalThis.window.sanaImportStatic = (name) => import(globalThis.window.SANA_STATIC[name]);
  try {
    gate.fail();
    await assert.rejects(
      () => import(`${MODULE}?entrevistas-rejected=${Date.now()}`),
      /synthetic format load failure/,
    );
    assert.equal(typeof globalThis.SanaEntrevistas, "undefined");
    assert.equal(typeof globalThis.sanaRouteEntrevistas, "undefined");
    assert.equal(world.host.innerHTML, "");
  } finally {
    world.restore();
  }
});

test("a rejected controller import paints the existing retry recovery from the template bootstrap", async () => {
  const world = installDom();
  const dir = mkdtempSync(join(tmpdir(), "entrevistas-bootstrap-"));
  try {
    const host = world.host;
    host.setAttribute("data-load-error", "No se pudo cargar.");
    host.setAttribute("data-retry", "Retry");
    let reloaded = 0;
    world.location.reload = () => { reloaded += 1; };
    const html = readFileSync(TEMPLATE, "utf8");
    const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1]
      .replace(
        /var url = "{{ module_static\('modules\/intake\/admin-entrevistas\.mjs'\) }}";/,
        'var url = "data:text/javascript,throw new Error(\\"synthetic entrevistas import failure\\")";',
      );
    const bootFile = join(dir, "entrevistas-bootstrap.mjs");
    writeFileSync(bootFile, script);
    await import(`${pathToFileURL(bootFile).href}?t=${Date.now()}`);
    await until(() => /data-ent-retry/.test(host.innerHTML), "the retry recovery paints");
    assert.match(host.innerHTML, /No se pudo cargar/);
    assert.match(host.innerHTML, /data-ent-retry/);
    const retry = host.querySelector("[data-ent-retry]");
    retry.dispatchEvent({ type: "click", target: retry });
    assert.equal(reloaded, 1);
    assert.equal(typeof globalThis.SanaEntrevistas, "undefined");
  } finally {
    world.restore();
    rmSync(dir, { recursive: true, force: true });
  }
});
