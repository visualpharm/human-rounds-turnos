// Exercise the lifecycle module script emitted by
// templates/admin/appearance.html itself. The script text is extracted from
// the template and executed verbatim as a module; the ONLY substitution is
// the server-produced static URL, which is re-pointed at controlled
// dependency modules (data: URLs) the way static_manifest would serve the
// real one. The recovery template DOM is derived from the template's own
// markup with each {{ t(...) }} rendered to its embedded fallback — a
// fixture rendering, NOT evidence for real Jinja translation.
//
// Evidence limit: the dependency module is a controlled stub honouring the
// contract the script relies on (mount(host, {status, onError}) → dispose
// with a .ready promise). A green run here proves the TEMPLATE owns the
// bootstrap lifecycle — generation guard, eight-second deadline, pagehide
// disposal, persisted-pageshow remount, stale-recovery inertness — NOT that
// a real browser evaluates the script and NOT that the real controller
// behaves (that is pinned by tests/admin-appearance-lifecycle.test.mjs and
// tests/admin-appearance-input-abort.test.mjs, which import the real
// static/platform/admin-appearance.mjs end to end).
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { placeHost, withScreenBoot, withSiblings } from './screen-boot-fixture.mjs';

const template = await readFile(new URL('../templates/admin/appearance.html', import.meta.url), 'utf8');
const moduleScript = /<script type="module">\n([\s\S]*?)<\/script>/.exec(template)?.[1];
assert.ok(moduleScript, 'appearance template contains its native bootstrap module script');
const staticExpression = '"{{ static(\'platform/admin-appearance.mjs\') }}"';
assert.ok(moduleScript.includes(staticExpression), 'the bootstrap imports the native Appearance controller');

// The server renders {{ t('key', 'fallback') }}; this fixture renders the
// fallback. If a t() call ever stops naming its fallback the parse fails the
// suite loudly instead of silently rendering an empty string.
const recoveryMarkup = /<template id="appearance-load-recovery">([\s\S]*?)<\/template>/.exec(template)?.[1];
assert.ok(recoveryMarkup, 'appearance template ships its load-recovery template');
const renderedRecovery = recoveryMarkup.replace(/\{\{\s*t\(([^)]*)\)\s*\}\}/g, (_match, args) => {
  const fallback = /,\s*'([^']*)'\s*$/.exec(args.trim());
  assert.ok(fallback, 'every t() in the recovery template names its fallback: ' + args);
  return fallback[1];
});

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 8) { for (let turn = 0; turn < turns; turn += 1) await tick(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function dataModule(source) {
  return `data:text/javascript,${encodeURIComponent(source)}`;
}

// Gate promises cross from this file into the string-bodied dependency
// modules through globalThis keys; every key created is recorded so the
// fixture restore can delete it again (no globals survive a test).
const gateGlobals = new Set();
function gateOnGlobalThis(value) {
  const key = `__appearanceGate_${Math.random()}`;
  globalThis[key] = value;
  gateGlobals.add(key);
  return key;
}

const Element = withSiblings(class {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.attributes = new Map();
    this.onclick = null;
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'class') this.className = String(value);
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  replaceChildren(...nodes) {
    this.children = nodes.flatMap(node => (node && node.fragment ? node.children : [node]));
  }
});

// Build the recovery clone from the template's own rendered markup, so the
// fixture's alert line and retry button cannot drift from what the screen
// actually ships.
function recoveryClone() {
  const tags = [...renderedRecovery.matchAll(/<([a-z]+)\b([^>]*)>([^<]*)<\/\1>/g)];
  assert.equal(tags.length, 2, 'the recovery template is one alert line and one retry button');
  const children = tags.map(match => {
    const el = new Element(match[1]);
    // Valueless attributes (data-retry) parse to "" exactly like the DOM's
    // getAttribute would answer for them.
    for (const token of match[2].trim().split(/\s+/)) {
      if (!token) continue;
      const valued = /^([a-zA-Z-]+)="([^"]*)"$/.exec(token);
      el.setAttribute(valued ? valued[1] : token, valued ? valued[2] : '');
    }
    el.textContent = match[3].trim();
    return el;
  });
  const retry = children.find(el => el.getAttribute('data-retry') !== null);
  assert.ok(retry, 'the recovery template carries the data-retry button');
  return { fragment: true, children, querySelector(selector) { return selector === '[data-retry]' ? retry : null; } };
}

