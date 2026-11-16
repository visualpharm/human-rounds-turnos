// Delayed/rejected import and stale-screen proofs for the canonical Users
// module. Uses the real runtime files and rendered controls — not a source
// scan or a hand-rolled renderer.
import { test, within, hookOptions, openPage } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { stays } from "./helpers/wait.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file) => fs.readFileSync(path.join(ROOT, file), "utf8");
const COUNTRY_PHONE = JSON.parse(read("packs/ar/country.json")).phone;
const ORIGIN = "https://users-native.test";
const MODULE_PATH = "/static/platform/admin-users.aaaaaaaa.mjs";
const FILES = {
  [MODULE_PATH]: "static/platform/admin-users.mjs",
  "/static/platform/dates.mjs": "static/platform/dates.mjs",
  "/static/platform/format.mjs": "static/platform/format.mjs",
  "/static/platform/admin-preferences.mjs": "static/platform/admin-preferences.mjs",
  "/static/platform/admin-session.mjs": "static/platform/admin-session.mjs",
  "/static/platform/admin-table.mjs": "static/platform/admin-table.mjs",
  "/static/platform/i18n.mjs": "static/platform/i18n.mjs",
  "/static/platform/admin-filter-controls.mjs": "static/platform/admin-filter-controls.mjs",
};
const STATIC = {
  "platform/dates.mjs": "/static/platform/dates.mjs",
  "platform/format.mjs": "/static/platform/format.mjs",
  "platform/admin-preferences.mjs": "/static/platform/admin-preferences.mjs",
  "platform/admin-session.mjs": "/static/platform/admin-session.mjs",
  "platform/admin-table.mjs": "/static/platform/admin-table.mjs",
  "platform/i18n.mjs": "/static/platform/i18n.mjs",
  "platform/admin-filter-controls.mjs": "/static/platform/admin-filter-controls.mjs",
  "platform/admin-users.mjs": MODULE_PATH,
};
const ITEMS = [
  { usuario: "owner", es_dueno: true, rol: "admin", activo: true, email: "owner@example.com" },
  { usuario: "mesa.uno", rol: "admisionista", activo: true, email: "mesa@example.com" },
];
const DOCTOR = { id: "diego-arturo-rivera", nombre: "Diego Arturo Rivera" };

function deferred() {
  let release;
  let done = false;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release() { if (!done) { done = true; release(); } } };
}

function pageHtml({ screen = "usuarios", error = "No se pudo cargar. Reintentá.", extra = "" } = {}) {
  const boot = screen === "usuarios"
    ? `(function () {
        var sec = document.getElementById("sec-usuarios");
        var status = document.getElementById("users-status");
        function fail() {
          var msg = (status && status.getAttribute("data-controller-load-error")) || ${JSON.stringify(error)};
          if (sec) sec.innerHTML = '<p class="adm-empty" role="alert">' + msg + "</p>";
        }
        var url = window.SANA_STATIC && window.SANA_STATIC["platform/admin-users.mjs"];
        if (!url) { fail(); return; }
        import(url).then(function (m) { return m.boot(); }).catch(fail);
      })();`
    : "";
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
<script>window.SANA_ADMIN_SCREEN=${JSON.stringify(screen)};window.CURRENT_USER="owner";
window.SANA_COUNTRY_PHONE=${JSON.stringify(COUNTRY_PHONE)};
window.SANA_STATIC=${JSON.stringify(STATIC)};
window.sanaImportStatic=function(name){return import(window.SANA_STATIC[name]);};</script>
</head><body>
<nav id="adm-nav"><a data-nav="usuarios" href="/admin/users">Usuarios</a></nav>
<div class="adm-h1row"><h1 id="adm-h1" class="adm-h1"></h1>
<a class="btn btn-accent adm-title-action" href="/admin/users?new=1" data-users-state="new">Nuevo usuario</a></div>
<div id="sec-usuarios"></div>
<p id="users-status" class="adm-empty" role="status" aria-live="polite" hidden
   data-controller-load-error="${error}"></p>
<div id="med-user-panel"></div>
${extra}
<script>${boot}</script>
</body></html>`;
}

async function fulfillFiles(route, { holdModule, abortModule } = {}) {
  const url = new URL(route.request().url());
  const pathname = url.pathname;
  if (abortModule && pathname === MODULE_PATH) return route.abort("failed");
  if (holdModule && pathname === MODULE_PATH) {
    holdModule.requested.release();
    await holdModule.release.promise;
  }
  if (pathname === "/api/admin/users") {
    if (holdModule && holdModule.users) {
      holdModule.users.requested.release();
      await holdModule.users.release.promise;
    }
    return route.fulfill({ json: { items: ITEMS } });
  }
  if (pathname === "/api/admin/preferences") return route.fulfill({ json: { items: {} } });
  if (pathname === "/api/admin/content/doctors") {
    return route.fulfill({ json: { items: [DOCTOR] } });
  }
  if (pathname === "/api/admin/content/centers") {
    return route.fulfill({ json: { items: [{ slug: "hospital", name: "Hospital" }] } });
  }
  if (FILES[pathname]) {
    return route.fulfill({
      contentType: "text/javascript; charset=utf-8",
      body: read(FILES[pathname]),
    });
  }
  if (pathname === "/admin/users" || pathname.startsWith("/admin/users?")) {
    return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml() });
  }
  return route.fulfill({ status: 404, body: "" });
}

// One Chromium per file, a fresh context per page (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

async function withPage(run) {
  const page = await openPage(browser, { viewport: { width: 1280, height: 900 } });
  try {
    const failures = [];
    page.on("pageerror", (error) => failures.push(String(error)));
    await run(page, failures);
    assert.deepEqual(failures.filter((msg) => !/failed to fetch|Failed to fetch|Load failed|error loading dynamically imported module/i.test(msg)), []);
  } finally {
    await page.context().close();
  }
}

// Hold a real Response's JSON continuation independently of network arrival.
// This is a synthetic scheduling seam, not a persistence assertion.
async function holdBody(page, pathname, method, clone = false) {
  await page.addInitScript(({ pathname, method, clone }) => {
    const nativeFetch = window.fetch;
    let held = false;
    window.fetch = async (...args) => {
      const response = await nativeFetch(...args);
      const requestMethod = args[1]?.method || 'GET';
      if (held || new URL(response.url).pathname !== pathname || requestMethod !== method) return response;
      held = true;
      function pause(target) {
        const json = target.json.bind(target);
        target.json = async () => {
          window.__bodyWaiting = true;
          await new Promise(resolve => { window.__releaseBody = resolve; });
          try { return await json(); } finally { window.__bodySettled = true; }
        };
        return target;
      }
      if (clone) {
        const nativeClone = response.clone.bind(response);
        response.clone = () => pause(nativeClone());
      } else pause(response);
      return response;
    };
  }, { pathname, method, clone });
}

async function replaceUser(page) {
  await page.evaluate(() => {
    history.pushState({}, '', '/admin/users?user=mesa.dos'); dispatchEvent(new PopStateEvent('popstate'));
  });
  await page.waitForFunction(() => document.querySelector('#usr-email')?.value === 'second@example.com');
}

async function drainFrame(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

// Synthetic responses here prove route ownership only; the real DB-browser
// passkey suite separately proves successful registration, deletion and reset.
for (const lifecycle of ['route', 'restore']) for (const action of ['reset', 'save', 'deactivate']) for (const status of [200, 500]) {
  test(`abandoned Users ${action} response cannot notify or navigate its replacement (${lifecycle}, ${status})`, async () => {
    await withPage(async page => {
      const arrived = deferred(), released = deferred();
      const target = action === 'reset' ? '/api/admin/passkeys/reset' : '/api/admin/users/mesa.uno';
      await page.route(`${ORIGIN}/**`, async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname === target && request.method() !== 'GET') {
          arrived.release(); await released.promise;
          return route.fulfill({ status, json: status === 200 ? { ok: true } : { error: 'Synthetic abandoned failure' } });
        }
        if (url.pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [{ id: 'synthetic-key' }] } });
        if (url.pathname === '/api/admin/users' && request.method() === 'GET') return route.fulfill({ json: { items: [...ITEMS,
          { usuario: 'mesa.dos', rol: 'admisionista', activo: true, email: 'second@example.com' }] } });
        return fulfillFiles(route);
      });
      try {
        await page.goto(ORIGIN + '/admin/users?user=mesa.uno');
        await page.locator('#usr-form').waitFor();
        await page.evaluate(() => { window.__oldForm = document.querySelector('#usr-form'); window.__notices = []; window.toast = text => window.__notices.push(text); });
        if (action === 'reset') await page.locator('#usr-keys-quitar').click();
        else if (action === 'save') await page.locator('button[type="submit"][form="usr-form"]').click();
        else { page.once('dialog', dialog => dialog.accept()); await page.locator('#usr-desactivar').click(); }
        await arrived.promise;
        const expectedUser = lifecycle === 'route' ? 'mesa.dos' : 'mesa.uno';
        const expectedEmail = lifecycle === 'route' ? 'second@example.com' : 'mesa@example.com';
        await page.evaluate(lifecycle => {
          if (lifecycle === 'route') {
            history.pushState({}, '', '/admin/users?user=mesa.dos'); dispatchEvent(new PopStateEvent('popstate'));
          } else {
            dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
            dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
          }
        }, lifecycle);
        await page.waitForFunction(email => document.querySelector('#usr-form') !== window.__oldForm && document.querySelector('#usr-email')?.value === email, expectedEmail);
        const acknowledgement = page.waitForResponse(response => new URL(response.url()).pathname === target && response.request().method() !== 'GET');
        released.release(); await (await acknowledgement).finished();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.deepEqual(await page.evaluate(() => window.__notices), []);
        assert.equal(new URL(page.url()).searchParams.get('user'), expectedUser);
        assert.equal(await page.locator('#usr-email').inputValue(), expectedEmail);
      } finally { released.release(); }
    });
  });
}

for (const status of [200, 401]) test(`abandoned Users list response cannot replace a fresh cache or redirect (${status})`, async () => {
  await withPage(async page => {
    const arrived = deferred(), released = deferred(); let reads = 0;
    const items = [...ITEMS, { usuario: 'mesa.dos', rol: 'admisionista', activo: true, email: 'second@example.com' }];
    await page.route(`${ORIGIN}/**`, async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/admin/users') {
        if (++reads === 1) { arrived.release(); await released.promise; return route.fulfill({ status, json: status === 401 ? { error: 'no_autenticado' } : { items } }); }
        return route.fulfill({ json: { items: items.map(item => item.usuario === 'mesa.uno' ? { ...item, email: 'fresh@example.com' } : item) } });
      }
      if (url.pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [] } });
      return fulfillFiles(route);
    });
    try {
      await page.goto(ORIGIN + '/admin/users?user=mesa.uno'); await arrived.promise;
      await page.evaluate(() => { history.pushState({}, '', '/admin/users?user=mesa.dos'); dispatchEvent(new PopStateEvent('popstate')); });
      await page.waitForFunction(() => document.querySelector('#usr-email')?.value === 'second@example.com');
      const old = page.waitForResponse(response => new URL(response.url()).pathname === '/api/admin/users');
      released.release(); await (await old).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(new URL(page.url()).searchParams.get('user'), 'mesa.dos');
      await page.evaluate(() => { history.pushState({}, '', '/admin/users?user=mesa.uno'); dispatchEvent(new PopStateEvent('popstate')); });
      await page.waitForFunction(() => document.querySelector('#usr-email')?.value === 'fresh@example.com');
      assert.equal(reads, 2, 'the fresh owner cache remains authoritative');
    } finally { released.release(); }
  });
});

test('returning to the Users list sorts once per click, Enter or Space and ignores retired filters', async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      if (pathname === '/api/admin/users/mesa.uno' && request.method() === 'PUT') return route.fulfill({ json: { ok: true } });
      if (pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [] } });
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + '/admin/users');
    await page.locator('#usr-tbody tr').first().waitFor();
    await page.evaluate(() => { window.__oldFilter = document.querySelector('#usr-q'); });
    await page.locator('a[data-user="mesa.uno"]').click();
    await page.locator('button[type="submit"][form="usr-form"]').click();
    const header = page.locator('th[data-sort="usuario"]');
    await header.waitFor();
    const initial = await header.getAttribute('aria-sort');
    await header.click();
    assert.notEqual(await header.getAttribute('aria-sort'), initial);
    await header.press('Enter');
    assert.equal(await header.getAttribute('aria-sort'), initial);
    await header.press('Space');
    assert.notEqual(await header.getAttribute('aria-sort'), initial);
    await page.evaluate(() => { window.__oldFilter.value = 'retired-filter'; window.__oldFilter.dispatchEvent(new Event('input', { bubbles: true })); });
    assert.equal(await page.locator('#usr-q').inputValue(), '');
    assert.equal(await page.locator('#usr-tbody tr').count(), ITEMS.length);
    for (let cycle = 0; cycle < 2; cycle++) {
      await page.locator('a[data-user="mesa.uno"]').click();
      await page.locator('button[type="submit"][form="usr-form"]').click(); await header.waitFor();
      const before = await header.getAttribute('aria-sort');
      await header.click(); assert.notEqual(await header.getAttribute('aria-sort'), before);
      await header.press('Enter'); assert.equal(await header.getAttribute('aria-sort'), before);
    }
  });
});

for (const retired of [false, true]) test(`Users confirmed session expiry respects its owner (${retired ? 'retired' : 'current'})`, async () => {
  await withPage(async page => {
    const arrived = deferred(), released = deferred(); let reads = 0;
    await page.route(`${ORIGIN}/**`, async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/admin/users') {
        if (++reads === 1) return route.fulfill({ status: 401, json: { error: 'no_autenticado' } });
        return route.fulfill({ json: { items: [...ITEMS, { usuario: 'mesa.dos', rol: 'admisionista', activo: true, email: 'second@example.com' }] } });
      }
      if (url.pathname === '/api/admin/whoami') {
        arrived.release(); await released.promise;
        return route.fulfill({ status: 401, json: { error: 'no_autenticado' } });
      }
      if (url.pathname === '/admin/login') return route.fulfill({ contentType: 'text/html', body: '<p>Login</p>' });
      if (url.pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [] } });
      return fulfillFiles(route);
    });
    try {
      await page.goto(ORIGIN + '/admin/users?user=mesa.uno'); await arrived.promise;
      if (retired) await replaceUser(page);
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/admin/whoami');
      released.release(); await (await response).finished();
      if (retired) { await drainFrame(page); assert.equal(new URL(page.url()).searchParams.get('user'), 'mesa.dos'); }
      else {
        await page.waitForURL('**/admin/login?next=*');
        assert.equal(new URL(page.url()).searchParams.get('next'), '/admin/users?user=mesa.uno');
      }
    } finally { released.release(); }
  });
});

