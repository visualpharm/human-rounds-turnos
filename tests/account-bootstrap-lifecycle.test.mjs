// Exercise the lifecycle script emitted by templates/account.html itself. The
// only script substitution below is the server-produced static URL. The recovery
// template DOM is an explicit fixture, NOT evidence for real Jinja translation.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const template = await readFile(new URL('../templates/account.html', import.meta.url), 'utf8');
const moduleScript = /<script type="module">\n([\s\S]*?)<\/script>/.exec(template)?.[1];
assert.ok(moduleScript, 'account template contains its native module lifecycle script');

const nativeSetTimeout = globalThis.setTimeout.bind(globalThis);
const tick = () => new Promise((resolve) => nativeSetTimeout(resolve, 0));
async function settle(turns = 4) {
  for (let turn = 0; turn < turns; turn += 1) await tick();
}
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
function dataModule(source) {
  return `data:text/javascript,${encodeURIComponent(source)}`;
}

class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName;
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.attributes = new Map();
    this.href = '';
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  replaceChildren(...children) { this.children = children.flatMap((child) => child.fragment ? child.children : [child]); }
}

function accountModule({ mountGate = Promise.resolve(), ready = Promise.resolve() } = {}) {
  const gateKey = `__accountMountGate_${Math.random()}`;
  const readyKey = `__accountReady_${Math.random()}`;
  globalThis[gateKey] = mountGate;
  globalThis[readyKey] = ready;
  return dataModule(`
    await globalThis[${JSON.stringify(gateKey)}];
    export function mount(host, options) {
      globalThis.__accountMounts.push({ freshSession: options.freshSession, bootstrap: window.__ACCOUNT_BOOTSTRAP__ });
      const sensitive = document.createElement('p');
      sensitive.textContent = 'Sensitive patient account data';
      host.replaceChildren(sensitive);
      const dispose = () => { globalThis.__accountDisposals += 1; };
      dispose.ready = globalThis[${JSON.stringify(readyKey)}];
      return dispose;
    }
  `);
}

function renderedScript({ accountUrl }) {
  const output = moduleScript
    .replace('"{{ static(\'platform/account.mjs\') }}"', 'globalThis.__accountModuleUrl__');
  assert.doesNotMatch(output, /{{|}}/, 'all server expressions in the executed module script were substituted');
  globalThis.__accountModuleUrl__ = accountUrl;
  return dataModule(`${output}\n// lifecycle test run: ${Math.random()}`);
}

function installPage({ accountUrl, bootstrap = { usuario: { nombre: 'stale bootstrap' } }, errorMessage, reloadLabel } = {}) {
  const previous = Object.fromEntries(['document', 'window', 'location', 'setTimeout', 'clearTimeout', '__accountMounts', '__accountDisposals', '__accountModuleUrl__']
    .map((key) => [key, globalThis[key]]));
  const host = new Element('div');
  const events = new Map();
  const timers = new Map();
  let nextTimer = 1;
  const recovery = { content: { cloneNode() {
    const message = new Element('p');
    message.className = 'account-load-error';
    message.setAttribute('role', 'alert');
    message.textContent = errorMessage || 'El formulario no pudo cargarse.';
    const reload = new Element('a');
    reload.textContent = reloadLabel || 'Recargar ahora';
    return { fragment: true, children: [message, reload], querySelector() { return reload; } };
  } } };
  globalThis.document = {
    getElementById(id) { return id === 'cuenta-host' ? host : id === 'account-load-recovery' ? recovery : null; },
    createElement(tagName) { return new Element(tagName); },
  };
  globalThis.window = {
    __ACCOUNT_BOOTSTRAP__: bootstrap,
    addEventListener(type, listener) { events.set(type, listener); },
  };
  globalThis.location = { href: 'https://turnos.example/account' };
  globalThis.setTimeout = (callback, delay) => {
    const id = nextTimer++;
    timers.set(id, { callback, delay });
    return id;
  };
  globalThis.clearTimeout = (id) => { timers.delete(id); };
  globalThis.__accountMounts = [];
  globalThis.__accountDisposals = 0;
  return {
    host, events, timers,
    async execute() { await import(renderedScript({ accountUrl, errorMessage, reloadLabel })); await settle(); },
    fireTimeout(delay = 8000) {
      const entry = [...timers.entries()].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, `an active ${delay}ms lifecycle timeout exists`);
      timers.delete(entry[0]);
      entry[1].callback();
    },
    restore() {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
      }
    },
  };
}

