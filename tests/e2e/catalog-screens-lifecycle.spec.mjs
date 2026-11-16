// Served native catalog lifecycle evidence. This suite deliberately exercises
// the actual ASGI app and disposable example-town database: routes may hold or
// reject a real response for a named recovery case, but never invent HTML,
// controllers, or successful persistence.
//
// Port of tests/e2e/catalog-screens-lifecycle.e2e.mjs (24 story tests + 1 validation-recovery
// test, same titles, same order, each story still runs at the same five widths).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })). Each story
//   opens one context per width through `newContext` (viewport + the layout-shift observer
//   init script), signs in with the shared loginAdmin() and leaves the login landing document
//   for about:blank, as before; the contexts are closed with the test.
// - The file is `serial`: every story appends its record to the module-level `records` list
//   and the afterAll hook (the original `after`) writes report.json and asserts that ALL 24
//   stories produced complete, distinct evidence. A failure skips the rest and a retry re-runs
//   the whole file on a fresh worker, so the evidence list can never be completed by a
//   half-run.
// - The per-run evidence directory (allocateEvidenceRunDir, lib/hsi-evidence.mjs, unchanged)
//   is allocated in beforeAll instead of at module import. The screenshots (viewport and full
//   page) go through saveShot() into that directory (not attached to the report: 374 frames per run); the
//   .html and .json files and the size/uniqueness pins of catalogEvidenceComplete() are as
//   before.
// - The hold/reject recovery cases use holdGate(cleanup) (released on cleanup too) for the
//   withheld response; the race «interception vs. response without interception» is unchanged.
// - Waits are web-first (waitFor -> expect(...).toBeVisible/toBeHidden/toHaveCount, the
//   focus wait -> toBeFocused, URL waits -> waitForURL, predicate waits -> toContainText /
//   expect.poll) with the original explicit timeouts (12 s on screen readiness and saves).
//   Positive state reads that only follow a user action (checkbox / visibility toggles of the
//   service editor, rendered doctor ids after a filter) are retrying expects or expect.poll;
//   negative reads («unchecked», «not visible», no rows, still enabled) and the
//   reads that guard a race stay snapshots (rows while the response is held, `aria-invalid`
//   gone, the persisted catalog after an invalid save, geometry and CLS in capture()).
// - Locators left as CSS: the catalog editor's ids / data attributes (#sec-*, #cnt-*, #svc-*,
//   #cen-*, #med-*, [data-tab], [data-tabpanel], [data-catalog-*]) and the table classes -
//   the contract and the geometry under test.
import { readFile, stat, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";
import { test, expect, loginAdmin, saveShot, holdGate } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";
import { cumulativeLayoutShift } from "./lib/layout-shift-evidence.mjs";
import { catalogEvidenceComplete, requiredPhases } from "./lib/catalog-evidence.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_catalog_screens_lifecycle" } });
test.describe.configure({ mode: "serial" });

const WIDTHS = [360, 390, 768, 1024, 1280];
let evidence = "";
const records = [];
const validationRecovery = [];
const sourceHash = createHash("sha256").update(await readFile(new URL("../../static/platform/admin-content.mjs", import.meta.url))).digest("hex");

const STORIES = [
  ["S01", "servicios", "list"], ["S02", "servicios", "search"], ["S03", "servicios", "nomatch"],
  ["S04", "servicios", "existing"], ["S05", "servicios", "agenda"], ["S06", "servicios", "invalid"],
  ["S07", "servicios", "create"], ["S08", "servicios", "hold"],
  ["C01", "centros", "list"], ["C02", "centros", "existing"], ["C03", "centros", "edit"],
  ["C04", "centros", "invalid"], ["C05", "centros", "create"], ["C06", "centros", "disable"],
  ["C07", "centros", "hold"], ["C08", "centros", "reject"],
  ["D01", "medicos", "list"], ["D02", "medicos", "filter"], ["D03", "medicos", "tabs"],
  ["D04", "medicos", "reload"], ["D05", "medicos", "invalid"], ["D06", "medicos", "create"],
  ["D07", "medicos", "calendar-reject"], ["D08", "medicos", "hold"],
];
const PATH = { servicios: "services", centros: "centers", medicos: "doctors" };
const SECTION = { servicios: "servicios", centros: "centros", medicos: "medicos" };
const EXISTING = { servicios: "medicina-general-riverside-clinic", centros: "riverside-clinic", medicos: "gonzalez-juan" };

