// admin-combo.js — the admin panel's typeable combo box, extracted out of
// admin-app.js on 2026-08-21 so admin-content.mjs (and a future direct Doctor
// screen) can use it without the legacy queue controller.
//
// What this suite pins, in order:
//   1. the boundary (window.SanaCombo + the legacy window.comboHtml alias,
//      module.exports for Node) and the markup it produces;
//   2. behavior on a real (hand-rolled — this repo has no jsdom) DOM: open on
//      focus, accent-insensitive filtering, ↑/↓ + Enter, Escape, pointer pick,
//      close policy (catalog-only vs free text), the empty-list hook;
//   3. both consumers (admin-app.js, admin-content.mjs) reach the widget ONLY
//      through that boundary, no second implementation exists anywhere, the
//      fingerprinted load order is right on the direct screens (Solicitudes,
//      Patients), and a missing module fails LOUDLY — neither
//      consumer silently falls back to a copy of its own.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setCatalog } from '../static/platform/i18n.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel) => readFileSync(join(ROOT, rel), 'utf8');
function staticMap() {
  return {
    'platform/i18n.mjs': pathToFileURL(join(ROOT, 'static/platform/i18n.mjs')).href,
    'platform/search.mjs': pathToFileURL(join(ROOT, 'static/platform/search.mjs')).href,
  };
}
const COMBO = read('static/platform/admin-combo.mjs');
const APP = read('static/root/admin-app.js');
const CONTENT = read('static/platform/admin-content.mjs');
const PATIENTS = read('static/platform/admin-patients.mjs');
const SOLICITUDES_TPL = read('templates/admin/solicitudes.html');
const PATIENTS_TPL = read('templates/admin/pacientes.html');
const FINGERPRINT_PY = read('app/core/static_fingerprint.py');

// ---------------------------------------------------------------------------
// A minimal DOM: just enough of the platform for the widget's own code paths
// (closest / querySelector(All) on `.cls` and `input[type=hidden]`, classList,
// dataset, innerHTML parsing of the markup the widget itself emits, value /
// hidden, dispatchEvent, contains, scrollIntoView, select, focus).
// ---------------------------------------------------------------------------
const VOID = new Set(['input', 'br']);
const unescapeHtml = (s) => String(s)
  .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<')
  .replace(/&gt;/g, '>').replace(/&amp;/g, '&');

