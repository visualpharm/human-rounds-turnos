// tests/e2e/admin-preparations-lifecycle.spec.mjs -- real-browser acceptance
// for native /admin/preparations (static/platform/admin-preparations.mjs).
// Real local ASGI, real login, real template/native imports/session/table
// modules and real DOM; only /api/admin/content/preparations* is intercepted
// with synthetic payloads. Never opens a production endpoint. Never replaces
// the controller. One explicit missing-guard negative control mutates only
// the served template ownership check. templates/admin/preparations.html
// loads the actual native module; these assertions never skip a classic entry.
//
// Missing coverage (do not treat as PASS): actual BFCache admission.
// The shared table-engine column dropping at 360 and its 1280
// re-expansion ARE asserted in-file. Root owns browser/profile wiring,
// the real run, failures, responsive refinements and final acceptance.
//
// Port of tests/e2e/admin-preparations-lifecycle.e2e.mjs (27 tests, same titles, same order).
//
// Differences from the node:test original:
// - One serial describe: the cold-load / editor / column-drop / retired-URL measurement
//   arrays are filled by earlier tests and the last test («en» cold loads) and the
//   `afterAll` report read all of them; the first step (enable both real locale catalogs)
//   is a `beforeAll`. A failure skips the rest; a retry re-runs the file on a fresh worker.
// - The evidence directory is reserved in `beforeAll` (not at import time, which would
//   also run in the runner's listing process) under artifactPath("human-rounds",
//   "preparations-native-lifecycle") - the same <artifacts>/human-rounds tree as before.
//   Screenshots go through saveShot(): same <id>.png files there, plus attached to the
//   report. The `editor-<lang>-390.html` page dumps are still written beside them.
// - The page is the runner's `page` (viewport 390x900 through test.use; the width loops set
//   their own) and the sign-in is the shared loginAdmin() followed by about:blank (the
//   original parked the page on about:blank as well). Layout-shift observer, page-error and
//   console-error collectors, and the `lang` cookie are unchanged. `context.request` is
//   `page.request`, `context.addInitScript` is `page.addInitScript` (installed before the
//   first navigation); URLs are relative to the app's baseURL. The `try/finally` around
//   `context.close()` is gone with the context; held routes are released through the
//   `cleanup` fixture (holdGate), so a failing test never leaves a route hanging.
// - The 8.5 s `waitForTimeout` of the «es» 360px cold load («success cancels the page's
//   8 s owner deadline») is holdsFor(…, { during: 8500, every: 250 }): the list stays
//   mounted and the recovery action never appears over the whole window, i.e. continuously
//   across the deadline instead of one look after a sleep. It is the only deliberate
//   real-time wait of the file.
// - `waitForGet(n)`'s 20 ms polling loop (8 s bound) is `expect.poll` with the same bound
//   and message. «A repeated Save starts only one PUT» (single-flight) is an invariant
//   over a window and is checked with holdsFor() for 300 ms (stricter than one read).
// - The negative controls are kept in the same form (the mutant-guard test of the old
//   template watchdog went with it on 2026-11-16: the guard lives in the shared loader,
//   pinned by tests/screen-boot.test.mjs), and the checkbox / column-drop predicates are asserted to FAIL on the injected CSS and pass
//   again once it is removed.
// - Waits are web-first (`toBeVisible`, `toBeAttached`, `toHaveValue`, `toHaveText`,
//   `toBeChecked`); `waitForFunction` stays only for the column-drop class
//   state (a DOM class predicate over several keys). Checks that pin a moment (`count()` of
//   a withdrawn control, request counters, values read after `settleFrames`, measurements
//   from one evaluate, «save still enabled» after an error) stay point-in-time snapshots.
// - Locators stay CSS where markup is the subject: the editor's ids (#pa-*), `.pa-*` /
//   `.cnt-*` classes, [data-retry] and every geometry/computed-style probe. Labels are
//   used where the original used them (`getByLabel`); the alert is `getByRole("alert")`.
import {
  test, expect, loginAdmin, holdGate, holdsFor, saveShot, artifactPath,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";
import { cumulativeLayoutShift } from "./lib/layout-shift-evidence.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_prep_native_lifecycle" },
  viewport: { width: 390, height: 900 },
});
test.describe.configure({ mode: "serial" });

const CREATED_ID = "prep-created-e2e";
const NATIVE_ENTRY = /\/static\/platform\/admin-preparations\.[a-f0-9]+\.mjs/;
const PREP_API = "/api/admin/content/preparations";
let evidence;
const measurements = [];
const editorMeasurements = [];
const columnDropMeasurements = [];
const shots = [];
const retiredResponses = [];

function isPrepApi(url) {
  const path = new URL(url).pathname;
  return path === PREP_API || path.startsWith(PREP_API + "/");
}
function json(data, status = 200) {
  return { status, contentType: "application/json", body: typeof data === "string" ? data : JSON.stringify(data) };
}
function item(overrides = {}) {
  return {
    id: "eco",
    nombre: "Ecografía de prueba",
    resumen: "Sintético, no clínico",
    match: ["eco"],
    ayuno: { required: true, horas: 8, agua_permitida: true, nota: 'Agua & "té"' },
    antes: ["Traer orden"],
    traer: ["Documento"],
    avisar: ["Alergias"],
    no_hace_falta: ["Ayuno extra"],
    despues: ["Puede irse"],
    fuentes: ["synthetic-pack"],
    sin_preparacion: false,
    extra_flag: "keep-me",
    preparacion: "legacy-antes",
    ...overrides,
  };
}
const eco = item();
const lab = item({ id: "lab", nombre: "Laboratorio de prueba", match: ["lab"] });
const created = item({ id: CREATED_ID, nombre: "First name", _origen: "db", preparacion: undefined });

test.beforeAll(async ({ browser, app }) => {
  evidence = allocateEvidenceRunDir({
    root: artifactPath("human-rounds", "preparations-native-lifecycle"),
    label: "run",
  });
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await loginAdmin(page, app.baseUrl);
    const response = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(response.status()).toBe(200);
  } finally { await context.close(); }
});

test.afterAll(async () => {
  const ids = new Set(measurements.map((row) => row.id));
  const measured = measurements.every((row) => row.measurementComplete);
  const asserted = measurements.every((row) => row.assertionComplete);
  const editorSetComplete = editorMeasurements.length === 10
    && new Set(editorMeasurements.map((row) => row.id)).size === 10;
  const editorAssertionsComplete = editorSetComplete
    && editorMeasurements.every((row) => row.assertionComplete);
  const checkboxGeometryComplete = editorSetComplete
    && editorMeasurements.every((row) => Array.isArray(row.checkTargets)
      && row.checkTargets.length === 2
      && row.checkTargets.map((target) => target.id).sort().join(",") === "pa-ay-agua,pa-ay-req"
      && row.checkTargets.every((target) => target.associated && target.width >= 44 && target.height >= 44));
  const checkboxNegativeControlComplete = editorMeasurements.some((row) =>
    row.id === "editor-es-390" && row.negativeControlRejected === true);
  const helperDensityComplete = editorSetComplete
    && editorMeasurements.every((row) => Array.isArray(row.helpers) && row.helpers.length === 2
      && row.helpers.every((helper) => helper.text && helper.px >= 16 && helper.matchesBodyText === true)
      && row.helpers[0].for === "pa-match" && row.helpers[1].for === "pa-avisar"
      && (row.id.startsWith("editor-es")
        ? (/^Una palabra por línea\./.test(row.helpers[0].text) && /alergias, embarazo, medicación\.$/.test(row.helpers[1].text))
        : (/^One word per line\./.test(row.helpers[0].text) && /allergies, pregnancy, medication\.$/.test(row.helpers[1].text))))
    && editorMeasurements.every((row) => row.crumb && row.crumb.present === true && row.crumb.links === 1
      && row.crumb.parentHref === "/admin/preparations"
      && row.crumb.repeatsCurrentItem === false && row.crumb.hasSeparator === false);
  const columnDropComplete = columnDropMeasurements.length === 3
    && new Set(columnDropMeasurements.map((row) => row.id)).size === 3
    && ["prep-columns-360", "prep-columns-360-negative", "prep-columns-1280"]
      .every((id) => columnDropMeasurements.some((row) => row.id === id && row.measurementComplete && row.assertionComplete))
    && columnDropMeasurements.some((row) => row.id === "prep-columns-360-negative" && row.negativeControlRejected === true);
  const retirementComplete = retiredResponses.length === 12
    && new Set(retiredResponses.map((row) => row.path)).size === 12
    && retiredResponses.every((row) => row.assertionComplete);
  await writeFile(join(evidence, "report.json"), JSON.stringify({
    expectedColdLoads: 10,
    recorded: measurements.length,
    unique: ids.size,
    measurementComplete: measurements.length === 10 && ids.size === 10 && measured,
    assertionComplete: measurements.length === 10 && ids.size === 10 && asserted,
    complete: measurements.length === 10 && ids.size === 10 && measured && asserted && editorAssertionsComplete && retirementComplete && checkboxGeometryComplete && checkboxNegativeControlComplete && helperDensityComplete && columnDropComplete,
    retirementComplete,
    retiredResponses,
    expectedEditorChecks: 10,
    editorSetComplete,
    editorAssertionsComplete,
    checkboxGeometryComplete,
    checkboxNegativeControlComplete,
    expectedHelperNotes: 2,
    helperDensityComplete,
    expectedColumnDropRows: 3,
    columnDropComplete,
    columnDropMeasurements,
    editorMeasurements,
    shots,
    measurements,
    missingCoverage: [
      "actual BFCache admission (synthetic event wiring is tested)",
    ],
    boundary: "Local synthetic test database and intercepted preparations API payloads. Not production and not BFCache admission evidence.",
  }, null, 2));
});

