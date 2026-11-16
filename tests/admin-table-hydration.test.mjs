import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../static/platform/admin-table.mjs", import.meta.url), "utf8")
  .replace("export function crear(cfg) {", "function crear(cfg) {")
  .replace("export { columnas };", "")
  .replace("export { exportHtml, collectExport, exportTable };", "")
  .replace("export { barrer };", "globalThis.__table = { crear: crear, columnas: columnas, barrer: barrer };");

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function tableWindow(moduleReady) {
  const document = {
    readyState: "loading",
    body: { classList: { add() {}, remove() {} }, appendChild() {} },
    head: { appendChild() {} },
    addEventListener() {}, getElementById() { return null; }, querySelectorAll() { return []; },
    createElement() { return { style: {}, setAttribute() {}, appendChild() {} }; }
  };
  const window = {
    document, sanaImportStatic: () => moduleReady.promise, addEventListener() {},
    requestAnimationFrame(fn) { fn(); }, getComputedStyle() { return {}; }, innerWidth: 1200, innerHeight: 800
  };
  const context = vm.createContext({
    window, document, MutationObserver: class { observe() {} }, requestAnimationFrame: window.requestAnimationFrame,
    getComputedStyle: window.getComputedStyle, setTimeout, clearTimeout, console
  });
  vm.runInContext(source, context, { filename: "static/platform/admin-table.mjs" });
  return context.__table;
}

test("an edit before the preferences module resolves survives hydration and persists", async () => {
  const moduleReady = deferred();
  const getReady = deferred();
  const writes = [];
  const table = tableWindow(moduleReady);

  const cols = [{ key: "name", label: "Name", default: true }, { key: "status", label: "Status", default: true }];
  const changes = [];
  const group = table.columnas({
    clave: "queue", cols, onChange: (active) => changes.push(active.map((column) => column.key))
  });
  group._set(["name"]);

  const values = {};
  moduleReady.resolve({
    load: () => getReady.promise.then((items) => Object.assign(values, items)),
    get: (key, fallback) => Object.prototype.hasOwnProperty.call(values, key) ? values[key] : fallback,
    set: (key, value) => { values[key] = value; writes.push([key, value]); }
  });
  await Promise.resolve();
  getReady.resolve({ "table.columns.queue": ["status"] });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(Array.from(group.visibles()), ["name"]);
  assert.deepEqual(Array.from(values["table.columns.queue"]), ["name"]);
  assert.equal(writes.length, 1);
  assert.equal(writes[0][0], "table.columns.queue");
  assert.deepEqual(Array.from(writes[0][1]), ["name"]);
  assert.deepEqual(changes, [["name"]]);
});

test("hydration can rebuild its owning picker without recursively repainting", async () => {
  const moduleReady = deferred();
  const table = tableWindow(moduleReady);
  const cols = [{ key: "name", default: true }, { key: "status", default: true }];
  let paints = 0;
  let picker;
  function render() {
    picker = table.columnas({ clave: "queue", cols, onChange() {
      paints++;
      // Bound the broken implementation too, so the regression reports a
      // failure instead of starving the event loop and hanging the suite.
      if (paints < 4) render();
    } });
  }
  render();
  moduleReady.resolve({ load: async () => {}, get: () => ["status"] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(paints, 1, "only the provisional picker requires hydration");
  assert.deepEqual(Array.from(picker.visibles()), ["status"]);
  render();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(paints, 1, "an already hydrated picker must not notify its owner");
});

test("unchanged hydrated defaults do not request a repaint", async () => {
  const moduleReady = deferred();
  const table = tableWindow(moduleReady);
  let paints = 0;
  table.columnas({ clave: "queue", cols: [{ key: "name", default: true }],
    onChange() { paints++; } });
  moduleReady.resolve({ load: async () => {}, get: (_key, fallback) => fallback });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(paints, 0);
});

// One column picker for every table (the product owner 28.07, 05.09): the catalog lists
// (Servicios, Centros, Médicos) moved onto the shared picker. Their people
// had saved a choice under `catalog.columns.<tipo>`; that choice is read once,
// copied to the shared key and kept — nobody loses their columns.
function loadedPrefs(values, writes) {
  return {
    load: async () => values,
    get: (key, fallback) => Object.prototype.hasOwnProperty.call(values, key) ? values[key] : fallback,
    set: (key, value) => { values[key] = value; writes.push([key, value]); }
  };
}
const catalogCols = [
  { key: "nombre", label: "Servicio", always: true },
  { key: "centro", label: "Centro", default: true },
  { key: "modo", label: "Modo", default: true },
  { key: "created", label: "Creado", default: false }
];

test("a catalog choice saved under the old key hydrates and moves to the shared key", async () => {
  const moduleReady = deferred();
  const table = tableWindow(moduleReady);
  const values = { "catalog.columns.servicios": ["centro", "created"] };
  const writes = [];
  const changes = [];
  const picker = table.columnas({ clave: "catalog-servicios", legado: "catalog.columns.servicios", cols: catalogCols,
    onChange: (active) => changes.push(active.map((column) => column.key)) });
  moduleReady.resolve(loadedPrefs(values, writes));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(Array.from(picker.visibles()), ["centro", "created"]);
  assert.deepEqual(changes, [["nombre", "centro", "created"]]);
  assert.deepEqual(Array.from(values["table.columns.catalog-servicios"]), ["centro", "created"]);
  // A later visit reads the shared key, even with the old one still there.
  const again = table.columnas({ clave: "catalog-servicios", legado: "catalog.columns.servicios", cols: catalogCols });
  assert.deepEqual(Array.from(again.visibles()), ["centro", "created"]);
  again._set(["modo"]);
  assert.deepEqual(Array.from(values["table.columns.catalog-servicios"]), ["modo"]);
  const third = table.columnas({ clave: "catalog-servicios", legado: "catalog.columns.servicios", cols: catalogCols });
  assert.deepEqual(Array.from(third.visibles()), ["modo"]);
});

test("an old saved choice passes through the screen's own migration once", async () => {
  const moduleReady = deferred();
  const table = tableWindow(moduleReady);
  const values = { "catalog.columns.medicos": ["login", "centro"] };
  const picker = table.columnas({ clave: "catalog-medicos", legado: "catalog.columns.medicos", cols: catalogCols,
    migrar: (keys) => keys.map((key) => key === "login" ? "cuenta" : key) });
  moduleReady.resolve(loadedPrefs(values, []));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(Array.from(picker.visibles()), ["cuenta", "centro"]);
  assert.deepEqual(Array.from(values["table.columns.catalog-medicos"]), ["cuenta", "centro"]);
});

test("an empty old choice means the defaults, as the catalog always read it", async () => {
  const moduleReady = deferred();
  const table = tableWindow(moduleReady);
  const values = { "catalog.columns.centros": [] };
  const picker = table.columnas({ clave: "catalog-centros", legado: "catalog.columns.centros", cols: catalogCols });
  moduleReady.resolve(loadedPrefs(values, []));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(Array.from(picker.visibles()), ["centro", "modo"]);
  assert.equal(values["table.columns.catalog-centros"], undefined);
});
