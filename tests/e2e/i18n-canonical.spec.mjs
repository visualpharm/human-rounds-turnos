// Browser acceptance for the physical platform/i18n.mjs cutover. Visible
// patient strings come from the fingerprinted ES module; the deleted
// i18n.js / window.SanaI18n global never participate.
//
// Port of tests/e2e/i18n-canonical.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })) and the
//   page from the runner; there is no requireOk()/skip path - a missing prerequisite
//   fails the run. Both tests are independent (a fresh page each), so no serial mode.
// - openNavIfBurger() waited a fixed 600 ms for the nav's first measurement to settle.
//   That sleep is now the state it stood for: document.fonts.ready (nav.mjs re-measures
//   when the font settles). The burger is then opened exactly as before, retried
//   (`toPass`) only if a late re-measure folds it again, and `#stznav.open` (3 s) is a
//   web-first expectation. (network idle is not usable: the home page never goes idle.)
// - Waits are web-first (`expect(locator)...`); the one-shot probes (window.SanaI18n, the
//   shipped catalog, the requested-paths list) stay point-in-time reads.
// - Locators: the CSS ones are kept on purpose - `#stznav` / `.stznav-*` / `.stznav-menu a`
//   are the markup contract of the shared nav under test, not user-facing labels.
import { test, expect } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_i18n_canonical",
    env: { LANGUAGES: "es,en" },
  },
});

// The Spanish menu plus the language switcher does not fit beside the brand at
// the default 1280px, so the shared nav lays out as a burger. This file tests
// the i18n module, not the layout: open the menu when it is a burger.
async function openNavIfBurger(page) {
  const nav = page.locator("#stznav");
  await expect(nav.locator(".stznav-inner")).toBeVisible({ timeout: 10_000 });
  // nav.mjs measures once at start and again when the font settles; wait for that
  // state instead of a fixed pause before reading which layout it ended up in.
  await page.evaluate(() => document.fonts.ready);
  const burger = page.locator(".stznav-burger");
  if (await burger.isVisible()) {
    // Open it once; if a late re-measure folds it again, open it again (never toggle
    // an already open menu closed).
    await expect(async () => {
      if (!(await nav.evaluate((node) => node.classList.contains("open")))) await burger.click();
      await expect(nav).toHaveClass(/\bopen\b/, { timeout: 3_000 });
    }).toPass({ timeout: 10_000 });
  }
}

function observe(page) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/login-panel/event" && request.failure()?.errorText === "net::ERR_ABORTED") return;
    failures.push({ path, error: request.failure()?.errorText || "request failed" });
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ path, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested };
}

test("Spanish home has no SanaI18n global and requests fingerprinted platform/i18n.mjs", async ({ page }) => {
  const seen = observe(page);
  await page.goto("/", { waitUntil: "load", timeout: 20_000 });
  await openNavIfBurger(page);
  await expect(page.locator(".stznav-menu a").first()).toBeVisible({ timeout: 10_000 });
  expect(await page.evaluate(() => window.SanaI18n)).toBeUndefined();
  const manifestUrl = await page.evaluate(() => window.SANA_STATIC?.["platform/i18n.mjs"] || "");
  expect(manifestUrl).toMatch(/\/static\/platform\/i18n\.[a-f0-9]+\.mjs$/);
  expect(seen.requested.some((path) => /\/static\/platform\/i18n\.[a-f0-9]+\.mjs$/.test(path)),
    "the browser must request the fingerprinted i18n module").toBe(true);
  expect(seen.requested.some((path) => path.endsWith("/i18n.js") || /\/i18n\.[a-f0-9]+\.js$/.test(path)),
    "the deleted i18n.js classic script must never be requested").toBe(false);
  const catalog = await page.evaluate(() => (window.SANA_I18N && window.SANA_I18N.catalog) || {});
  expect(Object.keys(catalog).length, "canonical Spanish pages ship no catalog").toBe(0);
  const navText = await page.locator("#stznav").innerText();
  expect(navText).toMatch(/Médicos|Ingresar|Estudios/);
  expect(navText).not.toMatch(/\bnav\.[a-z_.]+\b/);
  expect(seen.failures).toEqual([]);
});

test("English home renders translated nav through the native module", async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: "lang", value: "en", url: baseURL }]);
  await page.goto("/", { waitUntil: "load", timeout: 20_000 });
  await expect(page.locator(".stznav-menu a").first()).toBeVisible({ timeout: 10_000 });
  const navText = await page.locator("#stznav").innerText();
  expect(navText).toMatch(/Doctors|Sign in|Studies/);
  expect(navText).not.toMatch(/Médicos/);
  expect(navText).not.toMatch(/\bnav\.[a-z_.]+\b/);
  expect(await page.evaluate(() => window.SanaI18n)).toBeUndefined();
});
