// tests/e2e/admin-hsi-lifecycle.spec.mjs -- real-browser acceptance for the
// native /admin/hsi screen (static/platform/admin-hsi.mjs mounted by
// templates/admin/hsi.html). Real local ASGI server, real synthetic login,
// real template/native imports/session module and real DOM interactions;
// only the three HSI APIs (/api/admin/hsi-sync, .../resolver, .../conflicts)
// are intercepted with synthetic payloads, because the harness database has
// no HSI conflicts and TURNOS_SKIP_HSI=1 keeps the connector local. Synthetic
// pagehide/pageshow events exercise the template's ownership wiring only --
// they are NOT a BFCache proof. No production endpoint is ever opened.
//
// Port of tests/e2e/admin-hsi-lifecycle.e2e.mjs (20 tests, same titles, same order).
//
// Differences from the node:test original:
// - The tests append to one shared `measurements` record: the second cold-load test closes the
//   ten-capture set and report.json is written from it after the file, so the file is `serial`
//   (a failure skips the rest, a retry re-runs everything on a fresh worker with a fresh record).
// - `test.before` (ES/EN setup through the settings API) is `test.beforeAll`, `test.after` (report.json,
//   written from whatever was recorded, so an interrupted run reports complete:false) is `test.afterAll`.
// - The evidence directory is reserved in `test.beforeAll` (still with `allocateEvidenceRunDir`), not at
//   import time: Playwright imports a spec file in the runner process too, which would create an empty
//   run directory for every listing.
// - `session(lang)` uses the runner's `page` (390x900 through `test.use`), registers the same layout-shift
//   observer init script on its context and the same pageerror/console collectors, signs in with the
//   original sequence (login form, any /admin/* landing, about:blank) and sets the `lang` cookie. The
//   explicit `lang` cookie (and LANG_DEFAULT=es) decides the page language, so the runner's default
//   Accept-Language (en-US) does not matter here. The `finally { context.close() }` blocks are gone.
// - Screenshots go through `saveShot` to the same files as before and are attached to the report.
// - «No hsi-sync request after 5 s» is `expect.poll(...).toBe(true)` with a 5 s timeout instead of a
//   `Promise.race` against a rejecting timer; the hand-made `hold` promises are `holdGate(cleanup)`
//   (released on teardown, so a failing test never leaves a route hanging).
// - Waits and attribute/text checks are web-first (`toBeVisible`, `toHaveAttribute`, `toHaveCount`,
//   `toContainText`, `toHaveValue`, `toBeEnabled`...); the in-page polling `waitForFunction` of
//   `readyPanel`/`readyEmpty` is unchanged. Every «not yet / never» check stays a point-in-time read at
//   the original moment (request counters, `.hsi-tabla`/`.hsi-vacio` absence, the recorded POST bodies,
//   the replacement-owner text, the status text that must not claim success).
// - Locators that stay CSS: the HSI screen's own markup contract (`#sec-hsi`, `.hsi-*`, `[data-hsi-apply]`,
//   `#hsi-status`, `#hsi-rescan-btn`, `#hsi-retry-btn`, `[data-retry]`) is what is driven and measured
//   (geometry, fonts, pseudo-element labels), as are the `#adm-nav a[data-nav="hsi"]` navigation entry
//   and the `<h1 id="adm-h1">` title.
import fs from "node:fs/promises";
import { join } from "node:path";
import {
  test, expect, saveShot, holdGate, holdsFor, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_hsi_native_lifecycle", hsi: true, requests: true },
  viewport: { width: 390, height: 900 },
});
test.describe.configure({ mode: "serial" });

const CF_ID = 7101;
// Operator evidence: one atomically unique durable directory per run, below
// the same artifacts root family the HSI-off suite uses. A previous run's
// evidence can never be overwritten or pruned by this one.
let evidence;
const measurements = [];

test.beforeAll(async ({ app, browser }) => {
  evidence = allocateEvidenceRunDir({ root: artifactPath("human-rounds", "hsi-native-lifecycle"), label: "run" });
  // Real ES/EN dual-language setup through the same settings API the
  // Appearance lifecycle suite uses -- the `lang` cookie below only works
  // against a server that actually offers both languages.
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await page.goto("/admin/login", { waitUntil: "load" });
    await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
    await page.fill("#gate-user", ADMIN_USER);
    await page.fill("#gate-pass", ADMIN_PASS);
    await page.click("#gate-btn");
    await page.waitForFunction(
      () => location.pathname.startsWith("/admin/") && location.pathname !== "/admin/login",
      null, { timeout: 15000 },
    );
    const response = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(response.status()).toBe(200);
  } finally { await context.close(); }
});