for (const retired of [false, true]) test(`Users 2FA repair after body parsing respects its owner (${retired ? 'retired' : 'current'})`, async () => {
  await withPage(async page => {
    await holdBody(page, '/api/admin/users', 'GET', true);
    let reads = 0;
    await page.route(`${ORIGIN}/**`, route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/admin/users') return route.fulfill(++reads === 1
        ? { status: 403, json: { error: '2fa_requerido' } }
        : { json: { items: [...ITEMS, { usuario: 'mesa.dos', rol: 'admisionista', activo: true, email: 'second@example.com' }] } });
      if (url.pathname === '/admin/account') return route.fulfill({ contentType: 'text/html', body: '<p>Account</p>' });
      if (url.pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [] } });
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + '/admin/users?user=mesa.uno');
    await page.waitForFunction(() => window.__bodyWaiting);
    if (retired) await replaceUser(page);
    await page.evaluate(() => window.__releaseBody());
    if (retired) {
      await page.waitForFunction(() => window.__bodySettled); await drainFrame(page);
      assert.equal(new URL(page.url()).searchParams.get('user'), 'mesa.dos');
    } else await page.waitForURL('**/admin/account?setup=2fa');
  });
});

for (const status of [200, 500]) for (const action of ['reset', 'save', 'deactivate', 'invite']) test(`retired Users ${action} JSON continuation cannot affect its replacement (${status})`, async () => {
  await withPage(async page => {
    const target = action === 'reset' ? '/api/admin/passkeys/reset' : action === 'invite' ? '/api/admin/users' : '/api/admin/users/mesa.uno';
    const method = action === 'reset' || action === 'invite' ? 'POST' : action === 'save' ? 'PUT' : 'DELETE';
    await holdBody(page, target, method);
    await page.route(`${ORIGIN}/**`, route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === target && request.method() === method) return route.fulfill(status === 500
        ? { status, contentType: 'application/json', body: '{' }
        : { json: action === 'invite' ? { ok: true, item: { usuario: 'synthetic.doctor' }, email_enviado: false, password: 'fixture-only' } : { ok: true } });
      if (url.pathname === '/api/admin/users') return route.fulfill({ json: { items: [...ITEMS, { usuario: 'mesa.dos', rol: 'admisionista', activo: true, email: 'second@example.com' }] } });
      if (url.pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [{ id: 'synthetic-key' }] } });
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + (action === 'invite' ? '/admin/users?new=1' : '/admin/users?user=mesa.uno'));
    await page.locator('#usr-form').waitFor();
    await page.evaluate(() => { window.__notices = []; window.toast = text => window.__notices.push(text); });
    if (action === 'invite') {
      await page.locator('#usr-usuario').fill('synthetic.doctor'); await page.locator('#usr-email').fill('synthetic@example.com');
      await page.locator('#usr-invite').check();
    }
    if (action === 'reset') await page.locator('#usr-keys-quitar').click();
    else if (action === 'deactivate') { page.once('dialog', dialog => dialog.accept()); await page.locator('#usr-desactivar').click(); }
    else await page.locator('button[type="submit"][form="usr-form"]').click();
    await page.waitForFunction(() => window.__bodyWaiting); await replaceUser(page);
    await page.evaluate(() => window.__releaseBody()); await page.waitForFunction(() => window.__bodySettled); await drainFrame(page);
    assert.deepEqual(await page.evaluate(() => window.__notices), []);
    assert.equal(new URL(page.url()).searchParams.get('user'), 'mesa.dos');
    assert.equal(await page.locator('#usr-pass').inputValue(), '');
    assert.equal(await page.locator('#usr-pass').getAttribute('type'), 'password');
    assert.equal(await page.locator('button[type="submit"][form="usr-form"]').isEnabled(), true);
    assert.equal(await page.locator('#usr-keys-quitar').isEnabled(), true);
  });
});

