// static/platform/admin-login.mjs + the direct /admin/login screen —
// EXECUTABLE controller tests. This suite imports the REAL production
// native module (no VM, no import/export stripping, no fake exports)
// against a minimal hand-rolled DOM — the repo's established pattern.
// No assertion here inspects source text: every claim is proven by driving
// the controller's own wired event handlers (the same submit/click the
// browser fires) and observing the fetch it issues, the location it
// assigns, and the DOM it mutates.
//
// What that buys, per acceptance:
//   - password login: the form's standard name="username" field is read by
//     the controller and POSTed as the API's `usuario` payload field;
//   - server-owned direct landing: a successful password OR passkey login on
//     /admin/login always hands off to /admin.  A safe explicit data-next is
//     carried there, but the client never trusts a returned role to select a
//     screen; the Python resolver proves role/default/preference policy;
//   - unknown roles also reach /admin, where the authenticated server applies
//     the one fail-closed role policy instead of duplicating it in JavaScript;
//   - expired session: on a direct screen the shared controller re-enters
//     through /admin/login?next=<current screen>, never /admin;
//   - no function API namespace: window.SanaAdminLogin is gone; configuration
//     data globals remain, named ESM exports are the function API;
//   - a submit that lands before boot() is queued and processed after i18n
//     is ready — never lost, never left to the browser's default navigation;
//   - the real failure paths (wrong password, need_totp, 6-digit code,
//     cancelled passkey) still behave.
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const MODULE_PATH = join(ROOT, 'static/platform/admin-login.mjs');
const MODULE_URL = pathToFileURL(MODULE_PATH).href;
const I18N_URL = pathToFileURL(join(ROOT, 'static/platform/i18n.mjs')).href;

test('the classic root controller is physically retired', () => {
  assert.equal(existsSync(join(ROOT, 'admin-login.js')), false);
});

// ---------------------------------------------------------------------------
// Minimal DOM: exactly the nodes the gate paths touch. Each element records
// its listeners so tests dispatch the controller's OWN wiring.
// ---------------------------------------------------------------------------
class El {
  constructor(id, attrs = {}) {
    this.id = id;
    this.attrs = new Map(Object.entries(attrs));
    this.listeners = {};
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.disabled = false;
    this.hidden = 'hidden' in attrs;
    this.style = {};
  }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  setAttribute(name, v) { this.attrs.set(name, String(v)); }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    this.listeners[type] = list.filter((item) => item !== fn);
  }
  focus() {}
  dispatch(type, ev) {
    ev = ev || {};
    if (!ev.preventDefault) ev.preventDefault = () => {};
    return Promise.all((this.listeners[type] || []).map((fn) => fn(ev)));
  }
}

function buildGate() {
  const els = {
    'gate-wrap': new El('gate-wrap'),
    'gate': new El('gate'),
    'gate-form': new El('gate-form', { autocomplete: 'on' }),
    'gate-creds': new El('gate-creds'),
    'gate-user': new El('gate-user', { name: 'username', type: 'text', autocomplete: 'username' }),
    'gate-pass': new El('gate-pass', { name: 'password', type: 'password', autocomplete: 'current-password' }),
    'gate-code': new El('gate-code', { name: 'otp', autocomplete: 'one-time-code' }),
    'gate-2fa': new El('gate-2fa'),
    'gate-turnstile': new El('gate-turnstile'),
    'gate-btn': new El('gate-btn'),
    'gate-passkey': new El('gate-passkey', { hidden: '' }),
    'gate-forgot': new El('gate-forgot'),
    'gate-reset-msg': new El('gate-reset-msg'),
    'gate-err': new El('gate-err'),
    'gate-intro': new El('gate-intro'),
    'gate-side': new El('gate-side'),
    'gp-card': new El('gp-card'),
  };
  const document = {
    documentElement: { getAttribute: () => 'es' },
    querySelector: (sel) => {
      if (sel && sel.charAt(0) === '#') return els[sel.slice(1)] || null;
      return null;
    },
    querySelectorAll: () => [],
    getElementById: (id) => els[id] || null,
  };
  return { els, document };
}

const flush = () => new Promise((r) => setTimeout(r, 10));

let bootLock = Promise.resolve();

