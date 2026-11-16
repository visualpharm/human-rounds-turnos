// Runtime lifecycle contracts for the real native Account, Requests, and Labs
// modules.  These use a deliberately small browser/network seam so they remain
// DB-free while exercising the production module boundaries and dynamic imports.
import test from 'node:test';
import assert from 'node:assert/strict';

const root = new URL('..', import.meta.url);
const moduleUrl = (path) => new URL(path, root).href;
const urls = {
  account: moduleUrl('static/platform/account.mjs'),
  requests: moduleUrl('static/platform/account-requests.mjs'),
  labs: moduleUrl('static/platform/account-labs.mjs'),
};
const Account = await import(urls.account);
const Requests = await import(urls.requests);

function response(body, status = 200) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });
}

for (const failure of [500, 503, 'network']) {
  test(`real Requests ${failure} failure is an error, not an empty list or login`, async () => {
    let calls = 0;
    const env = installEnvironment({
      map: baseMap(),
      fetchImpl(url) {
        if (url === '/api/requests') {
          calls++;
          return failure === 'network' ? Promise.reject(new Error('synthetic offline')) : response({}, failure);
        }
        return response({ servicios: [], centros: [] });
      },
    });
    let dispose;
    try {
      const host = env.document.getElementById('cuenta-host');
      dispose = Requests.mount(host);
      await settle();
      assert.match(host.innerHTML, /role="alert"/);
      assert.match(host.innerHTML, /No pudimos cargar tus solicitudes/);
      assert.doesNotMatch(host.innerHTML, /ms-auth|Todavía no|Aún no/);
      assert.equal(globalThis.__authCallbacks.length, 0);
      assert.equal(calls, 1, 'no automatic request retry loop');
    } finally { dispose?.(); env.restore(); }
  });
}

for (const status of [401, 403]) {
  test(`real Requests ${status} retains the sign-in boundary`, async () => {
    const env = installEnvironment({
      map: baseMap({ 'platform/directory.mjs': dataModule('export function render() {}') }),
      fetchImpl: (url) => response({}, url === '/api/requests' ? status : 200),
    });
    let dispose;
    try {
      const host = env.document.getElementById('cuenta-host');
      dispose = Requests.mount(host);
      await settle();
      assert.match(host.innerHTML, /id="ms-auth"/);
      assert.equal(globalThis.__authCallbacks.length, 1);
      assert.doesNotMatch(host.innerHTML, /No pudimos cargar tus solicitudes/);
    } finally { dispose?.(); env.restore(); }
  });
}

test('Account retains Requests navigation when the initial request lookup fails', async () => {
  let calls = 0;
  const env = installEnvironment({
    map: baseMap({ 'platform/account-requests.mjs': urls.requests }),
    fetchImpl(url) {
      if (url === '/api/account/me') return response({ usuario: { nombre: 'Test account' } });
      if (url === '/api/requests') { calls++; return response({}, 503); }
      return response({ servicios: [], centros: [] });
    },
  });
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Account.mount(host);
    await settle();
    assert.match(host.innerHTML, /data-tab="turnos"/);
    assert.doesNotMatch(host.innerHTML, /class="rail-badge"/);
    assert.equal(env.location.hash, '#turnos');
    assert.match(env.document.getElementById('ms-host').innerHTML, /No pudimos cargar tus solicitudes/);
    assert.equal(calls, 2, 'one shell lookup and one child lookup, never an automatic loop');
  } finally { dispose?.(); env.restore(); }
});

test('a known empty bootstrap keeps «Mis turnos» as the open tab, without a redundant request lookup', async () => {
  let calls = 0;
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: 'New account' }, solicitudes: [] },
    map: baseMap(),
    fetchImpl(url) { if (url === '/api/requests') calls++; return response({}); },
  });
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Account.mount(host);
    await settle();
    // 2026-10-28: a person with no requests still gets the tab (its empty state
    // says how to book) and lands on it, not on «Datos personales».
    assert.match(host.innerHTML, /data-tab="turnos"/);
    assert.doesNotMatch(host.innerHTML, /class="rail-badge"/);
    assert.equal(env.location.hash, '#turnos');
    assert.equal(calls, 0);
  } finally { dispose?.(); env.restore(); }
});

