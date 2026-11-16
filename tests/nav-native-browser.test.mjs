import { test, ceil, hookOptions } from './helpers/ceiling.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright';

const source = readFileSync(new URL('../static/platform/nav.mjs', import.meta.url));
const moduleUrl = '/static/platform/nav.' + createHash('sha256').update(source).digest('hex').slice(0, 8) + '.mjs';
const i18n = readFileSync(new URL('../static/platform/i18n.mjs', import.meta.url));
const photo = readFileSync(new URL('../packs/example-town/centro-photos/sampleton-hospital.webp', import.meta.url));
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

function barrier() {
  let release;
  return { promise: new Promise(resolve => { release = resolve; }), release: () => release() };
}

async function fixture({ delayed, rejected = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  ceil(page);
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    if (path === moduleUrl) return route.fulfill({ contentType: 'text/javascript', body: source });
    if (path === '/i18n.mjs') {
      if (delayed) { delayed.requested.release(); await delayed.response.promise; }
      return rejected ? route.abort() : route.fulfill({ contentType: 'text/javascript', body: i18n });
    }
    if (path === '/api/account/me') return route.fulfill({ json: { usuario: { nombre: 'Synthetic', apellido: 'Patient' } } });
    if (path === '/api/ai-status') return route.fulfill({ json: { disponible: true } });
    if (path === '/api/account/config') return route.fulfill({ json: { auth_mode: 'password' } });
    if (path === '/api/account/photo') {
      if (new URL(route.request().url()).searchParams.has('probe')) return route.fulfill({ json: { source: 'upload' } });
      return route.fulfill({ contentType: 'image/webp', body: photo });
    }
    if (path === '/account') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="es"><head>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <script>window.SANA_STATIC={"platform/i18n.mjs":"/i18n.mjs"};window.SANA_I18N={lang:"es",catalog:{}};
      localStorage.setItem('st_nav',JSON.stringify({loggedIn:true,iniciales:'SP',authMode:'password'}));</script>
      </head><body><header id="stznav" class="external-owner"></header><main><p id="emergency">Call 107</p></main>
      <script type="module">import * as nav from '${moduleUrl}';window.navModule=nav;</script></body></html>` });
    return route.fulfill({ status: 404, body: 'Outside synthetic test scope' });
  });
  await page.goto('https://native-nav.test/account');
  await page.waitForFunction(() => Boolean(window.navModule));
  return { page, errors, requests };
}

test('actual native header suspends, restores once, refreshes the avatar, and disposes terminally', async () => {
  const { page, errors, requests } = await fixture();
  try {
    await page.waitForFunction(() => {
      const image = document.querySelector('.stznav-cuenta-av:not(.noimg) img');
      return image?.complete && image.naturalWidth > 0;
    });
    assert.equal(await page.locator('#emergency a').getAttribute('href'), 'tel:107');
    await page.evaluate(() => { localStorage.setItem('st_photo_v', 'old-test-version'); window.navModule.refreshAvatar(); });
    await page.waitForFunction(() => document.querySelector('.stznav-cuenta-av img')?.getAttribute('src')?.includes(localStorage.getItem('st_photo_v')));
    const version = await page.evaluate(() => localStorage.getItem('st_photo_v'));
    assert.notEqual(version, 'old-test-version');
    const before = requests.filter(p => p === '/api/account/me').length;
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    assert.equal(await page.locator('#stznav').innerHTML(), '');
    assert.equal(await page.locator('#stznav').getAttribute('class'), 'external-owner');
    await page.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    });
    await page.waitForFunction(() => {
      const image = document.querySelector('.stznav-cuenta-av:not(.noimg) img');
      return image?.complete && image.naturalWidth > 0;
    });
    assert.equal(requests.filter(p => p === '/api/account/me').length, before + 1);
    const burger = page.locator('.stznav-burger');
    await burger.click();
    assert.equal(await burger.getAttribute('aria-expanded'), 'true');
    await page.keyboard.press('Escape');
    assert.equal(await burger.getAttribute('aria-expanded'), 'false');
    await page.evaluate(async () => {
      const owner = window.navModule.mount();
      await owner.ready;
      owner.dispose();
      window.navModule.refreshAvatar();
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
      await Promise.resolve();
    });
    assert.equal(await page.locator('#stznav').innerHTML(), '');
    assert.equal(await page.evaluate(() => typeof window.SiteNav), 'undefined');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});

test('delayed real i18n cannot mount a hidden document, then restored navigation becomes usable', async () => {
  const delayed = { requested: barrier(), response: barrier() };
  const { page, errors, requests } = await fixture({ delayed });
  try {
    await delayed.requested.promise;
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    delayed.response.release();
    await page.evaluate(async () => { await import('/i18n.mjs'); await Promise.resolve(); });
    assert.equal(await page.locator('#stznav').innerHTML(), '');
    assert.equal(requests.filter(p => p === '/api/account/me').length, 0);
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await page.locator('.stznav-brand').waitFor();
    assert.equal(await page.locator('.stznav-brand').getAttribute('href'), '/');
    assert.equal(await page.locator('#emergency a').getAttribute('href'), 'tel:107');
    assert.deepEqual(errors, []);
  } finally { delayed.response.release(); await page.close(); }
});

test('rejected i18n retains usable fallback navigation and emergency link', async () => {
  const { page, errors } = await fixture({ rejected: true });
  try {
    await page.locator('.stznav-brand').waitFor();
    assert.equal(await page.locator('a[data-nav-key="medicos"]').getAttribute('href'), '/doctors');
    assert.equal(await page.locator('#emergency a').getAttribute('href'), 'tel:107');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
});
