// Real local ASGI, login, controller and database. Only explicit failure
// cases intercept requests. Synthetic lifecycle events are not BFCache proof.
//
// Port of tests/e2e/admin-appearance-lifecycle.e2e.mjs (23 tests, same titles, same order).
//
// Differences from the node:test original:
// - The tests append to one shared `measurements` record that is written to measurements.json
//   (with its completeness flag) after the file, and several of them change the stored settings
//   in order (colour, Turnstile, logo), so the file is `serial`: a failure skips the rest and a
//   retry re-runs everything on a fresh worker with a fresh record.
// - `test.before` (admin enables the English catalog through the settings API) is `test.beforeAll`;
//   `test.after` (writes measurements.json) is `test.afterAll`.
// - `session(lang)` uses the runner's `page` (390x900 through `test.use`), registers the same
//   layout-shift observer init script, signs in with the original sequence (commit-level wait for
//   /admin/solicitudes, then about:blank) and sets the `lang` cookie; the `finally { context.close() }`
//   blocks are gone. The explicit `lang` cookie (and LANG_DEFAULT=es) decides the page language, so the
//   runner's default Accept-Language (en-US) does not matter here.
// - Screenshots go through `saveShot` to the same files as before (artifactPath("human-rounds",
//   "appearance-native-lifecycle", <timestamp>)) and are attached to the report; the per-width
//   `*-appearance.html` snapshots are written next to them with `fs.writeFile`.
// - «No branding request after 5 s» is `expect.poll(...).toBe(true)` with a 5 s timeout instead of a
//   `Promise.race` against a rejecting timer; the hand-made `hold` promises are `holdGate(cleanup)`.
// - Waits are web-first (`toBeVisible`, `toHaveCount`, `toHaveValue`, `toBeChecked`, `toBeAttached`...)
//   and the keyboard-toggle wait (`waitForFunction` on checked + «Listo» message) is two web-first
//   expects; every check that catches a race stays a point-in-time read at the original moment
//   (`ready()`'s hidden status, request counters, the replacement-owner text, the checkbox restored
//   after a refused mutation, the visible removal button after a cancelled dialog, and every
//   absence `count()` of 0 the original read once).
// - Locators that stay CSS: `#sec-configuracion`, `.cfg-h2`, `#cfg-*`, `[data-retry]`, `[data-tema]`,
//   `[data-auth]` and friends are the Appearance screen's own markup contract; the geometry sweep
//   (gutters, 16px fonts, 44px controls) is one in-page evaluation exactly as before.
import fs from "node:fs/promises";
import { join } from "node:path";
import {
  test, expect, saveShot, holdGate, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";

test.use({
  // RENAPER is a choice only with its credentials or the fake (2026-11-08 D8).
  appOptions: { dbName: "sana_turnos_test_appearance_lifecycle", requests: true, env: { TURNOS_FAKE_RENAPER: "1" } },
  viewport: { width: 390, height: 900 },
});
test.describe.configure({ mode: "serial" });

const WIDTHS = [360, 390, 768, 834, 1024, 1280, 1440];
const evidence = artifactPath("human-rounds", "appearance-native-lifecycle", new Date().toISOString().replaceAll(":", "-"));
const measurements = [];

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
    const response = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(response.status()).toBe(200);
  } finally { await context.close(); }
});

test.afterAll(async () => {
  await fs.writeFile(join(evidence, "measurements.json"), JSON.stringify({
    expected: WIDTHS.length * 2, recorded: measurements.length,
    complete: measurements.length === WIDTHS.length * 2 && new Set(measurements.map((row) => row.id)).size === WIDTHS.length * 2,
    measurements, boundary: "Local synthetic test database. Not production or BFCache admission evidence.",
  }, null, 2));
});