test("retired Preparations URLs fail closed on the real ASGI server", async ({ page, app }) => {
  await session(page, app);
  for (const path of ["/admin-preparations.js", "/static/admin-preparations.js",
    "/static/admin-preparations.11b389d1.js", "/static/admin-preparations.deadbeef.js",
    "/static/platform/admin-preparations.deadbeef.mjs", "/admin-preparaciones.js"]) {
    // a directory-shaped path is 403 inside the asset trees and 404 elsewhere (not on the fallback's list)
    for (const [suffix, expected] of [["", 404], ["/", path.startsWith("/static/") ? 403 : 404]]) {
      const fullPath = path + suffix;
      const response = await page.request.get(fullPath, { maxRedirects: 0 });
      const row = { path: fullPath, status: response.status(), cacheControl: response.headers()["cache-control"], assertionComplete: false };
      retiredResponses.push(row);
      expect(row.status, fullPath).toBe(expected);
      expect(row.cacheControl, fullPath).toBe("no-store");
      row.assertionComplete = true;
    }
  }
});

/** Sign in as the harness admin, park the page on about:blank and set the language cookie. */
async function session(page, app, lang = "es") {
  await page.addInitScript(() => {
    window.__prepShifts = [];
    window.__prepObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (!entry.hadRecentInput)
        window.__prepShifts.push({ value: entry.value, startTime: entry.startTime });
    });
    window.__prepObserver.observe({ type: "layout-shift", buffered: true });
  });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await loginAdmin(page, app.baseUrl);
  await page.goto("about:blank");
  await page.context().addCookies([{ name: "lang", value: lang, url: app.baseUrl }]);
  return { pageErrors, consoleErrors };
}

const settleFrames = (page) => page.evaluate(
  () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

async function readyList(page) {
  await expect(page.locator("#pa-nueva")).toBeAttached();
  await expect(page.locator("#sec-preparaciones tbody tr").first()).toBeAttached();
}
async function readyEditor(page) {
  await expect(page.locator("#sec-preparaciones #pa-guardar")).toBeAttached();
}
async function readyRecovery(page) {
  await expect(page.locator("#sec-preparaciones [data-retry]")).toBeVisible({ timeout: 12000 });
}
/** The save status line has left its «Guardando…» state (it exists and says something else). */
async function saveSettled(page) {
  await expect(page.locator("#sec-preparaciones #pa-msg")).toHaveText(/^(?!Guardando…$)/);
}

async function capture(testInfo, page, id) {
  const { bytes } = await saveShot(testInfo, page, join(evidence, id + ".png"), { fullPage: true });
  expect(bytes, `${id} screenshot must not be empty`).toBeGreaterThan(0);
  shots.push({ id, bytes });
  return bytes;
}

async function nativeEntry(page) {
  return page.evaluate((pattern) => {
    const html = document.documentElement.innerHTML;
    const re = new RegExp(pattern);
    const fromPerf = performance.getEntriesByType("resource").map((entry) => entry.name).find((name) => re.test(name)) || null;
    const fromHtml = (html.match(/\/static\/platform\/admin-preparations\.[a-f0-9]+\.mjs/) || [])[0] || null;
    return {
      fromPerf,
      fromHtml,
      classicScript: /(?:src|href)=["'][^"']*admin-preparations[^"']*\.js["']/.test(html),
    };
  }, NATIVE_ENTRY.source);
}

async function assertNativeEntry(page, label) {
  const info = await nativeEntry(page);
  expect(info.fromHtml, `${label}: rendered server entry must name platform/admin-preparations.mjs`).toBeTruthy();
  expect(info.fromPerf, `${label}: actual native entry must load`).toBeTruthy();
  expect(info.classicScript, `${label}: classic admin-preparations.js must not be the screen entry`).toBe(false);
}

// One geometry predicate serves the real screen and the negative control:
// exactly the two checkbox-bearing .pa-check labels, each an associated
// >=44x44px hit area (the label wraps the input, so the whole box toggles).
const CHECK_TARGET_GEOMETRY = () => {
  const host = document.querySelector("#sec-preparaciones");
  const targets = [...host.querySelectorAll("label.pa-check")]
    .filter((label) => label.querySelector("input[type=checkbox]"))
    .map((label) => {
      const input = label.querySelector("input[type=checkbox]");
      const box = label.getBoundingClientRect();
      return {
        id: input.id,
        width: box.width,
        height: box.height,
        associated: input.labels.length === 1 && input.labels[0] === label,
      };
    });
  return {
    count: targets.length,
    ids: targets.map((target) => target.id).sort(),
    targets,
    ok: targets.length === 2
      && targets.every((target) => target.associated && target.width >= 44 && target.height >= 44),
  };
};

async function stubPrep(page, handler) {
  await page.route((url) => isPrepApi(url), async (route) => {
    try {
      await handler(route);
    } catch {
      try { await route.abort(); } catch { /* already finished */ }
    }
  });
}

function fulfillList(route, items) {
  return route.fulfill(json({ items }));
}

function writeUrl(request) {
  return new URL(request.url()).pathname;
}

// ---- (1) list → Add → POST ack → later PUT of the created id, even if typing during POST
test("Preparations create then PUT the acknowledged id even if typing during POST", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const post = holdGate(cleanup);
  const writes = [];
  await stubPrep(page, async (route) => {
    const request = route.request();
    const method = request.method();
    const path = writeUrl(request);
    if (method === "GET" && path === PREP_API) return fulfillList(route, [eco, lab]);
    if (method === "POST" && path === PREP_API) {
      await post.held;
      return route.fulfill(json({ ok: true, item: created }));
    }
    if (method === "PUT" && path === PREP_API + "/" + CREATED_ID) {
      return route.fulfill(json({ ok: true, item: { ...created, nombre: JSON.parse(request.postData()).nombre } }));
    }
    return route.fulfill(json({ ok: false }, 500));
  });
  page.on("request", (request) => {
    if (!isPrepApi(request.url()) || request.method() === "GET") return;
    writes.push({ method: request.method(), path: writeUrl(request), body: request.postData() });
  });
  await page.goto("/admin/preparations", { waitUntil: "load" });
  await readyList(page);
  await assertNativeEntry(page, "create");
  await page.locator("#pa-nueva").click();
  await readyEditor(page);
  await page.fill("#pa-nombre", "First name");
  const posted = page.waitForRequest((request) =>
    request.method() === "POST" && writeUrl(request) === PREP_API);
  await page.locator("#pa-guardar").click();
  await posted;
  await page.fill("#pa-nombre", "Unsaved new name");
  await capture(testInfo, page, "create-then-put");
  post.release();
  await saveSettled(page);
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Unsaved new name");
  expect(new URL(page.url()).searchParams.get("item")).toBe(CREATED_ID);
  const put = page.waitForRequest((request) =>
    request.method() === "PUT" && writeUrl(request) === PREP_API + "/" + CREATED_ID);
  await page.locator("#pa-guardar").click();
  await put;
  expect(writes.length).toBe(2);
  expect(writes[0].method).toBe("POST");
  expect(writes[0].path).toBe(PREP_API);
  expect(JSON.parse(writes[0].body).nombre).toBe("First name");
  expect(writes[1].method).toBe("PUT");
  expect(writes[1].path).toBe(PREP_API + "/" + CREATED_ID);
  expect(JSON.parse(writes[1].body).nombre).toBe("Unsaved new name");
});

// ---- (2) real DOM textareas keep every clinical array, unknowns, quotes, & and newlines
test("Preparations DOM textareas preserve clinical arrays, unknowns, quotes, ampersands and newlines", async ({ page, app }, testInfo) => {
  await session(page, app);
  const puts = [];
  const loaded = item({
    antes: ["Keep me"],
    traer: ["Keep me"],
    avisar: ["Keep me"],
    no_hace_falta: ["Keep me"],
    despues: ["Keep me"],
  });
  await stubPrep(page, async (route) => {
    const request = route.request();
    const method = request.method();
    const path = writeUrl(request);
    if (method === "GET" && path === PREP_API) return fulfillList(route, [loaded, lab]);
    if (method === "PUT" && path === PREP_API + "/eco") {
      puts.push(request.postData());
      return route.fulfill(json({ ok: true, item: loaded }));
    }
    return route.fulfill(json({ ok: false }, 500));
  });
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await readyEditor(page);
  await assertNativeEntry(page, "clinical");
  await expect(page.getByLabel("Aclaración", { exact: true })).toHaveCount(1);
  await page.locator("#pa-ay-nota").focus();
  await expect(page.locator("#pa-ay-nota")).toHaveCSS("outline-style", "solid");
  expect(await page.locator("#pa-ay-nota").evaluate((el) => parseFloat(getComputedStyle(el).outlineWidth) >= 2)).toBe(true);
  await expect(page.locator("#pa-nombre")).toHaveValue(loaded.nombre);
  for (const key of ["antes", "traer", "avisar", "no_hace_falta", "despues"])
    await expect(page.locator("#pa-" + key)).toHaveValue("Keep me");
  await expect(page.locator("#pa-ay-nota")).toHaveValue('Agua & "té"');
  await page.fill("#pa-match", "eco\nultrasonido");
  await page.fill("#pa-antes", 'Traer "orden" & DNI\nNo comer');
  await page.fill("#pa-traer", 'Toalla & "gel"');
  await page.fill("#pa-avisar", 'Alergia a "yodo" & latex');
  await page.fill("#pa-no_hace_falta", 'No hace falta "ayuno extra"\nNi & nada más');
  await page.fill("#pa-despues", 'Puede "manejar"\nBeber agua & jugo');
  await page.fill("#pa-ay-nota", 'Agua & "té" permitidos');
  await capture(testInfo, page, "clinical-roundtrip");
  // The route handler records the PUT and only then fulfills it: waiting for
  // the response (not the request, which fires before the handler runs) makes
  // the one-shot count below race-free.
  const put = page.waitForResponse((response) =>
    response.request().method() === "PUT" && writeUrl(response.request()) === PREP_API + "/eco");
  await page.locator("#pa-guardar").click();
  await put;
  expect(puts.length, "one PUT from the real Save control").toBe(1);
  const body = JSON.parse(puts[0]);
  expect(body.match).toEqual(["eco", "ultrasonido"]);
  expect(body.antes).toEqual(['Traer "orden" & DNI', "No comer"]);
  expect(body.traer).toEqual(['Toalla & "gel"']);
  expect(body.avisar).toEqual(['Alergia a "yodo" & latex']);
  expect(body.no_hace_falta).toEqual(['No hace falta "ayuno extra"', "Ni & nada más"]);
  expect(body.despues).toEqual(['Puede "manejar"', "Beber agua & jugo"]);
  expect(body.ayuno.nota).toBe('Agua & "té" permitidos');
  expect(body.fuentes).toEqual(["synthetic-pack"]);
  expect(body.sin_preparacion).toBe(false);
  expect(body.extra_flag).toBe("keep-me");
  expect("preparacion" in body).toBe(false);
  expect("Before you come" in body).toBe(false);
});

// ---- (3) a held post-save refresh cannot erase newer typing
test("Preparations held post-save refresh cannot erase newer typing", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  let reads = 0;
  const refresh = holdGate(cleanup);
  await stubPrep(page, async (route) => {
    const request = route.request();
    if (request.method() === "GET" && writeUrl(request) === PREP_API) {
      reads += 1;
      if (reads === 1) return fulfillList(route, [eco, lab]);
      refresh.arrive();
      await refresh.held;
      return fulfillList(route, [item({ nombre: "Saved older value" }), lab]);
    }
    if (request.method() === "PUT") return route.fulfill(json({ ok: true, item: eco }));
    return route.fulfill(json({ ok: false }, 500));
  });
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await readyEditor(page);
  await assertNativeEntry(page, "held-refresh");
  const refreshStarted = page.waitForRequest((request) =>
    request.method() === "GET" && writeUrl(request) === PREP_API);
  await page.locator("#pa-guardar").click();
  await refreshStarted;
  await refresh.arrived;
  expect(reads, "actual post-save refresh has started").toBe(2);
  await page.fill("#pa-nombre", "Newer unsaved draft");
  await capture(testInfo, page, "held-refresh-draft");
  refresh.release();
  await settleFrames(page);
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Newer unsaved draft");
  expect(await page.locator("#pa-nombre").inputValue()).not.toMatch(/Saved older value/);
});

