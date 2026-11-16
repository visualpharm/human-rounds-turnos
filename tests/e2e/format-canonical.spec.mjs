// Browser acceptance for the physical format.mjs cutover. These journeys
// prove that visible patient/admin values come from the fingerprinted ES
// module and that the deleted compatibility script/global never participate.
//
// Port of tests/e2e/format-canonical.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   requests/account options of the original); there is no requireOk()/skip path. Pages
//   come from the runner: the patient session cookie is added to the test's own context
//   (a fresh one per test) and each viewport is set with page.setViewportSize().
// - Evidence screenshots: capture() still writes to TURNOS_FORMAT_EVIDENCE_DIR only when
//   that variable is set (same file names, full page), now through `saveShot`, which also
//   attaches the picture to the report.
// - The admin login is the shared loginAdmin() helper with next=/admin/solicitudes.
// - Waits are web-first (`toBeVisible`, `toHaveText`, `toHaveAttribute`); the rendered text
//   is compared to the value computed by the real format.mjs in the page. The «no raw ISO
//   shape» and the runtime contract (no global, module requested, no failures) stay
//   point-in-time reads.
// - Locators: `.wz-seg-opt`, `#wz-send`, `.wz-val a[href^='https://wa.me/']`, `#list .sol-row`,
//   `#alert-bell`, `[data-alert-row]` stay CSS - the markup and the wa.me link shape are
//   what the formatter output is verified on.
import path from "node:path";
import {
  test, expect, loginAdmin, saveShot, patientCookie, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_format_canonical",
    requests: true,
    account: true,
  },
});

const EVIDENCE_DIR = process.env.TURNOS_FORMAT_EVIDENCE_DIR || "";

async function capture(testInfo, page, name) {
  if (!EVIDENCE_DIR) return;
  await saveShot(testInfo, page, path.join(EVIDENCE_DIR, name), { fullPage: true });
}

function observe(page) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/login-panel/event" && request.failure()?.errorText === "net::ERR_ABORTED") return;
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

async function assertCanonicalRuntime(page, seen) {
  expect(await page.evaluate(() => window["Sana" + "Format"])).toBeUndefined();
  const manifestUrl = await page.evaluate(() => window.SANA_STATIC?.["platform/format.mjs"] || "");
  expect(manifestUrl).toMatch(/\/static\/platform\/format\.[a-f0-9]+\.mjs$/);
  expect(seen.requested.some((path) => /\/static\/platform\/format\.[a-f0-9]+\.mjs$/.test(path)),
    "the browser must actually request the fingerprinted format module").toBe(true);
  expect(seen.requested.some((path) => path.endsWith("/format.js")),
    "the deleted format.js compatibility script must never be requested").toBe(false);
  expect(seen.failures).toEqual([]);
}

test("patient confirmation phone is visibly formatted by format.mjs", async ({ page, context, app }, testInfo) => {
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
  const seen = observe(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/service/medicina-general-riverside-clinic/book?slots=1", {
    waitUntil: "load", timeout: 15_000,
  });
  await expect(page.locator(".wz-seg-opt").first()).toBeVisible({ timeout: 10_000 });
  await page.locator(".wz-seg-opt").first().click();
  await expect(page.locator("#wz-send")).toBeVisible({ timeout: 10_000 });
  const contactLink = page.locator(".wz-val a[href^='https://wa.me/']").first();
  await expect(contactLink).toBeVisible({ timeout: 10_000 });
  const expected = await page.evaluate(async (phone) => {
    const mod = await import(window.SANA_STATIC["platform/format.mjs"]);
    return { text: mod.telTexto(phone), href: "https://wa.me/" + mod.telWa(phone) };
  }, "+5492254100002");
  await expect(contactLink).toHaveText(expected.text);
  await expect(contactLink).toHaveAttribute("href", expected.href);
  await assertCanonicalRuntime(page, seen);
  await capture(testInfo, page, "patient-confirmation-390.png");
});

test("admin alert renders normalized name and WhatsApp link through format.mjs", async ({ page, app }, testInfo) => {
  const seen = observe(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  const item = {
    id: "format-e2e-alert", triage: "rojo", paciente: "ROSA MELANO",
    dni: "30123456", contacto: "02254 15 40-2237",
    servicio: "Clínica médica", medico: "Dra. Pérez",
    fecha: new Date().toISOString(), request_id: "format-e2e-request",
  };
  await page.route("**/api/admin/intake/alerts/count", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ count: 1 }) }));
  await page.route("**/api/admin/intake/alerts", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [item] }) }));
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/solicitudes");
  await expect(page.locator("#list .sol-row").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#alert-bell")).toBeVisible({ timeout: 10_000 });
  await page.locator("#alert-bell").click();
  const row = page.locator('[data-alert-row="format-e2e-alert"]');
  await expect(row).toBeVisible({ timeout: 10_000 });
  const expected = await page.evaluate(async (value) => {
    const mod = await import(window.SANA_STATIC["platform/format.mjs"]);
    return { name: mod.nombre("ROSA MELANO"), wa: "https://wa.me/" + mod.telWa(value) };
  }, item.contacto);
  await expect(row).toHaveText(new RegExp(expected.name));
  await expect(row.locator('a[href^="https://wa.me/"]')).toHaveAttribute("href", expected.wa);
  await assertCanonicalRuntime(page, seen);
  await capture(testInfo, page, "admin-alert-1280.png");
});
