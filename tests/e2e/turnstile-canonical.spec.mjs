// Browser acceptance for the canonical Turnstile module: the upload and booking
// wizards, the admin login and the account sign-in all load the fingerprinted
// ES module (never the retired classic script or global), fit their viewport and,
// for the logins, mount the shared interaction-only widget.
//
// Port of tests/e2e/turnstile-canonical.e2e.mjs (4 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. Each journey gets a fresh runner
//   page whose viewport (390x844 or 1280x900) is set with page.setViewportSize() before the
//   first goto.
// - Waits are web-first (`toBeVisible` 10 s). The wait for the module's resource entry
//   (`waitForFunction` over the performance timeline, 10 s, failure deliberately swallowed
//   and reported by the `requested` assertion right after) and the wait for the synthetic
//   widget's options are kept as waits for that state. The count of `#sc-nombre`, the
//   global/manifest checks, the request/error lists and the overflow check stay
//   point-in-time reads.
// - Locators: `#wz-upzone`, `#gate-btn`, `#sc-*`, `[data-interactive-challenge]` stay CSS -
//   the ids are what the shared widget mounts into.
import { test, expect } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_turnstile_canonical" } });

async function assertCanonical(page, path, ready) {
  const requested = [];
  const errors = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(path, { waitUntil: "load", timeout: 15_000 });
  await expect(page.locator(ready)).toBeVisible({ timeout: 10_000 });

  if (path === "/account" && await page.locator("#sc-nombre").count()) await page.locator("#sc-swap").click();
  expect(await page.evaluate(() => window.SanaTurnstile)).toBeUndefined();
  const moduleUrl = await page.evaluate(() => window.SANA_STATIC?.["platform/turnstile.mjs"] || "");
  expect(moduleUrl).toMatch(/\/static\/platform\/turnstile\.[a-f0-9]{8}\.mjs$/);
  // The wizard imports the module on its own schedule (after its first
  // paint), so sampling right after the ready locator can be one tick early:
  // wait for the real request, never assume it already happened.
  await page.waitForFunction(
    () => performance.getEntriesByType("resource").some(
      (entry) => /\/static\/platform\/turnstile\.[a-f0-9]{8}\.mjs$/.test(new URL(entry.name).pathname)),
    null, { timeout: 10_000 },
  ).catch(() => {});
  expect(requested.some((pathname) => /\/static\/platform\/turnstile\.[a-f0-9]{8}\.mjs$/.test(pathname)),
    "the page must request the canonical fingerprinted Turnstile module").toBe(true);
  expect(requested.some((pathname) => pathname.endsWith("/turnstile.js")),
    "the retired classic Turnstile script must never be requested").toBe(false);
  expect(errors).toEqual([]);

  const fit = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  expect(fit.overflow, `page overflows ${fit.width}px viewport by ${fit.overflow}px`).toBeLessThanOrEqual(1);
}

test("order upload uses canonical Turnstile at 390px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await assertCanonical(page, "/upload-order", "#wz-upzone");
});

test("referral booking uses canonical Turnstile at 1280px", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await assertCanonical(page, "/service/electrocardiograma-hospital/book", "#wz-upzone");
});

for (const [path, ready] of [["/admin/login", "#gate-btn"], ["/account", "#sc-submit"]]) {
  test(`login uses the shared interaction-only widget: ${path}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/admin/config", (route) => route.fulfill({ json: { turnstile_sitekey: "synthetic-key" } }));
    await page.route("**/api/account/config", (route) => route.fulfill({ json: { turnstile_sitekey: "synthetic-key", auth_mode: "password" } }));
    await page.addInitScript(() => {
      window.turnstile = {
        render(box, options) {
          window.__tsOptions = options;
          box.innerHTML = "<div data-interactive-challenge hidden>Challenge</div>";
          return "synthetic-widget";
        },
        getResponse() { return window.__tsToken || ""; },
        reset() {}, remove() {},
      };
    });
    await assertCanonical(page, path, ready);
    if (path === "/account" && await page.locator("#sc-nombre").count()) await page.locator("#sc-swap").click();
    await page.waitForFunction(() => !!window.__tsOptions);
    expect(await page.evaluate(() => window.__tsOptions.appearance)).toBe("interaction-only");
    await page.evaluate(() => { document.querySelector("[data-interactive-challenge]").hidden = false; });
    expect(await page.locator("[data-interactive-challenge]").isVisible()).toBe(true);
  });
}