test('the «Mis turnos» badge counts only live requests, not cancelled or past ones', async () => {
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: 'Badge account' }, solicitudes: [
      { id: 1, estado: 'aprobada', turno_pasado: false },
      { id: 2, estado: 'pendiente', turno_pasado: false },
      { id: 3, estado: 'cancelada', turno_pasado: false },
      { id: 4, estado: 'rechazada' },
      { id: 5, estado: 'aprobada', turno_pasado: true },
    ] },
    map: baseMap(),
    fetchImpl: () => response({ servicios: [], centros: [] }),
  });
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Account.mount(host);
    await settle();
    assert.match(host.innerHTML, /class="rail-badge">2</);
  } finally { dispose?.(); env.restore(); }
});

test('no badge when every request is cancelled or past', async () => {
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: 'Badge account' }, solicitudes: [
      { id: 3, estado: 'cancelada' }, { id: 5, estado: 'aprobada', turno_pasado: true },
    ] },
    map: baseMap(),
    fetchImpl: () => response({ servicios: [], centros: [] }),
  });
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Account.mount(host);
    await settle();
    assert.doesNotMatch(host.innerHTML, /class="rail-badge"/);
  } finally { dispose?.(); env.restore(); }
});

test('«Mis turnos» with no requests is an instruction with ONE big «Sacar un turno» button, not "no data"', async () => {
  const env = installEnvironment({
    map: baseMap(),
    fetchImpl: (url) => response(url === '/api/requests' ? [] : { servicios: [], centros: [] }),
  });
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Requests.mount(host, { requests: [] });
    await settle();
    const html = host.innerHTML;
    assert.match(html, /Todavía no tenés turnos\. Para sacar el primero, elegí una especialidad y un horario\./);
    assert.equal((html.match(/class="req-btn req-btn-primary mt-empty-cta" href="\/">Sacar un turno</g) || []).length, 1);
    assert.doesNotMatch(html, /class="mt-sacar"/, 'the small secondary «Sacar turno» is not repeated next to the big one');
    assert.doesNotMatch(html, /Todavía no enviaste|sin datos|No hay datos/i);
  } finally { dispose?.(); env.restore(); }
});

test('server bootstrap canonicalizes the demo URL without navigation or a second session lookup', async () => {
  let meCalls = 0;
  const replaced = [];
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: 'Bootstrapped patient' }, solicitudes: [] },
    map: baseMap(),
    fetchImpl(url) { if (url === '/api/account/me') meCalls++; return response([]); },
  });
  globalThis.window.__ACCOUNT_CANONICAL_PATH__ = '/account';
  const originalReplace = globalThis.history.replaceState;
  globalThis.history.replaceState = function (state, title, next) {
    replaced.push(next);
    if (next.startsWith('#')) originalReplace(state, title, next);
  };
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Account.mount(host);
    await dispose.ready;
    assert.equal(replaced[0], '/account#turnos');
    assert.equal(env.location.href, 'UNCHANGED');
    assert.equal(meCalls, 0);
    assert.match(host.innerHTML, /Bootstrapped patient/);
  } finally { dispose?.(); env.restore(); }
});

test('a late Requests failure cannot replace the next mounted account', async () => {
  const pending = deferred();
  const env = installEnvironment({
    map: baseMap(),
    fetchImpl: (url) => url === '/api/requests' ? pending.promise : response({}),
  });
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Requests.mount(host);
    await settle();
    dispose();
    host.innerHTML = 'Next account';
    pending.resolve({ ok: false, status: 503 });
    await settle();
    assert.equal(host.innerHTML, 'Next account');
    assert.equal(globalThis.__authCallbacks.length, 0);
  } finally { dispose?.(); env.restore(); }
});

