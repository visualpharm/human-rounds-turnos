// Actual ASGI page, native controller and dependencies. Only deliberately
// failing/held network boundaries are intercepted. PageTransitionEvent tests
// prove ownership, not actual browser BFCache admission. CLS is observed,
// not inferred from screenshots or claimed to be zero.
//
// Port of tests/e2e/admin-reports-lifecycle.e2e.mjs (12 tests, same titles, same order).
//
// Differences from the node:test original:
// - The tests append to one shared `measurements` record that is written to measurements.json
//   (with its completeness flags) after the file, so the file is `serial`: a failure skips the
//   rest and a retry re-runs everything on a fresh worker with a fresh record.
// - `test.before` (admin enables the English catalog through the settings API) is `test.beforeAll`;
//   `test.after` (writes measurements.json) is `test.afterAll`.
// - `session(lang)` uses the runner's `page` (390x844 through `test.use`), registers the same
//   layout-shift observer init script on its context, signs in with the original sequence
//   (commit-level wait for /admin/solicitudes, then about:blank) and sets the `lang` cookie;
//   the `finally { context.close() }` blocks are gone.
// - Screenshots go through `saveShot` to the same files as before (artifactPath("human-rounds",
//   "reports-native-lifecycle", <timestamp>)) and are attached to the report (40 + 1).
// - «Missing native <name> request after 5 s» is `expect.poll(...).toBe(true)` with a 5 s timeout
//   instead of a `Promise.race` against a rejecting timer; the hand-made `hold` promises are
//   `holdGate(cleanup)` (released on teardown, so a failing test never leaves a route hanging).
// - Text/visibility waits are web-first (`toBeVisible`, `toHaveText`, `toContainText`); every check
//   of «absent at this moment» (alerts, request counters, `#rep-body` before the module, replacement
//   owner text) stays a point-in-time read exactly where the original took it. The in-page polling
//   `waitForFunction` of `visible()` is unchanged.
// - Locators that stay CSS: `#rep-body`, `.rep-sumcard`, `#sec-reportes`, `[data-retry]`,
//   `#reports-retry` and `#adm-h1` are the report screen's own markup contract (measured geometry and
//   ownership are the subject); the alert is `getByRole("alert")` only where it is a plain presence count.
import {
  test, expect, saveShot, holdGate, holdsFor, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import fs from "node:fs/promises";
import { join } from "node:path";

test.use({
  appOptions: { dbName: "sana_turnos_test_reports_lifecycle", requests: true },
  viewport: { width: 390, height: 844 },
});
test.describe.configure({ mode: "serial" });

const stamp = new Date().toISOString().replaceAll(":", "-");
const evidence = artifactPath("human-rounds", "reports-native-lifecycle", stamp);
const measurements = [];
const widths = [360, 390, 768, 1024, 1280];
const otherViews = ["recorrido", "medicos", "pacientes", "busquedas", "centros", "recupero", "sumar", "estadistica", "capacidad", "ia", "login"];

test.beforeAll(async ({ app, browser }) => {
  await fs.mkdir(evidence, { recursive: true });
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await page.goto("/admin/login?next=%2Fadmin%2Fsolicitudes", { waitUntil: "load" });
    await page.fill("#gate-user", ADMIN_USER);
    await page.fill("#gate-pass", ADMIN_PASS);
    await page.click("#gate-btn");
    await page.waitForURL(/\/admin\/solicitudes/, { waitUntil: "commit", timeout: 15000 });
    const saved = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(saved.status()).toBe(200);
  } finally { await context.close(); }
});

test.afterAll(async () => {
  await fs.writeFile(join(evidence, "measurements.json"), JSON.stringify({
    expected: 40, recorded: measurements.length,
    unique: new Set(measurements.map((row) => row.id)).size,
    complete: measurements.length === 40 && new Set(measurements.map((row) => row.id)).size === 40,
    measurements, boundary: "Local synthetic data; consult TAP verdict. Not production or BFCache admission proof.",
  }, null, 2));
});