// The controlled stand-in for static/platform/admin-appearance.mjs. It
// implements only the surface the bootstrap script touches: mount(host,
// options) → dispose, dispose.ready. The unhide of options.status mirrors
// the real controller's contract that the explicit status element is its
// one load-error surface (setStatus shows it on a load failure) — exactly
// the state the bootstrap's failed() must undo before showing recovery.
function appearanceModule({ mountGate = Promise.resolve(), ready = Promise.resolve(), loaded = Promise.resolve(true), readyCallsOnError = false } = {}) {
  const gateKey = gateOnGlobalThis(mountGate);
  const readyKey = gateOnGlobalThis(ready);
  const loadedKey = gateOnGlobalThis(loaded);
  return dataModule(`
    await globalThis[${JSON.stringify(gateKey)}];
    export function mount(host, options) {
      globalThis.__appearanceMounts.push({ host: host, status: options.status, hasOnError: typeof options.onError === 'function' });
      options.status.hidden = false;
      const panel = document.createElement('section');
      panel.textContent = 'Appearance panel';
      host.replaceChildren(panel);
      const dispose = () => { globalThis.__appearanceDisposals += 1; };
      dispose.loaded = globalThis[${JSON.stringify(loadedKey)}];
      dispose.ready = ${readyCallsOnError ? `globalThis[${JSON.stringify(readyKey)}].then(function (go) {
        if (go) options.onError(new Error('Missing Appearance dependency: platform/admin-session.mjs'));
      })` : `globalThis[${JSON.stringify(readyKey)}]`};
      return dispose;
    }
  `);
}
const rejectedModule = dataModule('throw new Error("module unavailable")');