for (const status of [200, 500]) for (const action of ['save', 'deactivate', 'invite']) test(`retired Users ${action} refresh cannot publish old cache, credentials or navigation (${status})`, async () => {
  await withPage(async page => {
    const arrived = deferred(), released = deferred(); let reads = 0;
    const items = [...ITEMS, { usuario: 'mesa.dos', rol: 'admisionista', activo: true, email: 'second@example.com' }];
    await page.route(`${ORIGIN}/**`, async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === '/api/admin/users' && request.method() === 'GET') {
        if (++reads === 2) { arrived.release(); await released.promise; return route.fulfill({ status, json: { items } }); }
        return route.fulfill({ json: { items: items.map(item => item.usuario === 'mesa.uno' && reads > 2 ? { ...item, email: 'fresh@example.com' } : item) } });
      }
      if (url.pathname.startsWith('/api/admin/users') && request.method() !== 'GET') return route.fulfill({ json: action === 'invite'
        ? { ok: true, item: { usuario: 'synthetic.doctor' }, email_enviado: false, password: 'fixture-only' } : { ok: true } });
      if (url.pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [] } });
      return fulfillFiles(route);
    });
    try {
      await page.goto(ORIGIN + (action === 'invite' ? '/admin/users?new=1' : '/admin/users?user=mesa.uno'));
      await page.locator('#usr-form').waitFor();
      if (action === 'invite') {
        await page.locator('#usr-usuario').fill('synthetic.doctor'); await page.locator('#usr-email').fill('synthetic@example.com');
        await page.locator('#usr-invite').check();
      }
      if (action === 'deactivate') { page.once('dialog', dialog => dialog.accept()); await page.locator('#usr-desactivar').click(); }
      else await page.locator('button[type="submit"][form="usr-form"]').click();
      await arrived.promise; await replaceUser(page);
      await page.evaluate(() => { window.__notices = []; window.toast = text => window.__notices.push(text); });
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/admin/users');
      released.release(); await (await response).finished(); await drainFrame(page);
      assert.deepEqual(await page.evaluate(() => window.__notices), []);
      assert.equal(new URL(page.url()).searchParams.get('user'), 'mesa.dos');
      assert.equal(await page.locator('#usr-pass').inputValue(), '');
      assert.equal(await page.locator('button[type="submit"][form="usr-form"]').isEnabled(), true);
      await page.evaluate(() => { history.pushState({}, '', '/admin/users?user=mesa.uno'); dispatchEvent(new PopStateEvent('popstate')); });
      await page.waitForFunction(() => document.querySelector('#usr-email')?.value === 'fresh@example.com');
      assert.equal(reads, 3);
    } finally { released.release(); }
  });
});

test('an abandoned invitation acknowledgement cannot reveal its generated password in another user form', async () => {
  await withPage(async page => {
    const arrived = deferred(), released = deferred();
    await page.route(`${ORIGIN}/**`, async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname === '/api/admin/users' && request.method() === 'POST') {
        arrived.release(); await released.promise;
        return route.fulfill({ json: { ok: true, item: { usuario: 'synthetic.doctor' }, email_enviado: false, password: 'fixture-only' } });
      }
      if (url.pathname === '/api/admin/passkeys') return route.fulfill({ json: { passkeys: [] } });
      return fulfillFiles(route);
    });
    try {
      await page.goto(ORIGIN + '/admin/users?new=1');
      await page.locator('#usr-usuario').fill('synthetic.doctor');
      await page.locator('#usr-email').fill('synthetic@example.com');
      await page.locator('#usr-invite').check();
      await page.locator('button[type="submit"][form="usr-form"]').click(); await arrived.promise;
      await page.evaluate(() => { history.pushState({}, '', '/admin/users?user=mesa.uno'); dispatchEvent(new PopStateEvent('popstate')); });
      await page.waitForFunction(() => document.querySelector('#usr-email')?.value === 'mesa@example.com');
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/admin/users' && r.request().method() === 'POST');
      released.release(); await (await response).finished();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.locator('#usr-pass').inputValue(), '');
      assert.equal(await page.locator('#usr-pass').getAttribute('type'), 'password');
      assert.equal(await page.locator('#usr-pass').evaluate(node => node.readOnly), false);
    } finally { released.release(); }
  });
});

test('clipboard completion is silent after leaving Users, and a fresh copy still notifies', async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => new URL(route.request().url()).pathname === '/api/admin/passkeys'
      ? route.fulfill({ json: { passkeys: [] } }) : fulfillFiles(route));
    await page.goto(ORIGIN + '/admin/users?user=mesa.uno'); await page.locator('#usr-form').waitFor();
    await page.evaluate(() => {
      window.__notices = []; window.toast = text => window.__notices.push(text);
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => new Promise(resolve => { window.__copyDone = resolve; }) } });
      document.querySelector('#sec-usuarios').insertAdjacentHTML('beforeend', '<button data-copy="synthetic">Copy fixture</button>');
    });
    await page.locator('[data-copy]').click();
    await page.evaluate(() => { history.pushState({}, '', '/admin/users?new=1'); dispatchEvent(new PopStateEvent('popstate')); });
    await page.locator('#usr-usuario').waitFor();
    await page.evaluate(async () => { window.__copyDone(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    assert.deepEqual(await page.evaluate(() => window.__notices), []);
    await page.evaluate(() => document.querySelector('#sec-usuarios').insertAdjacentHTML('beforeend', '<button data-copy="synthetic">Copy fixture</button>'));
    await page.locator('[data-copy]').click(); await page.evaluate(() => window.__copyDone());
    await page.waitForFunction(() => window.__notices.length === 1);
    assert.equal(await page.evaluate(() => window.__notices[0]), 'Copiado.');
  });
});

for (const delivered of [true, false]) {
  test(`legacy doctor invitation sends entered email and login, without clipboard or password (delivery=${delivered})`, async () => {
    await withPage(async (page) => {
      const requests = [];
      let invited = false;
      await page.route(`${ORIGIN}/**`, async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === '/api/admin/users/actions/invite') {
          requests.push(route.request().postDataJSON());
          invited = true;
          return route.fulfill({ json: { ok: true, resultados: [{ ok: true, usuario: 'chosen.doctor',
            email_enviado: delivered, email: 'doctor@example.test',
            ...(delivered ? {} : { enlace: 'https://users-native.test/admin/reset?token=fixture' }) }] } });
        }
        if (url.pathname === '/api/admin/users' && invited) {
          return route.fulfill({ json: { items: [...ITEMS, { usuario: 'chosen.doctor', rol: 'medico', medico_id: DOCTOR.id,
            activo: true, cuenta_estado: 'invitado', email: 'doctor@example.test' }] } });
        }
        if (url.pathname === '/admin/doctors') {
          return route.fulfill({ contentType: 'text/html', body: pageHtml({ screen: 'medicos' }) });
        }
        return fulfillFiles(route);
      });
      await page.goto(ORIGIN + '/admin/doctors');
      await page.evaluate(async doctor => {
        window.clipboardWrites = 0;
        Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
          write: () => { window.clipboardWrites++; return Promise.resolve(); },
          writeText: () => { window.clipboardWrites++; return Promise.resolve(); }
        } });
        const Users = await import(window.SANA_STATIC['platform/admin-users.mjs']);
        await Users.mountDoctorUser(document.querySelector('#med-user-panel'), doctor.id, doctor).ready;
      }, { ...DOCTOR, email: 'doctor@example.test' });
      await page.locator('#doctor-user-new').fill('chosen.doctor');
      // One email per doctor: the tab shows the record's, no second field.
      assert.equal(await page.locator('#doctor-user-email').textContent(), 'doctor@example.test');
      assert.equal(await page.locator('#doctor-user-create-form input[type="email"]').count(), 0);
      await page.locator('#doctor-user-mode').selectOption('invite');
      assert.equal(await page.locator('#doctor-user-create-form button').textContent(), 'Enviar invitación');
      await page.locator('#doctor-user-create-form button').click();
      await page.locator('#doctor-user-outcome .doctor-user-outcome').waitFor();
      assert.deepEqual(requests, [{ medico_ids: [DOCTOR.id], usuario: 'chosen.doctor', email: 'doctor@example.test' }]);
      // The invitation gave the doctor its account: it is shown inline, in its state.
      assert.match(await page.locator('.doctor-user-summary').textContent(), /chosen\.doctor[\s\S]*Invitado/);
      assert.match(await page.locator('#doctor-user-outcome').textContent(), delivered
        ? /Invitación enviada a doctor@example\.test/ : /No se pudo enviar el email a doctor@example\.test/);
      assert.equal(await page.locator('#doctor-user-outcome .doctor-user-link-input').count(), delivered ? 0 : 1);
      assert.equal(await page.locator('#doctor-user-password').count(), 0);
      assert.equal(await page.evaluate(() => window.clipboardWrites), 0);
    });
  });
}

// Médico = usuario (responsable del producto, 2026-10-19): every doctor already has its account.
// The Usuario tab shows it inline and invites it without asking for any field.
function doctorAccount(overrides = {}) {
  return { usuario: 'diego.rivera', rol: 'medico', medico_id: DOCTOR.id, activo: false,
    cuenta_estado: 'sin_invitar', email: '', ultimo_acceso: null, ...overrides };
}

async function mountAccount(page, { account, onAction, onPut }) {
  let current = account;
  await page.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/admin/users/actions/')) {
      const action = url.pathname.split('/').pop();
      const reply = await onAction(action, route.request().postDataJSON());
      if (reply.account) current = reply.account;
      return route.fulfill({ json: { ok: true, resultados: [{ ok: true, medico_id: DOCTOR.id, ...reply.result }] } });
    }
    if (onPut && route.request().method() === 'PUT' && url.pathname === '/api/admin/users/' + encodeURIComponent(current.usuario)) {
      const reply = await onPut(route.request().postDataJSON());
      if (reply.account) current = reply.account;
      return route.fulfill({ status: reply.status || 200, json: reply.json || { ok: true } });
    }
    if (url.pathname === '/api/admin/users') return route.fulfill({ json: { items: [...ITEMS, current] } });
    if (url.pathname === '/admin/doctors') return route.fulfill({ contentType: 'text/html', body: pageHtml({ screen: 'medicos' }) });
    return fulfillFiles(route);
  });
  await page.goto(ORIGIN + '/admin/doctors');
  await page.evaluate(async doctor => {
    window.clipboardTexts = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: text => { window.clipboardTexts.push(text); return Promise.resolve(); }
    } });
    window.ClipboardItem = undefined;
    const Users = await import(window.SANA_STATIC['platform/admin-users.mjs']);
    await Users.mountDoctorUser(document.querySelector('#med-user-panel'), doctor.id, doctor).ready;
  }, DOCTOR);
}