// Boot the REAL native module in a fresh import. opts:
//   dataNext   — the form's data-next attribute value (omitted = absent)
//   direct     — true sets window.SANA_ADMIN_SCREEN = "login" first
//   pathname   — location.pathname (default /admin/login)
//   search     — location.search (default empty)
//   fetchImpl  — fetch (default: fail the test if called unexpectedly)
//   passkeys   — the passkeys.mjs namespace stub (default: unsupported)
//   catalog    — optional i18n catalog adopted before first T()
//   skipBoot   — import without calling boot() (early-submit evidence)
async function boot(opts = {}) {
  let releaseLock;
  const next = new Promise((resolve) => { releaseLock = resolve; });
  const previousLock = bootLock;
  bootLock = next;
  await previousLock;

  const { els, document } = buildGate();
  if (opts.staffPending) els['gate-form'].setAttribute('data-staff-pending', '1');
  if ('dataNext' in opts) els['gate-form'].setAttribute('data-next', opts.dataNext);
  const calls = { fetches: [], enterArgs: [] };
  const location = {
    href: '', pathname: opts.pathname || '/admin/login', search: opts.search || '',
  };
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    location: globalThis.location,
    fetch: globalThis.fetch,
    SANA_ADMIN_SCREEN: globalThis.SANA_ADMIN_SCREEN,
    SANA_STATIC: globalThis.SANA_STATIC,
    SANA_I18N: globalThis.SANA_I18N,
    SanaAdminLogin: globalThis.SanaAdminLogin,
  };
  const passkeys = opts.passkeys || {
    soportado: () => false,
    ingresar: () => Promise.reject(new Error('no passkeys')),
    cancelado: () => false,
  };
  globalThis.__TURNOS_PASSKEY_STUBS ||= new Map();
  const stubId = `${Date.now()}-${Math.random()}`;
  globalThis.__TURNOS_PASSKEY_STUBS.set(stubId, passkeys);
  const moduleSource = [
    `const api = globalThis.__TURNOS_PASSKEY_STUBS.get(${JSON.stringify(stubId)});`,
    'export const soportado = (...args) => api.soportado(...args);',
    'export const ingresar = (...args) => api.ingresar(...args);',
    'export const cancelado = (...args) => api.cancelado(...args);',
  ].join('\n');
  const passkeysUrl = `data:text/javascript,${encodeURIComponent(moduleSource)}`;

  const innerFetch = opts.fetchImpl || ((url) => { throw new Error('unexpected fetch: ' + url); });
  const fetchImpl = async (url, init) => {
    const path = typeof url === 'string' ? url : '';
    if (path === '/api/admin/config' || path.startsWith('/api/admin/config?')) {
      return { ok: true, status: 200, json: async () => ({ turnstile_sitekey: null, totp: false, usuario: 'admin' }) };
    }
    if (path === '/api/login-panel' || path.startsWith('/api/login-panel?')) {
      return { ok: false, status: 404, json: async () => ({}) };
    }
    calls.fetches.push({ url, init });
    return innerFetch(url, init);
  };

  globalThis.document = document;
  globalThis.location = location;
  globalThis.fetch = fetchImpl;
  globalThis.window = globalThis;
  if (opts.direct) globalThis.SANA_ADMIN_SCREEN = 'login';
  else delete globalThis.SANA_ADMIN_SCREEN;
  globalThis.SANA_I18N = { catalog: opts.catalog || {} };
  globalThis.SANA_STATIC = {
    'platform/passkeys.mjs': passkeysUrl,
    'platform/i18n.mjs': I18N_URL,
  };
  delete globalThis.SanaAdminLogin;

  const restore = () => {
    globalThis.window = previous.window;
    globalThis.document = previous.document;
    globalThis.location = previous.location;
    globalThis.fetch = previous.fetch;
    if (previous.SANA_ADMIN_SCREEN === undefined) delete globalThis.SANA_ADMIN_SCREEN;
    else globalThis.SANA_ADMIN_SCREEN = previous.SANA_ADMIN_SCREEN;
    if (previous.SANA_STATIC === undefined) delete globalThis.SANA_STATIC;
    else globalThis.SANA_STATIC = previous.SANA_STATIC;
    if (previous.SANA_I18N === undefined) delete globalThis.SANA_I18N;
    else globalThis.SANA_I18N = previous.SANA_I18N;
    if (previous.SanaAdminLogin === undefined) delete globalThis.SanaAdminLogin;
    else globalThis.SanaAdminLogin = previous.SanaAdminLogin;
    globalThis.__TURNOS_PASSKEY_STUBS.delete(stubId);
    releaseLock();
  };

  try {
    const i18n = await import(I18N_URL);
    i18n.setCatalog(opts.catalog || {});
    const api = await import(`${MODULE_URL}?login-test=${Date.now()}-${Math.random()}`);
    if (!opts.skipBoot) await api.boot({ panel: false });
    await flush();
    return { els, api, calls, location, restore, document };
  } catch (err) {
    restore();
    throw err;
  }
}

