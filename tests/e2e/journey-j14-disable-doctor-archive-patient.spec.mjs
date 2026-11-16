// tests/e2e/journey-j14-disable-doctor-archive-patient.spec.mjs -- J14 ("Taking a
// doctor or a patient out of service while they still have live appointments")
// cross-role journey acceptance.
//
// Project rule (admin_panel/AGENTS.md): «Deshabilitar, never
// eliminar» when an entity has appointments; a doctor and the rows that name him
// must never be left pointing at nothing, and archiving a patient must leave
// nothing alive under their DNI. Each test follows one decision of the admin or
// the front desk through to what the OTHER roles see afterwards:
//
//   A. The admin disables doctors in three situations -- one with an approved
//      appointment ahead, one with only a pending request parked on him, one
//      named only by a pre-visit interview. The desk, the patient and the
//      public pages must agree afterwards.
//   B. The front desk archives a patient who has an approved appointment, a
//      pending request, a session and a calendar link. The desk is told what
//      archiving does to the appointments, the doctor's queue stops listing
//      them, and the patient can no longer get in.
//
// The two K6 fixes (a doctor with an approved appointment ahead is refused; archiving a patient
// asks about and cancels the live appointments and ends the session and the calendar link) are on
// main, so every check below is a plain gate. NOTE: refusing to disable a doctor with an approved
// appointment ahead was a proposal that awaited the owner's answer; if the answer is "allow it",
// that one step is rewritten, nothing else moves.
//
// Port of tests/e2e/journey-j14-disable-doctor-archive-patient.e2e.mjs (2 tests, same titles).
//
// Differences from the node:test original:
// - The app and the desk account come from the worker fixtures (`appOptions` + `journey`); the
//   catalog the admin sets up once for both journeys (the service and four doctors) is a
//   `beforeAll` on a context of the runner.
// - The nested `t.test` subtests are `test.step`s; `waitForFunction` polls are retrying expects.
import {
  test, expect, runFixture, signInStaff, DESK, ADMIN_USER, ADMIN_PASS,
} from "./lib/journey.mjs";
import { queryUser } from "./lib/fixtures.mjs";

const SERVICE_ID = "diagnostico-por-imagenes-j14";
const SERVICE = {
  nombre: "Diagnóstico por imágenes J14", centro: "maternity-center", modo: "solicitud", cluster: "diagnostico",
  horario: "Según el médico",
  agenda: { franjas: [{ dias: [0, 1, 2, 3, 4, 5, 6], desde: "10:00", hasta: "12:00", cada_min: 30 }], excepciones: [] },
  confirmar_reinicio_agendas: true,
};
const doctorForm = (nombre_pila, apellido) => ({ nombre_pila, apellido, especialidad: "Diagnóstico por imágenes", centro: "maternity-center" });
const IRENE = { id: "irene-imagen", nombre: "Irene Imagen", form: doctorForm("Irene", "Imagen") };
const SOFIA = { id: "sofia-segundo", nombre: "Sofía Segundo", form: doctorForm("Sofía", "Segundo") };
// Another specialty: not in the service's pool, so the queue never parks a request on him by itself --
// the pre-visit interview below is the ONLY row that names him.
const TOMAS = { id: "tomas-tercero", nombre: "Tomás Tercero", form: { nombre_pila: "Tomás", apellido: "Tercero", especialidad: "Psiquiatría", centro: "maternity-center" } };
// Never disabled: the doctor of the patient-archive journey, so the two journeys do not depend on each other.
const VERA = { id: "vera-cuarta", nombre: "Vera Cuarta", form: doctorForm("Vera", "Cuarta") };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j14" },
  journey: { staff: [DESK] },
});

