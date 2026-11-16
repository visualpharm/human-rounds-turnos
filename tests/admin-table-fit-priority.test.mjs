// What the shared width engine gives up first when a table does not fit
// (pinamar-turnos 04.11, «Pacientes de hoy» at 1440): a column whose cells are
// all empty falls before any declared `drop` column, and an `entero` column
// (Paciente, Servicio) keeps the width its content measures while another
// elastic column can still give way — the name was cut with «…» next to an
// empty 120 px column.
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
  return { addEventListener() {}, removeEventListener() {} };
}
function element(tag) {
  return { tagName: tag, style: {}, children: [], appendChild(child) { this.children.push(child); return child; } };
}

async function fixture(t, { width, natural }) {
  const saved = new Map();
  const medium = { clientWidth: width };
  const headers = Object.keys(natural).map(key => ({
    textContent: key, style: {}, querySelector() { return null; },
    getBoundingClientRect: () => ({ width: natural[key] }),
  }));
  let colgroup = null;
  const table = {
    style: {}, classList: classes(), tHead: { rows: [{ cells: headers }] }, parentNode: medium,
    querySelector(selector) { return selector === 'colgroup' ? colgroup : null; },
    insertBefore(node) { colgroup = node; node.parentNode = table; },
    get firstChild() { return colgroup; },
    closest(selector) { return selector === '.cnt-table-wrap' ? medium : null; },
    matches: selector => selector === '#fit',
  };
  const document = Object.assign(events(), {
    readyState: 'loading', body: { classList: classes() },
    querySelectorAll: selector => selector === 'table' ? [table] : [],
    createElement: element,
  });
  let resolvePreferences;
  const preferences = new Promise(resolve => { resolvePreferences = resolve; });
  const window = Object.assign(events(), {
    document, SANA_STATIC: {},
    sanaImportStatic: async () => ({ load: () => preferences, get: (key, fallback) => fallback, set() {} }),
    requestAnimationFrame() { return 0; },
    cancelAnimationFrame() {},
    matchMedia: () => ({ matches: false }),
  });
  const replacements = { window, document,
    requestAnimationFrame: window.requestAnimationFrame,
    getComputedStyle: () => ({ position: 'relative' }),
  };
  for (const [key, value] of Object.entries(replacements)) {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  t.after(async () => {
    resolvePreferences({}); await settle();
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  });
  const mod = await import(new URL(`../static/platform/admin-table.mjs?fit=${++sequence}`, import.meta.url));
  return {
    table,
    fit(cfg) {
      const owner = mod.crear(Object.assign({ clave: 'fit-' + sequence, selector: '#fit', tablas: () => [table] }, cfg));
      owner.autoFit();
      const widths = {};
      Object.keys(natural).forEach((key, i) => { widths[key] = parseInt(colgroup.children[i].style.width, 10); });
      return widths;
    },
  };
}

const COLS = [
  { key: 'hora', min: 50, resizable: false },
  { key: 'paciente', min: 120, max: 300, flex: 1, always: true, entero: true, resizable: false },
  { key: 'cobertura', min: 80, flex: 1, resizable: false },
  { key: 'servicio', min: 100, flex: 1, entero: true, resizable: false },
  { key: 'dni', min: 90, resizable: false },
];
const NATURAL = { hora: 50, paciente: 260, cobertura: 140, servicio: 200, dni: 100 };

test('an all-empty column falls before the declared drop order, and the rest keeps its full text', async t => {
  const f = await fixture(t, { width: 620, natural: NATURAL });
  const w = f.fit({ cols: () => COLS, drop: ['dni'], vacia: key => key === 'cobertura' });
  assert.equal(f.table.classList.contains('tbl-oc-cobertura'), true, 'the empty column falls');
  assert.equal(f.table.classList.contains('tbl-oc-dni'), false, 'the declared drop column stays once the table fits');
  assert.equal(w.cobertura, 0);
  assert.ok(w.paciente >= NATURAL.paciente, `Paciente keeps its measured width (${w.paciente})`);
  assert.ok(w.servicio >= NATURAL.servicio, `Servicio keeps its measured width (${w.servicio})`);
  assert.equal(Object.values(w).reduce((a, b) => a + b, 0), 620, 'no empty space left at the end');
});

test('a column with data is not dropped as empty: the declared order applies to it', async t => {
  const f = await fixture(t, { width: 620, natural: NATURAL });
  f.fit({ cols: () => COLS, drop: ['dni'], vacia: () => false });
  assert.equal(f.table.classList.contains('tbl-oc-cobertura'), false);
  assert.equal(f.table.classList.contains('tbl-oc-dni'), true);
});

test('an `entero` column gives way only after the other elastic columns reached their floor', async t => {
  const natural = { paciente: 260, nota: 300 };
  const f = await fixture(t, { width: 450, natural });
  const w = f.fit({ cols: () => [
    { key: 'paciente', min: 120, max: 300, flex: 1, entero: true, resizable: false },
    { key: 'nota', min: 80, max: 420, flex: 1, resizable: false },
  ] });
  assert.equal(w.paciente, 260, 'the name keeps what it measures');
  assert.equal(w.nota, 190, 'the other column absorbs the whole shortfall');
});

// pinamar-turnos 05.11, live demo at 1440: one long name («Maximiliano
// Domínguez 14 años») split the shortfall with Servicio, and every row read
// «Ginecología y Obstetri…». With what each cell needs, the shortfall goes
// where it hides the least text.
const ENTERAS = [
  { key: 'paciente', min: 120, max: 300, flex: 1, entero: 2, resizable: false },
  { key: 'servicio', min: 104, max: 300, flex: 1, entero: true, resizable: false },
];

test('one long name gives way before a service that every row needs whole', async t => {
  const f = await fixture(t, { width: 420, natural: { paciente: 252, servicio: 196 } });
  const cells = { paciente: [252, 205, 200, 190, 180, 170], servicio: [196, 196, 196, 196, 196, 196] };
  const w = f.fit({ cols: () => ENTERAS, anchosCelda: key => cells[key] });
  assert.equal(w.servicio, 196, 'every service stays whole');
  assert.equal(w.paciente, 224, 'only the one long name loses its last pixels');
});

test('when every name is long the service gives way first: a hidden name weighs double', async t => {
  const f = await fixture(t, { width: 420, natural: { paciente: 252, servicio: 196 } });
  const cells = { paciente: [252, 252, 252, 252, 252, 252], servicio: [196, 196, 196, 196, 196, 196] };
  const w = f.fit({ cols: () => ENTERAS, anchosCelda: key => cells[key] });
  assert.equal(w.paciente, 252, 'the names stay whole');
  assert.equal(w.servicio, 168, 'the service absorbs the shortfall');
});