test.afterAll(async () => {
  // The report is written from whatever was actually recorded, so an
  // interrupted run reports complete:false -- evidence survives failures.
  const ids = new Set(measurements.map((row) => row.id));
  await fs.writeFile(join(evidence, "report.json"), JSON.stringify({
    expected: 10,
    recorded: measurements.length,
    unique: ids.size,
    complete: measurements.length === 10 && ids.size === 10
      && measurements.every((row) => Array.isArray(row.shifts) && Number.isFinite(row.cls)
        && Array.isArray(row.assets) && row.assets.length > 0 && row.hsiModule),
    measurements,
    boundary: "Local synthetic test database, intercepted HSI API payloads, synthetic pagehide/pageshow events. Not production and not BFCache admission evidence.",
  }, null, 2));
});

// ---- Shared fixture: synthetic HSI API payloads (interception is the only
// ---- synthetic data in this file; every render/controller/session path is
// ---- the real one). Shape mirrors booking_emr._hsi_sync_status_core:
// ---- top-level ok:true, panel metrics integer-count/numeric-percent, and
// ---- apply acknowledgements carry a NUMERIC conflicto_id with BOOLEAN ok.
const RESOLUCIONES = [
  { accion: "mover", label: "Mover al primer turno libre" },
  { accion: "sobreturno", label: "Dejar como sobreturno" },
  { accion: "rechazar", label: "Rechazar la solicitud" },
];
function hsiPanel(open) {
  return {
    abiertos: open ? 1 : 0,
    metricas: { futuras: 12, pct_futuras: open ? 83 : 92 },
    conflictos: open ? [{
      conflicto_id: CF_ID, fecha: "2026-09-12 10:30", solicitud_id: 1,
      paciente: "PACIENTE DE PRUEBA", medico: "DRA. PRUEBA",
      problema: "El turno en HSI quedó en otro horario que el local.",
      resoluciones: RESOLUCIONES,
    }] : [],
    resueltos: [],
  };
}
function syncBody(open) {
  return JSON.stringify({ ok: true, counts: {}, outbox: [], conflicts: [], hsi_activo: false, panel: hsiPanel(open) });
}
function applyAck(detalle) {
  return JSON.stringify({
    ok: true,
    resultados: [{ conflicto_id: CF_ID, ok: true, detalle }],
    panel: hsiPanel(false),
  });
}
async function stubSync(page, open) {
  await page.unroute("**/api/admin/hsi-sync");
  const body = syncBody(open);
  await page.route("**/api/admin/hsi-sync", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body }));
}

// A real login through the real gate, and a layout-shift PerformanceObserver
// installed BEFORE any navigation -- CLS below is always measured from
// actual entries, never assumed zero.
async function session(page, app, lang = "es") {
  const context = page.context();
  await context.addInitScript(() => {
    window.__hsiShifts = [];
    window.__hsiObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput)
        window.__hsiShifts.push({ value: entry.value, startTime: entry.startTime });
    });
    window.__hsiObserver.observe({ type: "layout-shift", buffered: true });
  });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.goto("/admin/login", { waitUntil: "load" });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  // The server owns post-login landing; any current direct screen is fine.
  await page.waitForFunction(
    () => location.pathname.startsWith("/admin/") && location.pathname !== "/admin/login",
    null, { timeout: 15000 },
  );
  await page.goto("about:blank");
  await context.addCookies([{ name: "lang", value: lang, url: app.baseUrl }]);
  return { pageErrors, consoleErrors };
}

