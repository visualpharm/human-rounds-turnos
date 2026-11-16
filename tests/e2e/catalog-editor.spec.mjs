// tests/e2e/catalog-editor.spec.mjs -- the shared catalog editor's module
// cohort, proven in a real browser at phone and desktop widths.
//
// Regression (2026-09-04, commit 7e284c60): cuposEditor() renders the
// SERVICES agenda editor through the canonical native hours module
// (capacityDescriptionHtml/webChoiceHtml/closure controls). A service with
// an agenda used to throw when the classic helper was absent; the native
// module is a held dependency of platform/admin-content.mjs and must be
// ready before the form renders.
// these proofs pin the corrected architecture from the browser side:
//
//   1. a real example-town service WITH an agenda opens clean (no page
//      error, .cnt-cupos rendered through the native module) at 390px and
//      1280px, including the narrow 360px service-editor regression;
//   2. the shared dependency loads EXACTLY once on services AND doctors;
//   3. the catalog editor is dynamically imported exactly once per screen,
//      and the doctor-only dependencies (the EAGER users-module import and
//      the calendar-grid the MedCal island shares, plus SANA_ISLAND_URLS)
//      are requested on /admin/doctors ALONE -- create-appointment and the
//      MedCal island itself stay on-demand (zero requests on a list load).
//      Counts read the browser's actual resource timing once the live
//      catalog has painted, never classic script-tag counts.
//
// Port of tests/e2e/catalog-editor.e2e.mjs (4 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })). Every test
//   (and every width of the first one) opens its own context of the runner's browser through
//   `newContext` with its viewport, as the original did, and signs in with loginAdmin();
//   the page-error / console.error collectors are attached before the first navigation.
// - Test 2: the 150 ms sleep before «the form must not paint capacity controls» is a 150 ms
//   holdsFor() window (no .cnt-cupos, no legacy global) on the held native hours module; the
//   hand-made held promise is holdGate(cleanup), released on cleanup too.
// - Waits are web-first with the original timeouts: waitFor -> toBeVisible, the resource
//   timing waitForFunction (doctors) -> expect.poll, the «Guardado.» status wait ->
//   toHaveText. State reads of the overbooking controls (checked / disabled / value) are
//   retrying expects, except the «unchecked» reads, which stay snapshots; the page-error lists and the resource-timing counts stay snapshots.
// - Locators left as CSS: the editor's classes / data attributes (.cnt-cupos, .hor-limits,
//   [data-limits-overbooking*], [data-tab], [data-add-excepcion], #sec-*, #cnt-save-status)
//   and the form-associated submit button - the markup contract under test; the
//   resource-timing counts are computed from the browser's own entries.
import { test, expect, loginAdmin, holdGate, holdsFor } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_catalog_editor" } });

// example-town service with modo=solicitud AND a weekly agenda -- the
// exact shape whose editor path goes through cuposEditor().
const AGENDA_SERVICE = "medicina-general-riverside-clinic";

