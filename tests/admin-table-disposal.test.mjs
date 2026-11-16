import assert from 'node:assert/strict';
import test from 'node:test';

let sequence = 0;
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
function classes() {
  const values = new Set();
  return { add: x => values.add(x), remove: x => values.delete(x), contains: x => values.has(x),
    toggle(x, on) { if (on) values.add(x); else values.delete(x); } };
}
function events() {
  const handlers = new Map();
  return {
    addEventListener(type, fn) { const list = handlers.get(type) || []; list.push(fn); handlers.set(type, list); },
    emit(type, event = {}) { for (const fn of handlers.get(type) || []) fn(event); },
  };
}

async function fixture(t) {
  const saved = new Map();
  const frames = new Map();
  const timers = new Map();
  const observers = [];
  let id = 0;
  let resolvePreferences;
  const preferences = new Promise(resolve => { resolvePreferences = resolve; });
  const values = {};
  const writes = [];
  const medium = { clientWidth: 0 };
  const headers = ['Patient', 'Service', 'Status'].map(textContent => ({
    textContent, style: {}, querySelector() { return { remove() {} }; },
    getBoundingClientRect: () => ({ width: 100 }),
  }));
  const table = {
    style: {}, classList: classes(), tHead: { rows: [{ cells: headers }] }, parentNode: medium,
    querySelector() { return null; },
    closest(selector) { return selector === '.cnt-table-wrap' ? medium : selector.startsWith('section') ? { id: 'screen' } : null; },
    matches: selector => selector === '#owned',
  };
  const fonts = Object.assign(events(), { forEach() {} });
  const document = Object.assign(events(), {
    readyState: 'loading', body: { classList: classes() }, fonts,
    querySelectorAll: selector => selector === 'table' ? [table] : [],
  });
  const window = Object.assign(events(), {
    document, SANA_STATIC: {},
    sanaImportStatic: async () => ({ load: () => preferences,
      get: (key, fallback) => Object.hasOwn(values, key) ? values[key] : fallback,
      set: (key, value) => writes.push([key, value]) }),
    requestAnimationFrame(fn) { frames.set(++id, fn); return id; },
    cancelAnimationFrame(key) { frames.delete(key); },
    matchMedia: () => ({ matches: false }),
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
      observe(target) { this.target = target; }
      disconnect() { this.disconnected = true; }
    },
  });
  const replacements = { window, document,
    requestAnimationFrame: window.requestAnimationFrame,
    getComputedStyle: () => ({ position: 'relative' }),
    setTimeout(fn) { timers.set(++id, fn); return id; },
    clearTimeout(key) { timers.delete(key); },
  };
  for (const [key, value] of Object.entries(replacements)) {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  t.after(async () => {
    resolvePreferences(); await settle();
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  });
  // Import the unchanged complete production ES module; no rewritten exports.
  const mod = await import(new URL(`../static/platform/admin-table.mjs?disposal=${++sequence}`, import.meta.url));
  return {
    mod, table, window, document, frames, observers, writes,
    create() { return mod.crear({ clave: 'owned', selector: '#owned', tablas: () => [table],
      cols: () => headers.map((h, i) => ({ key: String(i), min: 20, resizable: false })) }); },
    flushFrames() { const work = [...frames.values()]; frames.clear(); work.forEach(fn => fn()); },
    resize() { window.emit('resize'); const work = [...timers.values()]; timers.clear(); work.forEach(fn => fn()); },
    async hydrate(data = {}) { Object.assign(values, data); resolvePreferences(); await settle(); },
  };
}

test('positive control: live table binds, observes its medium and participates in resize', async t => {
  const f = await fixture(t); const owner = f.create();
  owner.programarFit();
  assert.equal(f.table._sanaAnchos, undefined, 'the fit waits for the end of the asking task');
  await Promise.resolve();
  assert.equal(f.table._sanaAnchos, owner, 'and lands before the next frame: no frame is ever requested');
  assert.equal(f.frames.size, 0);
  assert.equal(f.observers.length, 1);
  delete f.table._sanaAnchos;
  f.resize(); await Promise.resolve();
  assert.equal(f.table._sanaAnchos, owner);
});