async function readyPanel(page) {
  await page.waitForFunction(() =>
    document.querySelectorAll("#sec-hsi .hsi-tabla tbody tr").length === 1
    && document.getElementById("hsi-status").hidden, null, { timeout: 15000 });
}
async function readyEmpty(page) {
  await page.waitForFunction(() => !!document.querySelector("#sec-hsi .hsi-vacio"), null, { timeout: 15000 });
}
const settleFrames = (page) => page.evaluate(
  () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

// ---- 10 ES/EN cold loads: readability, layout and honest CLS -------------
for (const lang of ["es", "en"]) test(`HSI ${lang}: real cold loads at five widths stay readable`, async ({ page, app }, testInfo) => {
  const { pageErrors, consoleErrors } = await session(page, app, lang);
  await stubSync(page, true);
  for (const width of [360, 390, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/hsi", { waitUntil: "load" });
    await readyPanel(page);
    await page.evaluate(() => document.fonts.ready);
    await settleFrames(page);
    // Capture BEFORE any assertion, so failed checks keep their evidence.
    const row = await page.evaluate(() => {
      const host = document.getElementById("sec-hsi");
      const vw = document.documentElement.clientWidth;
      const overflow = [...host.querySelectorAll("*")].flatMap((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && getComputedStyle(el).visibility !== "hidden" && (r.left < -1 || r.right > vw + 1)
          ? [{ tag: el.tagName, cls: String(el.className), left: Math.round(r.left), right: Math.round(r.right) }] : [];
      });
      const shifts = [...window.__hsiShifts, ...window.__hsiObserver.takeRecords()
        .filter((entry) => !entry.hadRecentInput).map((entry) => ({ value: entry.value, startTime: entry.startTime }))];
      let cls = 0, sum = 0, first = 0, last = -Infinity;
      for (const entry of shifts) {
        if (entry.startTime - last >= 1000 || entry.startTime - first >= 5000) { sum = 0; first = entry.startTime; }
        sum += entry.value; last = entry.startTime; cls = Math.max(cls, sum);
      }
      const geom = (el) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { width: r.width, height: r.height, font: parseFloat(getComputedStyle(el).fontSize) };
      };
      const labels = [];
      const seen = (what, el) => {
        if (!el) return;
        const r = el.getBoundingClientRect();
        if (r.width > 2 && r.height > 2) labels.push({ what, size: parseFloat(getComputedStyle(el).fontSize) });
      };
      seen("hero", host.querySelector(".hsi-hero"));
      host.querySelectorAll(".hsi-tabla thead th").forEach((th) => seen("column", th));
      seen("problem", host.querySelector("td.hsi-td-prob"));
      seen("patient", host.querySelector(".hsi-td-pac a"));
      const prob = host.querySelector("td.hsi-td-prob");
      const before = prob ? getComputedStyle(prob, "::before") : null;
      const content = before && before.content;
      const pseudo = content && content !== "none" && content !== "normal"
        ? { what: "data-label", size: parseFloat(before.fontSize), content } : null;
      if (pseudo) labels.push(pseudo);
      const link = host.querySelector(".hsi-td-pac a");
      return {
        vw,
        lang: document.documentElement.lang,
        pageOverflow: document.documentElement.scrollWidth - vw,
        overflow,
        shifts,
        cls,
        controls: {
          apply: geom(host.querySelector("[data-hsi-apply]")),
          rescan: geom(document.getElementById("hsi-rescan-btn")),
          alt: geom(host.querySelector(".hsi-alt")),
        },
        labels,
        pseudo,
        tableViewportWidth: host.querySelector(".hsi-tabla-wrap").clientWidth,
        heroLeft: host.querySelector(".hsi-hero").getBoundingClientRect().left,
        selectTextWidth: (() => {
          const select = host.querySelector(".hsi-alt");
          const cs = getComputedStyle(select);
          const canvas = document.createElement("canvas");
          const ctx = canvas.getContext("2d");
          ctx.font = cs.font;
          return Math.max(...[...select.options].map((option) => ctx.measureText(option.text).width))
            + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + 24;
        })(),
        hero: (host.querySelector(".hsi-hero") || {}).textContent || "",
        columns: [...host.querySelectorAll(".hsi-tabla thead th")].map((th) => th.textContent),
        rescanText: (document.getElementById("hsi-rescan-btn") || {}).textContent || "",
        patient: link ? { href: link.getAttribute("href"), text: link.textContent.trim() } : null,
        medico: ((host.querySelector(".hsi-td-med") || {}).textContent || "").trim(),
        fecha: ((host.querySelector(".hsi-td-fecha") || {}).textContent || "").trim(),
        altOptions: host.querySelectorAll(".hsi-alt option").length,
        altLabel: host.querySelector(".hsi-alt")
          ? host.querySelector(".hsi-alt").getAttribute("aria-label") : null,
        statusHidden: document.getElementById("hsi-status").hidden,
        assets: [...new Set(performance.getEntriesByType("resource")
          .map((entry) => entry.name).filter((name) => name.includes("/static/")))].sort(),
        hsiModule: performance.getEntriesByType("resource")
          .map((entry) => entry.name).find((name) => /\/static\/platform\/admin-hsi\.[a-f0-9]+\.mjs/.test(name)) || null,
      };
    });
    const id = `${lang}-${width}`;
    measurements.push({ id, width, height: 900, ...row, pageErrors: [...pageErrors], consoleErrors: [...consoleErrors] });
    await saveShot(testInfo, page, join(evidence, id + ".png"), { fullPage: true });
    expect(row.lang, id).toBe(lang);
    expect(row.statusHidden, id).toBe(true);
    expect(row.pageOverflow, `${id} zero horizontal page overflow`).toBe(0);
    expect(row.overflow, `${id} no element escapes the viewport`).toEqual([]);
    expect(Number.isFinite(row.cls), id).toBe(true);
    expect(row.controls.apply.height >= 44 && row.controls.apply.width >= 44,
      `${id} resolution button ${row.controls.apply.width}x${row.controls.apply.height}`).toBe(true);
    expect(row.controls.rescan.height >= 44 && row.controls.rescan.width >= 44,
      `${id} rescan ${row.controls.rescan.width}x${row.controls.rescan.height}`).toBe(true);
    expect(row.controls.apply.font, `${id} resolution text ${row.controls.apply.font}px`).toBeGreaterThanOrEqual(16);
    expect(row.controls.rescan.font, `${id} rescan text ${row.controls.rescan.font}px`).toBeGreaterThanOrEqual(16);
    expect(row.controls.alt.height, `${id} select ${row.controls.alt.height}px tall`).toBeGreaterThanOrEqual(44);
    expect(row.controls.alt.font, `${id} select font ${row.controls.alt.font}px`).toBeGreaterThanOrEqual(16);
    expect(row.controls.alt.width,
      `${id} select needs ${row.selectTextWidth}px for its options; has ${row.controls.alt.width}px`).toBeGreaterThanOrEqual(row.selectTextWidth);
    expect(row.heroLeft, `${id} metric text needs a readable side gutter`).toBeGreaterThanOrEqual(16);
    for (const label of row.labels) expect(label.size, `${id} ${label.what} label ${label.size}px`).toBeGreaterThanOrEqual(13);
    expect(row.patient.href, id).toBe("/admin/solicitudes");
    expect(row.patient.text, id).toBe("Paciente de Prueba");
    expect(row.medico, id).toBe("Dra. Prueba");
    expect(!!row.fecha && !/\d{4}-\d{2}-\d{2}/.test(row.fecha), `${id} human date, got "${row.fecha}"`).toBe(true);
    expect(row.hero, id).toMatch(/83%/);
    expect(row.hero, id).toMatch(lang === "en" ? /of the upcoming approved appointments/ : /de los turnos futuros aprobados/);
    expect(row.columns, id).toEqual(lang === "en"
      ? ["Date", "Patient", "Doctor", "Problem", "Resolution", "Other options"]
      : ["Fecha", "Paciente", "Médico", "Problema", "Resolución", "Otras opciones"]);
    expect(row.rescanText, id).toMatch(lang === "en" ? /Check again/ : /Volver a revisar/);
    expect(row.altOptions, id).toBe(3);
    expect(row.altLabel, id).toBe(lang === "en" ? "Other resolution" : "Otra resolución");
    expect(!!row.hsiModule, `${id} native module requested`).toBe(true);
    if (row.tableViewportWidth > 900) {
      expect(row.pseudo, `${id} a real table needs no stacked data-labels`).toBeNull();
    } else {
      expect(!!row.pseudo && row.pseudo.size >= 16,
        `${id} mobile data-label ${row.pseudo ? row.pseudo.size + "px" : "missing"}`).toBe(true);
    }
    if (lang === "es" && width === 360) {
      await expect(page.locator("#hsi-status")).toHaveAttribute("role", "status");
      await expect(page.locator("#hsi-status")).toHaveAttribute("aria-live", "polite");
      await expect(page.locator("h1#adm-h1")).toContainText(/HSI/);
      await expect(page.locator('#adm-nav a[data-nav="hsi"]')).toHaveAttribute("href", "/admin/hsi");
      await expect(page.locator('#adm-nav a[data-nav="hsi"]')).toHaveAttribute("class", "on");
      expect(await page.locator("#sec-hsi [role=tab], #sec-hsi hr").count()).toBe(0);
    }
  }
  expect(pageErrors, `${lang} cold loads must not throw page errors`).toEqual([]);
  if (lang === "en") { // the second language's test closes the 10-capture set
    const ids = new Set(measurements.map((row) => row.id));
    expect(measurements.length, "all ten cold-load captures must be recorded").toBe(10);
    expect(ids.size, "every cold-load capture must be distinct").toBe(10);
    expect(measurements.every((row) => row.assets.length > 0 && row.hsiModule), "empty capture arrays are not success").toBe(true);
  }
});

// ---- Entry deadline: an import stalled past 8s shows the late copy beside the
// ---- screen and keeps loading; the late module mounts and clears the copy.
test("HSI import held past the deadline shows the late copy, then mounts the panel", async ({ page, app, cleanup }) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  const matcher = "**/static/platform/admin-hsi.*.mjs";
  let syncGets = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/admin/hsi-sync") syncGets += 1;
  });
  await stubSync(page, true);
  await page.route(matcher, async (route) => {
    await gate.held;
    try { await route.continue(); } catch { /* the page moved on without it */ }
  });
  await page.goto("/admin/hsi", { waitUntil: "commit" });
  await expect(page.locator(".screen-load-slow [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-hsi [data-retry]").count(), "the copy never lands inside the screen").toBe(0);
  expect(syncGets, "nothing starts before the module arrives").toBe(0);
  gate.release();
  await readyPanel(page);
  expect(syncGets).toBe(1);
  await expect(page.locator(".screen-load-slow"), "the late answer clears the copy").toHaveCount(0);
});

