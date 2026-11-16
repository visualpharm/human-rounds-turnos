// Real-browser lifecycle acceptance for the native general-staff patient
// roster. It uses the local ASGI harness and a throwaway database; API routes
// are intercepted only where a deterministic stale-owner contract needs a
// held response. Synthetic pagehide/pageshow checks template ownership, not
// browser BFCache admission.
//
// Port of tests/e2e/admin-patients-lifecycle.e2e.mjs (24 tests, same titles, same order).
//
// Differences from the node:test original:
// - One serial describe: the ten cold-load captures and the four email writes are
//   collected in module arrays that the last test (and the report.json written in
//   `afterAll`) read, and the first step (enable both real locale catalogs) is a
//   `beforeAll`. A failure skips the rest; a retry re-runs the file on a fresh worker.
// - The evidence directory is reserved in `beforeAll` (not at import time, which would
//   also run in the runner's listing process) under artifactPath("human-rounds",
//   "patients-native-lifecycle") - the same <artifacts>/human-rounds tree as before. Every
//   screenshot is taken with saveShot(): same file names there, plus attached to the report.
// - The page is the runner's `page` (viewport 1280x900 through test.use; the cold-load
//   cases set their own width) and the sign-in is the shared loginAdmin(); the original's
//   hand-made context per case is not needed. The layout-shift observer, the page-error
//   collector, the preference reset (cold loads must show the default roster), the about:blank
//   landing and the `lang` cookie are unchanged. `page.setDefaultTimeout(10000)` is gone: the
//   config's action/expect timeouts apply (15 s expect), explicit timeouts are kept.
// - The 45 s timeout of the cold-load cases is `test.setTimeout(45000)`. The `try/finally`
//   around `context.close()` is gone with the context; held routes are released by the
//   `cleanup` fixture (holdGate), so a failing test never leaves a route hanging.
// - `bounded(promise)` (race against a 10 s timer) became `held.started()`: a retrying
//   poll with the same 10 s bound and message, on a flag the route handler sets.
// - «only one held mutation starts» (single-flight create) is an invariant over a window,
//   not one look right after the second click: it is checked with holdsFor() for 300 ms,
//   which is stricter than the original's single instant read.
// - Checks that pin a moment stay point-in-time snapshots (`expect(await ...count())`,
//   the values read after `settled()`, geometry measured in one evaluate); waits for
//   things that must become true are web-first (`toBeVisible`, `toHaveText`, `toBeDisabled`).
// - Locators stay CSS where markup is the subject: ids/data-* of the roster, form and
//   coverage controls (#pac-q, #pat-*, #pac-*, [data-cob-*], [data-retry]) and all of the
//   geometry/computed-style probes.
import {
  test, expect, loginAdmin, holdGate, holdsFor, saveShot, artifactPath,
} from "./lib/fixtures.mjs";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_admin_patients_native_lifecycle" },
  viewport: { width: 1280, height: 900 },
});
test.describe.configure({ mode: "serial" });

let evidence;
const captures = [];
const emailWrites = [];

const A = { dni: "30111222", apellido: "Varela", nombre: "Alejo", nacimiento: "1978-10-28", sexo: "M", contacto: "2254445566", email: "alejo@example.test", cobertura_nombre: "PAMI", servicio: "Cardiología", activo: true };
const B = { dni: "28777666", apellido: "López", nombre: "María", nacimiento: "1985-03-12", sexo: "F", contacto: "2254555444", email: "", cobertura_nombre: "OSDE", servicio: "Kinesiología", activo: true };
const detail = (item, dependientes = []) => ({ item: { ...item, cobertura: { obras_sociales: [{ nombre: "PAMI", origen: "puco", afiliado: "123" }], prepaga: {}, principal: "PAMI" } }, cobertura_principal: { clave: "PAMI" }, ranking: { puntaje: 62, etiqueta: "Suele venir", nivel: "medio", hechos: 3, motivos: ["Asistió a 2 de los últimos 3 turnos"] }, dependientes, a_cargo_de: [] });
const list = (items = [A, B]) => ({ items, total: items.length, offset: 0, limit: 200, modo: "recientes" });

test.beforeAll(async ({ browser, app }) => {
  evidence = allocateEvidenceRunDir({
    root: artifactPath("human-rounds", "patients-native-lifecycle"),
    label: "run",
  });
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await loginAdmin(page, app.baseUrl);
    const response = await page.request.post("/api/admin/settings", { data: {
      grupo: "languages", valores: { LANGUAGES: "es,en", LANG_DEFAULT: "es", LANG_SWITCHER_MODE: "secret" },
    } });
    expect(response.status(), "enable both real locale catalogs").toBe(200);
  } finally { await context.close(); }
});

test.afterAll(async () => {
  const ids = captures.map((row) => row.id);
  await writeFile(join(evidence, "report.json"), JSON.stringify({
    expected: 10, recorded: captures.length, unique: new Set(ids).size,
    captureSetComplete: captures.length === 10 && new Set(ids).size === 10 && captures.every((row) =>
      row.moduleLoaded && row.screenshotBytes > 0 && Number.isFinite(row.cls)
      && row.observerInstalled && row.overflow === 0 && row.outside.length === 0),
    captureAssertionsPassed: captures.length === 10 && new Set(ids).size === 10
      && captures.every((row) => row.assertionsPassed === true),
    captures,
    emailWrites,
    emailWritesComplete: emailWrites.length === 4 && new Set(emailWrites.map((row) => row.name)).size === 4
      && emailWrites.every((row) => row.actual === row.expected && row.screenshotBytes > 0),
    boundary: "Local ASGI + synthetic database only. Held API responses and synthetic pagehide/pageshow prove ownership; they are not production or BFCache evidence.",
  }, null, 2));
});

