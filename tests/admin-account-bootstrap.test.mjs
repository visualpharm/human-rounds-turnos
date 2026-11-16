// Execute the actual template module script. Only the Jinja static URL is
// substituted; localized recovery DOM is a fixture, not rendered-page proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { placeHost, withScreenBoot, withSiblings } from './screen-boot-fixture.mjs';

const template = readFileSync(new URL('../templates/admin/account.html', import.meta.url), 'utf8');
const script = /<script type="module">([\s\S]*?)<\/script>/.exec(template)?.[1];
assert.ok(script);
const tick = () => new Promise(resolve => process.nextTick(resolve));
const settle = async () => { for (let i = 0; i < 10; i++) await tick(); };
const moduleURL = source => `data:text/javascript,${encodeURIComponent(source)}`;
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
// The smallest DOM node the template and the shared loader touch.
const Node = withSiblings(class {
  constructor() { this.children = []; this.className = ''; this.hidden = false; this.parent = null; }
  replaceChildren(...nodes) { this.children = nodes.flatMap(node => (node && node.fragment ? node.children : [node])); }
});
function fixture({ heldImport = Promise.resolve(), ready = Promise.resolve(), loaded = Promise.resolve(true), fonts, fail = false } = {}) {
  const key = '__staffBoot' + Math.random().toString(36).slice(2);
  const state = { heldImport, ready, loaded, mounts: 0, disposals: 0, reloads: 0 };
  globalThis[key] = state;
  const native = moduleURL(`const state = globalThis[${JSON.stringify(key)}];
    await state.heldImport;
    ${fail ? 'throw new Error("unavailable");' : ''}
    export function mount(host) {
      state.mounts++; host.children = ['account'];
      const dispose = () => { state.disposals++; };
      dispose.loaded = state.loaded; dispose.ready = state.ready; return dispose;
    }`);
  const events = new Map(), timers = new Map();
  let timerID = 0;
  const host = new Node();
  const placed = placeHost(Node, host);
  const recovery = { content: { cloneNode() {
    const button = { onclick: null };
    return { fragment: true, children: ['alert', button], querySelector: () => button };
  } } };
  const values = {
    document: { fonts, getElementById: id => id === 'sec-cuenta' ? host : recovery, createElement: () => new Node() },
    window: { addEventListener: (type, listener) => events.set(type, listener) },
    location: { reload: () => state.reloads++ },
    setTimeout: (callback, delay) => { timers.set(++timerID, { callback, delay }); return timerID; },
    clearTimeout: id => timers.delete(id),
  };
  const descriptors = Object.fromEntries(Object.keys(values).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  const rendered = withScreenBoot(script).replace('"{{ static(\'platform/admin-account.mjs\') }}"', JSON.stringify(native));
  assert.doesNotMatch(rendered, /{{|}}/);
  return {
    state, host, events, timers, slowCopy: placed.slowCopy,
    async execute() { await import(moduleURL(rendered + '\n// ' + key)); await settle(); },
    timeout() { const timer = [...timers.values()].find(t => t.delay === 8000); assert.ok(timer); timer.callback(); },
    restore() {
      events.get('pagehide')?.();
      for (const [key, descriptor] of Object.entries(descriptors)) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
      }
      delete globalThis[key];
    },
  };
}

test('staff Account classic controller is physically retired', () => {
  assert.equal(existsSync(new URL('../admin-account.js', import.meta.url)), false);
  assert.doesNotMatch(template, /admin-account\.js/);
});
test('staff Account waits for initial fonts before mounting', async () => {
  const held = deferred(), page = fixture({ fonts: { ready: held.promise } });
  try {
    await page.execute(); assert.equal(page.state.mounts, 0);
    held.resolve(); await settle(); assert.equal(page.state.mounts, 1);
  } finally { page.restore(); }
});
test('fonts slower than the deadline show the copy beside Account and still mount it', async () => {
  const held = deferred(), page = fixture({ fonts: { ready: held.promise } });
  try {
    await page.execute(); page.timeout();
    assert.equal(page.state.mounts, 0); assert.deepEqual(page.slowCopy().children[0], 'alert');
    assert.deepEqual(page.host.children, [], 'the copy never lands inside the screen');
    held.resolve(); await settle();
    assert.equal(page.state.mounts, 1); assert.deepEqual(page.host.children, ['account']);
    assert.equal(page.slowCopy(), null, 'the late answer removes the copy');
  } finally { page.restore(); }
});
test('font settlement after pagehide cannot mount Account', async () => {
  const held = deferred(), page = fixture({ fonts: { ready: held.promise } });
  try {
    await page.execute(); page.events.get('pagehide')(); held.resolve(); await settle();
    assert.equal(page.state.mounts, 0); assert.deepEqual(page.host.children, []);
  } finally { page.restore(); }
});
test('template waits for the dependencies, then cancels recovery timeout', async () => {
  const loaded = deferred(), page = fixture({ loaded: loaded.promise, ready: new Promise(() => {}) });
  try {
    await page.execute(); assert.equal(page.state.mounts, 1); assert.equal(page.timers.size, 1);
    loaded.resolve(true); await settle(); assert.equal(page.timers.size, 0);
  } finally { page.restore(); }
});
test('a first read slower than the deadline never shows the recovery', async () => {
  const ready = deferred(), page = fixture({ ready: ready.promise });
  try {
    await page.execute(); assert.equal(page.state.mounts, 1); assert.equal(page.timers.size, 0);
    assert.deepEqual(page.host.children, ['account']);
    ready.resolve(); await settle(); assert.deepEqual(page.host.children, ['account']);
  } finally { page.restore(); }
});
test('template import rejection exposes an owned reload control', async () => {
  const page = fixture({ fail: true });
  try {
    await page.execute(); assert.equal(page.host.children[0], 'alert');
    const retry = page.host.children[1]; retry.onclick(); assert.equal(page.state.reloads, 1);
    page.events.get('pagehide')(); retry.onclick(); assert.equal(page.state.reloads, 1);
  } finally { page.restore(); }
});
test('template ready rejection disposes mount and shows recovery', async () => {
  const ready = deferred(), page = fixture({ ready: ready.promise });
  try {
    await page.execute(); ready.reject(new Error('dependency')); await settle();
    assert.equal(page.state.disposals, 1); assert.equal(page.host.children[0], 'alert');
  } finally { page.restore(); }
});
test('a late import past the deadline still mounts Account and clears the copy', async () => {
  const held = deferred(), page = fixture({ heldImport: held.promise });
  try {
    await page.execute(); page.timeout();
    assert.ok(page.slowCopy()); assert.equal(page.state.mounts, 0);
    held.resolve(); await settle();
    assert.equal(page.state.mounts, 1); assert.deepEqual(page.host.children, ['account']);
    assert.equal(page.slowCopy(), null);
  } finally { page.restore(); }
});
test('pagehide rejects an old import and persisted pageshow mounts once', async () => {
  const held = deferred(), page = fixture({ heldImport: held.promise });
  try {
    await page.execute(); page.events.get('pagehide')(); held.resolve(); await settle();
    assert.equal(page.state.mounts, 0);
    page.events.get('pageshow')({ persisted: true }); await settle(); assert.equal(page.state.mounts, 1);
    page.events.get('pagehide')(); assert.equal(page.state.disposals, 1);
    assert.deepEqual(page.host.children, [], 'pagehide clears prior profile DOM before a fresh read');
    page.events.get('pageshow')({ persisted: false }); await settle(); assert.equal(page.state.mounts, 1);
  } finally { page.restore(); }
});
