import { test, hookOptions, openPage, within } from './helpers/ceiling.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';

// One Chromium per file, a fresh context per test (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
test.after(async () => { await browser?.close(); });

const ORIGIN = 'https://editorial-save.test';
const PHONE = JSON.parse(readFileSync(new URL('../packs/ar/country.json', import.meta.url), 'utf8')).phone;
const legal = { id: 'terms', titulo: 'Terms fixture', pagina: '/terms', cuerpo: 'Original terms', editado: false };
const prompt = { clave: 'test_prompt', titulo: 'Prompt fixture', texto_en: 'Original prompt', texto_es: 'Original', alineado: true, variables: [], tecnico_en: '', historial: [], que_hace: 'Synthetic instruction', donde: '' };

for (const surface of ['legal', 'prompts']) {
 for (const scenario of ['navigate', 'restore', 'remount']) {
  test(`${surface}: pending save owns its ${scenario} state`, async () => {
    const page = await openPage(browser);
    page.on('pageerror', error => console.error(surface, String(error)));
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let requested;
    const sent = new Promise(resolve => { requested = resolve; });
    const item = { ...(surface === 'legal' ? legal : prompt), editado: true };
    const query = surface === 'legal' ? '?doc=terms' : '?prompt=test_prompt';
    const textarea = surface === 'legal' ? '#lg-cuerpo' : '#pr-en';
    const button = surface === 'legal' ? '#lg-guardar' : '#pr-guardar';
    try {
      await page.route(ORIGIN + '/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.startsWith('/static/platform/')) return route.fulfill({ contentType: 'application/javascript', body: readFileSync(new URL('../static/platform/' + path.split('/').at(-1), import.meta.url), 'utf8') });
        if (path === '/api/admin/' + surface && route.request().method() === 'GET') return route.fulfill({ json: { items: [item] } });
        if (path.startsWith('/api/admin/' + surface + '/') && ['PUT', 'POST'].includes(route.request().method())) {
          requested(); await held;
          return route.fulfill({ json: { ok: true, item: { ...item, cuerpo: 'Saved old terms', texto_en: 'Saved old prompt' } } });
        }
        if (path.startsWith('/api/')) return route.fulfill({ json: { items: [], lang: 'en', catalog: {} } });
        const manifest = Object.fromEntries(['format', 'admin-session', 'i18n'].map(name => ['platform/' + name + '.mjs', '/static/platform/' + name + '.mjs']));
        return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="en"><head><script>window.SANA_STATIC=${JSON.stringify(manifest)};window.SANA_COUNTRY_PHONE=${JSON.stringify(PHONE)};window.SANA_I18N={active:'en',catalog:{}};</script></head><body><h1 id="adm-h1"></h1><div id="sec-${surface}"></div><p id="${surface}-status" hidden></p><script type="module">window.editorialModule=await import('/static/platform/admin-${surface}.mjs');await window.editorialModule.mount();</script></body></html>` });
      });
      await page.goto(ORIGIN + '/admin/' + surface + query, { waitUntil: 'domcontentloaded' });
      await page.locator(textarea).waitFor();
      await page.fill(textarea, 'Changed draft');
      await page.click(button);
      await within(sent, 'save request after the click on ' + button);
      if (scenario === 'restore') {
        const restore = surface === 'legal' ? '#lg-restaurar' : '#pr-restaurar';
        assert.equal(await page.locator(restore).isDisabled(), true, 'restore shares the pending mutation lock');
        release();
        return;
      }
      if (scenario === 'remount') {
        await page.evaluate(async () => { window.editorialModule.destroy(); await window.editorialModule.mount(); });
        await page.locator(textarea).waitFor();
        assert.equal(await page.inputValue(textarea), 'Changed draft', 'lifecycle preserves a pending-save draft');
        release();
        return;
      }
      await page.evaluate(surface => { history.pushState({}, '', '/admin/' + surface); dispatchEvent(new PopStateEvent('popstate')); }, surface);
      await page.locator(surface === 'legal' ? '.lg-card' : '.pr-card').waitFor();
      const completed = page.waitForResponse(response => response.request().method() !== 'GET' && response.url().includes('/api/admin/' + surface + '/'));
      release();
      await completed;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.locator(textarea).count(), 0, 'late save must not reopen its abandoned editor');
      assert.equal(await page.locator(surface === 'legal' ? '.lg-card' : '.pr-card').count(), 1);
    } finally { release(); await page.context().close(); }
  });
 }
}
