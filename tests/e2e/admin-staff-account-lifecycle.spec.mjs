// Real-browser acceptance for the native staff Account screen.  The server,
// login gate, template, fingerprinted entry, dependencies, and read APIs are
// real; narrow mutation and TOTP-setup responses are synthetic so this suite
// never changes the shared fixture account.  Synthetic page-transition events
// prove ownership wiring only, not BFCache admission.
//
// Case matrix (written before browser setup):
// C01 native direct screen/profile/retired entries; C02 email acknowledgement;
// C03 password mismatch; C04 rejected password; C05 five-width TOTP setup;
// C06 rejected TOTP confirmation; C07 setup regeneration; C08 rejected entry;
// C09 missing entry; C10 prior-DOM clearance on pagehide/pageshow; C11 duplicate
// passkey delete; C12 evidence-completeness gate; C13/C14 held/failed fonts;
// C15 delayed security-notice geometry.
//
// Port of tests/e2e/admin-staff-account-lifecycle.e2e.mjs (15 tests, same titles, same order).
//
// Differences from the node:test original:
// - One serial describe: every case appends a record (assertions, nonempty screenshots,
//   measured widths) to the shared `records` array and C12, the `afterAll` report and its
//   completeness assertion read all of them. A failure skips the rest; a retry re-runs
//   the file on a fresh worker. As in the original, the completeness assertion lives in
//   the file's after-hook (`afterAll`), after report.json has been written.
// - The evidence directory is reserved in `beforeAll` (not at import time, which would
//   also run in the runner's listing process) under artifactPath("human-rounds",
//   "staff-account-native-lifecycle") - the same <artifacts>/human-rounds tree as before.
//   Screenshots go through saveShot(): same <id>.png files there, plus attached to the report.
// - The page is the runner's `page` (viewport 390x900 through test.use) and the sign-in is
//   the shared loginAdmin() followed by about:blank, so the login landing document (the
//   Account screen itself) is gone before any case installs its fault routes. The layout-shift
//   observer, page-error and console-error collectors are installed before the sign-in, as
//   before. `context.request` is `page.request`; URLs are relative to the app's baseURL.
// - Held routes use holdGate (released by the `cleanup` fixture, so a failing test never
//   leaves a route hanging): C11's delete and C13/C14's fonts. C10 keeps the route object
//   and fulfils it itself, as before.
// - C13/C14: the fixed 300 ms sleep before «no form while the font is held» is now
//   holdsFor() over the same 300 ms (the claim is an absence over time, checked
//   continuously rather than once after a sleep) after waiting for the font request to
//   be seen (`expect.poll`); `document.fonts.status === 'loading'` is an `expect.poll` too.
// - Selector waits are web-first (`toBeVisible`, `toHaveText`, `toBeDisabled`);
//   checks that pin a moment (`count()` of a withdrawn control, the post counters, the values
//   returned by one evaluate, «submit still enabled», «TOTP key changed») stay point-in-time snapshots. The C08/C09 alert is found with
//   `getByRole("alert")`.
// - Locators stay CSS where markup is the subject: the Account form ids/data-* hooks
//   (#acct-*, [data-key-del], [data-retry]), `.acct-*` classes and the geometry probes.
import {
  test, expect, loginAdmin, holdGate, holdsFor, saveShot, artifactPath, ADMIN_USER,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";
import { cumulativeLayoutShift } from "./lib/layout-shift-evidence.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_staff_account_native_lifecycle" },
  viewport: { width: 390, height: 900 },
});
test.describe.configure({ mode: "serial" });

const CASE_IDS = Object.freeze(["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C13", "C14", "C15"]);
const ENTRY = /\/static\/platform\/admin-account\.[a-f0-9]+\.mjs/;
let evidence;
const records = [];

function allSetupWidthsMeasured() {
  const setup = records.find((record) => record.id === "C05");
  const widths = setup?.viewports?.map((viewport) => viewport.width).sort((a, b) => a - b);
  return JSON.stringify(widths) === JSON.stringify([360, 390, 768, 1024, 1280])
    && setup.assets.length === 5
    && new Set(setup.assets.map((asset) => asset.path)).size === 5;
}

function json(data, status = 200) {
  return { status, contentType: "application/json", body: JSON.stringify(data) };
}

