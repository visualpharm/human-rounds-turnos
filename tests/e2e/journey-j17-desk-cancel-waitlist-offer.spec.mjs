// tests/e2e/journey-j17-desk-cancel-waitlist-offer.spec.mjs -- J17 ("The desk cancels an
// appointment by phone, the patient is told, and the freed time goes to the patient who
// asked to be warned about a sooner one") cross-role journey acceptance.
//
// The roles: three patients on the public site (the one who cancels by phone, the one
// who waits for a sooner time, a stranger looking at the wizard), the front desk in the
// panel's «Pacientes de hoy» queue.
//
// Terminal outcome: the waitlisted patient is moved onto the freed time by the link of the
// email she got -- a time held for her for the offer window: the public wizard does not list it
// and nobody else can take it --, the desk's queue shows her there and no longer shows the
// patient who cancelled, and the time she left behind is offered to the public again.
//
// Steps:
//   1. Patient A and patient W each book Cardiología (A sooner, W a day later); the desk
//      approves both
//   2. W ticks «Avisame si se libera un turno antes» in her account
//   3. The desk cancels A's appointment from the queue (the phone call): the panel answers 200,
//      A is told by email, A's account no longer holds it
//   4. W is offered the time by email; while the offer lives the public wizard does not list the
//      time and a stranger cannot take it (the slot-hold endpoint answers 409); the link opens
//      with no session
//   5. W takes it: her appointment moves, the desk's queue lists W at A's time and not A, the
//      time W left is offered to the public, W is told the new time by email, and the time W took
//      stays unavailable (now held by her appointment)
// and, in its own test, a waitlisted patient who says «No lo quiero» gives the time to the public
// (hidden while the offer lives, listed again at once when she declines) and keeps her own (here
// the patient cancels from her own account).
//
// External services: the mail provider is a local receiver (lib/journey.mjs,
// `journey: { sink: true }`). No key, no network.
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  offeredSlots, hasSlot, expectOfferedAgain, sendRequest, cancelFromAccount, dayLabel,
  DESK,
} from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const DOCTOR = "Camila Levin";
const PATIENT_A = { dni: "30777001", nombre: "Ana", apellido: "Llamada", email: "ana.llamada@example.invalid", contacto: "+5492254700001" };
const PATIENT_W = { dni: "30777002", nombre: "Walter", apellido: "Espera", email: "walter.espera@example.invalid", contacto: "+5492254700002" };
const PATIENT_X = { dni: "30777003", nombre: "Ximena", apellido: "Curiosa", email: "ximena.curiosa@example.invalid", contacto: "+5492254700003" };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j17" },
  journey: { sink: true, staff: [DESK] },
});

test.describe.configure({ mode: "serial" });

const slotsNow = (page) => offeredSlots(page, SERVICE_ID);

/** The time is not in the wizard (the server's few-seconds cache of taken times may still list it for a moment). */
const expectNotOffered = (page, slot, what) => expect.poll(async () => hasSlot(await slotsNow(page), slot),
  { message: what, timeout: 20_000, intervals: [1000] }).toBe(false);

/** The desk approves the only pending request of `patient` with the doctor, at the time asked for. */
async function approveAsked(page, patient, slot) {
  await page.goto("/admin/solicitudes", { waitUntil: "load" });
  const detail = await openRequest(page, { hasText: `${patient.nombre} ${patient.apellido}` });
  await doctorCombo(detail).pick(DOCTOR);
  await expect(detail.locator("input.turno"), "the approval proposes the time the patient asked for")
    .toHaveValue(`${slot.dia}T${slot.hora}`);
  await approve(page, detail);
}

