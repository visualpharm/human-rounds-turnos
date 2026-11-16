// tests/e2e/admin-shell.spec.mjs -- the shared shell facilities (2026-08-31
// slice) against a REAL browser on a REAL running server: the red-triage
// bell and the transient toast on BOTH admin surfaces (legacy /admin hash
// shell and the server-routed /admin/<screen> renders), with the toast's
// screen-reader announcement semantics, and the title band fitting at
// phone/laptop widths with the bell in it.
//
// The alert APIs are route-fulfilled with fictional data (2 unseen
// red-triage interviews, one ack) so the FRONTEND contract is proven
// deterministically in the browser; the APIs themselves, the render-time
// role wiring and the scripts' behavior are proven by tests/test_admin_shared_shell.py
// and tests/admin-shell-shared.test.mjs.
//
// Port of tests/e2e/admin-shell.e2e.mjs (5 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `requests: true`); the runner's page replaces `browser.newPage()` (default 1280x720) and
//   the sign-in is the shared loginAdmin() landing on /admin/solicitudes, followed by the same
//   «routed AND booted» wait the original had (SANA_ADMIN_SCREEN + #sec-solicitudes).
// - The file is `serial`: the two responsive tests append to a measurement list that the
//   afterAll hook writes to `measurements.json` with a completeness flag (10 measurements,
//   2 scenarios), exactly as the node:test `after` hook did, so a failure skips the rest and a
//   retry re-runs the whole file. Screenshots (normal-<w>.png / recovery-<w>.png) go through
//   saveShot() into the same artifacts directory and are attached to the report.
// - The 120 ms sleep «let the layout settle at the new width» of the responsive test is a wait
//   until the header measurements are identical in three consecutive reads 50 ms apart
//   (5 s cap, then it measures anyway). The recovery test never slept and still measures
//   straight after the resize.
// - Waits are web-first with the original timeouts (waitFor -> toBeVisible / toBeAttached /
//   toHaveCount, waitForFunction -> toHaveClass / expect.poll). Attribute, text and count reads
//   that only follow a wait are retrying expects; the «no #toast node before first use», «no
//   reload control once the bell is back», «ack still enabled after the malformed answer» and
//   the layout/measurement reads stay point-in-time.
// - Locators left as CSS: ids / data attributes of the shell (#alert-bell, #alert-bell-panel,
//   .alert-row[data-alert-row], [data-ack], #toast, [data-alerts-reload], #adm-*) - the markup
//   contract and the geometry under test.
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { test, expect, loginAdmin, saveShot, artifactPath, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_shell", requests: true } });
test.describe.configure({ mode: "serial" });

const artifactDir = artifactPath("human-rounds", "alerts-lifecycle-audit", "browser-" + new Date().toISOString().replaceAll(":", "-"));
const measurements = [];
const completedMeasurements = new Set();

function measureHeaderText() {
  const h1 = document.getElementById("adm-h1");
  const brokenWords = [];
  const walker = document.createTreeWalker(h1, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    for (const match of node.textContent.matchAll(/\S+/g)) {
      const range = document.createRange();
      range.setStart(node, match.index);
      range.setEnd(node, match.index + match[0].length);
      const tops = new Set([...range.getClientRects()].map((r) => Math.round(r.top)));
      if (tops.size > 1) brokenWords.push(match[0]);
    }
  }
  const action = document.getElementById("adm-crear-turno");
  const range = document.createRange();
  range.selectNodeContents(action);
  const t = h1.getBoundingClientRect();
  const a = action.getBoundingClientRect();
  return {
    brokenWords,
    titleOverflow: h1.scrollWidth - h1.clientWidth,
    counter: !!document.getElementById("adm-h1-count"),
    actionOnTitleLine: a.top < t.bottom && a.bottom > t.top,
    actionLines: new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size,
  };
}

function assertReadableHeader(layout, width) {
  expect(layout.brokenWords, `heading words must not split at ${width}px`).toEqual([]);
  expect(layout.titleOverflow, `heading is clipped at ${width}px`).toBeLessThanOrEqual(1);
  // One screen header (the product owner, 2026-11-08): no counter, «Crear turno» on the title's line.
  expect(layout.counter, `no pending counter beside the title at ${width}px`).toBe(false);
  expect(layout.actionOnTitleLine, `create action shares the title's line at ${width}px: ${JSON.stringify(layout)}`).toBe(true);
  expect(layout.actionLines, `create action must keep its readable label at ${width}px`).toBe(1);
}

// Wait until the header measurements stop changing (three identical reads in a row).
async function settleLayout(page) {
  let last = "", same = 0;
  await expect.poll(async () => {
    const now = JSON.stringify(await page.evaluate(measureHeaderText));
    same = now === last ? same + 1 : 0;
    last = now;
    return same >= 2;
  }, { timeout: 5000, intervals: [50] }).toBe(true).catch(() => {});
}

test.beforeAll(async () => {
  await mkdir(artifactDir, { recursive: true });
});

test.afterAll(async () => {
  await mkdir(artifactDir, { recursive: true });
  await writeFile(join(artifactDir, "measurements.json"), JSON.stringify({ complete: measurements.length === 10 && completedMeasurements.size === 2, completedScenarios: [...completedMeasurements], measurements }, null, 2));
});

// Each shell journey owns its start screen.  A prior successful page can
// legitimately have been saved for this account, so use the server-owned
// explicit continuation rather than assuming login always picks the role
// default.  The wait proves the final direct document is both routed and
// booted; URL arrival alone was flaky under the shared E2E server.
async function signIn(page, app) {
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/solicitudes");
  await expect.poll(
    () => page.evaluate(() => window.SANA_ADMIN_SCREEN === "solicitudes" && !!document.getElementById("sec-solicitudes")),
    { timeout: 10000 },
  ).toBe(true);
}

// Two unseen red-triage interviews + a working ack, fictional end to end.
const ALERT_ITEM = {
  id: "e2e-rojo-1", triage: "rojo", paciente: "Rosa Melano", dni: "30123456",
  contacto: "+5492254402237", servicio: "Clínica médica", medico: "Dra. Pérez",
  fecha: new Date().toISOString(), request_id: "e2e-req-1",
};

async function fulfillAlertApis(page) {
  await page.route("**/api/admin/intake/alerts/count", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ count: 2 }) }));
  await page.route("**/api/admin/intake/alerts", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [ALERT_ITEM] }) }));
  await page.route("**/api/admin/intake/alerts/*/ack", (route) => {
    if (route.request().method() !== "POST") return route.continue();
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
}

test("direct screen: the bell mounts with the server-verified role, opens a dialog, acks and announces through the shared toast", async ({ page, app }) => {
  await fulfillAlertApis(page);
  await signIn(page, app);
  await page.goto("/admin/solicitudes", { waitUntil: "load", timeout: 15000 });

  // The bell appears in the shared title band, driven by the role the
  // server rendered into the page (sanaInitAlerts("admin") from
  // admin_auth.active_role(), not a whoami round-trip).
  const bell = page.locator("#alert-bell");
  await expect(bell).toBeVisible({ timeout: 10000 });
  await expect(bell).toHaveAttribute("aria-haspopup", "dialog");
  await expect(page.locator("#alert-bell-badge")).toHaveText("2");
  await expect(bell).toHaveAttribute("aria-label", /2/);
  // The band the bell lives in is the one every direct screen renders.
  await expect(page.locator(".adm-topbar .adm-h1row #alert-bell")).not.toHaveCount(0);

  // The toast node does not exist yet: it is created on first use, and
  // nothing about the panel boot creates one.
  expect(await page.locator("#toast").count()).toBe(0);

  // Opening the bell: a real dialog with the full list, fetched on open.
  await bell.click();
  const panel = page.locator("#alert-bell-panel");
  await expect(panel).toBeVisible({ timeout: 8000 });
  await expect(panel).toHaveAttribute("role", "dialog");
  await expect(bell).toHaveAttribute("aria-expanded", "true");
  const row = page.locator('.alert-row[data-alert-row="e2e-rojo-1"]');
  await expect(row).toBeVisible({ timeout: 8000 });
  // Direct-screen link shape: the consultation screen's canonical query.
  await expect(row.locator('a[href="/admin/consult?interview=e2e-rojo-1"]'))
    .toHaveAttribute("href", "/admin/consult?interview=e2e-rojo-1");

  // Ack: POSTs, drops the row, and confirms through the ONE shared toast.
  await row.locator("[data-ack]").click();
  await expect(row).toHaveCount(0, { timeout: 8000 });
  const toast = page.locator("#toast");
  await expect(toast).toBeAttached({ timeout: 8000 });
  // The announcement contract: a blind operator (reunión 2026-07-15) is
  // TOLD the action succeeded, not shown a color.
  await expect(toast).toHaveAttribute("role", "status");
  await expect(toast).toHaveAttribute("aria-live", "polite");
  await expect(toast).toHaveAttribute("aria-atomic", "true");
  await expect(toast).toHaveText(/vist/i);
  // Transient: the node persists but goes quiet after ~2,6 s.
  await expect(toast).not.toHaveClass(/(^|\s)on(\s|$)/, { timeout: 8000 });
  expect(await page.locator("#toast").count()).toBe(1);
});

test("another direct screen: same bell, same toast node, direct consultation link", async ({ page, app }) => {
  await fulfillAlertApis(page);
  await signIn(page, app);
  await page.goto("/admin/cola", { waitUntil: "load", timeout: 15000 });
  const bell = page.locator("#alert-bell");
  await expect(bell).toBeVisible({ timeout: 10000 });
  await bell.click();
  const row = page.locator('.alert-row[data-alert-row="e2e-rojo-1"]');
  await expect(row).toBeVisible({ timeout: 8000 });
  await expect(row.locator('a[href="/admin/consult?interview=e2e-rojo-1"]'))
    .toHaveAttribute("href", "/admin/consult?interview=e2e-rojo-1");
  await row.locator("[data-ack]").click();
  await expect.poll(
    () => page.evaluate(() => {
      var t = document.getElementById("toast");
      return !!t && t.getAttribute("role") === "status" && /vist/i.test(t.textContent || "");
    }),
    { timeout: 8000 },
  ).toBe(true);
});

test("responsive: the title band (title + screen control + bell) fits at 360/390/768/1024/1280 on a direct screen", async ({ page, app }, testInfo) => {
  await fulfillAlertApis(page);
  await signIn(page, app);
  await page.goto("/admin/solicitudes", { waitUntil: "load", timeout: 15000 });
  await expect(page.locator("#alert-bell")).toBeVisible({ timeout: 10000 });
  // The seeded requests hydrate the queue the band sits on top of.
  await expect(page.locator("#list .sol-row")).not.toHaveCount(0, { timeout: 10000 });
  for (const width of [360, 390, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    // Let the layout settle at the new width before measuring.
    await settleLayout(page);
    const layout = await page.evaluate(() => {
      const bell = document.getElementById("alert-bell");
      const h1 = document.getElementById("adm-h1");
      const vw = document.documentElement.clientWidth;
      const inside = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.left >= -0.5 && r.right <= vw + 0.5;
      };
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        bellInside: inside(bell),
        h1Inside: inside(h1),
        h1Text: h1 ? h1.textContent.trim() : "",
      };
    });
    expect(
      layout.overflow,
      `/admin/solicitudes: ${layout.overflow}px horizontal overflow at ${width}px`,
    ).toBeLessThanOrEqual(1);
    expect(layout.bellInside, `bell must fit inside the viewport at ${width}px`).toBeTruthy();
    expect(layout.h1Inside, `title must fit inside the viewport at ${width}px`).toBeTruthy();
    expect(layout.h1Text).toMatch(/solicitudes/i);
    Object.assign(layout, await page.evaluate(measureHeaderText));
    measurements.push({ scenario: "normal", width, ...layout });
    await saveShot(testInfo, page, join(artifactDir, `normal-${width}.png`), { fullPage: true });
    assertReadableHeader(layout, width);
  }
  completedMeasurements.add("normal");
});