test("Preparations failed post-save refresh keeps saved fields and supports another save", async ({ page, app }, testInfo) => {
  await session(page, app);
  let gets = 0, puts = 0;
  await stubPrep(page, async (route) => {
    const req = route.request();
    if (req.method() === "GET") {
      gets++;
      return gets === 2 ? route.fulfill(json({ error: "synthetic refresh failure" }, 500))
        : fulfillList(route, [eco, lab]);
    }
    if (req.method() === "PUT") {
      puts++;
      return route.fulfill(json({ ok: true, item: eco }));
    }
    return route.fulfill(json({}, 500));
  });
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await readyEditor(page);
  await page.fill("#pa-nombre", "Saved text stays visible");
  await page.locator("#pa-guardar").click();
  await expect(page.locator("#pa-msg.pa-msg--error")).toBeVisible();
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Saved text stays visible");
  expect(await page.locator("#pa-guardar").isEnabled()).toBe(true);
  expect(puts).toBe(1);
  await capture(testInfo, page, "failed-refresh-editor-preserved");
  const retry = page.waitForResponse((r) => r.request().method() === "PUT" && isPrepApi(r.url()));
  await page.locator("#pa-guardar").click();
  await (await retry).finished();
  expect(puts).toBe(2);
  await readyEditor(page);
});