/** Sign in, reset the server-backed table preferences and park the page on about:blank. */
async function session(page, app, lang = "es") {
  await page.addInitScript(() => {
    window.__patientsShifts = [];
    window.__patientsObserverInstalled = false;
    try {
      const observer = new PerformanceObserver((entries) => {
        for (const entry of entries.getEntries()) if (!entry.hadRecentInput)
          window.__patientsShifts.push({ value: entry.value, startTime: entry.startTime });
      });
      observer.observe({ type: "layout-shift", buffered: true });
      window.__patientsObserver = observer;
      window.__patientsObserverInstalled = PerformanceObserver.supportedEntryTypes.includes("layout-shift");
    } catch { /* unsupported browsers are recorded honestly below */ }
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await loginAdmin(page, app.baseUrl);
  // Preferences are real and server-backed, so a new browser context alone
  // does not isolate search/filter state left by the preceding case. Column
  // choice and manual column widths are reset the same way: cold-loads must
  // show the DEFAULT roster (7 columns, measured widths), not whatever a
  // previous case dragged or toggled.
  for (const [key, value] of [["patients.search", ""], ["patients.filters", {}], ["patients.sort", { key: "nombre", dir: 1 }],
    ["table.columns.patients", ["nombre", "dni", "contacto", "servicio", "medico", "ranking", "turnos", "estado"]],
    ["table.widths.st_adm_anchos-pacientes", {}]]) {
    const response = await page.request.put("/api/admin/preferences", { data: { key, value } });
    expect(response.status()).toBe(200);
  }
  await page.goto("about:blank");
  await page.context().addCookies([{ name: "lang", value: lang, url: app.baseUrl }]);
  return { errors };
}

async function seedRoster(page, handler = (route) => route.fulfill({ json: list() })) {
  await page.route("**/api/admin/patients?**", handler);
  await page.route("**/api/admin/patients", handler);
}
async function readyList(page) {
  await expect(page.locator("#pac-q")).toBeVisible({ timeout: 12000 });
  await expect(page.locator("#sec-pacientes")).toBeVisible();
}
async function readyDetail(page, dni) {
  const fixed = page.locator(".pat-head");
  await expect(fixed).toBeVisible({ timeout: 12000 });
  await expect(fixed).toHaveText(new RegExp(dni.slice(-3)));
}
const frames = (page) => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
/**
 * A route that holds its response until released. `begin()` is called by the route
 * handler once it has the request; `started()` waits (at most 10 s) for that.
 */
function heldRoute(cleanup) {
  const gate = holdGate(cleanup);
  let seen = false;
  return {
    held: gate.held,
    release: gate.release,
    begin() { seen = true; gate.arrive(); },
    started: () => expect.poll(() => seen, { message: "Expected request did not start within 10 seconds", timeout: 10000 }).toBe(true),
  };
}
// Explicit transport seam for stale-continuation negatives: still use the
// browser's real fetch and real controller/session, but make synthetic patient
// reads uncancellable. Ordinary AbortController success is not stale-owner proof.
async function ignoreReadAbort(page) {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.__patientReads = [];
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input.url;
      const read = (!init?.method || init.method === "GET") && /\/api\/admin\/(patients|whoami)/.test(url);
      const response = await original(input, read ? { ...init, signal: undefined } : init);
      if (read) window.__patientReads.push(url);
      return response;
    };
  });
}
async function settled(page, suffix) {
  await page.waitForFunction((s) => window.__patientReads?.some((url) => url.includes(s)), suffix);
  await frames(page);
}
async function navigateDetail(page, dni) {
  await page.evaluate((id) => { history.pushState({}, "", "/admin/pacientes?patient=" + id); dispatchEvent(new PopStateEvent("popstate")); }, dni);
}

