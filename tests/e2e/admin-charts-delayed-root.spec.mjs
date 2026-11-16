// Independent acceptance: real report boot must wait for both native dependencies.
//
// Port of tests/e2e/admin-charts-delayed-root.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `requests: true`); the runner's page replaces `browser.newPage()` (390x844 via test.use,
//   then 1280x900 for the reload as before). The login stays a local helper because the shared
//   loginAdmin() always pins `?next=`; this one lands wherever the panel sends the account.
// - The hand-made hold promise is holdGate(cleanup): it is released on cleanup too, so a
//   failing test never leaves the route hanging. `route.continue()` tolerates a page closed
//   meanwhile.
// - «Was the held module requested within 8 s» is expect.poll on a flag set by the route
//   handler (same 8 s) instead of a Promise.race against a timer.
// - «Report data is not fetched while a dependency is held» keeps the original two-frame
//   wait and then holds the same checks (no report request, no tile, no page error) for a
//   further 300 ms window with holdsFor(): the absence over time is the claim.
// - Waits are web-first with the original timeouts; snapshots of races (typeof the retired
//   global, overflow, heading text, caption text, request/failure lists) stay point-in-time.
// - The evidence screenshots keep their directory
//   (~/.claude/artifacts/human-rounds/astra-charts-delayed-root/) via saveShot(), which also
//   attaches them to the report.
// - Locators left as CSS: `#rep-body .sana-tile`, `.sana-chart-read`, `#adm-h1` - class/id
//   hooks of the report body whose absence while held is the subject.
import os from "node:os";
import path from "node:path";
import { test, expect, holdGate, holdsFor, saveShot, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_charts_delayed_root", requests: true },
  viewport: { width: 390, height: 844 },
});

async function loginEnglish(page, app) {
  await page.goto(app.baseUrl + "/admin/login", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 10000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/(?!login)/, { waitUntil: "commit", timeout: 10000 });
  const response = await page.request.post("/api/admin/settings", { data: {
    grupo: "languages", valores: {
      LANGUAGES: "es,en", LANG_DEFAULT: "en", LANG_SWITCHER_MODE: "secret",
    },
  } });
  expect(response.status()).toBe(200);
}

for (const held of ["admin-charts", "i18n"]) {
  test(`reports await delayed native ${held} before requesting data and render English charts after release`, async ({ page, app, cleanup }, testInfo) => {
    const gate = holdGate(cleanup);
    const failures = [];
    const reportRequests = [];
    await loginEnglish(page, app);
    // Login may restore the previous Reports screen, whose asynchronous
    // boot can still issue requests. Detach that authenticated document
    // before observing the separately held cold-load journey below.
    await page.goto("about:blank");
    let requested = false;
    await page.route(`**/static/platform/${held}.*.mjs`, async (route) => {
      requested = true;
      await gate.held;
      await route.continue().catch(() => {});
    });
    page.on("pageerror", (error) => failures.push(String(error)));
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/admin/reports")) reportRequests.push(request.url());
    });
    await page.goto(app.baseUrl + "/admin/reportes?view=gestion", { waitUntil: "domcontentloaded", timeout: 20000 });
    await expect.poll(() => requested, {
      timeout: 8000, message: `native ${held} module was never requested`,
    }).toBe(true);
    expect(await page.evaluate(() => typeof window.sanaRouteReportes)).toBe("undefined");
    // Let the installed controller execute across browser frames while the
    // real dependency response stays withheld; never infer this from source.
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await holdsFor(async () => {
      expect(reportRequests.length, "report data must not be fetched before dependencies resolve").toBe(0);
      expect(await page.locator("#rep-body .sana-tile").count()).toBe(0);
      expect(failures).toEqual([]);
    }, { during: 300 });
    gate.release();
    await expect(page.locator("#rep-body .sana-tile").first()).toBeVisible({ timeout: 15000 });
    expect(reportRequests.length, "real report API must be requested after release").toBeGreaterThan(0);
    expect((await page.locator("#adm-h1").innerText()).trim()).toBe("Reports");
    expect(await page.locator("#rep-body .sana-chart-read").first().innerText()).toMatch(/^How to read it:/);
    const proof = await page.evaluate(() => ({
      legacy: typeof window.SanaCharts,
      charts: window.SANA_STATIC["platform/admin-charts.mjs"],
      overflow: document.documentElement.scrollWidth > innerWidth,
    }));
    expect(proof.legacy).toBe("undefined");
    expect(proof.charts).toMatch(/\/static\/platform\/admin-charts\.[a-f0-9]{8}\.mjs$/);
    expect(proof.overflow).toBe(false);
    expect(failures).toEqual([]);
    const evidence = path.join(os.homedir(), ".claude", "artifacts", "human-rounds", "astra-charts-delayed-root");
    await saveShot(testInfo, page, `${evidence}/${held}-390.png`, { fullPage: true });
    await page.unrouteAll({ behavior: "wait" });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.reload({ waitUntil: "load", timeout: 15000 });
    await expect(page.locator("#rep-body .sana-tile").first()).toBeVisible({ timeout: 15000 });
    expect((await page.locator("#adm-h1").innerText()).trim()).toBe("Reports");
    expect(await page.locator("#rep-body .sana-chart-read").first().innerText()).toMatch(/^How to read it:/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    expect(failures).toEqual([]);
    await saveShot(testInfo, page, `${evidence}/${held}-1280.png`, { fullPage: true });
  });
}