// ---- (4) late old list/read after route switch cannot overwrite the current editor,
// ---- including when fetch abort is a no-op
test("Preparations late list/read after route switch cannot overwrite the current editor", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  // Uncancellable fetch seam: AbortController is advisory in the controller.
  // Dropping the signal proves a completed late JSON still cannot repaint.
  await page.addInitScript(() => {
    const orig = window.fetch;
    window.fetch = function (input, init) {
      if (!init || !init.signal) return orig.call(this, input, init);
      const copy = {};
      for (const key of Object.keys(init)) if (key !== "signal") copy[key] = init[key];
      return orig.call(this, input, copy);
    };
  });
  const pendingGets = [];
  cleanup(() => pendingGets.forEach((entry) => entry.resolve({ items: [] })));
  await stubPrep(page, async (route) => {
    const request = route.request();
    if (request.method() !== "GET" || writeUrl(request) !== PREP_API) {
      return route.fulfill(json({ ok: false }, 500));
    }
    let resolve;
    const ticket = new Promise((ok) => { resolve = ok; });
    pendingGets.push({ route, resolve });
    const body = await ticket;
    await route.fulfill(json(body));
  });
  async function waitForGet(n) {
    await expect.poll(() => pendingGets.length, { message: `GET ${n} did not arrive`, timeout: 8000 }).toBeGreaterThanOrEqual(n);
    return pendingGets[n - 1];
  }
  const nav = page.goto("/admin/preparations", { waitUntil: "commit" });
  const first = await waitForGet(1);
  first.resolve({ items: [eco, lab] });
  await nav;
  await page.waitForLoadState("load");
  await readyList(page);
  await assertNativeEntry(page, "stale-read");
  await page.locator('#sec-preparaciones a[href="/admin/preparations?item=eco"]').click();
  await waitForGet(2);
  await page.evaluate(() => {
    history.pushState({}, "", "/admin/preparations?item=lab");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await waitForGet(3);
  const staleResponse = page.waitForResponse((response) =>
    response.request().method() === "GET" && writeUrl(response.request()) === PREP_API);
  pendingGets[1].resolve({ items: [item({ nombre: "STALE ECO OVERWRITE" })] });
  await (await staleResponse).finished();
  await settleFrames(page);
  expect(await page.locator("#pa-nombre").count(), "stale eco editor must not paint").toBe(0);
  expect(await page.locator("#sec-preparaciones").innerText()).not.toMatch(/STALE ECO OVERWRITE/);
  pendingGets[2].resolve({ items: [eco, lab] });
  await readyEditor(page);
  await capture(testInfo, page, "stale-read-route-switch");
  await expect(page.locator("#pa-nombre")).toHaveValue("Laboratorio de prueba");
  expect(new URL(page.url()).searchParams.get("item")).toBe("lab");
  expect(await page.locator("#sec-preparaciones").innerText()).not.toMatch(/STALE ECO OVERWRITE/);
});

// ---- (5) failed/malformed mutation ack stays current and actionable; no false success
test("Preparations failed or malformed mutation ack remains current with no false success", async ({ page, app }, testInfo) => {
  await session(page, app);
  let puts = 0;
  let gets = 0;
  let ack = { ok: true };
  await stubPrep(page, async (route) => {
    const request = route.request();
    const method = request.method();
    const path = writeUrl(request);
    if (method === "GET" && path === PREP_API) {
      gets += 1;
      return fulfillList(route, [eco, lab]);
    }
    if (method === "PUT" && path === PREP_API + "/eco") {
      puts += 1;
      if (typeof ack === "string") return route.fulfill(json(ack));
      return route.fulfill(json(ack, ack.status || 200));
    }
    return route.fulfill(json({ ok: false }, 500));
  });
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await readyEditor(page);
  await assertNativeEntry(page, "malformed-ack");
  await page.fill("#pa-nombre", "Still the current draft");
  await page.locator("#pa-guardar").click();
  await expect(page.locator("#pa-msg.pa-msg--error")).toBeVisible({ timeout: 10000 });
  await capture(testInfo, page, "malformed-mutation-ack");
  expect(await page.locator("#pa-msg").textContent()).toMatch(/No se pudo completar la acción|Could not complete/);
  expect(await page.locator("#pa-msg").textContent()).not.toMatch(/Guardando/);
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Still the current draft");
  expect(await page.locator("#pa-guardar").isEnabled()).toBe(true);
  expect(new URL(page.url()).searchParams.get("item")).toBe("eco");
  expect(gets, "a malformed ack must not refresh as success").toBe(1);
  ack = "not-json{{{";
  await page.locator("#pa-guardar").click();
  await expect(page.locator("#sec-preparaciones #pa-msg.pa-msg--error")).toHaveText(/^(?!Guardando…$)/);
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Still the current draft");
  expect(await page.locator("#pa-guardar").count()).toBe(1);
  expect(puts).toBe(2);
  expect(gets).toBe(1);
});

for (const scenario of [
  { id: "ordinary-403", status: 403, body: { error: "sin_permiso" }, whoami: false },
  { id: "upstream-401", status: 401, body: { error: "upstream_auth" }, whoami: false },
  { id: "live-session-401", status: 401, body: { error: "no_autenticado" }, whoami: true },
]) test(`Preparations ${scenario.id} preserves a current draft without a login redirect`, async ({ page, app }, testInfo) => {
  await session(page, app);
  let gets = 0, puts = 0, checks = 0;
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") { gets++; return fulfillList(route, [eco]); }
    puts++;
    return route.fulfill(json(scenario.body, scenario.status));
  });
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await readyEditor(page);
  // Install only after shell boot: count the mutation's session verification,
  // not unrelated navigation bootstrap requests.
  await page.route("**/api/admin/whoami", (route) => {
    checks++;
    return route.fulfill(json({ usuario: "synthetic-admin", rol: "admin" }));
  });
  await page.fill("#pa-nombre", "Draft must remain");
  await page.locator("#pa-guardar").click();
  await expect(page.locator("#pa-msg.pa-msg--error")).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/admin/preparations");
  expect(new URL(page.url()).searchParams.get("item")).toBe("eco");
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Draft must remain");
  expect(await page.locator("#pa-guardar").isEnabled()).toBe(true);
  expect(gets).toBe(1); expect(puts).toBe(1);
  expect(checks).toBe(Number(scenario.whoami));
  expect(await page.locator("#pa-msg").innerText()).not.toMatch(/no_autenticado|sin_permiso|upstream_auth/);
  await capture(testInfo, page, scenario.id);
});

test("Preparations repeated Save is single-flight and unlocks after acknowledgement", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const first = holdGate(cleanup);
  let puts = 0;
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") return fulfillList(route, [eco]);
    puts++;
    if (puts === 1) { first.arrive(); await first.held; }
    return route.fulfill(json({ ok: true, item: eco }));
  });
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await readyEditor(page);
  await page.locator("#pa-guardar").click(); await first.arrived;
  await page.locator("#pa-guardar").click({ clickCount: 3 });
  await settleFrames(page);
  await holdsFor(async () => { expect(puts, "only one pending PUT").toBe(1); }, { during: 300 });
  const refresh = page.waitForResponse((r) => isPrepApi(r.url()) && r.request().method() === "GET");
  first.release(); await (await refresh).finished(); await readyEditor(page);
  const next = page.waitForResponse((r) => isPrepApi(r.url()) && r.request().method() === "PUT");
  await page.locator("#pa-guardar").click(); await (await next).finished();
  expect(puts, "acknowledgement releases mutation ownership").toBe(2);
  await capture(testInfo, page, "single-flight-save");
});

test("Preparations Restore and Disable honor cancelled and accepted browser confirmations", async ({ page, app }, testInfo) => {
  await session(page, app);
  let current = item({ _origen: "json+db" });
  const writes = [];
  await stubPrep(page, async (route) => {
    const method = route.request().method(), path = writeUrl(route.request());
    if (method === "GET") return fulfillList(route, [current]);
    writes.push({ method, path });
    if (method === "POST" && path === PREP_API + "/eco/restaurar") {
      current = item({ _origen: "json", nombre: "Restored original" });
      return route.fulfill(json({ ok: true, item: current }));
    }
    if (method === "DELETE" && path === PREP_API + "/eco") {
      current = { ...current, _borrado: true };
      return route.fulfill(json({ ok: true, turnos: 0, conservado: true }));
    }
    return route.fulfill(json({ error: "unexpected" }, 500));
  });
  const dialogs = [];
  let accept = false;
  page.on("dialog", async (dialog) => {
    dialogs.push(dialog.message());
    if (accept) await dialog.accept(); else await dialog.dismiss();
  });
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await readyEditor(page);
  await page.locator("#pa-restaurar").click();
  await page.locator("#pa-deshabilitar").click();
  await settleFrames(page);
  expect(dialogs.length).toBe(2); expect(writes.length).toBe(0);
  expect(await page.locator("#pa-nombre").inputValue()).toBe(eco.nombre);
  accept = true;
  await page.locator("#pa-restaurar").click();
  await expect(page.locator("#pa-nombre")).toHaveValue("Restored original");
  expect(await page.locator("#pa-restaurar").count()).toBe(0);
  await page.locator("#pa-deshabilitar").click();
  await expect(page.locator("#pa-habilitar")).toBeVisible();
  expect(await page.locator("#pa-deshabilitar").count()).toBe(0);
  expect(dialogs.length).toBe(4);
  expect(writes).toEqual([{ method: "POST", path: PREP_API + "/eco/restaurar" },
    { method: "DELETE", path: PREP_API + "/eco" }]);
  await capture(testInfo, page, "confirmed-restore-disable");
});

test("Preparations created item remains visible when returning to the list without extra typing", async ({ page, app }, testInfo) => {
  await session(page, app);
  const items = [eco]; let posts = 0;
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") return fulfillList(route, items);
    if (route.request().method() === "POST" && writeUrl(route.request()) === PREP_API) {
      posts++;
      const saved = item({ ...JSON.parse(route.request().postData()), id: CREATED_ID });
      items.push(saved);
      return route.fulfill(json({ ok: true, item: saved }));
    }
    return route.fulfill(json({ error: "unexpected" }, 500));
  });
  await page.goto("/admin/preparations", { waitUntil: "load" });
  await readyList(page); await page.locator("#pa-nueva").click(); await readyEditor(page);
  await page.fill("#pa-nombre", "Created and retained"); await page.locator("#pa-guardar").click();
  await page.waitForURL((url) => url.searchParams.get("item") === CREATED_ID);
  await expect(page.locator(".cnt-h1")).toHaveText("Created and retained");
  await page.locator("#sec-preparaciones .cnt-crumb a").click();
  await readyList(page);
  const link = page.locator("#sec-preparaciones tbody a", { hasText: "Created and retained" });
  await expect(link).toHaveCount(1);
  expect(new URL(await link.getAttribute("href"), app.baseUrl).searchParams.get("item")).toBe(CREATED_ID);
  expect(posts).toBe(1);
  await capture(testInfo, page, "created-item-list");
});

