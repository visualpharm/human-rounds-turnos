// The actual Python offer page and served canonical module are exercised here.
// Only offer endpoint outcomes and module transport timing are synthetic.
//
// Differences from the node:test original (tests/e2e/appointment-offer-native.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//     `page` replaces state.browser.newPage(). One test = one page (2026-11-05): the auto= cases, the
//     503/410 cases and every width × surface are their own tests, so a loaded machine never has to fit
//     three or four page loads into one 45 s budget. Width tests size the page with
//     page.setViewportSize() before navigating; the phone-width test sets 390x844 the same way. The
//     "no uncaught errors" check runs in an afterEach.
//   - The held module is a promise gate (holdGate) released by the test, as in the original; the gate is
//     also released in `finally`, so a failing test never leaves a route hanging.
//   - waitForFunction(#oferta-msg text === ...) is expect(#oferta-msg).toHaveText(...);
//     waitForNavigation (deprecated) is waitForEvent("domcontentloaded") started before the click.
//   - holdsFor: in "pagehide makes a held early intent inert" the original looked once at the POST
//     list right after the module import; a POST triggered by the retired intent would arrive a moment
//     later, so the empty list is now checked continuously for 300 ms after the import.
//   - Snapshots that guard "nothing was posted / no hash change yet" stay point-in-time reads
//     (expect(await ...) / expect(posts)), never retrying ones.
//   - Evidence screenshots keep their place (OFFER_NATIVE_EVIDENCE_DIR or artifacts/appointment-offer-native
//     /screenshots under the repo root) and are also attached to the report (saveShot, fullPage kept).
//     { timeout: 45000 } is test.setTimeout(45_000).
//   - Locators: #oferta-*, [data-offer-retry] and the offer module URL pattern stay CSS/route globs: they
//     are the controller's markup contract and the fingerprinted asset under test.
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { test, expect, holdGate, holdsFor, saveShot, ROOT } from "./lib/fixtures.mjs";

const SCREENSHOTS = process.env.OFFER_NATIVE_EVIDENCE_DIR ||
  join(ROOT, "artifacts", "appointment-offer-native", "screenshots");
const pageErrors = new WeakMap();

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_offer_native" } });

// The page is rendered by the real Python owner once per worker, before the
// tests: a python3 start that imports the app takes tens of seconds on a loaded
// machine, and paying it inside a page load (every test, every reload) is what
// ran the tests out of their navigation and test time.
let offerHtml = null;
test.beforeAll(() => {
  test.setTimeout(180_000);
  const code = [
    "from app.modules.turnos import appointment_offer as o",
    "print(o._offer_available_page('tok-e2e', 'Consulta', 'CAPS', 'Mañana').decode())",
  ].join("; ");
  offerHtml = execFileSync("python3", ["-c", code], { cwd: ROOT, encoding: "utf8" });
});

function renderedOffer(auto = "") {
  return auto
    ? offerHtml.replace('data-token="tok-e2e"', `data-token="tok-e2e" data-auto="${auto}"`)
    : offerHtml;
}

test.afterEach(async ({ page }) => {
  if (!pageErrors.has(page)) return;
  await page.unrouteAll({ behavior: "wait" });
  expect(pageErrors.get(page), "offer page has no uncaught errors").toEqual([]);
});

async function installPage(page, { auto = "", response = { ok: true }, rawBody = null } = {}) {
  const posts = [];
  const errors = [];
  pageErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.route("**/appointment/offer/tok-e2e**", (route) => route.fulfill({
    status: 200, contentType: "text/html", body: renderedOffer(auto),
  }));
  await page.route("**/api/offer/tok-e2e/**", async (route) => {
    posts.push({ url: route.request().url(), method: route.request().method() });
    await route.fulfill({
      status: response.status || 200,
      contentType: "application/json",
      body: rawBody === null ? JSON.stringify(response.body || response) : rawBody,
    });
  });
  return posts;
}