// Given an authenticated operator and the real native template; Action: cold
// load ES and EN at the five practical widths; Expected: visible list controls,
// loaded native module, finite observed CLS, no horizontal overflow and ten
// durable, unique captures.
for (const lang of ["es", "en"]) for (const width of [360, 390, 768, 1024, 1280]) {
  test(`patients ${lang}: cold-load ${width}px`, async ({ page, app }, testInfo) => {
    test.setTimeout(45000);
    const { errors } = await session(page, app, lang);
    await seedRoster(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/pacientes", { waitUntil: "load" });
    await readyList(page);
    await page.evaluate(() => document.fonts.ready);
    await frames(page);
    const id = `${lang}-${width}`;
    const shot = await saveShot(testInfo, page, join(evidence, `patients-${id}.png`), { fullPage: true });
    const geometry = await page.evaluate(() => {
      const host = document.getElementById("sec-pacientes");
      const q = document.getElementById("pac-q");
      for (const entry of window.__patientsObserver?.takeRecords() || [])
        if (!entry.hadRecentInput) window.__patientsShifts.push({ value: entry.value, startTime: entry.startTime });
      const shifts = window.__patientsShifts;
      let cls = 0, total = 0, first = 0, last = 0;
      for (const entry of shifts) {
        if (entry.startTime - last >= 1000 || entry.startTime - first >= 5000) {
          total = 0; first = entry.startTime;
        }
        total += entry.value; last = entry.startTime; cls = Math.max(cls, total);
      }
      const viewport = document.documentElement.clientWidth;
      const outside = [...host.querySelectorAll("*")].filter((el) => {
        const rect = el.getBoundingClientRect();
        return rect.width && rect.height && getComputedStyle(el).visibility !== "hidden"
          && (rect.left < -1 || rect.right > viewport + 1);
      }).map((el) => ({ tag: el.tagName, id: el.id, class: el.className }));
      const assets = performance.getEntriesByType("resource").map((row) => row.name);
      const collapsedInk = [...host.querySelectorAll("th,td")].filter((el) => {
        const rect = el.getBoundingClientRect();
        return rect.height > 0 && rect.width <= 1 && el.textContent.trim()
          && getComputedStyle(el).overflowX === "visible";
      }).map((el) => el.textContent);
      // Responsable del producto, 2026-11-03: ningún teléfono ni mail impreso; son iconos que abren el cliente.
      const printedContact = [...host.querySelectorAll('td[data-col="contacto"]')]
        .filter((cell) => cell.textContent.trim()).map((cell) => cell.textContent.trim());
      const rect = (el) => { const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right) }; };
      const titleRow = document.querySelector(".adm-h1row");
      const titleBox = rect(document.getElementById("adm-h1"));
      const newBtn = document.querySelector("a.pat-title-new");
      const titleRowGeometry = {
        row: rect(titleRow), title: titleBox, button: rect(newBtn),
        siblings: [...titleRow.children].map((el) => el.tagName + "." + el.className + "#" + el.id),
        marginLeft: getComputedStyle(newBtn).marginLeft,
      };
      return {
        titleRowGeometry,
        viewport, overflow: document.documentElement.scrollWidth - viewport, outside, collapsedInk, printedContact,
        contactIcons: [...host.querySelectorAll('td[data-col="contacto"] a.cnt-ico-link')]
          .filter((el) => el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0).length,
        whatsappIcons: [...host.querySelectorAll('td[data-col="contacto"] a.cnt-wa-link[href^="https://wa.me/"]')]
          .filter((el) => el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0).length,
        visibleServiceCount: [...host.querySelectorAll('td[data-col="servicio"]')]
          .filter((el) => el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0).length,
        coverageColumn: host.querySelectorAll('th[data-col="cobertura"], td[data-col="cobertura"]').length,
        columns: [...host.querySelectorAll("#pat-table th")].map((th) => th.dataset.col),
        tableWidth: Math.round((document.querySelector("#pat-table table") || {}).getBoundingClientRect?.().width || 0),
        wrapWidth: document.getElementById("pat-table") ? document.getElementById("pat-table").clientWidth : 0,
        hintLeft: document.getElementById("pat-modo-hint") ? Math.round(document.getElementById("pat-modo-hint").getBoundingClientRect().left) : null,
        hostOverflow: host.scrollWidth - host.clientWidth,
        moduleLoaded: !!q && assets.some((url) => /\/static\/platform\/admin-patients\.[a-f0-9]+\.mjs/.test(url)), assets,
        locale: document.documentElement.lang,
        readable: q && parseFloat(getComputedStyle(q).fontSize) >= 16,
        observerInstalled: window.__patientsObserverInstalled === true,
        cls, shifts, observedUntilMs: performance.now(),
      };
    });
    const capture = { id, lang, width, screenshot: shot.path, screenshotBytes: shot.bytes, ...geometry, assertionsPassed: false };
    captures.push(capture);
    expect(geometry.moduleLoaded, JSON.stringify(geometry)).toBe(true);
    // Responsable del producto, 2026-11-02: «Patients, New patient en una línea, a la derecha» — at
    // every width the button shares the title's row and ends at the row's right edge.
    const tr = geometry.titleRowGeometry;
    expect(tr.button.top, "the button is on the title's row: " + JSON.stringify(tr)).toBeLessThan(tr.title.bottom);
    expect(tr.row.right - tr.button.right, "the button sits at the right edge of the title row: " + JSON.stringify(tr)).toBeLessThanOrEqual(24);
    expect(geometry.readable, JSON.stringify(geometry)).toBe(true);
    expect(geometry.locale).toBe(lang);
    await expect(page.locator("#pac-f-edad"), "no age filter (responsable del producto, 2026-11-03)").toHaveCount(0);
    await expect(page.locator('#pac-f-banda option[value="viene-siempre"]')).toHaveText(lang === "en" ? "Always shows up" : "Viene siempre");
    if (width < 640) {
      await expect(page.locator('#pat-table td[data-col="nombre"]').first()).toHaveCSS("text-align", "left");
    }
    expect(geometry.overflow, JSON.stringify(geometry)).toBe(0);
    expect(geometry.outside, JSON.stringify(geometry)).toEqual([]);
    expect(geometry.collapsedInk, "dropped column headers cannot paint through a zero-width cell").toEqual([]);
    expect(geometry.printedContact, "phone and email are icons, never printed text").toEqual([]);
    if (width === 1280) {
      expect(geometry.contactIcons, "one phone icon per patient plus the email icon of the one who has it").toBe(3);
      expect(geometry.whatsappIcons, "a WhatsApp icon beside each phone icon (the product owner, 2026-11-04)").toBe(2);
      expect(geometry.visibleServiceCount, "the main-service column is in the default desktop list").toBe(2);
      expect(geometry.coverageColumn, "Cobertura left the list; it lives in «Editar datos»").toBe(0);
      // Every panel table is fitted at the end of the task that redraws it (2026-11-10): a fit
      // left to the next animation frame let a redraw landing late in a frame be painted once
      // at the table's natural width and jump when the fit arrived (the queue's CLS in Q02).
      // Two keyboard sorts of the current column redraw the list twice and leave the default
      // order; the read waits one microtask — no rendering step can come between — so no
      // frame timing decides it.
      const layout = await page.evaluate(async () => {
        // each sort replaces the table, so the header is looked up again for the second one
        for (let i = 0; i < 2; i++) document.querySelector('#pat-table th[data-sort="nombre"]')
          .dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        const sorted = document.querySelector('#pat-table th[data-sort="nombre"]').getAttribute("aria-sort");
        const redrawn = document.querySelector("#pat-table table");
        const before = redrawn.style.tableLayout;
        await Promise.resolve();
        return { sorted, fresh: redrawn !== null && before === "", after: redrawn.style.tableLayout };
      });
      expect(layout, "a redrawn patients table has its column widths before it is painted")
        .toEqual({ sorted: "ascending", fresh: true, after: "fixed" });
    }
    expect(geometry.observerInstalled, "supported Chromium must install a real layout-shift observer").toBe(true);
    expect(Number.isFinite(geometry.cls)).toBe(true);
    expect(shot.bytes).toBeGreaterThan(0);
    expect(errors).toEqual([]);
    capture.assertionsPassed = true;
  });
}