test('a never-invited doctor account is shown inline with Invitar and Usuario y contraseña, never a link control', async () => {
  await withPage(async (page) => {
    await mountAccount(page, { account: doctorAccount(), onAction: async () => ({ result: {} }) });
    const summary = await page.locator('.doctor-user-summary').textContent();
    assert.match(summary, /diego\.rivera/);
    assert.match(summary, /Sin invitar/);
    assert.equal(await page.locator('#doctor-user-invite-form button').textContent(), 'Invitar');
    assert.equal(await page.locator('#doctor-user-password-access').textContent(), 'Usuario y contraseña');
    assert.equal(await page.locator('#doctor-user-repair').count(), 0);
    assert.equal(await page.locator('#doctor-user-link-form').count(), 0);
    assert.equal(await page.locator('#doctor-user-new').count(), 0);
    assert.equal(await page.locator('[data-doctor-user-action="reset-password"]').count(), 0);
  });
});

test('Invitar without an email hands back the one-use link and Copiar copies exactly that link', async () => {
  await withPage(async (page) => {
    const calls = [];
    const link = 'https://users-native.test/admin/reset?token=fixture';
    await mountAccount(page, { account: doctorAccount(), onAction: async (action, body) => {
      calls.push([action, body]);
      return { result: { usuario: 'diego.rivera', email_enviado: false, enlace: link, cuenta_estado: 'invitado' },
        account: doctorAccount({ activo: true, cuenta_estado: 'invitado' }) };
    } });
    await page.locator('#doctor-user-invite-form button').click();
    await page.locator('#doctor-user-outcome .doctor-user-link-input').waitFor();
    assert.deepEqual(calls, [['invite', { medico_ids: [DOCTOR.id] }]]);
    assert.equal(await page.locator('#doctor-user-outcome .doctor-user-link-input').inputValue(), link);
    // The link alone is not enough without an email: the username goes with it.
    assert.match(await page.locator('#doctor-user-outcome').textContent(), /Usuario\s*diego\.rivera/);
    assert.match(await page.locator('.doctor-user-summary').textContent(), /Invitado/);
    assert.equal(await page.locator('#doctor-user-password-access').count(), 0, 'a password is only for a never-used account');
    await page.locator('#doctor-user-outcome .doctor-user-link-copy').click();
    await page.waitForFunction(() => window.clipboardTexts.length === 1);
    assert.deepEqual(await page.evaluate(() => window.clipboardTexts), [link]);
    assert.equal(await page.locator('#doctor-user-outcome .doctor-user-copy-status').textContent(), 'Enlace copiado.');
  });
});

test('Invitar with an email sends it and says to whom', async () => {
  await withPage(async (page) => {
    const calls = [];
    await mountAccount(page, { account: doctorAccount({ email: 'diego@example.test' }), onAction: async (action, body) => {
      calls.push([action, body]);
      return { result: { usuario: 'diego.rivera', email_enviado: true, email: 'diego@example.test' },
        account: doctorAccount({ activo: true, cuenta_estado: 'invitado', email: 'diego@example.test' }) };
    } });
    // The address it goes to is the doctor's one email, read-only here.
    assert.equal(await page.locator('#doctor-user-invite-email').textContent(), 'diego@example.test');
    await page.locator('#doctor-user-invite-form button').click();
    await page.locator('#doctor-user-outcome .doctor-user-outcome').waitFor();
    assert.deepEqual(calls, [['invite', { medico_ids: [DOCTOR.id] }]]);
    assert.equal(await page.locator('#doctor-user-outcome').textContent(), 'Invitación enviada a diego@example.test.');
    assert.equal(await page.locator('#doctor-user-outcome .doctor-user-link-input').count(), 0);
    assert.equal(await page.locator('#doctor-user-invite-form button').textContent(), 'Reenviar invitación');
  });
});

test('Usuario y contraseña on a never-used account reveals and copies the access once', async () => {
  await withPage(async (page) => {
    const calls = [];
    await mountAccount(page, { account: doctorAccount(), onAction: async (action, body) => {
      calls.push([action, body]);
      return { result: { usuario: 'diego.rivera', password: 'fixture-pass' } };
    } });
    await page.locator('#doctor-user-password-access').click();
    await page.locator('#doctor-user-password').waitFor();
    assert.deepEqual(calls, [['create', { medico_ids: [DOCTOR.id] }]]);
    assert.equal(await page.locator('#doctor-user-password').inputValue(), 'fixture-pass');
    await page.waitForFunction(() => window.clipboardTexts.length === 1);
    assert.match(await page.evaluate(() => window.clipboardTexts[0]), /diego\.rivera[\s\S]*fixture-pass/);
  });
});

test('an account in use offers reset, 2FA reset and deactivate, not a password', async () => {
  await withPage(async (page) => {
    await mountAccount(page, { account: doctorAccount({ activo: true, cuenta_estado: 'perfil_completo', email: 'diego@example.test' }),
      onAction: async () => ({ result: {} }) });
    assert.match(await page.locator('.doctor-user-summary').textContent(), /Perfil completo/);
    assert.equal(await page.locator('#doctor-user-invite-email').textContent(), 'diego@example.test');
    assert.equal(await page.locator('#doctor-user-password-access').count(), 0);
    for (const action of ['reset-password', 'reset-2fa', 'disable']) {
      assert.equal(await page.locator(`[data-doctor-user-action="${action}"]`).count(), 1, action);
    }
  });
});

test('an account that already came in has no «Reenviar invitación»; a deactivated one keeps «Invitar»', async () => {
  // It has its access: an invitation would only send a second way in.
  for (const cuenta_estado of ['ingreso', 'perfil_completo']) {
    await withPage(async (page) => {
      await mountAccount(page, { account: doctorAccount({ activo: true, cuenta_estado, email: 'diego@example.test' }),
        onAction: async () => ({ result: {} }) });
      assert.equal(await page.locator('#doctor-user-invite-form').count(), 0, cuenta_estado);
      assert.doesNotMatch(await page.locator('#med-user-panel').textContent(), /Reenviar invitación|Invitar/, cuenta_estado);
    });
  }
  // Inviting is how a deactivated account comes back (invite_account sets activo).
  await withPage(async (page) => {
    await mountAccount(page, { account: doctorAccount({ activo: false, cuenta_estado: 'perfil_completo' }),
      onAction: async () => ({ result: {} }) });
    assert.equal(await page.locator('#doctor-user-invite-form button').textContent(), 'Invitar');
  });
});

test('an account that already came in without an email does not ask for the invitation\'s email', async () => {
  // The hint says where the invitation goes; a doctor who logged in needs none.
  for (const cuenta_estado of ['ingreso', 'perfil_completo']) {
    await withPage(async (page) => {
      await mountAccount(page, { account: doctorAccount({ activo: true, cuenta_estado, email: '' }),
        onAction: async () => ({ result: {} }) });
      assert.equal(await page.locator('#doctor-user-invite-email').count(), 0, cuenta_estado);
      assert.doesNotMatch(await page.locator('#med-user-panel').textContent(), /para que la invitación le llegue/, cuenta_estado);
    });
  }
  // Still invited, or deactivated: the invitation is the next step, the hint stays.
  for (const account of [doctorAccount({ activo: true, cuenta_estado: 'invitado' }),
    doctorAccount({ activo: false, cuenta_estado: 'perfil_completo' })]) {
    await withPage(async (page) => {
      await mountAccount(page, { account, onAction: async () => ({ result: {} }) });
      assert.match(await page.locator('#doctor-user-invite-email').textContent(), /para que la invitación le llegue/, account.cuenta_estado);
    });
  }
});

test('«Cambiar la dirección del calendario» asks in place, like the doctor\'s own, before it changes anything', async () => {
  await withPage(async (page) => {
    const calls = [];
    await mountAccount(page, { account: doctorAccount({ activo: true, cuenta_estado: 'perfil_completo' }),
      onAction: async (action, body) => { calls.push([action, body]); return { result: {} }; } });
    await page.evaluate(() => { window.__notices = []; window.toast = text => window.__notices.push(text); });
    const rotate = page.locator('.doctor-user-actions [data-doctor-user-cal-rotate]');
    assert.equal(await rotate.textContent(), 'Cambiar la dirección del calendario');
    await rotate.click();
    const confirm = page.locator('.doctor-user-actions .doctor-user-cal-rotate');
    assert.equal(await confirm.locator('p').textContent(),
      `La dirección actual deja de funcionar y ${DOCTOR.nombre} va a tener que agregar la nueva desde Mis turnos.`);
    assert.equal(await confirm.locator('[data-doctor-user-cal-rotate-yes]').textContent(), 'Cambiar la dirección');
    assert.match(await confirm.locator('[data-doctor-user-cal-rotate-yes]').getAttribute('class'), /\bbtn-no\b/);
    assert.equal(await confirm.locator('[data-doctor-user-cal-rotate-no]').textContent(), 'No cambiarla');
    assert.match(await confirm.locator('[data-doctor-user-cal-rotate-no]').getAttribute('class'), /\bbtn-quiet\b/);
    assert.deepEqual(calls, [], 'asking changes nothing');
    // «No cambiarla» puts the button back, nothing sent.
    await confirm.locator('[data-doctor-user-cal-rotate-no]').click();
    assert.equal(await page.locator('.doctor-user-actions [data-doctor-user-cal-rotate]').count(), 1);
    assert.equal(await confirm.locator('p').count(), 0);
    assert.deepEqual(calls, []);
    await page.locator('.doctor-user-actions [data-doctor-user-cal-rotate]').click();
    await page.locator('[data-doctor-user-cal-rotate-yes]').click();
    await page.waitForFunction(() => window.__notices.length === 1);
    assert.deepEqual(calls, [['reset-calendar', { medico_ids: [DOCTOR.id] }]]);
    assert.deepEqual(await page.evaluate(() => window.__notices), ['Listo: la dirección anterior ya no funciona.']);
    await page.locator('.doctor-user-actions [data-doctor-user-cal-rotate]').waitFor();
  });
});

