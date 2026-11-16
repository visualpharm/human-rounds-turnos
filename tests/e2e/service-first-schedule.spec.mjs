// The first schedule of a service asks before it supersedes the doctors' own
// ones, lists ONLY the doctors of that service, and a confirmed save resets
// only what the service covers (agenda creators' hotfix, 2026-10-14).
//
// Differences from the node:test original (tests/e2e/service-first-schedule.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the admin
//     session is the `adminPage` fixture (the same login form flow), whose viewport is 1280x900
//     through test.use({ viewport }) as the original's newContext({ viewport }). The default
//     admin landing (/admin/account) replaces the original's "any /admin/ page that is not login".
//   - Waits are web-first (expect(locator).toBeVisible/toHaveText with the original timeouts) and
//     assert.* became expect(...) with the same messages.
//   - The two scenarios share the database: the first one resets a doctor's own schedule and the second
//     picks "another doctor than the first scenario used" from what is left, so the file is serial
//     (the original ran them in this order too).
//   - The original's t.skip when the fixture has no doctor with a schedule is kept as test.skip(...).
//   - Locators: ids/data attributes of the service form (#cnt-form, #svc-*, input[data-agenda-dia], the
//     dialog.cnt-schedule-confirm class, #cnt-save-status) stay CSS, they are the form's markup contract.
import { test, expect } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_service_first_schedule" },
  viewport: { width: 1280, height: 900 },
});
test.describe.configure({ mode: "serial" });

const franjasOf = (doctor) => (doctor.centros || []).flatMap((c) => c.franjas || []);
const snapshot = (doctor) => JSON.stringify([doctor.centro, doctor.centros || null, doctor.agenda || null]);

test("first schedule: dialog names only the service doctors; cancel keeps all; continue resets only them", async ({ adminPage: page, app }) => {
  const api = async (path) => (await page.request.get(app.baseUrl + path)).json();
  const before = await api("/api/admin/content/doctors");
  const owner = before.items.find((d) => d.especialidad && franjasOf(d).length && d.centros && d.centros[0] && d.centros[0].centro);
  expect(owner, "the fixture has a doctor with a schedule and a specialty").toBeTruthy();
  const center = owner.centros[0].centro;

  // A service that IS the doctor's specialty at the doctor's center (name contains the specialty).
  const name = `${owner.especialidad} Prueba E2E`;
  const created = await page.request.post(app.baseUrl + "/api/admin/content/services", {
    data: { nombre: name, centro: center, modo: "solicitud", cluster: owner.cluster || "otras",
            horario: "Lunes a viernes de 9 a 12 h" } });
  expect(created.status(), await created.text()).toBe(200);
  const slug = (await created.json()).item.id;

  await page.goto(`${app.baseUrl}/admin/services?item=${encodeURIComponent(slug)}`, { waitUntil: "load" });
  await expect(page.locator("#cnt-form")).toBeVisible();
  await page.locator("#svc-schedule-owner").check();
  await page.locator('input[data-agenda-dia="svc"][value="1"]').check();
  const requestBody = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().includes("/api/admin/content/services/")) requestBody.push(request.postDataJSON());
  });

  // 1) save → the dialog lists the service's own doctors, and only them
  await page.locator('button[type="submit"]').click();
  const dialog = page.locator("dialog.cnt-schedule-confirm");
  await expect(dialog).toBeVisible({ timeout: 15_000 });
  const listed = await dialog.innerText();
  expect(listed.includes(owner.nombre), "the doctor of the service is named: " + listed).toBe(true);
  const strangers = before.items.filter((d) => d.id !== owner.id && d.especialidad !== owner.especialidad && listed.includes(d.nombre));
  expect(strangers.map((d) => d.nombre), "no doctor of another specialty is named").toEqual([]);

  // 2) Cancelar → nothing was saved, no doctor was touched
  await dialog.getByRole("button", { name: "Cancelar" }).click();
  await expect(page.locator("#cnt-save-status").filter({ hasText: "No se guardaron cambios." })).toBeVisible({ timeout: 10_000 });
  const afterCancel = await api("/api/admin/content/doctors");
  expect(afterCancel.items.map(snapshot), "cancel changed nothing").toEqual(before.items.map(snapshot));

  // 3) save again → Continuar → saved; only the service's doctors lost their window at that center
  await page.locator('button[type="submit"]').click();
  await expect(dialog).toBeVisible({ timeout: 15_000 });
  await dialog.getByRole("button", { name: "Continuar" }).click();
  await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible({ timeout: 15_000 });
  expect(requestBody.some((body) => body.confirmar_reinicio_agendas === true), "the confirmed save carries the flag").toBe(true);

  const services = await api("/api/admin/content/services");
  const saved = services.items.find((item) => item.id === slug);
  expect(saved && saved.agenda && (saved.agenda.dias || saved.agenda.franjas), "the service owns its schedule now").toBeTruthy();
  const afterContinue = await api("/api/admin/content/doctors");
  const byId = Object.fromEntries(afterContinue.items.map((d) => [d.id, d]));
  const ownerAfter = byId[owner.id];
  const atCenter = (ownerAfter.centros || []).filter((c) => c.centro === center).flatMap((c) => c.franjas || []);
  expect(atCenter.filter((f) => !f.servicio || f.servicio === slug), "the doctor has no own window left at that center").toEqual([]);
  for (const doctor of before.items) {
    if (doctor.id === owner.id || doctor.especialidad === owner.especialidad) continue;
    expect(snapshot(byId[doctor.id]), `${doctor.nombre} was not touched`).toBe(snapshot(doctor));
  }
});