// Given a list whose rows come through the real native controller; Action:
// navigate list -> new -> list -> detail; Expected: direct-query route owns
// the visible screen and the old list controls are withdrawn immediately.
// Given an unchanged, edited, cleared or newly entered email; Action: save
// the real native form; Expected: its outgoing POST/PUT carries that value.
// This proves client serialization, not persistence through a mocked backend.
for (const scenario of [
  { name: "unchanged", create: false, value: A.email, expected: A.email },
  { name: "edited", create: false, value: "updated@example.test", expected: "updated@example.test" },
  { name: "cleared", create: false, value: "", expected: null },
  { name: "created", create: true, value: "new@example.test", expected: "new@example.test" },
]) test(`rendered patient email is sent on save: ${scenario.name}`, async ({ page, app }, testInfo) => {
  await session(page, app);
  const endpoint = "/api/admin/patients" + (scenario.create ? "" : "/" + A.dni);
  const method = scenario.create ? "POST" : "PUT";
  await seedRoster(page, (route) => route.request().method() === "POST"
    ? route.fulfill({ json: { ok: true, item: A } }) : route.fulfill({ json: list() }));
  await page.route("**/api/admin/patients/" + A.dni, (route) => route.fulfill({ json:
    route.request().method() === "PUT" ? { ok: true, item: A } : detail(A) }));
  await page.goto("/admin/pacientes" + (scenario.create ? "?new=1" : "?patient=" + A.dni));
  // La ficha de alguien con nombre abre en sus turnos; los datos van tras «Editar datos».
  if (!scenario.create) await page.click("#pac-edit");
  const email = page.locator('#pac-form input[type="email"]');
  await expect(email).toBeVisible();
  if (scenario.create) {
    await page.fill("#pat-dni", "30111999");
    await page.fill("#pat-name", "Nueva");
    await page.fill("#pac-apellido", "Persona");
    await page.fill("#pat-birth", "28/10/1978");
    await page.selectOption("#pat-sex", "F");
  }
  if (scenario.name !== "unchanged") await email.fill(scenario.value);
  else await expect(email).toHaveValue(A.email);
  const screenshot = await saveShot(testInfo, page, join(evidence, "email-" + scenario.name + ".png"), { fullPage: true });
  const requestPromise = page.waitForRequest((r) => r.method() === method && new URL(r.url()).pathname === endpoint);
  await page.click("#pac-save");
  const request = await requestPromise;
  const actual = request.postDataJSON().email;
  emailWrites.push({ name: scenario.name, method, actual, expected: scenario.expected,
    screenshot: screenshot.path, screenshotBytes: screenshot.bytes });
  expect(actual, "saving must use the email control actually rendered in this form").toBe(scenario.expected);
});

test("list, new and detail navigation own one screen at a time", async ({ page, app }, testInfo) => {
  const { errors } = await session(page, app);
  await seedRoster(page);
  await page.route("**/api/admin/patients/30111222", (route) => route.fulfill({ json: detail(A) }));
  await page.goto("/admin/pacientes");
  await readyList(page);
  await page.click("a.pat-title-new");
  await expect(page.locator("#pac-form")).toBeVisible();
  expect(new URL(page.url()).search).toBe("?new=1");
  await page.locator(".cnt-crumb a").click();
  await readyList(page);
  await page.locator('a[href="/admin/pacientes?patient=30111222"]').click();
  await readyDetail(page, "30111222");
  expect(await page.locator("#pac-q").count(), "list editable controls are absent under detail ownership").toBe(0);
  await saveShot(testInfo, page, join(evidence, "navigation-detail.png"), { fullPage: true });
  expect(errors).toEqual([]);
});

// Given a held old search request; Action: enter a search, then switch to a
// real detail route before releasing it; Expected: the started old request
// cannot repaint over the current detail, which still loads successfully.
test("old search success cannot replace a newer detail owner", async ({ page, app, cleanup }) => {
  await session(page, app);
  const old = heldRoute(cleanup);
  await ignoreReadAbort(page);
  await page.route("**/api/admin/patients?**", async (route) => {
    if (new URL(route.request().url()).searchParams.get("q") === "old") { old.begin(); await old.held; return route.fulfill({ json: list([A]) }); }
    return route.fulfill({ json: list() });
  });
  await page.route("**/api/admin/patients", (route) => route.fulfill({ json: list() }));
  await page.route("**/api/admin/patients/30111222", (route) => route.fulfill({ json: detail(A) }));
  await page.goto("/admin/pacientes"); await readyList(page);
  await page.fill("#pac-q", "old"); await old.started();
  await navigateDetail(page, A.dni); await readyDetail(page, A.dni);
  old.release(); await settled(page, "q=old");
  expect(await page.locator("#pac-form").count()).toBe(1);
  expect(await page.locator("#pac-q").count()).toBe(0);
});

