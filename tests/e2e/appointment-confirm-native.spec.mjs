// Native confirmation regression: the document is rendered by the real Python
// owner and the controller is the real fingerprinted module. Only appointment
// POST outcomes and module transport timing are synthetic.
//
// Differences from the node:test original (tests/e2e/appointment-confirm-native.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//     `page` replaces state.browser.newPage(). The two phone-width tests set the 390x844 viewport with
//     page.setViewportSize() before navigating; the default viewport is the original's default too.
//   - One test = one page (2026-11-05): each auto= case and each width × surface is its own test,
//     so a loaded machine never has to fit four page loads into one 45 s budget. The "no uncaught
//     page errors" check runs in an afterEach.
//   - The held module / i18n requests are promise gates (holdGate) released by the test, as in the
//     original; the gate is also released in `finally`, so a failing test never leaves a route hanging.
//   - waitForFunction(confirm-msg text === "Listo") is expect(#confirm-msg).toHaveText("Listo");
//     waitForNavigation (deprecated) is waitForEvent("domcontentloaded") started before the click.
//   - Snapshots that guard "nothing was posted / no hash change yet" stay point-in-time reads
//     (expect(await ...) / expect(posts)), never retrying ones.
//   - Evidence screenshots keep their place (artifacts/confirmation-native under the repo root) and
//     are also attached to the report (saveShot, fullPage kept). { timeout: 45000 } is test.setTimeout(45_000).
//   - Locators: [data-confirm-*], #confirm-msg and the module/i18n URL patterns stay CSS/route globs:
//     they are the controller's data contract and the fingerprinted-asset URLs under test.
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { test, expect, holdGate, saveShot, ROOT } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_confirm_native" } });

const pageErrors = new WeakMap();

test.afterEach(async ({ page }) => {
  if (!pageErrors.has(page)) return;
  await page.unrouteAll({ behavior: "wait" });
  expect(pageErrors.get(page), "confirmation has no uncaught page errors").toEqual([]);
});

// The page is rendered by the real Python owner once per worker for every mode
// the tests use, before the tests: a python3 start that imports the app takes
// tens of seconds on a loaded machine, and paying it inside a page load is what
// ran the tests out of their navigation and test time.
let confirmHtml = null;
test.beforeAll(() => {
  test.setTimeout(180_000);
  const code = [
    "import json",
    "from app.modules.turnos import appointment_confirm as c",
    "print(json.dumps({m: c._ask_page('tok-e2e', 'Consulta', 'CAPS', 'Mañana', m).decode() for m in ('', 'si', 'no')}))",
  ].join("; ");
  const out = execFileSync("python3", ["-c", code], { cwd: ROOT, encoding: "utf8" });
  confirmHtml = JSON.parse(out.trim().split("\n").pop());
});

function renderedConfirm(mode = "", auto = "") {
  const html = confirmHtml[mode];
  return auto ? html.replace('data-token="tok-e2e"', `data-token="tok-e2e" data-auto="${auto}"`) : html;
}

async function installPage(page, { mode = "", auto = "", response = { ok: true, mensaje: "Listo" } } = {}) {
  const posts = [];
  const errors = [];
  pageErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.route("**/appointment/confirm/tok-e2e**", (route) => route.fulfill({
    status: 200, contentType: "text/html", body: renderedConfirm(mode, auto),
  }));
  await page.route("**/api/confirm/tok-e2e/**", async (route) => {
    posts.push({ url: route.request().url(), method: route.request().method() });
    await route.fulfill({ status: response.status || 200, contentType: "application/json", body: JSON.stringify(response.body || response) });
  });
  return posts;
}

for (const action of ["si", "no"]) {
  test(`real confirmation controller keeps an early ${action} click and ignores a second click while its module is held`, async ({ page, app, cleanup }) => {
    test.setTimeout(45_000);
    await page.setViewportSize({ width: 390, height: 844 });
    const gate = holdGate(cleanup);
    try {
      const posts = await installPage(page);
      await page.route("**/static/modules/turnos/confirm.*.mjs", async (route) => { gate.arrive(); await gate.held; await route.continue(); });
      await page.goto(`${app.baseUrl}/appointment/confirm/tok-e2e`, { waitUntil: "domcontentloaded" });
      await gate.arrived;
      const target = page.locator(`[data-confirm-action="${action}"]`);
      await target.click();
      await target.click();
      expect(await page.evaluate(() => location.hash)).toBe("");
      expect(posts).toEqual([]);
      gate.release();
      await expect(page.locator("#confirm-msg")).toHaveText("Listo");
      expect(posts).toEqual([{ url: `${app.baseUrl}/api/confirm/tok-e2e/${action}`, method: "POST" }]);
    } finally {
      gate.release();
    }
  });
}

