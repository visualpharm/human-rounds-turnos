// admin-shell.js + admin-alerts.js — EXECUTABLE behavior tests for the
// shared shell facilities both admin surfaces now load (2026-08-31): the
// transient toast provider and the red-triage bell. Same pattern as
// admin-login-direct.test.mjs: the REAL production scripts run unmodified
// in node:vm against a hand-rolled DOM, and every claim is proven by
// driving the script's OWN wiring (the click the browser fires, the fetch
// it issues, the timers it schedules) — never by reading source text.
// The rendered markup/cohort these behaviors bind to is pinned by
// tests/test_admin_shared_shell.py; the real-browser path (toast
// announcement, bell visibility, responsive band fit) by
// tests/e2e/admin-shell.spec.mjs.
import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SHELL_JS = readFileSync(join(ROOT, 'static', 'root', 'admin-shell.js'), 'utf8');
const { mountAlerts } = await import('../static/platform/admin-alerts.mjs');
globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(join(ROOT, 'packs', 'ar', 'country.json'), 'utf8')).phone;
const FORMAT_MODULE = await import('../static/platform/format.mjs');
const DATES_MODULE = await import('../static/platform/dates.mjs');
const SESSION_MODULE = await import('../static/platform/admin-session.mjs');
// The complete native controller is imported unchanged. Only browser globals
// and dependency URLs are supplied by the fixture; no source/export rewriting.
const originalGlobals = new Map();
const alertOwners = [];
function installGlobals(sandbox) {
  for (const key of ['window', 'document', 'fetch', 'setInterval', 'clearInterval']) {
    if (!originalGlobals.has(key)) originalGlobals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    globalThis[key] = key === 'window' ? sandbox : sandbox[key];
  }
}
afterEach(() => {
  for (const { sandbox, dispose } of alertOwners.splice(0)) {
    installGlobals(sandbox);
    dispose();
  }
  for (const [key, descriptor] of originalGlobals) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  }
  originalGlobals.clear();
});

