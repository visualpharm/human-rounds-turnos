// The shared screen loader (static/platform/screen-boot.mjs): the eight-second
// deadline says a load is late and never abandons it; a real failure still
// stops it. Runs the real module against a small fake DOM and fake timers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bootScreen, SLOW_AFTER_MS } from '../static/platform/screen-boot.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 8) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

class Element {
  constructor(tag = 'div', id = '') {
    this.tagName = tag; this.id = id; this.children = []; this.parent = null;
    this.hidden = false; this.className = ''; this.onclick = null; this.textContent = '';
  }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parent = null;
    this.children = nodes.flatMap(node => (node && node.fragment ? node.children : [node]));
    for (const child of this.children) child.parent = this;
  }
  append(...nodes) {
    const add = nodes.flatMap(node => (node && node.fragment ? node.children : [node]));
    for (const child of add) child.parent = this;
    this.children.push(...add);
  }
  before(node) {
    const siblings = this.parent.children;
    siblings.splice(siblings.indexOf(this), 0, node);
    node.parent = this.parent;
  }
  remove() {
    if (!this.parent) return;
    this.parent.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }
  find(predicate) {
    for (const child of this.children) {
      if (predicate(child)) return child;
      const deep = child.find?.(predicate);
      if (deep) return deep;
    }
    return null;
  }
}

function copyTemplate(className, text) {
  return { content: { cloneNode() {
    const line = new Element('span'); line.textContent = text; line.className = className;
    const retry = new Element('button'); retry.className = 'retry';
    return { fragment: true, children: [line, retry], querySelector: selector => (selector === '[data-retry]' ? retry : null) };
  } } };
}

function recoveryTemplate() { return copyTemplate('alert', 'No se pudo cargar.'); }

// The page's shared late copy (templates/_screen_load_late.html).
function lateTemplate() { return copyTemplate('late', 'La carga tarda más de lo normal.'); }

function page({ recoveryKind = 'template', late = false } = {}) {
  const wrap = new Element('div', 'wrap');
  const host = new Element('div', 'host');
  const status = new Element('p', 'status');
  status.hidden = true;
  wrap.append(host, status);
  const skeleton = new Element('section', 'skeleton');
  const errorBox = new Element('div', 'error-box');
  errorBox.hidden = true;
  const elements = { host, status, recovery: recoveryKind === 'template' ? recoveryTemplate() : errorBox };
  if (late) elements['screen-load-late'] = lateTemplate();
  const events = new Map(), timers = new Map();
  let timerID = 0;
  const state = { reloads: 0 };
  const values = {
    document: { getElementById: id => elements[id] ?? null, createElement: tag => new Element(tag) },
    window: { addEventListener: (type, listener) => events.set(type, listener) },
    location: { reload: () => { state.reloads += 1; } },
    setTimeout: (callback, delay) => { timers.set(++timerID, { callback, delay }); return timerID; },
    clearTimeout: id => { timers.delete(id); },
  };
  const saved = Object.fromEntries(Object.keys(values).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  return {
    wrap, host, status, skeleton, errorBox, events, timers, state,
    deadline() {
      const entry = [...timers.entries()].find(([, timer]) => timer.delay === SLOW_AFTER_MS);
      assert.ok(entry, 'an armed eight-second deadline exists');
      timers.delete(entry[0]);
      entry[1].callback();
    },
    armed() { return [...timers.values()].some(timer => timer.delay === SLOW_AFTER_MS); },
    slowCopy() { return wrap.children.find(child => child.className === 'screen-load-slow') ?? null; },
    restore() {
      for (const [key, descriptor] of Object.entries(saved)) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
      }
    },
  };
}

// A controller that draws its skeleton into the host on mount, like the panel
// screens do, and whose dependencies (`loaded`) and first read (`ready`) are
// gated by the test.
function controllerModule(p, { loaded = Promise.resolve(true), ready = Promise.resolve() } = {}) {
  const calls = { mounts: [], disposals: 0, onError: null };
  return {
    calls,
    module: {
      mount(host, onError, context) {
        calls.mounts.push(context);
        calls.onError = onError;
        host.replaceChildren(p.skeleton);
        const dispose = () => { calls.disposals += 1; };
        dispose.loaded = loaded; dispose.ready = ready;
        return dispose;
      },
    },
  };
}

function boot(p, module, importGate = Promise.resolve(), extra = {}) {
  return bootScreen({
    host: p.host,
    recovery: 'recovery',
    load: () => importGate.then(() => module),
    mount: (mod, onError, context) => mod.mount(p.host, onError, context),
    ...extra,
  });
}