for (const [auto, title] of [
  ["", "an ordinary confirmation page never mutates"],
  ["no", "a forged auto=no confirmation page never mutates"],
  ["si", "an auto=yes confirmation page mutates once"],
]) {
  test(title, async ({ page, app }) => {
    test.setTimeout(45_000);
    const posts = await installPage(page, { mode: auto === "si" ? "si" : "", auto: auto === "no" ? "no" : "" });
    await page.goto(`${app.baseUrl}/appointment/confirm/tok-e2e`, { waitUntil: "load" });
    // Import the exact URL already emitted by the real loader, then flush its
    // mount callback. No arbitrary 80ms success window for a slow module.
    await page.evaluate(async () => {
      const script = document.querySelector("script[data-token]");
      const url = script.textContent.match(/import\("([^"]+)"\)/)[1];
      await import(url);
    });
    if (auto === "si") await expect(page.locator("#confirm-msg")).toHaveText("Listo");
    expect(posts.length, `auto=${auto || "none"}`).toBe(auto === "si" ? 1 : 0);
    if (auto === "si") expect(posts[0].url).toBe(`${app.baseUrl}/api/confirm/tok-e2e/si`);
  });
}

for (const i18nMode of ["held", "rejected"]) {
  test(`explicit decline waits for ${i18nMode} i18n readiness without being lost`, async ({ page, app, cleanup }) => {
    test.setTimeout(45_000);
    const gate = holdGate(cleanup);
    try {
      const posts = await installPage(page);
      await page.route("**/static/platform/i18n.*.mjs", async (route) => {
        gate.arrive();
        await gate.held;
        if (i18nMode === "rejected") await route.abort("failed");
        else await route.continue();
      });
      await page.goto(`${app.baseUrl}/appointment/confirm/tok-e2e`, { waitUntil: "domcontentloaded" });
      await gate.arrived;
      await page.locator('[data-confirm-action="no"]').click();
      expect(posts).toEqual([]);
      gate.release();
      await expect(page.locator("#confirm-msg")).toHaveText("Listo");
      expect(posts.length).toBe(1);
      expect(posts[0].url).toBe(`${app.baseUrl}/api/confirm/tok-e2e/no`);
    } finally {
      gate.release();
    }
  });
}

test("rejected confirmation module paints one reload control and its retry really reloads", async ({ page, app }) => {
  test.setTimeout(45_000);
  let loads = 0;
  const posts = await installPage(page);
  await page.route("**/static/modules/turnos/confirm.*.mjs", async (route) => { loads += 1; await route.abort("failed"); });
  await page.goto(`${app.baseUrl}/appointment/confirm/tok-e2e`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("[data-confirm-retry]")).toBeVisible();
  expect(await page.locator("[data-confirm-retry]").count()).toBe(1);
  await Promise.all([page.waitForEvent("domcontentloaded"), page.locator("[data-confirm-retry]").click()]);
  await expect(page.locator("[data-confirm-retry]")).toBeVisible();
  expect(loads).toBe(2);
  expect(posts).toEqual([]);
});

// Rendered evidence is generated from the real server template and canonical
// assets, with synthetic appointment outcomes only. Keep it outside /tmp.
for (const width of [360, 1280]) {
  for (const surface of ["ordinary", "decline", "success", "module-recovery"]) {
    test(`confirmation ${surface} surface fits at ${width}px`, async ({ page, app }, testInfo) => {
      test.setTimeout(45_000);
      const output = join(ROOT, "artifacts/confirmation-native");
      await page.setViewportSize({ width, height: 900 });
      const posts = await installPage(page, { mode: surface === "decline" ? "no" : "" });
      if (surface === "module-recovery") {
        await page.route("**/static/modules/turnos/confirm.*.mjs", (route) => route.abort("failed"));
      }
      await page.goto(`${app.baseUrl}/appointment/confirm/tok-e2e`, { waitUntil: "load" });
      if (surface === "module-recovery") await expect(page.locator("[data-confirm-retry]")).toBeVisible();
      if (surface === "success") {
        await page.locator('[data-confirm-action="si"]').click();
        await expect(page.locator("#confirm-msg")).toHaveText("Listo");
      }
      await page.evaluate(() => document.fonts.ready);
      const geometry = await page.evaluate(() => ({
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
        recovery: document.querySelector("[data-confirm-retry]")?.getBoundingClientRect().height,
      }));
      expect(geometry.document <= width && geometry.body <= width, JSON.stringify(geometry)).toBe(true);
      if (surface === "module-recovery") expect(geometry.recovery, "visible reload target is touch sized").toBeGreaterThanOrEqual(44);
      expect(posts.length).toBe(surface === "success" ? 1 : 0);
      await saveShot(testInfo, page, join(output, `${surface}-${width}.png`), { fullPage: true });
    });
  }
}
