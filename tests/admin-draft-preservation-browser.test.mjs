import { test, hookOptions, openPage } from './helpers/ceiling.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

// One Chromium per file, a fresh context per test (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
test.after(async () => { await browser?.close(); });

const ORIGIN = 'https://draft-preservation.test';
const settings = { lock: false, location: {}, languages: {}, api_keys: [], chat: {}, turnos: { photo_auto_enhance: true }, seguridad: {}, connectors: {} };
const article = { id: 'first-title', type: 'articulo', title: 'First title', content: 'Synthetic article', jurisdiction: 'interno', status: 'borrador', tags: [] };

async function fixture(t, surface, api) {
  const page = await openPage(browser, { viewport: { width: 1280, height: 900 } });
  t.after(() => page.context().close());
  await page.route(ORIGIN + '/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.startsWith('/static/platform/')) return route.fulfill({ contentType: 'application/javascript', body: readFileSync(new URL('../static/platform/' + path.split('/').at(-1), import.meta.url), 'utf8') });
    if (path.startsWith('/api/')) return api(route, path, request);
    const manifest = Object.fromEntries(['i18n', 'dates', 'admin-session'].map(name => ['platform/' + name + '.mjs', '/static/platform/' + name + '.mjs']));
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="es"><head><script>window.SANA_STATIC=${JSON.stringify(manifest)};window.SANA_I18N={active:'es',catalog:{}};</script></head><body><h1 id="adm-h1"></h1><nav><a href="/admin/settings" data-settings-view="general">General</a><a href="/admin/settings?view=integrations" data-settings-view="integrations">Integrations</a></nav><div id="sec-${surface}"></div><script type="module">const m=await import('/static/platform/admin-${surface}.mjs');${surface === 'knowledge' ? 'await m.mount()' : "await m.mount(document.getElementById('sec-settings')).ready"};window.controllerReady=true;</script></body></html>` });
  });
  await page.goto(ORIGIN + '/admin/' + surface);
  await page.waitForFunction(() => window.controllerReady === true);
  return page;
}

for (const mode of ['typing', 'silent DOM']) {
test('Wiki create keeps its saved identity when newer ' + mode + ' is reopened and saved', async t => {
  let release, requested;
  const held = new Promise(resolve => { release = resolve; });
  const sent = new Promise(resolve => { requested = resolve; });
  t.after(() => release());
  const writes = [];
  const page = await fixture(t, 'knowledge', async (route, path, request) => {
    if (request.method() === 'GET') return route.fulfill({ json: { items: [] } });
    writes.push({ path, method: request.method(), body: request.postDataJSON() });
    if (writes.length === 1) { requested(); await held; }
    return route.fulfill({ json: { ok: true, item: { ...article, ...request.postDataJSON(), id: article.id } } });
  });
  await page.fill('[name=title]', article.title);
  await page.fill('[name=content]', article.content);
  await page.click('.kn-save'); await sent;
  if (mode === 'typing') await page.fill('[name=title]', 'Newer article title');
  else await page.evaluate(() => { document.querySelector('[name=title]').value = 'Newer article title'; });
  await page.evaluate(() => document.activeElement.blur());
  release();
  await page.waitForFunction(() => document.querySelector('[name=id]').value === 'first-title');
  assert.equal(new URL(page.url()).searchParams.get('item'), article.id);
  // Close and reopen through real history/tree navigation, preserving the dirty draft.
  await page.evaluate(() => document.querySelector('[data-kn-close]').click());
  await page.locator('a.kn-node').click();
  assert.equal(await page.inputValue('[name=title]'), 'Newer article title');
  assert.equal(await page.inputValue('[name=id]'), article.id);
  const saved = page.waitForResponse(response => response.request().method() !== 'GET' && response.url().includes('/api/admin/content/knowledge'));
  await page.click('.kn-save'); await saved;
  assert.equal(writes.length, 2);
  assert.equal(writes[1].method, 'PUT');
  assert.equal(writes[1].path, '/api/admin/content/knowledge/' + article.id);
  assert.equal(writes[1].body.title, 'Newer article title');
});
}

for (const malformed of [{ ok: true }, { ok: true, item: { id: 'bad-ack' } }]) {
test('Wiki retains a new article after malformed acknowledgement ' + JSON.stringify(malformed), async t => {
  let writes = 0;
  const page = await fixture(t, 'knowledge', async (route, _path, request) => {
    if (request.method() === 'GET') return route.fulfill({ json: { items: [] } });
    writes++;
    return route.fulfill({ json: writes === 1 ? malformed : { ok: true, item: { ...article, ...request.postDataJSON(), id: article.id } } });
  });
  await page.fill('[name=title]', article.title);
  await page.fill('[name=content]', article.content);
  await page.click('.kn-save');
  await page.waitForFunction(() => document.querySelector('#kn-save-state')?.dataset.state === 'error');
  assert.equal(await page.inputValue('[name=title]'), article.title);
  assert.equal(await page.inputValue('[name=content]'), article.content);
  assert.equal(await page.locator('.kn-save').isEnabled(), true);
  assert.equal(await page.inputValue('[name=id]'), '');
  await page.click('.kn-save');
  await page.waitForFunction(() => document.querySelector('[name=id]')?.value === 'first-title');
  assert.equal(writes, 2);
});
}

test('Settings keeps an unsaved checkbox through section navigation and submits it', async t => {
  const writes = [];
  const page = await fixture(t, 'settings', async (route, _path, request) => {
    if (request.method() === 'GET') return route.fulfill({ json: settings });
    writes.push(request.postDataJSON());
    return route.fulfill({ json: { ok: true, turnos: { photo_auto_enhance: false } } });
  });
  const checkbox = page.locator('[data-key=TURNOS_PHOTO_AUTO_ENHANCE]');
  await checkbox.uncheck();
  await page.locator('[data-settings-view=integrations]').click();
  await page.locator('[data-settings-view=general]').click();
  assert.equal(await checkbox.isChecked(), false);
  await page.locator('.set-guardar[data-grupo=turnos]').click();
  await page.waitForFunction(() => document.querySelector('[data-msg=turnos]').textContent === 'Guardado.');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].valores.TURNOS_PHOTO_AUTO_ENHANCE, '0');
});