test('an import held past the deadline says so beside the screen and still mounts when it arrives', async () => {
  const p = page();
  try {
    const gate = deferred();
    const { calls, module } = controllerModule(p);
    boot(p, module, gate.promise);
    await settle();
    p.deadline();
    const copy = p.slowCopy();
    assert.ok(copy, 'the late copy is up');
    assert.equal(p.wrap.children.indexOf(copy), p.wrap.children.indexOf(p.host) - 1, 'right before the screen');
    assert.equal(p.host.children.length, 0, 'never inside the screen');
    gate.resolve();
    await settle();
    assert.equal(calls.mounts.length, 1, 'the late import is not abandoned');
    assert.equal(p.host.children[0], p.skeleton);
    assert.equal(p.slowCopy(), null, 'the healthy late answer removes the copy');
    assert.equal(calls.disposals, 0);
  } finally { p.restore(); }
});

test('dependencies late past the deadline leave the drawn skeleton in place', async () => {
  const p = page();
  try {
    const loaded = deferred(), ready = deferred();
    const { calls, module } = controllerModule(p, { loaded: loaded.promise, ready: ready.promise });
    boot(p, module);
    await settle();
    assert.equal(p.host.children[0], p.skeleton, 'the controller drew its skeleton');
    p.deadline();
    assert.ok(p.slowCopy());
    assert.equal(p.host.children[0], p.skeleton, 'the copy does not replace the skeleton');
    loaded.resolve(true);
    await settle();
    assert.equal(p.slowCopy(), null, 'dependencies in: the copy goes, the first read is the screen\'s own state');
    ready.resolve();
    await settle();
    assert.equal(calls.disposals, 0);
    assert.equal(p.host.children[0], p.skeleton);
  } finally { p.restore(); }
});

test('a screen ready before the deadline disarms it', async () => {
  const p = page();
  try {
    const ready = deferred();
    const { module } = controllerModule(p, { loaded: new Promise(() => {}), ready: ready.promise });
    let readyCalls = 0;
    boot(p, module, Promise.resolve(), { onReady: () => { readyCalls += 1; } });
    await settle();
    assert.ok(p.armed());
    ready.resolve();
    await settle();
    assert.equal(p.armed(), false);
    assert.equal(readyCalls, 1);
  } finally { p.restore(); }
});

test('a refused import stops and shows the copy in the screen', async () => {
  const p = page();
  try {
    const { calls, module } = controllerModule(p);
    let stops = 0;
    boot(p, module, Promise.reject(new Error('404')), { onStop: () => { stops += 1; } });
    await settle();
    assert.equal(calls.mounts.length, 0);
    assert.equal(p.host.children[0].className, 'alert', 'failure copy where the screen always showed it');
    assert.equal(p.armed(), false, 'a failure ends the deadline');
    assert.ok(stops >= 2, 'the failure ran the screen\'s stop');
    p.host.children[1].onclick();
    assert.equal(p.state.reloads, 1, 'the retry reloads');
  } finally { p.restore(); }
});

test('onError from the controller disposes it, also after the late copy, and moves the copy into the failure place', async () => {
  const p = page();
  try {
    const loaded = deferred();
    const { calls, module } = controllerModule(p, { loaded: loaded.promise, ready: new Promise(() => {}) });
    boot(p, module, Promise.resolve(), { failedIn: p.status });
    await settle();
    p.deadline();
    assert.ok(p.slowCopy());
    calls.onError();
    await settle();
    assert.equal(calls.disposals, 1);
    assert.equal(p.slowCopy(), null, 'one copy, not two');
    assert.equal(p.status.hidden, false);
    assert.equal(p.status.children[0].className, 'alert');
    loaded.resolve(true);
    await settle();
    assert.equal(p.status.children[0].className, 'alert', 'a stale settle does not clear a failure');
  } finally { p.restore(); }
});

test('a deadline before pagehide cannot mount the obsolete import; a stale retry does nothing', async () => {
  const p = page();
  try {
    const gate = deferred();
    const { calls, module } = controllerModule(p);
    boot(p, module, gate.promise);
    await settle();
    p.deadline();
    const staleRetry = p.slowCopy().children[1];
    p.events.get('pagehide')();
    assert.equal(p.slowCopy(), null, 'leaving the page takes the copy down');
    gate.resolve();
    await settle();
    assert.equal(calls.mounts.length, 0);
    staleRetry.onclick();
    assert.equal(p.state.reloads, 0);
  } finally { p.restore(); }
});

test('a given slow place gets the copy and its hidden state back', async () => {
  const p = page();
  try {
    const gate = deferred();
    const { module } = controllerModule(p);
    boot(p, module, gate.promise, { slowIn: p.status, failedIn: p.status });
    await settle();
    p.deadline();
    assert.equal(p.slowCopy(), null);
    assert.equal(p.status.hidden, false);
    assert.equal(p.status.children[0].className, 'alert');
    gate.resolve();
    await settle();
    assert.equal(p.status.children.length, 0);
    assert.equal(p.status.hidden, true, 'hidden again, as before the copy');
  } finally { p.restore(); }
});