// Given A's fully rendered editable form; Action: select B while its read is
// held; Expected: no A controls can mutate B during the identity transition.
test("A to B held detail immediately removes A editable controls", async ({ page, app, cleanup }) => {
  await session(page, app);
  const heldB = heldRoute(cleanup);
  await seedRoster(page);
  await page.route("**/api/admin/patients/30111222", (route) => route.fulfill({ json: detail(A) }));
  await page.route("**/api/admin/patients/28777666", async (route) => { heldB.begin(); await heldB.held; await route.fulfill({ json: detail(B) }); });
  await page.goto("/admin/pacientes"); await readyList(page);
  await navigateDetail(page, A.dni); await readyDetail(page, A.dni);
  await expect(page.locator("#pat-name")).toHaveValue(A.nombre);
  await navigateDetail(page, B.dni); await heldB.started();
  await expect(page.locator('#sec-pacientes [role="status"]')).toBeVisible();
  expect(await page.locator("#pac-save").count(), "A save is withdrawn before B arrives").toBe(0);
  heldB.release(); await readyDetail(page, B.dni);
  await expect(page.locator("#pat-name")).toHaveValue(B.nombre);
});

// Given a delayed old search 401 and the real shared session module; Action:
// switch to a newer successful search before releasing the 401; Expected: the
// stale confirmation cannot redirect the newer list owner.
test("old delayed search 401 cannot redirect a newer search owner", async ({ page, app, cleanup }) => {
  await session(page, app);
  const old = heldRoute(cleanup);
  await ignoreReadAbort(page);
  await page.route("**/api/admin/whoami", async (route) => {
    old.begin(); await old.held; return route.fulfill({ status: 401, json: { error: "no_autenticado" } });
  });
  await page.route("**/api/admin/patients?**", async (route) => {
    const q = new URL(route.request().url()).searchParams.get("q");
    if (q === "old401") return route.fulfill({ status: 401, json: { error: "no_autenticado" } });
    return route.fulfill({ json: list(q === "new" ? [B] : [A, B]) });
  });
  await page.route("**/api/admin/patients", (route) => route.fulfill({ json: list() }));
  await page.goto("/admin/pacientes"); await readyList(page);
  await page.fill("#pac-q", "old401"); await old.started();
  const freshResponse = page.waitForResponse((response) => response.url().includes("q=new") && response.status() === 200);
  await page.fill("#pac-q", "new"); await freshResponse;
  await expect(page.locator("#pat-table tbody tr")).toHaveCount(1);
  old.release(); await settled(page, "/api/admin/whoami");
  expect(new URL(page.url()).pathname).toMatch(/\/admin\/pacientes/);
  expect(await page.locator("#pac-q").inputValue()).toBe("new");
});

// Given the real template and a held initial roster request; Action: dispatch
// pagehide, replace the host, then release; Expected: disposal makes the old
// completion inert, while persisted pageshow remounts the real list.
test("template disposal and persisted remount retain ownership", async ({ page, app, cleanup }) => {
  await session(page, app);
  const initial = heldRoute(cleanup);
  await ignoreReadAbort(page);
  await page.route("**/api/admin/patients", async (route) => { initial.begin(); await initial.held; await route.fulfill({ json: list() }); });
  const nav = page.goto("/admin/pacientes", { waitUntil: "domcontentloaded" });
  await initial.started();
  await page.evaluate(() => { dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })); document.getElementById("sec-pacientes").textContent = "replacement owner"; });
  initial.release(); await nav; await settled(page, "/api/admin/patients");
  expect(await page.locator("#sec-pacientes").innerText()).toBe("replacement owner");
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  await readyList(page);
});

// Given a rejected native entry import; Action: load the real template;
// Expected: actionable reload recovery appears inside the screen.
test("entry dependency rejected has visible recovery", async ({ page, app }, testInfo) => {
  await session(page, app);
  const modulePattern = /\/static\/platform\/admin-patients\.[a-f0-9]+\.mjs/;
  await page.route(modulePattern, (route) => route.abort("failed"));
  await page.goto("/admin/pacientes", { waitUntil: "domcontentloaded" });
  await expect(page.locator("#sec-pacientes [data-retry]")).toBeVisible({ timeout: 12000 });
  await saveShot(testInfo, page, join(evidence, "entry-rejected-recovery.png"), { fullPage: true });
  await page.unroute(modulePattern);
  await seedRoster(page);
  await page.locator("[data-retry]").click();
  await readyList(page);
});

// Given a hung native entry import; Action: hold it past the eight-second
// deadline; Expected: the shared late copy shows beside the screen, and the
// released import still mounts the roster and clears it (2026-11-16,
// static/platform/screen-boot.mjs).
test("entry dependency hung past the deadline shows the late copy, then mounts", async ({ page, app, cleanup }, testInfo) => {
  await session(page, app);
  const gate = holdGate(cleanup);
  const modulePattern = /\/static\/platform\/admin-patients\.[a-f0-9]+\.mjs/;
  await page.route(modulePattern, async (route) => { await gate.held; return route.continue(); });
  await seedRoster(page);
  await page.goto("/admin/pacientes", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".screen-load-slow [data-retry]")).toBeVisible({ timeout: 12000 });
  expect(await page.locator("#sec-pacientes [data-retry]").count(), "the copy never replaces the screen").toBe(0);
  await saveShot(testInfo, page, join(evidence, "entry-hung-late-copy.png"), { fullPage: true });
  gate.release();
  await readyList(page);
  await expect(page.locator(".screen-load-slow")).toHaveCount(0);
});