for (const failure of [500, 503, 'offline']) {
  test(`Account session lookup ${failure} is a recoverable error, not authentication`, async () => {
    const env = installEnvironment({
      bootstrap: { usuario: { nombre: 'Previous session' }, solicitudes: [] },
      map: baseMap(),
      fetchImpl: () => failure === 'offline' ? Promise.reject(new Error('offline')) : response({}, failure),
    });
    let dispose;
    try {
      const host = env.document.getElementById('cuenta-host');
      dispose = Account.mount(host, { freshSession: true });
      await dispose.ready;
      assert.match(host.innerHTML, /role="alert"/);
      assert.equal(globalThis.__authCallbacks.length, 0);
      assert.doesNotMatch(host.innerHTML, /Previous session/);
    } finally { dispose?.(); env.restore(); }
  });
}

test('embedded Requests authentication replaces the entire shell before another account signs in', async () => {
  let expired = true;
  const env = installEnvironment({
    map: baseMap({ 'platform/account-requests.mjs': urls.requests }),
    fetchImpl(url) {
      if (url === '/api/account/me') return response({ usuario: { nombre: 'Previous patient' } });
      if (url === '/api/requests') return expired ? response({}, 401) :
        response([{ id: 'fresh', estado: 'pendiente', servicio_nombre: 'Fresh consultation' }]);
      return response({});
    },
  });
  let dispose;
  try {
    const host = env.document.getElementById('cuenta-host');
    dispose = Account.mount(host);
    await settle();
    assert.equal(host.innerHTML, 'Authentication form seam');
    assert.equal(env.document.getElementById('ms-host'), null, 'the old Requests host is retired');
    assert.equal(globalThis.__authCallbacks.length, 1);
    expired = false;
    globalThis.__authCallbacks[0]({ nombre: 'Next patient' });
    await settle();
    assert.match(host.innerHTML, /Next patient/);
    assert.doesNotMatch(host.innerHTML, /Previous patient/);
    assert.match(env.document.getElementById('ms-host').innerHTML, /Fresh consultation/);
  } finally { dispose?.(); env.restore(); }
});

for (const status of [401, 403, 503, 'offline']) {
  test(`embedded Labs ${status} respects Account authentication versus outage`, async () => {
    let expired = true;
    const env = installEnvironment({
      hash: '#labs',
      bootstrap: { usuario: { nombre: 'Previous patient' }, solicitudes: [] },
      map: baseMap({ 'platform/account-labs.mjs': urls.labs }),
      fetchImpl(url) {
        if (url === '/api/account/lab-results') {
          if (expired && status === 'offline') return Promise.reject(new Error('synthetic offline'));
          return response({ informes: [], series: [] }, expired ? status : 200);
        }
        return response([]);
      },
    });
    let dispose;
    try {
      const host = env.document.getElementById('cuenta-host');
      dispose = Account.mount(host);
      await settle();
      if (status === 401 || status === 403) {
        assert.equal(host.innerHTML, 'Authentication form seam');
        assert.equal(env.document.getElementById('lr-host'), null);
        assert.equal(globalThis.__authCallbacks.length, 1);
        expired = false;
        globalThis.__authCallbacks[0]({ nombre: 'Next patient' });
        await settle();
        assert.match(host.innerHTML, /Next patient/);
        assert.doesNotMatch(host.innerHTML, /Previous patient/);
        assert.ok(env.document.getElementById('lr-host'), 'fresh Labs mounts after reauthentication');
      } else {
        assert.equal(globalThis.__authCallbacks.length, 0);
        assert.match(host.innerHTML, /Previous patient/);
        assert.match(env.document.getElementById('lr-host').innerHTML, /role="alert"/);
      }
    } finally { dispose?.(); env.restore(); }
  });
}

test('a fresh-session mount ignores the previous document bootstrap', async () => {
  let meCalls = 0;
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: 'Previous session' }, solicitudes: [] },
    map: baseMap(),
    fetchImpl(url) {
      if (url === '/api/account/me') meCalls++;
      return Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) });
    },
  });
  try {
    const dispose = Account.mount(env.document.getElementById('cuenta-host'), { freshSession: true });
    await dispose.ready;
    assert.equal(meCalls, 1, 'restored page checks the current session instead of trusting cached HTML data');
    assert.equal(globalThis.__authCallbacks.length, 1, 'expired session returns to the real auth boundary');
    assert.doesNotMatch(env.document.getElementById('cuenta-host').innerHTML, /Previous session/);
    dispose();
  } finally { env.restore(); }
});

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle(turns = 24) {
  for (let index = 0; index < turns; index += 1) await tick();
}
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
function dataModule(source) {
  return 'data:text/javascript,' + encodeURIComponent(source);
}

