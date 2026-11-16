// tests/e2e/journey-j18-reject-decline-restore.spec.mjs -- J18 ("The ways a booking ends
// that nobody planned: the desk says no, the patient answers «no voy», the desk archives
// the patient and takes it back") cross-role journey acceptance.
//
// J11-J17 follow bookings that go through. Here each test ends one the other way and
// follows the decision to everybody it touches: the patient (mail and account), the public
// wizard (is the time offered again?) and the desk.
//
// Tests (each works on a day of the grid of its own, so the wizard's few-seconds cache of taken
// times cannot hand one test the time another has just taken):
//   1. The desk rejects a pending request: the reason is stored, the patient is told by email and
//      sees «Rechazada: …» with a way back in her account, the time is offered to the public again
//   2. «No voy a poder ir» in the day-before reminder: the link only asks (a mail scanner cancels
//      nothing), the answer releases the appointment, the desk's day queue and the public wizard
//      follow, and the same link opened again is not a dead end
//   3. The desk archives a patient with an appointment ahead and restores her: sign-in is refused
//      while archived, works after, and the appointment the archive cancelled is not revived
//
// External services: the mail provider is a local receiver (lib/journey.mjs,
// `journey: { sink: true }`). No key, no network.
import { queryUser } from "./lib/fixtures.mjs";
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  offeredSlots, expectOfferedAgain, sendRequest, signInPatient,
  DESK,
} from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const DOCTOR = "Camila Levin";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j18" },
  journey: { sink: true, staff: [DESK] },
});

test.describe.configure({ mode: "serial" });

const slotsNow = (page) => offeredSlots(page, SERVICE_ID);

/** Books `slot` as `patient` (already on a page with a session) and has the desk approve it with the doctor. */
async function bookAndApprove(app, deskPage, patientPage, person, slot) {
  await sendRequest(patientPage, slot);
  await deskPage.goto("/admin/solicitudes", { waitUntil: "load" });
  const detail = await openRequest(deskPage, { hasText: `${person.nombre} ${person.apellido}` });
  await doctorCombo(detail).pick(DOCTOR);
  await expect(detail.locator("input.turno"), "the approval proposes the time the patient asked for")
    .toHaveValue(`${slot.dia}T${slot.hora}`);
  await approve(deskPage, detail);
  return (await runFixture(app, "requests", { dni: person.dni })).requests[0];
}

/** The days the booking wizard lists, in order: each test of this file works on a day of its own. */
async function gridDays(page) {
  return [...new Set((await slotsNow(page)).map((s) => s.dia))];
}
const firstOf = async (page, day) => (await slotsNow(page)).find((s) => s.dia === day);