async function session(page, app, lang = "es") {
  const context = page.context();
  await context.addInitScript(() => {
    window.__appearanceShifts = [];
    window.__appearanceObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput)
        window.__appearanceShifts.push({ value: entry.value, startTime: entry.startTime });
    });
    window.__appearanceObserver.observe({ type: "layout-shift", buffered: true });
  });
  await page.goto("/admin/login?next=%2Fadmin%2Fsolicitudes", { waitUntil: "load" });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForURL(/\/admin\/solicitudes/, { waitUntil: "commit", timeout: 15000 });
  await page.goto("about:blank");
  await context.addCookies([{ name: "lang", value: lang, url: app.baseUrl }]);
}
async function ready(page) {
  await page.waitForFunction(() => document.querySelectorAll("#sec-configuracion h2.cfg-h2").length === 5);
  expect(await page.locator("#configuracion-status").isHidden()).toBe(true);
}

for (const lang of ["es", "en"]) test(`Appearance ${lang}: real cold loads at required viewport widths`, async ({ page, app }, testInfo) => {
  await session(page, app, lang);
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/appearance", { waitUntil: "load" });
    await ready(page);
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const row = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const overflow = [...document.querySelectorAll("#sec-configuracion *")].flatMap((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && getComputedStyle(el).visibility === "visible" && (r.left < -1 || r.right > vw + 1)
          ? [{ tag: el.tagName, id: el.id, left: r.left, right: r.right }] : [];
      });
      const shifts = [...window.__appearanceShifts, ...window.__appearanceObserver.takeRecords()
        .filter((entry) => !entry.hadRecentInput).map((entry) => ({ value: entry.value, startTime: entry.startTime }))];
      let cls = 0, sum = 0, first = 0, last = -Infinity;
      for (const entry of shifts) {
        if (entry.startTime - last >= 1000 || entry.startTime - first >= 5000) { sum = 0; first = entry.startTime; }
        sum += entry.value; last = entry.startTime; cls = Math.max(cls, sum);
      }
      const bounds = document.querySelector("#sec-configuracion h2").getBoundingClientRect();
      const host = document.getElementById("sec-configuracion");
      const fields = [...host.querySelectorAll('input:not([type="checkbox"]):not([type="file"])')].map((el) => ({
        id: el.id, font: parseFloat(getComputedStyle(el).fontSize),
      }));
      const model = host.querySelector("#cfg-voice-model");
      const voiceModel = { tag: model.tagName, editable: model.isContentEditable, font: parseFloat(getComputedStyle(model).fontSize) };
      const controls = [...host.querySelectorAll("button, label.cnt-check, label.btn")].map((el) => {
        const r = el.getBoundingClientRect();
        return { id: el.id, width: r.width, height: r.height };
      });
      return { overflow, pageOverflow: document.documentElement.scrollWidth - vw,
        lang: document.documentElement.lang, shifts, cls, leftGutter: bounds.left,
        rightGutter: vw - bounds.right, fields, controls, voiceModel,
        optionLabels: [...host.querySelectorAll("[data-auth] .cfg-name")].map((el) => el.textContent),
        checkboxLabel: host.querySelector("label.cnt-check")?.textContent,
        tinyBadges: host.querySelectorAll(".cfg-badge").length };
    });
    const id = `${lang}-${width}`;
    measurements.push({ id, width, ...row });
    await saveShot(testInfo, page, join(evidence, id + ".png"), { fullPage: true });
    expect(row.lang).toBe(lang);
    expect(row.pageOverflow, id).toBe(0);
    expect(row.overflow, id).toEqual([]);
    expect(Number.isFinite(row.cls)).toBe(true);
    expect(row.leftGutter >= 15 && row.rightGutter >= 15, id + " form needs inline gutters").toBe(true);
    // Voice policy is read-only; the colour and its hex are the editable
    // fields, the logo file is picked with the panel's button (2026-11-08).
    // Do not invent another input to satisfy a count.
    expect(row.fields.map((field) => field.id).sort()).toEqual(["cfg-mk-color", "cfg-mk-hex"]);
    expect(row.controls.some((control) => control.id === "cfg-mk-pick"), id + " logo picker").toBe(true);
    expect(row.voiceModel.tag).toBe("SPAN");
    expect(row.voiceModel.editable).toBe(false);
    expect(row.voiceModel.font).toBeGreaterThanOrEqual(16);
    for (const field of row.fields) expect(field.font, id + " " + field.id).toBeGreaterThanOrEqual(16);
    // The panel's buttons share one height (2026-11-08, R4): 36px under a
    // mouse, 44px on a touch screen (the pointer:coarse rule, pinned in
    // tests/admin-button-system.test.mjs). This run has a mouse at every width.
    for (const control of row.controls) expect(control.width >= 44 && control.height >= 36, id + " " + control.id).toBe(true);
    expect(row.tinyBadges).toBe(0);
    if (lang === "en") {
      expect(row.optionLabels).toEqual(["National ID and password", "Digital identity (RENAPER)"]);
      expect(row.checkboxLabel).toMatch(/^Require Cloudflare/);
    } else {
      expect(row.optionLabels).toEqual(["DNI y contraseña", "Identidad digital (RENAPER)"]);
    }
    // Preserve the actual rendered scope for the independent text-density
    // evaluator. Do not strip its warnings or other inconvenient elements.
    await fs.writeFile(join(evidence, id + "-appearance.html"), await page.locator("#sec-configuracion").evaluate((el) => el.outerHTML));
  }
  expect(errors).toEqual([]);
});