class Element {
  constructor(documentRef, id = '') {
    this.documentRef = documentRef;
    this.id = id;
    this.attrs = {};
    this.children = [];
    this.className = '';
    this.isConnected = true;
    this.listeners = new Map();
    this.style = {};
    this.textContent = '';
    this._html = '';
    this.classList = {
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(' ');
      },
    };
  }

  set innerHTML(value) {
    this.documentRef.disconnect(this.children);
    this.children = [];
    this._html = String(value);
    this.documentRef.install(this._html, this);
  }
  get innerHTML() { return this._html; }
  appendChild(child) {
    child.isConnected = this.isConnected;
    this.children.push(child);
    if (child.id) this.documentRef.nodes.set(child.id, child);
    return child;
  }
  insertBefore(child) { return this.appendChild(child); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }
  click() {
    const listener = this.listeners.get('click');
    if (listener) listener({ preventDefault() {}, target: this });
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) { return this.documentRef.query(selector, this); }
  scrollIntoView() {}
}

class FakeDocument {
  constructor() {
    this.nodes = new Map();
    for (const id of ['cuenta-host', 'cuenta-lede', 'cuenta-title', 'cuenta-head']) {
      this.nodes.set(id, new Element(this, id));
    }
    this.head = { appendChild() {} };
    this.body = { appendChild() {} };
  }
  getElementById(id) { return this.nodes.get(id) || null; }
  createElement() { return new Element(this); }
  createTextNode(text) { const node = new Element(this); node.textContent = String(text); return node; }
  querySelector(selector) { return this.query(selector)[0] || null; }
  disconnect(nodes) {
    for (const node of nodes) {
      node.isConnected = false;
      this.disconnect(node.children);
      if (node.id && this.nodes.get(node.id) === node) this.nodes.delete(node.id);
    }
  }
  install(html, parent) {
    for (const match of html.matchAll(/<([a-z][\w-]*)([^>]*)>/gi)) {
      const node = new Element(this);
      const attrs = match[2] || '';
      const id = /\bid="([^"]+)"/.exec(attrs);
      const classes = /\bclass="([^"]+)"/.exec(attrs);
      node.id = id ? id[1] : '';
      node.className = classes ? classes[1] : '';
      for (const attribute of attrs.matchAll(/\b(data-[\w-]+)="([^"]*)"/g)) node.attrs[attribute[1]] = attribute[2];
      parent.appendChild(node);
    }
  }
  descendants(parent) {
    const found = [];
    const visit = (node) => {
      for (const child of node.children || []) {
        found.push(child);
        visit(child);
      }
    };
    visit(parent);
    return found;
  }
  query(selector, parent) {
    const pool = parent ? this.descendants(parent) : [...this.nodes.values()];
    if (selector.startsWith('#')) {
      const id = selector.slice(1).split(/[ .[]/, 1)[0];
      const node = this.nodes.get(id);
      return node && node.isConnected !== false ? [node] : [];
    }
    if (selector.startsWith('.')) {
      const className = selector.slice(1);
      return pool.filter((node) => node.className.split(/\s+/).includes(className));
    }
    const data = /^\[data-([\w-]+)(?:="([^"]*)")?\]$/.exec(selector);
    if (data) return pool.filter((node) => node.attrs['data-' + data[1]] != null && (data[2] == null || node.attrs['data-' + data[1]] === data[2]));
    return [];
  }
}