// The late copy says the load is late, not that it failed (2026-11-16): the
// page's shared template, while a failure keeps the screen's own text.
test('the shared late copy, not the failure text, says a load is late; a failure still shows the failure text', async () => {
  const p = page({ late: true });
  try {
    const loaded = deferred();
    const { calls, module } = controllerModule(p, { loaded: loaded.promise, ready: new Promise(() => {}) });
    boot(p, module, Promise.resolve(), { failedIn: p.status });
    await settle();
    p.deadline();
    const copy = p.slowCopy();
    assert.ok(copy, 'the late copy is up beside the screen');
    assert.equal(copy.children[0].className, 'late');
    assert.equal(copy.children.some(child => child.className === 'alert'), false, 'no failure text while the load goes on');
    copy.children[1].onclick();
    assert.equal(p.state.reloads, 1, 'its button reloads');
    calls.onError();
    await settle();
    assert.equal(p.slowCopy(), null, 'one copy, not two');
    assert.equal(p.status.children[0].className, 'alert', 'the failure keeps the screen\'s own text');
  } finally { p.restore(); }
});

test('the shared late copy goes in a given slow place and leaves with a healthy answer', async () => {
  const p = page({ late: true });
  try {
    const gate = deferred();
    const { module } = controllerModule(p);
    boot(p, module, gate.promise, { slowIn: p.status, failedIn: p.status });
    await settle();
    p.deadline();
    assert.equal(p.status.children[0].className, 'late');
    assert.equal(p.status.hidden, false);
    gate.resolve();
    await settle();
    assert.equal(p.status.children.length, 0);
    assert.equal(p.status.hidden, true);
  } finally { p.restore(); }
});

test('with the shared late copy an element recovery stays hidden until a real failure', async () => {
  const p = page({ recoveryKind: 'element', late: true });
  try {
    const loaded = deferred();
    const { calls, module } = controllerModule(p, { loaded: loaded.promise, ready: new Promise(() => {}) });
    boot(p, module, Promise.resolve(), { slowIn: p.status });
    await settle();
    p.deadline();
    assert.equal(p.status.children[0].className, 'late');
    assert.equal(p.errorBox.hidden, true, 'the failure box is not the late copy');
    calls.onError();
    await settle();
    assert.equal(p.status.children.length, 0);
    assert.equal(p.errorBox.hidden, false);
  } finally { p.restore(); }
});

test('an element recovery is shown at the deadline and hidden by a late answer', async () => {
  const p = page({ recoveryKind: 'element' });
  try {
    const gate = deferred();
    const { module } = controllerModule(p);
    boot(p, module, gate.promise);
    await settle();
    p.deadline();
    assert.equal(p.errorBox.hidden, false);
    gate.resolve();
    await settle();
    assert.equal(p.errorBox.hidden, true);
  } finally { p.restore(); }
});

test('a persisted pageshow remounts as a later start, or reloads when asked', async () => {
  const p = page();
  try {
    const { calls, module } = controllerModule(p);
    boot(p, module);
    await settle();
    p.events.get('pagehide')();
    p.events.get('pageshow')({ persisted: false });
    await settle();
    assert.equal(calls.mounts.length, 1);
    p.events.get('pageshow')({ persisted: true });
    await settle();
    assert.deepEqual(calls.mounts, [{ first: true }, { first: false }]);
    assert.equal(calls.disposals, 1);
  } finally { p.restore(); }
  const q = page();
  try {
    const { module } = controllerModule(q);
    boot(q, module, Promise.resolve(), { pageshow: 'reload' });
    await settle();
    q.events.get('pageshow')({ persisted: true });
    assert.equal(q.state.reloads, 1);
  } finally { q.restore(); }
});

test('onFailed runs on a failure only, after the stop and before the copy', async () => {
  const p = page();
  try {
    const order = [];
    const { module } = controllerModule(p, { ready: Promise.reject(new Error('read')) });
    boot(p, module, Promise.resolve(), {
      onStop: () => order.push('stop'),
      onFailed: () => order.push(`failed:${p.host.children.length ? p.host.children[0].className : 'empty'}`),
    });
    await settle();
    assert.deepEqual(order.slice(-2), ['stop', 'failed:'], 'failed after the stop, while the skeleton is still the screen');
    assert.equal(p.host.children[0].className, 'alert');
    p.events.get('pagehide')();
    assert.equal(order.filter(entry => entry.startsWith('failed')).length, 1, 'leaving the page is not a failure');
  } finally { p.restore(); }
});

