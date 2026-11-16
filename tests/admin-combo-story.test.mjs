// Focused unit contract for the AdminCombo catalog slice. Rendered Storybook
// acceptance lives in tests/storybook/admin-combo-browser.mjs and requires an
// explicit URL.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import * as Combo from "../static/platform/admin-combo.mjs";
import * as HoursEditor from "../static/platform/admin-hours-editor.mjs";
import {
  SVC_COMPAT,
  franjaServicioHtml,
} from "../stories/admin-combo-fixture.mjs";

const ROOT = new URL("..", import.meta.url).pathname;
const DIRECT_CSS = readFileSync(ROOT + "static/root/admin-direct.css", "utf8");

test("the real combo API filters the synthetic catalog without a replacement renderer", () => {
  const items = SVC_COMPAT.map((service) => ({ v: service.id, t: service.nombre, s: service.centro }));
  assert.deepEqual(
    Combo.filter(items, "presion").map((item) => item.v),
    ["enfermeria-presion-riverside-clinic"],
  );
  assert.equal(Combo.filter(items, "no existe").length, 0);
  const html = Combo.html("fr-svc-sel", items, "", "", "Sin especificar", "Servicio");
  assert.match(html, /class="fr-svc-sel"/);
  assert.match(html, /class="cmb-in"/);
  assert.match(html, /class="cmb-list"/);
});

test("the shared combo keeps its compact desktop baseline and gives phone inputs and options 44px targets", () => {
  const actualMarkup = Combo.html("fr-svc-sel", SVC_COMPAT, "", "", "Sin especificar", "Servicio");
  assert.match(actualMarkup, /class="cmb-in"/);
  assert.match(actualMarkup, /class="cmb-list"/);
  assert.match(DIRECT_CSS, /\.cmb-in\{font:inherit;font-size:13px;/,
    "the shared desktop baseline remains compact");
  assert.match(DIRECT_CSS,
    /@media\(max-width:720px\),\(pointer:coarse\)\{\s*\.cmb-in\{font-size:16px;min-height:44px;box-sizing:border-box;\}\s*\.cmb-it,\.cmb-none\{font-size:16px;min-height:44px;padding-block:12px;box-sizing:border-box;white-space:normal;overflow-wrap:anywhere;\}/,
    "the one shared mobile rule covers editable inputs and both catalog result states");
  assert.match(DIRECT_CSS, /\.cmb-list\{[^}]*max-height:280px;overflow:auto;/,
    "a long catalog remains scrollable when its rows become touch sized");
});

test("DoctorHours injects the combo through HoursEditor's real servicioHtml seam", () => {
  const service = SVC_COMPAT.find((item) => item.id === "cardiologia-sampleton-hospital");
  const injected = HoursEditor.franjaRowHtml(
    0, 0, { dias: [1], servicio: service.id }, "sampleton-hospital", { servicioHtml: franjaServicioHtml },
  );
  assert.match(injected, /class="fr-svc-sel"/,
    "the injected production-shaped boundary supplies the canonical hidden input");
  assert.match(injected, /class="cmb-in"/,
    "the injected boundary supplies the real typeable combo markup");
  assert.match(injected, /Cardiolog.a/);

  const nativeFallback = HoursEditor.franjaRowHtml(
    0, 0, { dias: [1], servicio: service.id }, "sampleton-hospital", { servicios: () => [service] },
  );
  assert.match(nativeFallback, /<select class="hor-sel fr-svc-sel/,
    "the control proves it would otherwise be the native-select fallback");

  assert.throws(
    () => HoursEditor.franjaRowHtml(0, 0, { dias: [1] }, "sampleton-hospital", {
      servicioHtml() { throw new Error("missing actual combo renderer"); },
    }),
    /missing actual combo renderer/,
    "a missing renderer cannot silently fall back once the production seam is wired",
  );
});

test("the catalog stories are registered and keep the async boundary out of their exports", () => {
  for (const file of ["stories/AdminCombo.stories.js", "stories/DoctorHours.stories.js"]) {
    assert.ok(existsSync(ROOT + file));
    const source = readFileSync(ROOT + file, "utf8");
    assert.doesNotMatch(source, /async\s*(?:function|\()/,
      `${file} must return a Node synchronously for the HTML renderer`);
  }
  const comboSource = readFileSync(ROOT + "stories/AdminCombo.stories.js", "utf8");
  assert.match(comboSource, /from "\.\.\/static\/platform\/admin-combo\.mjs"/);
  assert.match(comboSource, /SeleccionOrdinaria/);
  assert.match(comboSource, /BusquedaSinResultados/);
  assert.match(comboSource, /TextoLibre/);
  assert.match(comboSource, /EtiquetasLargas/);
});