for (const scenario of [
  { id: "confirmed-expired", status: 401, error: "no_autenticado", destination: "/admin/login" },
  { id: "required-2fa", status: 403, error: "2fa_requerido", destination: "/admin/account" },
]) test(`Preparations ${scenario.id} navigates through the real shared session boundary`, async ({ page, app }, testInfo) => {
  await session(page, app);
  await stubPrep(page, (route) => route.fulfill(json({ error: scenario.error }, scenario.status)));
  // This case proves the redirect, not enrollment. Never generate/capture a
  // TOTP seed merely to screenshot the Account destination.
  if (scenario.status === 403) await page.route("**/api/admin/account/2fa/start", (route) => route.fulfill(json({ error: "synthetic_enrollment_not_in_scope" }, 503)));
  if (scenario.status === 401) await page.route("**/api/admin/whoami", (route) => route.fulfill(json({ error: "no_autenticado" }, 401)));
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
  await page.waitForURL((url) => url.pathname === scenario.destination, { timeout: 10000 });
  const url = new URL(page.url());
  expect(url.searchParams.get(scenario.status === 401 ? "next" : "setup")).toBe(scenario.status === 401 ? "/admin/preparations?item=eco" : "2fa");
  expect(await page.locator("#pa-guardar").count()).toBe(0);
  await capture(testInfo, page, scenario.id);
});

// window.load does not wait for dynamic import. Join the exact module and
// dependency evaluations, then inspect a synchronous mount effect as well as
// requests. A fixed grace period would only move the race.
async function settleLateEntry(page, url) {
  await page.evaluate(async (url) => {
    await import(url);
    await Promise.all(["platform/prep.mjs", "platform/i18n.mjs",
      "platform/admin-session.mjs", "platform/admin-table.mjs"]
      .map((name) => import(window.SANA_STATIC[name])));
  }, url);
  await settleFrames(page);
}

// The real 8 s deadline only says the load is late (2026-11-16, shared loader
// static/platform/screen-boot.mjs): the copy sits before the screen, the held
// import still mounts the list, and its answer clears the copy.
test("Preparations import held past the real deadline shows the late copy, then mounts the list", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const entry = holdGate(cleanup);
  let gets = 0;
  const matcher = "**/static/platform/admin-preparations.*.mjs";
  await stubPrep(page, (route) => { gets++; return fulfillList(route, [eco]); });
  await page.route(matcher, async (route) => { await entry.held; await route.continue(); });
  await page.goto("/admin/preparations", { waitUntil: "commit" });
  await expect(page.locator(".screen-load-slow [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-preparaciones [data-retry]").count(), "the copy never lands inside the screen").toBe(0);
  expect(gets).toBe(0);
  await capture(testInfo, page, "stalled-import-late-copy");
  entry.release();
  await readyList(page); expect(gets).toBe(1);
  await expect(page.locator(".screen-load-slow"), "the late answer clears the copy").toHaveCount(0);
});

test("Preparations synthetic pagehide retires a pending import before persisted remount", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const entry = holdGate(cleanup);
  let gets = 0;
  const matcher = "**/static/platform/admin-preparations.*.mjs";
  await stubPrep(page, (route) => { gets++; return fulfillList(route, [eco]); });
  await page.route(matcher, async (route) => { await entry.held; await route.continue(); });
  const imported = page.waitForRequest((r) => NATIVE_ENTRY.test(r.url()));
  await page.goto("/admin/preparations", { waitUntil: "commit" });
  const importUrl = (await imported).url();
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  const late = page.waitForResponse((r) => r.url() === importUrl);
  entry.release(); await (await late).finished(); await settleLateEntry(page, importUrl);
  expect(await page.locator("#prep-adm-css").count()).toBe(0);
  expect(gets).toBe(0); expect(await page.locator("#pa-nueva").count()).toBe(0);
  expect(await page.locator("#sec-preparaciones [data-retry]").count()).toBe(0);
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await readyList(page); expect(gets).toBe(1);
  await capture(testInfo, page, "import-pagehide-remount");
});

test("Preparations synthetic pagehide rejects uncancellable data and remounts one Save owner", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const firstRead = holdGate(cleanup);
  let gets = 0, puts = 0;
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = (url, options) => {
      if (String(url).startsWith("/api/admin/content/preparations")) {
        options = { ...options }; delete options.signal;
      }
      return original(url, options);
    };
  });
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") {
      gets++; if (gets === 1) { firstRead.arrive(); await firstRead.held; }
      return fulfillList(route, [eco]);
    }
    puts++; return route.fulfill(json({ ok: true, item: eco }));
  });
  const response = page.waitForResponse((r) => isPrepApi(r.url()) && r.request().method() === "GET");
  await page.goto("/admin/preparations?item=eco", { waitUntil: "load" }); await firstRead.arrived;
  await page.evaluate(() => {
    dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    document.querySelector("#sec-preparaciones").textContent = "Replacement owner";
  });
  firstRead.release(); await (await response).finished(); await settleFrames(page);
  expect(await page.locator("#sec-preparaciones").innerText()).toBe("Replacement owner");
  for (let i = 0; i < 2; i++) {
    const refreshed = page.waitForResponse((r) => isPrepApi(r.url()) && r.request().method() === "GET");
    await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
    await (await refreshed).finished();
    await readyEditor(page);
  }
  expect(gets, "one list read per initial mount and each restored owner").toBe(3);
  const saved = page.waitForResponse((r) => isPrepApi(r.url()) && r.request().method() === "PUT");
  await page.locator("#pa-guardar").click(); await (await saved).finished(); await settleFrames(page);
  expect(puts).toBe(1); expect(await page.locator("#pa-guardar").count()).toBe(1);
  await capture(testInfo, page, "data-pagehide-remount");
});

test("Preparations initial API failure has a working deliberate Reload action", async ({ page, app }, testInfo) => {
  await session(page, app); let gets = 0;
  await stubPrep(page, (route) => {
    gets++;
    return gets === 1 ? route.fulfill(json({ error: "unavailable" }, 503)) : fulfillList(route, [eco]);
  });
  await page.goto("/admin/preparations", { waitUntil: "load" });
  await expect(page.locator("#pa-retry")).toBeVisible();
  expect(await page.locator("#pa-nueva").count()).toBe(0);
  await expect(page.locator("#sec-preparaciones").getByRole("alert")).toHaveText(/No se pudo cargar/);
  await capture(testInfo, page, "initial-api-retry");
  await page.locator("#pa-retry").click(); await readyList(page);
  expect(gets).toBe(2); expect(await page.locator("#pa-retry").count()).toBe(0);
  await assertNativeEntry(page, "api-retry");
});