test("Appearance saves and resets color through the real local backend, including reload persistence", async ({ page, app }) => {
  await session(page, app);
  await page.goto("/admin/appearance");
  await ready(page);
  await page.locator("#cfg-mk-hex").fill("#345678");
  await page.locator("#cfg-mk-save").click();
  await expect(page.locator("#cfg-mk-reset")).toBeVisible();
  await page.reload();
  await ready(page);
  await expect(page.locator("#cfg-mk-hex")).toHaveValue("#345678");
  await page.locator("#cfg-mk-reset").click();
  await expect(page.locator("#cfg-mk-reset")).toHaveCount(0);
  const response = await page.request.get("/api/admin/branding");
  expect(response.status()).toBe(200);
  const branding = await response.json();
  expect(branding.color).toBeNull();
  await expect(page.locator("#cfg-mk-hex")).toHaveValue(branding.color_efectivo);
  await page.reload();
  await ready(page);
  await expect(page.locator("#cfg-mk-hex")).toHaveValue(branding.color_efectivo);
});

for (const dependency of ["admin-appearance", "admin-session", "i18n"]) test(`Appearance failed ${dependency} import has a working Reload`, async ({ page, app }) => {
  await session(page, app);
  const matcher = `**/static/platform/${dependency}.*.mjs`;
  await page.route(matcher, (route) => route.fulfill({ status: 503, body: "" }));
  await page.goto("/admin/appearance", { waitUntil: "load" });
  await expect(page.locator("#sec-configuracion [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-configuracion h2.cfg-h2").count()).toBe(0);
  await page.unroute(matcher);
  await page.locator("#sec-configuracion [data-retry]").click();
  await ready(page);
});

test("Appearance partial failure preserves healthy controls and Reload restores all blocks", async ({ page, app }, testInfo) => {
  await session(page, app);
  const matcher = "**/api/admin/theme";
  await page.route(matcher, (route) => route.fulfill({ status: 503, contentType: "application/json", body: "{}" }));
  await page.goto("/admin/appearance");
  await expect(page.locator("#cfg-retry")).toBeVisible();
  await expect(page.locator("#sec-configuracion h2.cfg-h2")).toHaveCount(4);
  await expect(page.locator("#cfg-voice-model")).toBeVisible();
  await saveShot(testInfo, page, join(evidence, "partial-failure-390.png"), { fullPage: true });
  await page.unroute(matcher);
  await page.locator("#cfg-retry").click();
  await ready(page);
});

