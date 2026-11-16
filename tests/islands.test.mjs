// tests/islands.test.mjs — islands.mjs bootstrap + island lifecycle (Tier 5
// slice 3, docs/roadmap/tier5-frontend-design.md "Interactive screens as
// Preact+HTM islands"). islands.mjs is real ESM with no browser DOM available
// in plain Node, so this file proves what's provable WITHOUT a real DOM
// (URL resolution, data-props parsing, the feature-detect guard, the
// mount/update/pending-buffer lifecycle) via dependency injection —
// `mount(el, options)` accepts `importFn`/`urlMap`/`skipFeatureCheck`
// exactly so this is possible without jsdom. The real-DOM, real-Preact
// proof (the pilot island actually painting a month grid, a click
// re-rendering it) is the Playwright pass against the live-served admin
// page, not this file — same split docs/roadmap/tier5-frontend-design.md's
// own "Test harness extension" draws between the frontend suite and the
// e2e tier.
//
// `el` throughout is a minimal hand-built stand-in — just enough surface
// (getAttribute/dataset via getAttribute only, matching what islands.mjs
// actually calls) for these tests to stay real DOM-shaped without pulling
// in jsdom as a new dependency for one test file.
import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = {};
const islands = await import("../static/platform/islands.mjs");
const { mount, scan, unmount, update, _internal } = islands;

test("canonical islands module exposes no compatibility global", () => {
  assert.equal(window.SanaIslands, undefined);
});

function fakeEl(attrs) {
  const store = Object.assign({}, attrs);
  return {
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(store, name) ? store[name] : null; },
  };
}

// =====================================================================
// _internal — piezas puras (sin DOM real, sin dynamic import)
// =====================================================================

test("_internal.readProps: sin data-props da objeto vacío", () => {
  assert.deepEqual(_internal.readProps(fakeEl({ "data-island": "X" })), {});
});

test("_internal.readProps: JSON válido se parsea", () => {
  const el = fakeEl({ "data-props": '{"a":1,"b":"x"}' });
  assert.deepEqual(_internal.readProps(el), { a: 1, b: "x" });
});

test("_internal.readProps: JSON inválido da objeto vacío, no revienta", () => {
  const el = fakeEl({ "data-props": "{not json" });
  assert.deepEqual(_internal.readProps(el), {});
});

test("_internal.readProps: un JSON válido que no es objeto (array/número) también da vacío", () => {
  assert.deepEqual(_internal.readProps(fakeEl({ "data-props": "42" })), {});
  assert.deepEqual(_internal.readProps(fakeEl({ "data-props": "[1,2]" })), {});
});

test("_internal.resolveUrl: toma la URL del mapa por nombre", () => {
  const map = { MedCal: "/static/islands/MedCal.deadbeef.mjs" };
  assert.equal(_internal.resolveUrl("MedCal", map), "/static/islands/MedCal.deadbeef.mjs");
});

test("_internal.resolveUrl: nombre ausente del mapa da null", () => {
  assert.equal(_internal.resolveUrl("Ghost", {}), null);
});

test("_internal.supportsImportMaps: false si HTMLScriptElement no existe (Node)", () => {
  assert.equal(_internal.supportsImportMaps(), false);
});

// =====================================================================
// mount() — inyectando importFn/urlMap/skipFeatureCheck (sin dynamic
// import real, sin necesitar que un browser real resuelva un import map)
// =====================================================================

test("mount(): sin data-island no monta nada (devuelve null)", async () => {
  const el = fakeEl({});
  const res = await mount(el, { skipFeatureCheck: true });
  assert.equal(res, null);
});

test("mount(): sin URL registrada para el nombre no monta nada", async () => {
  const el = fakeEl({ "data-island": "Ghost" });
  const res = await mount(el, { skipFeatureCheck: true, urlMap: {} });
  assert.equal(res, null);
});

test("mount(): sin feature-detect saltado, en Node (sin import maps) no monta", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  const res = await mount(el, { urlMap: { MedCal: "/x.mjs" } }); // skipFeatureCheck NO pasado
  assert.equal(res, null);
});

test("mount(): resuelve la URL, llama importFn, y llama al mount(el, props) del módulo", async () => {
  const el = fakeEl({ "data-island": "MedCal", "data-props": '{"foo":"bar"}' });
  const calls = [];
  let fakeController = { update() {} };
  const importFn = async (url) => {
    calls.push(url);
    return {
      mount(elArg, props) {
        calls.push(["mount", elArg, props]);
        return fakeController;
      },
    };
  };
  const res = await mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/static/islands/MedCal.deadbeef.mjs" },
    importFn,
  });
  assert.equal(res, fakeController);
  assert.equal(calls[0], "/static/islands/MedCal.deadbeef.mjs");
  assert.equal(calls[1][0], "mount");
  assert.equal(calls[1][1], el);
  assert.equal(calls[1][2].foo, "bar");
});