test('a restart takes down a failure copy left outside the screen', async () => {
  const p = page();
  try {
    let refuse = true;
    const { calls, module } = controllerModule(p);
    bootScreen({
      host: p.host, recovery: 'recovery', failedIn: p.status, slowIn: p.status,
      load: () => (refuse ? Promise.reject(new Error('404')) : Promise.resolve(module)),
      mount: (mod, onError, context) => mod.mount(p.host, onError, context),
    });
    await settle();
    assert.equal(p.status.children[0].className, 'alert');
    refuse = false;
    p.events.get('pagehide')();
    p.events.get('pageshow')({ persisted: true });
    await settle();
    assert.equal(calls.mounts.length, 1);
    assert.equal(p.status.children.length, 0, 'no stale failure under a working screen');
    assert.equal(p.status.hidden, true, 'the status line is hidden again, as before the failure');
  } finally { p.restore(); }
});

test('onError called inside mount() keeps no live controller beside the failure copy', async () => {
  const p = page();
  try {
    let disposals = 0;
    bootScreen({
      host: p.host, recovery: 'recovery',
      load: () => Promise.resolve({}),
      mount: (_mod, onError) => {
        onError();
        const dispose = () => { disposals += 1; };
        dispose.ready = new Promise(() => {});
        return dispose;
      },
    });
    await settle();
    assert.equal(disposals, 1, 'the controller returned after the failure is disposed');
    assert.equal(p.host.children[0].className, 'alert');
    p.events.get('pagehide')();
    assert.equal(disposals, 1, 'and not kept to be disposed again');
  } finally { p.restore(); }
});

// Every screen that loads its controller after the page goes through the one
// loader; no screen keeps its own eight-second timer that abandons a late load.
const SCREENS = [
  'templates/admin/account.html', 'templates/admin/appearance.html', 'templates/admin/cola.html',
  'templates/admin/content.html', 'templates/admin/doctor-workspace.html', 'templates/admin/hsi.html',
  'templates/admin/mis-turnos.html', 'templates/admin/my-patients.html', 'templates/admin/pacientes.html',
  'templates/admin/plan.html', 'templates/admin/preparations.html', 'templates/admin/reportes.html',
  'templates/admin/settings.html', 'templates/admin/system.html', 'templates/auth-test.html',
  'templates/study/boot.html', 'app/modules/intake/admin_consult.html',
];

test('every late-loading screen boots through the shared loader', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..');
  for (const file of SCREENS) {
    const source = readFileSync(join(root, file), 'utf8');
    assert.match(source, /import \{ bootScreen \} from "\{\{ static\('platform\/screen-boot\.mjs'\) \}\}"/, `${file} imports the shared loader`);
    assert.match(source, /bootScreen\(\{/, `${file} boots through it`);
    assert.doesNotMatch(source, /setTimeout\([^)]*,\s*8000\)/, `${file} keeps no deadline of its own`);
  }
});

// Every such page carries the shared late copy exactly once: the panel's
// base for the screens that extend it (the usability screen's study loader
// included), the standalone pages on their own.
test('every late-loading page carries the shared late copy once', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..');
  const read = file => readFileSync(join(root, file), 'utf8');
  const include = /\{% include ["']_screen_load_late\.html["'] %\}/g;
  const late = read('templates/_screen_load_late.html');
  assert.match(late, /<template id="screen-load-late">/);
  assert.match(late, /t\('page\.load_late', 'La carga tarda más de lo normal\.'\)/, 'new key with its Spanish fallback');
  assert.match(late, /t\('admin\.content\.common\.reload', 'Recargar'\)/);
  assert.match(late, /data-retry/);
  assert.match(late, /class="screen-load-late-text" role="status"/, 'the text carries the class its styles key on');
  assert.equal(read('templates/admin/base.html').match(include)?.length, 1, 'the panel base carries it');
  for (const file of SCREENS) {
    const source = read(file);
    const own = source.match(include)?.length ?? 0;
    if (file === 'templates/study/boot.html') {
      assert.equal(own, 0, 'the study loader is also included under the panel base');
      assert.equal(read('templates/study/runner.html').match(include)?.length, 1, 'the public study page carries it');
    } else if (/\{% extends ["']admin\/base\.html["'] %\}/.test(source)) {
      assert.equal(own, 0, `${file} gets it from the panel base`);
    } else {
      assert.equal(own, 1, `${file} carries it itself`);
    }
  }
});

test('a controller object with destroy() is disposed too', async () => {
  const p = page();
  try {
    let destroyed = 0;
    bootScreen({
      host: p.host, recovery: 'recovery',
      load: () => Promise.resolve({}),
      mount: () => ({ destroy: () => { destroyed += 1; }, ready: Promise.resolve() }),
    });
    await settle();
    p.events.get('pagehide')();
    assert.equal(destroyed, 1);
  } finally { p.restore(); }
});
