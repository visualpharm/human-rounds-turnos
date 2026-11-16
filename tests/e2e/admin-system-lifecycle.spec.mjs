// Actual ASGI template, immutable native module, shared session module and browser.
// Disposable synthetic admin; deliberate API failures never contact live services.
//
// Port of tests/e2e/admin-system-lifecycle.e2e.mjs (14 tests, same titles, same order).
//
// Differences from the node:test original:
// - Every test is independent (its own signed-in page), so the file is NOT serial. The page comes
//   from the `adminPage` fixture (signed in through the same /admin/login?next=/admin/account
//   form); the per-width tests set the viewport on it before the first `/admin/system` load
//   (the original created the context with that width). Height stays 900 (`test.use`).
// - Screenshots go through `saveShot` to the same files as before (artifactPath("human-rounds",
//   "system-lifecycle-audit", "browser-<timestamp>")) and are attached to the report.
// - The page's 8 s deadline covers the module and its dependencies only (`dispose.loaded`): a
//   data answer held past it is the screen's own «Cargando…», never the recovery alert, and the
//   cards paint when it arrives (`System slow answer…`). A stalled import past the deadline shows
//   the shared late copy beside the screen and still mounts (`late main import…`, 2026-11-16). Requests are held on a gate (`holdGate`, released on cleanup).
//   The same gate pattern replaces the hand-made `held` promise elsewhere; `try/finally { release();
//   context.close() }` is the `cleanup` fixture.
// - Waits and checks on the page are web-first (`toBeVisible`, `toHaveAttribute`, `toHaveURL`);
//   the request counters (`calls`), the «no card» / «no alert» counts and the fits/size
//   measurements stay point-in-time reads at the moment the original read them.
// - Locators that stay CSS: `.sis-card`, `.sis-fix`, `[data-retry]` and `#sec-sistema` are the
//   Sistema screen's own markup; the alert is `getByRole("alert")` inside `#sec-sistema`.
import {
  test, expect, saveShot, holdGate, holdsFor, artifactPath,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_system_lifecycle" },
  viewport: { width: 1280, height: 900 },
});

const evidence = artifactPath("human-rounds", "system-lifecycle-audit", `browser-${new Date().toISOString().replaceAll(":", "-")}`);
const fresh = { estado: "ok", ok: 1, total: 1, clusters: [{
  estado: "warn", titulo: "Synthetic mail service", resumen: "Pending configuration",
  items: [{ label: "Domain", valor: "mail.synthetic.example", estado: "warn", campo: "MAILGUN_DOMAIN" }],
}] };
const good = (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fresh) });