test("failed native import exposes usable recovery at all five widths and reload restores the real widget", async ({ page, app }, testInfo) => {
  await fulfillAlertApis(page);
  await signIn(page, app);
  const pattern = "**/static/platform/admin-alerts.*.mjs";
  await page.route(pattern, (route) => route.abort("failed"));
  await page.goto("/admin/solicitudes", { waitUntil: "load" });
  const reload = page.locator(".adm-h1row [data-alerts-reload]");
  await expect(reload).toBeVisible();
  expect(await page.locator("#alert-bell").count()).toBe(0);
  // The optional bell's failure must not disable the actual appointments
  // screen. Wait for real hydration, not an empty first-paint screenshot.
  await expect(page.locator("#list .sol-row")).not.toHaveCount(0);
  await expect(page.locator("#adm-crear-turno")).toBeVisible();
  for (const width of [360, 390, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    const layout = await page.evaluate(() => {
      const r = document.querySelector(".adm-h1row [role=alert]").getBoundingClientRect();
      const width = document.documentElement.clientWidth;
      return { overflow: document.documentElement.scrollWidth - width, left: r.left, right: r.right, recoveryText: document.querySelector(".adm-h1row [role=alert]").textContent.trim() };
    });
    Object.assign(layout, await page.evaluate(measureHeaderText));
    measurements.push({ scenario: "recovery", width, ...layout });
    await saveShot(testInfo, page, join(artifactDir, `recovery-${width}.png`), { fullPage: true });
    assertReadableHeader(layout, width);
    expect(layout.overflow <= 1 && layout.left >= -0.5 && layout.right <= width + 0.5, JSON.stringify(layout)).toBe(true);
    expect(layout.recoveryText).toMatch(/No se pudo cargar\.\s+Recargar/);
    expect(layout.recoveryText).not.toMatch(/admin\.system/);
  }
  await page.unroute(pattern);
  await reload.click();
  await expect(page.locator("#alert-bell")).toBeVisible();
  expect(await page.locator(".adm-h1row [data-alerts-reload]").count()).toBe(0);
  completedMeasurements.add("recovery");
});

test("malformed success acknowledgement leaves the alert actionable until a real acknowledgement", async ({ page, app }) => {
  await fulfillAlertApis(page);
  let valid = false;
  await page.route("**/api/admin/intake/alerts/*/ack", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(valid ? { ok: true } : {}) }));
  await signIn(page, app);
  await page.locator("#alert-bell").click();
  const row = page.locator('.alert-row[data-alert-row="e2e-rojo-1"]');
  await expect(row).toBeVisible();
  await row.locator("[data-ack]").click();
  await expect(page.locator("#toast")).toHaveText(/No se pudo marcar/);
  expect(await row.count()).toBe(1);
  expect(await row.locator("[data-ack]").isEnabled()).toBe(true);
  valid = true;
  await row.locator("[data-ack]").click();
  await expect(row).toHaveCount(0);
  await expect(page.locator("#toast")).toHaveText(/vista/);
});