// ---------------------------------------------------------------------------
// Hand-rolled DOM: enough node/attribute/class machinery for both scripts,
// including an innerHTML micro-parser (admin-alerts builds its whole bell
// markup through wrap.innerHTML, then queries it back by id/class).
// ---------------------------------------------------------------------------
class El {
  constructor(tagName, attrs = {}) {
    this.tagName = tagName;
    this.attrs = new Map(Object.entries(attrs));
    this.children = [];
    this.textContent = '';
    this.listeners = {};
    this.parent = null;
    this.disabled = false;
    this.style = {};
    this.hidden = this.attrs.has('hidden');
    this._syncClass();
  }
  _syncClass() { this.classList = makeClassList(this); }
  get id() { return this.attrs.get('id') || ''; }
  set id(v) { this.attrs.set('id', String(v)); }
  get className() { return this.attrs.get('class') || ''; }
  set className(v) { this.attrs.set('class', String(v)); this._syncClass(); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  setAttribute(name, v) {
    this.attrs.set(name, String(v));
    if (name === 'class') this._syncClass();
    if (name === 'hidden') this.hidden = true;
  }
  removeAttribute(name) { this.attrs.delete(name); }
  set innerHTML(html) { this.children = parseHtml(html, this); }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter(item => item !== fn); }
  contains(node) { return collect(this, []).includes(node); }
  querySelectorAll(sel) { return collect(this, []).filter(node => node !== this && matchesSelector(node, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  remove() {
    if (this.parent) {
      const i = this.parent.children.indexOf(this);
      if (i >= 0) this.parent.children.splice(i, 1);
    }
  }
  matches(sel) { return matchesSelector(this, sel); }
  closest(sel) {
    let node = this;
    while (node) { if (matchesSelector(node, sel)) return node; node = node.parent; }
    return null;
  }
  focus() {}
  dispatch(type, ev) {
    ev = ev || {};
    if (!ev.preventDefault) ev.preventDefault = () => {};
    if (!ev.stopPropagation) ev.stopPropagation = () => {};
    if (!ev.target) ev.target = this;
    return Promise.all((this.listeners[type] || []).map((fn) => fn(ev)));
  }
}

function makeClassList(el) {
  const list = () => (el.attrs.get('class') || '').split(/\s+/).filter(Boolean);
  return {
    add(...cs) { const s = new Set(list()); cs.forEach((c) => s.add(c)); el.attrs.set('class', [...s].join(' ')); },
    remove(...cs) { const s = new Set(list()); cs.forEach((c) => s.delete(c)); el.attrs.set('class', [...s].join(' ')); },
    contains(c) { return list().includes(c); },
  };
}

function matchesSelector(el, sel) {
  const byId = /^#([\w-]+)$/.exec(sel);
  if (byId) return el.attrs.get('id') === byId[1];
  const byClass = /^\.([\w-]+)$/.exec(sel);
  if (byClass) return (el.attrs.get('class') || '').split(/\s+/).includes(byClass[1]);
  const byAttr = /^\[([\w-]+)\]$/.exec(sel);
  if (byAttr) return el.attrs.has(byAttr[1]);
  const byTag = /^([a-zA-Z][\w]*)$/.exec(sel);
  if (byTag) return el.tagName === byTag[1];
  return false;
}

// Minimal HTML parser: tags with double-quoted attributes, text nodes,
// explicit closes. Enough for the bell markup and the panel rows.
function parseHtml(html, parent) {
  const roots = [];
  const stack = [{ children: roots }];
  const re = /<\/?\s*([a-zA-Z][\w]*)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*>|([^<]+)/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (m[3] !== undefined) {
      const text = m[3];
      if (text.trim()) stack[stack.length - 1].children.push(text);
      continue;
    }
    const closing = m[0][1] === '/';
    const tag = m[1];
    if (closing) {
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === tag) { stack.length = i; break; }
      }
      continue;
    }
    const attrs = {};
    const are = /([\w-]+)(?:="([^"]*)")?/g;
    let a;
    while ((a = are.exec(m[2] || '')) !== null) attrs[a[1]] = a[2] === undefined ? '' : a[2];
    const el = new El(tag, attrs);
    el.parent = parent;
    stack[stack.length - 1].children.push(el);
    stack.push(el);
  }
  return roots;
}

function collect(node, out) {
  if (typeof node === 'string') return out;
  out.push(node);
  node.children.forEach((c) => collect(c, out));
  return out;
}

function buildDocument() {
  const body = new El('body');
  const head = new El('head');
  const h1row = new El('div', { class: 'adm-h1row' });
  const band = new El('header', { class: 'adm-topbar' });
  band.appendChild(h1row);
  const main = new El('main', { class: 'adm5-main' });
  main.appendChild(band);
  body.appendChild(main);
  const docListeners = {};
  const document = {
    head,
    body,
    createElement: (tag) => new El(tag),
    querySelector(sel) {
      for (const root of [body, head]) {
        for (const el of collect(root, [])) if (matchesSelector(el, sel)) return el;
      }
      return null;
    },
    getElementById(id) {
      for (const root of [body, head]) {
        for (const el of collect(root, [])) if (el.attrs.get('id') === id) return el;
      }
      return null;
    },
    addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
    removeEventListener(type, fn) { docListeners[type] = (docListeners[type] || []).filter(item => item !== fn); },
    dispatch(type, ev) {
      ev = ev || {};
      if (!ev.preventDefault) ev.preventDefault = () => {};
      return Promise.all((docListeners[type] || []).map((fn) => fn(ev)));
    },
  };
  return { document, body, head, h1row };
}

