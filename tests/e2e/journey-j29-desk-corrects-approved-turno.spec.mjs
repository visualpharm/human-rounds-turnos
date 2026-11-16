// tests/e2e/journey-j29-desk-corrects-approved-turno.spec.mjs -- J29 ("After the desk approved an
// appointment, the day goes on: the patient did not come, the desk marked it by mistake, the patient calls
// to change the doctor and the time, her phone or her mail was wrong") cross-role journey acceptance.
//
// The roles: the patient (public site and her account), the front desk in the panel («Pacientes de hoy»,
// «Pacientes», «Crear turno»), the doctor in his own «Pacientes de hoy», the administrator's report.
//
// What the journey pins, and why (each one is a rule of the product, not a screen):
//   1. «No vino» has a price: with the installation's no-show limit (3) the patient can no longer ask for a
//      turno on the web (the patient endpoint answers 403 «bloqueado_por_ausencias» with the sentence that
//      names the main center); the desk never stops («Never blocks the front desk or staff»).
//      So a mark made by mistake must be undoable: «corregir» → «Atendió» gives her the web back, the
//      doctor sees the corrected mark, the statistics report counts it, and the appointment itself is untouched
//      (still approved, same doctor, same time: a mark is not a cancellation and frees nothing).
//      A turno of a FUTURE day cannot be marked (409 `asistencia_no_elegible`) -- the doctor marks as the day goes.
//   2. The desk moves a patient to another doctor and another time the only way the product has (there is no
//      «move» on an approved turno): it cancels the turno in «Pacientes de hoy» and gives a new one with «Crear
//      turno». The old time goes back to the public wizard, the new one is taken, the patient is told twice
//      (cancelled at the old time; confirmed at the NEW time, with the new doctor, by mail AND by WhatsApp), the
//      day-before reminder is for the new turno only, the new doctor's «Pacientes de hoy» lists her and the old
//      doctor's does not, and the pre-visit interview of the new turno belongs to the new doctor
//      (docs/architecture/entrevista-previa.md: the interview is bound to the request's doctor).
//   3. Correcting the patient after approval (patient_contact.py, hospital decisions 2026-10-23/25): the desk
//      fixes the e-mail and the phone of her ACCOUNT in «Pacientes»; mail follows the account at once
//      (the reminder goes to the NEW address, nothing to the old one), WhatsApp keeps going to the phone given
//      for the turno (it is not overwritten, and staff see both numbers, each said whose it is). The desk's
//      `patient-data` correction (names/birth/sex, what HSI asks for) rewrites the turno's row and never the
//      name the patient wrote in her own account; an empty value is refused instead of erasing.
//
// External services: the mail provider and the WhatsApp gateway are a local receiver (lib/journey.mjs,
// `journey: { sink: true }`). No key, no network.
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  offeredSlots, hasSlot, expectOfferedAgain, sendRequest, dayLabel,
  DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS, showSlot,
} from "./lib/journey.mjs";
import { pickQueueDoctor } from "./lib/queue-doctor.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const CENTER = "sampleton-hospital";
const DOCTOR_A = { id: "levin-camila", name: "Camila Levin", usuario: "e2e-dr-levin", password: LOGIN_PHRASE };
const DOCTOR_B = { id: "ruiz-sergio", name: "Sergio Ruiz", usuario: "e2e-dr-ruiz", password: LOGIN_PHRASE };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_j29" },
  journey: {
    sink: true,
    staff: [
      DESK,
      { usuario: DOCTOR_A.usuario, password: DOCTOR_A.password, rol: "medico", medico_id: DOCTOR_A.id },
      { usuario: DOCTOR_B.usuario, password: DOCTOR_B.password, rol: "medico", medico_id: DOCTOR_B.id },
    ],
    intakeDoctors: [DOCTOR_A.id, DOCTOR_B.id],
  },
});

test.describe.configure({ mode: "serial" });

const SERVICE_SEEDED = "electrocardiograma-hospital"; // one of Camila Levin's four services

/** The desk's «Pacientes de hoy» (today), narrowed to the doctor so that only her rows are on the page. */
async function openQueueOf(page, doctor) {
  await page.goto("/admin/cola", { waitUntil: "load" });
  await pickQueueDoctor(page, doctor.id);
  await expect(page.locator("[data-cola-row]").first()).toBeVisible({ timeout: 10_000 });
}

/** Attendance lives in the appointment card the patient's name opens (the product owner, 2026-11-09), not in the row. */
async function openCard(page, id) {
  await page.locator(`[data-cola-row="${id}"] .queue-pat-link`).click();
  const card = page.locator(`.queue-card:has([data-cola-asis-box="${id}"])`);
  await expect(card).toBeVisible();
  return card;
}

async function closeCard(page) {
  await page.locator(".queue-card .solmod-x").click();
  await expect(page.locator(".queue-card")).toHaveCount(0);
}

