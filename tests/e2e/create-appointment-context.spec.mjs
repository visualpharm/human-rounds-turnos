// tests/e2e/create-appointment-context.spec.mjs -- R3 (meeting 2026-09-04,
// "Crear turno" from a doctor must carry that doctor's context), proven in a
// real browser through BOTH real entry points:
//
//   1. the "Crear turno" button of Pacientes de hoy (/admin/cola, the title
//      line's #adm-crear-turno; once the list is narrowed to one doctor by
//      that doctor's link in the doctor column, it books for that doctor);
//   2. the module's public abrir({medico}) API for the sole-service
//      auto-selection case.
//
// What is pinned here (the static suite covers the source; this file proves
// the BEHAVIOR):
//   - the pinned doctor renders as a context row ("Médico <name>") with a
//     Quitar action;
//   - while pinned, the service typeahead ONLY offers that doctor's services
//     (a query matching an unrelated cluster returns the scoped empty state,
//     never the whole catalog);
//   - Quitar removes the context and the same query then matches the full
//     catalog;
//   - a doctor with exactly one service gets it auto-selected;
//   - the overlay renders clean (no page errors, no horizontal overflow) at
//     phone width.
//
// Differences from the node:test original (tests/e2e/create-appointment-context.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), same setupHarness
//     options: queue); the runner's `page` replaces the per-test context, and each test sets its own
//     viewport (1280x800 / 390x844) with page.setViewportSize() before it navigates. The login is
//     the fixtures' loginAdmin() (the error listeners are attached first, as in the original).
//   - The three 400 ms sleeps after typing in the service typeahead are polls on the suggestion list
//     for the state each query must reach (only scoped empties / at least one real service / a
//     «medicina» match); the stricter «nothing but the scoped empty» wording is unchanged.
//   - Waits are web-first (expect(locator).toBeVisible with the original timeouts).
//   - Locators: ids / data-* of the "Crear turno" overlay (#ct-*, data-cambiar, #adm-crear-turno,
//     data-nota, queue rows) stay CSS: they are the overlay's markup contract, and the suggestion list
//     is read as data (data-svc / data-vacio).
import { test, expect, loginAdmin } from "./lib/fixtures.mjs";

// example-town doctors: every cardiologia doctor attends 4 services
// (cardiologia-sampleton-hospital, electrocardiograma-hospital,
// ergometria-hospital, marcapasos-hospital) -- the fixture's --queue seed
// books one of Sergio Ruiz's, so the per-doctor button on /admin/cola names
// him. "Medicina general" lives in the clinica cluster: a query that matches
// it must NOT resolve while a cardiologia doctor is pinned.
const UNRELATED_QUERY = "medicina";
const PINNED_QUERY = "cardio";
// Juan González (clinica) attends exactly ONE turno service.
const SOLE_SERVICE_DOCTOR = "Juan González";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_create_appt_context", queue: true } });

async function suggestionIds(page) {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll("#ct-svc-sug li")).map((li) => ({
      svc: li.getAttribute("data-svc"),
      vacio: li.hasAttribute("data-vacio"),
      text: li.textContent.trim(),
    })));
}