// ---- The entry deadline ends when the module and its dependencies are in. A
// ---- panel answer slower than 8 s is the screen's own waiting, never the
// ---- load failure; the panel paints when the answer arrives.
test("HSI slow panel answer past the entry deadline never shows the load failure and paints when it arrives", async ({ page, app, cleanup }) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  let requested = false;
  await page.route("**/api/admin/hsi-sync", async (route) => {
    requested = true;
    await gate.held;
    try {
      await route.fulfill({ status: 200, contentType: "application/json", body: syncBody(true) });
    } catch { /* the page moved on without it */ }
  });
  await page.goto("/admin/hsi", { waitUntil: "commit" });
  await expect.poll(() => requested, { message: "No hsi-sync request", timeout: 10000 }).toBe(true);
  // 10 s with the answer held: past the template's 8 s deadline, the screen keeps waiting.
  await holdsFor(async () => {
    expect(await page.locator("#sec-hsi [data-retry]").count()).toBe(0);
    expect(await page.locator("#sec-hsi [role=alert]").count()).toBe(0);
  }, { during: 10000, every: 250 });
  gate.release();
  await readyPanel(page);
  expect(await page.locator("#sec-hsi [data-retry]").count()).toBe(0);
});

// ---- A rejected import (main module or any of its three hard dependencies)
// ---- leaves one finite recovery line whose Reload reloads the whole page.
for (const dependency of ["admin-hsi", "admin-session", "i18n", "dates"]) test(`HSI rejected ${dependency} import has a working full-page Reload`, async ({ page, app }) => {
  await session(page, app);
  const matcher = `**/static/platform/${dependency}.*.mjs`;
  await stubSync(page, true);
  await page.route(matcher, (route) => route.fulfill({ status: 503, body: "" }));
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await expect(page.locator("#sec-hsi [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-hsi .hsi-tabla").count()).toBe(0);
  expect((await page.locator("#sec-hsi").textContent()) || "").not.toMatch(/Paciente de Prueba/);
  await page.unroute(matcher);
  await page.locator("#sec-hsi [data-retry]").click();
  await readyPanel(page);
});