/** The card of that turno shows the recorded mark. */
async function expectMarked(page, id, text, message) {
  const card = await openCard(page, id);
  await expect(card.locator(".queue-attendance-marked"), message).toContainText(text, { useInnerText: true });
  await closeCard(page);
}

/** Presses «No vino» / «Atendió» in the card (on a marked turno, «corregir» first) and resolves with the server's status. */
async function markAttendance(page, id, valor) {
  const card = await openCard(page, id);
  const fix = card.locator(`[data-corregir="${id}"]`);
  if (await fix.count()) await fix.click();
  const answered = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/admin/requests/${id}/attendance`
    && r.request().method() === "POST");
  await card.locator(`[data-asis="${id}"][data-valor="${valor}"]`).click();
  const status = (await answered).status();
  await closeCard(page);
  return status;
}

/**
 * The patient picks the first time of the grid's second day (today's first time may be minutes away or already
 * past, which would make the answer depend on the hour the suite runs) and sends: resolves with the answer of
 * POST /api/requests.
 */
async function askForTurno(page) {
  const slots = await offeredSlots(page, SERVICE_ID);
  const slot = slots.find((s) => s.dia === [...new Set(slots.map((x) => x.dia))][1]);
  expect(slot, "the wizard offers a time").toBeTruthy();
  await (await showSlot(page, slot)).click();
  await expect(page.locator("#wz-send")).toBeVisible();
  const answered = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/requests" && r.request().method() === "POST");
  await page.locator("#wz-send").click();
  const response = await answered;
  return { status: response.status(), json: await response.json().catch(() => ({})) };
}

const PATIENT_NO_SHOW = {
  dni: "30290001", nombre: "Nilda", apellido: "Faltadora", email: "nilda.faltadora@example.invalid", contacto: "+5492254290001",
};

test("J29 — three «No vino» close the web to a patient, a mistake is undone by «corregir», and a mark never frees the turno", async ({ app, role }) => {
  test.setTimeout(240_000);
  const seeded = await runFixture(app, "seed-visit", { spec: {
    dni: PATIENT_NO_SHOW.dni, nombre: PATIENT_NO_SHOW.nombre, apellido: PATIENT_NO_SHOW.apellido, account: true,
    contacto: PATIENT_NO_SHOW.contacto, email: PATIENT_NO_SHOW.email,
    doctor_id: DOCTOR_A.id, doctor_name: DOCTOR_A.name, servicio_id: SERVICE_SEEDED, centro: CENTER,
    when: "today", at: "09:00", interview: false } });
  const ids = [seeded.request_id];
  for (const at of ["09:10", "09:20"]) {
    ids.push((await runFixture(app, "seed-visit", { spec: {
      dni: PATIENT_NO_SHOW.dni, nombre: PATIENT_NO_SHOW.nombre, apellido: PATIENT_NO_SHOW.apellido,
      contacto: PATIENT_NO_SHOW.contacto, doctor_id: DOCTOR_A.id, doctor_name: DOCTOR_A.name,
      servicio_id: SERVICE_SEEDED, centro: CENTER, when: "today", at, interview: false } })).request_id);
  }
  const tomorrow = (await runFixture(app, "seed-visit", { spec: {
    dni: PATIENT_NO_SHOW.dni, nombre: PATIENT_NO_SHOW.nombre, apellido: PATIENT_NO_SHOW.apellido,
    contacto: PATIENT_NO_SHOW.contacto, doctor_id: DOCTOR_A.id, doctor_name: DOCTOR_A.name,
    servicio_id: SERVICE_SEEDED, centro: CENTER, when: "+1", at: "10:00", interview: false } })).request_id;
  const before = await Promise.all(ids.map(async (id) => (await runFixture(app, "request", { id: String(id) })).request));

  const patient = await role({ session: seeded.session });
  const desk = await role();
  const doctor = await role();
  const admin = await role();
  await signInStaff(desk.page, DESK.usuario, DESK.password);
  await signInStaff(doctor.page, DOCTOR_A.usuario, DOCTOR_A.password);
  await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
  const asistencia = async (id) => (await runFixture(app, "request", { id: String(id) })).request.asistencia;

  await test.step("1. the desk marks two «No vino» from the queue: both are stored, and she can still ask for a turno on the web", async () => {
    await openQueueOf(desk.page, DOCTOR_A);
    for (const id of ids.slice(0, 2)) {
      expect(await markAttendance(desk.page, id, "ausente"), "the desk's mark is accepted").toBe(200);
      await expectMarked(desk.page, id, "No vino");
    }
    expect(await Promise.all(ids.map(asistencia)), "two absences stored, the third turno still unmarked")
      .toEqual(["ausente", "ausente", null]);
    const asked = await askForTurno(patient.page);
    expect(asked.status, "two absences do not close the web").toBe(200);
    await expect(patient.page.locator("#svc-title")).toHaveText("Solicitud enviada");
    // she changes her mind: the request is cancelled from her account, so the next attempts are not stopped by the one-open-request rule
    const pending = (await runFixture(app, "requests", { dni: PATIENT_NO_SHOW.dni })).requests.filter((q) => q.estado === "pendiente");
    expect(pending.length, "exactly the request she just sent is waiting").toBe(1);
    const cancelled = await patient.context.request.post(`/api/requests/${pending[0].id}/cancel`, { data: {} });
    expect(cancelled.status(), "she cancels it herself").toBe(200);
  });

  await test.step("2. the third «No vino» closes the web to her, with the sentence that sends her to the main center", async () => {
    expect(await markAttendance(desk.page, ids[2], "ausente")).toBe(200);
    const asked = await askForTurno(patient.page);
    expect(asked.status, "the third absence is refused").toBe(403);
    expect(asked.json.error).toBe("bloqueado_por_ausencias");
    expect(asked.json.faltas).toBe(3);
    expect(asked.json.limite).toBe(3);
    expect(asked.json.detalle, "the sentence names the place where the desk lifts it").toMatch(
      /^Por tus ausencias recientes, este turno no se puede pedir por la web\. Acercate a la admisión y te ayudamos: .+\.$/);
    await expect(patient.page.locator("#svc-form-host"), "the wizard says it to her").toContainText(asked.json.detalle, { useInnerText: true });
    expect(await runFixture(app, "requests", { dni: PATIENT_NO_SHOW.dni }).then((r) =>
      r.requests.filter((q) => q.estado === "pendiente").length), "no request was created").toBe(0);

    // The block is for the web only: when she walks in or phones, the desk gives her a turno as usual.
    const free = (await (await desk.context.request.get(
      `/api/admin/appointment-slots?servicio_id=${SERVICE_SEEDED}&medico=${encodeURIComponent(DOCTOR_A.name)}`)).json()).libres;
    const slot = free[Math.min(10, free.length - 1)];
    expect(slot, "the desk is offered a free time").toBeTruthy();
    const given = await desk.context.request.post("/api/admin/appointments", {
      data: { dni: PATIENT_NO_SHOW.dni, servicio_id: SERVICE_SEEDED, dia: slot.dia, hora: slot.hora, medico: DOCTOR_A.name },
      failOnStatusCode: false });
    expect(given.status(), "the desk is not stopped by her absences").toBe(200);
  });

  await test.step("3. the desk undoes one mark with «corregir» → «Atendió»: the doctor sees it and she can ask again", async () => {
    expect(await markAttendance(desk.page, ids[0], "presente"), "the correction is accepted").toBe(200);
    await expectMarked(desk.page, ids[0], "Atendió");
    expect(await Promise.all(ids.map(asistencia))).toEqual(["presente", "ausente", "ausente"]);

    await openQueueOf(doctor.page, DOCTOR_A);
    await expectMarked(doctor.page, ids[0], "Atendió", "the doctor sees the corrected mark");
    await expectMarked(doctor.page, ids[1], "No vino");

    const asked = await askForTurno(patient.page);
    expect(asked.status, "two absences left: the web is open again").toBe(200);
    await expect(patient.page.locator("#svc-title")).toHaveText("Solicitud enviada");
  });

  await test.step("4. a mark frees nothing: the turnos are still approved, with the same doctor and the same time", async () => {
    const after = await Promise.all(ids.map(async (id) => (await runFixture(app, "request", { id: String(id) })).request));
    for (const [i, row] of after.entries()) {
      expect(row.estado, `turno ${i + 1} is still approved`).toBe("aprobada");
      expect(row.turno_dt, `turno ${i + 1} keeps its time`).toBe(before[i].turno_dt);
      expect(row.medico_asignado_id, `turno ${i + 1} keeps its doctor`).toBe(DOCTOR_A.id);
    }
  });

  await test.step("5. the statistics report counts the marks as corrected (1 attended, 2 absent)", async () => {
    const month = artParts(new Date().toISOString()).date.slice(0, 7);
    const report = await admin.context.request.get(`/api/admin/reports/statistics?mes=${month}`);
    expect(report.status()).toBe(200);
    const { kpis } = await report.json();
    expect(kpis.presentes, "attended").toBe(1);
    expect(kpis.ausentes, "absent").toBe(2);
  });

  await test.step("6. a turno of a future day cannot be marked", async () => {
    const refused = await desk.context.request.post(`/api/admin/requests/${tomorrow}/attendance`,
      { data: { asistencia: "ausente" }, failOnStatusCode: false });
    expect(refused.status()).toBe(409);
    expect((await refused.json()).error).toBe("asistencia_no_elegible");
    expect(await asistencia(tomorrow), "nothing was stored").toBeNull();
  });

  for (const [who, label] of [[patient, "the patient"], [desk, "the desk"], [doctor, "the doctor"], [admin, "the admin"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

const PATIENT_MOVE = {
  dni: "30290011", nombre: "Mauro", apellido: "Muevo", email: "mauro.muevo@example.invalid", contacto: "+5492254290011",
};
const WORKING_B = { dni: "30290012", nombre: "Berta", apellido: "Presente" }; // gives doctor B a turno today: his group is on the desk's queue
const WORKING_A = { dni: "30290013", nombre: "Aldo", apellido: "Agenda" }; // keeps doctor A's «Mis turnos» from being empty: a missing patient is then a real absence

test("J29 — the patient phones to change doctor and time: the desk cancels and gives a new turno, and everything follows the new one", async ({ app, sink, role }) => {
  test.setTimeout(300_000);
  const mailsTo = (to, pattern) => sink.mails.filter((m) => m.to === to && pattern.test(m.subject));
  const waTo = (pattern) => sink.whatsapp.filter((m) => String(m.chatId) === `${PATIENT_MOVE.contacto.replace(/\D/g, "")}@c.us` && pattern.test(m.text));
  const account = await seedPatient(app, PATIENT_MOVE);
  await runFixture(app, "seed-visit", { spec: {
    ...WORKING_B, doctor_id: DOCTOR_B.id, doctor_name: DOCTOR_B.name, servicio_id: SERVICE_ID, centro: CENTER,
    when: "today", at: "09:00", interview: false } });
  await runFixture(app, "seed-visit", { spec: {
    ...WORKING_A, doctor_id: DOCTOR_A.id, doctor_name: DOCTOR_A.name, servicio_id: SERVICE_ID, centro: CENTER,
    when: "+1", at: "11:40", interview: false } }); // (the first test already holds doctor A at 10:00 tomorrow)
  const patient = await role({ session: account.session });
  const stranger = await role();
  const desk = await role();
  const doctorA = await role();
  const doctorB = await role();
  const run = {};
  const slotsNow = () => offeredSlots(stranger.page, SERVICE_ID);

  await test.step("1. she books Cardiología, the desk approves her with doctor A", async () => {
    const days = [...new Set((await slotsNow()).map((s) => s.dia))];
    expect(days.length, "the grid has five days").toBeGreaterThanOrEqual(5);
    run.grid = await slotsNow();
    // The third day of the grid for the first turno and the fourth for the new one: never the first time offered
    // (it can be minutes away), and two different days.
    run.old = run.grid.find((s) => s.dia === days[2]);
    run.next = run.grid.find((s) => s.dia === days[3]);
    await offeredSlots(patient.page, SERVICE_ID);
    await sendRequest(patient.page, run.old);
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    await desk.page.goto("/admin/solicitudes", { waitUntil: "load" });
    const detail = await openRequest(desk.page, { hasText: `${PATIENT_MOVE.nombre} ${PATIENT_MOVE.apellido}` });
    await doctorCombo(detail).pick(DOCTOR_A.name);
    await expect(detail.locator("input.turno")).toHaveValue(`${run.old.dia}T${run.old.hora}`);
    await approve(desk.page, detail);
    const row = (await runFixture(app, "requests", { dni: PATIENT_MOVE.dni })).requests[0];
    run.oldId = row.id;
    run.oldWhen = artParts(row.turno_dt);
    expect(row.medico_asignado_id).toBe(DOCTOR_A.id);
    await expect.poll(() => mailsTo(PATIENT_MOVE.email, /confirmado/).length, { message: "the confirmation of the first turno" }).toBe(1);
    expect(mailsTo(PATIENT_MOVE.email, /confirmado/)[0].text).toContain(run.oldWhen.time);
    expect((await runFixture(app, "interview-of", { request: String(run.oldId) })).interviews.map((i) => i.doctor_id),
      "her pre-visit interview belongs to doctor A").toEqual([DOCTOR_A.id]);
  });

  await test.step("2. the desk cancels the old turno from «Pacientes de hoy»", async () => {
    const page = desk.page;
    await page.goto("/admin/cola", { waitUntil: "load" });
    const search = page.locator("[data-cola-buscar]");
    await expect(search).toBeVisible();
    const line = page.locator("#sec-cola table tbody tr", { hasText: `${PATIENT_MOVE.nombre} ${PATIENT_MOVE.apellido}` }).first();
    await expect(async () => {
      await search.fill(`${parseInt(run.oldWhen.date.slice(8), 10)}/${parseInt(run.oldWhen.date.slice(5, 7), 10)}`);
      await expect(line).toBeVisible({ timeout: 4000 });
    }).toPass({ timeout: 30_000, intervals: [500, 1000, 2000] });
    await line.locator("[data-cola-abrir]").first().click();
    await page.locator("[data-cola-cancel]").click();
    const cancelled = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/admin/requests/${run.oldId}/cancel`
      && r.request().method() === "POST");
    await page.locator("[data-cola-cancel-yes]").click();
    expect((await cancelled).status()).toBe(200);
    await expect.poll(() => mailsTo(PATIENT_MOVE.email, /Se canceló tu turno/).length).toBe(1);
    expect(mailsTo(PATIENT_MOVE.email, /Se canceló tu turno/)[0].text).toContain(run.oldWhen.time);
    await expectOfferedAgain(stranger.page, SERVICE_ID, run.old, "the old time is offered to the public again");
  });

  await test.step("3. the desk gives her a new turno with doctor B (Crear turno)", async () => {
    const page = desk.page;
    await page.goto("/admin/cola", { waitUntil: "load" });
    await expect(page.locator("[data-cola-buscar]")).toBeVisible();
    await page.locator("[data-cola-buscar]").fill(""); // the panel remembers the search of step 2
    // The list narrowed to him (his link in the doctor column), then the title's Crear turno.
    await pickQueueDoctor(page, DOCTOR_B.id);
    const crear = page.locator("#adm-crear-turno");
    await expect(crear).toBeVisible();
    await crear.click();
    await expect(page.locator("#ct-modal")).toBeVisible();
    await page.fill("#ct-pac", PATIENT_MOVE.dni);
    await page.locator(`#ct-pac-sug li[data-dni="${PATIENT_MOVE.dni}"]`).click();
    await page.fill("#ct-svc", "cardio");
    await page.locator(`#ct-svc-sug li[data-svc="${SERVICE_ID}"]`).click();
    await expect(page.locator(".ct-prop-cuando").first()).toBeVisible();
    // Not the proposal (the first free time): the time the patient asked for on the phone, from the calendar.
    const more = page.locator("#ct-vermas");
    await expect(async () => {
      if ((await more.innerText()).trim() === "Ver todos los horarios") await more.click({ timeout: 2000 });
      await expect(page.locator(".ct-cal-grid")).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: 20_000, intervals: [500, 1000] });
    const day = page.locator(`.ct-cal-dia[data-dia="${run.next.dia}"]`);
    if (!(await day.count())) await page.locator('.ct-cal-nav [data-mes="1"]').click();
    await day.click();
    await page.locator(".slot-time", { hasText: new RegExp(`^${run.next.hora}$`) }).click();
    const booked = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/appointments" && r.request().method() === "POST");
    await page.locator("#ct-ok").click();
    expect((await booked).status(), "the panel gives the turno").toBe(200);

    const rows = (await runFixture(app, "requests", { dni: PATIENT_MOVE.dni })).requests;
    expect(rows.map((r) => r.estado), "the new turno is approved, the old one stays cancelled").toEqual(["aprobada", "cancelada"]);
    const [fresh, stale] = rows;
    run.newId = fresh.id;
    run.newWhen = artParts(fresh.turno_dt);
    expect(run.newWhen.local, "the new turno is at the time asked for").toBe(`${run.next.dia}T${run.next.hora}`);
    expect(fresh.medico_asignado_id, "…with the new doctor").toBe(DOCTOR_B.id);
    expect(fresh.medico_asignado).toBe(DOCTOR_B.name);
    expect(fresh.servicio_id).toBe(SERVICE_ID);
    expect(fresh.contacto, "the desk's turno carries the phone of her account").toBe(PATIENT_MOVE.contacto);
    expect(stale.id, "the old turno is the cancelled one").toBe(run.oldId);
    expect(stale.medico_asignado_id, "…and it still names its own doctor").toBe(DOCTOR_A.id);
  });

  await test.step("4. she is told the NEW time, with the new doctor, by mail and by WhatsApp", async () => {
    await expect.poll(() => mailsTo(PATIENT_MOVE.email, /confirmado/).length, { message: "the confirmation mail of the new turno" }).toBe(2);
    const mail = mailsTo(PATIENT_MOVE.email, /confirmado/)[1];
    expect(mail.text, "the mail names the new time").toContain(run.newWhen.time);
    expect(mail.text, "the pre-visit interview of the new turno is offered under the new request").toContain(`/entrevista/${run.newId}?t=`);
    await expect.poll(() => waTo(/quedó confirmado/).length, { message: "the WhatsApp confirmation of the new turno" }).toBe(2);
    const wa = waTo(/quedó confirmado/)[1].text;
    expect(wa, "WhatsApp names the new time").toContain(run.newWhen.time);
    expect(wa, "…and the new doctor").toContain(`*Te atiende:* ${DOCTOR_B.name}`);
    expect(wa, "…and not the old one").not.toContain(DOCTOR_A.name);
    expect(waTo(/quedó confirmado/)[0].text, "(the first one was doctor A's)").toContain(`*Te atiende:* ${DOCTOR_A.name}`);
  });

  await test.step("5. the public wizard: the old time is free again, the new one is taken", async () => {
    await expectOfferedAgain(stranger.page, SERVICE_ID, run.old, "the old time is still offered");
    await expect.poll(async () => hasSlot(await slotsNow(), run.next), { message: "the new time is not offered any more", timeout: 20_000, intervals: [1000] }).toBe(false);
    await patient.page.goto("/account#turnos", { waitUntil: "load" });
    const card = patient.page.locator("#ms-host");
    await expect(card, "her account lists the new doctor").toContainText(DOCTOR_B.name, { useInnerText: true });
    await expect(card, "…at the new time").toContainText(run.newWhen.time, { useInnerText: true });
    await expect(card, "…on the new day").toContainText(dayLabel(run.newWhen), { useInnerText: true });
    await expect(card.getByText("Cancelada por el centro").first(), "the old one reads as cancelled by the centre").toBeVisible();
  });

  await test.step("6. the doctors: «Mis turnos» of the new doctor lists her, the old doctor's does not", async () => {
    await signInStaff(doctorB.page, DOCTOR_B.usuario, DOCTOR_B.password);
    await signInStaff(doctorA.page, DOCTOR_A.usuario, DOCTOR_A.password);
    await doctorB.page.goto("/admin/mis-turnos", { waitUntil: "load" });
    const row = doctorB.page.locator("#sec-mis-turnos .mt-day table tbody tr", { hasText: `${PATIENT_MOVE.nombre} ${PATIENT_MOVE.apellido}` });
    await expect(row, "doctor B's agenda has her once").toHaveCount(1);
    await expect(row).toContainText(run.newWhen.time, { useInnerText: true });
    const queueIds = async (who, date) => {
      const day = await who.context.request.get(`/api/admin/day-queue?dia=${date}`);
      expect(day.status()).toBe(200);
      return (await day.json()).grupos.flatMap((g) => g.turnos || []).map((r) => String(r.id));
    };
    expect(await queueIds(doctorB, run.newWhen.date), "B's day queue lists the new turno").toContain(String(run.newId));
    expect(await queueIds(doctorA, run.oldWhen.date), "A's day queue no longer lists the old turno").not.toContain(String(run.oldId));
    expect(await queueIds(doctorA, run.newWhen.date), "…and never the new one").not.toContain(String(run.newId));
    await doctorA.page.goto("/admin/mis-turnos", { waitUntil: "load" });
    await expect(doctorA.page.locator("#sec-mis-turnos .mt-day table tbody tr", { hasText: `${WORKING_A.nombre} ${WORKING_A.apellido}` }),
      "doctor A's agenda has loaded: it lists his other patient").toHaveCount(1);
    await expect(doctorA.page.locator("#sec-mis-turnos .mt-day table tbody tr", { hasText: PATIENT_MOVE.apellido }),
      "…and not the patient who was moved away").toHaveCount(0);
  });

  await test.step("7. her pre-visit interview follows the new doctor", async () => {
    const { interviews } = await runFixture(app, "interview-of", { request: String(run.newId) });
    expect(interviews.map((i) => i.doctor_id), "the interview of the new turno belongs to doctor B").toEqual([DOCTOR_B.id]);
    await doctorB.page.goto("/admin/my-patients", { waitUntil: "load" });
    const roster = doctorB.page.locator("#sec-mis-pacientes table tbody tr", { hasText: `${PATIENT_MOVE.nombre} ${PATIENT_MOVE.apellido}` });
    await expect(roster.locator(".dp-iv-link"), "B's roster links to the interview of the new turno")
      .toHaveAttribute("href", `/admin/consult?interview=${interviews[0].id}`);
  });

  await test.step("8. the reminder of the day before is for the new turno only", async () => {
    const dayBefore = (when) => artParts(new Date(Date.parse(`${when.date}T12:00:00-03:00`) - 86_400_000).toISOString()).date;
    const sweep = (when) => runFixture(app, "tick-reminders", { now: new Date(`${dayBefore(when)}T12:00:00-03:00`).toISOString() }, sink.env);
    const reminders = () => mailsTo(PATIENT_MOVE.email, /^Mañana/);
    await sweep(run.oldWhen);
    expect(reminders().length + waTo(/Te recordamos que mañana/).length, "the day before the OLD time nobody reminds her of it").toBe(0);
    await sweep(run.newWhen);
    await expect.poll(() => reminders().length, { message: "the reminder mail of the new turno" }).toBe(1);
    expect(reminders()[0].text, "the mail names the new time").toContain(run.newWhen.time);
    expect(reminders()[0].text, "…and the new doctor").toContain(`Te atiende: ${DOCTOR_B.name}.`);
    await expect.poll(() => waTo(/Te recordamos que mañana/).length, { message: "the reminder WhatsApp of the new turno" }).toBe(1);
    expect(waTo(/Te recordamos que mañana/)[0].text).toContain(run.newWhen.time);
    expect(waTo(/Te recordamos que mañana/)[0].text).toContain(`*Te atiende:* ${DOCTOR_B.name}`);
  });

  for (const [who, label] of [[patient, "the patient"], [stranger, "the stranger"], [desk, "the desk"], [doctorA, "doctor A"], [doctorB, "doctor B"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

// The numbers as the doctor's screens write them (format.tel_text of the +549 numbers below).
const PATIENT_FIX = {
  dni: "30290021", nombre: "Delia", apellido: "Datos", email: "delia.vieja@example.invalid",
  turnoPhone: "+5492254290021", turnoShown: "2254 29-0021",
  oldPhone: "+5492254290022", oldShown: "2254 29-0022",
  newPhone: "+5492254290023", newShown: "2254 29-0023",
  newEmail: "delia.nueva@example.invalid",
};

test("J29 — the desk fixes the patient's phone and mail after approval: mail follows the account, WhatsApp keeps the turno's number, staff see both", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const p = PATIENT_FIX;
  const waTo = (phone) => sink.whatsapp.filter((m) => String(m.chatId) === `${phone.replace(/\D/g, "")}@c.us`);
  const seeded = await runFixture(app, "seed-visit", { spec: {
    dni: p.dni, nombre: p.nombre, apellido: p.apellido, account: true, email: p.email, sexo: "",
    contacto: p.turnoPhone, account_contacto: p.oldPhone,
    doctor_id: DOCTOR_A.id, doctor_name: DOCTOR_A.name, servicio_id: SERVICE_ID, centro: CENTER,
    when: "+2", at: "10:00", interview: false } });
  const sid = seeded.request_id;
  const when = artParts((await runFixture(app, "request", { id: String(sid) })).request.turno_dt);
  const patient = await role({ session: seeded.session });
  const desk = await role();
  const doctor = await role();
  await signInStaff(desk.page, DESK.usuario, DESK.password);
  await signInStaff(doctor.page, DOCTOR_A.usuario, DOCTOR_A.password);
  const agendaRow = async () => {
    await doctor.page.goto("/admin/mis-turnos", { waitUntil: "load" });
    const row = doctor.page.locator("#sec-mis-turnos .mt-day table tbody tr", { hasText: `${p.nombre} ${p.apellido}` });
    await expect(row).toHaveCount(1);
    return row;
  };

  await test.step("1. before the fix the doctor sees the turno's number first and the wrong account number second, each said whose it is", async () => {
    const row = await agendaRow();
    // Product owner, 2026-11-03: no number is printed; each is an icon whose tooltip says the number and whose it is.
    const icons = row.locator("a.cnt-ico-link");
    await expect(icons).toHaveCount(2);
    await expect(icons.nth(0)).toHaveAttribute("title", new RegExp(`${p.turnoShown} — Dado al pedir el turno$`));
    await expect(icons.nth(0)).toHaveAttribute("href", `tel:${p.turnoPhone}`);
    await expect(icons.nth(1)).toHaveAttribute("title", new RegExp(`${p.oldShown} — En la cuenta del paciente$`));
    expect(await row.innerText(), "no phone number printed as text").not.toContain(p.turnoShown);
  });

  await test.step("2. the desk corrects the phone and the mail of her ACCOUNT in «Pacientes»", async () => {
    const page = desk.page;
    await page.goto(`/admin/pacientes?patient=${p.dni}`, { waitUntil: "load" });
    await page.click("#pac-edit");
    await expect(page.locator("#pat-email")).toHaveValue(p.email);
    await page.fill("#pac-contacto", p.newPhone);
    await page.fill("#pat-email", p.newEmail);
    const saved = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/admin/patients/${p.dni}` && r.request().method() === "PUT");
    await page.click("#pac-save");
    expect((await saved).status(), "the desk's correction is accepted").toBe(200);
    const { patient: account } = await runFixture(app, "patient", { dni: p.dni });
    expect(account.contacto, "the account has the new phone").toBe(p.newPhone);
    expect(account.email, "…and the new mail").toBe(p.newEmail);
    expect([account.nombre, account.apellido], "…and nothing else changed").toEqual([p.nombre, p.apellido]);
    const { request } = await runFixture(app, "request", { id: String(sid) });
    expect(request.contacto, "the turno keeps the number given for it").toBe(p.turnoPhone);
    expect(request.estado, "the approved turno is untouched").toBe("aprobada");
  });

  await test.step("3. she finds the corrected data in her own account", async () => {
    await patient.page.goto("/account#contacto", { waitUntil: "load" });
    await expect(patient.page.locator("#sc-email")).toHaveValue(p.newEmail);
    await expect(patient.page.locator("#sc-contacto")).toHaveValue(p.newPhone);
  });

  await test.step("4. the doctor now sees the corrected account number labelled, and the turno's number is still the main one", async () => {
    const row = await agendaRow();
    const icons = row.locator("a.cnt-ico-link");
    await expect(icons, "the account's number comes after the turno's").toHaveCount(2);
    await expect(icons.nth(0)).toHaveAttribute("title", new RegExp(`${p.turnoShown} — Dado al pedir el turno$`));
    await expect(icons.nth(1)).toHaveAttribute("title", new RegExp(`${p.newShown} — En la cuenta del paciente$`));
    expect(await row.locator("a.cnt-ico-link[title*='" + p.oldShown + "']").count(), "the wrong number is gone").toBe(0);
  });

  await test.step("5. the reminder of the day before: mail to the corrected address only, WhatsApp to the turno's number only", async () => {
    const dayBefore = artParts(new Date(Date.parse(`${when.date}T12:00:00-03:00`) - 86_400_000).toISOString()).date;
    const swept = await runFixture(app, "tick-reminders", { now: new Date(`${dayBefore}T12:00:00-03:00`).toISOString() }, sink.env);
    expect(swept.processed, "the sweep visits her turno").toBeGreaterThanOrEqual(1);
    await expect.poll(() => sink.mails.filter((m) => m.to === p.newEmail && /^Mañana/.test(m.subject)).length,
      { message: "the reminder mail at the corrected address" }).toBe(1);
    expect(sink.mails.filter((m) => m.to === p.newEmail)[0].text, "it names her time").toContain(when.time);
    expect(sink.mails.filter((m) => m.to === p.email).length, "nothing at the old address").toBe(0);
    await expect.poll(() => waTo(p.turnoPhone).filter((m) => /Te recordamos que mañana/.test(m.text)).length,
      { message: "the reminder WhatsApp at the turno's number" }).toBe(1);
    expect(waTo(p.turnoPhone)[0].text).toContain(when.time);
    expect(waTo(p.newPhone).length + waTo(p.oldPhone).length, "no WhatsApp at either account number").toBe(0);
  });

  await test.step("6. the desk completes her data for HSI: the turno changes, her own account is never overwritten, a missing value is gained", async () => {
    const url = `/api/admin/requests/${sid}/patient-data`;
    const post = (data) => desk.context.request.post(url, { data, failOnStatusCode: false });
    const empty = await post({ sexo: "" });
    expect(empty.status(), "an empty value is refused, not stored").toBe(400);
    expect((await empty.json()).error).toBe("sexo vacío");
    const wrong = await post({ sexo: "Z" });
    expect(wrong.status()).toBe(400);
    expect((await wrong.json()).error).toBe("sexo inválido (M, F o X)");
    const future = await post({ nacimiento: "2999-01-01" });
    expect(future.status(), "a birth date in the future is refused").toBe(400);
    expect((await future.json()).error).toBe("nacimiento inválido (usar AAAA-MM-DD)");

    const done = await post({ nombre: "Delia Beatriz", sexo: "f" });
    expect(done.status()).toBe(200);
    expect((await done.json()).paciente, "the answer carries what is stored").toMatchObject({ nombre: "Delia Beatriz", sexo: "F" });
    const { request } = await runFixture(app, "request", { id: String(sid) });
    expect(request.nombre, "the turno has the corrected name").toBe("Delia Beatriz");
    const { patient: account } = await runFixture(app, "patient", { dni: p.dni });
    expect(account.nombre, "her own account keeps the name SHE wrote").toBe(p.nombre);
    expect(account.sexo, "…and gains the sex it was missing, so nobody asks twice").toBe("F");

    const day = await (await desk.context.request.get(`/api/admin/day-queue?dia=${when.date}`)).json();
    const line = day.grupos.flatMap((g) => g.turnos || []).find((t) => String(t.id) === String(sid));
    expect(line.paciente, "the desk's queue shows the corrected name").toBe("Delia Beatriz Datos");
    expect(line.sexo).toBe("F");

    // What the AI read from a referral and the desk corrects is a field of the reading, never the patient's DNI.
    const field = await desk.context.request.post(`/api/admin/requests/${sid}/campos`, { data: { campo: "dni", valor: "30290099" } });
    expect(field.status()).toBe(200);
    expect((await field.json()).campos.dni, "the corrected reading is marked edited and not matching").toMatchObject({ valor: "30290099", editado: true, coincide: false });
    expect((await runFixture(app, "request", { id: String(sid) })).request.dni, "the request's DNI is not rewritten").toBe(p.dni);
  });

  for (const [who, label] of [[patient, "the patient"], [desk, "the desk"], [doctor, "the doctor"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});
