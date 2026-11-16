import { before, after } from 'node:test';
import { test, ceil, hookOptions, within } from './helpers/ceiling.mjs';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { chromium } from 'playwright';
import { until, stays } from './helpers/wait.mjs';

// Actual browser modules; synthetic identities and every request intercepted.
// No database, booking system, identity provider, registry or email access.
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
after(async () => { await browser?.close(); });
const root = new URL('../', import.meta.url);
const demoUser = { dni: '05123456', nombre: 'Demo', apellido: 'Prueba', nacimiento: '1990-01-01', contacto: '+542254000000', email: 'demo@example.invalid' };
const country = JSON.parse(readFileSync(new URL('packs/ar/country.json', root), 'utf8'));
const baseReceipt = { id: 77, servicio_id: 'demo', centro_slug: 'demo', estado: 'aprobada', turno_dt: '2026-11-20T10:00:00-03:00', paciente_dni: demoUser.dni, paciente_nombre: 'Demo Prueba', entrevista: { ofrecer: true, url: '/entrevista/77' } };
async function fixture(t, options = {}) {
  const page = await browser.newPage({ viewport: { width: options.width || 390, height: 844 } });
  ceil(page);
  t.after(() => page.close());
  let coverage = options.coverage || null, revision = 'r1', user = { ...demoUser }, receipt = { ...baseReceipt, ...options.receipt };
  let detailCount = 0, detailStatus = options.detailStatus || 200;
  let getCount = 0, failSaves = options.failSaves || 0, conflict = options.conflict || false;
  const requests = [], errors = [], waiters = [];
  // Resolves once `count` requests to `path` reached their handler. Each handler
  // reads the fixture state synchronously before its first await, so a test that
  // awaits this changes the state only after that request's answer is fixed.
  function served(path, count) {
    const reached = () => requests.filter(r => r.path === path).length >= count;
    if (reached()) return Promise.resolve();
    return within(new Promise(resolve => waiters.push({ reached, resolve })), `request number ${count} of ${path}`);
  }
  const api = {
    setUser(value) { user = value; }, setReceipt(value) { receipt = { ...receipt, ...value }; },
    setDetailStatus(value) { detailStatus = value; }, detailCount: () => detailCount,
    saved: () => coverage, requests, page, errors, getCount: () => getCount, served
  };
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', e => { if(e.type() === 'error' && e.text().includes('[service]')) t.diagnostic(e.text()); });
  const modules = ['i18n','account-form','age','calendar-grid','dates','format','prep','turnstile','dependents'];
  const manifest = Object.fromEntries(modules.map(n => [`platform/${n}.mjs`, `/static/platform/${n}.mjs`]));
  const catalog = Object.assign({}, ...['account.form','service','date'].map(n => JSON.parse(readFileSync(new URL(`locales/es/${n}.json`, root), 'utf8'))));
  await page.route('**/*', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    requests.push({ path, method: req.method(), body: req.method() === 'POST' ? req.postDataJSON() : null });
    for (const waiter of waiters.filter(w => w.reached())) { waiters.splice(waiters.indexOf(waiter), 1); waiter.resolve(); }
    // a module asset's URL (/modules/...) names it inside the code package
    const disk = new URL(path.startsWith('/modules/') ? `./app${path}` : `.${path}`, root);
    if (path.endsWith('.mjs') && existsSync(disk)) return route.fulfill({ contentType: 'text/javascript', body: readFileSync(disk, 'utf8') });
    if (path === '/api/account/me') return route.fulfill({ status: user ? 200 : 401, json: user ? { usuario: user } : { error: 'auth_requerida' } });
    if (path === '/api/requests/77') {
      const wait = options.detailWaits?.[detailCount++];
      const snapshot = { ...receipt }, status = detailStatus;
      if (wait) await wait;
      return route.fulfill({ status, json: { solicitud: snapshot } });
    }
    if (path === '/api/requests/77/coverage') {
      if (req.method() === 'GET') {
        getCount++;
        if (options.holdGet) await options.holdGet;
        if (options.reason) return route.fulfill({ json: { supported: false, reason: options.reason } });
        return route.fulfill({ json: { supported: true, cobertura: coverage, revision, sin_cobertura: coverage?.declarada_sin_cobertura === true } });
      }
      if (options.holdSave) await options.holdSave;
      if (options.saveStatus) return route.fulfill({ status: options.saveStatus, json: { error: 'unavailable' } });
      if (conflict) { conflict = false; coverage = { obras_sociales: [{ nombre: 'PAMI' }] }; revision = 'r2'; return route.fulfill({ status: 409, json: { error: 'coverage_changed' } }); }
      const body = req.postDataJSON();
      coverage = body.choice === 'none' ? { declarada_sin_cobertura: true } : body.cobertura;
      revision = 'r2';
      if (failSaves-- > 0) return route.abort('failed'); // server committed, reply lost
      return route.fulfill({ json: { ok: true, supported: true, cobertura: coverage, revision, sin_cobertura: coverage?.declarada_sin_cobertura === true } });
    }
    if (path === '/api/services') return route.fulfill({ json: [] });
    if (path === '/api/centers') return route.fulfill({ json: [] });
    if (path === '/api/preparation-studies') return route.fulfill({ json: [] });
    if (path === '/api/account/update') { user = { ...user, ...req.postDataJSON() }; return route.fulfill({ json: { usuario: user } }); }
    if (path === '/api/account/dependents') return route.fulfill({ json: { dependientes: [] } });
    if (path.startsWith('/api/service-slots/')) return route.fulfill({ json: { medicos: [{ medico_id: 'demo', nombre: 'Profesional Demo', turnos: [{ dia: '2026-11-20', hora: '10:00' }] }] } });
    if (path === '/api/slot-hold') return route.fulfill({ json: { ok: true, token: 'synthetic-hold', ttl_min: 3 } });
    if (path === '/api/requests' && req.method() === 'POST') return route.fulfill({ json: { ok: true, ...receipt } });
    if (path === '/fixture') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <style>html{box-sizing:border-box}*,*:before,*:after{box-sizing:inherit}body{margin:16px;font:16px/1.5 Arial,sans-serif;--font:Arial,sans-serif;--text:#24352d;--text-2:#4c5a51;--panel:#fff;--border:#ccd5cf;--accent:#14764f;--line:#ccd5cf}main{max-width:860px;margin:auto}.sec{min-width:0}a{color:var(--accent)}button,input{font:inherit}</style>
      <script>window.SANA_COUNTRY_PHONE=${JSON.stringify(country.phone)};window.SANA_I18N=${JSON.stringify({ active: options.lang || 'es', catalog: options.fallback ? {} : catalog })};window.SANA_STATIC=${JSON.stringify(manifest)};${options.service && !options.fresh ? 'history.replaceState({wzStep:"success",wzRequestId:77},"");' : ''}</script></head><body><main>
      ${options.service ? `<h1 id="svc-title"></h1><div id="svc-steps"></div><div id="svc-sub"></div><div id="svc-form-host"></div><script id="svc-data" type="application/json">${JSON.stringify({ servicio: { id: 'demo', centro: 'demo', nombre: 'Consulta de prueba', modo: 'solicitud' }, centro: { name: 'Centro de prueba' } })}</script><script type="module">import {boot} from '/modules/turnos/service.mjs';boot();</script>` : `<h1>Solicitud enviada</h1><a id="receipt" href="/my-requests">Ver mis turnos</a><section id="coverage"></section><script type="module">import {renderPostbookingCoverage} from '/static/platform/account-form.mjs';window.mount=()=>renderPostbookingCoverage(document.querySelector('#coverage'),{requestId:77,userKey:'05123456'});window.stop=window.mount();</script>`}
      </main><button id="outside">Otra acción</button></body></html>` });
    return route.fulfill({ status: 404, body: 'Outside synthetic fixture scope' });
  });
  await page.goto(`https://postcoverage.test/fixture${options.fresh ? '?slot=2026-11-20T10:00' : ''}`);
  t.after(() => assert.deepEqual(errors, []));
  return api;
}
const saves = f => f.requests.filter(r => r.path.endsWith('/coverage') && r.method === 'POST');
const bookings = f => f.requests.filter(r => r.path === '/api/requests' && r.method === 'POST');
async function pick(page, query, name, row = 0) {
  await page.locator(`#sc-os-${row}`).fill(query);
  await page.locator(`#sc-os-${row}-list`).getByRole('option', { name, exact: true }).click();
}
async function saved(page) { await page.locator('[data-coverage-edit]').waitFor(); }

test('actual shared picker: Swiss, manual cover, RNOS/member/plan, keyboard clear and reload roundtrip', async t => {
  const f = await fixture(t, { coverage: { obras_sociales: [{ nombre: 'IOMA', rnos: '0001', afiliado: '0007', origen: 'puco' }] } });
  await f.page.locator('[data-coverage-edit]').click();
  await f.page.locator('#sc-os-add').click();
  await f.page.locator('#sc-os-1').fill('SWÍSS');
  assert.equal(await f.page.locator('.sc-os-item[data-i="1"] .sc-os-del').isVisible(), false);
  await f.page.locator('#sc-os-1').press('ArrowDown'); await f.page.locator('#sc-os-1').press('Enter');
  await f.page.locator('#sc-os-af-1').fill('00042'); await f.page.locator('#sc-os-plan-1').fill('SMG20');
  await f.page.locator('[type="submit"]').click(); await saved(f.page);
  assert.deepEqual(saves(f)[0].body, { choice: 'coverage', revision: 'r1', cobertura: { obras_sociales: [{ nombre: 'IOMA', rnos: '0001', afiliado: '0007' }], prepaga: { nombre: 'Swiss Medical', afiliado: '00042', plan: 'SMG20' } } });
  await f.page.reload(); await f.page.locator('[data-coverage-edit]').click();
  assert.equal(await f.page.locator('#sc-os-plan-1').inputValue(), 'SMG20');
  await f.page.getByRole('button', { name: 'Quitar Swiss Medical' }).click();
  assert.equal(await f.page.locator('#sc-os-0').evaluate(el => el === document.activeElement), true);
  await f.page.locator('#sc-os-0').press('Escape');
  await f.page.locator('#sc-os-add').click(); await pick(f.page, 'Cobertura Demo', 'Agregar «Cobertura Demo» como obra social', 1);
  await f.page.locator('[type="submit"]').click(); await saved(f.page);
  assert.equal(saves(f).at(-1).body.cobertura.obras_sociales[1].nombre, 'Cobertura Demo');
  assert.equal(bookings(f).length, 0);
});
// One action on the card (the product owner 26.10): «Guardar cobertura», the page's
// primary button; «No tengo cobertura» and «Prefiero más tarde» are gone —
// whoever does not want to fill it in just leaves it.
test('one primary save, no «none»/«later», and query alone cannot save', async t => {
  const f = await fixture(t);
  await f.page.locator('[type="submit"]').waitFor();
  assert.equal(await f.page.locator('[data-coverage-none], [data-coverage-later]').count(), 0);
  // the picker's own controls (list toggle, «Agregar otra cobertura») aside
  assert.equal(await f.page.locator('.sc-postcoverage button:visible').evaluateAll(bs => bs.filter(b => !b.closest('[data-coverage-picker]')).length), 1);
  assert.match(await f.page.locator('[type="submit"]').getAttribute('class'), /\bsc-cta\b/);
  await f.page.locator('#sc-os-0').fill('swiss'); await f.page.locator('#sc-os-0').press('Escape'); await f.page.locator('[type="submit"]').click();
  assert.equal(saves(f).length, 0);
  assert.match(await f.page.locator('[data-coverage-status]').textContent(), /Elegí tu cobertura de la lista/);
});
for (const reason of ['dependent', 'identity']) test(`${reason} does not render holder editor or write`, async t => {
  const f = await fixture(t, { reason });
  await f.page.waitForFunction(() => !document.querySelector('[data-coverage-status]'));
  assert.equal(await f.page.locator('#sc-os-0').count(), 0);
  if (reason === 'dependent') assert.match(await f.page.locator('#coverage').textContent(), /admisión/);
  assert.equal(saves(f).length, 0);
});
test('lost save reply retries identical payload, double click does not create another operation', async t => {
  const f = await fixture(t, { failSaves: 1 }); await pick(f.page, 'swiss medical', 'Swiss Medical');
  await f.page.locator('[type="submit"]').dblclick(); await saved(f.page);
  assert.equal(saves(f).length, 2); assert.deepEqual(saves(f)[0].body, saves(f)[1].body); assert.equal(bookings(f).length, 0);
});
test('revision conflict requires reading saved state before another write', async t => {
  const f = await fixture(t, { conflict: true }); await pick(f.page, 'swiss', 'Swiss Medical');
  await f.page.locator('[type="submit"]').click(); await f.page.getByRole('button', { name: 'Revisar cobertura guardada' }).click(); await saved(f.page);
  assert.match(await f.page.locator('.sc-postcoverage').textContent(), /PAMI/); assert.equal(saves(f).length, 1);
});
for (const change of ['route', 'session', 'remove', 'dispose']) test(`stale GET ignored after ${change}`, async t => {
  let release; const holdGet = new Promise(r => { release = r; });
  const f = await fixture(t, { holdGet }); await f.page.waitForFunction(() => document.querySelector('[data-coverage-status]'));
  await until(() => f.getCount(), 'the coverage GET reaches the held route');
  if (change === 'route') await f.page.evaluate(() => { history.pushState({}, '', '#elsewhere'); window.dispatchEvent(new PopStateEvent('popstate')); });
  if (change === 'session') f.setUser({ ...demoUser, dni: '06123456' });
  if (change === 'remove') await f.page.locator('#coverage').evaluate(el => el.remove());
  if (change === 'dispose') await f.page.evaluate(() => window.stop());
  release();
  await stays(async () => await f.page.locator('#sc-os-0').count() === 0 && saves(f).length === 0, `the stale GET paints nothing after ${change}`, 80);
  assert.equal(await f.page.locator('#sc-os-0').count(), 0); assert.equal(saves(f).length, 0);
});
test('session changed before save or during save cannot display old coverage', async t => {
  const f = await fixture(t); await pick(f.page, 'swiss', 'Swiss Medical'); f.setUser(null);
  await f.page.locator('[type="submit"]').click(); await f.page.locator('.sc-postcoverage a[href="/account"]').waitFor();
  assert.equal(saves(f).length, 0); assert.equal(await f.page.locator('#sc-os-0').count(), 0);
});
test('leaving the card cancels delayed response and automatic retries; BFCache rechecks authoritative state', async t => {
  const f = await fixture(t, { saveStatus: 503 }); await pick(f.page, 'swiss', 'Swiss Medical');
  await f.page.locator('[type="submit"]').click(); await f.page.getByText(/Esperando conexión/).waitFor();
  await f.page.evaluate(() => window.stop());
  await stays(() => saves(f).length === 1, 'no automatic retry is sent after leaving the card', 1100);
  assert.equal(saves(f).length, 1);
  await f.page.evaluate(() => { document.querySelector('#coverage').innerHTML=''; window.stop=window.mount(); });
  await f.page.locator('#sc-os-0').waitFor();
  await f.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
  f.setUser(null);
  await f.page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
  await f.page.locator('.sc-postcoverage a[href="/account"]').waitFor(); assert.equal(await f.page.locator('#sc-os-0').count(), 0);
});
for (const width of [320,390,1280]) for (const estado of ['aprobada','pendiente']) test(`actual service receipt ${estado} at ${width}: optional picker, original actions, no repeat booking`, async t => {
  const f = await fixture(t, { service: true, width, receipt: { estado }, fallback: width === 320 });
  await f.page.locator('#sc-os-0').waitFor();
  assert.match(await f.page.locator('#svc-title').textContent(), estado === 'aprobada' ? /Turno confirmado/ : /Solicitud enviada/);
  assert.equal(await f.page.getByRole('link', { name: 'Contanos qué te pasa', exact: true }).count(), 1);
  assert.equal(await f.page.getByRole('link', { name: 'Ver mis turnos', exact: true }).count(), 0);
  assert.equal(await f.page.locator('.wz-success-summary').evaluate(el => el.nextElementSibling.classList.contains('sc-postcoverage')), true);
  await pick(f.page, 'Obra Social Sintética de Trabajadores Municipales', 'Agregar «Obra Social Sintética de Trabajadores Municipales» como obra social');
  const dir = process.env.TEST_POSTCOVERAGE_SCREENSHOT_DIR;
  if (dir) { mkdirSync(dir, { recursive: true }); await f.page.screenshot({ path: join(dir, `postcoverage-${estado}-${width}.png`), fullPage: true }); }
  assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(bookings(f).length, 0);
  await f.page.locator('[type="submit"]').click(); await saved(f.page); await f.page.reload(); await saved(f.page);
  assert.deepEqual(await f.page.evaluate(() => history.state), { wzStep: 'success', wzRequestId: 77 });
  assert.equal(bookings(f).length, 0);
});
for (const reason of ['expired','cancelled','foreign','elapsed']) test(`reload ${reason}: no old confirmed claim, coverage or booking`, async t => {
  const f = await fixture(t, { service: true, detailStatus: reason === 'foreign' ? 404 : 200, receipt: { estado: reason === 'cancelled' ? 'cancelada' : 'aprobada', turno_pasado: reason === 'elapsed' } });
  if (reason === 'expired') { f.setUser(null); await f.page.reload(); }
  await f.page.getByText(/Consultá el estado actual/).waitFor();
  assert.equal(await f.page.locator('#sc-os-0').count(), 0); assert.doesNotMatch(await f.page.locator('#svc-title').textContent(), /confirmado/);
  assert.equal(bookings(f).length, 0);
});

for (const estado of ['aprobada','pendiente']) test(`fresh booking response ${estado} mounts optional coverage once and reload never reposts`, async t => {
  const f = await fixture(t, { service: true, fresh: true, receipt: { estado } });
  await f.page.locator('#wz-send').click(); await f.page.locator('#sc-os-0').waitFor();
  assert.equal(bookings(f).length, 1);
  await f.page.reload(); await f.page.locator('#sc-os-0').waitFor();
  assert.equal(bookings(f).length, 1);
});
for (const change of ['route','session']) test(`stale save ignored after ${change}`, async t => {
  let release; const holdSave = new Promise(r => { release = r; });
  const f = await fixture(t, { holdSave }); await pick(f.page, 'swiss', 'Swiss Medical');
  await f.page.locator('[type="submit"]').click();
  await until(() => saves(f).length, 'the save reaches the held route');
  if (change === 'route') await f.page.evaluate(() => { history.pushState({}, '', '#other'); window.dispatchEvent(new PopStateEvent('popstate')); });
  else f.setUser({ ...demoUser, dni: '06123456' });
  release();
  await stays(async () => await f.page.locator('[data-coverage-edit]').count() === 0 && saves(f).length === 1, `the stale save paints nothing after ${change}`, 100);
  assert.equal(await f.page.locator('[data-coverage-edit]').count(), 0); assert.equal(saves(f).length, 1);
});
test('reload takes new pending/cancelled truth, never historical approved title', async t => {
  const f = await fixture(t, { service: true }); await f.page.locator('#sc-os-0').waitFor();
  f.setReceipt({ estado: 'pendiente', turno_dt: null }); await f.page.reload(); await f.page.locator('#sc-os-0').waitFor();
  assert.match(await f.page.locator('#svc-title').textContent(), /Solicitud enviada/);
  f.setReceipt({ estado: 'cancelada' }); await f.page.reload(); await f.page.getByText(/Consultá el estado actual/).waitFor();
  assert.equal(await f.page.locator('#sc-os-0').count(), 0); assert.equal(bookings(f).length, 0);
});

// Lifecycle events on the actual module, with held authenticated reads. These
// are deterministic interruption tests, not a claim of physical Safari BFCache.
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
async function detailStarted(f, count) {
  await f.served('/api/requests/77', count);
  assert.equal(f.detailCount(), count);
}
async function transition(page, name, persisted = true) {
  await page.evaluate(({name, persisted}) => window.dispatchEvent(new PageTransitionEvent(name, { persisted })), { name, persisted });
}
test('receipt interrupted BFCache pageshow then same-entry popstate preserves one authorized restoration', async t => {
  const first = deferred(), resumed = deferred();
  t.after(() => { first.resolve(); resumed.resolve(); });
  const f = await fixture(t, { service: true, detailWaits: [first.promise, resumed.promise] });
  await detailStarted(f, 1);
  await transition(f.page, 'pagehide');
  f.setReceipt({ estado: 'pendiente', turno_dt: null });
  await transition(f.page, 'pageshow'); await transition(f.page, 'pageshow');
  await f.page.evaluate(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { wzStep: 'success', wzRequestId: 77 } })));
  await detailStarted(f, 2);
  resumed.resolve(); await f.page.locator('#sc-os-0').waitFor();
  first.resolve();
  await stays(async () => /Solicitud enviada/.test(await f.page.locator('#svc-title').textContent()) && f.detailCount() === 2 && f.getCount() === 1,
    'the interrupted first read does not replace the resumed one', 60);
  assert.match(await f.page.locator('#svc-title').textContent(), /Solicitud enviada/);
  assert.equal(await f.page.locator('.sc-postcoverage').count(), 1);
  assert.equal(f.detailCount(), 2); assert.equal(f.getCount(), 1);
  await f.page.evaluate(() => window.dispatchEvent(new PopStateEvent('popstate', { state: { wzStep: 'success', wzRequestId: 77 } })));
  await pick(f.page, 'swiss', 'Swiss Medical'); await f.page.locator('[type="submit"]').click(); await saved(f.page);
  assert.equal(saves(f).length, 1);
  assert.equal(bookings(f).length, 0);
});
for (const change of ['expired','changed-during-resume','cancelled']) test(`receipt BFCache ${change}: current authority replaces interrupted success`, async t => {
  const first = deferred(), resumed = deferred();
  t.after(() => { first.resolve(); resumed.resolve(); });
  const f = await fixture(t, { service: true, detailWaits: [first.promise, resumed.promise] });
  await detailStarted(f, 1); await f.served('/api/account/me', 1); await transition(f.page, 'pagehide');
  if (change === 'expired') f.setUser(null);
  if (change === 'cancelled') f.setReceipt({ estado: 'cancelada' });
  await transition(f.page, 'pageshow'); await detailStarted(f, 2);
  // The resumed read asks for the receipt and the account together. Switch the
  // account only after that read's account was answered, so the change lands
  // between it and the confirming account read; switching earlier makes both
  // reads agree on the new account and the receipt is legitimately shown.
  if (change === 'changed-during-resume') { await f.served('/api/account/me', 2); f.setUser({ ...demoUser, dni: '06123456' }); }
  resumed.resolve(); first.resolve();
  await f.page.getByText(/Consultá el estado actual/).waitFor();
  assert.equal(await f.page.locator('.sc-postcoverage').count(), 0);
  assert.doesNotMatch(await f.page.locator('#svc-title').textContent(), /confirmado/);
  assert.equal(bookings(f).length, 0);
});
test('receipt page exit and route change dispose pending BFCache restoration', async t => {
  for (const exit of ['pagehide','route']) {
    const first = deferred(); t.after(() => first.resolve());
    const f = await fixture(t, { service: true, detailWaits: [first.promise] });
    await detailStarted(f, 1);
    if (exit === 'pagehide') await transition(f.page, 'pagehide', false);
    else await f.page.evaluate(() => { const state = { wzStep: 'success', wzRequestId: 77 }; history.pushState(state, '', '/other-route'); window.dispatchEvent(new PopStateEvent('popstate', { state })); });
    await transition(f.page, 'pageshow'); first.resolve();
    await stays(async () => f.detailCount() === 1 && f.getCount() === 0 && await f.page.locator('.sc-postcoverage').count() === 0 && bookings(f).length === 0,
      `a disposed receipt restoration does nothing after ${exit}`, 60);
    assert.equal(f.detailCount(), 1); assert.equal(f.getCount(), 0);
    assert.equal(await f.page.locator('.sc-postcoverage').count(), 0); assert.equal(bookings(f).length, 0);
  }
});