test("mount(): options.props se mezcla con data-props (options.props gana en conflicto)", async () => {
  const el = fakeEl({ "data-island": "MedCal", "data-props": '{"foo":"bar","x":1}' });
  let seenProps;
  const importFn = async () => ({
    mount(_el, props) { seenProps = props; return null; },
  });
  await mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/x.mjs" },
    importFn,
    props: { x: 2, onSelect: () => "callback-real" },
  });
  assert.deepEqual(seenProps, { foo: "bar", x: 2, onSelect: seenProps.onSelect });
  assert.equal(typeof seenProps.onSelect, "function");
});

test("mount(): un módulo que no exporta mount(el, props) no revienta, devuelve null", async () => {
  const el = fakeEl({ "data-island": "Roto" });
  const importFn = async () => ({});
  const res = await mount(el, { skipFeatureCheck: true, urlMap: { Roto: "/roto.mjs" }, importFn });
  assert.equal(res, null);
});

test("mount(): un importFn que rechaza no revienta, devuelve null", async () => {
  const el = fakeEl({ "data-island": "Falla" });
  const importFn = async () => { throw new Error("404 de mentira"); };
  const res = await mount(el, { skipFeatureCheck: true, urlMap: { Falla: "/f.mjs" }, importFn });
  assert.equal(res, null);
});

test("mount(): un segundo mount() sobre el MISMO el no vuelve a montar (idempotente)", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  let callCount = 0;
  const importFn = async () => {
    callCount++;
    return { mount() { return { update() {} }; } };
  };
  const opts = { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn };
  const first = await mount(el, opts);
  const second = await mount(el, opts);
  assert.equal(callCount, 1, "el segundo mount() no debería volver a importar el módulo");
  assert.equal(second, first, "el segundo mount() devuelve el MISMO controller");
});

// =====================================================================
// update() — buffer PENDING cuando el controller todavía no existe
// =====================================================================

test("update(): sobre un el nunca montado no revienta (no hay dueño: se descarta)", () => {
  // Sin controller y sin mount() en vuelo no hay a quién entregarle los
  // props — no se buffera (un update post-dispose no debe poder alimentar
  // al próximo mount del mismo elemento).
  const el = fakeEl({});
  assert.equal(update(el, { selected: "2026-08-12" }), null);
});

test("update() llamado ANTES de que mount() resuelva se aplica apenas el controller existe", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  const seenUpdates = [];
  let resolveImport;
  const importFn = () => new Promise((resolve) => { resolveImport = resolve; });
  const mountPromise = mount(el, { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn });

  // El caller (como cargarMedicoCalendario) llama a update() ANTES de que
  // el dynamic import termine — no debe perderse.
  update(el, { selected: "2026-08-12" });

  resolveImport({
    mount() {
      return { update(props) { seenUpdates.push(props); } };
    },
  });
  await mountPromise;

  assert.deepEqual(seenUpdates, [{ selected: "2026-08-12" }], "el update pendiente se aplicó al montar");
});

test("update() llamado DESPUÉS de montado va directo al controller, sin pasar por PENDING", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  const seenUpdates = [];
  const importFn = async () => ({
    mount() { return { update(props) { seenUpdates.push(props); } }; },
  });
  await mount(el, { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn });
  update(el, { selected: "a" });
  update(el, { selected: "b" });
  assert.deepEqual(seenUpdates, [{ selected: "a" }, { selected: "b" }]);
});

// =====================================================================
// unmount() — el contrato de lifetime compartido: disposición idempotente,
// cancelación de imports/mounts en vuelo, dueño nuevo sobre el mismo
// elemento, y fallos de carga que no envenenan el registro
// =====================================================================

test("unmount(): dispone el controller registrado, limpia el registro y es idempotente", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  const disposed = [];
  const importFn = async () => ({
    mount() { return { update() {}, dispose() { disposed.push(1); } }; },
  });
  await mount(el, { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn });
  assert.equal(unmount(el), true, "la primera llamada libera el island");
  assert.equal(unmount(el), false, "la segunda ya no tiene nada que liberar");
  assert.equal(disposed.length, 1, "dispose() se llama exactamente una vez");
  assert.equal(update(el, { selected: "z" }), null, "sin controller: un update posterior no resucita dueño");
});

test("unmount(): sobre un elemento nunca montado no revienta y no libera nada", () => {
  assert.equal(unmount(fakeEl({ "data-island": "MedCal" })), false);
  assert.equal(unmount(null), false);
});