const okLogin = (rol = 'admin') => async (url, init) => {
  assert.equal(url, '/api/admin/login', 'password login must POST the one real API');
  assert.equal(init.method, 'POST');
  assert.equal(init.credentials, 'same-origin');
  assert.equal(init.headers['Content-Type'], 'application/json');
  return { ok: true, status: 200, json: async () => ({ ok: true, usuario: 'u', rol }) };
};

const passkeysCon = (rol) => ({
  soportado: () => true,
  ingresar: async () => ({ ok: true, usuario: 'u', rol, via: 'passkey' }),
  cancelado: () => false,
});

test('recovery submits the current identifier without a password and restores the button', async () => {
  const h = await boot({ fetchImpl: async (url, init) => {
    assert.equal(url, '/api/admin/reset-password');
    assert.deepEqual(JSON.parse(init.body), { action: 'request', usuario: 'doctor@example.test' });
    return { ok: true, json: async () => ({ ok: true, message: 'Neutral response' }) };
  } });
  try {
    h.els['gate-user'].value = ' doctor@example.test ';
    h.els['gate-pass'].value = 'must-not-send';
    await h.els['gate-forgot'].dispatch('click');
    assert.equal(h.calls.fetches.length, 1);
    assert.equal(h.els['gate-reset-msg'].textContent, 'Neutral response');
    assert.equal(h.els['gate-forgot'].disabled, false);
    assert.equal(h.location.href, '');
  } finally { h.restore(); }
});

test('recovery requires an identifier and allows retry after a network failure', async () => {
  const h = await boot({ fetchImpl: async () => { throw new Error('offline'); } });
  try {
    await h.els['gate-forgot'].dispatch('click');
    assert.equal(h.calls.fetches.length, 0);
    assert.match(h.els['gate-reset-msg'].textContent, /usuario o email/);
    h.els['gate-user'].value = 'synthetic.doctor';
    await h.els['gate-forgot'].dispatch('click');
    assert.match(h.els['gate-reset-msg'].textContent, /Error de red/);
    assert.equal(h.els['gate-forgot'].disabled, false);
    await h.els['gate-forgot'].dispatch('click');
    assert.equal(h.calls.fetches.length, 2);
  } finally { h.restore(); }
});

test('password submit POSTs the form username as the API usuario payload (trimmed), same-origin JSON', async () => {
  const live = await boot({
    direct: true,
    fetchImpl: async (url, init) => {
      return { ok: true, status: 200, json: async () => ({ ok: true, usuario: 'dr.house', rol: 'medico' }) };
    },
  });
  try {
    live.els['gate-user'].value = '  dr.house  ';
    live.els['gate-pass'].value = 's3cret-wrong-shape-but-fine';
    await live.els['gate-form'].dispatch('submit');
    await flush();

    const loginPosts = live.calls.fetches.filter((item) => item.url === '/api/admin/login');
    assert.equal(loginPosts.length, 1, 'exactly one POST, nothing else fires');
    const body = JSON.parse(loginPosts[0].init.body);
    assert.deepEqual(
      Object.keys(body).sort(),
      ['codigo', 'password', 'turnstile_token', 'usuario'],
    );
    assert.equal(body.usuario, 'dr.house', 'name="username" maps to the API usuario field');
    assert.equal(body.password, 's3cret-wrong-shape-but-fine');
    assert.equal(live.location.href, '/admin');
    assert.equal(typeof globalThis.SanaAdminLogin, 'undefined');
  } finally {
    live.restore();
  }
});

