// Browser acceptance for the physical dates.mjs cutover. These journeys prove
// that visible patient/admin dates come from the fingerprinted ES module and
// that the deleted compatibility script/global never participate.
//
// Port of tests/e2e/dates-canonical.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), queue on
//   as before); there is no requireOk()/skip path. Pages come from the runner (a fresh
//   page per test; the two tests are independent, so no serial mode).
// - The admin login is the shared loginAdmin() helper (original: form login, then wait for
//   any /admin/ page except the login); /admin/cola is opened right after, as before.
// - Waits are web-first (`toBeVisible`, `toHaveText`); the expected text is computed by the
//   real dates.mjs/i18n.mjs in the page, unchanged. The «no raw ISO date» check and the
//   runtime contract (no global, module requested, no failures) stay point-in-time reads.
// - Locators: `.wz-seg-opt[data-dia]`, `.wz-day-h`, `.queue-date-label` stay CSS - the
//   `data-dia` attribute is the input of the formatter being verified.
import { test, expect, loginAdmin } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_dates_canonical",
    queue: true,
  },
});

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

async function assertCanonicalRuntime(page, seen) {
  expect(await page.evaluate(() => window["Sana" + "Dates"])).toBeUndefined();
  const manifestUrl = await page.evaluate(() => window.SANA_STATIC?.["platform/dates.mjs"] || "");
  expect(manifestUrl).toMatch(/\/static\/platform\/dates\.[a-f0-9]+\.mjs$/);
  expect(seen.requested.some((path) => /\/static\/platform\/dates\.[a-f0-9]+\.mjs$/.test(path)),
    "the browser must actually request the fingerprinted dates module").toBe(true);
  expect(seen.requested.some((path) => path.endsWith("/dates.js")),
    "the deleted dates.js compatibility script must never be requested").toBe(false);
  expect(seen.failures).toEqual([]);
}

test("patient booking day heading is rendered by dates.mjs", async ({ page }) => {
  const seen = observe(page);
  await page.goto("/service/electrocardiograma-hospital/book?slots=1", {
    waitUntil: "load", timeout: 15_000,
  });
  const option = page.locator(".wz-seg-opt[data-dia]").first();
  await expect(option).toBeVisible({ timeout: 15_000 });
  const diaIso = await option.getAttribute("data-dia");
  const heading = page.locator(".wz-day-h").first();
  await expect(heading).toBeVisible({ timeout: 10_000 });
  const expected = await page.evaluate(async (iso) => {
    const mod = await import(window.SANA_STATIC["platform/dates.mjs"]);
    return mod.diaFechado(iso);
  }, diaIso);
  await expect(heading).toHaveText(expected);
  expect(await heading.innerText()).not.toMatch(/\b\d{4}-\d{2}-\d{2}\b/);
  await assertCanonicalRuntime(page, seen);
});

test("admin day queue label is rendered by dates.mjs", async ({ page, app }) => {
  const seen = observe(page);
  await loginAdmin(page, app.baseUrl);
  await page.goto("/admin/cola", { waitUntil: "load", timeout: 15_000 });
  const label = page.locator(".queue-date-label");
  await expect(label).toBeVisible({ timeout: 10_000 });
  const expected = await page.evaluate(async () => {
    const response = await fetch("/api/admin/day-queue");
    const data = await response.json();
    const mod = await import(window.SANA_STATIC["platform/dates.mjs"]);
    // The short date; «Hoy» lives on its own button (2026-11-08).
    return (mod.cortaFija ? mod.cortaFija(data.dia) : mod.corta(data.dia, "")) || data.dia || "";
  });
  await expect(label).toHaveText(expected);
  expect(await label.innerText()).not.toMatch(/\b\d{4}-\d{2}-\d{2}\b/);
  await assertCanonicalRuntime(page, seen);
});
