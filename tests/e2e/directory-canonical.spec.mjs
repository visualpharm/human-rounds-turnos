// Browser acceptance for the physical directory.mjs cutover. Both responsive
// home layouts must render through the fingerprinted module without the
// deleted classic script or compatibility global.
//
// Port of tests/e2e/directory-canonical.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. Each width gets a fresh runner page
//   whose viewport is set with page.setViewportSize() before the first goto.
// - Evidence screenshots: still written to TURNOS_DIRECTORY_EVIDENCE_DIR only when that
//   variable is set (same file names, full page), now through `saveShot`, which also attaches
//   the picture to the report.
// - The wait for the first directory entry is web-first (`toBeVisible`, 10 s as before); the
//   entry count, the no-global/module-requested checks, the viewport-overflow check and the
//   failures list stay point-in-time reads.
// - Locators: `#directorio-host .dir-card, #directorio-host .dir-row` stay CSS - those are the
//   two responsive layouts of the module under test.
import path from "node:path";
import { test, expect, saveShot } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_directory_canonical" } });

const EVIDENCE_DIR = process.env.TURNOS_DIRECTORY_EVIDENCE_DIR || "";

for (const width of [390, 1280]) {
  test(`home directory uses canonical directory.mjs at ${width}px`, async ({ page }, testInfo) => {
    const failures = [];
    const requested = [];
    page.on("request", (request) => requested.push(new URL(request.url()).pathname));
    page.on("requestfailed", (request) => failures.push({
      pathname: new URL(request.url()).pathname,
      error: request.failure()?.errorText || "request failed",
    }));
    page.on("response", (response) => {
      const pathname = new URL(response.url()).pathname;
      if (pathname === "/api/account/me" && response.status() === 401) return;
      if (response.status() >= 400) failures.push({ pathname, status: response.status() });
    });
    page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/", { waitUntil: "load", timeout: 15_000 });
    const entries = page.locator("#directorio-host .dir-card, #directorio-host .dir-row");
    await expect(entries.first()).toBeVisible({ timeout: 10_000 });
    expect(await entries.count()).toBeGreaterThan(0);
    expect(await page.evaluate(() => window.SanaDirectory)).toBeUndefined();
    expect(requested.some((pathname) => /\/static\/platform\/directory\.[a-f0-9]{8}\.mjs$/.test(pathname))).toBe(true);
    expect(requested.some((pathname) => pathname.endsWith("/directory.js"))).toBe(false);
    const fit = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    expect(fit.overflow, `page overflows ${fit.viewport}px viewport by ${fit.overflow}px`).toBeLessThanOrEqual(1);
    expect(failures).toEqual([]);
    if (EVIDENCE_DIR) {
      await saveShot(testInfo, page, path.join(EVIDENCE_DIR, `home-directory-${width}.png`), { fullPage: true });
    }
  });
}