test('password login delegates a default target to /admin', async () => {
  const live = await boot({ direct: true, dataNext: '/admin', fetchImpl: okLogin('admin') });
  try {
    live.els['gate-user'].value = 'admin';
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin');
  } finally {
    live.restore();
  }
});

test('password login does not select a screen from admisionista response data', async () => {
  const live = await boot({ direct: true, dataNext: '/admin', fetchImpl: okLogin('admisionista') });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin');
  } finally {
    live.restore();
  }
});

test('password login does not select a screen from medico response data', async () => {
  const live = await boot({ direct: true, dataNext: '/admin', fetchImpl: okLogin('medico') });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin');
  } finally {
    live.restore();
  }
});

test('password login does not select a screen from reportes response data', async () => {
  const live = await boot({ direct: true, dataNext: '/admin', fetchImpl: okLogin('reportes') });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin');
  } finally {
    live.restore();
  }
});

test('password login with no data-next still delegates to /admin', async () => {
  const live = await boot({ direct: true, fetchImpl: okLogin('admin') });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin');
  } finally {
    live.restore();
  }
});

test('password carries validated explicit data-next to the server resolver', async () => {
  const live = await boot({
    direct: true, dataNext: '/admin/users', fetchImpl: okLogin('reportes'),
  });
  try {
    live.els['gate-user'].value = 'admin';
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin?next=' + encodeURIComponent('/admin/users'));
  } finally {
    live.restore();
  }
});

test('password: validated direct-screen query state survives the login round-trip', async () => {
  const live = await boot({
    direct: true, dataNext: '/admin/legal?doc=privacidad', fetchImpl: okLogin('admin'),
  });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin?next=' + encodeURIComponent('/admin/legal?doc=privacidad'));
  } finally {
    live.restore();
  }
});

test('password does not claim an explicit target is role-safe in JavaScript', async () => {
  const live = await boot({
    direct: true, dataNext: '/admin/patients', fetchImpl: okLogin('medico'),
  });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin?next=' + encodeURIComponent('/admin/patients'));
  } finally {
    live.restore();
  }
});

test('password: data-next="/admin/login" is self-referential — uses /admin', async () => {
  const live = await boot({
    direct: true, dataNext: '/admin/login', fetchImpl: okLogin('medico'),
  });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin');
  } finally {
    live.restore();
  }
});

const TAMPERED_NEXT = [
  '//evil.com',
  'https://evil.com/admin',
  '/admin/../users',
  '/admin/users?next=/evil',
  '/usuarios',
  '\\\\evil.com',
  '/admin@evil.com',
];
for (const bad of TAMPERED_NEXT) {
  test(`password: tampered data-next ${JSON.stringify(bad)} is ignored before server handoff`, async () => {
    const live = await boot({ direct: true, dataNext: bad, fetchImpl: okLogin('admin') });
    try {
      await live.els['gate-form'].dispatch('submit');
      await flush();
      assert.equal(live.location.href, '/admin');
      assert.notEqual(live.location.href, bad);
    } finally {
      live.restore();
    }
  });
}

test('password success outside the login screen fails closed to /admin/login', async () => {
  const live = await boot({ fetchImpl: okLogin('admin') });
  try {
    live.els['gate-user'].value = 'admin';
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin/login');
  } finally {
    live.restore();
  }
});

test('an injected default data-next outside the login screen cannot choose a landing', async () => {
  const live = await boot({ dataNext: '/admin', fetchImpl: okLogin('admin') });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin/login');
  } finally {
    live.restore();
  }
});

test('an injected foreign data-next outside the login screen is ignored', async () => {
  const live = await boot({ dataNext: '/admin/users', fetchImpl: okLogin('medico') });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin/login');
  } finally {
    live.restore();
  }
});

test('passkey login delegates every returned role to /admin', async () => {
  const cases = [
    ['admin', '/admin'],
    ['admisionista', '/admin'],
    ['medico', '/admin'],
    ['reportes', '/admin'],
  ];
  for (const [rol, target] of cases) {
    const live = await boot({ direct: true, dataNext: '/admin', passkeys: passkeysCon(rol) });
    try {
      await live.els['gate-passkey'].dispatch('click');
      await flush();
      assert.equal(live.location.href, target, `rol ${rol} hands off to ${target}`);
    } finally {
      live.restore();
    }
  }
});