test("unmount(): el mismo elemento se puede montar de nuevo tras disponer", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  const seenUpdates = [];
  let imports = 0;
  const importFn = async () => {
    imports++;
    return { mount() { return { update(p) { seenUpdates.push(p); } }; } };
  };
  const opts = { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn };
  await mount(el, opts);
  unmount(el);
  const second = await mount(el, opts);
  assert.equal(imports, 2, "el segundo mount() vuelve a importar y montar");
  assert.ok(second, "el remount devuelve el controller nuevo");
  assert.equal(update(el, { selected: "fresh" }), second, "update() rutea al controller NUEVO");
  assert.deepEqual(seenUpdates, [{ selected: "fresh" }]);
});

test("unmount() durante el import en vuelo: el import tardío NUNCA invoca el mount de la isla", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  let resolveImport;
  const importFn = () => new Promise((resolve) => { resolveImport = resolve; });
  const mountPromise = mount(el, { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn });
  assert.equal(unmount(el), true, "libera el claim aunque el import siga pendiente");
  let islandMountCalls = 0;
  resolveImport({ mount() { islandMountCalls++; return { update() {}, dispose() {} }; } });
  assert.equal(await mountPromise, null, "mount() resuelve null tras perder el elemento");
  assert.equal(islandMountCalls, 0, "el mount(el, props) de la isla no se invoca tras el unmount");
  // Y el elemento queda inmediatamente libre para un dueño nuevo:
  const fresh = await mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/x.mjs" },
    importFn: async () => ({ mount() { return { update() {} }; } }),
  });
  assert.ok(fresh, "mismo elemento: un dueño nuevo monta tras la cancelación");
});

test("controller que completa tras el unmount: dispose una sola vez, sin registrarse ni pisar al dueño nuevo", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  // Montaje viejo: import normal, pero su propio mount() queda en vuelo
  // (held). El gate `oldMountStarted` deja determinístico el "el mount de
  // la isla ya fue invocado" antes de que el test quite el elemento.
  let resolveOldMount;
  let oldMountStarted;
  const started = new Promise((resolve) => { oldMountStarted = resolve; });
  const oldPromise = mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/old.mjs" },
    importFn: async () => ({
      mount() {
        oldMountStarted();
        return new Promise((resolve) => { resolveOldMount = resolve; });
      },
    }),
  });
  await started;
  unmount(el); // libera el elemento mientras el mount de la isla está pendiente
  // Un dueño nuevo toma el elemento.
  const seenUpdates = [];
  const fresh = await mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/fresh.mjs" },
    importFn: async () => ({ mount() { return { update(p) { seenUpdates.push(p); } }; } }),
  });
  assert.ok(fresh, "el dueño nuevo monta");
  // El controller viejo completa AHORA, después de perder el elemento.
  let oldDisposed = 0, oldSawProps = 0;
  resolveOldMount({
    update() { oldSawProps++; },
    dispose() { oldDisposed++; },
  });
  assert.equal(await oldPromise, null, "el mount viejo no devuelve su controller al caller");
  assert.equal(oldDisposed, 1, "el controller tardío se dispone exactamente una vez");
  // El dueño nuevo sigue siendo el único registrado:
  update(el, { selected: "nuevo" });
  assert.equal(oldSawProps, 0, "el controller viejo no recibe nada");
  assert.deepEqual(seenUpdates, [{ selected: "nuevo" }], "update() llega sólo al dueño nuevo");
});

test("un mount viejo que completa tarde no consume el PENDING del dueño nuevo", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  let resolveOldMount;
  let oldMountStarted;
  const started = new Promise((resolve) => { oldMountStarted = resolve; });
  const oldPromise = mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/old.mjs" },
    importFn: async () => ({
      mount() {
        oldMountStarted();
        return new Promise((resolve) => { resolveOldMount = resolve; });
      },
    }),
  });
  await started;
  unmount(el);
  let resolveFreshImport;
  const freshPromise = mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/fresh.mjs" },
    importFn: () => new Promise((resolve) => { resolveFreshImport = resolve; }),
  });
  // Props frescos buffereados para el dueño nuevo mientras su import vuela:
  update(el, { selected: "props-frescos" });
  let staleSawProps = 0, staleDisposed = 0;
  resolveOldMount({
    update() { staleSawProps++; },
    dispose() { staleDisposed++; },
  });
  await oldPromise;
  assert.equal(staleSawProps, 0, "el controller viejo no consume el PENDING");
  assert.equal(staleDisposed, 1, "pero sí se dispone una vez");
  const freshUpdates = [];
  resolveFreshImport({ mount() { return { update(p) { freshUpdates.push(p); } }; } });
  await freshPromise;
  assert.deepEqual(freshUpdates, [{ selected: "props-frescos" }],
    "el PENDING intacto llega al dueño nuevo");
});