// Given a real new-patient form and a held POST acknowledgement; Action:
// press save twice; Expected: exactly one request starts and malformed 200
// acknowledgement leaves the typed form available for correction.
test("single-flight create requires an acknowledged mutation body", async ({ page, app, cleanup }) => {
  await session(page, app);
  const post = heldRoute(cleanup);
  let posts = 0;
  await seedRoster(page);
  await page.route("**/api/admin/patients", async (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: list() });
    posts += 1; post.begin(); await post.held; await route.fulfill({ status: 200, json: { ok: true } });
  });
  await page.goto("/admin/pacientes?new=1");
  await expect(page.locator("#pac-form")).toBeVisible();
  await page.fill("#pat-dni", "30999111"); await page.fill("#pat-name", "Rosa"); await page.fill("#pac-apellido", "Prueba"); await page.fill("#pat-birth", "01/02/1990");
  await page.selectOption("#pat-sex", "F");
  await page.click("#pac-save"); await post.started();
  await expect(page.locator("#pac-save")).toBeDisabled();
  await page.locator("#pac-save").dispatchEvent("click");
  await holdsFor(async () => { expect(posts, "only one held mutation starts").toBe(1); }, { during: 300 });
  post.release(); await frames(page);
  await expect(page.locator("#pac-save")).toBeEnabled();
  expect(await page.locator("#pac-form").count()).toBe(1);
  expect(await page.locator("#pat-name").inputValue()).toBe("Rosa");
});

// Given a detail with a registry-owned coverage and an editable dependent
// draft; Action: add/remove coverage rows and trigger a held dependent refresh;
// Expected: multiple drafts and registry provenance survive, and late refresh
// cannot erase a newly opened dependent form.
test("coverage drafts and dependent refresh retain current editable ownership", async ({ page, app, cleanup }) => {
  await session(page, app);
  const refresh = heldRoute(cleanup);
  let dependentPost = false;
  await ignoreReadAbort(page);
  await page.route("**/api/admin/patients/30111222", async (route) => {
    if (dependentPost && route.request().method() === "GET") { refresh.begin(); await refresh.held; return route.fulfill({ json: detail(A) }); }
    return route.fulfill({ json: detail(A) });
  });
  await page.route("**/api/admin/patients/30111222/dependents", (route) => { dependentPost = true; return route.fulfill({ json: { ok: true } }); });
  await page.goto("/admin/pacientes?patient=30111222"); await readyDetail(page, A.dni);
  await page.click("#pac-edit"); await page.click("#pat-coverage-add"); await page.click("#pat-coverage-add");
  const names = page.locator("[data-cob-nombre]");
  await names.nth(0).fill("Draft one"); await names.nth(1).fill("Draft two");
  expect(await page.locator(".pat-coverage-fixed").count(), "registry coverage remains fixed").toBe(1);
  await page.click('[data-cob-quitar="1"]');
  expect(await page.locator("[data-cob-nombre]").count(), "removing one draft preserves another").toBe(1);
  expect(await page.locator("[data-cob-nombre]").inputValue()).toBe("Draft two");
  await page.click("#pac-dep-add"); await page.fill("#dep-dni", "40111222"); await page.fill("#dep-nombre", "Tomás"); await page.fill("#dep-apellido", "Prueba"); await page.fill("#dep-nac", "01/02/2015");
  await page.click("#dep-save"); await refresh.started();
  await page.click("#pac-dep-add"); await page.fill("#dep-nombre", "Nueva dueña");
  await page.evaluate(() => { window.__patientReads = []; });
  refresh.release(); await settled(page, "/api/admin/patients/30111222");
  expect(await page.locator("#dep-nombre").inputValue()).toBe("Nueva dueña");
});

// Ley 25.326 art. 14, against the real backend (nothing intercepted): the
// admin's «Descargar todos los datos del paciente» sits next to «Ver quién
// abrió sus datos», downloads the zip, and the download is itself a row of
// the access log the admin can then read.
test("the admin downloads all of a patient's data and the copy shows in the access log", async ({ page, app }, testInfo) => {
  const { errors } = await session(page, app);
  const dni = "30222333";
  const created = await page.request.post("/api/admin/patients", { data: {
    dni, nombre: "Ana", apellido: "Pereyra", nacimiento: "1980-05-05", sexo: "F", contacto: "2254111222",
  } });
  expect(created.ok(), "a real patient to export").toBe(true);
  await page.goto("/admin/pacientes?patient=" + dni);
  await readyDetail(page, dni);
  const link = page.locator("#pac-datos-exportar");
  await expect(link).toHaveText("Descargar sus datos");
  const row = await page.evaluate(() => {
    const a = document.querySelector("#pac-datos-exportar").getBoundingClientRect();
    const b = document.querySelector("#pac-accesos-ver").getBoundingClientRect();
    return { sameLine: Math.abs(a.top - b.top) < 2, after: a.left > b.right };
  });
  expect(row, "a secondary link on the same line, after «Ver quién abrió sus datos»").toEqual({ sameLine: true, after: true });
  await link.scrollIntoViewIfNeeded();
  await saveShot(testInfo, page, join(evidence, "data-export-desktop.png"));
  await page.setViewportSize({ width: 390, height: 844 });
  await link.scrollIntoViewIfNeeded();
  const phone = await page.evaluate(() => {
    const r = (s) => document.querySelector(s).getBoundingClientRect();
    const a = r("#pac-datos-exportar"), b = r("#pac-accesos-ver"), h = r("#pat-accesos h2");
    return { sameLine: Math.abs(a.top - b.top) < 2, inside: a.right <= innerWidth && b.left >= h.left - 1 };
  });
  expect(phone, "on a phone both actions still sit on one line, inside the screen").toEqual({ sameLine: true, inside: true });
  await saveShot(testInfo, page, join(evidence, "data-export-phone.png"));

  const downloading = page.waitForEvent("download");
  await link.click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/^datos-[0-9a-f]{8}\.zip$/);
  const bytes = await readFile(await download.path());
  expect(bytes.subarray(0, 2).toString("latin1"), "a zip archive").toBe("PK");

  await page.click("#pac-accesos-ver");
  await expect(page.locator("#pat-accesos-out")).toContainText("Copia de todos sus datos");
  expect(errors).toEqual([]);
});