for (const lang of ["es", "en"]) test(`Preparations ${lang} editor labels, focus and targets fit five widths`, async ({ page, app }, testInfo) => {
  await session(page, app, lang);
  await stubPrep(page, (route) => fulfillList(route, [item({ _origen: "json+db" })]));
  for (const width of [360, 390, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/preparations?item=eco", { waitUntil: "load" });
    await readyEditor(page);
    const note = page.getByLabel(lang === "es" ? "Aclaración" : "Note", { exact: true });
    await expect(note).toHaveCount(1); await note.focus();
    const actual = await page.evaluate(() => {
      const host = document.querySelector("#sec-preparaciones"), vw = document.documentElement.clientWidth;
      return {
        width: innerWidth,
        unnamed: [...host.querySelectorAll("input,textarea")].filter((el) => !el.labels?.length).map((el) => el.id),
        smallFields: [...host.querySelectorAll(".pa-in,.pa-ta")].filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16).map((el) => el.id),
        smallActions: [...host.querySelectorAll("button")].filter((el) => el.getBoundingClientRect().height < 44).map((el) => el.id),
        overflow: [...host.querySelectorAll("*")].filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > vw + 1 || r.left < -1); }).map((el) => el.id || el.className),
        noteFocus: document.activeElement?.id,
        outline: getComputedStyle(document.querySelector("#pa-ay-nota")).outlineStyle,
        buttonTypes: [...host.querySelectorAll("button")].map((el) => el.getAttribute("type")),
        hoursMode: document.querySelector("#pa-ay-horas").inputMode,
        sharedTitle: document.querySelector("#adm-h1.cnt-h1")?.textContent,
        headingCount: document.querySelectorAll("h1").length,
        sharedTitleVisible: !!document.querySelector(".adm-topbar #adm-h1")?.getClientRects().length,
        // Copy density: exactly the two necessary guidance notes, measured
        // in the real layout against the input's dark body text token.
        helpers: (() => {
          const body = getComputedStyle(document.querySelector("#pa-nombre")).color;
          return [...host.querySelectorAll(".pa-nota")].map((el) => ({
            for: el.closest("label")?.getAttribute("for") || null,
            text: el.textContent.trim(),
            fontSize: getComputedStyle(el).fontSize,
            px: parseFloat(getComputedStyle(el).fontSize),
            color: getComputedStyle(el).color,
            matchesBodyText: getComputedStyle(el).color === body,
          }));
        })(),
        crumb: (() => {
          const crumb = host.querySelector(".cnt-crumb");
          const title = document.querySelector("#adm-h1.cnt-h1");
          const text = crumb ? crumb.textContent.replace(/\s+/g, " ").trim() : null;
          return {
            present: !!crumb,
            text,
            links: crumb ? crumb.querySelectorAll("a").length : 0,
            parentHref: crumb?.querySelector("a")?.getAttribute("href") || null,
            repeatsCurrentItem: !!crumb && !!title && text.includes(title.textContent.trim()),
            hasSeparator: !!crumb && text.includes("›"),
          };
        })(),
      };
    });
    const geometry = await page.evaluate(CHECK_TARGET_GEOMETRY);
    const recorded = { id: `editor-${lang}-${width}`, expectedWidth: width, ...actual,
      checkTargets: geometry.targets, assertionComplete: false };
    editorMeasurements.push(recorded);
    expect(actual.width).toBe(width);
    expect(actual.unnamed).toEqual([]); expect(actual.smallFields).toEqual([]);
    expect(actual.smallActions).toEqual([]); expect(actual.overflow).toEqual([]);
    expect(actual.noteFocus).toBe("pa-ay-nota"); expect(actual.outline).toBe("solid");
    expect(actual.sharedTitle).toBe("Ecografía de prueba");
    expect(actual.headingCount).toBe(1); expect(actual.sharedTitleVisible).toBe(true);
    expect(actual.buttonTypes.every((type) => type === "button")).toBe(true); expect(actual.hoursMode).toBe("numeric");
    expect(geometry.count, `${recorded.id}: exactly two checkbox hit-area measurements`).toBe(2);
    expect(geometry.ids, `${recorded.id}: both checkbox labels measured`).toEqual(["pa-ay-agua", "pa-ay-req"]);
    for (const target of geometry.targets) {
      expect(target.associated, `${recorded.id}#${target.id}: label is the associated hit area`).toBeTruthy();
      expect(target.width >= 44 && target.height >= 44,
        `${recorded.id}#${target.id}: hit area ${target.width}x${target.height} is under 44px`).toBe(true);
    }
    // Copy density: exactly two guidance notes survive (the match rule and
    // the avisar instruction), localized, >=16px, in the dark body text.
    expect(actual.helpers.length, `${recorded.id}: exactly two guidance notes`).toBe(2);
    const [matchHint, avisarHint] = actual.helpers;
    expect(matchHint.for, `${recorded.id}: first note belongs to the match rule`).toBe("pa-match");
    expect(avisarHint.for, `${recorded.id}: second note belongs to the avisar guidance`).toBe("pa-avisar");
    expect(matchHint.text, `${recorded.id}: match instructions stay localized`).toMatch(lang === "en" ? /^One word per line\./ : /^Una palabra por línea\./);
    expect(avisarHint.text, `${recorded.id}: avisar guidance stays localized`).toMatch(lang === "en" ? /allergies, pregnancy, medication\.$/ : /alergias, embarazo, medicación\.$/);
    for (const helper of actual.helpers) {
      expect(helper.text.length > 0, `${recorded.id}: no empty helper spans`).toBe(true);
      expect(helper.px >= 16, `${recorded.id}: helper at ${helper.px}px is under 16px`).toBe(true);
      expect(helper.matchesBodyText, `${recorded.id}: helper must use the dark body text token`).toBe(true);
    }
    // The breadcrumb keeps only the parent link; the shared heading already
    // names the current item, so no duplicate and no trailing separator.
    expect(actual.crumb.present, `${recorded.id}: breadcrumb present`).toBe(true);
    expect(actual.crumb.links, `${recorded.id}: breadcrumb keeps exactly the parent link`).toBe(1);
    expect(actual.crumb.parentHref, `${recorded.id}: parent link still targets the list`).toBe("/admin/preparations");
    expect(actual.crumb.repeatsCurrentItem, `${recorded.id}: breadcrumb must not repeat the current item`).toBe(false);
    expect(actual.crumb.hasSeparator, `${recorded.id}: no trailing separator`).toBe(false);
    expect(actual.crumb.text, `${recorded.id}: crumb holds only the parent title`).toBe(lang === "en" ? "How to prepare" : "Cómo prepararse");
    if (width === 390) {
      for (const id of ["pa-ay-req", "pa-ay-agua"]) {
        const box = page.locator("#" + id);
        const label = page.locator(`label.pa-check:has(#${id})`);
        const start = await box.isChecked();
        const labelBox = await stableBox(label, `${id} label`);
        const checkBox = await stableBox(box, `${id} checkbox`);
        expect(labelBox.width >= 44 && labelBox.height >= 44, `${id} label is at least 44x44`).toBe(true);
        const x = labelBox.x + labelBox.width - 8, y = labelBox.y + labelBox.height / 2;
        expect(x > checkBox.x + checkBox.width + 8, `${id} click point sits outside the tiny box`).toBe(true);
        await page.mouse.click(x, y);
        await expect(box, `${id} label click toggles`).toBeChecked({ checked: !start });
        await page.mouse.click(x, y);
        await expect(box, `${id} label click reverses`).toBeChecked({ checked: start });
        await box.focus();
        await page.keyboard.press("Space");
        await expect(box, `${id} Space toggles`).toBeChecked({ checked: !start });
        await page.keyboard.press("Space");
        await expect(box, `${id} Space reverses`).toBeChecked({ checked: start });
      }
      await expect(page.locator("#pa-ay-horas"), "hours draft intact").toHaveValue("8");
      await expect(page.locator("#pa-ay-nota"), "note draft intact").toHaveValue('Agua & "té"');
      await expect(page.locator("#pa-nombre"), "name draft intact").toHaveValue("Ecografía de prueba");
    }
    if (lang === "es" && width === 390) {
      // Negative control: shrink the owned labels in-browser; the SAME
      // predicate must reject, then pass again once the regression is gone.
      await page.evaluate(() => {
        const style = document.createElement("style");
        style.id = "pa-check-geometry-regression";
        style.textContent = "#sec-preparaciones .pa-ayuno .pa-check:has(input[type=checkbox]){min-height:43.75px;height:43.75px;min-width:0}";
        document.head.appendChild(style);
      });
      const regressed = await page.evaluate(CHECK_TARGET_GEOMETRY);
      expect(regressed.ok, "shrunk labels must fail the same geometry predicate").toBe(false);
      expect(regressed.targets.some((target) => target.height < 44), "the regression really shrinks a hit area").toBe(true);
      expect(regressed.targets.every((target) => target.height > 43.5), "fractional shortfall must not round up to a pass").toBe(true);
      await page.evaluate(() => document.getElementById("pa-check-geometry-regression").remove());
      const restoredGeometry = await page.evaluate(CHECK_TARGET_GEOMETRY);
      expect(restoredGeometry.ok, "removing the regression restores the hit areas").toBe(true);
      recorded.negativeControlRejected = true;
    }
    recorded.assertionComplete = true;
    if (width === 390) await writeFile(join(evidence, `editor-${lang}-390.html`), await page.content());
    await capture(testInfo, page, `editor-${lang}-${width}`);
  }
});

// ---- shared admin-table engine: actual column dropping at 360 and
// ---- re-expansion at 1280, on the real native entry and real fit engine
const DROPPED_AT_360 = ["resumen", "match", "ayuno"];

// One drop predicate serves the real screen and the negative control: a
// column is visible only when its header AND every cell paint (computed
// display + real bounds), hidden only when none of them do, and the
// engine's own pa-oc-* class on the table must agree with the expectation.
const COLUMN_DROP_STATE = (expectedHidden) => {
  const keys = ["nombre", "resumen", "match", "ayuno"];
  const host = document.querySelector("#sec-preparaciones");
  const table = host && host.querySelector("table");
  if (!table || !table.tHead || !table.tHead.rows.length || !table.tBodies.length) {
    return { present: false, ok: false, reason: "list table missing" };
  }
  const headers = Array.prototype.slice.call(table.tHead.rows[0].cells);
  const rows = Array.prototype.slice.call(table.tBodies[0].rows);
  const painted = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0;
  };
  const columns = keys.map((key, i) => {
    const header = headers[i];
    const cells = rows.map((row) => row.cells[i]);
    const headerPainted = !!header && painted(header);
    const cellsPainted = cells.length > 0 && cells.every(painted);
    const explicitlyHidden = (el) => !!el && getComputedStyle(el).display === "none"
      && el.getBoundingClientRect().width === 0;
    const cellsBlank = cells.length > 0 && cells.every(explicitlyHidden);
    return {
      key,
      headerText: header ? header.textContent.trim() : null,
      headerDisplay: header ? getComputedStyle(header).display : null,
      headerWidth: header ? header.getBoundingClientRect().width : null,
      engineDropped: key !== "nombre" && table.classList.contains("pa-oc-" + key),
      cellTexts: cells.map((cell) => (cell ? cell.textContent.trim() : null)),
      cellDisplays: cells.map((cell) => (cell ? getComputedStyle(cell).display : null)),
      cellWidths: cells.map((cell) => (cell ? cell.getBoundingClientRect().width : null)),
      visible: headerPainted && cellsPainted,
      hidden: explicitlyHidden(header) && cellsBlank,
    };
  });
  return {
    present: true,
    viewportWidth: window.innerWidth,
    rows: rows.length,
    tableClass: String(table.className),
    tableWidth: table.getBoundingClientRect().width,
    expectedHidden,
    columns,
    ok: columns.every((col) => (col.key === "nombre")
      ? expectedHidden.indexOf(col.key) === -1 && col.visible
      : (expectedHidden.indexOf(col.key) !== -1
        ? col.hidden && !col.visible && col.engineDropped === true
        : col.visible && !col.hidden && col.engineDropped === false)),
  };
};