test('passkey carries a validated explicit data-next to the server resolver', async () => {
  const live = await boot({ direct: true, dataNext: '/admin/settings', passkeys: passkeysCon('reportes') });
  try {
    await live.els['gate-passkey'].dispatch('click');
    await flush();
    assert.equal(live.location.href, '/admin?next=' + encodeURIComponent('/admin/settings'));
    assert.equal(live.els['gate-passkey'].disabled, false, 'button re-enabled after the flight');
  } finally {
    live.restore();
  }
});

test('passkey: tampered data-next is ignored before server handoff', async () => {
  const live = await boot({ direct: true, dataNext: '//evil.com', passkeys: passkeysCon('medico') });
  try {
    await live.els['gate-passkey'].dispatch('click');
    await flush();
    assert.equal(live.location.href, '/admin');
  } finally {
    live.restore();
  }
});

test('passkey success outside the login screen fails closed to /admin/login', async () => {
  const passkeys = { soportado: () => true, ingresar: async () => ({ ok: true, usuario: 'admin', rol: 'admin' }), cancelado: () => false };
  const live = await boot({ passkeys });
  try {
    await live.els['gate-passkey'].dispatch('click');
    await flush();
    assert.equal(live.location.href, '/admin/login');
  } finally {
    live.restore();
  }
});

test('passkey failure surfaces the controller error, no redirect, button re-enabled', async () => {
  const passkeys = {
    soportado: () => true,
    ingresar: async () => { throw new Error('credencial rechazada'); },
    cancelado: () => false,
  };
  const live = await boot({ direct: true, dataNext: '/admin/users', passkeys });
  try {
    await live.els['gate-passkey'].dispatch('click');
    await flush();
    assert.equal(live.location.href, '');
    assert.match(live.els['gate-err'].textContent, /rechazada/);
    assert.equal(live.els['gate-passkey'].disabled, false);
  } finally {
    live.restore();
  }
});

test('cancelled WebAuthn leaves the gate quiet: no error, no redirect, button re-enabled', async () => {
  const passkeys = {
    soportado: () => true,
    ingresar: async () => { throw new Error('NotAllowedError'); },
    cancelado: () => true,
  };
  const live = await boot({ direct: true, passkeys });
  try {
    await live.els['gate-passkey'].dispatch('click');
    await flush();
    assert.equal(live.location.href, '');
    assert.equal(live.els['gate-err'].textContent, '');
    assert.equal(live.els['gate-passkey'].disabled, false);
  } finally {
    live.restore();
  }
});

const DEAD_ROLES = ['marca', 'operador', 'sin-rol', ''];
for (const rol of DEAD_ROLES) {
  test(`password: returned retired/unknown rol ${JSON.stringify(rol)} still cannot alter the /admin handoff`, async () => {
    const live = await boot({ direct: true, dataNext: '/admin', fetchImpl: okLogin(rol) });
    try {
      await live.els['gate-form'].dispatch('submit');
      await flush();
      assert.equal(live.location.href, '/admin');
      assert.equal(live.els['gate-err'].textContent, '');
      assert.equal(live.els['gate-btn'].disabled, false, 'button re-enabled so staff can retry');
    } finally {
      live.restore();
    }
  });
  test(`passkey: returned retired/unknown rol ${JSON.stringify(rol)} still cannot alter the /admin handoff`, async () => {
    const live = await boot({ direct: true, passkeys: passkeysCon(rol) });
    try {
      await live.els['gate-passkey'].dispatch('click');
      await flush();
      assert.equal(live.location.href, '/admin');
      assert.equal(live.els['gate-err'].textContent, '');
      assert.equal(live.els['gate-passkey'].disabled, false);
    } finally {
      live.restore();
    }
  });
}

test('password: an identity with no rol field still delegates to /admin', async () => {
  const live = await boot({
    direct: true, dataNext: '/admin',
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ ok: true, usuario: 'u' }) }),
  });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '/admin');
    assert.equal(live.els['gate-err'].textContent, '');
  } finally {
    live.restore();
  }
});