test("J18 — the desk rejects a request: the patient is told why, and the time goes back to the public", async ({ app, sink, role }) => {
  test.setTimeout(180_000);
  const rita = { dni: "30888001", nombre: "Rita", apellido: "Rechazada", email: "rita.rechazada@example.invalid", contacto: "+5492254800001" };
  const otto = { dni: "30888002", nombre: "Otto", apellido: "Otro", email: "otto.otro@example.invalid", contacto: "+5492254800002" };
  const patient = await role({ session: (await seedPatient(app, rita)).session });
  const other = await role({ session: (await seedPatient(app, otto)).session });
  const desk = await role();
  const run = {};

  await test.step("1. Rita asks for a time; while it waits for the desk the time is not offered to anybody else", async () => {
    // Never the first day of the grid: its first time can be minutes away and turn into the past mid-run
    // (the offer would then start later than the time that was asked for). The second day is the other test's.
    const days = await gridDays(patient.page);
    expect(days.length, "the grid offers several days").toBeGreaterThanOrEqual(3);
    run.slot = await firstOf(patient.page, days[2]);
    await sendRequest(patient.page, run.slot);
    run.request = (await runFixture(app, "requests", { dni: rita.dni })).requests[0];
    expect(run.request.estado).toBe("pendiente");
    await expect.poll(async () => (await offeredSlots(other.page, SERVICE_ID)).some((s) => s.dia === run.slot.dia && s.hora === run.slot.hora),
      { message: "a time somebody asked for is not offered to the next patient", timeout: 20_000, intervals: [1000] }).toBe(false);
  });

  await test.step("2. the desk rejects it with the reason the panel proposes", async () => {
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    await desk.page.goto("/admin/solicitudes", { waitUntil: "load" });
    const detail = await openRequest(desk.page, { hasText: "Rita Rechazada" });
    run.reason = (await detail.locator(".motivo-sel").inputValue()).trim();
    expect(run.reason, "the panel proposes a reason").toBeTruthy();
    const rejected = desk.page.waitForResponse((r) => /\/api\/admin\/requests\/\d+\/reject$/.test(new URL(r.url()).pathname)
      && r.request().method() === "POST");
    await detail.locator('[data-act="rechazar"]').click();
    expect((await rejected).status()).toBe(200);
    const { request } = await runFixture(app, "request", { id: String(run.request.id) });
    expect(request.estado).toBe("rechazada");
    expect(request.motivo, "the reason is stored with the rejection").toBe(run.reason);
  });

  await test.step("3. Rita is told it was not approved, and why", async () => {
    await expect.poll(() => sink.mails.filter((m) => m.to === rita.email && /no fue aprobada/.test(m.subject)).length,
      { message: "the rejection email" }).toBe(1);
    const mail = sink.mails.find((m) => m.to === rita.email && /no fue aprobada/.test(m.subject));
    expect(mail.subject).toContain("Cardiología");
    expect(mail.text, "the email carries the desk's reason").toContain(run.reason);
    await patient.page.goto("/account#turnos", { waitUntil: "load" });
    await expect(patient.page.locator("#ms-host"), "her account says rejected, with the reason")
      .toContainText(`Rechazada: ${run.reason}`, { useInnerText: true });
    await expect(patient.page.locator("#ms-host").getByText("Volver a solicitar"), "…and offers the way back").toBeVisible();
  });

  await test.step("4. the time Rita asked for is offered to the public again", async () => {
    await expectOfferedAgain(other.page, SERVICE_ID, run.slot, "the time of a rejected request");
  });

  for (const [who, label] of [[patient, "Rita"], [other, "Otto"], [desk, "the desk"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

test("J18 — «No voy a poder ir» in the reminder frees the appointment for the public and the desk's queue", async ({ app, sink, role }) => {
  test.setTimeout(180_000);
  const nora = { dni: "30888011", nombre: "Nora", apellido: "Nopuede", email: "nora.nopuede@example.invalid", contacto: "+5492254800011" };
  const paco = { dni: "30888012", nombre: "Paco", apellido: "Pasa", email: "paco.pasa@example.invalid", contacto: "+5492254800012" };
  const patient = await role({ session: (await seedPatient(app, nora)).session });
  const other = await role({ session: (await seedPatient(app, paco)).session });
  const reader = await role(); // Nora's phone the evening before: no session
  const desk = await role();
  const run = {};

  await test.step("1. Nora books, the desk approves, the day before the reminder goes out", async () => {
    // The second day of the grid: the first test of this file uses the third.
    run.slot = await firstOf(patient.page, (await gridDays(patient.page))[1]);
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    run.request = await bookAndApprove(app, desk.page, patient.page, nora, run.slot);
    expect(run.request.estado).toBe("aprobada");
    run.when = artParts(run.request.turno_dt);
    const dayBefore = artParts(new Date(new Date(run.request.turno_dt).getTime() - 86_400_000).toISOString());
    const sweep = await runFixture(app, "tick-reminders", { now: new Date(`${dayBefore.date}T12:00:00-03:00`).toISOString() }, sink.env);
    expect(sweep.processed, "the sweep reminds this one appointment").toBe(1);
    await expect.poll(() => sink.mails.filter((m) => m.to === nora.email && /^Mañana/.test(m.subject)).length,
      { message: "the reminder email" }).toBe(1);
    const reminder = sink.mails.find((m) => m.to === nora.email && /^Mañana/.test(m.subject));
    const links = [...reminder.text.matchAll(/https?:\/\/\S+/g)].map((m) => new URL(m[0]));
    run.no = links.find((u) => /\/appointment\/confirm\//.test(u.pathname) && u.searchParams.get("r") === "no");
    expect(run.no, "the reminder carries the «no voy» link").toBeTruthy();
  });

  await test.step("2. opening the «no» link only asks; saying yes to the question releases the appointment", async () => {
    const page = reader.page;
    await page.goto(run.no.pathname + run.no.search, { waitUntil: "load" });
    await expect(page.locator("body")).toContainText("¿Liberamos tu turno?", { useInnerText: true });
    await expect(page.locator("body")).toContainText(run.when.time, { useInnerText: true });
    expect((await runFixture(app, "request", { id: String(run.request.id) })).request.estado,
      "a link opened (a mail scanner, a curious click) cancels nothing").toBe("aprobada");

    const released = page.waitForResponse((r) => /\/api\/confirm\/[^/]+\/no$/.test(new URL(r.url()).pathname));
    await page.locator('[data-confirm-action="no"]').click();
    expect((await released).status(), "the answer is accepted").toBe(200);
    expect((await runFixture(app, "request", { id: String(run.request.id) })).request.estado).toBe("cancelada");
  });

  await test.step("3. the day's queue and the public wizard no longer hold it; Nora sees it cancelled", async () => {
    const day = await desk.context.request.get(`/api/admin/day-queue?dia=${run.when.date}`);
    expect(day.status()).toBe(200);
    const ids = (await day.json()).grupos.flatMap((g) => g.turnos || []).map((r) => String(r.id));
    expect(ids, "the desk's queue no longer lists the appointment").not.toContain(String(run.request.id));

    await expectOfferedAgain(other.page, SERVICE_ID, run.slot, "the time of an appointment released from the reminder");
    await patient.page.goto("/account#turnos", { waitUntil: "load" });
    await expect(patient.page.getByText("Cancelada por vos").first(), "Nora answered «No voy», so it is hers").toBeVisible();
    await expect(patient.page.locator("[data-cancel]"), "Nora has no live appointment left").toHaveCount(0);
  });

  await test.step("4. the same link opened again does not cancel anything twice and is not a dead end", async () => {
    await reader.page.goto(run.no.pathname + run.no.search, { waitUntil: "load" });
    const body = reader.page.locator("body");
    await expect(body).toContainText("Ese turno está cancelado", { useInnerText: true });
    await expect(body).not.toContainText("¿Liberamos tu turno?", { useInnerText: true });
    await expect(reader.page.locator(`a[href*="/service/${SERVICE_ID}"]`).first(), "…and offers another time of the same service")
      .toBeVisible();
  });

  for (const [who, label] of [[patient, "Nora"], [other, "Paco"], [reader, "the phone"], [desk, "the desk"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

// The phrase journey_fixture.py gives every synthetic patient account (a throwaway database only).
const PATIENT_PHRASE = "e2e-journey-throwaway-pass";

test("J18 — an archived patient cannot get in, and once the desk restores her she is back — with the appointments the archive cancelled still cancelled", async ({ app, role }) => {
  test.setTimeout(180_000);
  const alma = { dni: "30888021", nombre: "Alma", apellido: "Archivable" };
  const visit = (await runFixture(app, "seed-visit", { spec: {
    ...alma, doctor_id: "levin-camila", doctor_name: DOCTOR, servicio_id: SERVICE_ID, centro: "sampleton-hospital",
    when: "+3", at: "11:00", account: true, interview: false, email: "alma.archivable@example.invalid", contacto: "+5492254800021" } }));
  const patient = await role();
  const desk = await role();
  const dialogs = [];
  desk.page.on("dialog", async (dialog) => { dialogs.push(dialog.message()); await dialog.accept(); });

  await test.step("1. Alma signs in with her DNI and sees her appointment", async () => {
    await signInPatient(patient.page, alma.dni, PATIENT_PHRASE);
    await expect(patient.page.locator("#ms-host").getByText(DOCTOR, { exact: true }).first()).toBeVisible();
  });

  await test.step("2. the desk archives her from her card; the appointment ahead is cancelled with her", async () => {
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    await desk.page.goto(`/admin/pacientes?patient=${alma.dni}`, { waitUntil: "load" });
    await desk.page.click("#pac-edit");
    // The patient's own archive/restore button of the data form, not a turno's action.
    const archive = desk.page.locator("#pac-arch");
    await expect(archive).toHaveText("Archivar");
    await archive.click();
    await desk.page.waitForURL((url) => url.pathname === "/admin/pacientes" && !url.searchParams.has("patient"));
    expect(dialogs.some((m) => /turnos? por venir/.test(m)), `the desk was asked about the appointment ahead (saw ${JSON.stringify(dialogs)})`).toBe(true);
    expect((await runFixture(app, "request", { id: String(visit.request_id) })).request.estado).toBe("cancelada");
  });

  await test.step("3. Alma's sign-in is refused", async () => {
    const fresh = await role();
    await signInPatient(fresh.page, alma.dni, PATIENT_PHRASE);
    await expect(fresh.page.locator("#sc-msg"), "the form refuses her, without saying the account is archived")
      .toHaveText("DNI o contraseña incorrectos.");
    expect((await fresh.context.request.get("/api/account/me", { failOnStatusCode: false })).status(), "no session was opened")
      .toBeGreaterThanOrEqual(400);
    expect(fresh.errors).toEqual([]);
  });

  await test.step("4. the desk restores her from the same card", async () => {
    await desk.page.goto(`/admin/pacientes?patient=${alma.dni}`, { waitUntil: "load" });
    await desk.page.click("#pac-edit");
    const restore = desk.page.locator("#pac-arch");
    await expect(restore).toHaveText("Restaurar");
    // While she is archived the cancelled turno offers no «Restaurar» of its
    // own: the patient comes back first (2026-11-06), so the card has one.
    await expect(desk.page.locator("[data-pt-reprog]").first()).toBeVisible();
    await expect(desk.page.locator("[data-pt-restore]")).toHaveCount(0);
    const saved = desk.page.waitForResponse((r) => new URL(r.url()).pathname === `/api/admin/patients/${alma.dni}`
      && r.request().method() === "PUT");
    await restore.click();
    expect((await saved).status()).toBe(200);
    expect((await queryUser(app.dbName, alma.dni)).activo, "she is active again").toBe(true);
  });

  await test.step("5. Alma signs in again; the cancelled appointment stays cancelled", async () => {
    const back = await role();
    await signInPatient(back.page, alma.dni, PATIENT_PHRASE);
    await back.page.goto("/account#turnos", { waitUntil: "load" });
    await expect(back.page.getByText("Cancelada por el centro").first(), "the archive was the desk's").toBeVisible();
    await expect(back.page.locator("[data-cancel]"), "the archive's cancellation is not undone by the restore").toHaveCount(0);
    expect((await runFixture(app, "request", { id: String(visit.request_id) })).request.estado).toBe("cancelada");
    expect((await back.context.request.get("/api/account/me")).status(), "her session works").toBe(200);
    expect(back.errors).toEqual([]);
  });

  for (const [who, label] of [[patient, "Alma"], [desk, "the desk"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});