class FakeEl {
  constructor(tag, attrs = {}, doc = null) {
    this.tag = tag;
    this.attrs = new Map(Object.entries(attrs));
    this.childNodes = [];
    this.parent = null;
    this.doc = doc;
    this.events = [];
    this.scrolled = 0;
    this.selected = 0;
    this._value = this.attrs.has('value') ? this.attrs.get('value') : '';
    this._hidden = this.attrs.has('hidden');
    const el = this;
    this.classList = {
      _list() { return (el.attrs.get('class') || '').split(/\s+/).filter(Boolean); },
      _set(list) { el.attrs.set('class', list.join(' ')); },
      contains(c) { return this._list().includes(c); },
      add(c) { const l = this._list(); if (!l.includes(c)) { l.push(c); this._set(l); } },
      remove(c) { this._set(this._list().filter((x) => x !== c)); },
      toggle(c, force) {
        const on = force === undefined ? !this.contains(c) : !!force;
        if (on) this.add(c); else this.remove(c);
        return on;
      },
    };
    this.dataset = new Proxy({}, {
      get: (_, name) => (typeof name === 'string' ? el.attrs.get('data-' + name) : undefined),
      set: (_, name, v) => { el.attrs.set('data-' + name, String(v)); return true; },
      has: (_, name) => el.attrs.has('data-' + name),
    });
  }
  get children() { return this.childNodes.filter((n) => n instanceof FakeEl); }
  get value() { return this._value; }
  set value(v) { this._value = String(v); }
  get hidden() { return this._hidden; }
  set hidden(v) { this._hidden = !!v; }
  get textContent() {
    return this.childNodes.map((n) => (n instanceof FakeEl ? n.textContent : n.text)).join('');
  }
  set innerHTML(html) {
    this.childNodes = [];
    parseInto(this, String(html), this.doc);
  }
  get innerHTML() { return serialize(this); }
  matches(sel) {
    if (sel.startsWith('.')) return this.classList.contains(sel.slice(1));
    if (sel === 'input[type=hidden]' || sel === 'input[type="hidden"]') {
      return this.tag === 'input' && this.attrs.get('type') === 'hidden';
    }
    return this.tag === sel;
  }
  _descendants() {
    const out = [];
    const walk = (n) => n.children.forEach((c) => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  querySelectorAll(sel) { return this._descendants().filter((n) => n.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) {
    let n = this;
    while (n) { if (n.matches(sel)) return n; n = n.parent; }
    return null;
  }
  contains(node) { return node === this || this._descendants().includes(node); }
  dispatchEvent(ev) { this.events.push(ev.type); return true; }
  scrollIntoView() { this.scrolled++; }
  select() { this.selected++; }
  focus() { if (this.doc) this.doc.activeElement = this; }
  appendChild(n) { n.parent = this; this.childNodes.push(n); return n; }
}

function serialize(el) {
  return el.childNodes.map((n) => {
    if (!(n instanceof FakeEl)) return n.text;
    const attrs = [...n.attrs].map(([k, v]) => (v === '' && k === 'hidden' ? ' hidden' : ` ${k}="${v}"`)).join('');
    return VOID.has(n.tag) ? `<${n.tag}${attrs}>` : `<${n.tag}${attrs}>${serialize(n)}</${n.tag}>`;
  }).join('');
}

// The markup the widget emits is flat and double-quoted: a tiny tokenizer is
// enough (and keeps the test honest — it parses the REAL comboHtml output).
function parseInto(root, html, doc) {
  const re = /<\/([a-zA-Z0-9]+)\s*>|<([a-zA-Z0-9]+)((?:\s+[a-zA-Z_:-]+(?:="[^"]*")?)*)\s*\/?>|([^<]+)/g;
  const attrRe = /([a-zA-Z_:-]+)(?:="([^"]*)")?/g;
  let cur = root;
  let m;
  while ((m = re.exec(html))) {
    if (m[1]) {
      if (cur === root) throw new Error('stray close tag ' + m[1]);
      cur = cur.parent;
    } else if (m[2]) {
      const attrs = {};
      let a;
      while ((a = attrRe.exec(m[3] || ''))) attrs[a[1]] = a[2] === undefined ? '' : unescapeHtml(a[2]);
      const el = new FakeEl(m[2].toLowerCase(), attrs, doc);
      cur.appendChild(el);
      if (!VOID.has(el.tag)) cur = el;
    } else if (m[4]) {
      cur.childNodes.push({ text: unescapeHtml(m[4]) });
    }
  }
  return root;
}

function fakeDocument() {
  const listeners = {};
  return {
    activeElement: null,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    listeners,
    fire(type, target, extra = {}) {
      const ev = { type, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
      (listeners[type] || []).forEach((fn) => fn(ev));
      return ev;
    },
  };
}

// Loads the real ES module with a fresh browser environment and module state.
let loadSequence = 0;
async function load({ catalog = null } = {}) {
  const document = fakeDocument();
  const timers = [];
  const window = { document, SANA_STATIC: staticMap() };
  setCatalog(catalog || {});
  globalThis.window = window;
  globalThis.document = document;
  globalThis.Event = Event;
  globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  const api = await import(`../static/platform/admin-combo.mjs?combo-test=${++loadSequence}`);
  const flushTimers = () => { while (timers.length) timers.shift().fn(); };
  return { window, document, api, flushTimers };
}

// Renders a combo through the REAL SanaCombo.html and parses it into the fake DOM.
function mount(api, document, cls, items, value = '', display = '', placeholder = '', title = '') {
  const root = new FakeEl('div', {}, document);
  root.innerHTML = api.html(cls, items, value, display, placeholder, title);
  const box = root.querySelector('.cmb');
  assert.ok(box, 'markup must contain .cmb');
  return {
    root, box,
    hid: box.querySelector('input[type=hidden]'),
    inp: box.querySelector('.cmb-in'),
    list: box.querySelector('.cmb-list'),
  };
}

const ITEMS = [
  { v: 'cardio', t: 'Cardiología', s: '· Hospital de Pinamar', k: 'corazon cardiologo' },
  { v: 'psiq', t: 'Psiquiatría', s: '', k: 'psiquiatra' },
  { v: 'psico', t: 'Psicología', s: '' },
  { v: 'derm', t: 'Dermatología' },
];

// ---------------------------------------------------------------------------
// 1. Boundary + markup
// ---------------------------------------------------------------------------
test('the boundary is exactly the seven named ES-module functions and publishes no globals', async () => {
  const { window, api } = await load();
  assert.deepEqual(Object.keys(api).sort(),
    ['close', 'filter', 'html', 'open', 'parts', 'pick', 'register']);
  for (const k of Object.keys(api)) assert.equal(typeof api[k], 'function', k);
  assert.equal(window.comboHtml, undefined);
  assert.equal(window.SanaCombo, undefined);
});

test('the ES-module boundary executes filter, open, pick and close behavior', async () => {
  const document = fakeDocument();
  globalThis.window = { SANA_STATIC: staticMap() };
  const api = await import(`../static/platform/admin-combo.mjs?combo-direct=${++loadSequence}`);
    const mounted = mount(api, document, 'direct-sel', ITEMS, 'psiq', 'Psiquiatría');
    assert.deepEqual(api.filter(ITEMS, 'cardiologia').map((item) => item.v), ['cardio']);
    assert.deepEqual(api.filter(ITEMS, 'psiquiatra').map((item) => item.v), ['psiq']);

    api.open(mounted.box, 'psi');
    const choices = mounted.list.querySelectorAll('.cmb-it');
    assert.deepEqual(choices.map((item) => item.dataset.v), ['psiq', 'psico']);
    api.pick(mounted.box, choices[1]);
    assert.equal(mounted.hid.value, 'psico');
    assert.equal(mounted.inp.value, 'Psicología');
    assert.deepEqual(mounted.hid.events, ['change']);

    mounted.inp.value = 'not in the catalog';
    api.close(mounted.box);
    assert.equal(mounted.inp.value, 'Psicología');
    api.register('direct-sel', {
      freeText: true,
      emptyHtml: () => '<div class="cmb-none">No matches</div>',
    });
    mounted.inp.value = 'Dra. Nueva';
    api.close(mounted.box);
    assert.equal(mounted.hid.value, 'Dra. Nueva');
    assert.deepEqual(mounted.hid.events, ['change', 'change']);

    mounted.inp.value = '';
    api.close(mounted.box);
    assert.equal(mounted.hid.value, '');
    assert.deepEqual(mounted.hid.events, ['change', 'change', 'change']);

    api.open(mounted.box, 'nothing matches');
    assert.equal(mounted.list.querySelector('.cmb-none').textContent, 'No matches');
});

test('the ES module installs and executes every delegated listener', async () => {
    const { api, document, flushTimers } = await load();
    const mounted = mount(api, document, 'installed-sel', ITEMS);

    document.fire('focusin', mounted.inp);
    mounted.inp.value = 'psi';
    document.fire('input', mounted.inp);
    document.fire('keydown', mounted.inp, { key: 'ArrowDown' });
    document.fire('keydown', mounted.inp, { key: 'ArrowUp' });
    document.fire('keydown', mounted.inp, { key: 'Enter' });
    assert.equal(mounted.hid.value, 'psiq');

    document.fire('focusin', mounted.inp);
    document.fire('keydown', mounted.inp, { key: 'Escape' });
    assert.equal(mounted.list.hidden, true);
    document.fire('focusin', mounted.inp);
    const pointer = document.fire('pointerdown', mounted.list.querySelector('.cmb-it'));
    assert.equal(pointer.defaultPrevented, true);

    mounted.inp.value = 'unknown';
    document.activeElement = null;
    document.fire('focusout', mounted.inp);
    flushTimers();
    assert.equal(mounted.inp.value, mounted.box.dataset.disp);
});

test('markup: hidden input keeps the consumer class + value, the text input is .cmb-in sized by content, list starts hidden, everything escaped', async () => {
  const { api, document } = await load();
  const items = [{ v: 'a<b', t: 'Ana "A"', s: '' }, { v: 'b', t: 'Beta', s: '· Centro' }];
  const html = api.html('svc-sel', items, 'a<b', 'Ana "A"', 'Elegí', 'Derivar a');
  assert.match(html, /^<span class="cmb" data-items="/);
  assert.match(html, /<input type="hidden" class="svc-sel" value="a&lt;b">/);
  assert.match(html, /<input type="text" class="cmb-in" style="width:\d+ch" value="Ana &quot;A&quot;" placeholder="Elegí" autocomplete="off" spellcheck="false">/);
  assert.match(html, /<div class="cmb-list" hidden><\/div>/);
  assert.match(html, /title="Derivar a"/);
  assert.ok(!html.includes('a<b'), 'raw value must not reach the markup unescaped');
  // data-items round-trips through esc(JSON) back into the same objects
  const { box, hid, inp, list } = mount(api, document, 'svc-sel', items, 'a<b', 'Ana "A"', 'Elegí', 'Derivar a');
  // (JSON compare: the parsed objects come from the vm realm, so strict
  // deepEqual would fail on prototype identity alone)
  assert.equal(JSON.stringify(api.parts(box).items), JSON.stringify(items));
  assert.equal(hid.value, 'a<b');
  assert.equal(inp.value, 'Ana "A"');
  assert.equal(box.dataset.disp, 'Ana "A"');
  assert.equal(list.hidden, true);
});

test('markup width: p90 of the typical item (+subtitle unless it is a "— libre" tail), floor 15ch, ceiling 48ch', async () => {
  const { api } = await load();
  const w = (html) => +html.match(/style="width:(\d+)ch"/)[1];
  assert.equal(w(api.html('x', [{ v: 1, t: 'ab' }], '', '', '', '')), 15);               // floor
  assert.equal(w(api.html('x', [{ v: 1, t: 'a'.repeat(80) }], '', '', '', '')), 48);      // ceiling
  // a display longer than every item widens the control for it
  assert.equal(w(api.html('x', [{ v: 1, t: 'ab' }], '', 'x'.repeat(20), '', '')), 26);
  // the service subtitle counts (it lands in the input on pick); "— libre …" does not
  assert.equal(w(api.html('x', [{ v: 1, t: 'Cardiología', s: '· Hospital de Pinamar' }], '', '', '', '')),
    'Cardiología · Hospital de Pinamar'.length + 6);
  assert.equal(w(api.html('x', [{ v: 1, t: 'Cardiología', s: '— libre Mañana, 07:30' }], '', '', '', '')),
    Math.max(15, 'Cardiología'.length + 6));
});

// ---------------------------------------------------------------------------
// 2. Behavior
// ---------------------------------------------------------------------------
test('filter: native Search ranks, stemming matches what substring cannot, substring remains the empty-rank safety net', async () => {
  const api = (await load()).api;
  assert.deepEqual(api.filter(ITEMS, '').map((x) => x.v), ['cardio', 'psiq', 'psico', 'derm']);
  assert.deepEqual(api.filter(ITEMS, 'cardiología').map((x) => x.v), ['cardio']);
  assert.deepEqual(api.filter(ITEMS, 'CARDIOLOGIA').map((x) => x.v), ['cardio']);
  assert.deepEqual(api.filter(ITEMS, 'pinamar').map((x) => x.v), ['cardio']);
  assert.deepEqual(api.filter(ITEMS, 'psiquiatra').map((x) => x.v), ['psiq']);
  assert.deepEqual(api.filter(ITEMS, 'zzz'), []);
  // "ginecólogo" is not a substring of "Ginecología"; only the native stemmer matches.
  const gine = [{ v: 'gine', t: 'Ginecología', s: '', k: '' }];
  assert.deepEqual(api.filter(gine, 'ginecologo').map((x) => x.v), ['gine']);
  assert.equal(gine.filter((it) => `${it.t} ${it.s} ${it.k}`.toLowerCase().includes('ginecologo')).length, 0);
  // ranking discarded the token → accent-insensitive substring still filters
  assert.deepEqual(api.filter(ITEMS, 'ermat').map((x) => x.v), ['derm']);
});

test('keyboard + pointer: focus opens the full list, typing filters and preselects, ↑/↓ move, Enter/pointer pick into the hidden input and fire change, Escape closes', async () => {
  const { api, document } = await load();
  const { box, hid, inp, list } = mount(api, document, 'fr-svc-sel', ITEMS, '', '', 'Sin especificar', '');

  document.fire('focusin', inp);
  assert.equal(inp.selected, 1, 'focus selects the typed text');
  assert.equal(list.hidden, false);
  assert.equal(list.querySelectorAll('.cmb-it').length, 4);
  assert.equal(list.querySelectorAll('.sel').length, 0, 'no preselection without a query');
  assert.equal(list.querySelector('.cmb-it').textContent, 'Cardiología · Hospital de Pinamar');
  assert.equal(list.querySelector('.cmb-sub').textContent, '· Hospital de Pinamar');

  inp.value = 'psi';
  document.fire('input', inp);
  const its = () => list.querySelectorAll('.cmb-it');
  assert.deepEqual(its().map((x) => x.dataset.v), ['psiq', 'psico']);
  assert.equal(its()[0].classList.contains('sel'), true, 'first match is preselected when typing');

  let ev = document.fire('keydown', inp, { key: 'ArrowDown' });
  assert.equal(ev.defaultPrevented, true);
  assert.deepEqual(its().map((x) => x.classList.contains('sel')), [false, true]);
  assert.equal(its()[1].scrolled, 1);
  document.fire('keydown', inp, { key: 'ArrowDown' });       // clamps at the end
  assert.deepEqual(its().map((x) => x.classList.contains('sel')), [false, true]);
  document.fire('keydown', inp, { key: 'ArrowUp' });
  assert.deepEqual(its().map((x) => x.classList.contains('sel')), [true, false]);
  document.fire('keydown', inp, { key: 'ArrowUp' });         // clamps at the start
  assert.deepEqual(its().map((x) => x.classList.contains('sel')), [true, false]);

  ev = document.fire('keydown', inp, { key: 'Enter' });
  assert.equal(ev.defaultPrevented, true, 'Enter on an open list never submits a form');
  assert.equal(hid.value, 'psiq');
  assert.equal(inp.value, 'Psiquiatría');
  assert.equal(box.dataset.disp, 'Psiquiatría');
  assert.equal(list.hidden, true);
  assert.deepEqual(hid.events, ['change'], 'a real change event reaches the consumer');

  // Enter again with the list closed: nothing happens
  document.fire('keydown', inp, { key: 'Enter' });
  assert.deepEqual(hid.events, ['change']);

  // ArrowDown on a closed list reopens it with the FULL list
  document.fire('keydown', inp, { key: 'ArrowDown' });
  assert.equal(list.hidden, false);
  assert.equal(its().length, 4);
  document.fire('keydown', inp, { key: 'Escape' });
  assert.equal(list.hidden, true);

  // pointer pick: the subtitle joins the displayed text, change fires once more
  document.fire('focusin', inp);
  ev = document.fire('pointerdown', its()[0]);
  assert.equal(ev.defaultPrevented, true, 'pointerdown must not steal focus before the pick');
  assert.equal(hid.value, 'cardio');
  assert.equal(inp.value, 'Cardiología · Hospital de Pinamar');
  assert.deepEqual(hid.events, ['change', 'change']);
  // picking the SAME value again does not fire a second change
  document.fire('focusin', inp);
  document.fire('pointerdown', its()[0]);
  assert.deepEqual(hid.events, ['change', 'change']);
});

test('doctor combo: picking closes to the name alone; the "— libre …" tail stays in the open list', async () => {
  const { api, document } = await load();
  const MEDS = [{ v: 'Daniel Kim', t: 'Daniel Kim', s: '— libre jue 17, 09:00' },
    { v: 'Ana Silva', t: 'Ana Silva', s: '' }];
  const { box, hid, inp, list } = mount(api, document, 'med-sel', MEDS, '', '', 'Médico: a asignar', '');
  document.fire('focusin', inp);
  assert.equal(list.querySelector('.cmb-sub').textContent, '— libre jue 17, 09:00');
  document.fire('pointerdown', list.querySelectorAll('.cmb-it')[0]);
  assert.equal(hid.value, 'Daniel Kim');
  assert.equal(inp.value, 'Daniel Kim');
  assert.equal(box.dataset.disp, 'Daniel Kim');
});

test('Enter picks the only visible item even without a preselection', async () => {
  const { api, document } = await load();
  const { hid, inp } = mount(api, document, 'x-sel', [{ v: 'solo', t: 'Único' }]);
  document.fire('focusin', inp);                 // opened with '' → no .sel
  document.fire('keydown', inp, { key: 'Enter' });
  assert.equal(hid.value, 'solo');
  assert.deepEqual(hid.events, ['change']);
});

test('close policy: an unregistered class is catalog-only (free text reverts, no change); empty text clears and fires change', async () => {
  const { api, document, flushTimers } = await load();
  const { box, hid, inp, list } = mount(api, document, 'fr-svc-sel', ITEMS, 'psiq', 'Psiquiatría');
  document.fire('focusin', inp);
  inp.value = 'algo que no existe';
  document.fire('input', inp);
  document.activeElement = null;                 // focus left the box
  document.fire('focusout', inp);
  assert.equal(list.hidden, false, 'close is deferred (120 ms) so a pick inside the list still lands');
  flushTimers();
  assert.equal(list.hidden, true);
  assert.equal(inp.value, 'Psiquiatría', 'typed text that matches nothing reverts to the last valid display');
  assert.equal(hid.value, 'psiq');
  assert.deepEqual(hid.events, []);
  // focus moved INSIDE the box (to the list): no close at all
  document.fire('focusin', inp);
  inp.value = 'xx';
  document.activeElement = list;
  document.fire('focusout', inp);
  flushTimers();
  assert.equal(list.hidden, false);
  assert.equal(inp.value, 'xx');
  // emptying the text clears the value and tells the consumer
  inp.value = '   ';
  document.activeElement = null;
  document.fire('focusout', inp);
  flushTimers();
  assert.equal(hid.value, '');
  assert.equal(box.dataset.disp, '');
  assert.deepEqual(hid.events, ['change']);
});

test('close policy: a class registered freeText accepts typed text as the value and fires change', async () => {
  const { api, document, flushTimers } = await load();
  api.register('med-sel', { freeText: true });
  const { box, hid, inp } = mount(api, document, 'med-sel', ITEMS, 'psiq', 'Psiquiatría');
  document.fire('focusin', inp);
  inp.value = ' Dra. Nueva ';
  document.activeElement = null;
  document.fire('focusout', inp);
  flushTimers();
  assert.equal(hid.value, 'Dra. Nueva');
  assert.equal(box.dataset.disp, 'Dra. Nueva');
  assert.deepEqual(hid.events, ['change']);
  // the registry is per class: an unregistered sibling on the same page stays strict
  const other = mount(api, document, 'svc-sel', ITEMS, 'psiq', 'Psiquiatría');
  document.fire('focusin', other.inp);
  other.inp.value = 'Dra. Nueva';
  document.activeElement = null;
  document.fire('focusout', other.inp);
  flushTimers();
  assert.equal(other.hid.value, 'psiq');
  assert.equal(other.inp.value, 'Psiquiatría');
  // register() validates and merges
  assert.throws(() => api.register('', { freeText: true }), /cls/);
  api.register('med-sel', { emptyHtml: () => '<div class="cmb-none">nadie</div>' });
  const { inp: inp2, list: list2 } = mount(api, document, 'med-sel', ITEMS);
  inp2.value = 'zzz';
  document.fire('input', inp2);
  assert.equal(list2.textContent, 'nadie', 'merge kept freeText and added emptyHtml');
});

test('empty list: the shared "Sin resultados" by default (through T()), the registered hook when it answers, the default when it returns ""', async () => {
  const { api, document } = await load({ catalog: { 'admin.queue.solicitudes.sin_resultados': 'No results' } });
  const a = mount(api, document, 'fr-svc-sel', ITEMS);
  a.inp.value = 'zzz';
  document.fire('input', a.inp);
  assert.equal(a.list.hidden, false);
  assert.equal(a.list.querySelector('.cmb-none').textContent, 'No results');
  assert.equal(a.list.querySelectorAll('.cmb-it').length, 0);
  // Enter on an empty list does nothing
  document.fire('keydown', a.inp, { key: 'Enter' });
  assert.equal(a.hid.value, '');

  const hookCalls = [];
  api.register('svc-sel', { emptyHtml(q, box) { hookCalls.push([q, box]); return q === 'resonancia' ? '<div class="cmb-none">El hospital no realiza <b>resonancia magnética</b>.</div>' : ''; } });
  const b = mount(api, document, 'svc-sel', ITEMS);
  b.inp.value = 'resonancia';
  document.fire('input', b.inp);
  assert.equal(b.list.querySelector('.cmb-none').textContent, 'El hospital no realiza resonancia magnética.');
  assert.equal(hookCalls[0][0], 'resonancia');
  assert.equal(hookCalls[0][1], b.box);
  b.inp.value = 'zzz';
  document.fire('input', b.inp);
  assert.equal(b.list.querySelector('.cmb-none').textContent, 'No results', 'hook returning "" falls back to the shared text');
  // the Spanish literal is the fallback when there is no catalog at all
  const es = await load();
  const c = mount(es.api, es.document, 'fr-svc-sel', ITEMS);
  c.inp.value = 'zzz';
  es.document.fire('input', c.inp);
  assert.equal(c.list.querySelector('.cmb-none').textContent, 'Sin resultados');
});

// ---------------------------------------------------------------------------
// 3. Consumers, single implementation, load order, fail-visible
// ---------------------------------------------------------------------------
test('admin-app.js imports the module and registers the queue policies there', () => {
  assert.match(APP, /sanaImportStatic\('platform\/admin-combo\.mjs'\)/);
  assert.match(APP, /Combo\.html\('svc-sel', svcItems, svcVal, svcDisp/);
  assert.match(APP, /Combo\.html\('med-sel', medItems/);
  assert.match(APP, /Combo\.register\('med-sel', \{freeText:true\}\)/);
  assert.match(APP, /Combo\.register\('svc-sel', \{emptyHtml: q=>\{/);
  // the "hospital does not do this study" text stayed the queue's (it needs
  // NO_SE_REALIZA), and still goes through the i18n keys
  assert.match(APP, /admin\.queue\.solicitudes\.hospital_no_realiza", \{ estudio: '<b>'\+esc\(no\)\+'<\/b>' \}/);
  assert.match(APP, /admin\.queue\.solicitudes\.rechaza_no_se_realiza/);
  // no private copy of any piece of the widget
  for (const re of [/function comboHtml\(/, /function cmbParts\(/, /function cmbFiltrar\(/, /function cmbOpen\(/,
    /function cmbVacio\(/, /function cmbPick\(/, /function cmbClose\(/, /contains\('cmb-in'\)/,
    /class="cmb-in"/, /class="cmb-list"/, /class="cmb-it/, /\bcomboHtml\(/, /window\.comboHtml/]) {
    assert.doesNotMatch(APP, re, `admin-app.js must not carry ${re}`);
  }
});

test('admin-content.mjs imports the widget (via combo()) and keeps no copy', () => {
  assert.match(CONTENT, /importStatic\("platform\/admin-combo\.mjs"\)/);
  assert.match(CONTENT, /combo\(\)\.html\("fr-svc-sel", items, actual, sel \? sel\.t : ""/);
  assert.match(CONTENT, /function combo\(\) \{[\s\S]*?Combo[\s\S]*?throw new Error\("admin-content\.mjs: platform\/admin-combo\.mjs must load/);
  for (const re of [/window\.comboHtml/, /function comboHtml\(/, /class="cmb-in"/, /class="cmb-list"/, /class="cmb-it/,
    /contains\("cmb-in"\)/, /contains\('cmb-in'\)/, /SanaCombo\s*\|\|/]) {
    assert.doesNotMatch(CONTENT, re, `admin-content.mjs must not carry ${re}`);
  }
});

test('static/platform/admin-patients.mjs has no doctor combo (the doctor is found by typing in the search) and no compatibility global remains', () => {
  assert.doesNotMatch(PATIENTS, /admin-combo/);
  assert.doesNotMatch(PATIENTS, /pat-medico-sel/);
  assert.doesNotMatch(COMBO + APP + CONTENT + PATIENTS, /window\.(?:comboHtml|SanaCombo)\s*=/);
});

test('exactly ONE file implements the combo: markup, filter, keyboard/pointer/focus listeners', () => {
  // Every root-level first-party script and admin template, plus the
  // modules' own admin scripts.
  const files = readdirSync(ROOT)
    .filter((f) => (f.endsWith('.js') || f.endsWith('.mjs') || f.endsWith('.html')) && statSync(join(ROOT, f)).isFile())
    .concat(readdirSync(join(ROOT, 'templates', 'admin'))
      .map((f) => join('templates', 'admin', f))
      .filter((f) => statSync(join(ROOT, f)).isFile()))
    .concat(readdirSync(join(ROOT, 'static', 'platform'))
      .filter((f) => f.endsWith('.mjs') && statSync(join(ROOT, 'static', 'platform', f)).isFile())
      .map((f) => join('static', 'platform', f)));
  const producers = {};
  const MARKERS = {
    'class="cmb-in"': /class="cmb-in"/,
    'class="cmb-list"': /class="cmb-list"/,
    'class="cmb-it': /class="cmb-it/,
    'admin combo html signature': /function html\(cls, items, value, display, placeholder, title\)/,
    'cmb-in focus/keyboard delegation': /contains\(['"]cmb-in['"]\)/,
    'combo filter': /function filter\(items, q\)/,
    'combo close': /function close\(box\)/,
    'combo open': /function open\(box, q\)/,
  };
  for (const f of files) {
    const src = read(f);
    for (const [name, re] of Object.entries(MARKERS)) {
      if (re.test(src)) (producers[name] = producers[name] || []).push(f);
    }
  }
  for (const name of Object.keys(MARKERS)) {
    assert.deepEqual(producers[name], ['static/platform/admin-combo.mjs'], `${name} must exist in static/platform/admin-combo.mjs only`);
  }
});

test('direct screens do not load a classic combo script', () => {
  assert.ok(!SOLICITUDES_TPL.includes("static('admin-combo.js')"));
  assert.ok(!PATIENTS_TPL.includes("static('admin-combo.js')"));
});

test('admin-combo.mjs is registered only as a client-loaded module', () => {
  const m = FINGERPRINT_PY.match(/ADMIN_ROOT_JAVASCRIPT = frozenset\(\{([\s\S]*?)\}\)/);
  assert.ok(m, 'ADMIN_ROOT_JAVASCRIPT cohort not found');
  assert.doesNotMatch(m[1], /admin-combo/);
  const client = FINGERPRINT_PY.match(/CLIENT_LOADED = frozenset\(\{([\s\S]*?)\}\)/);
  assert.ok(client && client[1].includes('"platform/admin-combo.mjs"'));
  assert.ok(!FINGERPRINT_PY.includes('"admin-combo.js"'));
  const manifests = readdirSync(join(ROOT, 'app', 'modules'))
    .filter((id) => statSync(join(ROOT, 'app', 'modules', id)).isDirectory())
    .map((id) => join('app', 'modules', id, 'module.json'));
  assert.ok(manifests.length >= 3, 'module manifests not found');
  for (const mf of manifests) {
    try { assert.ok(!read(mf).includes('platform/admin-combo.mjs'), `${mf} must not claim admin-combo.mjs`); } catch (e) { if (e.code !== 'ENOENT') throw e; }
  }
});

test('all consumers await the imported module before their first render and keep no fallback', () => {
  assert.match(APP, /Promise\.all\(\[[\s\S]*?comboReady/);
  assert.match(CONTENT, /var deps = \[i18nReady, hoursReady, datesReady, formatReady, comboReady, prefsReady, tableReady(?:, \w+Ready)*\]/);
  assert.match(PATIENTS, /var dependencies = Promise\.all\(\[[\s\S]*?dispose\.ready = dependencies\.then\(/);
  assert.match(PATIENTS, /Prefs = take\(mods\[4\][\s\S]*return Prefs\.load\(\)\.then\([\s\S]*return route\(\)/);
  const franja = CONTENT.match(/function franjaServicioHtml\(fr, centro, ci, fi\) \{[\s\S]*?\n  \}/)[0];
  assert.match(franja, /return combo\(\)\.html\("fr-svc-sel"/);
  assert.doesNotMatch(franja, /<select|<input|\|\|\s*function|comboHtml/);
});