test('redirigirSesionExpirada on a direct screen carries the exact pathname as ?next=', async () => {
  const live = await boot({ direct: true, pathname: '/admin/cola' });
  try {
    const handled = live.api.redirigirSesionExpirada();
    assert.equal(handled, true, 'the direct screen claims the expiry');
    assert.equal(live.location.href, '/admin/login?next=' + encodeURIComponent('/admin/cola'));
  } finally {
    live.restore();
  }
});

test('redirigirSesionExpirada preserves bounded direct query state', async () => {
  const target = '/admin/legal?doc=privacidad';
  const live = await boot({
    direct: true, pathname: '/admin/legal', search: '?doc=privacidad',
  });
  try {
    assert.equal(live.api.redirigirSesionExpirada(), true);
    assert.equal(live.location.href, '/admin/login?next=' + encodeURIComponent(target));
  } finally {
    live.restore();
  }
});

test('redirigirSesionExpirada on every direct screen shape: path → next, login/invalid → plain', async () => {
  const cases = [
    ['/admin/solicitudes', '/admin/login?next=' + encodeURIComponent('/admin/solicitudes')],
    ['/admin/my-patients', '/admin/login?next=' + encodeURIComponent('/admin/my-patients')],
    ['/admin/system', '/admin/login?next=' + encodeURIComponent('/admin/system')],
    ['/admin/login', '/admin/login'],
    ['/admin/../../etc', '/admin/login'],
    ['/otra/pagina', '/admin/login'],
    ['', '/admin/login'],
  ];
  for (const [pathname, expected] of cases) {
    const live = await boot({ direct: true, pathname });
    try {
      assert.equal(live.api.redirigirSesionExpirada(), true, `${pathname} handled`);
      assert.equal(live.location.href, expected, `${pathname} → ${expected}`);
    } finally {
      live.restore();
    }
  }
});

test('redirigirSesionExpirada without a screen flag returns false and navigates nowhere', async () => {
  const live = await boot({ pathname: '/admin' });
  try {
    assert.equal(live.api.redirigirSesionExpirada(), false);
    assert.equal(live.location.href, '');
  } finally {
    live.restore();
  }
});

test('the native module exposes no retired global function namespace', async () => {
  const live = await boot({ direct: true });
  try {
    assert.equal(typeof globalThis.showGate, 'undefined');
    assert.equal(typeof globalThis.SanaAdminLogin, 'undefined');
    assert.equal(typeof live.api.boot, 'function');
    assert.equal(typeof live.api.redirigirSesionExpirada, 'function');
    assert.equal('tryLogin' in live.api, true);
  } finally {
    live.restore();
  }
});

test('wrong password: human error on #gate-err, password cleared, button re-enabled, no redirect', async () => {
  const live = await boot({
    direct: true, dataNext: '/admin/users',
    fetchImpl: async () => ({ ok: false, status: 401, json: async () => ({ ok: false, error: 'credenciales' }) }),
  });
  try {
    live.els['gate-user'].value = 'admin';
    live.els['gate-pass'].value = 'nope';
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '');
    assert.match(live.els['gate-err'].textContent, /incorrect/i);
    assert.equal(live.els['gate-pass'].value, '');
    assert.equal(live.els['gate-btn'].disabled, false);
  } finally {
    live.restore();
  }
});

test('wrong-password error uses the awaited i18n catalog, never a raw key', async () => {
  const live = await boot({
    direct: true,
    catalog: { 'admin.shell.gate.err_credentials': 'Wrong user or password.' },
    fetchImpl: async () => ({ ok: false, status: 401, json: async () => ({ ok: false, error: 'credenciales' }) }),
  });
  try {
    live.els['gate-user'].value = 'admin';
    live.els['gate-pass'].value = 'nope';
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.els['gate-err'].textContent, 'Wrong user or password.');
    assert.doesNotMatch(live.els['gate-err'].textContent, /admin\.shell/);
  } finally {
    live.restore();
  }
});

