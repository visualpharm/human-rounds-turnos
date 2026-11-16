// Browser proof for the native Week renderer. This holds the actual
// fingerprinted module, not an arbitrary timeout, and observes the public
// service shell before and after its real imports settle.
//
// Differences from the node:test original (tests/e2e/week-native.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//     `page` replaces state.browser.newPage(): 1280x900 is test.use({ viewport }), the phone test sets
//     390x844 with page.setViewportSize() before it navigates. The second test switches the site
//     language to English through the settings API and the third one asks for ?lang=es explicitly,
//     so the three tests share app state and the file is serial.
//   - The held module is a promise gate (holdGate) released by the test, as in the original. The
//     "native Week module request" timeout (within(), 8 s) is expect.poll over the URL the route saw, and
//     the route handler no longer asserts the pathname itself (an exception inside a route
//     handler would not fail the test): the same toMatch runs on the captured URL right after the poll.
//   - waitFor({state}) calls are toBeAttached/toBeVisible with the original timeouts.
//   - The state before the held module is released ("no partial grid, no legacy global") and the
//     recovery snapshot of the rejected module stay point-in-time page.evaluate reads; «no visible
//     error after the reload» stays a point-in-time count.
//   - Evidence screenshots keep their place (artifactPath("human-rounds", "astra-week-native"), i.e.
//     ~/.claude/artifacts/... unless TURNOS_E2E_ARTIFACTS_DIR moves it; the original hard-wired the home
//     directory) and are also attached to the report (saveShot, fullPage kept).
//   - Locators: [data-semana], .sem-grid, [data-semana-error] stay CSS: they are the renderer's markup
//     contract, and the overflow probe walks every element on purpose.
import { join } from "node:path";
import { test, expect, ADMIN_USER, ADMIN_PASS, holdGate, saveShot, artifactPath } from "./lib/fixtures.mjs";

const SERVICE_ID = "taller-gimnasia-lakeview-clinic";
const MODULE_PATH = /\/static\/platform\/week\.[a-f0-9]{8}\.mjs$/;

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_week_native" }, viewport: { width: 1280, height: 900 } });
test.describe.configure({ mode: "serial" });

function observe(page) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => failures.push({
    path: new URL(request.url()).pathname, error: request.failure()?.errorText || "failed",
  }));
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ path, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested };
}

async function enableEnglish(page, baseUrl) {
  await page.goto(baseUrl + "/admin/login?next=%2Fadmin%2Fsettings", { waitUntil: "load", timeout: 15_000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/(?!login)/, { timeout: 10_000, waitUntil: "commit" });
  const saved = await page.evaluate(async () => {
    const response = await fetch("/api/admin/settings", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "en", LANG_SWITCHER_MODE: "secret" } }),
    });
    return { ok: response.ok, body: await response.json() };
  });
  expect(saved.ok, JSON.stringify(saved.body)).toBe(true);
}

function overflowProbe() {
  const viewport = document.documentElement.clientWidth;
  const elements = [...document.querySelectorAll("*")].filter((element) => {
    const box = element.getBoundingClientRect();
    return box.width > 0 && box.right > viewport + 1;
  }).map((element) => `${element.tagName}.${element.className}`);
  return { page: document.documentElement.scrollWidth - viewport, elements };
}