test('an invited account that never came in offers only what fits its state, «Desactivar» apart', async () => {
  // Opus UI review 2026-11-03: «Invitado» showed «Última visita —» and
  // «Restablecer contraseña / 2FA / Nueva dirección del calendario» for an
  // account with no password, no second factor and no calendar handed out.
  await withPage(async (page) => {
    await mountAccount(page, { account: doctorAccount({ activo: true, cuenta_estado: 'invitado', email: 'diego@example.test' }),
      onAction: async () => ({ result: {} }) });
    assert.match(await page.locator('.doctor-user-summary').textContent(), /Invitado/);
    assert.doesNotMatch(await page.locator('.doctor-user-summary').textContent(), /Última visita|—/);
    for (const action of ['reset-password', 'reset-2fa']) {
      assert.equal(await page.locator(`[data-doctor-user-action="${action}"]`).count(), 0, action);
    }
    assert.equal(await page.locator('[data-doctor-user-cal-rotate]').count(), 0, 'no calendar address handed out yet');
    assert.equal(await page.locator('.doctor-user-danger [data-doctor-user-action="disable"]').count(), 1);
    assert.equal(await page.locator('.doctor-user-actions [data-doctor-user-action="disable"]').count(), 0);
  });
});

test('«Rol» is one «Administrador» checkbox that saves by itself and finds the account again by the doctor', async () => {
  await withPage(async (page) => {
    const puts = [];
    await mountAccount(page, { account: doctorAccount({ activo: true, cuenta_estado: 'perfil_completo' }),
      onAction: async () => ({ result: {} }),
      onPut: async (body) => { puts.push(body); return { json: { ok: true } }; } });
    await page.evaluate(() => { window.__notices = []; window.toast = text => window.__notices.push(text); });
    const box = page.locator('.doctor-user-summary #doctor-user-admin');
    assert.equal(await box.getAttribute('type'), 'checkbox');
    assert.equal(await box.isChecked(), false);
    assert.equal(await page.locator('.doctor-user-summary label[for="doctor-user-admin"]').first().textContent(), 'Rol');
    assert.match(await page.locator('.doctor-user-summary .cnt-check').textContent(), /^Administrador$/);
    assert.equal(await page.locator('[data-doctor-user-role]').count(), 0, 'no button that flips its own label');
    await box.check();
    await page.waitForFunction(() => !document.querySelector('#doctor-user-admin').disabled);
    assert.deepEqual(puts, [{ rol: 'admin' }]);
    assert.equal(await box.isChecked(), true);
    await box.uncheck();
    await page.waitForFunction(() => !document.querySelector('#doctor-user-admin').disabled);
    assert.deepEqual(puts, [{ rol: 'admin' }, { rol: 'medico' }]);
    assert.deepEqual(await page.evaluate(() => window.__notices), [], 'the ticked box is the confirmation: no toast');
    assert.equal(await page.locator('#doctor-user-admin-msg').isHidden(), true);
  });
  // A refusal unticks the box and says why under the row.
  await withPage(async (page) => {
    await mountAccount(page, { account: doctorAccount({ activo: true, cuenta_estado: 'perfil_completo' }),
      onAction: async () => ({ result: {} }),
      onPut: async () => ({ status: 409, json: { ok: false, error: 'No se pudo cambiar el rol.' } }) });
    const box = page.locator('#doctor-user-admin');
    await box.click();
    await page.locator('#doctor-user-admin-msg').waitFor();
    assert.equal(await box.isChecked(), false);
    assert.equal(await box.isDisabled(), false);
    assert.equal(await page.locator('#doctor-user-admin-msg').textContent(), 'No se pudo cambiar el rol.');
  });
  await withPage(async (page) => {
    await mountAccount(page, { account: doctorAccount({ rol: 'admin', activo: true, cuenta_estado: 'perfil_completo' }),
      onAction: async () => ({ result: {} }) });
    assert.equal(await page.locator('#doctor-user-admin').isChecked(), true);
    assert.equal(await page.locator('#doctor-user-create-form').count(), 0);
  });
});

test('accountStateLabel names every state and an inactive used account', async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, route => new URL(route.request().url()).pathname === '/admin/doctors'
      ? route.fulfill({ contentType: 'text/html', body: pageHtml({ screen: 'medicos' }) }) : fulfillFiles(route));
    await page.goto(ORIGIN + '/admin/doctors');
    const labels = await page.evaluate(async () => {
      const Users = await import(window.SANA_STATIC['platform/admin-users.mjs']);
      return [
        { cuenta_estado: 'sin_invitar', activo: false }, { cuenta_estado: 'invitado', activo: true },
        { cuenta_estado: 'ingreso', activo: true }, { cuenta_estado: 'perfil_completo', activo: true },
        { cuenta_estado: 'perfil_completo', activo: false },
      ].map(Users.accountStateLabel);
    });
    assert.deepEqual(labels, ['Sin invitar', 'Invitado', 'Ingresó', 'Perfil completo', 'Inactiva']);
  });
});

