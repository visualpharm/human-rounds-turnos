import test from 'node:test';
import assert from 'node:assert/strict';
// Import the actual module, before exposing the browser fixture, so auto-boot
// does not create an extra owner. No source rewriting or extracted functions.
import { mount, refreshAvatar } from '../static/platform/nav.mjs';

function events() {
  const handlers = new Map();
  return {
    addEventListener(type, fn) {
      if (!handlers.has(type)) handlers.set(type, new Set());
      handlers.get(type).add(fn);
    },
    removeEventListener(type, fn) { handlers.get(type)?.delete(fn); },
    fire(type, value = {}) { for (const fn of [...(handlers.get(type) || [])]) fn(value); },
    count(type) { return handlers.get(type)?.size || 0; },
  };
}
function classes() {
  const values = new Set(['external-owner']);
  return {
    add(...names) { names.forEach(n => values.add(n)); },
    remove(...names) { names.forEach(n => values.delete(n)); },
    contains(name) { return values.has(name); },
    toggle(name, force) {
      const on = force ?? !values.has(name);
      if (on) values.add(name); else values.delete(name);
      return on;
    },
    clear() { values.clear(); },
  };
}
function fixture(t, loggedIn = false, hostless = false) {
  const previous = new Map();
  const install = (key, value) => {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  };
  const win = events(), doc = events();
  const requests = [], writes = [], observers = [], avatars = [];
  function avatar() {
    const img = { src: '', removeAttribute() { this.src = ''; } };
    const av = { img, classList: classes(), querySelector() { return img; } };
    avatars.push(av);
    return av;
  }
  const host = {
    isConnected: true, markup: '', classList: classes(),
    get innerHTML() { return this.markup; },
    set innerHTML(value) { this.markup = value; this.avatar = value.includes('stznav-cuenta-av') ? avatar() : null; },
    getAttribute() { return ''; },
    querySelector(selector) { return selector === '.stznav-cuenta-av' ? this.avatar : null; },
    querySelectorAll() { return []; }, contains() { return false; },
    replaceChildren() { this.innerHTML = ''; },
    set className(value) { this.classList.clear(); },
  };
  doc.body = {};
  doc.getElementById = id => id === 'stznav-styles' ? {} : (hostless ? null : host);
  doc.documentElement = { getAttribute() { return 'humanrounds'; }, style: {
    removeProperty() {}, setProperty() {},
  } };
  install('window', win); install('document', doc);
  install('location', { pathname: '/', hash: '', href: '/' });
  install('localStorage', { getItem(key) { return key === 'st_nav' && loggedIn ? JSON.stringify({loggedIn: true}) : null; }, setItem(...args) { writes.push(args); } });
  install('fetch', (url, options = {}) => new Promise(resolve => requests.push({ url, options, resolve })));
  install('MutationObserver', class {
    constructor(callback) { this.callback = callback; this.active = false; observers.push(this); }
    observe() { this.active = true; }
    disconnect() { this.active = false; }
  });
  const controllers = [];
  const start = () => { const c = mount(hostless ? undefined : host); controllers.push(c); return c; };
  t.after(() => {
    controllers.forEach(c => c.dispose());
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return { host, win, doc, requests, writes, observers, avatars, start };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('temporary emergency owners restore the live header hook immediately across repeated and nested transfers', async t => {
  const f = fixture(t), header = f.start();
  await header.ready;
  const markup = f.host.innerHTML;
  f.doc.getElementById = id => id === 'stznav-styles' ? {} : null;
  const active = () => f.observers.filter(o => o.active).length;
  for (const nested of [false, true, false]) {
    const emergency = mount();
    await emergency.ready;
    let last = emergency;
    if (nested) {
      last = mount();
      await last.ready;
      emergency.dispose();
      assert.equal(active(), 1, 'superseded emergency owner cannot reclaim');
    }
    assert.equal(active(), 1);
    last.dispose();
    assert.equal(active(), 1, 'header hook resumes synchronously');
    assert.equal(f.host.innerHTML, markup);
    assert.equal(f.doc.count('click'), 1);
  }
});

for (const inactive of ['disposed', 'detached', 'hidden']) test(`emergency takeover skips a ${inactive} header and permits only an eligible restore`, async t => {
  const f = fixture(t), header = f.start();
  await header.ready;
  f.doc.getElementById = id => id === 'stznav-styles' ? {} : null;
  const emergency = mount();
  await emergency.ready;
  if (inactive === 'disposed') header.dispose();
  else if (inactive === 'detached') f.host.isConnected = false;
  else f.win.fire('pagehide');
  emergency.dispose();
  assert.equal(f.observers.filter(o => o.active).length, 0);
  if (inactive === 'hidden') {
    f.win.fire('pageshow', { persisted: true });
    await settle();
    assert.equal(f.observers.filter(o => o.active).length, 1);
    assert.match(f.host.innerHTML, /stznav-brand/);
  }
});

test('an emergency-only mount cannot dispose a still-live header owner', async t => {
  const f = fixture(t), header = f.start();
  await header.ready;
  const rendered = f.host.innerHTML;
  f.doc.getElementById = () => null;
  const emergency = mount();
  t.after(() => emergency.dispose());
  await emergency.ready;
  assert.equal(f.host.innerHTML, rendered);
  assert.equal(f.doc.count('click'), 1);
  assert.equal(f.observers.filter(o => o.active).length, 1);
  header.dispose();
  assert.equal(f.observers.filter(o => o.active).length, 1);
});

test('early dynamic boot waits through pagehide until the restored DOM is ready', async t => {
  const f = fixture(t);
  f.doc.readyState = 'loading';
  const early = await import('../static/platform/nav.mjs?early-boot-control');
  f.win.fire('pagehide');
  f.doc.readyState = 'interactive';
  f.doc.fire('DOMContentLoaded');
  await settle();
  assert.equal(f.host.innerHTML, '', 'hidden page cannot acquire a late owner');
  assert.equal(f.requests.length, 0);
  f.win.fire('pageshow', { persisted: true });
  await settle();
  assert.match(f.host.innerHTML, /stznav-brand/);
  assert.equal(f.requests.length, 3);
  const cleanup = early.mount(f.host);
  cleanup.dispose();
});

test('dispose before readiness is terminal: no render, reads or observers', async t => {
  const f = fixture(t), c = f.start();
  c.dispose();
  await c.ready;
  assert.equal(f.host.innerHTML, '');
  assert.equal(f.requests.length, 0);
  assert.equal(f.observers.filter(o => o.active).length, 0);
  assert.equal(f.win.count('pagehide'), 0);
});

test('disposing a live owner preserves externally supplied host classes', async t => {
  const f = fixture(t), c = f.start();
  await c.ready;
  assert.match(f.host.innerHTML, /stznav-brand/);
  assert.equal(f.requests.length, 3);
  c.dispose();
  assert.equal(f.host.classList.contains('external-owner'), true);
  assert.equal(f.host.classList.contains('stznav'), false);
  assert.equal(f.doc.count('click'), 0);
  assert.equal(f.observers.filter(o => o.active).length, 0);
});

test('repeated persisted pageshow cannot accumulate listeners or observers', async t => {
  const f = fixture(t), c = f.start();
  await c.ready;
  f.win.fire('pagehide');
  assert.equal(f.host.innerHTML, '');
  f.win.fire('pageshow', { persisted: true });
  await settle();
  f.win.fire('pageshow', { persisted: true });
  await settle();
  assert.equal(f.doc.count('click'), 1);
  assert.equal(f.win.count('resize'), 1);
  assert.equal(f.observers.filter(o => o.active).length, 1);
});

test('fresh mount supersedes the old owner and stale reads cannot publish cache', async t => {
  const f = fixture(t), old = f.start();
  await old.ready;
  const oldRequests = [...f.requests];
  const fresh = f.start();
  await fresh.ready;
  const markup = f.host.innerHTML;
  old.dispose();
  assert.equal(f.host.innerHTML, markup);
  for (const request of oldRequests) request.resolve({ ok: true, json: async () => ({ usuario: { nombre: 'Fictional' } }) });
  await settle();
  assert.equal(f.writes.length, 0);
  assert.equal(f.doc.count('click'), 1);
  assert.equal(f.observers.filter(o => o.active).length, 1);
  fresh.dispose();
  assert.equal(f.host.classList.contains('stznav'), false);
  assert.equal(f.host.classList.contains('external-owner'), true);
});

test('detached host cannot publish late reconcile results', async t => {
  const f = fixture(t), c = f.start();
  await c.ready;
  f.host.isConnected = false;
  for (const request of f.requests) request.resolve({ ok: true, json: async () => ({ usuario: { nombre: 'Fictional' } }) });
  await settle();
  assert.equal(f.writes.length, 0);
});

test('live avatar response uses a callable owner and late image callbacks cannot mutate after dispose', async t => {
  const f = fixture(t, true), c = f.start();
  await c.ready;
  const av = f.avatars[0];
  const probe = f.requests.find(r => r.url.includes('photo?probe'));
  assert.ok(probe);
  probe.resolve({ ok: true, json: async () => ({ source: 'uploaded' }) });
  await settle();
  assert.match(av.img.src, /^\/api\/account\/photo\?v=/);
  const lateLoad = av.img.onload;
  assert.equal(typeof lateLoad, 'function');
  c.dispose();
  lateLoad();
  assert.equal(av.classList.contains('noimg'), true);
});

test('pagehide aborts every controller-owned GET, including the avatar probe', async t => {
  const f = fixture(t, true), c = f.start();
  await c.ready;
  assert.equal(f.requests.length, 4);
  f.win.fire('pagehide');
  for (const request of f.requests) {
    assert.equal(request.options.signal.aborted, true, request.url);
  }
  assert.equal(f.observers.filter(o => o.active).length, 0);
});

test('avatar refresh only reaches the current owner and rejects stale same-render callbacks', async t => {
  const f = fixture(t, true), old = f.start();
  await old.ready;
  const oldAvatar = f.avatars.at(-1);
  const oldProbe = f.requests.find(request => request.url.includes('photo?probe'));
  const fresh = f.start();
  await fresh.ready;
  const currentAvatar = f.avatars.at(-1);
  const firstCurrentProbe = [...f.requests].reverse().find(request => request.url.includes('photo?probe'));

  refreshAvatar();
  const secondCurrentProbe = f.requests.at(-1);
  assert.notEqual(secondCurrentProbe, firstCurrentProbe);
  assert.equal(firstCurrentProbe.options.signal.aborted, true);
  assert.equal(oldProbe.options.signal.aborted, true);

  oldProbe.resolve({ ok: true, json: async () => ({ source: 'uploaded' }) });
  firstCurrentProbe.resolve({ ok: true, json: async () => ({ source: 'uploaded' }) });
  await settle();
  assert.equal(oldAvatar.img.src, '');
  assert.equal(currentAvatar.img.src, '');

  secondCurrentProbe.resolve({ ok: true, json: async () => ({ source: 'uploaded' }) });
  await settle();
  assert.match(currentAvatar.img.src, /^\/api\/account\/photo\?v=/);
  const staleLoad = currentAvatar.img.onload;

  refreshAvatar();
  const finalProbe = f.requests.at(-1);
  assert.equal(secondCurrentProbe.options.signal.aborted, true);
  staleLoad();
  assert.equal(currentAvatar.classList.contains('noimg'), true);
  finalProbe.resolve({ ok: true, json: async () => ({ source: 'uploaded' }) });
  await settle();
  const finalLoad = currentAvatar.img.onload;
  fresh.dispose();
  assert.equal(currentAvatar.img.onload, null);
  assert.equal(currentAvatar.img.onerror, null);
  finalLoad();
  assert.equal(currentAvatar.classList.contains('noimg'), true);
});

test('a null-host remount retains one page-level emergency observer', async t => {
  const f = fixture(t, false, true), first = f.start();
  await first.ready;
  assert.equal(f.observers.filter(o => o.active).length, 1);
  const second = f.start();
  await second.ready;
  assert.equal(f.observers.filter(o => o.active).length, 1);
  assert.equal(f.win.count('pagehide'), 1);
  second.dispose();
  assert.equal(f.observers.filter(o => o.active).length, 0);
});