// Attaches the collectors BEFORE any navigation: page errors are only
// observable through the page object that was there when they fired.
async function freshPage(newContext, errors, viewport) {
  const context = await newContext({ viewport });
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console.error: ${m.text()}`);
  });
  return { context, page };
}

test("un servicio con agenda abre sin errores en ancho teléfono y escritorio", async ({ app, newContext }) => {
  for (const viewport of [
    { width: 360, height: 844 },
    { width: 390, height: 844 },
    { width: 768, height: 900 },
    { width: 1280, height: 800 },
  ]) {
    const errors = [];
    const { context, page } = await freshPage(newContext, errors, viewport);
    try {
      await loginAdmin(page, app.baseUrl);
      await page.goto(
        `${app.baseUrl}/admin/services?item=${AGENDA_SERVICE}`,
        { waitUntil: "load", timeout: 20000 },
      );
      // The agenda editor (the cuposEditor path that crashed) must be
      // painted, not just the shell.
      await expect(page.locator(".cnt-cupos")).toBeVisible({ timeout: 10000 });
      const capacity = await page.locator(".cnt-cupos .hor-capacity-description").count();
      expect(capacity, "la descripción de cupos del módulo nativo debe renderizar").toBeGreaterThanOrEqual(1);
      const fatal = errors.filter((e) => !e.includes("favicon"));
      expect(fatal, `no page errors at ${viewport.width}px`).toEqual([]);
      // Form controls must remain within the visible service editor at every
      // compact width, including a center name wider than the phone viewport.
      if (viewport.width <= 768) {
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `scroll horizontal ${overflow}px en ${viewport.width}px`).toBeLessThanOrEqual(1);
      }
    } finally {
      await context.close();
    }
  }
});

test("el módulo nativo retenido no pinta una agenda incompleta y un rechazo recupera al recargar", async ({ app, newContext, cleanup }) => {
  const errors = [];
  const { page } = await freshPage(newContext, errors, { width: 390, height: 844 });
  const moduleRoute = "**/platform/admin-hours-editor.*.mjs*";
  const gate = holdGate(cleanup);
  await loginAdmin(page, app.baseUrl);
  await page.route(moduleRoute, async (route) => {
    await gate.held;
    await route.continue().catch(() => {});
  });
  await page.goto(`${app.baseUrl}/admin/services?item=${AGENDA_SERVICE}`, {
    waitUntil: "domcontentloaded", timeout: 20000,
  });
  await holdsFor(async () => {
    expect(await page.locator(".cnt-cupos").count(),
      "the form must wait rather than paint capacity controls from a fallback").toBe(0);
    expect(await page.evaluate(() => typeof window.SanaHoursEditor)).toBe("undefined");
  }, { during: 150 });
  gate.release();
  await expect(page.locator(".cnt-cupos")).toBeVisible({ timeout: 10000 });
  expect(errors.filter((e) => !e.includes("favicon"))).toEqual([]);

  await page.unroute(moduleRoute);
  await page.route(moduleRoute, (route) => route.fulfill({ status: 503, body: "module unavailable" }));
  await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
  const recovery = page.locator("#sec-servicios .adm-empty");
  await expect(recovery).toBeVisible({ timeout: 10000 });
  expect(await recovery.innerText(),
    "a rejected module must show the controller recovery instead of a blank editor").toMatch(/No se pudo cargar|Could not load/i);

  await page.unroute(moduleRoute);
  errors.length = 0;
  await page.reload({ waitUntil: "load", timeout: 20000 });
  await expect(page.locator(".cnt-cupos")).toBeVisible({ timeout: 10000 });
  expect(errors.filter((e) => !e.includes("favicon")),
    "a normal reload retries the native import and restores the existing schedule").toEqual([]);
});

test("la dependencia compartida carga exactamente una vez, sin islas de médicos", async ({ app, newContext }) => {
  const errors = [];
  const { page } = await freshPage(newContext, errors, { width: 1280, height: 800 });
  await loginAdmin(page, app.baseUrl);
  const sections = { "/admin/services": "servicios", "/admin/centers": "centros", "/admin/doctors": "medicos" };
  for (const path of ["/admin/services", "/admin/centers", "/admin/doctors"]) {
    await page.goto(app.baseUrl + path, { waitUntil: "load", timeout: 20000 });
    // Live catalog readiness first: the list itself must be painted before
    // any dependency count is read, and on doctors the eager imports must
    // have actually landed (the list paints without waiting for them).
    await expect(page.locator(`#sec-${sections[path]} table.cnt-table`)).toBeVisible({ timeout: 10000 });
    if (path === "/admin/doctors") {
      await expect.poll(() => page.evaluate(() => {
        const names = performance.getEntriesByType("resource").map((entry) => entry.name);
        return names.some((name) => name.includes("platform/admin-users."))
          && names.some((name) => name.includes("platform/islands."))
          && names.some((name) => name.includes("platform/calendar-grid."));
      }), { timeout: 10000 }).toBe(true);
    }
    const counts = await page.evaluate(() => ({
      classicHours: document.querySelectorAll('script[src*="admin-hours-editor"]').length,
      nativeHours: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("platform/admin-hours-editor.")).length,
      legacyGlobal: typeof window.SanaHoursEditor,
      content: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("platform/admin-content.")).length,
      users: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("platform/admin-users.")).length,
      islandsLoader: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("platform/islands.")).length,
      calendarGrid: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("platform/calendar-grid.")).length,
      medcalIsland: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("islands/MedCal.")).length,
      createAppointment: performance.getEntriesByType("resource").filter((entry) => entry.name.includes("platform/admin-create-appointment.")).length,
      islandUrls: typeof window.SANA_ISLAND_URLS !== "undefined",
    }));
    expect(counts.classicHours, `${path}: no queda tag clásico del editor`).toBe(0);
    expect(counts.nativeHours,
      `${path}: carga exactamente el módulo nativo solo donde se editan horarios`).toBe(path === "/admin/centers" ? 0 : 1);
    expect(counts.legacyGlobal, `${path}: no publica un global de compatibilidad`).toBe("undefined");
    expect(counts.content, `${path}: el editor de catálogo se importa exactamente una vez`).toBe(1);
    if (path === "/admin/doctors") {
      expect(counts.islandUrls, "/admin/doctors conserva SANA_ISLAND_URLS").toBeTruthy();
      expect(counts.users, `${path}: el módulo users se importa una vez, solo en médicos`).toBe(1);
      expect(counts.islandsLoader, `${path}: el cargador de islas acompaña solo a médicos`).toBe(1);
      expect(counts.calendarGrid, `${path}: la grilla del calendario acompaña solo a médicos`).toBe(1);
    } else {
      expect(counts.islandUrls, `${path}: sin SANA_ISLAND_URLS`).toBe(false);
      expect(counts.users, `${path}: sin import de admin-users`).toBe(0);
      expect(counts.islandsLoader, `${path}: sin cargador de islas`).toBe(0);
      expect(counts.calendarGrid, `${path}: sin calendar-grid`).toBe(0);
    }
    expect(counts.medcalIsland, `${path}: la isla MedCal queda bajo demanda, no en la carga del listado`).toBe(0);
    expect(counts.createAppointment, `${path}: admin-create-appointment es lazy, cero requests al cargar`).toBe(0);
  }
  const fatal = errors.filter((e) => !e.includes("favicon"));
  expect(fatal, "sin errores de página en los tres catálogos").toEqual([]);
});