// The admin sets the catalog up once for both journeys: the service and four doctors.
test.beforeAll(async ({ browser, app }) => {
  const context = await browser.newContext({ baseURL: app.baseUrl });
  try {
    const page = await context.newPage();
    await signInStaff(page, ADMIN_USER, ADMIN_PASS);
    const service = await page.request.post("/api/admin/content/services", { data: SERVICE });
    expect(service.status(), await service.text()).toBe(200);
    for (const doctor of [IRENE, SOFIA, TOMAS, VERA]) {
      const created = await page.request.post("/api/admin/content/doctors", { data: doctor.form });
      expect(created.status(), await created.text()).toBe(200);
      expect((await created.json()).item.id).toBe(doctor.id);
    }
  } finally {
    await context.close();
  }
});

/** Opens a doctor's card as the admin and presses "Deshabilitar", accepting the browser confirmation. */
async function disableFromCard(page, doctor) {
  await page.goto(`/admin/doctors?item=${doctor.id}`, { waitUntil: "load" });
  const button = page.locator("#cnt-del");
  await expect(button).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  const answered = page.waitForResponse((r) =>
    new URL(r.url()).pathname === `/api/admin/content/doctors/${doctor.id}` && r.request().method() === "DELETE");
  await button.click();
  const response = await answered;
  // The toast carries the outcome in the panel's own words.
  const toast = page.locator("#toast");
  await expect(toast).toHaveText(/\S/);
  return { status: response.status(), toast: (await toast.innerText()).trim() };
}

const doctorsNow = async (app) => Object.fromEntries((await runFixture(app, "doctors")).doctors.map((d) => [d.id, d]));