async function fits(page) {
  const size = await page.evaluate(() => ({ view: innerWidth, content: document.documentElement.scrollWidth }));
  expect(size.content, JSON.stringify(size)).toBeLessThanOrEqual(size.view + 1);
}
async function restore(page) {
  // Synthetic lifecycle proof, not a claim of actual BFCache admission.
  await page.evaluate(() => {
    dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
}

for (const width of [360, 390, 768, 1024, 1280]) {
  test(`System ${width}px: cards, settings deep link, outage and real reload`, async ({ adminPage: page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let calls = 0;
    await page.route("**/api/admin/system", (route) => { calls++; return good(route); });
    await page.goto("/admin/system");
    await expect(page.locator(".sis-card")).toBeVisible();
    expect(calls).toBe(1);
    await expect(page.locator(".sis-fix")).toHaveAttribute("href", "/admin/settings?field=MAILGUN_DOMAIN");
    await fits(page);
    await saveShot(testInfo, page, `${evidence}/system-${width}.png`, { fullPage: true });
    await page.unroute("**/api/admin/system");
    await page.route("**/api/admin/system", (route) => route.fulfill({ status: 503, body: "{}" }));
    await restore(page);
    const section = page.locator("#sec-sistema");
    await expect(section.getByRole("alert")).toBeVisible();
    await expect(section.locator("[data-retry]")).toBeVisible();
    const action = await stableBox(section.locator("[data-retry]"), "system retry button");
    expect(action.width, JSON.stringify(action)).toBeGreaterThanOrEqual(44);
    expect(action.height, JSON.stringify(action)).toBeGreaterThanOrEqual(44);
    await fits(page);
    await saveShot(testInfo, page, `${evidence}/unavailable-${width}.png`, { fullPage: true });
    await page.unroute("**/api/admin/system");
    await page.route("**/api/admin/system", good);
    await section.locator("[data-retry]").click();
    await expect(page.locator(".sis-card")).toBeVisible();
    await page.locator(".sis-fix").click();
    await expect(page).toHaveURL(/\/admin\/settings\?field=MAILGUN_DOMAIN/);
  });
}

test("System slow answer past the page deadline is a wait, never the recovery alert, and paints when it arrives", async ({ adminPage: page, cleanup }) => {
  const gate = holdGate(cleanup);
  let requested = false;
  await page.route("**/api/admin/system", async (route) => {
    requested = true;
    await gate.held;
    await good(route).catch(() => {}); // The page may have moved on without it.
  });
  await page.goto("/admin/system");
  const section = page.locator("#sec-sistema");
  await expect.poll(() => requested, { message: "No /api/admin/system request", timeout: 10000 }).toBe(true);
  await expect(section).toContainText("Cargando…");
  // 10 s with the answer held: past the template's 8 s deadline, the screen keeps waiting.
  await holdsFor(async () => {
    expect(await section.getByRole("alert").count()).toBe(0);
    expect(await section.locator("[data-retry]").count()).toBe(0);
  }, { during: 10000, every: 250 });
  gate.release();
  await expect(page.locator(".sis-card").first()).toBeVisible();
  expect(await section.getByRole("alert").count()).toBe(0);
});

for (const failure of ["main-import", "i18n-import", "offline", "forbidden"]) {
  test(`System ${failure}: actual bootstrap exposes working recovery`, async ({ adminPage: page }) => {
    const api = "**/api/admin/system";
    const asset = failure === "main-import" ? "**/static/platform/admin-system.*.mjs" : "**/static/platform/i18n.*.mjs";
    if (failure.endsWith("import")) await page.route(asset, (route) => route.abort("failed"));
    if (failure === "offline") await page.route(api, (route) => route.abort("failed"));
    else if (failure === "forbidden") await page.route(api, (route) => route.fulfill({ status: 403, contentType: "application/json", body: '{"error":"forbidden"}' }));
    else await page.route(api, good);
    await page.goto("/admin/system");
    const section = page.locator("#sec-sistema");
    await expect(section.getByRole("alert")).toBeVisible({ timeout: 12000 });
    expect(new URL(page.url()).pathname).toBe("/admin/system");
    expect(await page.locator(".sis-card").count()).toBe(0);
    await page.unroute(asset);
    await page.unroute(api);
    await page.route(api, good);
    await section.locator("[data-retry]").click();
    await expect(page.locator(".sis-card")).toBeVisible();
  });
}

for (const status of [401, 403]) {
  test(`System ${status}: shared confirmed session transition owns navigation`, async ({ adminPage: page }) => {
    await page.route("**/api/admin/system", (route) => route.fulfill({ status, contentType: "application/json",
      body: JSON.stringify({ error: status === 401 ? "no_autenticado" : "2fa_requerido" }) }));
    if (status === 401) await page.route("**/api/admin/whoami", (route) => route.fulfill({ status: 401,
      contentType: "application/json", body: '{"error":"no_autenticado"}' }));
    await page.goto("/admin/system");
    await expect(page).toHaveURL(status === 401 ? /\/admin\/login\?next=/ : /\/admin\/account\?setup=2fa/);
    expect(await page.locator('#sec-sistema').getByRole("alert").count()).toBe(0);
  });
}

test("late main import past the deadline shows the late copy, then mounts once", async ({ adminPage: page, cleanup }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const gate = holdGate(cleanup);
  let calls = 0;
  await page.route("**/static/platform/admin-system.*.mjs", async (route) => {
    await gate.held;
    await route.continue();
  });
  await page.route("**/api/admin/system", (route) => { calls++; return good(route); });
  await page.goto("/admin/system", { waitUntil: "domcontentloaded" });
  const section = page.locator("#sec-sistema");
  await expect(page.locator(".screen-load-slow [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await section.locator("[data-retry]").count(), "the copy never replaces the screen").toBe(0);
  expect(calls).toBe(0);
  gate.release();
  await expect(page.locator(".sis-card")).toBeVisible();
  expect(calls).toBe(1);
  await expect(page.locator(".screen-load-slow")).toHaveCount(0);
});

test("actual page owner remount aborts stale status and paints the fresh response", async ({ adminPage: page, cleanup }) => {
  const gate = holdGate(cleanup);
  let settled;
  const oldDelivery = new Promise((resolve) => { settled = resolve; });
  let calls = 0;
  await page.route("**/api/admin/system", async (route) => {
    calls++;
    if (calls === 1) {
      gate.arrive();
      await gate.held;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        estado: "error", ok: 0, total: 1, clusters: [{ titulo: "Obsolete status", items: [] }],
      }) }).catch(() => {});
      settled();
    } else await good(route);
  });
  await page.goto("/admin/system");
  await gate.arrived;
  await restore(page);
  await expect(page.locator(".sis-card")).toBeVisible();
  gate.release();
  await oldDelivery;
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  const text = await page.locator("#sec-sistema").innerText();
  expect(text).toMatch(/Synthetic mail service/);
  expect(text).not.toMatch(/Obsolete status/);
  expect(calls).toBe(2);
});