async function session(page, app, lang = "es") {
  const context = page.context();
  await context.addInitScript(() => {
    window.__reportShifts = [];
    window.__reportObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput)
        window.__reportShifts.push({ value: entry.value, startTime: entry.startTime });
    });
    window.__reportObserver.observe({ type: "layout-shift", buffered: true });
  });
  await page.goto("/admin/login?next=%2Fadmin%2Fsolicitudes", { waitUntil: "load" });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/solicitudes/, { waitUntil: "commit", timeout: 15000 });
  await page.goto("about:blank");
  await context.addCookies([{ name: "lang", value: lang, url: app.baseUrl }]);
}
async function visible(page, view) {
  if (view === "resumen") await expect(page.locator("#rep-body .rep-sumcard").first()).toBeVisible();
  else await page.waitForFunction(() => document.querySelector("#rep-body")?.innerText.trim().length > 20
    && !document.querySelector('#sec-reportes [role="alert"]'));
  expect(await page.locator('#sec-reportes [role="alert"]').count()).toBe(0);
}
async function measure(page, testInfo, lang, view, width) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/admin/reportes?view=" + view, { waitUntil: "load" });
  await visible(page, view);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const id = `${lang}-${view}-${width}`;
  const row = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    // A row that scrolls sideways on purpose (the other reports' links on a
    // phone, 00f657794) holds its links past the edge; the row itself must fit.
    const inScroller = (el) => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === "auto" || ox === "scroll") return true;
      }
      return false;
    };
    const overflow = [...document.querySelectorAll("#sec-reportes *")].flatMap((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && getComputedStyle(el).visibility === "visible" && !inScroller(el)
        && (r.right > vw + 1 || r.left < -1)
        ? [{ tag: el.tagName, class: String(el.className), right: r.right, left: r.left }] : [];
    });
    const shifts = [...window.__reportShifts, ...window.__reportObserver.takeRecords()
      .filter((entry) => !entry.hadRecentInput).map((entry) => ({ value: entry.value, startTime: entry.startTime }))];
    let cls = 0, sum = 0, first = 0, last = -Infinity;
    for (const entry of shifts) {
      if (entry.startTime - last >= 1000 || entry.startTime - first >= 5000) { sum = 0; first = entry.startTime; }
      sum += entry.value; last = entry.startTime; cls = Math.max(cls, sum);
    }
    return { viewport: vw, pageOverflow: document.documentElement.scrollWidth - vw, overflow,
      language: document.documentElement.lang, title: document.querySelector("#adm-h1").textContent,
      controllerGlobal: typeof window.sanaRouteReportes, shifts, cls, observer: !!window.__reportObserver };
  });
  measurements.push({ id, lang, view, width, ...row });
  await saveShot(testInfo, page, join(evidence, id + ".png"), { fullPage: true });
  expect(row.language).toBe(lang);
  expect(row.controllerGlobal).toBe("undefined");
  expect(row.observer).toBe(true);
  expect(Number.isFinite(row.cls)).toBe(true);
  expect(row.pageOverflow, id).toBe(0);
  expect(row.overflow, id).toEqual([]);
}

for (const lang of ["es", "en"]) {
  test(`Reports ${lang}: thirteen real views, with five-width overview and management geometry`, async ({ page, app }, testInfo) => {
    await session(page, app, lang);
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    for (const width of widths) for (const view of ["resumen", "gestion"]) await measure(page, testInfo, lang, view, width);
    for (const view of otherViews) await measure(page, testInfo, lang, view, 390);
    expect(errors).toEqual([]);
  });
}

test("Reports prototype-name query values render the real overview instead of recovery", async ({ page, app }) => {
  await session(page, app, "en");
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  // The overview draws one card per report that has data (admin-reports.mjs hasData*), so the number is the
  // data's, not eleven: the real overview is the one a plain visit paints, card for card.
  const cards = () => page.locator("#rep-body .rep-sumcard").evaluateAll((els) => els.map((el) => el.innerText));
  await page.goto("/admin/reportes", { waitUntil: "load" });
  await visible(page, "resumen");
  const overview = await cards();
  expect(overview.length, "the fixture's requests give the overview cards to compare").toBeGreaterThan(0);
  expect(overview.length, "never more cards than reports").toBeLessThanOrEqual(11);
  for (const view of ["__proto__", "constructor", "toString", "hasOwnProperty"]) {
    await page.goto("/admin/reportes?view=" + view, { waitUntil: "load" });
    await visible(page, "resumen");
    await expect.poll(cards, { message: view }).toEqual(overview);
    expect(await page.locator("#sec-reportes [data-retry]").count(), view).toBe(0);
  }
  expect(errors).toEqual([]);
});

for (const name of ["i18n", "dates", "admin-charts", "search", "admin-session"]) {
  test(`Reports holds data until native ${name} resolves`, async ({ page, app, cleanup }) => {
    await session(page, app, "en");
    const gate = holdGate(cleanup);
    let requested = false;
    let calls = 0;
    page.on("request", (request) => { if (new URL(request.url()).pathname.startsWith("/api/admin/reports")) calls++; });
    await page.route(`**/static/platform/${name}.*.mjs`, async (route) => { requested = true; await gate.held; await route.continue(); });
    await page.goto("/admin/reportes?view=gestion", { waitUntil: "commit" });
    await expect.poll(() => requested, { message: `Missing native ${name} request`, timeout: 5000 }).toBe(true);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(calls).toBe(0);
    expect(await page.locator("#rep-body").count()).toBe(0);
    gate.release();
    await visible(page, "gestion");
    expect(calls).toBeGreaterThan(0);
  });
}