// ---- Synthetic lifecycle events (NOT a BFCache proof): pagehide retires the
// ---- owner, so a late import or an already-dispatched response is inert;
// ---- persisted pageshow mounts a fresh owner that owns the next GET.
test("HSI late import after a synthetic pagehide stays inert; persisted pageshow mounts a fresh owner", async ({ page, app, cleanup }) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  const matcher = "**/static/platform/admin-hsi.*.mjs";
  let syncGets = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/admin/hsi-sync") syncGets += 1;
  });
  await stubSync(page, true);
  await page.route(matcher, async (route) => {
    await gate.held;
    try { await route.continue(); } catch { /* the retired owner aborted it */ }
  });
  const imported = page.waitForRequest((request) => /\/static\/platform\/admin-hsi\.[a-f0-9]+\.mjs/.test(request.url()), { timeout: 5000 });
  await page.goto("/admin/hsi", { waitUntil: "commit" });
  await imported;
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  gate.release();
  await page.waitForLoadState("load");
  await settleFrames(page);
  expect(syncGets, "a retired owner must not start the panel GET").toBe(0);
  expect(await page.locator("#sec-hsi .hsi-tabla").count()).toBe(0);
  expect(await page.locator("#sec-hsi [data-retry]").count(), "pagehide cleared the entry deadline timer").toBe(0);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await readyPanel(page);
  expect(syncGets).toBe(1);
});