test('need_totp: reveals the 2FA row, hides credentials, no redirect', async () => {
  const live = await boot({
    direct: true, dataNext: '/admin/users',
    fetchImpl: async () => ({ ok: false, status: 200, json: async () => ({ ok: false, need_totp: true }) }),
  });
  try {
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '');
    assert.equal(live.els['gate-2fa'].style.display, '', '2FA row shown');
    assert.equal(live.els['gate-creds'].style.display, 'none', 'credentials hidden');
  } finally {
    live.restore();
  }
});

test('rejected six-digit TOTP keeps the code step and names the wrong code', async () => {
  const live = await boot({
    direct: true,
    fetchImpl: async () => ({
      ok: false, status: 401,
      json: async () => ({ ok: false, need_totp: true, error: 'codigo' }),
    }),
  });
  try {
    live.els['gate-user'].value = 'admin';
    live.els['gate-pass'].value = 'secret';
    live.els['gate-code'].value = '123456';
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '');
    assert.equal(live.els['gate-2fa'].style.display, '');
    assert.match(live.els['gate-err'].textContent, /incorrect|c[oó]digo/i);
  } finally {
    live.restore();
  }
});

test('a non-six-digit code is rejected locally with no login POST', async () => {
  const live = await boot({
    direct: true,
    fetchImpl: async () => { throw new Error('login must not be posted for a short code'); },
  });
  try {
    live.els['gate-user'].value = 'admin';
    live.els['gate-pass'].value = 'secret';
    live.els['gate-code'].value = '12';
    await live.els['gate-form'].dispatch('submit');
    await flush();
    assert.equal(live.location.href, '');
    assert.equal(live.calls.fetches.filter((item) => item.url === '/api/admin/login').length, 0);
    assert.match(live.els['gate-err'].textContent, /6/);
  } finally {
    live.restore();
  }
});

test('boot binds the submit and passkey handlers, and unhides the passkey button only when supported', async () => {
  const supported = await boot({
    direct: true,
    passkeys: { soportado: () => true, ingresar: async () => ({}), cancelado: () => false },
  });
  try {
    assert.ok(supported.els['gate-form'].listeners.submit.length >= 1, 'submit handler wired');
    assert.ok(supported.els['gate-passkey'].listeners.click.length >= 1, 'passkey handler wired');
    assert.equal(supported.els['gate-passkey'].hidden, false, 'button revealed when WebAuthn is supported');
  } finally {
    supported.restore();
  }

  const unsupported = await boot({ direct: true });
  try {
    assert.equal(unsupported.els['gate-passkey'].hidden, true, 'button stays hidden without WebAuthn');
  } finally {
    unsupported.restore();
  }
});

test('an early submit before boot is processed after i18n, never left to default navigation', async () => {
  const live = await boot({
    direct: true,
    skipBoot: true,
    fetchImpl: okLogin('admin'),
  });
  try {
    assert.equal((live.els['gate-form'].listeners.submit || []).length, 0, 'handlers bind in boot(), not at import');
    live.els['gate-user'].value = 'admin';
    live.els['gate-pass'].value = 'secret';
    const prevented = { called: false };
    await live.els['gate-form'].dispatch('submit', { preventDefault: () => { prevented.called = true; } });
    assert.equal(prevented.called, false, 'no module handler yet — the page hold owns preventDefault');
    assert.equal(live.location.href, '');
    assert.equal(live.calls.fetches.filter((item) => item.url === '/api/admin/login').length, 0);

    await live.api.boot({ panel: false, pendingSubmit: true });
    await flush();
    assert.equal(live.location.href, '/admin');
    assert.equal(live.calls.fetches.filter((item) => item.url === '/api/admin/login').length, 1);
    assert.equal(typeof globalThis.SanaAdminLogin, 'undefined');
  } finally {
    live.restore();
  }
});


test('staff handoff starts at TOTP and posts proof mode without a password', async () => {
  const f = await boot({staffPending:true,direct:true,fetchImpl:okLogin('medico')});
  try {
    assert.equal(f.els['gate-2fa'].style.display, '');
    assert.equal(f.els['gate-creds'].style.display, 'none');
    f.els['gate-code'].value='123456';
    await f.api.tryLogin();
    const payload=JSON.parse(f.calls.fetches[0].init.body);
    assert.equal(payload.staff_pending,true);
    assert.equal(payload.password,'');
    assert.equal(f.location.href,'/admin');
  } finally { f.restore(); }
});