function installEnvironment({ hash = '#turnos', search = '', bootstrap = null, map = {}, fetchImpl } = {}) {
  const previous = {
    document: globalThis.document, window: globalThis.window, location: globalThis.location,
    history: globalThis.history, localStorage: globalThis.localStorage, fetch: globalThis.fetch,
  };
  const document = new FakeDocument();
  const events = new Map();
  const location = { hash, search, href: 'UNCHANGED' };
  globalThis.document = document;
  globalThis.window = {
    SANA_STATIC: map,
    __ACCOUNT_BOOTSTRAP__: bootstrap,
    addEventListener(type, listener) { events.set(type, listener); },
    removeEventListener(type, listener) { if (events.get(type) === listener) events.delete(type); },
    scrollTo() {},
  };
  globalThis.location = location;
  globalThis.history = {
    replaceState(_state, _title, next) { location.hash = next; },
    pushState(_state, _title, next) { location.hash = next; },
  };
  globalThis.localStorage = { getItem() { return null; }, setItem() {} };
  globalThis.fetch = fetchImpl || (() => Promise.resolve({ ok: false, json: () => Promise.resolve({}) }));
  globalThis.__authCallbacks = [];
  return {
    document, location, events,
    restore() { Object.assign(globalThis, previous); delete globalThis.__authCallbacks; },
  };
}

const i18n = dataModule('export function t(key, vars, fallback) { return fallback || key; }');
const dates = dataModule('export function amigable(day, hour) { return day + (hour ? " " + hour : ""); }');
const prep = dataModule('export function ayuno() { return { hay: false }; } export function antes() { return []; }');
const dependents = dataModule('export function renderCuenta() {}');
const listing = dataModule('export function render(mount) { mount.innerHTML = "listing ready"; }');
// This is an explicit seam for Account's external form dependency, not a stand-in
// for the production Account module under test.  It only records the auth callback.
const form = dataModule('export function renderEditar() {} export function renderAuth(host, options) { host.innerHTML = "Authentication form seam"; globalThis.__authCallbacks.push(options.onDone); } export function fileToJpeg() { return Promise.reject(new Error("unused")); }');
function baseMap(extra = {}) {
  return {
    'platform/account-form.mjs': form,
    'platform/i18n.mjs': i18n,
    'platform/dependents.mjs': dependents,
    'platform/dates.mjs': dates,
    'platform/prep.mjs': prep,
    'platform/listing.mjs': listing,
    ...extra,
  };
}

// anon-16 (10.11): on /account, «Ingresar» inside the registration form showed
// the login form under the page title «Crear cuenta». The form reports every
// mode it draws; the title follows it.
test('the /account title names the form on screen after every switch inside it', async () => {
  const modeForm = dataModule('export function renderEditar() {} export function renderAuth(host, options) { host.innerHTML = "Authentication form seam"; globalThis.__authCallbacks.push(options.onMode); options.onMode(options.defaultMode); } export function fileToJpeg() { return Promise.reject(new Error("unused")); }');
  const env = installEnvironment({
    map: baseMap({ 'platform/account-form.mjs': modeForm }),
    fetchImpl: (url) => response({}, url === '/api/account/me' ? 401 : 200),
  });
  let dispose;
  try {
    dispose = Account.mount(env.document.getElementById('cuenta-host'));
    await settle();
    const title = env.document.getElementById('cuenta-title');
    const lede = env.document.getElementById('cuenta-lede');
    assert.equal(title.textContent, 'Crear cuenta');
    const onMode = globalThis.__authCallbacks[0];
    assert.equal(typeof onMode, 'function', 'the page hands the form a mode listener');
    onMode('login');
    assert.equal(title.textContent, 'Ingresar');
    assert.match(lede.textContent, /Entrá con tu DNI/);
    onMode('recuperar');
    assert.equal(title.textContent, 'Olvidé mi contraseña');
    onMode('otp');
    assert.equal(title.textContent, 'Olvidé mi contraseña', 'a code step keeps the title of the form that led there');
    onMode('registro');
    assert.equal(title.textContent, 'Crear cuenta');
  } finally { dispose?.(); env.restore(); }
});