test("J14 — disabling a doctor: the desk, the patient and the public pages agree afterwards", async ({ app, role }) => {
  test.setTimeout(180_000);
  const admin = await role();
  const desk = await role();
  const reader = await role();
  const run = {};

  await test.step("1. patients hold their appointments with three doctors", async () => {
    await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
    // P1: approved appointment tomorrow with Irene. P2: pending request parked on Sofía.
    run.p1 = await runFixture(app, "seed-visit", { spec: {
      dni: "30333001", nombre: "Pilar", apellido: "Aprobada", doctor_id: IRENE.id, doctor_name: IRENE.nombre,
      servicio_id: SERVICE_ID, centro: "maternity-center", when: "+1", at: "10:00", account: true, interview: false } });
    run.p2 = await runFixture(app, "seed-visit", { spec: {
      dni: "30333002", nombre: "Paula", apellido: "Pendiente", doctor_id: SOFIA.id, doctor_name: SOFIA.nombre,
      servicio_id: SERVICE_ID, centro: "maternity-center", pending: true, account: true } });
    // Tomás is named only by a pre-visit interview -- no booking points at him.
    run.interview = (await runFixture(app, "seed-interview", { spec: { doctor_id: TOMAS.id, nombre: "Tomás", apellido: "Visitante" } })).interview_id;
    const listed = await doctorsNow(app);
    for (const doctor of [IRENE, SOFIA, TOMAS]) expect(listed[doctor.id]?.borrado, `${doctor.nombre} starts active`).toBe(false);
    // The precondition of everything below, read back instead of assumed: who holds what.
    const approved = (await runFixture(app, "requests", { dni: "30333001" })).requests[0];
    expect([approved.estado, approved.medico_asignado_id], "P1 holds an approved appointment with Irene").toEqual(["aprobada", IRENE.id]);
    const waiting = (await runFixture(app, "requests", { dni: "30333002" })).requests[0];
    expect([waiting.estado, waiting.medico_asignado_id], "P2 waits with her request parked on Sofía").toEqual(["pendiente", SOFIA.id]);
  });

  await test.step("2. a doctor with an approved appointment ahead is not disabled; the admin is told why", async () => {
    const { status, toast } = await disableFromCard(admin.page, IRENE);
    expect(status, "the server refuses while an approved appointment is ahead").toBe(409);
    expect(toast, "the admin reads how many appointments are ahead").toMatch(/turnos? por venir/);
    const listed = await doctorsNow(app);
    expect(listed[IRENE.id].borrado, "the doctor is still active").toBe(false);
    const publicPage = await reader.page.goto(`/doctor/${IRENE.id}`, { waitUntil: "load" });
    expect(publicPage.status(), "the doctor's public page still opens").toBe(200);
  });

  await test.step("3. a doctor with only a pending request parked on him is disabled, and the desk gets the request back", async () => {
    const { status, toast } = await disableFromCard(admin.page, SOFIA);
    expect(status, toast).toBe(200);
    expect(toast).toMatch(/Deshabilitado/);
    const listed = await doctorsNow(app);
    expect(listed[SOFIA.id]?.borrado, "the doctor is disabled, not gone").toBe(true);
    const released = (await runFixture(app, "requests", { dni: "30333002" })).requests[0];
    expect(released.estado, "the patient's request is untouched and still waiting").toBe("pendiente");
    expect(released.medico_asignado_id, "…and parked on nobody").toBeNull();
    expect(released.medico_asignado, "…not even by name").toBeNull();

    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row").first()).toBeVisible();
    await page.locator("#list .sol-row", { hasText: "Paula Pendiente" }).first().click();
    const detail = page.locator("aside.sol-det[data-id]");
    await expect(detail).toBeVisible();
    const doctorBox = detail.locator(".cmb:has(.med-sel)");
    await doctorBox.locator(".cmb-in").focus();
    const offered = async () => (await doctorBox.locator(".cmb-it").allInnerTexts()).map((s) => s.split(" — ")[0].trim());
    await expect.poll(async () => (await offered()).length, { message: "the desk still has doctors to choose from" }).toBeGreaterThan(0);
    expect(await offered(), "the disabled doctor is not offered to the desk").not.toContain(SOFIA.nombre);
    await expect(doctorBox.locator("input.med-sel"), "the request is no longer parked on the disabled doctor").not.toHaveValue(SOFIA.nombre);

    await reader.page.goto(`/doctor/${SOFIA.id}`, { waitUntil: "load" });
    expect(new URL(reader.page.url()).pathname.startsWith("/doctor/"),
      "the public page of a disabled doctor sends the visitor to the directory").toBe(false);
    await expect(reader.page.locator("body"), "…and the directory does not list the doctor").not.toContainText(SOFIA.nombre, { useInnerText: true });
    // Opening the request lets the queue suggest a doctor of the service (it may store that
    // suggestion): whoever it is, it is not the disabled one.
    const { requests } = await runFixture(app, "requests", { dni: "30333002" });
    expect(requests[0].medico_asignado_id, "…and the request is not back on the disabled doctor").not.toBe(SOFIA.id);

    // The patient's side: her account still shows the request, and says it is WAITING for a professional.
    // (The account names a doctor on any card as soon as one is assigned -- static/platform/account-requests.mjs --
    // so the absent doctor line proves the disabled doctor was released from the waiting request.)
    const patient = await role({ session: run.p2.session });
    await patient.page.goto("/account#turnos", { waitUntil: "load" });
    const mine = patient.page.locator("#ms-host");
    await expect(mine.getByText("Diagnóstico por imágenes J14").first(), "her request is still on her account").toBeVisible();
    await expect(mine.locator(".mt-sec-note").first(), "…under the heading of requests still waiting for the hospital")
      .toContainText("apenas el hospital confirme", { useInnerText: true });
    await expect(mine.locator(".pt-medico"), "…with no doctor line at all").toHaveCount(0);
    await expect(mine, "…and no longer names the disabled doctor").not.toContainText(SOFIA.nombre, { useInnerText: true });
    expect(patient.errors).toEqual([]);
  });

  await test.step("4. a doctor named only by an interview is disabled, never deleted", async () => {
    const { status } = await disableFromCard(admin.page, TOMAS);
    expect(status).toBe(200);
    const listed = await doctorsNow(app);
    expect(listed[TOMAS.id]?.borrado, "the doctor row is kept, flagged disabled").toBe(true);
    const interview = await runFixture(app, "doctor-of-interview", { interview: String(run.interview) });
    expect(interview.doctor_id).toBe(TOMAS.id);
    expect(listed[interview.doctor_id], "the interview still points at a doctor that exists").toBeTruthy();
  });

  for (const [who, label] of [[admin, "admin"], [desk, "desk"], [reader, "reader"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});

test("J14 — archiving a patient with live appointments: the desk is asked, the doctor's queue and the patient follow", async ({ app, role }) => {
  const desk = await role();
  const run = {};
  let patient;

  await test.step("1. a patient has an approved appointment, a pending request, a session and a calendar link", async () => {
    run.approved = await runFixture(app, "seed-visit", { spec: {
      dni: "30333010", nombre: "Alba", apellido: "Archivada", doctor_id: VERA.id, doctor_name: VERA.nombre,
      servicio_id: SERVICE_ID, centro: "maternity-center", when: "+2", at: "11:00", account: true, interview: false } });
    run.pending = await runFixture(app, "seed-visit", { spec: {
      dni: "30333010", nombre: "Alba", apellido: "Archivada", servicio_id: SERVICE_ID, centro: "maternity-center",
      pending: true, account: false } });
    patient = await role({ session: run.approved.session });
    const page = patient.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    await expect(page.locator("#ms-host").getByText(VERA.nombre, { exact: true }).first()).toBeVisible();
    await page.locator("#ms-host button.ms-feed-link").click();
    await expect(page.locator("#ms-cal-url")).toBeVisible();
    run.feed = await page.locator("#ms-cal-url").inputValue();
    expect(run.feed, "the account offers the calendar link").toMatch(/\/feed\/appointments\/[\w-]+\.ics$/);
    run.feedPath = new URL(run.feed, app.baseUrl).pathname;
    const feed = await patient.context.request.get(run.feedPath);
    expect(feed.status(), "the calendar link works while the patient is active").toBe(200);
    const me = await patient.context.request.get("/api/account/me");
    expect(me.status(), "the patient's session works while the patient is active").toBe(200);
  });

  await test.step("2. the desk archives the patient from the patient's card", async () => {
    const page = desk.page;
    const dialogs = [];
    page.on("dialog", async (dialog) => { dialogs.push(dialog.message()); await dialog.accept(); });
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/pacientes?patient=30333010", { waitUntil: "load" });
    // The data form (and the archive button in it) is behind «Editar datos».
    await page.click("#pac-edit");
    const archive = page.locator("#pac-arch");
    await expect(archive).toHaveText("Archivar");
    await archive.click();
    // A successful archive returns to the roster (the toast goes with the page): wait for that,
    // then read the patient back from the database.
    await page.waitForURL((url) => url.pathname === "/admin/pacientes" && !url.searchParams.has("patient"));
    run.dialogs = dialogs;
    run.rows = (await runFixture(app, "requests", { dni: "30333010" })).requests;
    const stored = await queryUser(app.dbName, "30333010");
    expect(stored.found).toBe(true);
    expect(stored.activo, "the patient is archived").toBe(false);
  });

  await test.step("3. the desk was told what archiving does to the live appointments, and they are cancelled", async () => {
    expect(run.dialogs.some((m) => /turnos? por venir/.test(m)),
      `the desk was asked about the appointments ahead (saw: ${JSON.stringify(run.dialogs)})`).toBe(true);
    expect(run.rows.map((r) => r.estado).sort(), "nothing is left alive under an archived patient's DNI")
      .toEqual(["cancelada", "cancelada"]);
  });

  await test.step("4. the patient can no longer get in, and the calendar link stops", async () => {
    const me = await patient.context.request.get("/api/account/me", { failOnStatusCode: false });
    expect([401, 403], `the patient's session is gone (got ${me.status()})`).toContain(me.status());
    const feed = await patient.context.request.get(run.feedPath, { failOnStatusCode: false });
    expect(feed.status(), "the calendar link of an archived patient is dead").toBe(404);
  });

  expect(desk.errors, "no uncaught page errors for the desk").toEqual([]);
});