// Fake timers the scripts schedule — fired manually, never by wall clock.
function makeTimers() {
  let seq = 1;
  const timeouts = new Map();
  const intervals = new Map();
  return {
    timeouts,
    intervals,
    setTimeout(fn, ms) { const id = seq++; timeouts.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(fn, ms) { const id = seq++; intervals.set(id, { fn, ms }); return id; },
    clearInterval(id) { intervals.delete(id); },
    fireTimeouts() { [...timeouts.values()].forEach((t) => t.fn()); timeouts.clear(); },
  };
}

const flush = () => new Promise((r) => setTimeout(r, 10));

// ---------------------------------------------------------------------------
// admin-shell.js — the toast provider.
// ---------------------------------------------------------------------------
async function bootShell() {
  const dom = buildDocument();
  const timers = makeTimers();
  const sandbox = {
    console, document: dom.document, setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout, setInterval: timers.setInterval,
    clearInterval: timers.clearInterval,
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SHELL_JS, sandbox);
  return { dom, timers, sandbox };
}

test('no toast node exists before the first call; toast() creates it with the announcement contract', async () => {
  const { dom } = await bootShell();
  assert.equal(dom.document.getElementById('toast'), null, 'gate stays free of panel chrome until a toast fires');
  assert.equal(typeof dom.sandbox?.window?.toast, 'undefined');
});

test('toast() creates #toast on body with role=status, aria-live=polite, aria-atomic=true', async () => {
  const { dom, timers, sandbox } = await bootShell();
  sandbox.window.toast('Guardado.');
  const node = dom.document.getElementById('toast');
  assert.ok(node, 'node created');
  assert.equal(node.parent, dom.body, 'appended to body');
  assert.ok(node.classList.contains('toast'), 'carries the .toast style hook');
  assert.ok(node.classList.contains('on'), 'visible class applied');
  assert.equal(node.getAttribute('role'), 'status');
  assert.equal(node.getAttribute('aria-live'), 'polite');
  assert.equal(node.getAttribute('aria-atomic'), 'true');
  assert.equal(node.textContent, 'Guardado.');
  assert.equal([...timers.timeouts.values()][0].ms, 2600, 'auto-hide at 2600ms');
});

test('a missing canonical module stops the screen with a visible reload boundary', async () => {
  const { dom, sandbox } = await bootShell();
  await assert.rejects(sandbox.window.sanaImportStatic('platform/format.mjs'), /not present/);
  const error = dom.document.getElementById('admin-module-load-error');
  assert.ok(error, 'visible module failure was rendered');
  assert.equal(error.getAttribute('role'), 'alert');
  assert.match(error.textContent, /Recargá/);
});

test('the 2600ms timer removes the visibility class (toast is transient)', async () => {
  const { dom, timers, sandbox } = await bootShell();
  sandbox.window.toast('Guardado.');
  timers.fireTimeouts();
  const node = dom.document.getElementById('toast');
  assert.ok(node);
  assert.equal(node.classList.contains('on'), false);
});

test('a second toast reuses the node and resets the timer — never cut short by the first', async () => {
  const { dom, timers, sandbox } = await bootShell();
  sandbox.window.toast('Primero.');
  const firstTimerId = [...timers.timeouts.keys()][0];
  sandbox.window.toast('Segundo.');
  assert.ok(!timers.timeouts.has(firstTimerId), 'old timer cleared');
  assert.equal(timers.timeouts.size, 1, 'exactly one pending hide timer');
  const node = dom.document.getElementById('toast');
  assert.equal(node.textContent, 'Segundo.', 'same node reused');
  timers.fireTimeouts();
  assert.equal(node.classList.contains('on'), false);
});

// ---------------------------------------------------------------------------
// admin-alerts.js — the red-triage bell, driven through its own wiring.
// ---------------------------------------------------------------------------
async function bootAlerts(opts = {}) {
  const dom = buildDocument();
  const timers = makeTimers();
  const calls = { fetches: [], toasts: [] };
  const state = Object.assign({ count: 0, items: [] }, opts.state || {});
  const fetchImpl = opts.fetchImpl || (async (url, init) => {
    calls.fetches.push({ url, init });
    if (url === '/api/admin/intake/alerts/count') return { ok: true, status: 200, json: async () => ({ count: state.count }) };
    if (url === '/api/admin/intake/alerts') return { ok: true, status: 200, json: async () => ({ items: state.items }) };
    if (url.includes('/ack')) return { ok: true, status: 200, json: async () => ({ ok: true }) };
    return { ok: true, status: 200, json: async () => ({}) };
  });
  const sandbox = {
    console, fetch: fetchImpl, document: dom.document,
    setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    setInterval: timers.setInterval, clearInterval: timers.clearInterval,
    toast: (m) => calls.toasts.push(m),
    __modules: { format: FORMAT_MODULE, dates: DATES_MODULE },
  };
  sandbox.window = sandbox;
  sandbox.SANA_STATIC = {
    'platform/format.mjs': new URL('../static/platform/format.mjs', import.meta.url).href,
    'platform/dates.mjs': new URL('../static/platform/dates.mjs', import.meta.url).href,
    'platform/admin-session.mjs': new URL('../static/platform/admin-session.mjs', import.meta.url).href,
    'platform/i18n.mjs': 'data:text/javascript,export const t=(key,vars,fallback)=>fallback||key;',
  };
  sandbox.sanaImportStatic = async (name) => {
    if (name === 'platform/format.mjs') return FORMAT_MODULE;
    if (name === 'platform/admin-session.mjs') return SESSION_MODULE;
    throw new Error('unexpected module ' + name);
  };
  if (opts.direct) sandbox.SANA_ADMIN_SCREEN = 'hsi';
  installGlobals(sandbox);
  sandbox.mount = async role => {
    installGlobals(sandbox);
    const dispose = mountAlerts({ root: dom.document, role });
    alertOwners.push({ sandbox, dispose });
    await dispose.ready;
  };
  return { dom, timers, sandbox, calls };
}

const ALERT_ITEMS = [
  { id: 'a1', triage: 'rojo', paciente: 'Rosa Melano', dni: '30123456', contacto: '+5492254402237',
    servicio: 'Clínica', medico: 'Dra. Pérez', fecha: '2026-08-31T08:00:00', request_id: 'r1' },
];

test('sanaInitAlerts("admin") mounts the bell inside the shared .adm-h1row band and polls the cheap count', async () => {
  const { dom, timers, calls, sandbox } = await bootAlerts({ state: { count: 2 } });
  await sandbox.mount('admin');
  await flush();
  const wrap = dom.document.querySelector('.alert-bell-wrap');
  assert.ok(wrap, 'bell mounted');
  assert.equal(wrap.parent, dom.h1row, 'mounted in the title band, the element that persists on every screen');
  const bell = dom.document.getElementById('alert-bell');
  assert.ok(bell);
  assert.equal(bell.getAttribute('aria-haspopup'), 'dialog');
  assert.equal(dom.document.getElementById('alert-bell-panel').getAttribute('role'), 'dialog');
  const badge = dom.document.getElementById('alert-bell-badge');
  assert.equal(badge.textContent, '2');
  assert.equal(badge.hidden, false);
  assert.equal(bell.hidden, false, 'bell visible when there is something to see');
  assert.ok(calls.fetches.some((f) => f.url === '/api/admin/intake/alerts/count'), 'count fetched');
  assert.equal(calls.fetches.filter((f) => f.url === '/api/admin/intake/alerts').length, 0, 'full list NOT fetched until the panel opens');
  assert.ok([...timers.intervals.values()].some((i) => i.ms === 60000), 're-polls every ~60s');
});

test('count 0 keeps the bell hidden; 99+ caps the badge text', async () => {
  const cero = await bootAlerts({ state: { count: 0 } });
  await cero.sandbox.mount('admin');
  await flush();
  assert.equal(cero.dom.document.getElementById('alert-bell').hidden, true);
  const muchos = await bootAlerts({ state: { count: 150 } });
  await muchos.sandbox.mount('admin');
  await flush();
  assert.equal(muchos.dom.document.getElementById('alert-bell-badge').textContent, '99+');
});

test('non-alert roles tear the bell down (admisionista keeps the screen, loses the widget)', async () => {
  const { dom, timers, sandbox } = await bootAlerts({ state: { count: 5 } });
  await sandbox.mount('admin');
  await flush();
  assert.ok(dom.document.querySelector('.alert-bell-wrap'), 'admin had the bell');
  await sandbox.mount('admisionista');
  assert.equal(dom.document.querySelector('.alert-bell-wrap'), null, 'wrap removed');
  assert.equal(timers.intervals.size, 0, 'polling stopped');
  // And a medico — the other role that DOES keep it.
  await sandbox.mount('medico');
  await flush();
  assert.ok(dom.document.querySelector('.alert-bell-wrap'), 'medico keeps the bell (own interviews)');
});

test('opening the panel fetches the full list and renders a row with every action wired', async () => {
  const { dom, calls, sandbox } = await bootAlerts({ state: { count: 1, items: ALERT_ITEMS } });
  await sandbox.mount('admin');
  await flush();
  await dom.document.getElementById('alert-bell').dispatch('click');
  await flush();
  const panel = dom.document.getElementById('alert-bell-panel');
  assert.equal(panel.hidden, false, 'panel open');
  assert.equal(dom.document.getElementById('alert-bell').getAttribute('aria-expanded'), 'true');
  assert.ok(calls.fetches.some((f) => f.url === '/api/admin/intake/alerts'), 'list fetched on open');
  const row = dom.document.querySelector('.alert-row');
  assert.ok(row, 'row rendered');
  assert.equal(row.getAttribute('data-alert-row'), 'a1');
  const html = collect(row, []).filter((n) => typeof n !== 'string');
  const ack = dom.document.querySelector('[data-ack]');
  assert.equal(ack.getAttribute('data-ack'), 'a1');
  assert.ok(html.some((n) => n.getAttribute && n.getAttribute('href') === 'https://wa.me/5492254402237'), 'WhatsApp link from canonical Format module');
});

test('"Ver entrevista" uses the canonical consultation URL on direct screens', async () => {
  const direct = await bootAlerts({ direct: true, state: { count: 1, items: ALERT_ITEMS } });
  await direct.sandbox.mount('admin');
  await flush();
  await direct.dom.document.getElementById('alert-bell').dispatch('click');
  await flush();
  const links = collect(direct.dom.document.querySelector('.alert-row'), []).filter((n) => typeof n !== 'string');
  const ver = links.find((n) => (n.getAttribute('href') || '').includes('/admin/consult?'));
  assert.equal(ver.getAttribute('href'), '/admin/consult?interview=a1');
});

test('marking seen POSTs the ack, drops the row and confirms through the shared toast', async () => {
  const { dom, calls, sandbox } = await bootAlerts({ state: { count: 1, items: ALERT_ITEMS } });
  await sandbox.mount('admin');
  await flush();
  await dom.document.getElementById('alert-bell').dispatch('click');
  await flush();
  const ack = dom.document.querySelector('[data-ack]');
  await dom.document.dispatch('click', { target: ack });
  await flush();
  const ackCall = calls.fetches.find((f) => f.url === '/api/admin/intake/alerts/a1/ack');
  assert.ok(ackCall, 'ack POSTed');
  assert.equal(ackCall.init.method, 'POST');
  assert.equal(dom.document.querySelector('.alert-row'), null, 'row removed after ack');
  assert.ok(calls.toasts.some((m) => /visto|vista/i.test(m)), 'confirmation goes through the shared toast provider');
});