test('immediate Account disposal blocks delayed dependency completions', async () => {
  const accountGate = deferred();
  const i18nGate = deferred();
  const me = deferred();
  globalThis.__accountGate = accountGate.promise;
  globalThis.__i18nGate = i18nGate.promise;
  const delayedForm = dataModule('await globalThis.__accountGate; export function renderEditar() {} export function renderAuth() {}');
  const delayedI18n = dataModule('await globalThis.__i18nGate; export function t(key, vars, fallback) { return fallback || key; }');
  const env = installEnvironment({
    search: '?next=/must-not-navigate',
    map: baseMap({ 'platform/account-form.mjs': delayedForm, 'platform/i18n.mjs': delayedI18n }),
    fetchImpl(url) { return url === '/api/account/me' ? me.promise : Promise.resolve({ ok: false, json: () => Promise.resolve({}) }); },
  });
  try {
    const dispose = Account.mount(env.document.getElementById('cuenta-host'));
    dispose();
    accountGate.resolve();
    i18nGate.resolve();
    me.resolve({ ok: true, json: () => Promise.resolve({ usuario: { nombre: 'Late' } }) });
    await settle();
    assert.equal(env.document.getElementById('cuenta-host').innerHTML, '', 'disposed shell must not render delayed Account/i18n/me work');
    assert.equal(env.location.href, 'UNCHANGED', 'disposed shell must not navigate a delayed ?next response');
  } finally {
    env.restore();
    delete globalThis.__accountGate;
    delete globalThis.__i18nGate;
  }
});

test('disposing after Account started /me blocks its delayed response and redirect', async () => {
  const me = deferred();
  let meCalls = 0;
  const env = installEnvironment({
    search: '?next=/must-not-navigate',
    map: baseMap(),
    fetchImpl(url) {
      if (url === '/api/account/me') {
        meCalls += 1;
        return me.promise;
      }
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
    },
  });
  try {
    const dispose = Account.mount(env.document.getElementById('cuenta-host'));
    await settle(4);
    assert.equal(meCalls, 1, 'Account started its own-session lookup before disposal');
    dispose();
    me.resolve({ ok: true, json: () => Promise.resolve({ usuario: { nombre: 'Late' } }) });
    await settle();
    assert.equal(env.document.getElementById('cuenta-host').innerHTML, '', 'disposed shell ignores an already-started /me response');
    assert.equal(env.location.href, 'UNCHANGED', 'disposed shell cannot redirect from a delayed /me response');
  } finally {
    env.restore();
  }
});

test('a held real Requests import cannot write old data into a fresh Account remount', async () => {
  const requestsGate = deferred();
  globalThis.__requestsGate = requestsGate.promise;
  const heldRequests = dataModule('await globalThis.__requestsGate; export * from ' + JSON.stringify(urls.requests) + ';');
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: 'Old' }, solicitudes: [{ id: 'old-request-value', estado: 'pendiente', servicio_id: 'old-service', servicio_nombre: 'Old service' }] },
    map: baseMap({ 'platform/account-requests.mjs': heldRequests, 'platform/account-labs.mjs': urls.labs }),
    fetchImpl(url) {
      if (url === '/api/services') return Promise.resolve({ ok: true, json: () => Promise.resolve({ servicios: [{ id: 'fresh-service', nombre: 'Fresh service' }] }) });
      if (url === '/api/centers') return Promise.resolve({ ok: true, json: () => Promise.resolve({ centros: [] }) });
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
    },
  });
  try {
    const oldDispose = Account.mount(env.document.getElementById('cuenta-host'));
    await settle();
    const oldRequestsHost = env.document.getElementById('ms-host');
    assert.ok(oldRequestsHost, 'old shell reached the held Requests import boundary');
    const oldRequestsHtml = oldRequestsHost.innerHTML;
    oldDispose();
    globalThis.window.__ACCOUNT_BOOTSTRAP__ = { usuario: { nombre: 'Fresh' }, solicitudes: [{ id: 'fresh', estado: 'pendiente', servicio_id: 'fresh-service' }] };
    const freshDispose = Account.mount(env.document.getElementById('cuenta-host'));
    await settle(4);
    const freshRequestsHost = env.document.getElementById('ms-host');
    assert.notEqual(freshRequestsHost, oldRequestsHost, 'remount must replace the Request host itself');
    requestsGate.resolve();
    await settle();
    assert.equal(oldRequestsHost.isConnected, false, 'old Request host was retired before its held import resolved');
    assert.equal(oldRequestsHost.innerHTML, oldRequestsHtml, 'held import did not newly paint the retired old Request host');
    assert.match(env.document.getElementById('cuenta-host').innerHTML, /Fresh/, 'fresh shell still belongs to the fresh account');
    assert.doesNotMatch(env.document.getElementById('cuenta-host').innerHTML, /Old/, 'old account markup did not reappear after the held import');
    assert.match(freshRequestsHost.innerHTML, /Mis turnos/, 'the real Requests module mounted only in the fresh Request host');
    assert.match(freshRequestsHost.innerHTML, /Fresh service/, 'fresh Request host renders the fresh service data');
    assert.doesNotMatch(freshRequestsHost.innerHTML, /old-service|Old service|old-request-value/i, 'fresh Request host contains none of the old request values');
    freshDispose();
  } finally {
    env.restore();
    delete globalThis.__requestsGate;
  }
});