for (const [endpoint, selector] of [
  ["theme", "[data-tema]"], ["auth-mode", "[data-auth]"], ["turnstile", "#cfg-ts-check"],
  ["branding", "#cfg-mk-save"], ["chat-voice", "#cfg-voice-model"],
]) test(`Appearance absent ${endpoint} hides only its own controls`, async ({ page, app }) => {
  await session(page, app);
  await page.route("**/api/admin/" + endpoint, (route) => route.fulfill({ status: 404, contentType: "application/json", body: "{}" }));
  await page.goto("/admin/appearance");
  await page.waitForFunction(() => document.querySelectorAll("#sec-configuracion h2.cfg-h2").length === 4);
  expect(await page.locator("#sec-configuracion " + selector).count()).toBe(0);
  expect(await page.locator("#configuracion-status").isHidden()).toBe(true);
  expect(await page.locator("#cfg-retry").count()).toBe(0);
});

for (const [label, status, body, whoamiExpired, destination] of [
  ["ordinary forbidden", 403, { error: "forbidden" }, false, null],
  ["unconfirmed expired response", 401, { error: "no_autenticado" }, false, null],
  ["confirmed expired session", 401, { error: "no_autenticado" }, true, "/admin/login"],
  ["required second factor", 403, { error: "2fa_requerido" }, false, "/admin/account"],
]) test(`Appearance session boundary: ${label}`, async ({ page, app }) => {
  await session(page, app);
  await page.route("**/api/admin/branding", (route) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) }));
  if (whoamiExpired) await page.route("**/api/admin/whoami", (route) => route.fulfill({
    status: 401, contentType: "application/json", body: '{"error":"no_autenticado"}',
  }));
  await page.goto("/admin/appearance");
  if (destination) {
    await page.waitForURL((url) => url.pathname === destination, { timeout: 10000 });
    const url = new URL(page.url());
    expect(url.searchParams.get(destination === "/admin/login" ? "next" : "setup")).toBe(
      destination === "/admin/login" ? "/admin/appearance" : "2fa");
  } else {
    await expect(page.locator("#cfg-retry")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/admin/appearance");
    expect(await page.locator("#sec-configuracion h2.cfg-h2").count()).toBe(0);
  }
});

test("Appearance pagehide retires pending data and persisted pageshow creates a new owner", async ({ page, app, cleanup }) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  let requested = false;
  await page.route("**/api/admin/branding", async (route) => {
    requested = true; await gate.held;
    try { await route.continue(); } catch { /* the retired owner aborts its request */ }
  });
  await page.goto("/admin/appearance");
  await expect.poll(() => requested, { message: "No branding request", timeout: 5000 }).toBe(true);
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    document.getElementById("sec-configuracion").textContent = "Replacement owner";
  });
  gate.release();
  await page.unrouteAll({ behavior: "wait" });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(await page.locator("#sec-configuracion").innerText()).toBe("Replacement owner");
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await ready(page);
  expect(await page.locator("#sec-configuracion").innerText()).not.toMatch(/Replacement owner/);
});

test("Appearance import held past the deadline shows the late copy beside the screen, then mounts", async ({ page, app, cleanup }) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  let calls = 0;
  const matcher = "**/static/platform/admin-appearance.*.mjs";
  page.on("request", (request) => { if (new URL(request.url()).pathname === "/api/admin/branding") calls++; });
  await page.route(matcher, async (route) => { await gate.held; await route.continue(); });
  await page.goto("/admin/appearance", { waitUntil: "commit" });
  // The shared loader's late copy sits before the host, never inside it.
  await expect(page.locator(".screen-load-slow [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-configuracion [data-retry]").count()).toBe(0);
  expect(calls, "nothing starts before the module arrives").toBe(0);
  gate.release();
  await ready(page);
  expect(calls).toBe(1);
  await expect(page.locator(".screen-load-slow"), "the late answer clears the copy").toHaveCount(0);
});

