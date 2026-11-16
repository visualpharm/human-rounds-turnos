// Acceptance for the native i18n module's first-paint contract.  This is
// deliberately a browser test: a source scan cannot catch labels frozen by
// nav.js/listing.js before an asynchronous catalog is available.
//
// Port of tests/e2e/i18n-delayed-load.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. The page is the runner's, at the
//   original 1280x900 viewport (test.use({ viewport })).
// - The two hand-made `deferred()` objects are one `holdGate` (the withheld i18n module is
//   `held`; `release` is also called on cleanup, so a failing test never leaves the route
//   hanging - replaces the try/finally). «The module was requested within 8 s» was a
//   Promise.race against a setTimeout; it is `expect.poll` on the intercepted URL with
//   the same 8 s limit and message.
// - The path check that sat INSIDE the route handler (an assert thrown in a handler is not
//   reported as a test failure) now records every intercepted pathname and asserts them
//   all in the test body, next to the existing check of the module URL.
// - Waits are web-first (`toBeVisible`, `toHaveValue`, 5-10 s as before). The first-paint
//   proof (`window.__i18nFirstPaint` samples, the bootstrap catalog and the ESM contract
//   probe) is a point-in-time evaluate exactly as before - a retrying check would pass
//   a Spanish flash that came late.
// - The English bootstrap (login + POST /api/admin/settings from the page) is unchanged.
// - Locators: `#q`, `#resultados table thead th`, `.stznav-menu a` and the login ids stay CSS
//   - they are the first-paint observer's targets and the markup contract under test.
import { test, expect, holdGate, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_i18n_delayed_load" },
  viewport: { width: 1280, height: 900 },
});

const MODULE_NAME = "platform/i18n.mjs";
const MODULE_PATH = /\/static\/platform\/i18n\.[a-f0-9]{8}\.mjs$/;

async function enableEnglish(page) {
  await page.goto("/admin/login?next=%2Fadmin%2Fsettings", { waitUntil: "load", timeout: 15_000 });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 10_000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/(?!login)/, { timeout: 10_000, waitUntil: "commit" });
  const saved = await page.evaluate(async () => {
    const response = await fetch("/api/admin/settings", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        grupo: "languages",
        valores: { LANGUAGES: "es,en", LANG_DEFAULT: "en", LANG_SWITCHER_MODE: "secret" },
      }),
    });
    return { ok: response.ok, body: await response.json() };
  });
  expect(saved.ok, `English bootstrap setup failed: ${JSON.stringify(saved.body)}`).toBe(true);
}