test('real Account logout then login remounts real Requests into the new host', async () => {
  const env = installEnvironment({
    bootstrap: { usuario: { nombre: 'One' }, solicitudes: [{ id: 'one', estado: 'pendiente', servicio_id: 'service' }] },
    map: baseMap({ 'platform/account-requests.mjs': urls.requests, 'platform/account-labs.mjs': urls.labs }),
    fetchImpl(url) {
      if (url === '/api/services') return Promise.resolve({ ok: true, json: () => Promise.resolve({ servicios: [] }) });
      if (url === '/api/centers') return Promise.resolve({ ok: true, json: () => Promise.resolve({ centros: [] }) });
      if (url === '/api/requests') return Promise.resolve({ ok: true, json: () => Promise.resolve([{ id: 'two', estado: 'pendiente', servicio_id: 'service' }]) });
      if (url === '/api/account/logout') return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
    },
  });
  try {
    const dispose = Account.mount(env.document.getElementById('cuenta-host'));
    await settle();
    const firstRequestsHost = env.document.getElementById('ms-host');
    env.document.getElementById('acc-out').click();
    await settle();
    assert.equal(globalThis.__authCallbacks.length, 1, 'logout displays the Account auth seam');
    globalThis.__authCallbacks[0]({ nombre: 'Two' });
    await settle();
    const secondRequestsHost = env.document.getElementById('ms-host');
    assert.notEqual(secondRequestsHost, firstRequestsHost, 'login creates a distinct Requests host');
    assert.equal(firstRequestsHost.isConnected, false, 'logout retired the prior Requests host');
    assert.match(env.document.getElementById('cuenta-host').innerHTML, /Two/, 'new account shell renders the logged-in user');
    assert.match(secondRequestsHost.innerHTML, /Mis turnos/, 'real Requests mounted again after the new account render');
    dispose();
  } finally {
    env.restore();
  }
});

test('direct #labs&r=0 survives Account boot and opens report zero in real Labs', async () => {
  const env = installEnvironment({
    hash: '#labs&r=0',
    bootstrap: { usuario: { nombre: 'Lab' }, solicitudes: [] },
    map: baseMap({ 'platform/account-requests.mjs': urls.requests, 'platform/account-labs.mjs': urls.labs }),
    fetchImpl(url) {
      if (url === '/api/account/lab-results') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({
          informes: [{ fecha: '2026-09-01', tipo_estudio: 'Hemograma', items: [] }],
          series: [], analisis_pendiente: 0,
        }) });
      }
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
    },
  });
  try {
    const dispose = Account.mount(env.document.getElementById('cuenta-host'));
    await settle();
    const labsHost = env.document.getElementById('lr-host');
    const detail = labsHost.children.find((child) => child.getAttribute('data-panel') === 'detalle');
    assert.equal(env.location.hash, '#labs&r=0', 'Account preserves the report fragment until Labs consumes it');
    assert.ok(detail, 'real Labs created its report-detail panel');
    assert.equal(detail.style.display, '', 'real Labs opens report zero from the direct link');
    dispose();
  } finally {
    env.restore();
  }
});