// Demo 2026-10-26: searching and opening a patient's card on the read-only demo
// toasted «No se pudo guardar tu preferencia». The search is remembered as a
// preference; the demo refuses every write; a preference now stays in the tab.
test("on the read-only demo, searching and opening a card raises no save error", async ({ browser, launchApp, cleanup }, testInfo) => {
  test.setTimeout(120_000);
  const fixture = await launchApp({ dbName: "sana_turnos_test_patients_demo_prefs", env: { TURNOS_DEMO_MODE: "1" } });
  const owner = await browser.newContext({ baseURL: fixture.baseUrl });
  cleanup(() => owner.close());
  const ownerPage = await owner.newPage();
  await loginAdmin(ownerPage, fixture.baseUrl);
  // Outside the demo seed's own patients (scripts/seed_demo_all.py: 30000000 + i * 137,
  // and 30009042 is its patient 66): the demo seeds them in the background at boot, so
  // that DNI answered 409 whenever the seed got there first (under load, 2 of 3 attempts).
  const alta = await ownerPage.request.post("/api/admin/patients", { data: {
    dni: "27009042", nombre: "Ana", apellido: "Gómez", nacimiento: "1980-01-01", sexo: "F",
  } });
  expect(alta.status(), "the owner seeds one patient").toBe(200);

  const demo = await browser.newContext({ baseURL: fixture.baseUrl, viewport: { width: 390, height: 844 } });
  cleanup(() => demo.close());
  const page = await demo.newPage();
  const puts = [];
  page.on("request", (req) => { if (req.method() === "PUT" && req.url().includes("/api/admin/preferences")) puts.push(req.url()); });
  await page.goto("/admin/demo-access");
  await page.waitForURL((url) => url.pathname === "/admin/solicitudes");
  await page.goto("/admin/pacientes");
  await readyList(page);
  const refused = page.waitForResponse((res) => res.request().method() === "PUT"
    && res.url().includes("/api/admin/preferences"), { timeout: 10_000 });
  await page.fill("#pac-q", "Gómez");
  expect((await refused).status(), "the demo refuses the first preference write").toBe(403);
  await page.fill("#pac-q", "Góme");
  // The new search is remembered (300 ms + 180 ms debounce) without a request.
  await holdsFor(async () => expect(puts.length).toBe(1), { during: 900 });
  const fila = page.locator('a[href="/admin/pacientes?patient=27009042"]');
  await expect(fila).toBeVisible({ timeout: 10_000 });
  await fila.click();
  await readyDetail(page, "27009042");
  await page.goto("/admin/pacientes");
  await readyList(page);
  await holdsFor(async () => {
    await expect(page.getByText(/No se pudo guardar tu preferencia/)).toHaveCount(0);
    await expect(page.getByText(/Demo de solo lectura/)).toHaveCount(0);
  }, { during: 1500 });
  expect(puts.length, "after the first refusal the later choices stay in the tab").toBe(1);
  await expect(page.locator("#pac-q"), "the visit still remembers the search").toHaveValue("Góme");
  await page.screenshot({ path: testInfo.outputPath("demo-pacientes-sin-aviso-390.png") });
});

