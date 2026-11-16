// Browser acceptance for the canonical hours.mjs migration. These journeys
// exercise all three browser consumers that previously depended on the
// the former schedule compatibility global: public listings, weekly schedule
// grids, and the admin catalog preview.
//
// Port of tests/e2e/hours-canonical.e2e.mjs (3 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. Pages come from the runner (a
//   fresh page per test; the tests are independent, so no serial mode).
// - The admin login is the shared loginAdmin() helper (original: form login, then wait for
//   any /admin/ page except the login); the editor is opened right after, as before.
// - Waits are web-first: the «Atiende ahora / Abre ...» wait is `toContainText` on the same
//   result row (the original polled a function over the first row with that name), the
//   preview wait is `toHaveText`; the weekly grid texts and the old-global/manifest/
//   failures checks stay point-in-time reads.
// - Locators: `#q`, `#resultados tr`, `[data-semana] .sem-grid`, `#svc-horario*` stay CSS -
//   they are the markup of the three consumers the migration is about.
import { test, expect, loginAdmin } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_hours_canonical" } });

const WALKIN_ID = "taller-gimnasia-lakeview-clinic";
const WALKIN_NAME = "Taller de gimnasia para adultos mayores";

function captureFailures(page) {
  const failures = [];
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    // Login telemetry uses sendBeacon during navigation; Chromium reports an
    // expected aborted request when the next direct screen wins the race.
    if (path === "/api/login-panel/event" && request.failure()?.errorText === "net::ERR_ABORTED") return;
    failures.push({ path, error: request.failure()?.errorText || "request failed" });
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    // Anonymous patient pages probe for an optional account. A 401 means
    // "visitor", not a broken page.
    if (path === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) {
      failures.push({ path, status: response.status() });
    }
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return failures;
}

test("public listing enriches a walk-in schedule through hours.mjs", async ({ page }) => {
  const failures = captureFailures(page);
  await page.goto("/", { waitUntil: "load", timeout: 15_000 });
  await page.fill("#q", WALKIN_NAME);
  const result = page.locator("#resultados tr").filter({ hasText: WALKIN_NAME }).first();
  await expect(result).toBeVisible({ timeout: 15_000 });
  await expect(result).toContainText(/Atiende ahora|Abre (?:hoy|mañana|el )/i, { timeout: 10_000 });
  expect(await page.evaluate(() => window["Sana" + "Schedules"])).toBeUndefined();
  expect(await page.evaluate(() => window.SANA_STATIC?.["platform/hours.mjs"] || "")).toMatch(/\/static\/platform\/hours\.[a-f0-9]+\.mjs$/);
  expect(failures).toEqual([]);
});

test("service schedule paints a real weekly grid without the old global", async ({ page }) => {
  const failures = captureFailures(page);
  await page.goto(`/service/${WALKIN_ID}`, { waitUntil: "load", timeout: 15_000 });
  const grid = page.locator("[data-semana] .sem-grid");
  await expect(grid).toBeVisible({ timeout: 10_000 });
  expect(await grid.innerText()).toMatch(/Miércoles/i);
  expect(await grid.innerText()).toMatch(/13:00/);
  expect(await page.evaluate(() => window["Sana" + "Schedules"])).toBeUndefined();
  expect(failures).toEqual([]);
});

test("admin service editor previews a typed schedule through hours.mjs", async ({ page, app }) => {
  const failures = captureFailures(page);
  await loginAdmin(page, app.baseUrl);
  await page.goto(`/admin/services?item=${WALKIN_ID}`, {
    waitUntil: "load",
    timeout: 15_000,
  });
  const input = page.locator("#svc-horario");
  await expect(input).toBeVisible({ timeout: 10_000 });
  await input.fill("Lunes a viernes de 8 a 12 h");
  const preview = page.locator("#svc-horario-preview");
  await expect(preview).toHaveText(/^Vista previa:/, { timeout: 10_000 });
  expect(await preview.innerText()).toMatch(/^Vista previa:/);
  expect(await page.evaluate(() => window["Sana" + "Schedules"])).toBeUndefined();
  expect(failures).toEqual([]);
});