test('Users invitation choice hides the password and submits a staff invitation; doctors are created in Médicos', async () => {
  await withPage(async page => {
    let submitted;
    await page.route(`${ORIGIN}/**`, route => {
      if (new URL(route.request().url()).pathname === '/api/admin/users' && route.request().method() === 'POST') {
        submitted = route.request().postDataJSON();
        return route.fulfill({ json: { ok: true, item: { usuario: submitted.usuario }, email_enviado: true } });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + '/admin/users?new=1');
    // Médico = usuario: a doctor is never created twice. The new-user form
    // offers no médico role and no doctor picker, only the way to Médicos.
    assert.equal(await page.locator('input[name="usr-rol"][value="medico"]').count(), 0);
    assert.equal(await page.locator('#usr-medico').count(), 0);
    assert.equal(await page.locator('.usr-doctors-elsewhere a').getAttribute('href'), '/admin/doctors');
    await page.locator('#usr-usuario').fill('mesa.nueva');
    await page.locator('#usr-email').fill('mesa@example.test');
    await page.locator('#usr-invite').check();
    assert.equal(await page.locator('#usr-pass').isVisible(), false);
    const submit = page.locator('button[type="submit"][form="usr-form"]');
    assert.equal(await submit.isVisible(), true);
    assert.equal(await submit.isEnabled(), true);
    assert.equal(await submit.evaluate(button => button.form === document.getElementById('usr-form')), true);
    await submit.click();
    await page.waitForURL(ORIGIN + '/admin/users');
    assert.equal(submitted.access_mode, 'invite');
    assert.equal(submitted.email, 'mesa@example.test');
    assert.equal(submitted.rol, 'admisionista');
    assert.equal(submitted.medico_id, '');
    assert.equal('password' in submitted, false);
  });
});

test('Users list leaves doctor accounts to Médicos and keeps an unlinked médico account for repair', async () => {
  await withPage(async page => {
    const items = [...ITEMS,
      { usuario: 'dora.villaverde', rol: 'medico', medico_id: 'dora-villaverde', activo: true, cuenta_estado: 'ingreso' },
      { usuario: 'medico.suelto', rol: 'medico', medico_id: null, activo: true }];
    await page.route(`${ORIGIN}/**`, route => new URL(route.request().url()).pathname === '/api/admin/users'
      ? route.fulfill({ json: { items } }) : fulfillFiles(route));
    await page.goto(ORIGIN + '/admin/users');
    await page.locator('#usr-tbody tr').first().waitFor();
    const names = await page.locator('#usr-tbody td[data-col="usuario"]').allTextContents();
    assert.equal(names.some(name => name.includes('dora.villaverde')), false);
    assert.equal(names.some(name => name.includes('medico.suelto')), true);
    assert.equal(names.some(name => name.includes('mesa.uno')), true);
  });
});

// Opus UI review 2026-11-03: what is missing or the same on every row is not
// written — no «—» email or visit, no «Activo» on each active account; only
// «Inactivo» stands out.
test('Users list writes no dash for a missing email or visit and marks only the inactive account', async () => {
  await withPage(async page => {
    const items = [...ITEMS, { usuario: 'mesa.vieja', rol: 'admisionista', activo: false }];
    await page.route(`${ORIGIN}/**`, route => new URL(route.request().url()).pathname === '/api/admin/users'
      ? route.fulfill({ json: { items } }) : fulfillFiles(route));
    await page.goto(ORIGIN + '/admin/users');
    await page.locator('#usr-tbody tr').first().waitFor();
    const row = page.locator('#usr-tbody tr', { hasText: 'mesa.vieja' });
    assert.equal((await row.locator('td[data-col="email"]').textContent()).trim(), '');
    assert.equal((await row.locator('td[data-col="ultimo_acceso"]').textContent()).trim(), '');
    assert.equal((await row.locator('td[data-col="activo"]').textContent()).trim(), 'Inactivo');
    const active = await page.locator('#usr-tbody tr', { hasText: 'mesa.uno' }).locator('td[data-col="activo"]').textContent();
    assert.equal(active.trim(), '');
    assert.doesNotMatch(await page.locator('#usr-tbody').textContent(), /—/);
  });
});

// pinamar-turnos 2026-11-03: the owner row opens — one's own to Cuenta,
// another admin's to a read-only page with no form, field or button.
test("the owner row opens: another admin sees it read-only, the owner goes to Cuenta", async () => {
  await withPage(async page => {
    const items = [{ usuario: "jefa", es_dueno: true, rol: "admin", activo: true, email: "jefa@example.com", tiene_2fa: true }, ...ITEMS.filter(i => !i.es_dueno)];
    await usersWith(page, items);
    await page.locator("#usr-tbody tr").first().waitFor();
    const link = page.locator('#usr-tbody tr', { hasText: "jefa" }).locator('td[data-col="usuario"] a');
    assert.equal(await link.getAttribute("href"), "/admin/users?user=jefa");
    await page.goto(ORIGIN + "/admin/users?user=jefa");
    const view = page.locator("[data-owner-readonly]");
    await view.waitFor();
    assert.match(await view.textContent(), /jefa@example\.com/);
    assert.match(await view.textContent(), /Activa/);
    assert.equal(await page.locator("#sec-usuarios input, #sec-usuarios button, #sec-usuarios form").count(), 0);
    assert.match(await page.locator("#sec-usuarios").textContent(), /Solo esa persona cambia sus datos, desde su pantalla Cuenta/);
    // «Usuarios / name» sits in the title band, like a doctor's card; the body
    // repeats neither, and «Nuevo usuario» leaves the band (ui-expert 15.11).
    assert.equal((await page.locator("#adm-h1").textContent()).replace(/\s+/g, " ").trim(), "Usuarios/jefa");
    assert.equal(await page.locator('#adm-h1 a.adm-crumb[href="/admin/users"]').count(), 1);
    assert.equal(await page.locator("#sec-usuarios .cnt-crumb, #sec-usuarios h1").count(), 0);
    assert.equal(await page.locator('.adm-h1row a[data-users-state="new"]').isVisible(), false);
    await page.locator("#adm-h1 a.adm-crumb").click();
    await page.locator("#usr-tbody tr").first().waitFor();
    assert.equal(await page.locator("#adm-h1").textContent(), "Usuarios");
  });
  await withPage(async page => {
    await usersWith(page, ITEMS);
    await page.locator("#usr-tbody tr").first().waitFor();
    const own = page.locator('#usr-tbody tr', { hasText: "owner" }).locator('td[data-col="usuario"] a');
    assert.match(await own.getAttribute("href"), /^\/admin\/account/);
  });
});

// Progressive disclosure (the product owner, 2026-10-26: «if there's no content, why to
// show all the ui?»). Empty → no data tools; filtered to nothing → the
// filters stay with «Limpiar filtros»; a filter with one value is hidden.
async function usersWith(page, items) {
  await page.route(`${ORIGIN}/**`, route => new URL(route.request().url()).pathname === '/api/admin/users'
    ? route.fulfill({ json: { items } }) : fulfillFiles(route));
  await page.goto(ORIGIN + '/admin/users');
}

test('DISCLOSURE: Users with nobody listed shows the title, «Nuevo usuario» and one empty state — not «ningún usuario coincide»', async () => {
  await withPage(async page => {
    await usersWith(page, [{ usuario: 'dora.villaverde', rol: 'medico', medico_id: 'dora-villaverde', activo: true }]);
    await page.locator('[data-empty-state="empty"]').waitFor();
    const sec = page.locator('#sec-usuarios');
    assert.equal(await sec.getAttribute('data-disclosure'), 'empty');
    assert.match(await page.locator('[data-empty-state="empty"]').innerText(), /Todavía no hay usuarios del panel\.[\s\S]*Médicos/);
    assert.doesNotMatch(await sec.innerText(), /Ningún usuario coincide/);
    for (const sel of ['#usr-q', 'select', '[data-tblcol-gear]', 'th', '[data-clear-filters]']) {
      assert.equal(await sec.locator(sel).count(), 0, sel + ' is a data tool: not rendered without data');
    }
    // The create action sits on the title's own line (one screen header,
    // 2026-11-08), never a copy in the empty list.
    assert.equal(await page.locator('.adm-h1row a.adm-title-action[href="/admin/users?new=1"]').isVisible(), true, 'the primary action stays');
    assert.equal(await sec.locator('a[data-users-state="new"]').count(), 0, 'no second create action inside the list');
  });
});

// A list worth filtering (Filters.FILTER_MIN_ROWS = 10): the owner, the desk
// and nine more desk accounts.
const ELEVEN = ITEMS.concat(Array.from({ length: 9 }, (_, i) => (
  { usuario: 'mesa.' + (i + 2), rol: 'admisionista', activo: true, email: `mesa${i + 2}@example.com` })));

test('DISCLOSURE: the role filter offers only listed roles, a one-value filter hides, and a search that hides everyone offers «Limpiar filtros»', async () => {
  await withPage(async page => {
    await usersWith(page, ELEVEN);
    await page.locator('#usr-tbody tr').first().waitFor();
    const sec = page.locator('#sec-usuarios');
    assert.equal(await sec.getAttribute('data-disclosure'), 'data');
    const roles = await page.locator('#usr-f-rol option').evaluateAll(os => os.map(o => o.value).filter(Boolean).sort());
    assert.deepEqual(roles, ['admin', 'admisionista'], 'only roles someone listed has');
    assert.equal(await page.locator('#usr-f-rol').isVisible(), true);
    assert.equal(await page.locator('#usr-f-estado').isVisible(), false, 'everyone is active: no status choice');
    await page.locator('#usr-q').fill('nadie-se-llama-asi');
    await page.locator('[data-empty-state="no-match"]').waitFor();
    assert.equal(await sec.getAttribute('data-disclosure'), 'no-match');
    assert.equal(await sec.locator('th').count(), 0, 'no table header over the no-match line');
    assert.equal(await page.locator('#usr-q').isVisible(), true, 'the search that emptied the list stays');
    assert.match(await page.locator('[data-empty-state="no-match"]').innerText(), /Ningún usuario coincide con los filtros\./);
    await page.locator('[data-clear-filters]').click();
    await page.locator('#usr-tbody tr').first().waitFor();
    assert.equal(await page.locator('#usr-q').inputValue(), '');
    assert.equal(await page.locator('#usr-tbody tr').count(), ELEVEN.length);
    assert.equal(await sec.getAttribute('data-disclosure'), 'data');
  });
});

test('DISCLOSURE: one role among the listed users hides the role filter; active and inactive show the status filter', async () => {
  await withPage(async page => {
    const admins = Array.from({ length: 9 }, (_, i) => ({ usuario: 'admin.' + i, rol: 'admin', activo: true, email: `a${i}@example.com` }));
    await usersWith(page, [ITEMS[0], ...admins, { usuario: 'admin.dos', rol: 'admin', activo: false, email: 'dos@example.com' }]);
    await page.locator('#usr-tbody tr').first().waitFor();
    assert.equal(await page.locator('#usr-f-rol').isVisible(), false);
    assert.equal(await page.locator('#usr-f-estado').isVisible(), true);
  });
});

// Product owner 05.09 C1, 21.10, 26.10: «fewer than ~10 records — do not draw filters
// at all»; 15.07, 28.07: the default option is the field name, never «Todos».
test('THRESHOLD: under ten users there is no filter even with two roles and two states; the labels are the field names', async () => {
  await withPage(async page => {
    await usersWith(page, [...ITEMS, { usuario: 'admin.dos', rol: 'admin', activo: false, email: 'dos@example.com' }]);
    await page.locator('#usr-tbody tr').first().waitFor();
    assert.equal(await page.locator('#usr-q').isVisible(), true, 'the search is not a filter');
    assert.equal(await page.locator('#usr-f-rol').isVisible(), false);
    assert.equal(await page.locator('#usr-f-estado').isVisible(), false);
    assert.equal(await page.locator('#usr-f-rol option').first().textContent(), 'Rol');
    assert.equal(await page.locator('#usr-f-estado option').first().textContent(), 'Estado');
  });
});

// 2026-11-08 review D14 (the product owner 26.10: «if there's no content, why to show all
// the ui?»): the column chooser and «Descargar» follow the filters' threshold.
test('THRESHOLD: under ten users there is no column chooser and no «Descargar»; the search stays', async () => {
  await withPage(async page => {
    await usersWith(page, ITEMS);
    await page.locator('#usr-tbody tr').first().waitFor();
    const sec = page.locator('#sec-usuarios');
    assert.equal(await page.locator('#usr-q').isVisible(), true, 'the search is not a filter');
    assert.equal(await sec.locator('[data-tblcol-gear]').count(), 0, 'no columns to pick on a short list');
    assert.equal(await sec.locator('[data-tbl-export]').count(), 0, 'nothing to download from a short list');
  });
  await withPage(async page => {
    await usersWith(page, ELEVEN);
    await page.locator('#usr-tbody tr').first().waitFor();
    const sec = page.locator('#sec-usuarios');
    assert.equal(await sec.locator('.cnt-toolbar-right [data-tblcol-gear]').isVisible(), true, 'a long list keeps the column chooser');
    assert.equal(await sec.locator('.cnt-toolbar-right [data-tbl-export]').isVisible(), true, 'and «Descargar»');
  });
});

test("a Users list 401 the session check does not confirm shows the load error, not an empty screen", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => {
      if (new URL(route.request().url()).pathname === "/api/admin/users") {
        return route.fulfill({ status: 401, json: { error: "no_autenticado" } });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/users", { waitUntil: "domcontentloaded" });
    await page.locator("#sec-usuarios p.adm-empty:not([role])").waitFor();
    assert.match(await page.locator("#sec-usuarios").innerText(), /No se pudo cargar/);
  });
});

test("a Users save 401 the session check does not confirm frees the button and says it was not saved", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => {
      const request = route.request();
      if (new URL(request.url()).pathname === "/api/admin/users/mesa.uno" && request.method() === "PUT") {
        return route.fulfill({ status: 401, json: { error: "no_autenticado" } });
      }
      if (new URL(request.url()).pathname === "/api/admin/passkeys") return route.fulfill({ json: { passkeys: [] } });
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/users?user=mesa.uno");
    await page.locator("#usr-form").waitFor();
    const save = page.locator('button[type="submit"][form="usr-form"]');
    await save.click();
    await page.locator("#usr-save-msg").filter({ hasText: /\S/ }).waitFor();
    assert.equal(await save.isDisabled(), false);
  });
});