for (const [action, selector] of [["accept", "#oferta-aceptar"], ["reject", "#oferta-rechazar"]]) {
  test(`real offer bootstrap preserves an early ${action}, prevents hash navigation, and ignores a second click`, async ({ page, app, cleanup }) => {
    test.setTimeout(45_000);
    await page.setViewportSize({ width: 390, height: 844 });
    const gate = holdGate(cleanup);
    try {
      const posts = await installPage(page);
      await page.route("**/static/modules/turnos/offer.*.mjs", async (route) => { await gate.held; await route.continue(); });
      await page.goto(`${app.baseUrl}/appointment/offer/tok-e2e`, { waitUntil: "domcontentloaded" });
      const target = page.locator(selector);
      await target.click();
      await target.click();
      expect(await page.evaluate(() => location.hash)).toBe("");
      expect(posts).toEqual([]);
      gate.release();
      const success = action === "accept"
        ? "¡Listo! Tu turno quedó confirmado. Te llevamos a Mis turnos…"
        : "Avisamos a la siguiente persona de la lista. Gracias por responder.";
      await expect(page.locator("#oferta-msg")).toHaveText(success);
      expect(posts).toEqual([{ url: `${app.baseUrl}/api/offer/tok-e2e/${action}`, method: "POST" }]);
    } finally { gate.release(); }
  });
}

for (const auto of ["", "accept", "reject"]) {
  test(`${auto ? `forged auto=${auto}` : "ordinary"} offer GET never mutates`, async ({ page, app }) => {
    test.setTimeout(45_000);
    const posts = await installPage(page, { auto });
    await page.goto(`${app.baseUrl}/appointment/offer/tok-e2e`, { waitUntil: "load" });
    await page.evaluate(async () => {
      const script = document.querySelector("script[data-token]");
      const url = script.textContent.match(/import\("([^"]+)"\)/)[1];
      await import(url);
    });
    expect(posts, `auto=${auto || "none"}`).toEqual([]);
  });
}

for (const failure of ["rejected module", "throwing mount"]) {
  test(`${failure} paints one visible reload control that actually reloads`, async ({ page, app }) => {
    test.setTimeout(45_000);
    let loads = 0;
    const posts = await installPage(page);
    await page.route("**/static/modules/turnos/offer.*.mjs", async (route) => {
      loads += 1;
      if (failure === "rejected module") await route.abort("failed");
      else await route.fulfill({ contentType: "text/javascript", body: "export function mount(){throw new Error('synthetic mount failure')}" });
    });
    await page.goto(`${app.baseUrl}/appointment/offer/tok-e2e`, { waitUntil: "domcontentloaded" });
    const retry = page.locator("[data-offer-retry]");
    await expect(retry).toBeVisible();
    expect(await retry.count()).toBe(1);
    await page.locator("#oferta-rechazar").focus();
    await page.keyboard.press("Enter");
    expect(await page.evaluate(() => location.hash)).toBe("");
    expect(posts).toEqual([]);
    await Promise.all([page.waitForEvent("domcontentloaded"), retry.click()]);
    await expect(page.locator("[data-offer-retry]")).toBeVisible();
    expect(loads).toBe(2);
    expect(posts).toEqual([]);
  });
}

for (const [name, response, rawBody, retryable] of [
  ["503", { status: 503, body: { error: "temporary" } }, null, true],
  ["malformed shape", { status: 200 }, "{}", true],
  ["410", { status: 410, body: { error: "gone" } }, null, false],
]) {
  test(`${name} ${retryable ? "unlocks for retry" : "remains terminal"}`, async ({ page, app }) => {
    test.setTimeout(45_000);
    const posts = await installPage(page, { response, rawBody });
    await page.goto(`${app.baseUrl}/appointment/offer/tok-e2e`, { waitUntil: "load" });
    await page.locator("#oferta-aceptar").click();
    const expected = retryable
      ? "No pudimos procesar tu respuesta. Probá de nuevo."
      : "Este turno ya no está disponible.";
    await expect(page.locator("#oferta-msg")).toHaveText(expected);
    expect(await page.locator("#oferta-aceptar").isDisabled(), name).toBe(!retryable);
    expect(await page.locator("#oferta-rechazar").evaluate((el) => el.style.pointerEvents), name).toBe(retryable ? "" : "none");
    expect(posts.length).toBe(1);
  });
}