function installPage({ moduleUrl } = {}) {
  const previous = Object.fromEntries(
    ['document', 'window', 'location', 'setTimeout', 'clearTimeout', '__appearanceMounts', '__appearanceDisposals', '__appearanceModuleUrl__']
      .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const host = new Element('div');
  const placed = placeHost(Element, host);
  const status = new Element('p');
  status.hidden = true; // the shipped markup: <p id="configuracion-status" … hidden>
  const events = new Map();
  const timers = new Map();
  const reloads = [];
  let nextTimer = 1;
  globalThis.document = {
    getElementById(id) {
      return id === 'sec-configuracion' ? host
        : id === 'configuracion-status' ? status
        : id === 'appearance-load-recovery' ? { content: { cloneNode: recoveryClone } }
        : null;
    },
    createElement(tagName) { return new Element(tagName); },
  };
  globalThis.window = { addEventListener(type, listener) { events.set(type, listener); } };
  globalThis.location = { href: 'https://turnos.example/admin/appearance', reload() { reloads.push(1); } };
  globalThis.setTimeout = (callback, delay) => {
    const id = nextTimer++;
    timers.set(id, { callback, delay });
    return id;
  };
  globalThis.clearTimeout = id => { timers.delete(id); };
  globalThis.__appearanceMounts = [];
  globalThis.__appearanceDisposals = 0;
  globalThis.__appearanceModuleUrl__ = moduleUrl;
  return {
    host, status, events, timers, reloads, slowCopy: placed.slowCopy,
    setModuleUrl(url) { globalThis.__appearanceModuleUrl__ = url; },
    async execute() {
      const output = withScreenBoot(moduleScript).replace(staticExpression, 'globalThis.__appearanceModuleUrl__');
      assert.doesNotMatch(output, /\{\{|\}\}/, 'all server expressions in the executed module script were substituted');
      await import(dataModule(`${output}\n// appearance bootstrap test run: ${Math.random()}`));
      await settle();
    },
    fireDeadline() {
      const entry = [...timers.entries()].find(([, timer]) => timer.delay === 8000);
      assert.ok(entry, 'an armed 8000ms load deadline exists');
      timers.delete(entry[0]);
      entry[1].callback();
    },
    restore() {
      // Retire the real bootstrap before removing the globals it owns.
      events.get('pagehide')?.({ persisted: false });
      assert.equal(timers.size, 0, 'bootstrap cleanup must release every owned deadline');
      for (const [key, descriptor] of Object.entries(previous)) {
        if (descriptor === undefined) delete globalThis[key];
        else Object.defineProperty(globalThis, key, descriptor);
      }
      for (const key of gateGlobals) delete globalThis[key];
      gateGlobals.clear();
    },
  };
}

test('a successful boot mounts the dependency with the explicit host and status inputs', async () => {
  const page = installPage({ moduleUrl: appearanceModule() });
  try {
    await page.execute();
    assert.equal(page.events.size, 2, 'pagehide and pageshow are registered exactly once each');
    assert.ok(page.events.has('pagehide') && page.events.has('pageshow'));
    assert.equal(globalThis.__appearanceMounts.length, 1, 'the dependency mounted exactly once');
    const mount = globalThis.__appearanceMounts[0];
    assert.equal(mount.host, page.host, 'mount receives the real #sec-configuracion host');
    assert.equal(mount.status, page.status, 'mount receives the real #configuracion-status element');
    assert.equal(mount.hasOnError, true, 'the boot passes its own failed() as onError');
    assert.equal(page.host.children.length, 1, 'the mounted panel owns the host');
    assert.equal(page.host.children[0].textContent, 'Appearance panel');
    assert.equal(page.status.hidden, false, 'a healthy boot leaves the status line to the controller');
    assert.equal(page.timers.size, 0, 'a resolved ready cleared the load deadline');
    assert.equal(page.reloads.length, 0, 'no reload on a healthy boot');
    assert.equal(globalThis.__appearanceDisposals, 0, 'nothing is disposed while the page lives');
  } finally { page.restore(); }
});

test('the load deadline stays armed until the mount\'s dependencies are in', async () => {
  const loaded = deferred();
  const page = installPage({ moduleUrl: appearanceModule({ loaded: loaded.promise, ready: new Promise(() => {}) }) });
  try {
    await page.execute();
    assert.equal(globalThis.__appearanceMounts.length, 1, 'the controller mounted');
    assert.equal(page.timers.size, 1, 'the deadline is still armed while the dependencies are pending');
    const [armed] = page.timers.values();
    assert.equal(armed.delay, 8000, 'the deadline is the shipped eight seconds');
    loaded.resolve(true);
    await settle();
    assert.equal(page.timers.size, 0, 'loaded dependencies clear the deadline');
    assert.equal(page.host.children.length, 1, 'the panel stayed mounted');
    assert.equal(globalThis.__appearanceDisposals, 0);
  } finally { page.restore(); }
});

test('a first read slower than the deadline never shows the load failure', async () => {
  const ready = deferred();
  const page = installPage({ moduleUrl: appearanceModule({ ready: ready.promise }) });
  try {
    await page.execute();
    assert.equal(page.timers.size, 0, 'the dependencies are in, so no deadline is left to fire');
    assert.equal(page.host.children.length, 1, 'the panel stays while the first read is pending');
    assert.equal(page.host.children[0].textContent, 'Appearance panel');
    ready.resolve();
    await settle();
    assert.equal(page.host.children.length, 1, 'the late answer leaves the panel');
    assert.equal(globalThis.__appearanceDisposals, 0);
  } finally { page.restore(); }
});

test('dependencies that fail to load keep the deadline armed so the recovery still appears', async () => {
  const page = installPage({ moduleUrl: appearanceModule({ loaded: Promise.resolve(false), ready: new Promise(() => {}) }) });
  try {
    await page.execute();
    assert.equal(page.timers.size, 1, 'a failed dependency set does not disarm the deadline');
    page.fireDeadline();
    assert.ok(page.slowCopy(), 'the deadline showed the copy beside the screen');
    assert.equal(page.host.children[0].textContent, 'Appearance panel', 'the panel stays where it was');
  } finally { page.restore(); }
});

test('a rejected dependency import shows the template recovery and hides the status line', async () => {
  const page = installPage({ moduleUrl: rejectedModule });
  try {
    await page.execute();
    assert.equal(globalThis.__appearanceMounts.length, 0, 'a failed import never mounts');
    assert.equal(page.host.children.length, 2, 'recovery is one alert line and one action');
    const [alert, retry] = page.host.children;
    assert.equal(alert.getAttribute('role'), 'alert');
    assert.equal(alert.getAttribute('class'), 'adm-empty');
    assert.equal(alert.textContent, 'Algunas opciones no se pudieron cargar. Recargá la página.');
    assert.equal(retry.getAttribute('data-retry'), '', 'the action is the data-retry button');
    assert.equal(retry.getAttribute('type'), 'button');
    assert.equal(retry.getAttribute('class'), 'btn');
    assert.equal(retry.textContent, 'Recargar');
    assert.equal(page.status.hidden, true, 'failed() hides the status line');
    assert.equal(page.timers.size, 0, 'a failed boot disarms its deadline');
    retry.onclick();
    assert.equal(page.reloads.length, 1, 'the current recovery action reloads the page');
  } finally { page.restore(); }
});

test('a rejected mount.ready disposes the half-mounted controller before showing recovery', async () => {
  const ready = deferred();
  const page = installPage({ moduleUrl: appearanceModule({ ready: ready.promise }) });
  try {
    await page.execute();
    assert.equal(page.host.children.length, 1, 'the panel had painted');
    assert.equal(page.status.hidden, false, 'the controller owned a visible status line');
    ready.reject(new Error('controller failed'));
    await settle();
    assert.equal(page.host.children.length, 2, 'recovery replaced the dead panel');
    assert.equal(page.status.hidden, true, 'failed() re-hides the status line the controller had taken');
    assert.equal(globalThis.__appearanceDisposals, 1, 'the half-mounted controller is disposed, not leaked');
    assert.equal(page.timers.size, 0);
    assert.equal(page.reloads.length, 0);
  } finally { page.restore(); }
});

test('the controller\'s own fail-closed onError drives the same recovery, and its later ready resolution is a no-op', async () => {
  const ready = deferred();
  const page = installPage({ moduleUrl: appearanceModule({ ready: ready.promise, readyCallsOnError: true }) });
  try {
    await page.execute();
    assert.equal(globalThis.__appearanceMounts.length, 1);
    ready.resolve(true);
    await settle();
    assert.equal(page.host.children.length, 2, 'the missing-dependency failure reached failed() through onError');
    assert.equal(page.host.children[0].getAttribute('role'), 'alert');
    assert.equal(globalThis.__appearanceDisposals, 1, 'the failed mount was disposed');
    assert.equal(page.status.hidden, true);
    assert.equal(page.timers.size, 0);
    assert.equal(page.reloads.length, 0);
    // ready resolves AFTER onError ran: the stale generation guard must turn
    // the follow-up .then into a silent no-op, not a second recovery pass.
    await settle();
    assert.equal(page.host.children.length, 2, 'no second recovery pass');
    assert.equal(page.reloads.length, 0);
  } finally { page.restore(); }
});

test('the eight-second deadline says the load is late and a late import still mounts', async () => {
  const heldImport = deferred();
  const page = installPage({ moduleUrl: appearanceModule({ mountGate: heldImport.promise }) });
  try {
    await page.execute();
    assert.equal(page.timers.size, 1, 'the deadline is armed while the import is in flight');
    page.fireDeadline();
    assert.ok(page.slowCopy(), 'the deadline showed the copy beside the screen');
    assert.equal(page.host.children.length, 0, 'never inside the screen');
    heldImport.resolve();
    await settle();
    assert.equal(globalThis.__appearanceMounts.length, 1, 'an import settling after the deadline still mounts');
    assert.equal(page.host.children[0].textContent, 'Appearance panel');
    assert.equal(page.slowCopy(), null, 'the healthy late answer takes the copy down');
    assert.equal(globalThis.__appearanceDisposals, 0);
    assert.equal(page.reloads.length, 0);
  } finally { page.restore(); }
});

test('a late import rejecting after pagehide paints nothing and stays inert', async () => {
  const heldImport = deferred();
  const page = installPage({ moduleUrl: appearanceModule({ mountGate: heldImport.promise }) });
  try {
    await page.execute();
    assert.equal(page.timers.size, 1, 'the deadline was armed');
    page.events.get('pagehide')();
    assert.equal(page.timers.size, 0, 'pagehide disarms the pending deadline');
    heldImport.reject(new Error('module unavailable'));
    await settle();
    assert.equal(globalThis.__appearanceMounts.length, 0, 'the obsolete import cannot mount');
    assert.equal(page.host.children.length, 0, 'a stale failure cannot paint recovery after pagehide');
    assert.equal(page.status.hidden, true);
    assert.equal(page.reloads.length, 0);
  } finally { page.restore(); }
});

test('pagehide disposes the mounted controller exactly once; repeated hides are inert', async () => {
  const page = installPage({ moduleUrl: appearanceModule() });
  try {
    await page.execute();
    assert.equal(globalThis.__appearanceMounts.length, 1);
    page.events.get('pagehide')();
    assert.equal(globalThis.__appearanceDisposals, 1, 'pagehide disposes the live controller');
    page.events.get('pagehide')();
    assert.equal(globalThis.__appearanceDisposals, 1, 'a second pagehide finds nothing left to dispose');
    assert.equal(page.timers.size, 0);
    assert.equal(globalThis.__appearanceMounts.length, 1, 'no remount happened on the hide itself');
  } finally { page.restore(); }
});

test('a persisted pageshow remounts a fresh controller; a vanilla pageshow does not', async () => {
  const page = installPage({ moduleUrl: appearanceModule() });
  try {
    await page.execute();
    page.events.get('pageshow')({ persisted: false });
    await settle();
    assert.equal(globalThis.__appearanceMounts.length, 1, 'a vanilla pageshow restarts nothing');
    page.events.get('pagehide')();
    assert.equal(globalThis.__appearanceDisposals, 1);
    page.events.get('pageshow')({ persisted: true });
    await settle();
    assert.equal(globalThis.__appearanceMounts.length, 2, 'a bfcache restore remounts the controller');
    for (const mount of globalThis.__appearanceMounts) {
      assert.equal(mount.host, page.host, 'every generation mounts into the same real host');
      assert.equal(mount.status, page.status);
    }
    assert.equal(globalThis.__appearanceDisposals, 1, 'only the retired generation was disposed');
    assert.equal(page.host.children.length, 1, 'the fresh panel owns the host');
    assert.equal(page.timers.size, 0, 'the remount completed and cleared its own deadline');
  } finally { page.restore(); }
});

test('a stale recovery button is inert once a new generation owns the page', async () => {
  const page = installPage({ moduleUrl: rejectedModule });
  try {
    await page.execute();
    const staleRetry = page.host.children[1];
    staleRetry.onclick();
    assert.equal(page.reloads.length, 1, 'while current, the recovery action reloads');
    // The bfcache restore boots a healthy controller in a new generation.
    page.setModuleUrl(appearanceModule());
    page.events.get('pagehide')();
    page.events.get('pageshow')({ persisted: true });
    await settle();
    assert.equal(globalThis.__appearanceMounts.length, 1, 'the new generation mounted');
    assert.equal(page.host.children.length, 1, 'the mounted panel replaced the recovery block');
    assert.equal(page.host.children[0].textContent, 'Appearance panel');
    staleRetry.onclick();
    assert.equal(page.reloads.length, 1, 'the retired generation\'s recovery can no longer reload');
    assert.equal(page.status.hidden, false, 'the new generation\'s controller owns the status line');
  } finally { page.restore(); }
});
