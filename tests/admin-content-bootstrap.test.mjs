import { test, ceil } from './helpers/ceiling.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright';
import { withScreenBootInline } from './screen-boot-fixture.mjs';

// Execute the template's actual bootstrap in Chromium. The controller fixture
// isolates document ownership; actual catalog behavior has separate runtime tests.
const template = readFileSync(new URL('../templates/admin/content.html', import.meta.url), 'utf8');
const bootstrap = withScreenBootInline(template.match(/<script type="module">([\s\S]*?)<\/script>/)[1]);
const recovery = template.match(/<template id="catalog-load-recovery">([\s\S]*?)<\/template>/)[1]
  .replace(/\{\{ t\('admin.system.common.load_error',[\s\S]*?\) \}\}/, "Couldn't load this section.")
  .replace(/\{\{ t\('admin.content.common.reload',[\s\S]*?\) \}\}/, 'Reload');
const dataURL = source => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const controller = dataURL(`export function mount() {
  window.mounts++; let stopped = false;
  function dispose() { if (!stopped) { stopped = true; window.disposals++; } }
  dispose.loaded = window.loadedGate; dispose.ready = window.readyGate; return dispose;
}`);

test('catalog document bootstrap owns readiness, recovery and persisted-page replacement', async t => {
  // Close is registered before the launch resolves: a test timeout during a
  // slow launch must still close the late browser, or the file never exits.
  const launching = chromium.launch({ headless: true });
  t.after(async () => (await launching).close());
  const browser = await launching;
  async function fixture(url = controller) {
    const page = ceil(await browser.newPage());
    await page.clock.install();
    await page.setContent(`<div id="sec-centros">Preserved content</div><p id="content-status" hidden></p>
      <template id="catalog-load-recovery">${recovery}</template>`);
    await page.evaluate(() => {
      window.mounts = 0; window.disposals = 0;
      window.readyGate = new Promise((resolve, reject) => { window.resolveReady = resolve; window.rejectReady = reject; });
      window.loadedGate = new Promise(resolve => { window.resolveLoaded = resolve; });
    });
    await page.addScriptTag({ type: 'module', content: bootstrap
      .replaceAll('{{ editor.id }}', 'centros')
      .replace("{{ static('platform/admin-content.mjs') }}", url) });
    return page;
  }
  await t.test('busy state lasts until controller readiness; persisted pages remount', async () => {
    const page = await fixture();
    try {
      assert.equal(await page.locator('#sec-centros').getAttribute('aria-busy'), 'true');
      await page.evaluate(() => window.resolveReady());
      await page.waitForFunction(() => !document.querySelector('#sec-centros').hasAttribute('aria-busy'));
      await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
      assert.equal(await page.evaluate(() => window.disposals), 1);
      await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
      await page.waitForFunction(() => window.mounts === 2);
      assert.equal(await page.locator('#content-status').isVisible(), false);
    } finally { await page.close(); }
  });
  await t.test('the deadline says the load is late and keeps it; late readiness clears the copy', async () => {
    const page = await fixture();
    try {
      await page.waitForFunction(() => window.mounts === 1);
      await page.clock.fastForward(8001);
      assert.equal(await page.locator('#content-status [data-retry]').isVisible(), true);
      assert.equal(await page.evaluate(() => window.disposals), 0, 'the load is not abandoned');
      assert.equal(await page.locator('#sec-centros').textContent(), 'Preserved content');
      await page.evaluate(() => window.resolveReady());
      await page.waitForFunction(() => !document.querySelector('#sec-centros').hasAttribute('aria-busy'));
      assert.equal(await page.locator('#content-status [data-retry]').count(), 0);
      assert.equal(await page.locator('#content-status').isVisible(), false);
      assert.equal(await page.evaluate(() => window.mounts), 1);
    } finally { await page.close(); }
  });
  await t.test('a first read slower than the deadline is waiting, not failure, once the dependencies are in', async () => {
    const page = await fixture();
    try {
      await page.waitForFunction(() => window.mounts === 1);
      await page.evaluate(() => window.resolveLoaded(true));
      await page.clock.fastForward(30000);
      assert.equal(await page.locator('#content-status [data-retry]').count(), 0);
      assert.equal(await page.evaluate(() => window.disposals), 0);
      assert.equal(await page.locator('#sec-centros').getAttribute('aria-busy'), 'true');
      await page.evaluate(() => window.resolveReady());
      await page.waitForFunction(() => !document.querySelector('#sec-centros').hasAttribute('aria-busy'));
      assert.equal(await page.locator('#content-status [data-retry]').count(), 0);
    } finally { await page.close(); }
  });
  await t.test('import failure presents bounded recovery without erasing content', async () => {
    const page = await fixture(dataURL('throw new Error("synthetic import failure")'));
    try {
      await page.locator('#content-status [data-retry]').waitFor();
      assert.equal(await page.locator('#sec-centros').textContent(), 'Preserved content');
      assert.equal(await page.evaluate(() => window.mounts), 0);
      assert.equal(await page.locator('#sec-centros').getAttribute('aria-busy'), null);
    } finally { await page.close(); }
  });
  await t.test('late readiness rejection cannot reopen a departed page', async () => {
    const page = await fixture();
    try {
      // The page must have mounted before it departs: a pagehide that beats the
      // module import (a loaded machine) correctly never mounts, so there is
      // nothing to dispose and `disposals` would stay 0.
      await page.waitForFunction(() => window.mounts === 1);
      await page.evaluate(() => {
        window.dispatchEvent(new PageTransitionEvent('pagehide'));
        window.rejectReady(new Error('late failure'));
      });
      await page.clock.fastForward(8001);
      assert.equal(await page.locator('#content-status').isVisible(), false);
      assert.equal(await page.evaluate(() => window.disposals), 1);
    } finally { await page.close(); }
  });
});