test("pagehide makes a held early intent inert before the canonical module arrives", async ({ page, app, cleanup }) => {
  test.setTimeout(45_000);
  const gate = holdGate(cleanup);
  try {
    const posts = await installPage(page);
    await page.route("**/static/modules/turnos/offer.*.mjs", async (route) => { await gate.held; await route.continue(); });
    await page.goto(`${app.baseUrl}/appointment/offer/tok-e2e`, { waitUntil: "domcontentloaded" });
    await page.locator("#oferta-aceptar").click();
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide")));
    gate.release();
    await page.evaluate(async () => {
      const script = document.querySelector("script[data-token]");
      const url = script.textContent.match(/import\("([^"]+)"\)/)[1];
      await import(url);
    });
    await holdsFor(async () => { expect(posts).toEqual([]); }, { during: 300 });
  } finally { gate.release(); }
});

test("accepted offer redirects after one POST", async ({ page, app }) => {
  test.setTimeout(45_000);
  const posts = await installPage(page, { response: { ok: true } });
  await page.goto(`${app.baseUrl}/appointment/offer/tok-e2e`, { waitUntil: "load" });
  const [redirect] = await Promise.all([
    page.waitForResponse((response) => response.url() === `${app.baseUrl}/my-requests`
      && response.request().isNavigationRequest()),
    page.locator("#oferta-aceptar").click(),
  ]);
  expect(redirect.status()).toBe(302);
  expect(redirect.headers().location).toBe("/account#turnos");
  // Follow the real server alias; /my-requests is not the terminal page.
  await page.waitForURL(`${app.baseUrl}/account#turnos`, { timeout: 5_000 });
  expect(posts).toEqual([{ url: `${app.baseUrl}/api/offer/tok-e2e/accept`, method: "POST" }]);
});

for (const width of [360, 390, 768, 1024, 1280]) {
  const surfaces = width === 360 || width === 1280 ? ["ordinary", "error", "recovery"] : ["ordinary"];
  for (const surface of surfaces) {
    test(`offer ${surface} surface fits at ${width}px`, async ({ page, app }, testInfo) => {
      test.setTimeout(45_000);
      await page.setViewportSize({ width, height: 900 });
      const posts = await installPage(page, { response: surface === "error" ? { status: 503, body: { error: "temporary" } } : { ok: true } });
      if (surface === "recovery") await page.route("**/static/modules/turnos/offer.*.mjs", (route) => route.abort("failed"));
      await page.goto(`${app.baseUrl}/appointment/offer/tok-e2e`, { waitUntil: "load" });
      if (surface === "error") {
        await page.locator("#oferta-aceptar").click();
        await expect(page.locator("#oferta-msg")).toHaveText("No pudimos procesar tu respuesta. Probá de nuevo.");
        expect(await page.locator("#oferta-aceptar").isDisabled()).toBe(false);
      }
      if (surface === "recovery") await expect(page.locator("[data-offer-retry]")).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const geometry = await page.evaluate(() => ({
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
        recovery: document.querySelector("[data-offer-retry]")?.getBoundingClientRect().height,
        recoveryDisplay: document.querySelector("[data-offer-retry]") &&
          getComputedStyle(document.querySelector("[data-offer-retry]")).display,
        statusFont: parseFloat(getComputedStyle(document.getElementById("oferta-msg")).fontSize),
        rejectFont: parseFloat(getComputedStyle(document.getElementById("oferta-rechazar")).fontSize),
      }));
      expect(geometry.document <= width && geometry.body <= width, JSON.stringify(geometry)).toBe(true);
      expect(geometry.statusFont >= 16 && geometry.rejectFont >= 16, "patient controls and status stay readable").toBe(true);
      if (surface === "recovery") {
        expect(geometry.recovery, "new reload control is touch sized").toBeGreaterThanOrEqual(44);
        expect(geometry.recoveryDisplay, "reload starts its own row, not inside error prose").toBe("block");
      }
      expect(posts.length).toBe(surface === "error" ? 1 : 0);
      if (width === 360 || width === 1280) await saveShot(testInfo, page, join(SCREENSHOTS, `${surface}-${width}.png`), { fullPage: true });
    });
  }
}