test("Reports data failure preserves the database message and offers a working Reload", async ({ page, app }, testInfo) => {
  await session(page, app, "es");
  const matcher = "**/api/admin/reports?days=30";
  await page.route(matcher, (route) => route.fulfill({ status: 503, contentType: "application/json", body: "{}" }));
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "load" });
  await expect(page.locator("#reports-retry")).toBeVisible();
  await expect(page.locator("#reports-retry")).toHaveText("Recargar", { useInnerText: true });
  await expect(page.locator("#rep-body")).toContainText(/base de datos/, { useInnerText: true });
  await saveShot(testInfo, page, join(evidence, "data-failure-390.png"), { fullPage: true });
  await page.unroute(matcher);
  await page.locator("#reports-retry").click();
  await visible(page, "gestion");
});

test("Reports slow data answer shows loading, never the load failure, and paints when it arrives", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app, "es");
  const gate = holdGate(cleanup);
  const matcher = "**/api/admin/reports?days=30";
  await page.route(matcher, async (route) => {
    gate.arrive();
    await gate.held;
    await route.continue();
  });
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "commit" });
  await gate.arrived;
  await expect(page.locator("#rep-body").getByRole("status")).toHaveText("Cargando…", { useInnerText: true });
  // 10 s with the request held: past the template's 8 s load deadline, the module keeps waiting.
  await holdsFor(async () => {
    expect(await page.locator("#sec-reportes [role=alert]").count()).toBe(0);
    expect(await page.locator("#sec-reportes [data-retry]").count()).toBe(0);
  }, { during: 10000, every: 250 });
  await expect(page.locator("#rep-body").getByRole("status")).toBeVisible();
  await saveShot(testInfo, page, join(evidence, "slow-answer-loading-390.png"), { fullPage: true });
  gate.release();
  await visible(page, "gestion");
  await expect(page.locator("#rep-body").getByRole("status")).toHaveCount(0);
});

test("Reports module dependency held past the deadline says the load is late, then mounts", async ({ page, app, cleanup }) => {
  await session(page, app, "es");
  const gate = holdGate(cleanup);
  const matcher = "**/static/platform/search.*.mjs";
  await page.route(matcher, async (route) => { await gate.held; await route.continue(); });
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "commit" });
  await expect(page.locator(".screen-load-slow [data-retry]")).toBeVisible({ timeout: 12000 });
  // Late, not failed: the shared late copy, not the screen's failure text.
  await expect(page.locator('.screen-load-slow [role="status"]')).toHaveText("La carga tarda más de lo normal.", { useInnerText: true });
  expect(await page.locator('.screen-load-slow [role="alert"]').count()).toBe(0);
  expect(await page.locator("#sec-reportes [data-retry]").count(), "the copy never replaces the screen").toBe(0);
  gate.release();
  await visible(page, "gestion");
  await expect(page.locator(".screen-load-slow")).toHaveCount(0);
});

test("Reports rejected entry import recovers by Reload without a classic fallback", async ({ page, app }) => {
  await session(page, app, "en");
  const matcher = "**/static/platform/admin-reports.*.mjs";
  await page.route(matcher, (route) => route.fulfill({ status: 500, body: "" }));
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "load" });
  await expect(page.locator("#sec-reportes [data-retry]")).toBeVisible();
  expect(await page.evaluate(() => typeof window.sanaRouteReportes)).toBe("undefined");
  await page.unroute(matcher);
  await page.locator("#sec-reportes [data-retry]").click();
  await visible(page, "gestion");
});

// The 8 s deadline only says the load is late (2026-11-16, static/platform/screen-boot.mjs):
// a late entry module still mounts the report and clears the copy.
test("Reports entry import held past the deadline still mounts the report", async ({ page, app, cleanup }) => {
  await session(page, app, "en");
  const gate = holdGate(cleanup);
  const matcher = "**/static/platform/admin-reports.*.mjs";
  let calls = 0;
  page.on("request", (request) => { if (new URL(request.url()).pathname.startsWith("/api/admin/reports")) calls++; });
  await page.route(matcher, async (route) => { await gate.held; await route.continue(); });
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "commit" });
  await expect(page.locator(".screen-load-slow [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(calls).toBe(0);
  gate.release();
  await visible(page, "gestion");
  expect(calls).toBeGreaterThan(0);
  await expect(page.locator(".screen-load-slow")).toHaveCount(0);
  expect(await page.locator("#sec-reportes [data-retry]").count()).toBe(0);
});

test("Reports page exit prevents a late response painting and persisted pageshow remounts", async ({ page, app, cleanup }) => {
  await session(page, app, "en");
  const gate = holdGate(cleanup);
  const matcher = "**/api/admin/reports?days=30";
  await page.route(matcher, async (route) => {
    gate.arrive();
    await gate.held;
    try { await route.continue(); } catch { /* pagehide can abort this held request */ }
  });
  await page.goto("/admin/reportes?view=gestion", { waitUntil: "load" });
  await gate.arrived;
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    document.getElementById("sec-reportes").textContent = "Replacement owner";
  });
  gate.release();
  await page.unrouteAll({ behavior: "wait" });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await page.locator("#sec-reportes").innerText()).toBe("Replacement owner");
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await visible(page, "gestion");
  expect(await page.locator("#sec-reportes").innerText()).not.toMatch(/Replacement owner/);
});