test.beforeAll(() => {
  evidence = allocateEvidenceRunDir({ root: join(homedir(), ".claude/artifacts/human-rounds/catalog-native-lifecycle"), label: "run" });
});
test.afterAll(async () => {
  await writeFile(join(evidence, "report.json"), JSON.stringify({
    expectedIDs: STORIES.map(([id]) => id), widths: WIDTHS, records, validationRecovery,
    complete: complete(records),
    boundary: "real local ASGI, synthetic example-town database, served template and fingerprinted native resources",
    screenshotReview: "pending root-owned visual review",
  }, null, 2));
  expect(complete(records), "all 24 stories require complete distinct evidence").toBe(true);
});

function complete(rows) {
  return catalogEvidenceComplete(rows, STORIES, WIDTHS, sourceHash);
}
async function pageFor(env, width, record) {
  const context = await env.newContext({ viewport: { width, height: 900 } });
  await context.addInitScript(() => {
    if (!PerformanceObserver.supportedEntryTypes.includes("layout-shift")) {
      throw new Error("layout-shift PerformanceObserver entry type is unsupported");
    }
    window.__catalogShiftObserverReady = false; window.__catalogShifts = [];
    const copy = (entry) => ({ value: entry.value, startTime: entry.startTime, hadRecentInput: entry.hadRecentInput });
    window.__catalogShiftObserver = new PerformanceObserver((list) => window.__catalogShifts.push(...list.getEntries().map(copy)));
    window.__catalogShiftObserver.observe({ type: "layout-shift", buffered: true });
    window.__catalogShiftObserverReady = true;
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => record.errors.push({ width, type: "pageerror", text: String(error) }));
  page.on("console", (message) => { if (message.type() === "error") record.errors.push({ width, type: "console", text: message.text(), url: message.location().url }); });
  await loginAdmin(page, env.app.baseUrl);
  // Close the login landing document before installing catalog fault routes.
  // Its in-flight reads are not the dependency owned by the next journey.
  await page.goto("about:blank");
  return { page, context };
}
async function open(env, page, kind, item = "") {
  const url = `${env.app.baseUrl}/admin/${PATH[kind]}${item ? `?item=${encodeURIComponent(item)}` : ""}`;
  await page.goto(url, { waitUntil: "load", timeout: 20000 });
  await expect(page.locator(`#sec-${SECTION[kind]}`)).toBeVisible({ timeout: 12000 });
  if (!item) await expect(page.locator(`#sec-${SECTION[kind]} table.cnt-table`)).toBeVisible({ timeout: 12000 });
  else await expect(page.locator("#cnt-form")).toBeVisible({ timeout: 12000 });
}
function allowObservedFailure(record, width, response, status) {
  expect(response.status()).toBe(status);
  record.expectedErrors.push({ width, url: response.url(), status });
}
function clearExpectedErrors(record) {
  record.observedExpectedErrors = record.errors.filter((error) => record.expectedErrors.some((expected) =>
    error.type === "console" && error.width === expected.width && error.url === expected.url
      && error.text.startsWith("Failed to load resource:") && error.text.includes(`status of ${expected.status} `)));
  record.errors = record.errors.filter((error) => !record.observedExpectedErrors.includes(error));
}
async function verifyRetry(page, selector) {
  const button = page.locator(selector);
  await expect(button).toBeVisible();
  expect(await button.innerText(), "retry uses the loaded Spanish catalog").toBe("Recargar");
  const box = await stableBox(button, "retry button");
  expect(box.height >= 44 && box.width >= 44, "retry has a usable target").toBe(true);
  // Panel buttons share ONE size (--adm-btn-font, 14px since 8af2e67c /
  // 110fbc13, 2026-09-15); the retry must use it, never a local override.
  const size = await button.evaluate((el) => ({
    own: parseFloat(getComputedStyle(el).fontSize),
    shared: parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--adm-btn-font")),
  }));
  expect(size.shared, "shared panel button size token").toBe(14);
  expect(size.own, "retry uses the shared panel button size").toBe(size.shared);
}

async function capture(env, page, record, width, phase, validate = true) {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const base = join(evidence, `${record.id}-${width}-${phase}`); const png = `${base}.png`, html = `${base}.html`, json = `${base}.json`;
  const viewportPng = `${base}-viewport.png`;
  await saveShot(env.testInfo, page, viewportPng, { fullPage: false, attach: false });
  await saveShot(env.testInfo, page, png, { fullPage: true, attach: false });
  await writeFile(html, await page.content());
  const geometry = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const visible = (element) => { const b = element.getBoundingClientRect(), s = getComputedStyle(element); return b.width > 0 && b.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
    const escaped = [...document.querySelectorAll("button,input,select,textarea,a")].filter(visible).map((element) => {
      const b = element.getBoundingClientRect(); return { tag: element.tagName, id: element.id, left: b.left, right: b.right, top: b.top, bottom: b.bottom };
    }).filter((b) => b.left < -1 || b.right > viewport + 1);
    const pending = window.__catalogShiftObserver.takeRecords().map((entry) => ({ value: entry.value, startTime: entry.startTime, hadRecentInput: entry.hadRecentInput }));
    window.__catalogShifts.push(...pending);
    return { viewport, overflow: document.documentElement.scrollWidth - viewport, escaped,
      observerSupported: PerformanceObserver.supportedEntryTypes.includes("layout-shift"),
      observerReady: window.__catalogShiftObserverReady === true, shifts: [...window.__catalogShifts],
      resources: performance.getEntriesByType("resource").map((entry) => entry.name) };
  });
  const urls = geometry.resources.filter((url) => /\/platform\/admin-content\.[a-f0-9]+\.mjs(?:\?|$)/.test(url));
  let identity = { urls, sourceHash: null };
  if (urls.length === 1) {
    const response = await page.request.get(urls[0]);
    identity = { urls, status: response.status(), sourceHash: createHash("sha256").update(await response.body()).digest("hex") };
  }
  geometry.cls = cumulativeLayoutShift(geometry.shifts); await writeFile(json, JSON.stringify({ phase, geometry, identity, expected: record.action }, null, 2));
  const assets = await Promise.all([png, html, json].map(async (path) => ({ path, bytes: (await stat(path)).size })));
  (record.views[width] ||= {})[phase] = { phase, assets, geometry, identity,
    viewportAsset: { path: viewportPng, bytes: (await stat(viewportPng)).size } };
  if (!validate) return;
  expect(urls.length, "exactly one fingerprinted native catalog module was loaded").toBe(1);
  expect(identity.sourceHash, "served module bytes match this candidate").toBe(sourceHash);
  expect(geometry.observerSupported, `${record.id}: layout-shift observer entry type is supported`).toBe(true);
  expect(geometry.observerReady, `${record.id}: layout-shift observer initialized before navigation`).toBe(true);
  expect(geometry.overflow, `${record.id}: no document overflow at ${width}`).toBe(0);
  expect(geometry.escaped, `${record.id}: visible controls fit at ${width}`).toEqual([]);
  expect(geometry.cls, `${record.id}: observed CLS ${geometry.cls}`).toBeLessThanOrEqual(0.1);
}
async function submitAndReturnList(page, kind) {
  await page.locator('button[type="submit"]').click();
  await page.waitForURL((url) => url.pathname === `/admin/${PATH[kind]}` && !url.search, { timeout: 12000 });
  await expect(page.locator(`#sec-${SECTION[kind]} table.cnt-table`)).toBeVisible();
}
// Médico = usuario (2026-10-19): «Nuevo médico» stays on the created doctor
// and says at the top what happened to its account; it does not return to
// the list.
async function submitAndStayOnCreatedDoctor(page) {
  await page.locator('button[type="submit"]').click();
  await page.waitForURL((url) => url.pathname === "/admin/doctors"
    && !!url.searchParams.get("item")
    && url.searchParams.get("item") !== "new", { timeout: 12000 });
  const outcome = page.locator("#med-account-outcome .doctor-user-outcome");
  await expect(outcome).toBeVisible({ timeout: 12000 });
  expect(await outcome.innerText()).toMatch(/Se creó su cuenta \S+, todavía sin invitar\./);
  await expect(page.locator("#med-account-invite")).toBeVisible();
}
async function create(env, page, kind, id) {
  await open(env, page, kind, "new");
  if (kind === "servicios") {
    await page.fill("#svc-nombre", `Lifecycle service ${id}`); await page.selectOption("#svc-centro", { index: 1 }); await page.fill("#svc-horario", "Lunes a viernes de 9 a 12 h");
  } else if (kind === "centros") {
    await page.fill("#cen-nombre", `Lifecycle center ${id}`); await page.fill("#cen-direccion", "Calle Sintética 1");
  } else {
    await page.locator('[data-tab="info"]').click(); await page.fill("#med-nombre-pila", "Lifecycle"); await page.fill("#med-apellido", `Doctor ${id}`); await page.selectOption("#med-especialidad", { index: 1 });
    return submitAndStayOnCreatedDoctor(page);
  }
  await submitAndReturnList(page, kind);
}
async function exercise(env, page, id, kind, action, record, width) {
  const api = `/api/admin/content/${PATH[kind]}`;
  const snapshot = (phase) => capture(env, page, record, width, phase);
  async function persisted() {
    const response = await page.request.get(env.app.baseUrl + api);
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(Array.isArray(body.items)).toBe(true);
    return body.items;
  }
  if (action === "list") return open(env, page, kind);
  if (action === "search") {
    await open(env, page, kind);
    // Catalog preferences persist server-side across fresh browser contexts.
    await page.locator(".cnt-search").fill("");
    const other = page.locator('a[href="/admin/services?item=demanda-espontanea-riverside-clinic"]');
    await expect(other).toBeVisible();
    await page.locator(".cnt-search").fill("Medicina general");
    await expect(page.locator('a[href="/admin/services?item=medicina-general-riverside-clinic"]')).toBeVisible();
    await expect(other).toBeHidden();
    await expect(page.locator("#cnt-tbody-servicios tr")).toHaveCount(3);
    return;
  }
  if (action === "nomatch") {
    await open(env, page, kind); const search = page.locator(".cnt-search");
    await search.fill("zzzz-no-match");
    await expect(page.locator("[data-clear-catalog]")).toBeVisible();
    expect(await page.locator("#cnt-tbody-servicios tr.cnt-tr").count()).toBe(0);
    // Progressive disclosure: the no-match state is its own block next to the (hidden,
    // emptied) table, with the message and «Limpiar filtros» together.
    await expect(page.locator("#cnt-nomatch-servicios")).toContainText(/Ninguno coincide/);
    await expect(page.locator("#cnt-nomatch-servicios [data-clear-catalog]")).toBeVisible();
    await snapshot("no-match");
    await page.locator("[data-clear-catalog]").click();
    await expect(page.locator("#cnt-tbody-servicios tr.cnt-tr").first()).toBeVisible();
    expect(await search.inputValue()).toBe("");
    return;
  }
  if (action === "existing") {
    await open(env, page, kind, kind === "servicios" ? "demanda-espontanea-riverside-clinic" : EXISTING[kind]);
    if (kind === "servicios") {
      expect(await page.locator("#svc-c-turno").isChecked()).toBe(false);
      expect(await page.locator("#svc-agenda-wrap").isVisible()).toBe(false);
      const approval = page.locator("#svc-c-aprobacion");
      const sameDay = page.locator("#svc-c-mismodia");
      const approvalRow = page.locator(".cnt-modo-op").filter({ has: approval });
      const sameDayRow = page.locator(".cnt-modo-op").filter({ has: sameDay });
      expect(await approvalRow.isVisible(), "unavailable approval label and hint must be absent").toBe(false);
      await expect(approval).toBeDisabled();
      await expect(sameDayRow).toBeVisible();
      await sameDay.check();
      await expect(page.locator("#svc-agenda-wrap")).toBeVisible();
      await page.locator("#svc-c-turno").check();
      await expect(approvalRow).toBeVisible();
      expect(await sameDayRow.isVisible(), "scheduled services have no same-day opening property").toBe(false);
      await expect(sameDay).toBeDisabled();
      await approval.uncheck();
      await page.locator("#svc-c-turno").uncheck();
      expect(await approvalRow.isVisible()).toBe(false);
      await expect(sameDayRow).toBeVisible();
      await expect(sameDay, "mode changes preserve the prior setting").toBeChecked();
      await expect(page.locator("#svc-agenda-wrap"), "returning to same-day walk-in restores its agenda immediately").toBeVisible();
      await page.locator("#svc-c-turno").check();
      expect(await approval.isChecked(), "approval choice survives a mode round trip").toBe(false);
      await page.locator("#svc-c-turno").uncheck();
      await sameDay.uncheck();
      expect(await page.locator("#svc-agenda-wrap").isVisible()).toBe(false);
    } else {
      const item = (await persisted()).find((row) => row.slug === EXISTING[kind]);
      expect(item).toBeTruthy();
      await expect(page.locator("#cen-direccion")).toHaveValue(item.address || "");
    }
    return;
  }
  if (action === "agenda") { await open(env, page, kind, EXISTING[kind]); await expect(page.locator(".cnt-cupos")).toBeVisible(); await page.reload({ waitUntil: "load" }); await expect(page.locator(".cnt-cupos")).toBeVisible(); return; }
  if (action === "invalid") {
    await open(env, page, kind, "new");
    const before = await persisted();
    // Médico = usuario (2026-10-19): a doctor with every field empty is a
    // valid create now. What is still invalid is an email the browser lets
    // through (no dot in the domain) but the server rejects.
    if (kind === "medicos") {
      await page.locator('[data-tab="info"]').click();
      await page.fill("#med-email", "medico@hospital");
    }
    const rejected = page.waitForResponse((response) => new URL(response.url()).pathname === api && response.request().method() === "POST");
    await page.locator('button[type="submit"]').click();
    const response = await rejected, body = await response.json();
    expect(response.status()).toBe(400); expect(body.error).toBeTruthy();
    const field = kind === "servicios" ? "#svc-nombre" : kind === "centros" ? "#cen-nombre" : "#med-email";
    const expectedMessage = kind === "medicos" ? "Ingresá un email válido o dejalo vacío." : "Escribí el nombre.";
    await expect(page.locator(`${field}-msg`)).toBeVisible();
    await expect(page.locator(field)).toHaveAttribute("aria-invalid", "true");
    expect(await page.locator(`${field}-msg`).innerText()).toBe(expectedMessage);
    await expect(page.locator(field)).toBeFocused();
    await snapshot("invalid-field");
    await expect(page.locator("#cnt-form")).toBeVisible();
    expect(await page.locator('button[type="submit"]').isEnabled()).toBe(true);
    expect(await persisted(), "invalid save did not mutate catalog").toEqual(before);
    await page.locator(field).fill(kind === "medicos" ? "recovered@hospital.example" : "Recovered validation draft");
    await expect(page.locator(`${field}-msg`)).toHaveCount(0);
    expect(await page.locator(field).getAttribute("aria-invalid")).toBeNull();
    allowObservedFailure(record, width, response, 400);
    return;
  }
  if (action === "create") {
    await create(env, page, kind, id);
    // A doctor stays on its record after the create; go to the list.
    if (kind === "medicos") await open(env, page, kind);
    else await page.reload({ waitUntil: "load" });
    // The catalog search persists server-side across contexts (see 'search'),
    // so an earlier story's query may still narrow the list: clear it.
    // Centers have no search box; the other two catalogs do.
    await expect(page.locator(`#sec-${SECTION[kind]} table.cnt-table`)).toBeVisible();
    if (kind !== "centros") await page.locator(".cnt-search").fill("");
    const created = page.getByText(new RegExp(`Lifecycle (service|center|Doctor) ${id}`, "i")).first();
    await expect(created).toBeVisible(); await created.scrollIntoViewIfNeeded();
    expect((await persisted()).filter((row) => String(row.nombre || row.name).includes(id)).length).toBe(1);
    return;
  }
  if (action === "edit") {
    // An existing item stays on its form and confirms in place (slice 3,
    // 4db1be73); only a CREATE returns to the list.
    await open(env, page, kind, EXISTING[kind]); await page.fill("#cen-direccion", `Changed ${id}`);
    await page.locator('button[type="submit"]').click();
    await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible({ timeout: 12000 });
    expect(new URL(page.url()).searchParams.get("item")).toBe(EXISTING[kind]);
    await page.reload({ waitUntil: "load" });
    await expect(page.locator("#cen-direccion")).toHaveValue(`Changed ${id}`);
    expect((await persisted()).find((row) => row.slug === EXISTING[kind]).address).toBe(`Changed ${id}`);
    return;
  }
  if (action === "disable") {
    await create(env, page, kind, id); page.once("dialog", (dialog) => dialog.accept());
    const link = page.locator('a[href*="item="]').filter({ hasText: `Lifecycle center ${id}` });
    const targetId = new URL(await link.getAttribute("href"), env.app.baseUrl).searchParams.get("item");
    await link.click(); await page.locator("#cnt-del").click();
    await page.waitForURL((url) => url.pathname === "/admin/centers" && !url.search);
    await page.reload({ waitUntil: "load" });
    await expect(page.locator("#cnt-tbody-centros")).toBeVisible();
    expect(await page.locator('a[href*="item="]').filter({ hasText: `Lifecycle center ${id}` }).count()).toBe(0);
    const row = (await persisted()).find((item) => item.slug === targetId);
    expect(!row || row._borrado, "disabled center is absent or explicitly tombstoned").toBeTruthy();
    return;
  }
  if (action === "filter") {
    await open(env, page, kind);
    await page.locator(".cnt-search").fill("");
    // <=640px folds the selects behind the row's one filter button
    // (110fbc13, 2026-09-15); wider screens keep them inline with no button.
    const toggle = page.locator(`#sec-${SECTION[kind]} .adm-filter-toggle`);
    if (width <= 640) {
      expect(await page.locator("#med-f-centro").isVisible(), "phone filters start folded").toBe(false);
      await toggle.click();
    } else {
      expect(await toggle.isVisible(), "no filter button beside inline filters").toBe(false);
    }
    await expect(page.locator("#med-f-centro")).toBeVisible();
    // A filter is offered only when the data holds two values for it (admin-filter-controls.mjs):
    // the example town's doctors may all share one agenda state, so clear what is shown.
    for (const selector of ["#med-f-centro", "#med-f-agenda", "#med-f-especialidad"]) {
      if (await page.locator(selector).count()) await page.locator(selector).selectOption("");
    }
    const select = page.locator("#med-f-especialidad");
    const specialty = await select.locator("option").nth(1).getAttribute("value");
    const all = (await persisted()).filter((row) => !row._borrado);
    const expected = all.filter((row) => row.especialidad === specialty).map((row) => row.id).sort();
    expect(expected.length > 0 && expected.length < all.length, "fixture distinguishes filtered doctors").toBe(true);
    await select.selectOption(specialty);
    const renderedIds = () => page.locator('#cnt-tbody-medicos a[href^="/admin/doctors?item="]').evaluateAll((links) =>
      links.map((link) => new URL(link.href).searchParams.get("item")).sort());
    await expect.poll(renderedIds, { message: "filter exactly matches independent catalog data" }).toEqual(expected);
    await snapshot("filtered");
    await select.selectOption("");
    await expect.poll(renderedIds).toEqual(all.map((row) => row.id).sort());
    return;
  }
  if (action === "tabs") {
    await open(env, page, kind, EXISTING[kind]);
    for (const tab of ["agenda", "calendario", "info"]) {
      await page.locator(`[data-tab="${tab}"]`).click();
      await expect(page.locator(`[data-tabpanel="${tab}"]`)).toBeVisible();
      if (tab === "calendario") await expect(page.locator('[data-tab="calendario"]')).toHaveAttribute("data-count", /.*/);
      if (tab === "info") expect(await page.locator("#med-nombre-pila").inputValue()).toBeTruthy();
      await snapshot(`tab-${tab}`);
    }
    return;
  }
  if (action === "reload") { await open(env, page, kind, EXISTING[kind]); await page.locator('[data-tab="calendario"]').click(); await page.reload({ waitUntil: "load" }); await expect(page.locator("#cnt-form")).toBeVisible(); return; }
  if (action === "calendar-reject") {
    const pattern = "**/api/admin/doctors/*/appointments?*"; let hits = 0;
    await page.route(pattern, (route) => { hits++; return route.fulfill({ status: 503, body: "dependency unavailable" }); });
    const rejected = page.waitForResponse((response) => response.url().includes("/appointments?days=30"));
    await open(env, page, kind, EXISTING[kind]); await page.locator('[data-tab="calendario"]').click();
    const response = await rejected;
    await expect(page.locator("#med-cal")).toContainText("No se pudo cargar el calendario");
    await verifyRetry(page, "[data-catalog-calendar-retry]");
    expect(hits).toBeGreaterThan(0); allowObservedFailure(record, width, response, 503); await snapshot("rejected");
    await page.unroute(pattern);
    const recovered = page.waitForResponse((r) => r.url().includes("/appointments?days=30") && r.status() === 200);
    await page.locator('[data-tab="info"]').click();
    await page.locator("#med-nombre-pila").fill("Unsaved retry draft");
    await page.locator('[data-tab="calendario"]').click();
    await page.locator("[data-catalog-calendar-retry]").click(); await recovered;
    await expect.poll(() => page.evaluate(() => {
      const tab = document.querySelector('[data-tab="calendario"]'), host = document.querySelector("#med-cal");
      return !!(tab?.hasAttribute("data-count") && host?.textContent.trim() && !host.textContent.includes("No se pudo cargar"));
    })).toBe(true);
    await page.locator('[data-tab="info"]').click();
    expect(await page.locator("#med-nombre-pila").inputValue()).toBe("Unsaved retry draft");
    await page.locator('[data-tab="calendario"]').click();
    return;
  }
  if (action === "hold" || action === "reject") {
    const pattern = `**/api/admin/content/${kind === "servicios" ? "services" : kind === "centros" ? "centers" : "doctors"}`;
    const gate = holdGate(env.cleanup);
    let hits = 0;
    await page.route(pattern, async (route) => { hits++; gate.arrive(); if (action === "reject") return route.fulfill({ status: 503, body: "dependency unavailable" }); await gate.held; await route.continue().catch(() => {}); });
    const responsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === api);
    try {
      await page.goto(`${env.app.baseUrl}/admin/${PATH[kind]}`, { waitUntil: "domcontentloaded" });
      await Promise.race([gate.arrived, responsePromise.then(() => { throw new Error("dependency response without interception"); })]);
      expect(hits).toBeGreaterThan(0);
      if (action === "hold") {
        expect(await page.locator(`#sec-${SECTION[kind]} table.cnt-table`).count()).toBe(0);
        await snapshot("held"); gate.release(); expect((await responsePromise).status()).toBe(200);
      } else {
        const response = await responsePromise;
        await verifyRetry(page, "[data-catalog-route-retry]");
        expect(await page.locator(`#sec-${SECTION[kind]} [role="alert"]`).innerText()).toMatch(/No se pudo cargar/);
        allowObservedFailure(record, width, response, 503); await snapshot("rejected");
        await page.unroute(pattern); await page.locator("[data-catalog-route-retry]").click();
      }
      await expect(page.locator(`#sec-${SECTION[kind]} table.cnt-table`)).toBeVisible();
    } finally { gate.release(); await page.unroute(pattern); }
    return;
  }
  throw new Error(`unimplemented action ${action}`);
}