test("public grid waits for delayed native Week, then survives reloads at phone and desktop widths", async ({ page, app, cleanup }, testInfo) => {
  const seen = observe(page);
  const gate = holdGate(cleanup);
  let moduleURL = "";
  try {
    await page.route("**/static/platform/week.*.mjs", async (route) => {
      moduleURL = route.request().url();
      gate.arrive();
      await gate.held;
      await route.continue();
    });
    const navigation = page.goto(app.baseUrl + `/service/${SERVICE_ID}`, { waitUntil: "commit", timeout: 15_000 });
    await expect.poll(() => moduleURL, { message: "native Week module request", timeout: 8_000 }).not.toBe("");
    expect(new URL(moduleURL).pathname).toMatch(MODULE_PATH);
    await expect(page.locator("[data-semana]")).toBeAttached({ timeout: 8_000 });
    const before = await page.evaluate(() => ({
      grid: !!document.querySelector("[data-semana] .sem-grid"),
      legacy: typeof window.SanaWeek,
      manifest: window.SANA_STATIC?.["platform/week.mjs"] || "",
    }));
    expect(before.grid, "the schedule must not paint a partial fallback while Week is held").toBe(false);
    expect(before.legacy).toBe("undefined");
    expect(before.manifest).toMatch(MODULE_PATH);

    gate.release();
    await navigation;
    const grid = page.locator("[data-semana] .sem-grid");
    await expect(grid).toBeVisible({ timeout: 15_000 });
    await expect(grid).toHaveText(/Miércoles|Miercoles/, { useInnerText: true });
    expect(await page.evaluate(() => window.SanaWeek)).toBeUndefined();
    expect(new URL(moduleURL).pathname).toMatch(MODULE_PATH);
    expect(seen.requested.some((path) => /(?:^|\/)week\.js$/.test(path))).toBe(false);

    const artifacts = artifactPath("human-rounds", "astra-week-native");
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await page.reload({ waitUntil: "load", timeout: 20_000 });
      await expect(grid).toBeVisible({ timeout: 15_000 });
      expect(await page.evaluate(overflowProbe), `overflow at ${width}px`).toEqual({ page: 0, elements: [] });
      await saveShot(testInfo, page, join(artifacts, `week-${width}.png`), { fullPage: true });
    }
    expect(seen.failures).toEqual([]);
  } finally {
    gate.release();
  }
});

test("English Week labels are native-rendered after the language switch", async ({ page, app }) => {
  await enableEnglish(page, app.baseUrl);
  const seen = observe(page);
  await page.goto(app.baseUrl + `/service/${SERVICE_ID}?lang=en`, { waitUntil: "load", timeout: 20_000 });
  const grid = page.locator("[data-semana] .sem-grid");
  await expect(grid).toBeVisible({ timeout: 15_000 });
  const proof = await page.evaluate(() => ({
    lang: document.documentElement.lang,
    text: document.querySelector("[data-semana] .sem-grid")?.innerText || "",
    legacy: typeof window.SanaWeek,
    manifest: window.SANA_STATIC?.["platform/week.mjs"] || "",
  }));
  expect(proof.lang).toBe("en");
  expect(proof.text).toMatch(/Monday/);
  expect(proof.text).toMatch(/Closed/);
  expect(proof.legacy).toBe("undefined");
  expect(proof.manifest).toMatch(MODULE_PATH);
  expect(seen.failures).toEqual([]);
});

test("a rejected native Week shows localized reload recovery without the retired script or global", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const seen = observe(page);
  await page.route("**/static/platform/week.*.mjs", (route) => route.abort("failed"));
  await page.goto(app.baseUrl + `/service/${SERVICE_ID}?lang=es`, { waitUntil: "load", timeout: 15_000 });
  await expect(page.locator("[data-semana]")).toBeAttached({ timeout: 8_000 });
  const proof = await page.evaluate(() => ({
    grid: !!document.querySelector("[data-semana] .sem-grid"),
    lang: document.documentElement.lang,
    recovery: document.querySelector("[data-semana-error]")?.textContent?.trim() || "",
    legacy: typeof window.SanaWeek,
    manifest: window.SANA_STATIC?.["platform/week.mjs"] || "",
  }));
  expect(proof.grid).toBe(false);
  expect(proof.lang).toBe("es");
  expect(proof.recovery).toMatch(/No pudimos cargar los horarios\.\s*Reintentar/);
  expect(proof.legacy).toBe("undefined");
  expect(proof.manifest).toMatch(MODULE_PATH);
  expect(seen.requested.some((path) => /(?:^|\/)week\.js$/.test(path))).toBe(false);
  expect(seen.failures.filter((failure) => failure.path !== new URL(proof.manifest, app.baseUrl).pathname)).toEqual([]);
  await page.unroute("**/static/platform/week.*.mjs");
  await page.reload({ waitUntil: "load", timeout: 20_000 });
  await expect(page.locator("[data-semana] .sem-grid")).toBeVisible({ timeout: 15_000 });
  expect(await page.locator("[data-semana-error]:visible").count()).toBe(0);
});