// The engine drops columns by toggling pa-oc-* classes on the table inside
// a requestAnimationFrame fit (and again, debounced, after a viewport
// resize): waiting on the class state itself is the condition wait, not a
// timed sleep.
async function waitForColumnDrop(page, keys, dropped) {
  await page.waitForFunction(({ keys, dropped }) => {
    const table = document.querySelector("#sec-preparaciones table");
    return !!table && keys.every((key) => table.classList.contains("pa-oc-" + key) === dropped);
  }, { keys, dropped }, { timeout: 15000 });
}

test("Preparations shared table engine drops columns at 360 and restores them at 1280", async ({ page, app }, testInfo) => {
  await session(page, app);
  const ecoCols = item({
    resumen: "Resumen eco sintético",
    match: ["eco", "ultrasonido"],
    ayuno: { required: true, horas: 8, agua_permitida: true, nota: 'Agua & "té"' },
  });
  const labCols = item({
    id: "lab", nombre: "Laboratorio de prueba", resumen: "Resumen laboratorio sintético",
    match: ["lab"], ayuno: { required: false, horas: null, agua_permitida: true, nota: null },
  });
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") return fulfillList(route, [ecoCols, labCols]);
    return route.fulfill(json({ ok: false }, 500));
  });
  await page.setViewportSize({ width: 360, height: 900 });
  await page.goto("/admin/preparations", { waitUntil: "load" });
  await readyList(page);
  await assertNativeEntry(page, "column-drop");
  await waitForColumnDrop(page, DROPPED_AT_360, true);
  await settleFrames(page);
  const narrow = await page.evaluate(COLUMN_DROP_STATE, DROPPED_AT_360);
  const narrowShot = await capture(testInfo, page, "prep-columns-360");
  const narrowRow = {
    id: "prep-columns-360", expectedHidden: DROPPED_AT_360, state: narrow, screenshotBytes: narrowShot,
    measurementComplete: narrow.present === true && narrow.rows === 2 && narrow.tableWidth > 0
      && narrow.columns.every((col) => !!col.headerText && Number.isFinite(col.headerWidth)
        && col.cellTexts.length === 2 && col.cellTexts.every((text) => typeof text === "string")
        && col.cellWidths.length === 2 && col.cellWidths.every((width) => Number.isFinite(width)))
      && narrowShot > 0,
    assertionComplete: false,
  };
  columnDropMeasurements.push(narrowRow);
  expect(narrow.present, "the shared-engine list table must be mounted").toBe(true);
  expect(narrow.ok, "at 360 the engine must drop exactly resumen, match and ayuno").toBe(true);
  expect(narrow.columns.filter((col) => col.visible).map((col) => col.key)).toEqual(["nombre"]);
  expect(narrow.columns.filter((col) => col.hidden).map((col) => col.key)).toEqual(DROPPED_AT_360);
  const narrowName = narrow.columns.find((col) => col.key === "nombre");
  expect(narrowName.cellTexts).toEqual(["Ecografía de prueba", "Laboratorio de prueba"]);
  expect(narrowName.headerWidth > 0 && narrowName.cellWidths.every((width) => width > 0),
    "the retained nombre column must paint real bounds, header and cells").toBe(true);
  for (const key of DROPPED_AT_360) {
    const col = narrow.columns.find((column) => column.key === key);
    expect(col.headerDisplay, `${key} header must be display:none at 360`).toBe("none");
    expect(col.cellDisplays.every((display) => display === "none"), `${key} cells must be display:none at 360`).toBe(true);
    expect(col.cellWidths.every((width) => width === 0), `${key} cells must have zero bounds at 360`).toBe(true);
    expect(col.engineDropped, `${key} must carry the engine drop class`).toBe(true);
  }
  // Navigation survives the narrow layout: the retained column's link
  // still opens the editor, and coming back re-drops on the refit table.
  await page.locator('#sec-preparaciones a[href="/admin/preparations?item=eco"]').click();
  await readyEditor(page);
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Ecografía de prueba");
  await page.locator("#sec-preparaciones .cnt-crumb a").click();
  await readyList(page);
  await waitForColumnDrop(page, DROPPED_AT_360, true);
  narrowRow.assertionComplete = true;
  // Negative control: force-reveal one dropped column over the engine's
  // decision; the SAME predicate must reject it, then pass again once the
  // regression is gone.
  await page.evaluate(() => {
    const style = document.createElement("style");
    style.id = "pa-column-drop-regression";
    style.textContent = "#sec-preparaciones table.pa-oc-ayuno :is(th,td):nth-child(4){display:table-cell !important}"
      + "#sec-preparaciones table.pa-oc-ayuno colgroup col{width:110px !important}";
    document.head.appendChild(style);
  });
  const regressed = await page.evaluate(COLUMN_DROP_STATE, DROPPED_AT_360);
  const negativeShot = await capture(testInfo, page, "prep-columns-360-negative");
  const regressedAyuno = regressed.columns.find((col) => col.key === "ayuno");
  const negativeRow = {
    id: "prep-columns-360-negative",
    injectedCss: "force-reveal ayuno; every col width110px to cover visible-cell remapping when other cells are display:none",
    regressed, screenshotBytes: negativeShot,
    measurementComplete: regressed.present === true && regressedAyuno.headerWidth > 0
      && regressedAyuno.cellWidths.every((width) => width > 0) && negativeShot > 0,
    negativeControlRejected: false,
    assertionComplete: false,
  };
  columnDropMeasurements.push(negativeRow);
  expect(regressed.ok, "a revealed dropped column must fail the same drop predicate").toBe(false);
  expect(regressedAyuno.visible, "the regression really reveals the ayuno column").toBe(true);
  expect(regressedAyuno.headerWidth > 0 && regressedAyuno.cellWidths.every((width) => width > 0),
    "the revealed column must paint real bounds, header and cells").toBe(true);
  expect(regressedAyuno.engineDropped, "the engine still owns ayuno as dropped while CSS reveals it").toBe(true);
  await page.evaluate(() => document.getElementById("pa-column-drop-regression").remove());
  const restoredNarrow = await page.evaluate(COLUMN_DROP_STATE, DROPPED_AT_360);
  expect(restoredNarrow.ok, "removing the regression restores the dropped state").toBe(true);
  negativeRow.restored = restoredNarrow;
  negativeRow.negativeControlRejected = true;
  negativeRow.assertionComplete = true;
  // Re-expansion: same page, real viewport resize; the engine's refit
  // restores every dropped column and its content, and navigation still
  // works at desktop width.
  await page.setViewportSize({ width: 1280, height: 900 });
  await waitForColumnDrop(page, DROPPED_AT_360, false);
  await settleFrames(page);
  const wide = await page.evaluate(COLUMN_DROP_STATE, []);
  const wideShot = await capture(testInfo, page, "prep-columns-1280");
  const wideRow = {
    id: "prep-columns-1280", expectedHidden: [], state: wide, screenshotBytes: wideShot,
    measurementComplete: wide.present === true && wide.rows === 2 && wide.tableWidth > 0
      && wide.columns.every((col) => !!col.headerText && col.headerWidth > 0
        && col.cellTexts.length === 2 && col.cellTexts.every((text) => typeof text === "string" && text.length > 0)
        && col.cellWidths.length === 2 && col.cellWidths.every((width) => width > 0))
      && wideShot > 0,
    assertionComplete: false,
  };
  columnDropMeasurements.push(wideRow);
  expect(wide.viewportWidth).toBe(1280);
  expect(wide.ok, "at 1280 the engine must restore every dropped column").toBe(true);
  expect(wide.columns.map((col) => col.visible)).toEqual([true, true, true, true]);
  expect(wide.columns.map((col) => col.engineDropped)).toEqual([false, false, false, false]);
  const wideTexts = (key) => wide.columns.find((col) => col.key === key).cellTexts;
  expect(wideTexts("nombre")).toEqual(["Ecografía de prueba", "Laboratorio de prueba"]);
  expect(wideTexts("resumen")).toEqual(["Resumen eco sintético", "Resumen laboratorio sintético"]);
  expect(wideTexts("match")).toEqual(["eco · ultrasonido", "lab"]);
  // No fasting is an empty cell, not a «No» (2026-11-15, c1a75f220).
  expect(wideTexts("ayuno")).toEqual(["8 h", ""]);
  await page.locator('#sec-preparaciones a[href="/admin/preparations?item=eco"]').click();
  await readyEditor(page);
  expect(await page.locator("#pa-nombre").inputValue()).toBe("Ecografía de prueba");
  wideRow.assertionComplete = true;
});