for (const [id, kind, action] of STORIES) test(`${id} served ${kind} ${action}`, async ({ app, newContext, cleanup }, testInfo) => {
  test.setTimeout(180_000);
  const env = { app, newContext, cleanup, testInfo };
  const record = { id, kind, action, errors: [], expectedErrors: [], views: {}, observerInitialized: false, assertionComplete: false }; records.push(record);
  for (const width of WIDTHS) {
    const { page } = await pageFor(env, width, record);
    try {
      // Each viewport writes to the same synthetic database. Names must differ
      // so later viewports cannot hit duplicate-name validation or reuse a row.
      await exercise(env, page, `${id}-${width}`, kind, action, record, width);
      await capture(env, page, record, width, action);
    } catch (error) {
      record.failure = { width, action, message: String(error) };
      // Preserve the actual failed state before test teardown closes the page.
      // A diagnostic capture never changes the original failing verdict.
      try { await capture(env, page, record, width, `${action}-failed`, false); }
      catch (captureError) { record.failure.captureError = String(captureError); }
      throw error;
    }
  }
  record.observerInitialized = WIDTHS.every((width) => requiredPhases(action).every((phase) => record.views[width][phase].geometry.observerReady));
  clearExpectedErrors(record);
  expect(record.errors).toEqual([]);
  record.assertionComplete = true;
});