test("Users waits for its manifest module; delayed load does not paint rows or publish a global", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { holdModule: { requested, release } }));
    const navigation = page.goto(ORIGIN + "/admin/users", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the users module");
    const delayed = await page.evaluate(() => ({
      search: document.querySelectorAll("#usr-q").length,
      rows: document.querySelectorAll("#usr-tbody tr").length,
      alert: document.querySelectorAll("#sec-usuarios [role=alert]").length,
      legacy: typeof window.SanaUsers,
      route: typeof window.sanaRouteUsuarios,
      moduleUrl: window.SANA_STATIC?.["platform/admin-users.mjs"] || "",
    }));
    assert.equal(delayed.search, 0, "search must not paint before the renderer is ready");
    assert.equal(delayed.rows, 0);
    assert.equal(delayed.alert, 0);
    assert.equal(delayed.legacy, "undefined");
    assert.equal(delayed.route, "undefined");
    assert.equal(delayed.moduleUrl, MODULE_PATH);
    release.release();
    await navigation;
    await page.locator("#usr-q").waitFor({ state: "visible" });
    assert.equal(await page.locator("#usr-tbody a[data-user='mesa.uno']").count(), 1);
    const after = await page.evaluate(() => ({
      legacy: typeof window.SanaUsers,
      route: typeof window.sanaRouteUsuarios,
    }));
    assert.equal(after.legacy, "undefined");
    assert.equal(after.route, "undefined");
  });
});

test("a rejected Users module shows localized recovery and no retired global", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { abortModule: true }));
    await page.goto(ORIGIN + "/admin/users", { waitUntil: "domcontentloaded" });
    const alert = page.locator("#sec-usuarios [role=alert]");
    await alert.waitFor({ state: "visible" });
    assert.match(await alert.innerText(), /No se pudo cargar\. Reintentá\./);
    assert.equal(await page.locator("#usr-q").count(), 0);
    assert.equal(await page.evaluate(() => typeof window.SanaUsers), "undefined");
    assert.equal(await page.evaluate(() => typeof window.sanaRouteUsuarios), "undefined");
  });
});

test("English rejected Users import uses the translated recovery, not Spanish fallback", async () => {
  await withPage(async (page) => {
    const english = "Couldn't load. Try again.";
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/admin/users") {
        return route.fulfill({
          contentType: "text/html; charset=utf-8",
          body: pageHtml({ error: english }),
        });
      }
      return fulfillFiles(route, { abortModule: true });
    });
    await page.goto(ORIGIN + "/admin/users", { waitUntil: "domcontentloaded" });
    const alert = page.locator("#sec-usuarios [role=alert]");
    await alert.waitFor({ state: "visible" });
    assert.equal(await alert.innerText(), english);
    assert.equal(await page.locator("#usr-q").count(), 0);
  });
});

test("a late list fetch for an abandoned Users route does not restore the list over the form", async () => {
  await withPage(async (page) => {
    const usersRequested = deferred();
    const usersRelease = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdModule: { requested: deferred(), release: { promise: Promise.resolve(), release() {} }, users: { requested: usersRequested, release: usersRelease } },
    }));
    await page.goto(ORIGIN + "/admin/users", { waitUntil: "domcontentloaded" });
    await within(usersRequested.promise, "request of the users API");
    assert.equal(await page.locator("#usr-q").count(), 0);
    assert.equal(await page.locator("#usr-usuario").count(), 0);
    await page.evaluate(() => {
      history.pushState({}, "", "/admin/users?new=1");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    usersRelease.release();
    await page.locator("#usr-usuario").waitFor({ state: "visible" });
    assert.equal(await page.locator("#usr-q").count(), 0, "abandoned list must not overwrite the new-user form");
    // Four roles exist; a new user never takes «Médico» (Médicos creates it).
    assert.equal(await page.locator('input[name="usr-rol"]').count(), 3);
    assert.equal(await page.evaluate(() => typeof window.SanaUsers), "undefined");
  });
});

test("doctor-account rerender releases detached controls and ignores out-of-order reads", async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => {
      if (new URL(route.request().url()).pathname === '/admin/doctors')
        return route.fulfill({ contentType: 'text/html', body: pageHtml({ screen: 'medicos' }) });
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + '/admin/doctors');
    const result = await page.evaluate(async doctor => {
      const Users = await import(window.SANA_STATIC['platform/admin-users.mjs']);
      const nativeFetch = window.fetch;
      let posts = 0, reads = 0, releaseOld;
      window.fetch = async (url, config) => {
        if (String(url).includes('/actions/create')) { posts++; return new Response('{}', { status: 503 }); }
        if (url === '/api/admin/users') {
          reads++;
          if (reads === 2) return new Promise(resolve => { releaseOld = () => resolve(new Response(JSON.stringify({ items: [{ usuario: 'old.read', rol: 'medico', medico_id: doctor.id }] }))); });
          return new Response(JSON.stringify({ items: [] }));
        }
        return nativeFetch(url, config);
      };
      const host = document.querySelector('#med-user-panel');
      const owner = Users.mountDoctorUser(host, doctor.id, doctor);
      await owner.ready;
      const detachedButton = host.querySelector('#doctor-user-create-form button');
      const oldRead = owner.render();
      await owner.render();
      detachedButton.click();
      releaseOld();
      await oldRead;
      const result = { posts, freshForm: !!host.querySelector('#doctor-user-new'), staleText: host.textContent.includes('old.read') };
      owner.dispose();
      return result;
    }, DOCTOR);
    assert.deepEqual(result, { posts: 0, freshForm: true, staleText: false });
  });
});

test("doctor account owner suppresses late reads and clears only its own host", async () => {
  await withPage(async (page) => {
    const firstRequested = deferred();
    const firstRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        if (reads === 1) {
          firstRequested.release();
          await firstRelease.promise;
          return route.fulfill({ json: { items: [{ usuario: "late", rol: "medico", medico_id: DOCTOR.id }] } });
        }
        return route.fulfill({ json: { items: ITEMS } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
    }, DOCTOR);
    await within(firstRequested.promise, "request of the first doctor-account read");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    await page.locator("#doctor-user-new").waitFor({ state: "visible" });
    assert.equal(await page.locator(".doctor-user-summary").count(), 0);
    firstRelease.release();
    await page.evaluate(() => window.__oldDoctorOwner.ready);
    assert.equal(await page.locator("#doctor-user-new").count(), 1,
      "a late old read must not replace the fresh account form");
    await page.evaluate(() => window.__freshDoctorOwner.dispose());
    assert.equal(await page.locator("#med-user-panel").innerHTML(), "");
    await page.evaluate(() => window.__freshDoctorOwner.dispose());
    assert.equal(await page.locator("#med-user-panel").innerHTML(), "", "dispose is idempotent");
  });
});

test("an accepted old doctor-account creation cannot publish credentials into its replacement", async () => {
  await withPage(async (page) => {
    const createRequested = deferred();
    const createRelease = deferred();
    let creates = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users/actions/create") {
        creates += 1;
        createRequested.release();
        await createRelease.promise;
        return route.fulfill({ json: { ok: true, resultados: [{ ok: true, usuario: "diego.rivera", password: "late-secret" }] } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__oldDoctorOwner.ready;
    }, DOCTOR);
    await page.locator("#doctor-user-create-form button").click();
    await within(createRequested.promise, "request of the doctor-account create");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    createRelease.release();
    await page.evaluate(() => window.__oldDoctorOwner.ready);
    await stays(async () => creates === 1 && await page.locator("#doctor-user-password").count() === 0
      && await page.locator("#doctor-user-new").count() === 1, "the old create completion leaves the replacement owner alone", 50);
    assert.equal(creates, 1, "the accepted create is not repeated or rolled back");
    assert.equal(await page.locator("#doctor-user-password").count(), 0,
      "the old completion must not reveal credentials in the replacement owner");
    assert.equal(await page.locator("#doctor-user-new").count(), 1);
  });
});

test("a disposed doctor-account owner cannot redirect after delayed session confirmation", async () => {
  await withPage(async (page) => {
    const whoamiRequested = deferred();
    const whoamiRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        return route.fulfill(reads === 1
          ? { status: 401, json: { error: "no_autenticado" } }
          : { json: { items: ITEMS } });
      }
      if (url.pathname === "/api/admin/whoami") {
        whoamiRequested.release();
        await whoamiRelease.promise;
        return route.fulfill({ status: 401, json: { error: "no_autenticado" } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      window.__oldDoctorOwner.ready.catch(() => {});
    }, DOCTOR);
    await within(whoamiRequested.promise, "request of the session confirmation");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    whoamiRelease.release();
    await stays(async () => new URL(page.url()).pathname === "/admin/doctors" && await page.locator("#doctor-user-new").count() === 1,
      "a stale session check leaves the fresh doctor screen alone", 50);
    assert.equal(new URL(page.url()).pathname, "/admin/doctors",
      "a stale session check must not redirect the fresh doctor screen");
    assert.equal(await page.locator("#doctor-user-new").count(), 1);
  });
});

test("the original doctor-account disposer still owns an action-triggered reread", async () => {
  await withPage(async (page) => {
    const rereadRequested = deferred();
    const rereadRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        if (reads === 2) {
          rereadRequested.release();
          await rereadRelease.promise;
        }
        return route.fulfill({ json: { items: ITEMS } });
      }
      if (url.pathname === "/api/admin/users/actions/link") {
        return route.fulfill({ json: { ok: true, resultados: [{ ok: true, usuario: "mesa.uno" }] } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__doctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__doctorOwner.ready;
    }, DOCTOR);
    // Linking an existing user is a legacy repair, folded behind «Reparar vínculo».
    assert.equal(await page.locator("#doctor-user-existing").isVisible(), false);
    await page.locator("#doctor-user-repair").click();
    await page.locator("#doctor-user-existing").selectOption("mesa.uno");
    await page.locator("#doctor-user-link-form button").click();
    await within(rereadRequested.promise, "request of the action reread");
    await page.evaluate(() => window.__doctorOwner.dispose());
    rereadRelease.release();
    await stays(async () => await page.locator("#med-user-panel").innerHTML() === "", "the released reread repaints nothing after dispose", 50);
    assert.equal(await page.locator("#med-user-panel").innerHTML(), "",
      "the original disposer must still scrub the action-rerendered owner");
  });
});