test("HSI pagehide disposes the pending response; persisted pageshow creates a new owner", async ({ page, app, cleanup }) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  let requested = false;
  await page.route("**/api/admin/hsi-sync", async (route) => {
    requested = true;
    await gate.held;
    try {
      await route.fulfill({ status: 200, contentType: "application/json", body: syncBody(true) });
    } catch { /* the retired owner aborted its request */ }
  });
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await expect.poll(() => requested, { message: "No hsi-sync request", timeout: 5000 }).toBe(true);
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    document.getElementById("sec-hsi").textContent = "Replacement owner";
  });
  gate.release();
  await settleFrames(page);
  expect(await page.locator("#sec-hsi").innerText()).toBe("Replacement owner");
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await readyPanel(page);
  expect(await page.locator("#sec-hsi").innerText()).not.toMatch(/Replacement owner/);
});

// ---- Session boundary: only the exact shared whoami-confirmed expiry and
// ---- the exact 2FA contract navigate; everything else stays local.
for (const [label, status, body, whoamiExpired, destination] of [
  ["ordinary forbidden", 403, { error: "forbidden" }, false, null],
  ["unconfirmed expired response", 401, { error: "no_autenticado" }, false, null],
  ["confirmed expired session", 401, { error: "no_autenticado" }, true, "/admin/login"],
  ["required second factor", 403, { error: "2fa_requerido" }, false, "/admin/account"],
]) test(`HSI session boundary: ${label}`, async ({ page, app }) => {
  await session(page, app);
  await page.route("**/api/admin/hsi-sync", (route) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) }));
  if (whoamiExpired) await page.route("**/api/admin/whoami", (route) =>
    route.fulfill({ status: 401, contentType: "application/json", body: '{"error":"no_autenticado"}' }));
  await page.goto("/admin/hsi", { waitUntil: "load" });
  if (destination) {
    await page.waitForURL((url) => url.pathname === destination, { timeout: 10000 });
    const url = new URL(page.url());
    expect(url.searchParams.get(destination === "/admin/login" ? "next" : "setup")).toBe(
      destination === "/admin/login" ? "/admin/hsi" : "2fa");
    if (destination === "/admin/account") await expect(page.locator("#sec-cuenta")).toBeVisible({ timeout: 10000 });
  } else {
    await expect(page.locator("#hsi-retry-btn")).toBeVisible({ timeout: 10000 });
    expect(new URL(page.url()).pathname).toBe("/admin/hsi");
    expect(await page.locator("#sec-hsi .hsi-tabla").count()).toBe(0);
  }
});