test("doctor specialty recovery reveals its hidden tab and permits one corrected save", async ({ app, newContext, cleanup }, testInfo) => {
  const env = { app, newContext, cleanup, testInfo };
  const record = { id: "doctor-hidden-tab-validation", action: "validation-recovery", views: {}, errors: [], expectedErrors: [], assertionComplete: false };
  validationRecovery.push(record);
  const { page } = await pageFor(env, 390, record);
  const path = `/api/admin/content/doctors/${EXISTING.medicos}`;
  await open(env, page, "medicos", EXISTING.medicos);
  const specialty = page.locator("#med-especialidad");
  const note = page.locator("#med-nota");
  const originalSpecialty = await specialty.inputValue();
  const originalNote = await note.inputValue();
  expect(originalSpecialty, "fixture doctor has a specialty to restore after recovery").toBeTruthy();
  await page.locator('[data-tab="info"]').click();
  await specialty.evaluate((control) => control.setAttribute("aria-describedby", "preexisting-description"));
  await specialty.selectOption("");
  await note.fill("Unsaved hidden-tab validation draft");
  await page.locator('[data-tab="agenda"]').click();

  const writes = [];
  page.on("response", (response) => {
    if (new URL(response.url()).pathname === path && response.request().method() === "PUT") writes.push(response.status());
  });
  const rejected = page.waitForResponse((response) => new URL(response.url()).pathname === path && response.request().method() === "PUT");
  await page.locator('button[type="submit"]').click();
  const rejectedResponse = await rejected;
  allowObservedFailure(record, 390, rejectedResponse, 400);
  await expect(page.locator('[data-tabpanel="info"]')).toBeVisible();
  await expect(page.locator("#med-especialidad-msg")).toBeVisible();
  expect(await page.locator("#med-especialidad-msg").innerText()).toBe("Elegí la especialidad.");
  expect(await specialty.getAttribute("aria-invalid")).toBe("true");
  expect(await specialty.getAttribute("aria-describedby")).toBe("preexisting-description med-especialidad-msg");
  await expect(page.locator("#med-especialidad")).toBeFocused();
  expect(await note.inputValue()).toBe("Unsaved hidden-tab validation draft");
  await capture(env, page, record, 390, "invalid-field");

  await specialty.selectOption(originalSpecialty);
  await expect(page.locator("#med-especialidad-msg")).toHaveCount(0);
  expect(await specialty.getAttribute("aria-invalid")).toBeNull();
  expect(await specialty.getAttribute("aria-describedby")).toBe("preexisting-description");
  await note.fill(originalNote);
  await capture(env, page, record, 390, "corrected-field");
  const saved = page.waitForResponse((response) => new URL(response.url()).pathname === path && response.request().method() === "PUT" && response.status() === 200);
  await page.locator('button[type="submit"]').click();
  await saved;
  await expect(page.locator("#cnt-save-status")).toBeVisible();
  expect(await page.locator("#med-especialidad").inputValue()).toBe(originalSpecialty);
  expect(writes, "one rejected save is followed by exactly one valid save").toEqual([400, 200]);
  clearExpectedErrors(record);
  expect(record.errors).toEqual([]);
  record.assertionComplete = true;
});