test("Appearance shows dictation's state, not the service key, with no editable field", async ({ page, app }) => {
  await session(page, app);
  await page.goto("/admin/appearance");
  await ready(page);
  await expect(page.locator("#cfg-voice-model")).toHaveCount(1);
  expect(await page.locator("input#cfg-voice-model").count(), "no editable field: the server pins the model").toBe(0);
  expect(await page.locator("#cfg-voice-save").count(), "nothing to save").toBe(0);
  expect(await page.locator("#sec-configuracion").innerText(), "no service key on screen").not.toMatch(/fal-ai/);
});

test("Appearance obsolete assets stay denied while the real native module is served", async ({ page, app }) => {
  await session(page, app);
  await page.goto("/admin/appearance");
  await ready(page);
  for (const path of ["/admin-theme.js", "/static/admin-theme.2eaabaf7.js", "/static/admin-theme.deadbeef.js"]) {
    const response = await page.request.get(path);
    expect(response.status(), path).toBe(404);
    expect(response.headers()["cache-control"], path).toMatch(/no-store/);
  }
  const url = await page.evaluate(() => performance.getEntriesByType("resource")
    .find((entry) => /\/static\/platform\/admin-appearance\.[a-f0-9]+\.mjs/.test(entry.name))?.name);
  expect(url, "actual native entry was requested").toBeTruthy();
  const response = await page.request.get(url);
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toMatch(/immutable/);
  expect(await response.text()).toMatch(/export function mount\(/);
});

test("Appearance checkbox uses keyboard activation and persists the real setting", async ({ page, app }) => {
  await session(page, app);
  await page.goto("/admin/appearance");
  await ready(page);
  const initial = await page.locator("#cfg-ts-check").isChecked();
  await page.locator("#cfg-ts-check").focus();
  const saved = page.waitForResponse((r) => r.url().endsWith("/api/admin/turnstile") && r.request().method() === "POST");
  await page.keyboard.press("Space");
  expect((await saved).status()).toBe(200);
  await expect(page.locator("#cfg-ts-check")).toBeChecked({ checked: !initial });
  await expect(page.locator("#cfg-ts-msg")).toHaveText(/^Listo/);
  await page.reload();
  await ready(page);
  expect(await page.locator("#cfg-ts-check").isChecked()).toBe(!initial);
});

test("Appearance checkbox restores stored state when the server refuses a mutation", async ({ page, app }) => {
  await session(page, app);
  await page.route("**/api/admin/turnstile", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 200, contentType: "application/json", body: '{"ok":"yes"}' }) : route.continue());
  await page.goto("/admin/appearance");
  await ready(page);
  const initial = await page.locator("#cfg-ts-check").isChecked();
  await page.locator("#cfg-ts-check").click();
  await expect(page.locator("#cfg-ts-msg.err")).toBeVisible();
  expect(await page.locator("#cfg-ts-check").isChecked()).toBe(initial);
});

test("Appearance uploads a real image, preserves a cancelled removal, then restores the default", async ({ page, app }) => {
  await session(page, app);
  await page.goto("/admin/appearance");
  await ready(page);
  const original = await page.request.get("/logo.png");
  expect(original.status()).toBe(200);
  const bytes = await original.body();
  await page.locator("#cfg-mk-file").setInputFiles({ name: "test-logo.png", mimeType: original.headers()["content-type"], buffer: bytes });
  await expect(page.locator("#cfg-mk-quitar")).toBeVisible();
  const uploaded = await page.request.get("/logo.png");
  expect(await uploaded.body()).toEqual(bytes);
  await page.reload();
  await ready(page);
  expect(await page.locator("#cfg-mk-quitar").isVisible()).toBe(true);
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.locator("#cfg-mk-quitar").click();
  expect(await page.locator("#cfg-mk-quitar").isVisible()).toBe(true);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#cfg-mk-quitar").click();
  await expect(page.locator("#cfg-mk-quitar")).toHaveCount(0);
  await page.reload();
  await ready(page);
  await expect(page.locator("#cfg-mk-quitar")).toHaveCount(0);
  const branding = await page.request.get("/api/admin/branding");
  expect((await branding.json()).logo).toBe(false);
});