test('pagehide disposes the mounted account and clears sensitive DOM', async () => {
  const page = installPage({ accountUrl: accountModule() });
  try {
    await page.execute();
    assert.equal(page.host.children[0].textContent, 'Sensitive patient account data');
    page.events.get('pagehide')();
    assert.equal(globalThis.__accountDisposals, 1, 'pagehide disposes the old Account instance');
    assert.equal(page.host.children.length, 0, 'pagehide removes sensitive old account DOM');
  } finally { page.restore(); }
});

test('a stale import completion cannot remount after pagehide', async () => {
  const heldImport = deferred();
  const page = installPage({ accountUrl: accountModule({ mountGate: heldImport.promise }) });
  try {
    await page.execute();
    page.events.get('pagehide')();
    heldImport.resolve();
    await settle();
    assert.equal(globalThis.__accountMounts.length, 0, 'the obsolete import cannot mount after pagehide');
    assert.equal(page.host.children.length, 0, 'the obsolete import cannot restore sensitive DOM');
  } finally { page.restore(); }
});

test('persisted pageshow remounts with a fresh session rather than stale bootstrap', async () => {
  const page = installPage({ accountUrl: accountModule(), bootstrap: { usuario: { nombre: 'old cached person' } } });
  try {
    await page.execute();
    page.events.get('pagehide')();
    page.events.get('pageshow')({ persisted: true });
    await settle();
    assert.deepEqual(globalThis.__accountMounts.map((call) => call.freshSession), [false, true]);
    assert.equal(globalThis.__accountMounts[1].bootstrap.usuario.nombre, 'old cached person', 'freshSession:true gives Account the fresh-session branch instead of choosing bootstrap in the page script');
  } finally { page.restore(); }
});

test('module failure shows the server-localized alert and a real reload link', async () => {
  const page = installPage({
    accountUrl: dataModule('throw new Error("module unavailable")'),
    errorMessage: 'No se pudo abrir tu cuenta.',
    reloadLabel: 'Recargar página',
  });
  try {
    await page.execute();
    assert.equal(page.host.children.length, 2);
    const [message, reload] = page.host.children;
    assert.equal(message.className, 'account-load-error');
    assert.equal(message.getAttribute('role'), 'alert');
    assert.equal(message.textContent, 'No se pudo abrir tu cuenta.');
    assert.equal(reload.textContent, 'Recargar página');
    assert.equal(reload.href, 'https://turnos.example/account');
    let prevented = false, reloads = 0;
    globalThis.location.reload = () => { reloads++; };
    reload.onclick({ preventDefault() { prevented = true; } });
    assert.equal(prevented, true, 'do not treat a deep-link recovery as same-document navigation');
    assert.equal(reloads, 1, 'the recovery action explicitly reloads the document');
  } finally { page.restore(); }
});

test('past the eight-second deadline a late healthy mount replaces the recovery copy', async () => {
  const heldImport = deferred();
  const page = installPage({ accountUrl: accountModule({ mountGate: heldImport.promise }) });
  try {
    await page.execute();
    page.fireTimeout();
    assert.equal(page.host.children[0].className, 'account-load-error', 'a slow load says so at the deadline');
    heldImport.resolve();
    await settle();
    assert.equal(globalThis.__accountMounts.length, 1, 'the slow load is not abandoned at the deadline');
    assert.equal(page.host.children.length, 1);
    assert.equal(page.host.children[0].textContent, 'Sensitive patient account data', 'the late account replaces the recovery copy');
    assert.equal(globalThis.__accountDisposals, 0);
  } finally { page.restore(); }
});

test('the deadline never covers an account that already drew', async () => {
  const ready = deferred();
  const page = installPage({ accountUrl: accountModule({ ready: ready.promise }) });
  try {
    await page.execute();
    page.fireTimeout();
    assert.equal(page.host.children[0].textContent, 'Sensitive patient account data');
    ready.resolve();
    await settle();
    assert.equal(page.host.children[0].textContent, 'Sensitive patient account data');
  } finally { page.restore(); }
});

test('a deadline that fired before pagehide cannot mount the obsolete import', async () => {
  const heldImport = deferred();
  const page = installPage({ accountUrl: accountModule({ mountGate: heldImport.promise }) });
  try {
    await page.execute();
    page.fireTimeout();
    page.events.get('pagehide')();
    heldImport.resolve();
    await settle();
    assert.equal(globalThis.__accountMounts.length, 0);
    assert.equal(page.host.children.length, 0);
  } finally { page.restore(); }
});

test('a successful Account.ready clears the eight-second timeout', async () => {
  const ready = deferred();
  const page = installPage({ accountUrl: accountModule({ ready: ready.promise }) });
  try {
    await page.execute();
    assert.equal(page.timers.size, 1, 'the timeout remains until Account.ready resolves');
    ready.resolve();
    await settle();
    assert.equal(page.timers.size, 0, 'successful readiness clears the lifecycle timeout');
  } finally { page.restore(); }
});