function syntheticSetup(serial) {
  return {
    ok: true,
    setup_token: `fixture-${serial}`,
    // Test-only geometry payload, not a credential or a usable enrolled QR.
    qr_svg: '<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="120" height="120" fill="white"/><path d="M12 12h32v32H12zm64 0h32v32H76zM12 76h32v32H12zm48 0h16v16H60zm24 0h24v24H84z" fill="black"/></svg>',
    secret: `TESTMANUALKEY${serial}`,
  };
}

test.beforeAll(() => {
  evidence = allocateEvidenceRunDir({
    root: artifactPath("human-rounds", "staff-account-native-lifecycle"),
    label: "run",
  });
});

test.afterAll(async () => {
  const ids = records.map((record) => record.id);
  const complete = allSetupWidthsMeasured() && ids.length === CASE_IDS.length
    && new Set(ids).size === CASE_IDS.length
    && CASE_IDS.every((id) => ids.includes(id))
    && records.every((record) => record.assertionComplete === true
      && Array.isArray(record.assets) && record.assets.length > 0
      && record.assets.every((asset) => Number.isInteger(asset.bytes) && asset.bytes > 0));
  await writeFile(join(evidence, "report.json"), JSON.stringify({
    expectedCaseIDs: CASE_IDS,
    recordedCaseIDs: ids,
    uniqueCaseIDs: new Set(ids).size,
    complete,
    records,
    missingCoverage: [
      "Actual BFCache admission: synthetic pagehide/pageshow events only test template ownership.",
      "QR decodability: the intercepted synthetic SVG proves geometry and visible guidance, not authenticator enrollment.",
    ],
    boundary: "Local ASGI server, throwaway synthetic-pack database, and narrow intercepted account mutations/TOTP setup tokens. No production endpoint or account mutation.",
  }, null, 2));
  expect(complete, "all case IDs need assertions and nonempty durable screenshots").toBe(true);
});

async function session(page, app) {
  await page.addInitScript(() => {
    window.__staffAccountShifts = [];
    window.__staffAccountShiftRecord = (entry) => ({ value: entry.value, startTime: entry.startTime,
      fonts: document.fonts.status,
      sources: (entry.sources || []).map((source) => ({
        tag: source.node?.tagName, id: source.node?.id, className: String(source.node?.className || ""),
        previousRect: source.previousRect.toJSON(), currentRect: source.currentRect.toJSON(),
      })) });
    window.__staffAccountObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput) {
        window.__staffAccountShifts.push(window.__staffAccountShiftRecord(entry));
      }
    });
    window.__staffAccountObserver.observe({ type: "layout-shift", buffered: true });
  });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await loginAdmin(page, app.baseUrl);
  // End the login landing document (the Account screen) before a case installs its fault routes.
  await page.goto("about:blank");
  return { pageErrors, consoleErrors };
}

// Cuenta is a view (2026-10-27): each value carries its «Editar»/«Cambiar»
// and the form opens in place; no field is open on arrival.
async function accountReady(page) {
  await expect(page.locator("#sec-cuenta #acct-email-edit")).toBeVisible({ timeout: 12000 });
  await expect(page.locator("#sec-cuenta #acct-password-edit")).toBeVisible({ timeout: 12000 });
  await expect(page.locator("#sec-cuenta #acct-email-form")).toBeHidden();
  await expect(page.locator("#sec-cuenta #acct-password-form")).toBeHidden();
}

async function openEdit(page, kind) {
  await page.locator(`#acct-${kind}-edit`).click();
  await expect(page.locator(`#acct-${kind}-form`)).toBeVisible();
}

