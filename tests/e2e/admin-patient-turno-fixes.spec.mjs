// tests/e2e/admin-patient-turno-fixes.spec.mjs -- the patient's ficha undoes a desk mistake (the product owner, 2026-11-05:
// "if he did not come, a button to change it, in case it was pressed by accident; if it was cancelled, reschedule
// it or bring it back" -- translated).
//
//   1. A «No vino» marked by mistake goes back to «Atendió» from the ficha: the same attendance write as the day
//      queue, which replaces the earlier mark.
//   2. A cancelled turno is restored at its own time while that time is still free and ahead: the turno is
//      approved again (same doctor, same time) and the patient gets the confirmation mail again. Once the desk
//      gives that time to somebody else, «Restaurar» is gone and only «Reprogramar» is left.
//   3. «Reprogramar» opens the desk's «Crear turno» on the same patient, service and doctor: only the time is
//      missing, and the new turno is approved with them.
//
// The mail provider is the journey's local receiver (lib/journey.mjs, `journey: { sink: true }`). No network.
import { test, expect, runFixture, seedPatient, signInStaff, DESK } from "./lib/journey.mjs";

const SERVICE = "electrocardiograma-hospital";
const CENTER = "sampleton-hospital";
const DOCTOR = { id: "levin-camila", name: "Camila Levin" };
const PATIENT = { dni: "30310001", nombre: "Rita", apellido: "Arreglada", contacto: "+5492254310001", email: "rita.arreglada@example.invalid" };
const OTHER = { dni: "30310002", nombre: "Otto", apellido: "Ocupante", contacto: "+5492254310002" };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_patient_turno_fixes" },
  journey: { sink: true, staff: [DESK] },
});

test.describe.configure({ mode: "serial" });

const row = (page, id) => page.locator(`[data-pt-fila="${id}"]`);

async function openFicha(page) {
  await page.goto(`/admin/pacientes?patient=${PATIENT.dni}`, { waitUntil: "load" });
  await expect(page.locator("#pat-turnos")).toBeVisible();
}

