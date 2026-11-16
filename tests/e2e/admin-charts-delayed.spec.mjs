// Delayed native charts module on the real reports screen. A source scan
// cannot prove admin-reports.js waits for chartsReady before painting.
//
// Port of tests/e2e/admin-charts-delayed.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//   page replaces `browser.newPage()` (viewport 1280x900 via test.use) and the sign-in is the
//   shared loginAdmin() landing on /admin/solicitudes. The English bootstrap posts the
//   settings with page.request (same session cookie) instead of an in-page fetch.
// - The hand-made deferred() is holdGate(cleanup): it releases the held module on cleanup too,
//   so a failing test never leaves the route hanging. The route handler no longer asserts the
//   module path itself (an exception inside a handler is not a test failure): it records the
//   URL and the test asserts it with the other module-URL checks that were already there.
//   `route.continue()` tolerates the page having closed while released by cleanup.
// - «Was the module requested within 8 s» is expect.poll on a flag set by the route handler
//   (same 8 s) instead of a Promise.race against a timer.
// - Waits are web-first with the original timeouts. The races stay point-in-time snapshots
//   (cards painted while the module is held, legacy global, innerText of the chart caption,
//   failure/request lists), never retrying expects.
// - Locators left as CSS: `#sec-reportes`, `.rep-sumcard`, `#rep-body .sana-chart-read` -
//   section/card hooks whose presence or absence over time is the subject.
import { test, expect, loginAdmin, holdGate, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

// A report draws only when it has data (admin-reports.mjs hasData*), and on an empty database the one
// card left is the pack's Google reviews, which has no chart caption. The two fictional pending requests
// give Management the data its chart needs, so the English caption below is still what is under test.
test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_admin_charts_delayed", requests: true },
  viewport: { width: 1280, height: 900 },
});

const MODULE_NAME = "platform/admin-charts.mjs";
const MODULE_PATH = /\/static\/platform\/admin-charts\.[a-f0-9]{8}\.mjs$/;
const CLASSIC = /(?:^|\/)admin-charts\.js$/;

const signIn = (page, app) => loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/solicitudes");

async function enableEnglish(page, app) {
  await signIn(page, app);
  const saved = await page.request.post("/api/admin/settings", { data: {
    grupo: "languages",
    valores: { LANGUAGES: "es,en", LANG_DEFAULT: "en", LANG_SWITCHER_MODE: "secret" },
  } });
  expect(saved.ok(), `English bootstrap setup failed: ${await saved.text()}`).toBe(true);
}

function observe(page) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/account/me" && request.failure()?.errorText === "net::ERR_ABORTED") return;
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

test("reports wait for the native charts module, never the retired global or classic script", async ({ page, app, cleanup }) => {
  const gate = holdGate(cleanup);
  let moduleRequested = false;
  let moduleURL = "";
  await signIn(page, app);
  // Observe this reports journey, not login telemetry intentionally aborted
  // when authentication navigates away from the gate.
  const seen = observe(page);
  await page.route("**/static/platform/admin-charts.*.mjs", async (route) => {
    moduleURL = route.request().url();
    moduleRequested = true;
    await gate.held;
    await route.continue().catch(() => {});
  });

  await page.goto("/admin/reportes", { waitUntil: "domcontentloaded", timeout: 20000 });
  await expect.poll(() => moduleRequested, {
    timeout: 8000, message: `${MODULE_NAME} was not requested from the manifest`,
  }).toBe(true);

  await expect(page.locator("#sec-reportes")).toBeAttached({ timeout: 5000 });
  const whileDelayed = await page.evaluate(() => ({
    cards: document.querySelectorAll("#sec-reportes .rep-sumcard").length,
    legacyGlobal: typeof window.SanaCharts,
    manifestURL: window.SANA_STATIC?.["platform/admin-charts.mjs"] || "",
    searchGlobal: typeof window.SanaSearch,
  }));
  expect(whileDelayed.cards, "report cards must not paint before chartsReady resolves").toBe(0);
  expect(whileDelayed.legacyGlobal).toBe("undefined");
  expect(whileDelayed.manifestURL).toMatch(MODULE_PATH);
  expect(whileDelayed.searchGlobal).toBe("undefined");

  gate.release();
  await expect(page.locator("#sec-reportes .rep-sumcard").first()).toBeVisible({ timeout: 15000 });

  const after = await page.evaluate(() => ({
    cards: document.querySelectorAll("#sec-reportes .rep-sumcard").length,
    legacyGlobal: typeof window.SanaCharts,
    manifestURL: window.SANA_STATIC?.["platform/admin-charts.mjs"] || "",
  }));
  expect(after.cards, `expected >=1 .rep-sumcard, found ${after.cards}`).toBeGreaterThanOrEqual(1);
  expect(after.legacyGlobal).toBe("undefined");
  expect(after.manifestURL).toMatch(MODULE_PATH);
  expect(new URL(moduleURL).pathname).toMatch(MODULE_PATH);
  expect(new URL(moduleURL).pathname).toBe(new URL(after.manifestURL, app.baseUrl).pathname);
  expect(seen.requested.some((path) => MODULE_PATH.test(path))).toBe(true);
  expect(seen.requested.some((path) => CLASSIC.test(path)),
    `retired classic charts script was requested: ${seen.requested.filter((path) => CLASSIC.test(path))}`).toBe(false);
  expect(seen.failures).toEqual([]);

  await page.reload({ waitUntil: "load", timeout: 20000 });
  await expect(page.locator("#sec-reportes .rep-sumcard").first()).toBeVisible({ timeout: 15000 });
  const reloaded = await page.evaluate(() => ({
    cards: document.querySelectorAll("#sec-reportes .rep-sumcard").length,
    legacyGlobal: typeof window.SanaCharts,
  }));
  expect(reloaded.cards).toBeGreaterThanOrEqual(1);
  expect(reloaded.legacyGlobal).toBe("undefined");
});

test("English report charts use the catalog after the native module settles", async ({ page, app }) => {
  await enableEnglish(page, app);
  const seen = observe(page);
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "load", timeout: 20000 });
  await expect(page).toHaveURL((url) => url.search === "?view=gestion", { timeout: 5000 });
  const caption = page.locator("#rep-body .sana-chart-read").first();
  await expect(caption).toBeVisible({ timeout: 15000 });
  expect(await caption.innerText()).toMatch(/^How to read it:/);
  const proof = await page.evaluate(() => {
    const body = document.getElementById("rep-body");
    const text = (body && body.innerText) || "";
    return {
      text,
      legacyGlobal: typeof window.SanaCharts,
      language: document.documentElement.lang,
    };
  });
  expect(proof.language).toBe("en");
  expect(proof.legacyGlobal).toBe("undefined");
  expect(proof.text.includes("Sin datos todavía."), "Spanish chart fallback leaked onto an English page").toBe(false);
  expect(seen.requested.some((path) => CLASSIC.test(path))).toBe(false);
  expect(seen.failures).toEqual([]);
});