/** Collect page errors the way the original did; favicon noise is ignored. */
function collectErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console.error: ${m.text()}`);
  });
  return errors;
}

test("el botón del médico fija el contexto: sólo sus servicios, nunca el catálogo entero", async ({ page, app }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const errors = collectErrors(page);
  await loginAdmin(page, app.baseUrl);
  await page.goto(app.baseUrl + "/admin/cola", { waitUntil: "load", timeout: 20_000 });

  // The real entry point: the doctor's link in the day's one list narrows
  // it to that doctor, then the title's Crear turno. The fixture's --queue
  // seed books one turno on Sergio Ruiz (cardiologia, several services) —
  // a doctor whose cluster makes the scoping observable.
  const doctorLink = page.locator('#sec-cola button.queue-doctor-link[data-cola-doctor="ruiz-sergio"]').first();
  await expect(doctorLink).toBeVisible({ timeout: 15_000 });
  const listName = (await doctorLink.innerText()).trim();
  await doctorLink.click();
  await expect(page.locator("#sec-cola .queue-doctor-head h2")).toHaveText(listName);
  const crearBtn = page.locator("#adm-crear-turno");
  await expect(crearBtn).toBeVisible({ timeout: 10_000 });
  await crearBtn.click();
  await expect(page.locator("#ct-modal")).toBeVisible({ timeout: 10_000 });

  // The overlay always boots at the patient step; the pinned-doctor context
  // row lives on the service step. Take the "anotar" free-text path — no
  // seeded patient needed.
  await expect(page.locator("#ct-pac")).toBeVisible({ timeout: 10_000 });
  await page.fill("#ct-pac", "García María");
  await expect(page.locator("#ct-pac-sug li[data-nota]")).toBeVisible({ timeout: 10_000 });
  await page.click("#ct-pac-sug li[data-nota]");
  await expect(page.locator("#ct-svc")).toBeVisible({ timeout: 10_000 });

  // The context row names the doctor and carries the Quitar action.
  const ctxRow = page.locator(".ct-paso", { has: page.locator('[data-cambiar="medico"]') }).first();
  await expect(ctxRow).toBeVisible({ timeout: 10_000 });
  const shown = await ctxRow.locator("b").innerText();
  expect(
    shown.trim().toLowerCase(),
    "la fila de contexto muestra el médico del botón",
  ).toBe(listName.trim().toLowerCase());

  // A cardiologia doctor is pinned: a clinica-cluster query must hit the
  // scoped empty state, never the full catalog.
  await page.fill("#ct-svc", UNRELATED_QUERY);
  await expect.poll(
    async () => { const items = await suggestionIds(page); return items.length > 0 && items.every((i) => i.vacio); },
    { message: `"${UNRELATED_QUERY}" no puede ofrecer servicios de otro profesional mientras el médico está fijado (y hay una fila de resultado: el vacío del scope)` },
  ).toBe(true);

  // A query inside the doctor's own cluster resolves to their services.
  await page.fill("#ct-svc", PINNED_QUERY);
  await expect.poll(
    async () => (await suggestionIds(page)).filter((i) => !i.vacio).length > 0,
    { message: `"${PINNED_QUERY}" ofrece los servicios del médico fijado` },
  ).toBe(true);

  // Quitar removes the context and the same query falls back to the FULL
  // catalog (medicina general is reachable again).
  await page.click('[data-cambiar="medico"]');
  await expect(page.locator("#ct-svc")).toBeVisible({ timeout: 10_000 });
  expect(await page.locator('[data-cambiar="medico"]').count(),
    "quitar el médico elimina la fila de contexto").toBe(0);
  await page.fill("#ct-svc", UNRELATED_QUERY);
  await expect.poll(
    async () => (await suggestionIds(page)).filter((i) => !i.vacio).some((i) => /medicina/i.test(i.text)),
    { message: `sin médico fijado el catálogo completo responde de nuevo y "${UNRELATED_QUERY}" encuentra Medicina general` },
  ).toBe(true);

  const fatal = errors.filter((e) => !e.includes("favicon"));
  expect(fatal, "sin errores de página").toEqual([]);
});

test("el DNI con puntos, como lo muestra la cola, encuentra la ficha en teléfono", async ({ page, app }) => {
  // Demo 2026-10-26: «30.009.042» copiado de Pacientes de hoy no encontraba a
  // nadie y la mesa daba de alta un duplicado.
  await page.setViewportSize({ width: 390, height: 844 });
  const errors = collectErrors(page);
  await loginAdmin(page, app.baseUrl);
  const alta = await page.request.post(app.baseUrl + "/api/admin/patients", { data: {
    dni: "30009042", nombre: "Ana", apellido: "Gómez", nacimiento: "1980-01-01", sexo: "F",
  } });
  expect(alta.status(), "la ficha existe antes de buscarla").toBe(200);
  await page.goto(app.baseUrl + "/admin/solicitudes", { waitUntil: "load", timeout: 20_000 });
  await expect(page.locator("#adm-crear-turno")).toBeVisible({ timeout: 10_000 });
  await page.evaluate(async () => {
    const mod = await import(window.SANA_STATIC["platform/admin-create-appointment.mjs"]);
    return mod.abrir({});
  });
  await expect(page.locator("#ct-pac")).toBeVisible({ timeout: 10_000 });
  await page.fill("#ct-pac", "30.009.042");
  const ficha = page.locator('#ct-pac-sug li[data-dni="30009042"]');
  await expect(ficha, "la ficha aparece como primera opción").toBeVisible({ timeout: 10_000 });
  await expect(ficha, "con el DNI escrito como en la cola").toContainText("30.009.042");
  await expect(page.locator("#ct-pac-sug li[data-alta]"), "no ofrece dar de alta otra vez").toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("crear-turno-dni-con-puntos-390.png") });
  const fatal = errors.filter((e) => !e.includes("favicon"));
  expect(fatal, "sin errores de página en 390px").toEqual([]);
});

test("un médico con un solo servicio llega con ese servicio ya elegido, también en teléfono", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const errors = collectErrors(page);
  await loginAdmin(page, app.baseUrl);
  await page.goto(app.baseUrl + "/admin/solicitudes", { waitUntil: "load", timeout: 20_000 });
  await expect(page.locator("#adm-crear-turno")).toBeVisible({ timeout: 10_000 });
  // The module's public API, the same call the queue button makes.
  await page.evaluate(async (medico) => {
    const url = window.SANA_STATIC["platform/admin-create-appointment.mjs"];
    const mod = await import(url);
    return mod.abrir({ medico });
  }, SOLE_SERVICE_DOCTOR);
  await expect(page.locator("#ct-modal")).toBeVisible({ timeout: 10_000 });
  // The overlay boots at the patient step; a nota is enough to advance.
  await expect(page.locator("#ct-pac")).toBeVisible({ timeout: 10_000 });
  await page.fill("#ct-pac", "López Pedro");
  await expect(page.locator("#ct-pac-sug li[data-nota]")).toBeVisible({ timeout: 10_000 });
  await page.click("#ct-pac-sug li[data-nota]");
  // Sole service: auto-selected, no empty picker.
  const elegido = page.locator(".ct-paso", { has: page.locator('[data-cambiar="servicio"]') }).first();
  await expect(elegido).toBeVisible({ timeout: 15_000 });
  await expect(elegido.locator("b"),
    `${SOLE_SERVICE_DOCTOR} tiene un solo servicio y llega ya elegido`).toHaveText(/medicina general/i);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, `scroll horizontal ${overflow}px con el overlay abierto en 390px`).toBeLessThanOrEqual(1);
  const fatal = errors.filter((e) => !e.includes("favicon"));
  expect(fatal, "sin errores de página en 390px").toEqual([]);
});
