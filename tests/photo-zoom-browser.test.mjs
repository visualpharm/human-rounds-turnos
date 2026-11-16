// Real browser execution of the canonical module, including failure/loading
// boundaries. Consumer-specific uploads are covered in turnos-upload-native.
import { test, ceil, hookOptions } from './helpers/ceiling.mjs';
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright';

const source = readFileSync(new URL('../static/platform/photo-zoom.mjs', import.meta.url));
const url = '/static/platform/photo-zoom.' + createHash('sha256').update(source).digest('hex').slice(0, 8) + '.mjs';
const i18n = readFileSync(new URL('../static/platform/i18n.mjs', import.meta.url));
const origin = 'https://photo-viewer.test';
const photo = readFileSync(new URL('../packs/example-town/centro-photos/sampleton-hospital.webp', import.meta.url));
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

function barrier() {
  let release;
  return { promise: new Promise(resolve => { release = resolve; }), release: () => release() };
}

async function pageFor(width, { hold, reject = false, holdI18n, rejectI18n = false } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  ceil(page);
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.context().route(origin + '/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === url) {
      if (hold) { hold.requested.release(); await hold.release.promise; }
      return reject ? route.abort() : route.fulfill({ contentType: 'text/javascript', body: source });
    }
    if (path === '/i18n.mjs') {
      if (holdI18n) { holdI18n.requested.release(); await holdI18n.release.promise; }
      return rejectI18n ? route.abort() : route.fulfill({ contentType: 'text/javascript', body: i18n });
    }
    if (path.endsWith('.png')) return route.fulfill({ contentType: 'image/webp', body: photo });
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="en"><head>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <style>body{font:18px sans-serif}a{display:block;margin:20px}</style>
      <script>window.SANA_STATIC={"platform/i18n.mjs":"/i18n.mjs"};window.SANA_I18N={catalog:{"intake.common.close":"Close","intake.photozoom.prev":"Previous photo","intake.photozoom.next":"Next photo"}};</script>
      </head><body><a id="single" href="/one.png" target="_blank" data-photozoom>Portrait</a>
      <a id="first" href="/one.png" data-photozoom data-photozoom-group="center">First</a>
      <a id="second" href="/two.png" data-photozoom data-photozoom-group="center">Second</a>
      <a id="other" href="/outside.png" data-photozoom data-photozoom-group="other">Other center</a>
      <script type="module" src="${url}"></script></body></html>` });
  });
  const navigation = page.goto(origin, { waitUntil: 'domcontentloaded' });
  if (hold) await hold.requested.promise;
  else await navigation;
  return { page, errors, navigation };
}

for (const width of [360, 390, 768, 1024, 1280]) {
  test(`viewer interaction and lifecycle at ${width}px`, async () => {
    const { page, errors } = await pageFor(width);
    try {
      await page.evaluate(() => { document.documentElement.style.overflow = 'auto'; });
      await page.locator('#single').click();
      await page.locator('.pz-open .pz-img.pz-ready').waitFor();
      assert.equal(await page.locator('.pz-prev').isVisible(), false);
      assert.equal(await page.locator('.pz-counter').isVisible(), false);
      await page.waitForFunction(() => document.querySelector('.pz-close')?.getAttribute('aria-label') === 'Close');
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'single');
      assert.equal(await page.evaluate(() => document.documentElement.style.overflow), 'auto');
      await page.locator('#second').click();
      assert.equal(await page.locator('.pz-counter').innerText(), '2 / 2');
      await page.keyboard.press('ArrowRight');
      assert.equal(await page.locator('.pz-counter').innerText(), '1 / 2');
      await page.locator('.pz-prev').click();
      assert.equal(await page.locator('.pz-counter').innerText(), '2 / 2');
      await page.locator('.pz-stage').evaluate(stage => {
        const start = new Touch({ identifier: 1, target: stage, clientX: 200 });
        const end = new Touch({ identifier: 1, target: stage, clientX: 80 });
        stage.dispatchEvent(new TouchEvent('touchstart', { touches: [start] }));
        stage.dispatchEvent(new TouchEvent('touchend', { changedTouches: [end] }));
      });
      assert.equal(await page.locator('.pz-counter').innerText(), '1 / 2');
      await page.waitForFunction(() => {
        const image = document.querySelector('.pz-img');
        return image?.complete && image.naturalWidth > 100 && getComputedStyle(image).opacity === '1';
      });
      const fit = await page.locator('.pz-overlay').evaluate(el => {
        const nodes = [...el.querySelectorAll('button,img')].filter(x => getComputedStyle(x).display !== 'none');
        return nodes.every(x => { const r=x.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight; });
      });
      assert.equal(fit, true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (process.env.TURNOS_PHOTO_EVIDENCE_DIR) {
        mkdirSync(process.env.TURNOS_PHOTO_EVIDENCE_DIR, { recursive: true });
        await page.screenshot({ path: `${process.env.TURNOS_PHOTO_EVIDENCE_DIR}/gallery-${width}.png` });
      }
      await page.evaluate(async moduleUrl => {
        const m = await import(moduleUrl);
        const old = m.mount();
        if (m.mount() !== old) throw Error('duplicate mount');
        old.open('/two.png');
        old.destroy();
        if (document.documentElement.style.overflow !== 'auto') throw Error('lost scroll state');
        if (old.open('/one.png') !== false) throw Error('destroyed controller reopened');
        m.mount();
      }, url);
      await page.locator('#single').click();
      assert.equal(await page.locator('.pz-overlay').count(), 1);
      await page.locator('.pz-close').click();
      assert.equal(await page.locator('.pz-open').count(), 0);
      assert.equal(await page.evaluate(() => typeof window.PhotoZoom), 'undefined');
      assert.deepEqual(errors, []);
    } finally { await page.close(); }
  });
}

for (const mode of ['held', 'rejected']) {
  test(`${mode} optional i18n does not block a working viewer`, async () => {
    const holdI18n = mode === 'held' ? { requested: barrier(), release: barrier() } : null;
    const { page, errors } = await pageFor(390, { holdI18n, rejectI18n: mode === 'rejected' });
    try {
      if (holdI18n) await holdI18n.requested.promise;
      await page.locator('#single').click();
      await page.locator('.pz-open .pz-img.pz-ready').waitFor();
      assert.equal(await page.locator('.pz-close').getAttribute('aria-label'), 'Cerrar');
      if (holdI18n) {
        await page.evaluate(async u => { (await import(u)).mount().destroy(); }, url);
        holdI18n.release.release();
        await page.evaluate(async u => { (await import(u)).mount(); }, url);
        await page.locator('#single').click();
        await page.waitForFunction(() => document.querySelector('.pz-close')?.getAttribute('aria-label') === 'Close');
        assert.equal(await page.locator('.pz-overlay').count(), 1);
      }
      assert.deepEqual(errors, []);
    } finally { holdI18n?.release.release(); await page.close(); }
  });
  test(`${mode} viewer preserves the actual anchor destination`, async () => {
    const hold = mode === 'held' ? { requested: barrier(), release: barrier() } : null;
    const { page, navigation } = await pageFor(390, { hold, reject: mode === 'rejected' });
    try {
      const popupPromise = page.waitForEvent('popup');
      await page.locator('#single').click();
      const popup = await popupPromise;
      await popup.waitForLoadState();
      assert.equal(popup.url(), origin + '/one.png');
      assert.equal(await page.locator('.pz-overlay').count(), 0);
      await popup.close();
      if (hold) { hold.release.release(); await navigation; }
    } finally { hold?.release.release(); await page.close(); }
  });
}