test("a stale doctor-account JSON-body rejection settles without replacing the fresh panel", async () => {
  await withPage(async (page) => {
    const firstRequested = deferred();
    const firstRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        if (reads === 1) {
          firstRequested.release();
          await firstRelease.promise;
          return route.fulfill({ contentType: "application/json", body: "{" });
        }
        return route.fulfill({ json: { items: ITEMS } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
    }, DOCTOR);
    await within(firstRequested.promise, "request of the old body");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    firstRelease.release();
    assert.equal(await page.evaluate(async () => {
      await window.__oldDoctorOwner.ready;
      return "settled";
    }), "settled");
    assert.equal(await page.locator("#doctor-user-new").count(), 1);
  });
});

test("doctor user panel waits for the native module and renders real account controls", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({
          contentType: "text/html; charset=utf-8",
          body: pageHtml({ screen: "medicos" }),
        });
      }
      return fulfillFiles(route, { holdModule: { requested, release } });
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    const mount = page.evaluate(async (doctor) => {
      const url = window.SANA_STATIC["platform/admin-users.mjs"];
      const pending = import(url).then((m) => m.renderDoctorUser(
        document.querySelector("#med-user-panel"), doctor.id, doctor,
      ));
      window.__doctorUserReady = pending;
      return { started: true };
    }, DOCTOR);
    await within(requested.promise, "request of the users module for doctor panel");
    await mount;
    assert.equal(await page.locator("#doctor-user-existing").count(), 0,
      "doctor account controls must wait for the native module");
    release.release();
    await page.evaluate(() => window.__doctorUserReady);
    await page.locator("#doctor-user-new").waitFor({ state: "visible" });
    assert.equal(await page.locator("#doctor-user-link-form").count(), 1);
    assert.equal(await page.evaluate(() => typeof window.SanaUsers), "undefined");
  });
});

test("doctor-account rerender releases detached controls and ignores out-of-order reads", async () => {
  await withPage(async page => {
    await page.route(`${ORIGIN}/**`, route => {
      if (new URL(route.request().url()).pathname === '/admin/doctors')
        return route.fulfill({ contentType: 'text/html', body: pageHtml({ screen: 'medicos' }) });
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + '/admin/doctors');
    const result = await page.evaluate(async doctor => {
      const Users = await import(window.SANA_STATIC['platform/admin-users.mjs']);
      const nativeFetch = window.fetch;
      let posts = 0, reads = 0, releaseOld;
      window.fetch = async (url, config) => {
        if (String(url).includes('/actions/create')) { posts++; return new Response('{}', { status: 503 }); }
        if (url === '/api/admin/users') {
          reads++;
          if (reads === 2) return new Promise(resolve => { releaseOld = () => resolve(new Response(JSON.stringify({ items: [{ usuario: 'old.read', rol: 'medico', medico_id: doctor.id }] }))); });
          return new Response(JSON.stringify({ items: [] }));
        }
        return nativeFetch(url, config);
      };
      const host = document.querySelector('#med-user-panel');
      const owner = Users.mountDoctorUser(host, doctor.id, doctor);
      await owner.ready;
      const detachedButton = host.querySelector('#doctor-user-create-form button');
      const oldRead = owner.render();
      await owner.render();
      detachedButton.click();
      releaseOld();
      await oldRead;
      const result = { posts, freshForm: !!host.querySelector('#doctor-user-new'), staleText: host.textContent.includes('old.read') };
      owner.dispose();
      return result;
    }, DOCTOR);
    assert.deepEqual(result, { posts: 0, freshForm: true, staleText: false });
  });
});

test("doctor account owner suppresses late reads and clears only its own host", async () => {
  await withPage(async (page) => {
    const firstRequested = deferred();
    const firstRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        if (reads === 1) {
          firstRequested.release();
          await firstRelease.promise;
          return route.fulfill({ json: { items: [{ usuario: "late", rol: "medico", medico_id: DOCTOR.id }] } });
        }
        return route.fulfill({ json: { items: ITEMS } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
    }, DOCTOR);
    await within(firstRequested.promise, "request of the first doctor-account read");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    await page.locator("#doctor-user-new").waitFor({ state: "visible" });
    assert.equal(await page.locator(".doctor-user-summary").count(), 0);
    firstRelease.release();
    await page.evaluate(() => window.__oldDoctorOwner.ready);
    assert.equal(await page.locator("#doctor-user-new").count(), 1,
      "a late old read must not replace the fresh account form");
    await page.evaluate(() => window.__freshDoctorOwner.dispose());
    assert.equal(await page.locator("#med-user-panel").innerHTML(), "");
    await page.evaluate(() => window.__freshDoctorOwner.dispose());
    assert.equal(await page.locator("#med-user-panel").innerHTML(), "", "dispose is idempotent");
  });
});

test("an accepted old doctor-account creation cannot publish credentials into its replacement", async () => {
  await withPage(async (page) => {
    const createRequested = deferred();
    const createRelease = deferred();
    let creates = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users/actions/create") {
        creates += 1;
        createRequested.release();
        await createRelease.promise;
        return route.fulfill({ json: { ok: true, resultados: [{ ok: true, usuario: "diego.rivera", password: "late-secret" }] } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__oldDoctorOwner.ready;
    }, DOCTOR);
    await page.locator("#doctor-user-create-form button").click();
    await within(createRequested.promise, "request of the doctor-account create");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    createRelease.release();
    await page.evaluate(() => window.__oldDoctorOwner.ready);
    await stays(async () => creates === 1 && await page.locator("#doctor-user-password").count() === 0
      && await page.locator("#doctor-user-new").count() === 1, "the old create completion leaves the replacement owner alone", 50);
    assert.equal(creates, 1, "the accepted create is not repeated or rolled back");
    assert.equal(await page.locator("#doctor-user-password").count(), 0,
      "the old completion must not reveal credentials in the replacement owner");
    assert.equal(await page.locator("#doctor-user-new").count(), 1);
  });
});

test("a disposed doctor-account owner cannot redirect after delayed session confirmation", async () => {
  await withPage(async (page) => {
    const whoamiRequested = deferred();
    const whoamiRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        return route.fulfill(reads === 1
          ? { status: 401, json: { error: "no_autenticado" } }
          : { json: { items: ITEMS } });
      }
      if (url.pathname === "/api/admin/whoami") {
        whoamiRequested.release();
        await whoamiRelease.promise;
        return route.fulfill({ status: 401, json: { error: "no_autenticado" } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      window.__oldDoctorOwner.ready.catch(() => {});
    }, DOCTOR);
    await within(whoamiRequested.promise, "request of the session confirmation");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    whoamiRelease.release();
    await stays(async () => new URL(page.url()).pathname === "/admin/doctors" && await page.locator("#doctor-user-new").count() === 1,
      "a stale session check leaves the fresh doctor screen alone", 50);
    assert.equal(new URL(page.url()).pathname, "/admin/doctors",
      "a stale session check must not redirect the fresh doctor screen");
    assert.equal(await page.locator("#doctor-user-new").count(), 1);
  });
});

test("the original doctor-account disposer still owns an action-triggered reread", async () => {
  await withPage(async (page) => {
    const rereadRequested = deferred();
    const rereadRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        if (reads === 2) {
          rereadRequested.release();
          await rereadRelease.promise;
        }
        return route.fulfill({ json: { items: ITEMS } });
      }
      if (url.pathname === "/api/admin/users/actions/link") {
        return route.fulfill({ json: { ok: true, resultados: [{ ok: true, usuario: "mesa.uno" }] } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__doctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__doctorOwner.ready;
    }, DOCTOR);
    // Linking an existing user is a legacy repair, folded behind «Reparar vínculo».
    assert.equal(await page.locator("#doctor-user-existing").isVisible(), false);
    await page.locator("#doctor-user-repair").click();
    await page.locator("#doctor-user-existing").selectOption("mesa.uno");
    await page.locator("#doctor-user-link-form button").click();
    await within(rereadRequested.promise, "request of the action reread");
    await page.evaluate(() => window.__doctorOwner.dispose());
    rereadRelease.release();
    await stays(async () => await page.locator("#med-user-panel").innerHTML() === "", "the released reread repaints nothing after dispose", 50);
    assert.equal(await page.locator("#med-user-panel").innerHTML(), "",
      "the original disposer must still scrub the action-rerendered owner");
  });
});

test("a stale doctor-account JSON-body rejection settles without replacing the fresh panel", async () => {
  await withPage(async (page) => {
    const firstRequested = deferred();
    const firstRelease = deferred();
    let reads = 0;
    await page.route(`${ORIGIN}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/admin/users") {
        reads += 1;
        if (reads === 1) {
          firstRequested.release();
          await firstRelease.promise;
          return route.fulfill({ contentType: "application/json", body: "{" });
        }
        return route.fulfill({ json: { items: ITEMS } });
      }
      if (url.pathname === "/admin/doctors") {
        return route.fulfill({ contentType: "text/html; charset=utf-8", body: pageHtml({ screen: "medicos" }) });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/admin/doctors", { waitUntil: "domcontentloaded" });
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__oldDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
    }, DOCTOR);
    await within(firstRequested.promise, "request of the old body");
    await page.evaluate(async (doctor) => {
      const Users = await import(window.SANA_STATIC["platform/admin-users.mjs"]);
      window.__freshDoctorOwner = Users.mountDoctorUser(document.querySelector("#med-user-panel"), doctor.id, doctor);
      await window.__freshDoctorOwner.ready;
    }, DOCTOR);
    firstRelease.release();
    assert.equal(await page.evaluate(async () => {
      await window.__oldDoctorOwner.ready;
      return "settled";
    }), "settled");
    assert.equal(await page.locator("#doctor-user-new").count(), 1);
  });
});
