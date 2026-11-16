import { before, after } from "node:test";
import { test, ceil, hookOptions } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

// The panel's date field (probador en sitio, 2026-10-21: "el calendario está en
// inglés"). A real Chromium in ENGLISH, the real modules, no server: the
// picker must still speak the page's language, and every reader of `.value`
// (and of FormData) must keep receiving ISO dates.
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
after(async () => { await browser?.close(); });

async function fixture(t) {
  const context = await browser.newContext({ locale: "en-US", viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  ceil(page);
  t.after(() => context.close());
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  const catalog = JSON.parse(readFileSync(new URL("../locales/es/date.json", import.meta.url), "utf8"));
  await page.route("**/*", route => {
    const path = new URL(route.request().url()).pathname;
    if (["/static/platform/date-field.mjs", "/static/platform/i18n.mjs", "/static/platform/calendar-grid.mjs"].includes(path)) {
      return route.fulfill({ contentType: "text/javascript", body: readFileSync(new URL(`..${path}`, import.meta.url), "utf8") });
    }
    if (path === "/panel") return route.fulfill({ contentType: "text/html", body: `<!doctype html>
      <html lang="es"><head><meta charset="utf-8">
      <script>window.SANA_I18N=${JSON.stringify({ active: "es", catalog })};
      window.SANA_STATIC={"platform/i18n.mjs":"/static/platform/i18n.mjs","platform/calendar-grid.mjs":"/static/platform/calendar-grid.mjs"};
      window.changes=[];</script></head>
      <body><div id="rows"><input type="date" id="desde" value="2026-11-01"><input type="time" id="hora" value="20:00"></div>
      <form id="f"><input type="date" name="valid_from" id="named" value="2026-10-05"></form>
      <script type="module">
      import { install } from '/static/platform/date-field.mjs';
      install(document);
      document.addEventListener('change', e => window.changes.push(e.target.value));
      window.ready = true;
      </script></body></html>` });
    return route.fulfill({ status: 404, body: "Outside synthetic test scope" });
  });
  await page.goto("https://panel.test/panel");
  await page.waitForFunction(() => window.ready);
  t.after(() => assert.deepEqual(errors, []));
  return page;
}

test("an English browser still shows DD/MM/AAAA and a Spanish month; .value stays ISO", async t => {
  const page = await fixture(t);
  const desde = page.locator("#desde");
  assert.equal(await desde.getAttribute("type"), "text");
  assert.equal(await desde.inputValue(), "01/11/2026");
  assert.equal(await desde.evaluate(e => e.value), "2026-11-01");
  // The month grid is not fetched with the page, only on the first open.
  assert.equal(await page.evaluate(() => performance.getEntriesByType("resource").some(e => e.name.includes("calendar-grid"))), false);
  await desde.click();
  assert.equal((await page.locator(".df-title").textContent()).toLowerCase(), "noviembre 2026");
  await page.locator('[data-df-date="2026-11-14"]').click();
  assert.equal(await desde.evaluate(e => e.value), "2026-11-14");
  assert.equal(await desde.inputValue(), "14/11/2026");
  assert.deepEqual(await page.evaluate(() => window.changes), ["2026-11-14"]);
  assert.equal(await page.locator(".df-pop").count(), 0);
});

test("typing a date sets ISO; an incomplete one reads empty; a dynamically added field is enhanced", async t => {
  const page = await fixture(t);
  const desde = page.locator("#desde");
  await desde.fill("3/12/2026");
  assert.equal(await desde.evaluate(e => e.value), "2026-12-03");
  await desde.fill("31/02/2026");
  assert.equal(await desde.evaluate(e => e.value), "");
  await page.evaluate(() => { document.getElementById("rows").insertAdjacentHTML("beforeend", '<input type="date" id="nuevo" value="2027-01-02">'); });
  await page.waitForFunction(() => document.getElementById("nuevo").type === "text");
  assert.equal(await page.locator("#nuevo").inputValue(), "02/01/2027");
});

test("an English browser gets a 24-hour typed clock, never AM/PM; .value stays HH:MM", async t => {
  const page = await fixture(t);
  const hora = page.locator("#hora");
  assert.equal(await hora.getAttribute("type"), "text");
  assert.equal(await hora.inputValue(), "20:00");
  await hora.fill("930");
  assert.equal(await hora.evaluate(e => e.value), "09:30");
  await hora.fill("25:00");
  assert.equal(await hora.evaluate(e => e.value), "");
});

test("a named form field stays native: its form addresses and submits it by name", async t => {
  const page = await fixture(t);
  assert.equal(await page.locator("#named").getAttribute("type"), "date");
  assert.equal(await page.locator('[name="valid_from"]').count(), 1);
  assert.equal(await page.evaluate(() => new FormData(document.getElementById("f")).get("valid_from")), "2026-10-05");
});