test("doctor overbooking defaults to 20 percent, persists edits, and disables cleanly", async ({ app, newContext }) => {
  const errors = [];
  const { context, page } = await freshPage(newContext, errors, { width: 390, height: 844 });
  await loginAdmin(page, app.baseUrl);
  // The seed doctor inherits a service-owned agenda. Create an independent
  // synthetic center/doctor so this case exercises the editable own agenda.
  const id = "overbooking-" + Date.now(), centerId = id + "-center";
  const doctorsResponse = await context.request.get(app.baseUrl + "/api/admin/content/doctors");
  expect(doctorsResponse.status()).toBe(200);
  const doctors = await doctorsResponse.json();
  const template = doctors.items.find((item) => item.id === "gonzalez-juan");
  expect(template).toBeTruthy();
  const center = await context.request.post(app.baseUrl + "/api/admin/content/centers", {
    data: { slug: centerId, name: "Synthetic Schedule Center" },
  });
  expect(center.status()).toBe(200); expect((await center.json()).ok).toBe(true);
  const doctor = { ...template, id, nombre: "Synthetic Scheduler", nombre_pila: "Synthetic", apellido: "Scheduler",
    centros: [{ centro: centerId, franjas: [{ dias: [1], desde: "08:00", hasta: "12:00", cada_min: 20 }], excepciones: [] }] };
  for (const key of Object.keys(doctor)) if (key.startsWith("_")) delete doctor[key];
  const created = await context.request.post(app.baseUrl + "/api/admin/content/doctors", { data: doctor });
  expect(created.status()).toBe(200);
  const creation = await created.json(); expect(creation.ok).toBe(true); expect(creation.item.id).toBe(id);
  const url = app.baseUrl + "/admin/doctors?item=" + id;
  // El control por franja se retiró: la edición vive en la sección
  // "Límites" del médico, que escribe inputs ocultos en cada franja.
  async function openHours() {
    await page.goto(url, { waitUntil: "load" });
    await page.locator('[data-tab="agenda"]').click();
    const limits = page.locator(".hor-limits").first();
    await expect(page.locator("[data-limits-overbooking]").first()).toBeVisible();
    return limits;
  }
  // Reencuentra la sección SIN navegar: un rerender de filas puede volver a
  // dibujar (y cerrar) "Límites", pero lo que se está probando es que el
  // valor sobreviva a ese rerender. Recargar la página lo borraría por
  // diseño -- la edición todavía no está guardada.
  async function reopenLimits() {
    await expect(page.locator(".hor-limits").first()).toBeAttached();
    await expect(page.locator("[data-limits-overbooking]").first()).toBeVisible();
  }
  await openHours();
  const toggle = page.locator("[data-limits-overbooking]").first();
  const percent = page.locator("[data-limits-overbooking-percent]").first();
  expect(await toggle.isChecked()).toBe(false);
  await expect(percent).toBeDisabled();
  await toggle.check();
  await expect(percent).toHaveValue("20");
  await percent.fill("35");
  await toggle.uncheck();
  await expect(percent).toBeDisabled();
  await toggle.check();
  await expect(percent).toHaveValue("35");
  // A separate schedule edit rerenders all rows; percent must survive it.
  await page.locator("[data-add-excepcion]").first().click();
  await reopenLimits();
  await expect(page.locator("[data-limits-overbooking-percent]").first()).toHaveValue("35");
  await page.locator("[data-remove-excepcion]").first().click();
  await reopenLimits();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  async function save() {
    const response = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/content/doctors/" + id && r.request().method() === "PUT");
    await page.locator('button[form="cnt-form"][type="submit"]').click();
    const result = await response;
    expect(result.status()).toBe(200);
    const acknowledgement = await result.json();
    expect(acknowledgement.ok).toBe(true); expect(acknowledgement.item.id).toBe(id);
    await expect(page.locator("#cnt-save-status")).toHaveText(/^Guardado\.?$/);
  }
  await save();
  await openHours();
  await expect(page.locator("[data-limits-overbooking]").first()).toBeChecked();
  await expect(page.locator("[data-limits-overbooking-percent]").first()).toHaveValue("35");
  await page.locator("[data-limits-overbooking]").first().uncheck();
  await save();
  await openHours();
  expect(await page.locator("[data-limits-overbooking]").first().isChecked()).toBe(false);
  await expect(page.locator("[data-limits-overbooking-percent]").first()).toBeDisabled();
  expect(errors.filter((e) => !e.includes("favicon"))).toEqual([]);
});