// Product owner, 2026-11-03: "fix the patient's turnos, not the patient: cancel, confirm" (translated). The ficha
// opens on the patient's turnos with their actions; the data form is behind «Editar datos».
test("the ficha opens on the patient's turnos with their actions, and the data form is behind «Editar datos»", async ({ page, app }) => {
  await session(page, app);
  const turnos = [
    { id: 901, estado: "pendiente", dia: "2099-01-05", hora: "09:00", servicio: "Clínica", medico: "Dra. Prueba", centro: "Hospital", futuro: true },
    { id: 902, estado: "aprobada", dia: "2099-01-06", hora: "10:00", servicio: "Clínica", medico: "Dra. Prueba", centro: "Hospital", futuro: true },
    { id: 903, estado: "aprobada", dia: "2020-01-06", hora: "10:00", servicio: "Clínica", medico: "Dr. Otro", centro: "Hospital", futuro: false },
  ];
  const posts = [];
  await page.route("**/api/admin/patients/30111222", (route) => route.fulfill({ json: { ...detail(A), turnos } }));
  await page.route("**/api/admin/requests/*/cancel", (route) => { posts.push(route.request().url()); return route.fulfill({ json: { ok: true } }); });
  await page.route("**/api/admin/requests/*/attendance", (route) => { posts.push(route.request().postData()); return route.fulfill({ json: { ok: true } }); });
  await page.goto("/admin/pacientes?patient=30111222");
  await readyDetail(page, A.dni);

  // The top is one line: name and age, the DNI small, icons. No doctors line: every turno names its doctor.
  await expect(page.locator(".pat-head-name")).toHaveText(/^Alejo Varela, \d+\s*$/);
  await expect(page.locator(".pat-head-info a.cnt-ico-link")).toHaveCount(2);
  await expect(page.locator(".pat-head-doc")).toHaveCount(0);
  expect(await page.locator(".pat-head").innerText(), "the phone is not printed").not.toContain(A.contacto);

  // The turnos come first; the form is closed.
  await expect(page.locator("#pat-turnos")).toBeVisible();
  await expect(page.locator("#pac-datos")).toBeHidden();
  await expect(page.locator("#pac-edit")).toHaveAttribute("aria-expanded", "false");

  // A pending turno is confirmed from the desk's list; a future approved one can only be cancelled; a past one is marked.
  await expect(page.locator('[data-pt-fila="901"] a[href="/admin/solicitudes"]')).toBeVisible();
  await expect(page.locator('[data-pt-fila="902"] [data-pt-cancel]')).toBeVisible();
  await expect(page.locator('[data-pt-fila="902"] [data-pt-asis]')).toHaveCount(0);
  await page.click('[data-pt-fila="903"] [data-pt-asis][data-valor="presente"]');
  await expect.poll(() => posts.length, { message: "the attendance write" }).toBe(1);
  expect(posts[0]).toContain("presente");
  await page.click('[data-pt-fila="902"] [data-pt-cancel]');
  await expect(page.locator('[data-pt-fila="902"] [data-pt-cancel-yes]')).toBeVisible();
  await page.click('[data-pt-fila="902"] [data-pt-cancel-no]');
  await expect(page.locator('[data-pt-fila="902"] [data-pt-cancel]')).toBeVisible();
  await page.click('[data-pt-fila="902"] [data-pt-cancel]');
  await page.click('[data-pt-fila="902"] [data-pt-cancel-yes]');
  await expect.poll(() => posts.length, { message: "the cancel write" }).toBe(2);
  expect(posts[1]).toContain("/api/admin/requests/902/cancel");

  // «Editar datos» opens the form and closes it again.
  await page.click("#pac-edit");
  await expect(page.locator("#pac-datos")).toBeVisible();
  await expect(page.locator("#pac-edit")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#pat-name")).toHaveValue("Alejo");
  await page.click("#pac-edit");
  await expect(page.locator("#pac-datos")).toBeHidden();
});

// Product owner, 2026-11-04: «the first column is taller because it holds buttons; the height must not change»
// (translated). A turno row weighs the same with buttons, with a different button, or with none.
for (const [width, heightPx] of [[1440, 900], [1024, 900], [390, 844]]) {
  test(`a turno row in the ficha is as tall with buttons as without them, at ${width}px`, async ({ page, app }) => {
    await page.setViewportSize({ width, height: heightPx });
    await session(page, app);
    const turnos = [
      { id: 911, estado: "pendiente", dia: "2099-01-05", hora: "09:00", servicio: "Clínica", medico: "Dra. Prueba", centro: "Hospital", futuro: true },
      { id: 912, estado: "aprobada", dia: "2099-01-06", hora: "10:00", servicio: "Clínica", medico: "Dra. Prueba", centro: "Hospital", futuro: true },
      { id: 913, estado: "aprobada", dia: "2020-01-06", hora: "10:00", servicio: "Clínica", medico: "Dr. Otro", centro: "Hospital", futuro: false },
      { id: 914, estado: "cancelada", dia: "2026-10-06", hora: "10:00", servicio: "Clínica", medico: "Dr. Otro", centro: "Hospital", futuro: false },
      { id: 915, estado: "aprobada", asistencia: "presente", dia: "2020-02-06", hora: "11:00", servicio: "Clínica", medico: "Dr. Otro", centro: "Hospital", futuro: false },
    ];
    await page.route("**/api/admin/patients/30111222", (route) => route.fulfill({ json: { ...detail(A), turnos } }));
    await page.goto("/admin/pacientes?patient=30111222");
    await readyDetail(page, A.dni);
    await expect(page.locator("[data-pt-fila]")).toHaveCount(turnos.length);
    await page.evaluate(() => document.fonts.ready);
    const heights = await page.evaluate(() => [...document.querySelectorAll("[data-pt-fila]")].map((tr) => ({
      id: tr.dataset.ptFila, buttons: tr.querySelectorAll("button, a.btn").length, h: Math.round(tr.getBoundingClientRect().height * 10) / 10,
      text: Math.round(tr.querySelector(".pat-appt-main").firstElementChild.getBoundingClientRect().height * 10) / 10,
      td: [...tr.cells].map((c) => Math.round(c.getBoundingClientRect().height * 10) / 10), btn: [...tr.querySelectorAll("button, a.btn")].map((b) => b.getBoundingClientRect().height),
    })));
    expect(heights.some((r) => r.buttons === 0), "one row has no button").toBe(true);
    expect(heights.some((r) => r.buttons === 2), "one row has two buttons").toBe(true);
    const none = heights.find((r) => r.buttons === 0);
    // A collapsed table border shares half a pixel between neighbours, so the first row can read 0.5 px less.
    // Only the text may make a row taller: when the status does not fit beside the date it takes its own
    // line (the first line grows), and the row grows by exactly that line, never because of the buttons.
    for (const row of heights) expect(Math.abs(row.h - none.h - (row.text - none.text)), `row ${row.id} (${row.buttons} buttons) vs the row with none: ${JSON.stringify(heights)}`).toBeLessThanOrEqual(1);
  });
}

test("patient lifecycle evidence report has all ten unique cold-load captures", () => {
  expect(captures.length).toBe(10);
  expect(new Set(captures.map((row) => row.id)).size).toBe(10);
});
