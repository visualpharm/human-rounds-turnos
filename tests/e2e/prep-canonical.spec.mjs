// Browser acceptance for the physical prep.mjs cutover. These journeys prove
// that both patient and staff surfaces load the fingerprinted ES module and
// that the deleted compatibility script/global never participate.
//
// Port of tests/e2e/prep-canonical.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   requests/account options of the original); there is no requireOk()/skip path. Pages
//   come from the runner (a fresh page per test) and each viewport is set with
//   page.setViewportSize().
// - Evidence screenshots: capture() still writes to TURNOS_PREP_EVIDENCE_DIR only when that
//   variable is set (same file names, full page), now through `saveShot`, which also attaches
//   the picture to the report.
// - The admin login is the shared loginAdmin() helper with next=/admin/preparations.
// - Waits are web-first (`toBeVisible`, `click` auto-waits). The editor-label comparison
//   (labels read once after the editor is up), the viewport-overflow check and the runtime
//   contract (no global, fingerprint derived from the file bytes, no failures) stay
//   point-in-time reads.
// - Locators: `#wz-upzone`, `#sec-preparaciones ...`, `#pa-antes` and `label[for^="pa-"]` stay
//   CSS - the labels' `for` ids are what ties the visible sections to prep.mjs.
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { test, expect, loginAdmin, saveShot, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_prep_canonical",
    requests: true,
    account: true,
  },
});

const EVIDENCE_DIR = process.env.TURNOS_PREP_EVIDENCE_DIR || "";
const prepBytes = fs.readFileSync(new URL("../../static/platform/prep.mjs", import.meta.url));
const prepUrl = `/static/platform/prep.${createHash("sha256").update(prepBytes).digest("hex").slice(0, 8)}.mjs`;

async function capture(testInfo, page, name) {
  if (!EVIDENCE_DIR) return;
  await saveShot(testInfo, page, path.join(EVIDENCE_DIR, name), { fullPage: true });
}

function observe(page) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/login-panel/event" && request.failure()?.errorText === "net::ERR_ABORTED") return;
    failures.push({ pathname, error: request.failure()?.errorText || "request failed" });
  });
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname;
    if (pathname === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ pathname, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested };
}

async function assertCanonicalRuntime(page, seen) {
  expect(await page.evaluate(() => window.SanaPrep)).toBeUndefined();
  const manifestUrl = await page.evaluate(() => window.SANA_STATIC?.["platform/prep.mjs"] || "");
  expect(manifestUrl, "manifest fingerprint is derived from the actual canonical bytes").toBe(prepUrl);
  expect(seen.requested.includes(prepUrl),
    "the browser must actually request the fingerprinted preparation module").toBe(true);
  expect(seen.requested.some((pathname) => pathname.endsWith("/prep.js")),
    "the deleted prep.js compatibility script must never be requested").toBe(false);
  expect(seen.requested.some((pathname) => /^\/(?:static\/)?prep(?:\.[a-f0-9]+)?\.mjs$/.test(pathname)),
    "a retired root module or root-fingerprinted alias must never participate").toBe(false);
  expect(seen.failures).toEqual([]);
}

async function assertFits(page) {
  const result = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  expect(result.overflow, `page overflows viewport ${result.viewport}px by ${result.overflow}px`).toBeLessThanOrEqual(1);
}

test("patient referral upload boots through canonical prep.mjs at 360px", async ({ page }, testInfo) => {
  const seen = observe(page);
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/upload-order", { waitUntil: "load", timeout: 15_000 });
  await expect(page.locator("#wz-upzone")).toBeVisible({ timeout: 10_000 });
  await assertCanonicalRuntime(page, seen);
  await assertFits(page);
  await capture(testInfo, page, "patient-upload-360.png");
});

test("staff preparation editor gets its visible sections from prep.mjs", async ({ page, app }, testInfo) => {
  const seen = observe(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/preparations");
  const first = page.locator('#sec-preparaciones a[href^="/admin/preparations?item="]').first();
  await expect(first).toBeVisible({ timeout: 10_000 });
  await first.click();
  await expect(page.locator("#pa-antes")).toBeVisible({ timeout: 10_000 });
  const expected = await page.evaluate(async () => {
    const mod = await import(window.SANA_STATIC["platform/prep.mjs"]);
    return mod.SECCIONES.map((section) => section[1]);
  });
  const labels = await page.locator('#sec-preparaciones label[for^="pa-"]').allTextContents();
  for (const title of expected) {
    expect(labels.some((label) => label.includes(title)), `visible editor must include ${title}`).toBe(true);
  }
  await assertCanonicalRuntime(page, seen);
  await assertFits(page);
  await capture(testInfo, page, "staff-preparation-editor-1280.png");
});