// ---- Load failures are retryable and never an invented empty success.
test("HSI unparsable JSON shows Retry, and Retry draws the real panel with one extra GET", async ({ page, app }) => {
  await session(page, app);
  let body = "not-json{{{";
  let gets = 0;
  page.on("request", (request) => {
    if (request.method() === "GET" && new URL(request.url()).pathname === "/api/admin/hsi-sync") gets += 1;
  });
  await page.route("**/api/admin/hsi-sync", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body }));
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await expect(page.locator("#hsi-retry-btn")).toBeVisible({ timeout: 10000 });
  expect(await page.locator("#sec-hsi .hsi-tabla").count()).toBe(0);
  expect((await page.locator("#sec-hsi").textContent()) || "").not.toMatch(/Cargando|Loading/);
  body = syncBody(true);
  await page.locator("#hsi-retry-btn").click();
  await readyPanel(page);
  expect(gets).toBe(2);
});

test("HSI malformed panel is a retry failure, never an invented empty success", async ({ page, app }) => {
  await session(page, app);
  let body = JSON.stringify({ ok: true, panel: { conflictos: [], resueltos: [] } });
  let gets = 0;
  page.on("request", (request) => {
    if (request.method() === "GET" && new URL(request.url()).pathname === "/api/admin/hsi-sync") gets += 1;
  });
  await page.route("**/api/admin/hsi-sync", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body }));
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await expect(page.locator("#hsi-retry-btn")).toBeVisible({ timeout: 10000 });
  expect(await page.locator("#sec-hsi .hsi-vacio").count(), "a malformed panel must not paint the empty state").toBe(0);
  expect(await page.locator("#sec-hsi .hsi-hero").count()).toBe(0);
  body = syncBody(true);
  await page.locator("#hsi-retry-btn").click();
  await readyPanel(page);
  expect(gets).toBe(2);
});

// ---- Rejection flow with the REAL confirm dialog: cancelling keeps the same
// ---- action selectable; accepting then posts exactly one correct payload.
test("HSI rejection dialog: cancel keeps the action; accept then posts exactly once", async ({ page, app }) => {
  await session(page, app);
  await stubSync(page, true);
  await page.route("**/api/admin/hsi-sync/conflicts", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: applyAck("Solicitud rechazada.") }));
  const posts = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/admin/hsi-sync/conflicts") {
      posts.push(request.postData());
    }
  });
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await readyPanel(page);
  const alt = page.locator("#sec-hsi .hsi-alt");
  page.once("dialog", (dialog) => dialog.dismiss());
  await alt.selectOption("rechazar");
  await expect(alt).toHaveValue("");
  await expect(alt).toBeEnabled();
  expect(posts, "a cancelled confirmation must not post").toEqual([]);
  await stubSync(page, false);
  page.once("dialog", (dialog) => dialog.accept());
  await alt.selectOption("rechazar");
  await readyEmpty(page);
  expect(posts.length, "accepting once posts exactly once").toBe(1);
  expect(JSON.parse(posts[0])).toEqual({ accion: "rechazar", conflictos: [CF_ID] });
  expect(await page.locator("#hsi-status").textContent()).toMatch(/Solicitud rechazada/);
});

// ---- An API refusal restores the retry: the select resets, every control
// ---- re-enables, and the same action succeeds once the API answers.
test("HSI API refusal restores the retry and re-enables every control", async ({ page, app }) => {
  await session(page, app);
  await stubSync(page, true);
  await page.route("**/api/admin/hsi-sync/conflicts", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"hsi_no_disponible"}' }));
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await readyPanel(page);
  const alt = page.locator("#sec-hsi .hsi-alt");
  await alt.selectOption("sobreturno");
  await expect(page.locator("#hsi-status")).toBeVisible({ timeout: 10000 });
  expect(await page.locator("#hsi-status").textContent()).toMatch(/HSI no responde|HSI is not responding/);
  expect(await alt.inputValue(), "the refused action stays selectable").toBe("");
  expect(await alt.isDisabled()).toBe(false);
  await page.locator("#sec-hsi [data-hsi-apply]").click();
  await expect(page.locator("#sec-hsi [data-hsi-apply]")).toBeEnabled();
  expect(await page.locator("#sec-hsi [data-hsi-apply]:disabled, #sec-hsi .hsi-alt:disabled").count()).toBe(0);
  await page.unroute("**/api/admin/hsi-sync/conflicts");
  await page.route("**/api/admin/hsi-sync/conflicts", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: applyAck("Dejado como sobreturno.") }));
  await stubSync(page, false);
  await alt.selectOption("sobreturno");
  await readyEmpty(page);
  expect(await page.locator("#sec-hsi .hsi-tabla").count()).toBe(0);
});