test("un rechazo tardío del import viejo no borra el claim ni el PENDING del dueño nuevo", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  let rejectOld;
  const oldPromise = mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/old.mjs" },
    importFn: () => new Promise((resolve, reject) => { rejectOld = reject; }),
  });
  unmount(el);
  let resolveFresh;
  const freshPromise = mount(el, {
    skipFeatureCheck: true,
    urlMap: { MedCal: "/fresh.mjs" },
    importFn: () => new Promise((resolve) => { resolveFresh = resolve; }),
  });
  update(el, { selected: "fresco" });
  rejectOld(new Error("404 del import viejo"));
  assert.equal(await oldPromise, null);
  const freshUpdates = [];
  resolveFresh({ mount() { return { update(p) { freshUpdates.push(p); } }; } });
  const controller = await freshPromise;
  assert.ok(controller, "el dueño nuevo monta pese al rechazo tardío del import viejo");
  assert.deepEqual(freshUpdates, [{ selected: "fresco" }],
    "ni el claim ni el PENDING del dueño nuevo fueron tocados por el fallo viejo");
});

test("un fallo real de carga no envenena el registro: el reintento reimporta y monta", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  let attempts = 0;
  const importFn = async () => {
    attempts++;
    if (attempts === 1) throw new Error("503 de mentira");
    return { mount() { return { update() {} }; } };
  };
  const opts = { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn };
  assert.equal(await mount(el, opts), null, "el primer intento falla");
  const retried = await mount(el, opts);
  assert.equal(attempts, 2, "el segundo mount() reintenta el import");
  assert.ok(retried, "y esta vez monta");
});

test("update() tras unmount no resucita un dueño ni alimenta al próximo mount del mismo elemento", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  const seenUpdates = [];
  const importFn = async () => ({ mount() { return { update(p) { seenUpdates.push(p); } }; } });
  const opts = { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" }, importFn };
  await mount(el, opts);
  unmount(el);
  assert.equal(update(el, { selected: "stale" }), null, "no hay controller que lo reciba");
  await mount(el, opts);
  assert.deepEqual(seenUpdates, [],
    "el update post-dispose NO se aplica al dueño nuevo vía PENDING");
});

// =====================================================================
// scan() — recorre [data-island] bajo una raíz inyectada
// =====================================================================

test("a rejected island mount releases its claim for a real retry", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  let attempts = 0;
  const opts = { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" },
    importFn: async () => ({ mount: async () => {
      if (++attempts === 1) throw new Error("mount failed");
      return { update() {}, dispose() {} };
    } }) };
  await mount(el, opts).catch(() => null);
  assert.ok(await mount(el, opts));
  assert.equal(attempts, 2);
});

test("dispose may start a fresh owner without the retiring unmount erasing it", async () => {
  const el = fakeEl({ "data-island": "MedCal" });
  const updates = [];
  let freshPromise;
  const freshOpts = { skipFeatureCheck: true, urlMap: { MedCal: "/x.mjs" },
    importFn: async () => ({ mount: () => ({ update(p) { updates.push(p); }, dispose() {} }) }) };
  await mount(el, { ...freshOpts, importFn: async () => ({ mount: () => ({
    dispose() { freshPromise = mount(el, freshOpts); }
  }) }) });
  unmount(el);
  assert.ok(await freshPromise);
  update(el, { selected: "fresh" });
  assert.deepEqual(updates, [{ selected: "fresh" }]);
  unmount(el);
});

test("scan(): recorre cada [data-island] bajo la raíz sin reventar (querySelectorAll inyectado)", () => {
  // scan() llama a mount(el) SIN options — en uso real, mount() resuelve la
  // URL vía window.SANA_ISLAND_URLS, ya cubierto por los tests de mount()
  // de arriba con urlMap inyectado. Acá el objetivo es sólo el RECORRIDO:
  // que scan() itere cada elemento encontrado y no reviente ni con Node
  // (sin `window`, así que cada mount() interno resuelve null y sale) ni
  // con una raíz vacía.
  const els = [fakeEl({ "data-island": "A" }), fakeEl({ "data-island": "B" })];
  const root = { querySelectorAll: () => els };
  assert.doesNotThrow(() => scan(root));
});

test("scan(): una raíz sin elementos [data-island] no revienta", () => {
  const root = { querySelectorAll: () => [] };
  assert.doesNotThrow(() => scan(root));
});

test("scan(): una raíz sin querySelectorAll (no-DOM) no revienta, simplemente no hace nada", () => {
  assert.doesNotThrow(() => scan({}));
});