test("a service with two windows (as the agenda creators build them) saves its booking window without asking", async ({ adminPage: page, app }) => {
  const api = async (path) => (await page.request.get(app.baseUrl + path)).json();

  const doctors = await api("/api/admin/content/doctors");
  const candidates = doctors.items.filter((d) => d.especialidad && franjasOf(d).length && d.centros && d.centros[0] && d.centros[0].centro);
  // another doctor than the first scenario used, with a specialty of its own
  const owner = candidates.find((d) => candidates[0].especialidad !== d.especialidad) || candidates[candidates.length - 1];
  test.skip(!owner, "the fixture has no doctor with a schedule");
  const center = owner.centros[0].centro;
  const window = (days) => ({ dias: days, desde: "12:30", hasta: "14:00", cada_min: 20 });
  const body = { nombre: `${owner.especialidad} Dos Ventanas E2E`, centro: center, modo: "solicitud_con_orden",
                 cluster: owner.cluster || "otras", horario: "Martes y jueves de 12:30 a 14 h",
                 agenda: { franjas: [window([2]), window([4])], excepciones: [] }, confirmar_reinicio_agendas: true };
  const created = await page.request.post(app.baseUrl + "/api/admin/content/services", { data: body });
  expect(created.status(), await created.text()).toBe(200);
  const slug = (await created.json()).item.id;
  // a doctor of this service whose window is still there: make the doctor's own schedule exist again, so the
  // old behaviour («reiniciará los horarios») would trigger on an unchanged schedule
  const stored = (await api("/api/admin/content/services")).items.find((item) => item.id === slug);
  expect((stored.agenda.franjas || []).length, "stored with two windows").toBe(2);

  await page.goto(`${app.baseUrl}/admin/services?item=${encodeURIComponent(slug)}`, { waitUntil: "load" });
  await expect(page.locator("#cnt-form")).toBeVisible();
  await page.locator("#svc-internal > summary").click();
  await page.locator("#svc-dias-anticipacion").fill("15");
  await page.locator("#svc-apertura-turnos").fill("08:00");
  const write = page.waitForResponse((response) => new URL(response.url()).pathname.startsWith("/api/admin/content/services")
    && response.request().method() !== "GET", { timeout: 15_000 });
  await page.locator('button[type="submit"]').click();
  const response = await write;
  expect(response.status(), "saved at once: " + (await response.text()).slice(0, 300)).toBe(200);
  // A snapshot, not a retrying toHaveCount(0): the reset dialog must not have opened at any point after the save.
  expect(await page.locator("dialog[open]").count(), "no reset confirmation").toBe(0);
  await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible({ timeout: 15_000 });
});
