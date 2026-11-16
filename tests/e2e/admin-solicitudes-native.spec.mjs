// Native Requests renderer: prove the real direct screen waits for the
// manifest module, fails visibly when it is rejected, and recovers on reload.
//
// Port of tests/e2e/admin-solicitudes-native.e2e.mjs (3 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `requests: true`); the runner's page replaces `browser.newPage()` (viewport per test via
//   page.setViewportSize()) and the sign-in is the shared loginAdmin() landing on /admin/cola
//   so Requests stays cold. The third test signs in on the runner's page, copies its cookies
//   into a second context from the `newContext` fixture (1280x900) and works there, as before.
// - The hand-made deferred() promises are holdGate(cleanup) gates (released on cleanup too,
//   so a failing test never leaves a route hanging). The route handler of test 1 no longer
//   asserts the module path inside the handler (an exception there is not a test failure):
//   it records the URL and the test asserts it with the other module-URL checks that were
//   already there. `route.continue()` tolerates a page closed meanwhile.
// - «Was the held module requested within 8 s» is expect.poll on a flag set by the route
//   handler (same 8 s) instead of a Promise.race against a timer.
// - «Recovery must wait for the requested English catalog» (test 3) is held for 300 ms with
//   holdsFor() instead of being read once: the absence over time is the claim.
// - The English bootstrap posts the settings with page.request (same session cookie) instead
//   of an in-page fetch.
// - Waits are web-first with the original timeouts; race snapshots (rows painted while the
//   module is held, legacy global, request/failure lists, alert text) stay point-in-time.
// - Locators left as CSS: `#list .sol-row`, `aside.sol-det[data-id]` (row/detail hooks whose
//   absence while held and presence after are the subject); the alert is getByRole("alert").
import { test, expect, loginAdmin, holdGate, holdsFor, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_solicitudes_native", requests: true } });

const MODULE = "platform/admin-solicitudes.mjs";
const MODULE_PATH = /\/static\/platform\/admin-solicitudes\.[a-f0-9]{8}\.mjs$/;
const CLASSIC_PATH = /(?:^|\/)admin-solicitudes\.js$/;

// Keep Requests cold: the default post-login route is this screen, which
// would otherwise load and cache the very module this test must intercept.
const login = (page, app) => loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/cola");

async function enableEnglish(page, app) {
  await login(page, app);
  const saved = await page.request.post("/api/admin/settings", { data: {
    grupo: "languages", valores: {
      LANGUAGES: "es,en", LANG_DEFAULT: "en", LANG_SWITCHER_MODE: "secret",
    },
  } });
  expect(saved.ok(), `English setup failed: ${await saved.text()}`).toBe(true);
}

function observe(page) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => failures.push({
    path: new URL(request.url()).pathname, error: request.failure()?.errorText || "request failed",
  }));
  page.on("response", (response) => {
    if (response.status() >= 400) failures.push({ path: new URL(response.url()).pathname, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested };
}

test("Requests waits for its manifest module; rows, detail, and reload use no retired global", async ({ page, app, cleanup }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const gate = holdGate(cleanup);
  let moduleRequested = false;
  let moduleUrl = "";
  await login(page, app);
  const seen = observe(page); // login abort telemetry is deliberately outside this journey.
  await page.route("**/static/platform/admin-solicitudes.*.mjs", async (route) => {
    moduleUrl = route.request().url();
    moduleRequested = true;
    await gate.held;
    await route.continue().catch(() => {});
  });
  await page.goto("/admin/solicitudes", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect.poll(() => moduleRequested, { timeout: 8000, message: `${MODULE} was not requested` }).toBe(true);
  const delayed = await page.evaluate(() => ({
    rows: document.querySelectorAll("#list .sol-row").length,
    moduleUrl: window.SANA_STATIC?.["platform/admin-solicitudes.mjs"] || "",
    legacy: typeof window.SanaSolicitudes,
  }));
  expect(delayed.rows, "request rows must not paint before the renderer is ready").toBe(0);
  expect(delayed.moduleUrl).toMatch(MODULE_PATH);
  expect(delayed.legacy).toBe("undefined");

  gate.release();
  await expect(page.locator("#list .sol-row").first()).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#list .sol-row")).toHaveCount(2);
  await page.locator("#list .sol-row").first().click();
  await expect(page.locator("aside.sol-det[data-id]")).toBeVisible({ timeout: 8000 });
  const after = await page.evaluate(() => ({ legacy: typeof window.SanaSolicitudes }));
  expect(after.legacy).toBe("undefined");
  expect(new URL(moduleUrl).pathname).toMatch(MODULE_PATH);
  expect(seen.requested.some((path) => MODULE_PATH.test(path))).toBe(true);
  expect(seen.requested.some((path) => CLASSIC_PATH.test(path))).toBe(false);
  expect(seen.failures).toEqual([]);

  await page.reload({ waitUntil: "load", timeout: 20000 });
  await expect(page.locator("#list .sol-row").first()).toBeVisible({ timeout: 15000 });
});

test("a rejected Requests module has localized recovery and reloads cleanly", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, app);
  await page.route("**/static/platform/admin-solicitudes.*.mjs", (route) => route.abort("failed"));
  await page.goto("/admin/solicitudes?lang=es", { waitUntil: "load", timeout: 20000 });
  const alert = page.locator("#list").getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 10000 });
  expect(await alert.innerText()).toMatch(/No pudimos cargar las solicitudes\.\s*Reintentar/);
  expect(await page.evaluate(() => typeof window.SanaSolicitudes)).toBe("undefined");
  expect(await page.locator("#list .sol-row").count()).toBe(0);
  await page.unroute("**/static/platform/admin-solicitudes.*.mjs");
  await page.reload({ waitUntil: "load", timeout: 20000 });
  await expect(page.locator("#list .sol-row").first()).toBeVisible({ timeout: 15000 });
});

test("a failed renderer waits for delayed English i18n before showing recovery", async ({ page: setup, app, newContext, cleanup }) => {
  await enableEnglish(setup, app);
  const cookies = await setup.context().cookies(app.baseUrl);
  const context = await newContext({ viewport: { width: 1280, height: 900 } });
  await context.addCookies(cookies);
  const page = await context.newPage();
  const gate = holdGate(cleanup);
  let i18nRequested = false;
  await page.route("**/static/platform/i18n.*.mjs", async (route) => {
    i18nRequested = true;
    await gate.held;
    await route.continue().catch(() => {});
  });
  await page.route("**/static/platform/admin-solicitudes.*.mjs", (route) => route.abort("failed"));
  await page.goto("/admin/solicitudes?lang=en", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect.poll(() => i18nRequested, { timeout: 8000, message: "English i18n was not requested" }).toBe(true);
  await holdsFor(async () => {
    expect(await page.locator("#list").getByRole("alert").count(),
      "recovery must wait for the requested English catalog").toBe(0);
  }, { during: 300 });
  gate.release();
  const alert = page.locator("#list").getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 10000 });
  expect(await alert.innerText()).toMatch(/We could not load requests\.\s*Reload/);
});