// ---- (6) native entry failed import or ready rejection offers bounded Reload recovery
test("Preparations failed native import offers bounded Reload recovery", async ({ page, app }, testInfo) => {
  await session(page, app);
  const matcher = "**/static/platform/admin-preparations.*.mjs";
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") return fulfillList(route, [eco, lab]);
    return route.fulfill(json({ ok: false }, 500));
  });
  await page.route(matcher, (route) => route.fulfill({ status: 503, body: "" }));
  await page.goto("/admin/preparations", { waitUntil: "load" });
  await readyRecovery(page);
  await capture(testInfo, page, "import-reload-recovery");
  expect(await page.locator("#pa-nueva").count()).toBe(0);
  expect((await page.locator("#sec-preparaciones").textContent()) || "").not.toMatch(/Ecografía de prueba/);
  const requested = await nativeEntry(page);
  expect(requested.fromHtml || requested.fromPerf,
    "failed import still has to be the actual native entry, not a substitute bootstrap").toBeTruthy();
  await page.unroute(matcher);
  await page.locator("#sec-preparaciones [data-retry]").click();
  await readyList(page);
  await assertNativeEntry(page, "reload-recovered");
  expect(await page.locator("#sec-preparaciones tbody tr").count()).toBe(2);
});

test("Preparations ready rejection of a hard dependency offers bounded Reload recovery", async ({ page, app }, testInfo) => {
  await session(page, app);
  const matcher = "**/static/platform/prep.*.mjs";
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") return fulfillList(route, [eco, lab]);
    return route.fulfill(json({ ok: false }, 500));
  });
  await page.route(matcher, (route) => route.fulfill({ status: 503, body: "" }));
  await page.goto("/admin/preparations", { waitUntil: "load" });
  await readyRecovery(page);
  await capture(testInfo, page, "ready-reject-reload");
  expect(await page.locator("#pa-nueva").count()).toBe(0);
  await page.unroute(matcher);
  await page.locator("#sec-preparaciones [data-retry]").click();
  await readyList(page);
  await assertNativeEntry(page, "dep-reload-recovered");
});

// ---- 10 ES/EN cold loads: observer CLS, overflow, imported resources, console, screenshot size
for (const lang of ["es", "en"]) test(`Preparations ${lang}: real cold loads at five widths stay readable`, async ({ page, app }, testInfo) => {
  const { pageErrors, consoleErrors } = await session(page, app, lang);
  await stubPrep(page, async (route) => {
    if (route.request().method() === "GET") return fulfillList(route, [eco, lab]);
    return route.fulfill(json({ ok: false }, 500));
  });
  for (const width of [360, 390, 768, 1024, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/preparations", { waitUntil: "load" });
    await readyList(page);
    await page.evaluate(() => document.fonts.ready);
    await settleFrames(page);
    // One real deadline-survival check proves success cancels the timer,
    // rather than merely observing a screen before the deadline expires.
    // The page gives up after 8 s: watch the whole window past it.
    if (lang === "es" && width === 360) {
      await holdsFor(async () => {
        expect(await page.locator("#pa-nueva").count()).toBe(1);
        expect(await page.locator("#sec-preparaciones [data-retry]").count()).toBe(0);
      }, { during: 8500, every: 250 });
    }
    const row = await page.evaluate(() => {
      const host = document.getElementById("sec-preparaciones");
      const vw = document.documentElement.clientWidth;
      const overflow = [...host.querySelectorAll("*")].flatMap((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && getComputedStyle(el).visibility !== "hidden" && (r.left < -1 || r.right > vw + 1)
          ? [{ tag: el.tagName, cls: String(el.className), left: Math.round(r.left), right: Math.round(r.right) }] : [];
      });
      const shifts = [...window.__prepShifts, ...window.__prepObserver.takeRecords()
        .filter((entry) => !entry.hadRecentInput).map((entry) => ({ value: entry.value, startTime: entry.startTime }))];
      const add = document.getElementById("pa-nueva");
      const title = document.querySelector("#adm-h1.cnt-h1");
      const assets = [...new Set(performance.getEntriesByType("resource")
        .map((entry) => entry.name).filter((name) => name.includes("/static/")))].sort();
      const prepModule = performance.getEntriesByType("resource")
        .map((entry) => entry.name).find((name) => /\/static\/platform\/admin-preparations\.[a-f0-9]+\.mjs/.test(name)) || null;
      return {
        vw,
        actualWidth: window.innerWidth,
        actualHeight: window.innerHeight,
        lang: document.documentElement.lang,
        pageOverflow: document.documentElement.scrollWidth - vw,
        overflow,
        shifts,
        addText: add ? add.textContent.trim() : "",
        titleText: title ? title.textContent.trim() : "",
        headingCount: document.querySelectorAll("h1").length,
        sharedTitleVisible: !!title?.getClientRects().length && !!title.closest(".adm-topbar"),
        rows: host.querySelectorAll("tbody tr").length,
        assets,
        prepModule,
        htmlHasNative: /\/static\/platform\/admin-preparations\.[a-f0-9]+\.mjs/.test(document.documentElement.innerHTML),
      };
    });
    row.cls = cumulativeLayoutShift(row.shifts);
    const id = `${lang}-${width}`;
    const screenshotBytes = await capture(testInfo, page, id);
    const measurementComplete = Array.isArray(row.shifts) && Number.isFinite(row.cls)
      && Array.isArray(row.overflow) && Array.isArray(row.assets) && row.assets.length > 0
      && !!row.prepModule && screenshotBytes > 0 && Array.isArray(consoleErrors);
    const record = {
      id, width, height: 900, ...row, screenshotBytes,
      pageErrors: [...pageErrors], consoleErrors: [...consoleErrors],
      measurementComplete, assertionComplete: false,
    };
    measurements.push(record);
    expect(row.lang, id).toBe(lang);
    expect(row.actualWidth, `${id} actual viewport width`).toBe(width);
    expect(row.actualHeight, `${id} actual viewport height`).toBe(900);
    expect(row.pageOverflow, `${id} zero horizontal page overflow`).toBe(0);
    expect(row.overflow, `${id} no element escapes the viewport`).toEqual([]);
    expect(Number.isFinite(row.cls), `${id} CLS must come from the observer`).toBe(true);
    expect(row.cls >= 0 && row.cls <= 0.1, `${id} CLS exceeds 0.1: ${row.cls}`).toBe(true);
    expect(row.htmlHasNative && row.prepModule, `${id} native module requested`).toBeTruthy();
    expect(row.rows, id).toBe(2);
    expect(row.titleText, id).toMatch(lang === "en" ? /How to prepare/ : /Cómo prepararse/);
    expect(row.headingCount, `${id}: exactly one heading`).toBe(1);
    expect(row.sharedTitleVisible, `${id}: shared title band stays visible`).toBe(true);
    expect(row.addText, id).toMatch(lang === "en" ? /New preparation/ : /Nueva preparación/);
    expect(screenshotBytes, `${id} screenshot bytes`).toBeGreaterThan(0);
    expect(pageErrors, `${id} page errors`).toEqual([]);
    expect(consoleErrors, `${id} console errors`).toEqual([]);
    record.assertionComplete = true;
  }
  expect(pageErrors, `${lang} cold loads must not throw page errors`).toEqual([]);
  expect(consoleErrors, `${lang} cold loads must not log console errors`).toEqual([]);
  if (lang === "en") {
    const ids = new Set(measurements.map((row) => row.id));
    expect(measurements.length, "all ten cold-load captures must be recorded").toBe(10);
    expect(ids.size, "every cold-load capture must be distinct").toBe(10);
    expect(measurements.every((row) => row.measurementComplete), "empty capture arrays are not success").toBe(true);
    expect(measurements.every((row) => row.assertionComplete), "measurement without assertions is not success").toBe(true);
  }
});