test('dispose cancels queued fitting, releases resize registration and rejects late hydration', async t => {
  const f = await fixture(t); const owner = f.create();
  owner.programarFit();
  owner.dispose(); owner.dispose();
  await Promise.resolve();
  assert.equal(f.table._sanaAnchos, undefined, 'a fit queued before dispose never runs');
  assert.equal(f.frames.size, 0);
  let registryCalls = 0;
  owner.programarFit = () => { registryCalls++; };
  f.resize(); assert.equal(registryCalls, 0, 'disposed instance must leave the registry, not merely no-op');
  await f.hydrate(); assert.equal(f.frames.size, 0);
  owner.autoFit(); assert.equal(f.table._sanaAnchos, undefined);
});

test('dispose disconnects the medium observer and releases table binding and reservation', async t => {
  const f = await fixture(t); const owner = f.create(); owner.autoFit();
  const observer = f.observers[0];
  owner.dispose();
  assert.equal(observer.disconnected, true);
  assert.equal(f.table._sanaAnchos, undefined);
  observer.callback(); assert.equal(f.frames.size, 0);
  f.mod.barrer(); await Promise.resolve();
  assert.ok(f.table._sanaAnchos, 'released selector permits automatic ownership again');
  assert.notEqual(f.table._sanaAnchos, owner);
});

test('a web font that finishes loading refits the table, and a disposed table stops listening', async t => {
  const f = await fixture(t); const owner = f.create();
  await Promise.resolve();
  delete f.table._sanaAnchos;
  f.document.fonts.emit('loadingdone');
  assert.equal(f.table._sanaAnchos, undefined, 'the refit is queued, not run inside the font event');
  await Promise.resolve();
  assert.equal(f.table._sanaAnchos, owner, 'and lands before the next frame paints the wider text');
  delete f.table._sanaAnchos;
  let land;
  const face = { status: 'loading', loaded: new Promise(resolve => { land = resolve; }) };
  f.document.fonts.emit('loading', { fontfaces: [face] });
  land(face); await settle();
  assert.equal(f.table._sanaAnchos, owner, 'one face landing refits without waiting for the whole set');
  owner.dispose();
  delete f.table._sanaAnchos;
  f.document.fonts.emit('loadingdone'); await settle();
  assert.equal(f.table._sanaAnchos, undefined, 'a disposed table is no longer refitted');
});

test('disposing an old same-selector instance preserves the replacement binding and reservation', async t => {
  const f = await fixture(t); const old = f.create(); old.autoFit();
  const fresh = f.create(); fresh.autoFit();
  old.dispose(); assert.equal(f.table._sanaAnchos, fresh);
  delete f.table._sanaAnchos;
  f.mod.barrer(); await Promise.resolve();
  assert.equal(f.table._sanaAnchos, undefined, 'fresh reservation still blocks automatic ownership');
  fresh.autoFit(); assert.equal(f.table._sanaAnchos, fresh);
});

test('disposed picker cannot notify, write preferences or remove a newer same-key picker', async t => {
  const f = await fixture(t); const calls = [];
  const config = { clave: 'doctor', cols: [{ key: 'name', default: true }, { key: 'status' }] };
  const old = f.mod.columnas({ ...config, onChange: () => calls.push('old') });
  const fresh = f.mod.columnas({ ...config, onChange: () => calls.push('fresh') });
  old.dispose(); old._set(['status']);
  await f.hydrate({ 'table.columns.doctor': ['status'] });
  assert.deepEqual(calls, ['fresh']); assert.equal(f.writes.length, 0);
  const wrap = { getAttribute: () => 'doctor', querySelectorAll: () => [{ getAttribute: () => 'name' }] };
  const checkbox = { closest: () => wrap };
  f.document.emit('change', { target: { closest: () => checkbox } });
  assert.deepEqual(calls, ['fresh', 'fresh']); assert.equal(f.writes.length, 1);
  fresh.dispose(); f.document.emit('change', { target: { closest: () => checkbox } });
  assert.equal(f.writes.length, 1);
});