// ---- A pending mutation is single-flight: repeated programmatic events on
// ---- the button, the rescan control and the select cannot duplicate the POST.
test("HSI delayed mutation: repeated programmatic events cannot duplicate the POST", async ({ page, app, cleanup }) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  await stubSync(page, true);
  await page.route("**/api/admin/hsi-sync/conflicts", async (route) => {
    await gate.held;
    await route.fulfill({ status: 200, contentType: "application/json", body: applyAck("Reprogramado.") });
  });
  const posts = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/admin/hsi-sync/conflicts") {
      posts.push(request.postData());
    }
  });
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await readyPanel(page);
  const posted = page.waitForRequest((request) =>
    request.method() === "POST" && request.url().includes("/api/admin/hsi-sync/conflicts"));
  await page.locator("#sec-hsi [data-hsi-apply]").click();
  await posted;
  await page.evaluate(() => {
    const button = document.querySelector("#sec-hsi [data-hsi-apply]");
    for (let i = 0; i < 5; i += 1) button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    const rescan = document.getElementById("hsi-rescan-btn");
    for (let i = 0; i < 3; i += 1) rescan.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    const select = document.querySelector("#sec-hsi .hsi-alt");
    select.value = "sobreturno";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    select.value = "sobreturno";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await stubSync(page, false);
  gate.release();
  await readyEmpty(page);
  expect(posts.length, "exactly one POST survives the repeated events").toBe(1);
  expect(JSON.parse(posts[0])).toEqual({ accion: "mover", conflictos: [CF_ID] });
  expect(await page.locator("#hsi-status").textContent()).toBe("Reprogramado.");
});

// ---- A malformed resolver count is a failure, never "no changes" success.
test("HSI rescan with a malformed count cannot show success", async ({ page, app }) => {
  await session(page, app);
  await stubSync(page, true);
  await page.route("**/api/admin/hsi-sync/resolver", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ ok: true, resumen: { resueltos: "2" }, panel: hsiPanel(true) }),
  }));
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await readyPanel(page);
  await page.locator("#hsi-rescan-btn").click();
  await expect(page.locator("#hsi-status")).toBeVisible({ timeout: 10000 });
  const status = await page.locator("#hsi-status").textContent();
  expect(status).toMatch(/No se pudo revisar|Could not check/);
  expect(status).not.toMatch(/resuelt|resolved/i);
  expect(await page.locator("#sec-hsi .hsi-tabla tbody tr").count(),
    "a malformed resumen must not repaint the panel").toBe(1);
  expect(await page.locator("#hsi-rescan-btn").isDisabled()).toBe(false);
});

// ---- The retired classic controller stays denied while the real native
// ---- module is served immutable from its fingerprinted URL.
test("HSI retired classic assets stay denied while the real native module is served", async ({ page, app }) => {
  await session(page, app);
  await stubSync(page, true);
  await page.goto("/admin/hsi", { waitUntil: "load" });
  await readyPanel(page);
  for (const assetPath of ["/admin-hsi.js", "/static/admin-hsi.aee08932.js", "/static/admin-hsi.deadbeef.js"]) {
    const response = await page.request.get(assetPath);
    expect(response.status(), assetPath).toBe(404);
    expect(response.headers()["cache-control"], assetPath).toMatch(/no-store/);
  }
  const url = await page.evaluate(() => performance.getEntriesByType("resource")
    .find((entry) => /\/static\/platform\/admin-hsi\.[a-f0-9]+\.mjs/.test(entry.name))?.name);
  expect(url, "actual native entry was requested").toBeTruthy();
  const manifestUrl = await page.evaluate(() => window.SANA_STATIC && window.SANA_STATIC["platform/admin-hsi.mjs"]);
  expect(manifestUrl, "a template-owned entry must not expand the shared client manifest").toBeUndefined();
  expect((await page.content()).includes(new URL(url).pathname),
    "the rendered template names the actual fingerprinted entry").toBe(true);
  const response = await page.request.get(url);
  expect(response.status()).toBe(200);
  expect(response.headers()["cache-control"]).toMatch(/immutable/);
  expect(await response.text()).toMatch(/export function mount\(/);
});