async function settle(page) {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function screenshot(testInfo, page, id) {
  // Full-page captures otherwise strand fixed chrome at the last focused field.
  await page.evaluate(() => scrollTo(0, 0));
  await settle(page);
  const { path, bytes } = await saveShot(testInfo, page, join(evidence, `${id}.png`), { fullPage: true });
  expect(bytes, `${id}: screenshot must not be empty`).toBeGreaterThan(0);
  return { id, path, bytes };
}

async function captureViewport(page, width) {
  return page.evaluate((width) => {
    const host = document.getElementById("sec-cuenta");
    const vw = document.documentElement.clientWidth;
    const visible = (element) => {
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden" && element.getBoundingClientRect().width > 0;
    };
    const escaped = [...host.querySelectorAll("*")].flatMap((element) => {
      if (!visible(element)) return [];
      const box = element.getBoundingClientRect();
      return box.left < -1 || box.right > vw + 1
        ? [{ tag: element.tagName, className: String(element.className), left: Math.round(box.left), right: Math.round(box.right) }]
        : [];
    });
    const qr = host.querySelector(".acct-qr");
    const qrBox = qr && qr.getBoundingClientRect();
    const setup = host.querySelector("#acct-2fa-setup");
    const shifts = [...window.__staffAccountShifts, ...window.__staffAccountObserver.takeRecords()
      .filter((entry) => !entry.hadRecentInput).map(window.__staffAccountShiftRecord)];
    return {
      width,
      lang: document.documentElement.lang,
      pageOverflow: document.documentElement.scrollWidth - vw,
      escaped,
      shifts,
      qr: qrBox && { left: qrBox.left, right: qrBox.right, width: qrBox.width, height: qrBox.height },
      noviceText: setup ? setup.innerText : "",
      manualKey: host.querySelector(".acct-secret code")?.textContent || "",
      setupUrl: location.pathname + location.search,
    };
  }, width);
}

async function directAccount(page) {
  await page.goto("/admin/account", { waitUntil: "load", timeout: 15000 });
  await accountReady(page);
  await settle(page);
}

test("C01 native server screen renders the real profile and only its fingerprinted entry", async ({ page, app }, testInfo) => {
  const { pageErrors, consoleErrors } = await session(page, app);
  const record = { id: "C01", assets: [], assertionComplete: false };
  records.push(record);
  const template = await page.request.get("/admin/account");
  expect(template.status()).toBe(200);
  const html = await template.text();
  expect(html, "real template names the native fingerprinted entry").toMatch(ENTRY);
  expect(html, "real template does not fall back to classic Account").not.toMatch(/admin-account\.js/);
  for (const path of ["/admin-account.js", "/static/admin-account.js"]) {
    const response = await page.request.get(path, { maxRedirects: 0 });
    expect(response.status(), `${path} stays retired`).toBe(404);
  }
  await directAccount(page);
  record.assets.push(await screenshot(testInfo, page, "C01-native-profile"));
  const rendered = await page.evaluate(() => ({
    url: location.pathname + location.search,
    // The login name sits beside the password now (2026-10-13), not in the header.
    username: document.querySelector("#sec-cuenta #acct-username")?.value,
    entry: performance.getEntriesByType("resource").map((entry) => entry.name).find((name) => /\/static\/platform\/admin-account\.[a-f0-9]+\.mjs/.test(name)) || null,
    classic: [...document.scripts].some((script) => /admin-account\.js(?:$|[?])/.test(script.src)),
  }));
  expect(rendered.url).toBe("/admin/account");
  expect(rendered.username).toBe(ADMIN_USER);
  expect(rendered.entry, "the browser loaded the native entry").toBeTruthy();
  expect(rendered.classic).toBe(false);
  // At 390 «Cerrar sesión» is an item of the «Menú» (one screen header, 2026-11-08).
  await page.locator("details.adm5-menu > summary").click();
  await expect(page.locator("#adm-nav #adm-logout")).toBeVisible();
  const logout = await page.locator("#adm-logout").evaluate((button) => ({
    label: button.textContent.trim(), expected: button.getAttribute("aria-label"),
    width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height,
    overflow: button.scrollWidth > button.clientWidth,
  }));
  expect(logout.label, "visible localized sign-out label does not depend on an icon font").toBe(logout.expected);
  expect(logout.width >= 44 && logout.height >= 44 && !logout.overflow).toBe(true);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
  record.assertionComplete = true;
});

test("C02 Account email mutation sends the typed profile through the real controller", async ({ page, app }, testInfo) => {
  await session(page, app);
  const record = { id: "C02", assets: [], assertionComplete: false, posts: [] };
  records.push(record);
  await page.route("**/api/admin/account/profile", (route) => {
    record.posts.push(route.request().postDataJSON());
    return route.fulfill(json({ ok: true }));
  });
  await directAccount(page);
  await openEdit(page, "email");
  await page.fill("#acct-email", "staff.lifecycle@example.invalid");
  await page.locator("#acct-email-form button[type=submit]").click();
  await expect(page.locator("#acct-email-status")).toBeVisible();
  record.assets.push(await screenshot(testInfo, page, "C02-email-save"));
  expect(record.posts).toEqual([{ email: "staff.lifecycle@example.invalid" }]);
  await expect(page.locator("#acct-email-status")).toHaveText(/Email guardado\./);
  record.assertionComplete = true;
});

test("C03 Account rejects mismatched new passwords before any mutation request", async ({ page, app }, testInfo) => {
  await session(page, app);
  const record = { id: "C03", assets: [], assertionComplete: false, passwordPosts: 0 };
  records.push(record);
  await page.route("**/api/admin/account/password", (route) => { record.passwordPosts += 1; return route.fulfill(json({ ok: true })); });
  await directAccount(page);
  await openEdit(page, "password");
  await page.fill("#acct-pass-current", "test-current");
  await page.fill("#acct-pass-new", "first-value");
  await page.fill("#acct-pass-confirm", "other-value");
  await page.locator("#acct-password-form button[type=submit]").click();
  await expect(page.locator("#acct-pass-status")).toBeVisible();
  record.assets.push(await screenshot(testInfo, page, "C03-password-mismatch"));
  await expect(page.locator("#acct-pass-status")).toHaveText(/no coinciden/i);
  expect(record.passwordPosts).toBe(0);
  record.assertionComplete = true;
});

test("C04 Account keeps a rejected password mutation local and actionable", async ({ page, app }, testInfo) => {
  await session(page, app);
  const record = { id: "C04", assets: [], assertionComplete: false };
  records.push(record);
  await page.route("**/api/admin/account/password", (route) => route.fulfill(json({ ok: false, error: "La contraseña actual no coincide." }, 400)));
  await directAccount(page);
  await openEdit(page, "password");
  await page.fill("#acct-pass-current", "incorrect-current");
  await page.fill("#acct-pass-new", "same-next");
  await page.fill("#acct-pass-confirm", "same-next");
  await page.locator("#acct-password-form button[type=submit]").click();
  await expect(page.locator("#acct-pass-status")).toBeVisible();
  record.assets.push(await screenshot(testInfo, page, "C04-password-rejected"));
  await expect(page.locator("#acct-pass-status")).toHaveText(/actual no coincide/i);
  expect(await page.locator("#acct-password-form button[type=submit]").isEnabled()).toBe(true);
  record.assertionComplete = true;
});

test("C05 Spanish TOTP setup keeps QR, novice instructions, manual key, and page geometry usable at five widths", async ({ page, app }, testInfo) => {
  const { pageErrors, consoleErrors } = await session(page, app);
  const record = { id: "C05", assets: [], assertionComplete: false, viewports: [] };
  records.push(record);
  let starts = 0;
  await page.route("**/api/admin/account/2fa/start", (route) => route.fulfill(json(syntheticSetup(++starts))));
  for (const width of [360, 390, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await directAccount(page);
    await page.locator("#acct-2fa-start").click();
    await expect(page.locator("#acct-confirm-form")).toBeVisible({ timeout: 8000 });
    await page.locator(".acct-ayuda summary").click();
    await settle(page);
    const viewport = await captureViewport(page, width);
    viewport.cls = cumulativeLayoutShift(viewport.shifts);
    record.viewports.push(viewport);
    record.assets.push(await screenshot(testInfo, page, `C05-totp-${width}`));
    expect(viewport.lang, `${width}: Spanish account setup`).toBe("es");
    expect(viewport.setupUrl).toBe("/admin/account?setup=2fa");
    expect(viewport.pageOverflow, `${width}: zero page overflow`).toBe(0);
    expect(viewport.escaped, `${width}: no setup element escapes the viewport`).toEqual([]);
    expect(viewport.qr && viewport.qr.width > 0 && viewport.qr.height > 0, `${width}: QR is visible`).toBeTruthy();
    expect(viewport.qr.left >= -1 && viewport.qr.right <= width + 1, `${width}: QR fully fits`).toBe(true);
    // The app fallback links only Google Authenticator's official stores.
    expect(viewport.noviceText).toMatch(/Instalá Google Authenticator en tu teléfono/);
    expect(viewport.noviceText).toMatch(/Ingresá abajo el código de 6 dígitos/);
    expect(viewport.noviceText).toMatch(/No puedo escanear/);
    expect(viewport.manualKey).toBe(`TESTMANUALKEY${starts}`);
    expect(Number.isFinite(viewport.cls) && viewport.cls <= 0.1, `${width}: observed CLS stays within threshold`).toBe(true);
  }
  expect(starts).toBe(5);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
  record.assertionComplete = true;
});

test("C06 Account shows a rejected TOTP code without accepting the synthetic token", async ({ page, app }, testInfo) => {
  await session(page, app);
  const record = { id: "C06", assets: [], assertionComplete: false, confirmPosts: [] };
  records.push(record);
  await page.route("**/api/admin/account/2fa/start", (route) => route.fulfill(json(syntheticSetup(6))));
  await page.route("**/api/admin/account/2fa/confirm", (route) => {
    record.confirmPosts.push(route.request().postDataJSON());
    return route.fulfill(json({ ok: false, error: "Código inválido." }, 400));
  });
  await directAccount(page);
  await page.locator("#acct-2fa-start").click();
  await page.fill("#acct-confirm-code", "123456");
  await page.locator("#acct-confirm-form button[type=submit]").click();
  await expect(page.locator("#acct-confirm-status")).toBeVisible();
  record.assets.push(await screenshot(testInfo, page, "C06-totp-rejected"));
  expect(record.confirmPosts).toEqual([{ setup_token: "fixture-6", codigo: "123456" }]);
  await expect(page.locator("#acct-confirm-status")).toHaveText(/Código inválido/);
  expect(await page.locator("#acct-confirm-form").count()).toBe(1);
  record.assertionComplete = true;
});

test("C07 reloading canonical setup regenerates the synthetic TOTP setup instead of reusing stale setup state", async ({ page, app }, testInfo) => {
  await session(page, app);
  const record = { id: "C07", assets: [], assertionComplete: false, setupTokens: [] };
  records.push(record);
  let serial = 20;
  await page.route("**/api/admin/account/2fa/start", (route) => {
    const payload = syntheticSetup(serial++);
    record.setupTokens.push(payload.setup_token);
    return route.fulfill(json(payload));
  });
  await directAccount(page);
  await page.locator("#acct-2fa-start").click();
  await expect(page.locator("#acct-confirm-form")).toBeVisible();
  await page.locator(".acct-ayuda summary").click();
  const firstKey = await page.locator(".acct-secret code").innerText();
  await page.reload({ waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#acct-confirm-form")).toBeVisible({ timeout: 8000 });
  await page.locator(".acct-ayuda summary").click();
  record.assets.push(await screenshot(testInfo, page, "C07-totp-regenerated"));
  expect(page.url()).toBe(app.baseUrl + "/admin/account?setup=2fa");
  expect(record.setupTokens).toEqual(["fixture-20", "fixture-21"]);
  expect(await page.locator(".acct-secret code").innerText()).not.toBe(firstKey);
  record.assertionComplete = true;
});

async function entryRecoveryCase({ page, app }, testInfo, id, mode) {
  await session(page, app);
  const record = { id, assets: [], assertionComplete: false, mode };
  records.push(record);
  await page.route("**/static/platform/admin-account.*.mjs", (route) => mode === "rejected" ? route.abort("failed") : route.fulfill({ status: 404, body: "missing native entry" }));
  await page.goto("/admin/account", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#sec-cuenta [data-retry]")).toBeVisible({ timeout: 12000 });
  record.assets.push(await screenshot(testInfo, page, `${id}-${mode}-entry`));
  await expect(page.locator("#sec-cuenta").getByRole("alert")).toHaveText(/No se pudo cargar esta sección\.|Couldn't load this section\./);
  expect(await page.locator("#sec-cuenta #acct-email-form").count()).toBe(0);
  await page.unroute("**/static/platform/admin-account.*.mjs");
  await page.locator("#sec-cuenta [data-retry]").click();
  await accountReady(page);
  expect(await page.locator("#sec-cuenta [data-retry]").count()).toBe(0);
  record.assets.push(await screenshot(testInfo, page, `${id}-${mode}-recovered`));
  record.assertionComplete = true;
}

test("C08 rejected native Account entry exposes template-owned recovery", async ({ page, app }, testInfo) => entryRecoveryCase({ page, app }, testInfo, "C08", "rejected"));
test("C09 missing native Account entry exposes template-owned recovery", async ({ page, app }, testInfo) => entryRecoveryCase({ page, app }, testInfo, "C09", "missing"));

test("C10 pagehide clears prior profile and persisted pageshow awaits its fresh Account read", async ({ page, app }, testInfo) => {
  await session(page, app);
  const record = { id: "C10", assets: [], assertionComplete: false, accountReads: 0, staleVisibleBeforeFreshRead: null };
  records.push(record);
  let heldRoute;
  let heldSeen;
  const freshReadStarted = new Promise((resolve) => { heldSeen = resolve; });
  // First render puts both profile data and a setup key in the real
  // controller DOM.  Pagehide must retire that sensitive state before the
  // persisted restore can await its own account read.
  await page.route("**/api/admin/account/2fa/start", (route) => route.fulfill(json(syntheticSetup(10))));
  await directAccount(page);
  await page.locator("#acct-2fa-start").click();
  await expect(page.locator("#acct-confirm-form")).toBeVisible();
  await page.locator(".acct-ayuda summary").click();
  await expect(page.locator(".acct-secret code")).toHaveText("TESTMANUALKEY10");
  await page.route("**/api/admin/account", async (route) => {
    record.accountReads += 1;
    heldRoute = route;
    heldSeen();
  });
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await freshReadStarted;
  record.staleVisibleBeforeFreshRead = await page.evaluate(() => ({
    profile: !!document.querySelector("#sec-cuenta #acct-email-form"),
    setup: !!document.querySelector("#sec-cuenta #acct-confirm-form"),
    manualKey: document.querySelector("#sec-cuenta .acct-secret code")?.textContent || "",
  }));
  record.assets.push(await screenshot(testInfo, page, "C10-before-fresh-account-read"));
  expect(record.staleVisibleBeforeFreshRead,
    "persisted restore must not expose a prior profile or TOTP setup while its fresh account read is pending").toEqual({ profile: false, setup: false, manualKey: "" });
  await heldRoute.fulfill(json({ usuario: ADMIN_USER, rol: "admin", email: "", tiene_2fa: false }));
  await accountReady(page);
  record.assets.push(await screenshot(testInfo, page, "C10-pagehide-pageshow-fresh-owner"));
  expect(record.accountReads, "the persisted remount owns exactly one fresh account read").toBe(1);
  await expect(page.locator("#sec-cuenta #acct-username")).toHaveValue(ADMIN_USER);
  record.assertionComplete = true;
});

test("C11 a pending passkey delete rejects a duplicate activation from the same rendered row", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const record = { id: "C11", assets: [], assertionComplete: false, deletes: 0 };
  records.push(record);
  // `held` keeps the delete pending; `arrived` fires once the controller has sent it.
  const del = holdGate(cleanup);
  await page.route("**/api/admin/passkeys", (route) => route.fulfill(json({ passkeys: [{ credential_id: "test-key-1", nombre: "Llave de prueba", creado: "2026-01-01T00:00:00Z", ultimo_uso: null }] })));
  await page.route("**/api/admin/passkeys/delete", async (route) => {
    record.deletes += 1;
    del.arrive();
    await del.held;
    await route.fulfill(json({ ok: true }));
  });
  await directAccount(page);
  await expect(page.locator('[data-key-del="test-key-1"]')).toBeVisible();
  await page.evaluate(() => {
    const button = document.querySelector('[data-key-del="test-key-1"]');
    button.click();
  });
  await del.arrived;
  await expect(page.locator('[data-key-del="test-key-1"]')).toBeDisabled();
  await page.evaluate(() => document.querySelector('[data-key-del="test-key-1"]')
    .dispatchEvent(new MouseEvent("click", { bubbles: true })));
  await settle(page);
  expect(record.deletes, "controller flight ownership rejects duplicate delete while pending").toBe(1);
  del.release();
  // Without 2FA the security step owns the passkey status line; with 2FA
  // the standalone Passkeys section does (admin-account passkeyStatusEl).
  const keyStatus = page.locator("#acct-sec-pk-status, #acct-keys-status").first();
  await expect(keyStatus).toBeVisible();
  record.assets.push(await screenshot(testInfo, page, "C11-passkey-duplicate-delete"));
  await expect(keyStatus).toHaveText(/Passkey quitada/);
  expect(record.deletes).toBe(1);
  record.assertionComplete = true;
});

for (const [id, failFonts] of [["C13", false], ["C14", true]]) {
  test(`${id} Account defers first paint through real held fonts and survives font failure`, async ({ page, app, cleanup }, testInfo) => {
    await session(page, app);
    const record = { id, assets: [], assertionComplete: false };
    records.push(record);
    const fonts = holdGate(cleanup);
    let requests = 0;
    // Self-hosted theme typefaces (core/theme.py, 2026-09-17): hold the
    // real first-party .woff2 requests.
    await page.route(/\/static\/fonts\/[^/?]+\.woff2(\?|$)/, async (route) => {
      requests++; await fonts.held;
      return failFonts ? route.abort() : route.continue();
    });
    const entry = page.waitForResponse((response) => ENTRY.test(response.url()));
    await page.goto("/admin/account", { waitUntil: "domcontentloaded" });
    await entry;
    await expect.poll(() => page.evaluate(() => document.fonts.status)).toBe("loading");
    // A deliberately held cold font must not expose a fallback-font form.
    await expect.poll(() => requests, { message: "actual font requests must be held" }).toBeGreaterThan(0);
    await holdsFor(async () => {
      expect(await page.locator("#acct-email-form").count(), "no form before first-font settlement").toBe(0);
    }, { during: 300 });
    fonts.release();
    await accountReady(page); await settle(page);
    record.fontSettlement = await page.evaluate(() => ({ status: document.fonts.status,
      shifts: [...window.__staffAccountShifts, ...window.__staffAccountObserver.takeRecords()
        .filter((entry) => !entry.hadRecentInput).map((entry) => ({ value: entry.value, startTime: entry.startTime }))] }));
    expect(record.fontSettlement.status).toBe("loaded");
    expect(cumulativeLayoutShift(record.fontSettlement.shifts)).toBeLessThanOrEqual(0.1);
    record.assets.push(await screenshot(testInfo, page, id + "-font-settlement"));
    record.assertionComplete = true;
  });
}

test("C15 delayed Account security notice does not move the sign-out control sideways", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const record = { id: "C15", assets: [], assertionComplete: false };
  records.push(record);
  const read = holdGate(cleanup);
  await page.setViewportSize({ width: 360, height: 900 });
  await page.route("**/api/admin/account", async (route) => {
    const response = await route.fetch(); read.arrive(); await read.held;
    return route.fulfill({ response });
  });
  await page.goto("/admin/account"); await read.arrived; await settle(page);
  // At 360 «Cerrar sesión» is an item of the «Menú» (one screen header, 2026-11-08).
  await page.locator("details.adm5-menu > summary").click();
  const before = await stableBox(page.locator("#adm-logout"), "sign-out before the notice");
  read.release(); await accountReady(page); await settle(page);
  const after = await stableBox(page.locator("#adm-logout"), "sign-out after the notice");
  record.geometry = { before, after };
  record.assets.push(await screenshot(testInfo, page, "C15-security-notice"));
  expect(before && after, "sign-out remains a real visible control").toBeTruthy();
  expect(Math.abs(before.x - after.x), "security read must not throw sign-out across the header").toBeLessThanOrEqual(1);
  record.assertionComplete = true;
});

test("C12 durable evidence is already nonempty and unique before the final report writes", async ({ page, app }, testInfo) => {
  expect(allSetupWidthsMeasured(), "exactly five distinct measured setup widths and screenshots").toBe(true);
  await session(page, app);
  const record = { id: "C12", assets: [], assertionComplete: false };
  records.push(record);
  await directAccount(page);
  record.assets.push(await screenshot(testInfo, page, "C12-evidence-gate"));
  const prior = records.filter((item) => item.id !== "C12");
  expect(prior.length).toBe(CASE_IDS.length - 1);
  expect(new Set(prior.map((item) => item.id)).size).toBe(CASE_IDS.length - 1);
  expect(prior.every((item) => item.assertionComplete && item.assets.length > 0)).toBe(true);
  record.assertionComplete = true;
});
