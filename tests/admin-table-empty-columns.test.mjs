// A column that is empty in every row of the list is not drawn, at any width
// (pinamar-turnos 07.11, «Cómo prepararse» at 1440: «En una línea» had no
// value in 30 rows and still took its header's width while the names beside
// it ended in «…»). Its width goes to the neighbours; it comes back as soon
// as a row carries a value. `always` columns, untitled ones (actions) and the
// columns the person switched on in «Elegir columnas» stay.
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
function cell(text, width) {
  return { textContent: text, style: {}, children: [], clientWidth: 0, scrollWidth: 0,
    querySelector() { return null; }, hasAttribute: () => false,
    getElementsByTagName: () => [], getBoundingClientRect: () => ({ width: width || 0 }) };
}

async function fixture(t, { width, labels, natural, rows, stacked = false, stored = {} }) {
  const saved = new Map();
  const medium = { clientWidth: width };
  const headers = labels.map((label, i) => cell(label, natural[i]));
  const body = { rows: rows.map(values => ({ cells: values.map(v => cell(v, 0)) })) };
  let colgroup = null;
  const table = {
    style: {}, classList: classes(), tHead: { rows: [{ cells: headers }] }, tBodies: [body], parentNode: medium,
    querySelector(selector) { return selector === 'colgroup' ? colgroup : null; },
    insertBefore(node) { colgroup = node; node.parentNode = table; },
    get firstChild() { return colgroup; },
    closest(selector) { return selector === '.cnt-table-wrap' ? medium : null; },
    matches: selector => selector === '#vacias',
  };
  const document = Object.assign(events(), {
    readyState: 'loading', body: { classList: classes() },
    querySelectorAll: selector => selector === 'table' ? [table] : [],
    createElement: element,
  });
  const window = Object.assign(events(), {
    document, SANA_STATIC: {},
    sanaImportStatic: async () => ({ load: async () => stored, get: (key, fallback) => (key in stored ? stored[key] : fallback), set() {} }),
    requestAnimationFrame() { return 0; },
    cancelAnimationFrame() {},
    matchMedia: () => ({ matches: stacked }),
  });
  const replacements = { window, document,
    requestAnimationFrame: window.requestAnimationFrame,
    getComputedStyle: () => ({ position: 'relative', whiteSpace: 'nowrap' }),
  };
  for (const [key, value] of Object.entries(replacements)) {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  t.after(async () => {
    await settle();
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  });
  const mod = await import(new URL(`../static/platform/admin-table.mjs?vacias=${++sequence}`, import.meta.url));
  await settle();
  const clave = 'vacias-' + sequence;
  return {
    mod, clave, headers, body,
    hidden: i => [headers[i]].concat(body.rows.map(r => r.cells[i])).every(c => c.style.display === 'none'),
    shown: i => [headers[i]].concat(body.rows.map(r => r.cells[i])).every(c => !c.style.display),
    widths: () => (colgroup ? colgroup.children.map(c => parseInt(c.style.width, 10) || 0) : []),
    owner(cfg) {
      return mod.crear(Object.assign({ clave, selector: '#vacias', tablas: () => [table] }, cfg));
    },
  };
}

const PREP_COLS = [
  { key: 'nombre', label: 'Nombre', min: 120, max: 9999, flex: 2, resizable: false },
  { key: 'resumen', label: 'En una línea', min: 80, max: 420, flex: 1, resizable: false },
  { key: 'match', label: 'Se reconoce por', min: 80, max: 420, flex: 1, resizable: false },
  { key: 'ayuno', label: 'Ayuno', min: 60, resizable: false },
];
const PREP = {
  labels: ['Nombre', 'En una línea', 'Se reconoce por', 'Ayuno'],
  natural: [260, 120, 400, 60],
  rows: [
    ['MAPA / Presurometría', '', 'mapa · presión · holter', 'No'],
    ['Ecografía abdominal', '—', 'ecografía · abdomen', '8 h'],
  ],
};

test('at a wide width an all-empty column is not drawn and its width goes to the neighbours', async t => {
  const f = await fixture(t, Object.assign({ width: 1200 }, PREP));
  f.owner({ cols: () => PREP_COLS }).autoFit();
  assert.ok(f.hidden(1), '«En una línea» leaves header and cells');
  assert.ok(f.shown(0) && f.shown(2) && f.shown(3), 'the columns with data stay');
  const w = f.widths();
  assert.equal(w[1], 0, 'no width for the hidden column');
  assert.equal(w.reduce((a, b) => a + b, 0), 1200, 'the whole width is still used');
  assert.ok(w[2] >= 400, `«Se reconoce por» gets its whole text (${w[2]})`);
});

test('the column comes back as soon as one row carries a value', async t => {
  const f = await fixture(t, Object.assign({ width: 1200 }, PREP));
  const owner = f.owner({ cols: () => PREP_COLS });
  owner.autoFit();
  assert.ok(f.hidden(1));
  f.body.rows[1].cells[1].textContent = 'Venir con la vejiga llena';
  owner.autoFit();
  assert.ok(f.shown(1), 'shown again');
  assert.ok(f.widths()[1] > 0);
});

test('a new row drawn without a fit is caught by the observer sweep', async t => {
  const f = await fixture(t, Object.assign({ width: 1200 }, PREP));
  f.owner({ cols: () => PREP_COLS }).autoFit();
  f.body.rows.push({ cells: ['Holter', '', 'holter', 'No'].map(v => cell(v, 0)) });
  assert.equal(f.body.rows[2].cells[1].style.display, undefined);
  f.mod.barrer();
  assert.equal(f.body.rows[2].cells[1].style.display, 'none', 'the new cell joins its hidden column');
});

test('`always` columns and untitled action columns stay even when empty', async t => {
  const f = await fixture(t, {
    width: 900, labels: ['Nombre', 'Nota', ''], natural: [200, 80, 40],
    rows: [['Ana', '', ''], ['Luis', '—', '']],
  });
  f.owner({ cols: () => [
    { key: 'nombre', label: 'Nombre', min: 120, flex: 1, resizable: false },
    { key: 'nota', label: 'Nota', min: 60, flex: 1, always: true, resizable: false },
    { key: 'acciones', label: '', min: 40, resizable: false },
  ] }).autoFit();
  assert.ok(f.shown(1), 'the `always` column stays');
  assert.ok(f.shown(2), 'the untitled column stays');
});

test('a column the person switched on in «Elegir columnas» stays even when empty', async t => {
  const f = await fixture(t, Object.assign({ width: 1200 }, PREP, {
    stored: { ['table.columns.vacias-' + (sequence + 1)]: ['resumen', 'match', 'ayuno'] },
  }));
  const todas = [
    Object.assign({ always: true }, PREP_COLS[0]),
    PREP_COLS[1],                                   // not on by default: chosen
    Object.assign({ default: true }, PREP_COLS[2]),
    Object.assign({ default: true }, PREP_COLS[3]),
  ];
  const picker = f.mod.columnas({ clave: f.clave, cols: todas });
  assert.deepEqual(picker.activas().map(c => c.key), ['nombre', 'resumen', 'match', 'ayuno']);
  f.owner({ cols: () => picker.activas() }).autoFit();
  assert.ok(f.shown(1), 'switched on by hand: shown');
  assert.ok(f.widths()[1] > 0);
});

test('an empty column that comes on by default still hides', async t => {
  const f = await fixture(t, Object.assign({ width: 1200 }, PREP));
  const todas = [
    Object.assign({ always: true }, PREP_COLS[0]),
    Object.assign({ default: true }, PREP_COLS[1]),
    Object.assign({ default: true }, PREP_COLS[2]),
    Object.assign({ default: true }, PREP_COLS[3]),
  ];
  const picker = f.mod.columnas({ clave: f.clave, cols: todas });
  f.owner({ cols: () => picker.activas() }).autoFit();
  assert.ok(f.hidden(1));
});

test('a screen with its own picker says which columns were chosen (`elegida`)', async t => {
  const f = await fixture(t, Object.assign({ width: 1200 }, PREP));
  f.owner({ cols: () => PREP_COLS, elegida: key => key === 'resumen' }).autoFit();
  assert.ok(f.shown(1));
});

test('a stacked table (phone) hides the empty column too', async t => {
  const f = await fixture(t, Object.assign({ width: 390, stacked: true }, PREP));
  f.owner({ cols: () => PREP_COLS }).autoFit();
  assert.ok(f.hidden(1));
  assert.ok(f.shown(2));
});

test('a list without rows hides nothing', async t => {
  const f = await fixture(t, Object.assign({ width: 1200 }, PREP, { rows: [] }));
  f.owner({ cols: () => PREP_COLS }).autoFit();
  assert.ok(f.shown(1));
});