test("English first paint and an early real search survive a delayed native i18n module", async ({ page, app, cleanup }) => {
  const module = holdGate(cleanup);
  const requested = [];
  const failures = [];
  const routedPaths = [];
  let moduleURL = "";

  await enableEnglish(page);
  await page.addInitScript(() => {
    const proof = { firstNav: null, firstHeadings: null, samples: [] };
    const visibleText = (selector) => [...document.querySelectorAll(selector)]
      .filter((element) => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length))
      .map((element) => (element.textContent || "").trim())
      .filter(Boolean);
    let previous = "";
    const sample = () => {
      const nav = visibleText(".stznav-menu a");
      const headings = visibleText("#resultados table thead th");
      const key = JSON.stringify({ nav, headings });
      if (key !== previous) {
        previous = key;
        proof.samples.push({ nav, headings });
      }
      if (!proof.firstNav && nav.length) proof.firstNav = nav;
      if (!proof.firstHeadings && headings.length) proof.firstHeadings = headings;
    };
    new MutationObserver(sample).observe(document, {
      childList: true, characterData: true, subtree: true,
    });
    const frames = () => { sample(); requestAnimationFrame(frames); };
    requestAnimationFrame(frames);
    window.__i18nFirstPaint = proof;
  });
  await page.route("**/static/platform/i18n.*.mjs", async (route) => {
    routedPaths.push(new URL(route.request().url()).pathname);
    moduleURL = route.request().url();
    await module.held;
    await route.continue();
  });
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path === "/api/account/me" && response.status() === 401) return;
    if (path === "/api/i18n/e2e-non-ok.json") return;
    if (response.status() >= 400) failures.push({ path, status: response.status() });
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/i18n/e2e-rejected.json") return;
    failures.push({ path, error: request.failure()?.errorText || "request failed" });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));

  await page.goto("/", { waitUntil: "domcontentloaded", timeout: 15_000 });
  await expect.poll(() => moduleURL, {
    message: `${MODULE_NAME} was not requested from the manifest`, timeout: 8_000,
  }).not.toBe("");

  // The page's actual search box is already interactive while the module is
  // withheld. This is intentionally not a synthetic DOM value assignment.
  await expect(page.locator("#q")).toBeVisible({ timeout: 5_000 });
  await page.locator("#q").fill("medicine");
  await expect(page.locator("#q")).toHaveValue("medicine");

  module.release();
  await expect(page.locator("#resultados table thead th").first()).toBeVisible({ timeout: 10_000 });

  const firstVisible = await page.evaluate(() => ({
    language: document.documentElement.lang,
    firstNav: window.__i18nFirstPaint?.firstNav,
    firstHeadings: window.__i18nFirstPaint?.firstHeadings,
    samples: window.__i18nFirstPaint?.samples,
    query: document.querySelector("#q")?.value,
    manifestURL: window.SANA_STATIC?.["platform/i18n.mjs"],
    legacyGlobal: typeof window.SanaI18n,
    bootstrap: {
      doctors: window.SANA_I18N?.catalog?.["nav.doctors"],
      service: window.SANA_I18N?.catalog?.["listing.col.servicio"],
      care: window.SANA_I18N?.catalog?.["home.results.context"],
    },
  }));
  expect(firstVisible.language).toBe("en");
  expect(firstVisible.query, "initialization must not discard early typing").toBe("medicine");
  expect(firstVisible.bootstrap.doctors, "English bootstrap omitted nav.doctors").toBeTruthy();
  expect(firstVisible.bootstrap.service, "English bootstrap omitted listing.col.servicio").toBeTruthy();
  expect(firstVisible.bootstrap.care, "English bootstrap omitted home.results.context").toBeTruthy();
  expect(firstVisible.firstNav?.includes(firstVisible.bootstrap.doctors),
    `first visible nav did not use bootstrap English: ${firstVisible.firstNav}`).toBe(true);
  expect(firstVisible.firstHeadings?.includes(firstVisible.bootstrap.service),
    `listing PRESET froze before i18n: ${firstVisible.firstHeadings}`).toBe(true);
  expect(firstVisible.firstHeadings?.includes(firstVisible.bootstrap.care),
    `listing call-site heading froze before i18n: ${firstVisible.firstHeadings}`).toBe(true);
  expect(firstVisible.samples?.length, "first-paint observer saw no visible-label samples").toBeTruthy();
  for (const sample of firstVisible.samples) {
    expect(sample.nav.includes("Médicos"), `Spanish nav appeared before i18n resolved: ${sample.nav}`).toBe(false);
    expect(sample.headings.includes("Servicio"), `Spanish listing appeared before i18n resolved: ${sample.headings}`).toBe(false);
  }
  expect(firstVisible.legacyGlobal).toBe("undefined");
  expect(firstVisible.manifestURL || "").toMatch(MODULE_PATH);
  for (const pathname of routedPaths) expect(pathname).toMatch(MODULE_PATH);
  expect(new URL(moduleURL).pathname).toMatch(MODULE_PATH);
  expect(new URL(moduleURL).pathname).toBe(new URL(firstVisible.manifestURL, app.baseUrl).pathname);
  expect(requested.includes(new URL(firstVisible.manifestURL, app.baseUrl).pathname)).toBe(true);

  // Import the exact emitted module, rather than an on-disk relative path,
  // and exercise the old public contract as named ESM exports.
  await page.route("**/api/i18n/e2e-non-ok.json", (route) => route.fulfill({ status: 503, body: "unavailable" }));
  await page.route("**/api/i18n/e2e-rejected.json", (route) => route.abort("failed"));
  const moduleContract = await page.evaluate(async (url) => {
    const i18n = await import(url);
    const original = { ...(window.SANA_I18N?.catalog || {}) };
    try {
      i18n.setCatalog({ empty: "", greeting: "Hello {name}" });
      const empty = i18n.t("empty", null, "fallback");
      const interpolation = i18n.t("greeting", { name: "Ada" }, "fallback");
      const absent = i18n.t("missing", null, "fallback");
      const nonOk = await i18n.load("e2e-non-ok");
      const nonOkFallback = i18n.t("missing-after-non-ok", null, "fallback");
      const rejected = await i18n.load("e2e-rejected");
      return {
        namedExports: ["t", "setCatalog", "load"].every((name) => typeof i18n[name] === "function"),
        empty,
        interpolation,
        absent,
        nonOkFallback,
        rejectedFallback: i18n.t("missing-after-rejected", null, "fallback"),
        nonOk: nonOk && typeof nonOk === "object",
        rejected: rejected && typeof rejected === "object",
      };
    } finally {
      i18n.setCatalog(original);
    }
  }, firstVisible.manifestURL);
  expect(moduleContract).toEqual({
    namedExports: true,
    empty: "",
    interpolation: "Hello Ada",
    absent: "fallback",
    nonOkFallback: "fallback",
    rejectedFallback: "fallback",
    nonOk: true,
    rejected: true,
  });
  expect(failures).toEqual([]);
});