test("J17 — the desk cancels by phone: the patient is told and the freed time goes to the waitlist", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const mailsTo = (to, pattern) => sink.mails.filter((m) => m.to === to && pattern.test(m.subject));
  const a = await seedPatient(app, PATIENT_A);
  const w = await seedPatient(app, PATIENT_W);
  const x = await seedPatient(app, PATIENT_X);
  const patientA = await role({ session: a.session });
  const patientW = await role({ session: w.session });
  const stranger = await role({ session: x.session });
  const taker = await role(); // W's phone when she opens the link of the email: no session
  const desk = await role();
  const run = {};

  await test.step("1. A books the sooner time, W a later one, the desk approves both", async () => {
    // The second and third days of the grid, never the first time offered: that one can be minutes away
    // (a 08:20 slot at 08:17) and gone by the time the desk cancels and the offer is made. The second test
    // takes the fourth and fifth days: the wizard's list of taken times is cached for a few seconds.
    const days = [...new Set((await slotsNow(patientA.page)).map((s) => s.dia))];
    expect(days.length, "the grid has five days").toBeGreaterThanOrEqual(5);
    const firstOf = async (page, day) => (await slotsNow(page)).find((s) => s.dia === day);
    run.sooner = await firstOf(patientA.page, days[1]);
    await sendRequest(patientA.page, run.sooner);
    run.later = await firstOf(patientW.page, days[2]);
    expect(run.later, "the grid has a later day for W").toBeTruthy();
    await sendRequest(patientW.page, run.later);

    await signInStaff(desk.page, DESK.usuario, DESK.password);
    await approveAsked(desk.page, PATIENT_A, run.sooner);
    await approveAsked(desk.page, PATIENT_W, run.later);
    const rowA = (await runFixture(app, "requests", { dni: PATIENT_A.dni })).requests[0];
    const rowW = (await runFixture(app, "requests", { dni: PATIENT_W.dni })).requests[0];
    expect([rowA.estado, rowW.estado]).toEqual(["aprobada", "aprobada"]);
    run.idA = rowA.id;
    run.idW = rowW.id;
    run.whenA = artParts(rowA.turno_dt);
    run.whenW = artParts(rowW.turno_dt);
    expect(run.whenA.local < run.whenW.local, "A's time is sooner than W's").toBe(true);
  });

  await test.step("2. W asks to be warned about a sooner time", async () => {
    const page = patientW.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    const toggle = page.locator("[data-le]").first();
    await expect(toggle).toBeVisible();
    await toggle.check();
    await expect(page.locator("[data-le-msg]").first()).toContainText("te avisamos por email", { useInnerText: true });
  });

  await test.step("3. the desk cancels A from the queue", async () => {
    const page = desk.page;
    await page.goto("/admin/cola", { waitUntil: "load" });
    const search = page.locator("[data-cola-buscar]");
    await expect(search).toBeVisible();
    const line = page.locator("#sec-cola table tbody tr", { hasText: `${PATIENT_A.nombre} ${PATIENT_A.apellido}` }).first();
    // The panel loads the day and the saved search after the page, and that can wipe a search typed
    // meanwhile (seen once in 48 runs under load: the box empty, the list «Hoy: 0 turnos»): type it again until the line shows.
    await expect(async () => {
      await search.fill(`${parseInt(run.whenA.date.slice(8), 10)}/${parseInt(run.whenA.date.slice(5, 7), 10)}`);
      await expect(line).toBeVisible({ timeout: 4000 });
    }).toPass({ timeout: 30_000, intervals: [500, 1000, 2000] });
    await line.locator("[data-cola-abrir]").first().click();
    await page.locator("[data-cola-cancel]").click();
    const cancelled = page.waitForResponse((r) => /\/api\/admin\/requests\/\d+\/cancel$/.test(new URL(r.url()).pathname)
      && r.request().method() === "POST");
    await page.locator("[data-cola-cancel-yes]").click();
    expect((await cancelled).status(), "the panel accepts the cancellation").toBe(200);

    expect((await runFixture(app, "request", { id: String(run.idA) })).request.estado).toBe("cancelada");
    await expect.poll(() => mailsTo(PATIENT_A.email, /Se canceló tu turno/).length,
      { message: "A is told by email that the desk cancelled" }).toBe(1);
    expect(mailsTo(PATIENT_A.email, /Se canceló tu turno/)[0].text, "…naming the time that was cancelled")
      .toContain(run.whenA.time);
    expect(mailsTo(PATIENT_A.email, /Se canceló tu turno/)[0].text, "…and that it was the centre")
      .toContain("cancelado por el centro");
    await patientA.page.goto("/account#turnos", { waitUntil: "load" });
    await expect(patientA.page.getByText("Cancelada por el centro").first(), "the desk cancelled, and A reads it so").toBeVisible();
    await expect(patientA.page.locator("[data-cancel]"), "A has no live appointment left to cancel").toHaveCount(0);
  });

  await test.step("4. W is offered the freed time by email; while the offer lives nobody else can take it", async () => {
    await expect.poll(() => mailsTo(PATIENT_W.email, /Se liberó un turno más cercano/).length,
      { message: "the waitlist offer email" }).toBe(1);
    const offer = mailsTo(PATIENT_W.email, /Se liberó un turno más cercano/)[0];
    expect(offer.subject).toContain("Cardiología");
    expect(offer.text, "the offer names the freed time").toContain(run.whenA.time);
    const link = offer.text.match(/https?:\/\/\S+\/appointment\/offer\/[\w-]+/);
    expect(link, "the offer email carries the claim link").toBeTruthy();
    run.offerPath = new URL(link[0]).pathname;

    // «Por 5 minutos este turno es tuyo, garantizado»: an offer holds its time like a soft lock does.
    await expectNotOffered(stranger.page, run.sooner, "the time under offer is not listed by the wizard");
    const grab = await stranger.context.request.post("/api/slot-hold", {
      data: { servicio_id: SERVICE_ID, dia: run.sooner.dia, hora: run.sooner.hora, token: "stranger-hold" },
      failOnStatusCode: false });
    expect(grab.status(), "a stranger cannot hold the time under offer").toBe(409);

    await taker.page.goto(run.offerPath, { waitUntil: "load" });
    await expect(taker.page.locator("#oferta-aceptar")).toBeVisible();
    await expect(taker.page.locator("body")).toContainText(run.whenA.time, { useInnerText: true });
  });

  await test.step("5. W takes the time: the desk's queue, the public wizard and her account agree", async () => {
    const accepted = taker.page.waitForResponse((r) => /\/api\/offer\/[^/]+\/accept$/.test(new URL(r.url()).pathname));
    await taker.page.locator("#oferta-aceptar").click();
    expect((await accepted).status(), "the offer link claims the time").toBe(200);
    await expect(taker.page.locator("body")).toContainText("Tu turno quedó confirmado", { useInnerText: true });

    const { request } = await runFixture(app, "request", { id: String(run.idW) });
    expect(request.estado, "W stays approved").toBe("aprobada");
    expect(request.medico_asignado, "…with her doctor").toBe(DOCTOR);
    expect(artParts(request.turno_dt).local, "…now at the time A left").toBe(run.whenA.local);

    // The desk's day queue: W where A was, A gone.
    const dayRows = async (date) => {
      const day = await desk.context.request.get(`/api/admin/day-queue?dia=${date}`);
      expect(day.status()).toBe(200);
      return (await day.json()).grupos.flatMap((g) => g.turnos || []).map((r) => String(r.id));
    };
    expect(await dayRows(run.whenA.date), "the queue lists W at A's time").toContain(String(run.idW));
    expect(await dayRows(run.whenA.date), "…and no longer lists A").not.toContain(String(run.idA));
    if (run.whenW.date !== run.whenA.date) {
      expect(await dayRows(run.whenW.date), "…and W is gone from her old day").not.toContain(String(run.idW));
    }

    // The public: A's time is W's now, the one W left is free again.
    await expectOfferedAgain(stranger.page, SERVICE_ID, run.later, "the time W left behind is offered to the public");
    await expectNotOffered(stranger.page, run.sooner, "the time W took is not offered any more");

    // W is told the new time by email, and sees it in her account.
    await expect.poll(() => mailsTo(PATIENT_W.email, /confirmado/).length,
      { message: "the confirmation of the new time" }).toBe(2);
    expect(mailsTo(PATIENT_W.email, /confirmado/)[1].text).toContain(run.whenA.time);
    // W's page is still the one of step 2, already on /account#turnos: a goto to the same URL is a
    // same-document navigation (no request, the card keeps her OLD time), so reload it.
    await patientW.page.reload({ waitUntil: "load" });
    const card = patientW.page.locator("#ms-host");
    await expect(card, "her account lists the time she took").toContainText(run.whenA.time, { useInnerText: true });
    await expect(card, "…on the day she took it, not the day she left").toContainText(dayLabel(run.whenA), { useInnerText: true });
  });

  for (const [who, label] of [[patientA, "A"], [patientW, "W"], [stranger, "the stranger"], [taker, "the offer link"], [desk, "the desk"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

test("J17 — a waitlisted patient who says «No lo quiero» gives the time to the public and keeps her own", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const mailsTo = (to, pattern) => sink.mails.filter((m) => m.to === to && pattern.test(m.subject));
  const b = { dni: "30777011", nombre: "Bruno", apellido: "Cancela", email: "bruno.cancela@example.invalid", contacto: "+5492254700011" };
  const v = { dni: "30777012", nombre: "Valeria", apellido: "Paciente", email: "valeria.paciente@example.invalid", contacto: "+5492254700012" };
  const y = { dni: "30777013", nombre: "Yago", apellido: "Mirón", email: "yago.miron@example.invalid", contacto: "+5492254700013" };
  const seeded = { b: await seedPatient(app, b), v: await seedPatient(app, v), y: await seedPatient(app, y) };
  const patientB = await role({ session: seeded.b.session });
  const patientV = await role({ session: seeded.v.session });
  const stranger = await role({ session: seeded.y.session });
  const taker = await role();
  const desk = await role();
  const run = {};

  await test.step("1. B books a time, V a later one, the desk approves both, V asks to be warned", async () => {
    // The fourth and fifth days of the grid: the first test of this file took the second and third, and the
    // wizard's list of taken times is cached for a few seconds, so its last approvals could still be
    // offered here.
    const days = [...new Set((await slotsNow(patientB.page)).map((s) => s.dia))];
    expect(days.length, "the grid has five days").toBeGreaterThanOrEqual(5);
    const firstOf = async (page, day) => (await slotsNow(page)).find((s) => s.dia === day);
    run.sooner = await firstOf(patientB.page, days[3]);
    await sendRequest(patientB.page, run.sooner);
    run.later = await firstOf(patientV.page, days[4]);
    await sendRequest(patientV.page, run.later);
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    await approveAsked(desk.page, b, run.sooner);
    await approveAsked(desk.page, v, run.later);
    run.whenB = artParts((await runFixture(app, "requests", { dni: b.dni })).requests[0].turno_dt);
    run.idV = (await runFixture(app, "requests", { dni: v.dni })).requests[0].id;
    run.whenV = artParts((await runFixture(app, "request", { id: String(run.idV) })).request.turno_dt);

    await patientV.page.goto("/account#turnos", { waitUntil: "load" });
    await patientV.page.locator("[data-le]").first().check();
    await expect(patientV.page.locator("[data-le-msg]").first()).toContainText("te avisamos por email", { useInnerText: true });
  });

  await test.step("2. B cancels from his account; V is offered his time and says no", async () => {
    await cancelFromAccount(patientB.page);
    await expect.poll(() => mailsTo(v.email, /Se liberó un turno más cercano/).length, { message: "V's offer email" }).toBe(1);
    const link = mailsTo(v.email, /Se liberó un turno más cercano/)[0].text.match(/https?:\/\/\S+\/appointment\/offer\/[\w-]+/);
    expect(link).toBeTruthy();
    await expectNotOffered(stranger.page, run.sooner, "while the offer is open the time is not listed");
    await taker.page.goto(new URL(link[0]).pathname, { waitUntil: "load" });
    const declined = taker.page.waitForResponse((r) => /\/api\/offer\/[^/]+\/reject$/.test(new URL(r.url()).pathname));
    await taker.page.locator("#oferta-rechazar").click();
    expect((await declined).status()).toBe(200);
  });

  await test.step("3. the time goes back to the public and V keeps her own appointment", async () => {
    await expectOfferedAgain(stranger.page, SERVICE_ID, run.sooner, "the time V declined is offered to the public");
    const { request } = await runFixture(app, "request", { id: String(run.idV) });
    expect(request.estado, "V is still approved").toBe("aprobada");
    expect(artParts(request.turno_dt).local, "…at her own time").toBe(run.whenV.local);
    expect(mailsTo(v.email, /Se liberó un turno más cercano/).length, "no second offer goes to the one who said no").toBe(1);
  });

  for (const [who, label] of [[patientB, "B"], [patientV, "V"], [stranger, "the stranger"], [taker, "the offer link"], [desk, "the desk"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});