/** Clicks `selector` inside the turno's row and resolves with the status of the POST it sends to `suffix`. */
async function press(page, id, selector, suffix) {
  const answered = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/admin/requests/${id}/${suffix}`
    && r.request().method() === "POST");
  await row(page, id).locator(selector).click();
  return (await answered).status();
}

test("the ficha undoes «No vino», restores a cancelled turno while its time is free, and reschedules it", async ({ app, role, sink }) => {
  // Four flows and a dozen fixture processes in one test: under a loaded machine the run took 250 s (2026-11-05).
  test.setTimeout(420_000);
  await seedPatient(app, PATIENT);
  await seedPatient(app, OTHER);
  const absent = (await runFixture(app, "seed-visit", { spec: {
    dni: PATIENT.dni, nombre: PATIENT.nombre, apellido: PATIENT.apellido, contacto: PATIENT.contacto,
    doctor_id: DOCTOR.id, doctor_name: DOCTOR.name, servicio_id: SERVICE, centro: CENTER,
    when: "-1", at: "10:00", interview: false, asistencia: "ausente" } })).request_id;
  const request = async (id) => (await runFixture(app, "request", { id: String(id) })).request;
  const mailsTo = (pattern) => sink.mails.filter((m) => m.to === PATIENT.email && pattern.test(m.subject));

  const desk = await role();
  await signInStaff(desk.page, DESK.usuario, DESK.password);
  const page = desk.page;

  await test.step("1. «No vino» marked by mistake goes back to «Atendió»", async () => {
    await openFicha(page);
    await expect(row(page, absent).locator(".pat-appt-st")).toHaveText("No vino");
    await expect(row(page, absent).locator('[data-pt-asis][data-valor="ausente"]'), "the mark it already has is not offered").toHaveCount(0);
    expect(await press(page, absent, '[data-pt-asis][data-valor="presente"]', "attendance")).toBe(200);
    await expect(row(page, absent).locator(".pat-appt-st")).toHaveText("Vino");
    await expect(row(page, absent).locator("[data-pt-asis]"), "a corrected «Vino» has nothing left to press").toHaveCount(0);
    const fixed = await request(absent);
    expect(fixed.asistencia).toBe("presente");
    expect(fixed.estado, "a mark is not a cancellation").toBe("aprobada");
  });

  // A free time of the doctor, the way «Crear turno» offers it to the desk.
  const free = (await (await desk.context.request.get(
    `/api/admin/appointment-slots?servicio_id=${SERVICE}&medico=${encodeURIComponent(DOCTOR.name)}`)).json()).libres;
  const slot = free[Math.min(3, free.length - 1)];
  expect(slot, "the desk is offered a free time").toBeTruthy();
  const given = await desk.context.request.post("/api/admin/appointments", {
    data: { dni: PATIENT.dni, servicio_id: SERVICE, dia: slot.dia, hora: slot.hora, medico: DOCTOR.name } });
  expect(given.status()).toBe(200);
  const turno = (await given.json()).id;
  const before = await request(turno);
  await expect.poll(() => mailsTo(/confirmado/).length, { message: "the first confirmation" }).toBe(1);

  await test.step("2. a cancelled turno comes back at its own time while that time is free", async () => {
    await openFicha(page);
    await row(page, turno).locator("[data-pt-cancel]").click();
    expect(await press(page, turno, "[data-pt-cancel-yes]", "cancel")).toBe(200);
    await expect(row(page, turno).locator(".pat-appt-st")).toHaveText("Cancelado");
    await expect(row(page, turno).locator("[data-pt-reprog]")).toBeVisible();
    expect(await press(page, turno, "[data-pt-restore]", "restore")).toBe(200);
    await expect(row(page, turno).locator(".pat-appt-st")).toHaveText("Confirmado");
    await expect(row(page, turno).locator("[data-pt-cancel]"), "a restored turno can be cancelled again").toBeVisible();
    const back = await request(turno);
    expect(back.estado).toBe("aprobada");
    expect(back.turno_dt, "same time").toBe(before.turno_dt);
    expect(back.medico_asignado_id, "same doctor").toBe(DOCTOR.id);
    await expect.poll(() => mailsTo(/confirmado/).length, { message: "the patient is told again, as on any approval" }).toBe(2);
  });

  await test.step("2b. once the time is given to somebody else, only «Reprogramar» is left", async () => {
    await row(page, turno).locator("[data-pt-cancel]").click();
    expect(await press(page, turno, "[data-pt-cancel-yes]", "cancel")).toBe(200);
    const taken = await desk.context.request.post("/api/admin/appointments", {
      data: { dni: OTHER.dni, servicio_id: SERVICE, dia: slot.dia, hora: slot.hora, medico: DOCTOR.name } });
    expect(taken.status(), "the freed time goes to another patient").toBe(200);
    await openFicha(page);
    await expect(row(page, turno).locator(".pat-appt-st")).toHaveText("Cancelado");
    await expect(row(page, turno).locator("[data-pt-restore]")).toHaveCount(0);
    await expect(row(page, turno).locator("[data-pt-reprog]")).toBeVisible();
    const refused = await desk.context.request.post(`/api/admin/requests/${turno}/restore`, { data: {}, failOnStatusCode: false });
    expect(refused.status(), "the server refuses it too").toBe(409);
    expect((await request(turno)).estado).toBe("cancelada");
  });

  await test.step("3. «Reprogramar» opens «Crear turno» on the same patient, service and doctor", async () => {
    await row(page, turno).locator("[data-pt-reprog]").click();
    const modal = page.locator("#ct-modal");
    await expect(modal).toBeVisible();
    await expect(modal.locator("#ct-pac"), "the patient is already chosen").toHaveCount(0);
    await expect(modal).toContainText(`${PATIENT.apellido}, ${PATIENT.nombre}`);
    await expect(modal).toContainText(DOCTOR.name);
    await expect(modal.locator(".ct-prop-cuando").first(), "only the time is left").toBeVisible();
    const booked = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/appointments" && r.request().method() === "POST");
    await modal.locator("#ct-ok").click();
    const answer = await booked;
    expect(answer.status()).toBe(200);
    const sent = answer.request().postDataJSON();
    expect(sent.dni).toBe(PATIENT.dni);
    expect(sent.servicio_id).toBe(SERVICE);
    expect(sent.medico).toBe(DOCTOR.name);
    const fresh = await request((await answer.json()).id);
    expect(fresh.estado).toBe("aprobada");
    expect(fresh.medico_asignado_id).toBe(DOCTOR.id);
    await modal.locator("#ct-listo").click();
    await expect(modal).toBeHidden();
    await expect(row(page, fresh.id).locator(".pat-appt-st"), "the ficha shows the new turno").toHaveText("Confirmado");
  });

  expect(desk.errors, "no page error").toEqual([]);
});
