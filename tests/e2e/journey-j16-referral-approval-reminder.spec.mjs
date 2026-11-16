// tests/e2e/journey-j16-referral-approval-reminder.spec.mjs -- J16 ("A patient sends
// a referral, the desk checks what the AI read and approves it, and the patient is
// told -- now, and again the day before -- until she answers with one click")
// cross-role journey acceptance.
//
// Terminal outcome: the person who uploaded the referral gets one confirmation
// and one reminder on each channel she gave (email and WhatsApp), every message
// names the same service, doctor and time the desk approved, the reminder is sent
// once and never twice, and her one-click answer reaches the desk's day queue.
//
// Steps:
//   1. Patient uploads the referral photo for Electrocardiograma, picks a time,
//      sends the request (AI = the app's own fake, TURNOS_FAKE_AI=1: it "reads" the
//      pack's sample referral as a correct one)
//   2. Desk opens the request: what the AI read is on screen (the values, not just the
//      heading), the photo opens, the doctors offered are the cardiologists; a doctor of
//      another specialty is refused by the server if asked for directly; the desk approves
//   3. Patient is told by email and by WhatsApp: same service, doctor, time and place
//      (center name and address) as the approval
//   4. The day before, the reminder sweep runs (the app's own `reminders.tick`, with
//      the clock set to noon the day before): one email and one WhatsApp, with the
//      one-click answer links; a second sweep sends nothing
//   5. Patient follows the "Sí, voy a ir" link of the email with no session; the desk's
//      day queue marks the patient as confirmed
//   6. (own test) A study with no specialist at its center: the desk is not handed a
//      doctor of another specialty
//
// External services: the mail provider and the WhatsApp gateway are a local receiver
// (lib/journey.mjs, `journey: { sink: true }`), selected by the same environment keys
// production uses. No real key, no network.
//
// Port of tests/e2e/journey-j16-referral-approval-reminder.e2e.mjs (2 tests, same titles).
//
// Differences from the node:test original:
// - The app, the desk account and the receiver come from the worker fixtures (`appOptions` +
//   `journey`); every person is a `role()` context; the steps are `test.step`s and the waits for
//   the receiver are `expect.poll`.
// - The referral is chosen ONCE (`#wz-file` setInputFiles, as booking.spec.mjs does): the wizard
//   no longer loses a file picked while it re-renders (!67), so the original's helper that offered
//   the file again until the wizard reacted is gone.
// - The K2 check («the desk is offered the service's cardiologists and nobody else») is a plain
//   step now. The two fixed sleeps (after the second click on the same link; before reading the
//   doctor combo of the study with no specialist) wait for state: the page's own confirmation
//   message, and the detail pane's requests settling.
import path from "node:path";
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  DESK, ROOT, showSlot,
} from "./lib/journey.mjs";

// A patient with a referral and a service picks a time from the wizard's grid. The time is the first of
// the SECOND day offered, never the first time of the list: that one can be minutes away (or already
// past by the time the desk opens the request at 07:00 with a 07:00 slot), and the desk's approval
// would then be refused or shift. Same rule as J17.
async function pickLaterSlot(page) {
  const chips = page.locator(".wz-seg-opt");
  await expect(chips.first()).toBeVisible({ timeout: 25_000 });
  const offered = await chips.evaluateAll((all) => all.map((b) => ({ dia: b.dataset.dia, hora: b.dataset.hora })));
  const days = [...new Set(offered.map((slot) => slot.dia))];
  expect(days.length, "the grid offers more than one day").toBeGreaterThanOrEqual(2);
  const slot = offered.find((candidate) => candidate.dia === days[1]);
  await (await showSlot(page, slot)).click();
  return slot;
}

const SERVICE = { id: "electrocardiograma-hospital", nombre: "Electrocardiograma" };
const CARDIOLOGISTS = ["Camila Levin", "Diego Walsh", "Florencia Acosta", "Sergio Ruiz"];
// A traumatologist at the same hospital: a real doctor of the pack, just not of this service's specialty.
const OTHER_SPECIALTY_DOCTOR = "Ana Rojas";
// Where the service is (packs/example-town/centros-salud.json): both messages must name it.
const CENTER = { name: "Sampleton Hospital", address: "Main Rd. 123" };
const PATIENT = {
  dni: "30555001", nombre: "Olga", apellido: "Orden", email: "olga.orden@example.invalid", contacto: "+5492254500001",
};
const REFERRAL = path.join(ROOT, "packs/example-town/ordenes-demo/bad-handwriting-cardiologia-1.jpg");

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j16" },
  journey: { sink: true, staff: [DESK] },
});

test("J16 — referral to approval to reminder to the patient's one-click answer", async ({ app, sink, role }) => {
  test.setTimeout(180_000);
  const mailTo = (to, pattern) => sink.mails.filter((m) => m.to === to && pattern.test(m.subject));
  const whatsappTo = (digits, pattern) => sink.whatsapp.filter((m) => String(m.chatId).startsWith(digits) && pattern.test(m.text));
  const account = await seedPatient(app, PATIENT);
  const patient = await role({ session: account.session });
  const desk = await role();
  const WA = PATIENT.contacto.replace(/\D/g, "");
  const run = {};

  await test.step("1. patient uploads the referral, picks a time and sends the request", async () => {
    const page = patient.page;
    await page.goto(`/service/${SERVICE.id}/book`, { waitUntil: "load" });
    await expect(page.locator("#svc-title")).toHaveText("Subí tu orden");
    await page.locator("#wz-file").setInputFiles(REFERRAL);
    // A referral read as correct goes straight to choosing a time (no intermediate screen).
    run.slot = await pickLaterSlot(page);
    await expect(page.locator("#wz-send")).toBeVisible();
    await page.locator("#wz-send").click();
    await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada", { timeout: 25_000 });

    const { requests } = await runFixture(app, "requests", { dni: PATIENT.dni });
    expect(requests.length).toBe(1);
    run.requestId = requests[0].id;
    expect(requests[0].estado).toBe("pendiente");
    expect(requests[0].ai_verdict, "the AI's reading of the referral is stored with the request").toBe("parece_correcta");
    await expect.poll(() => mailTo(PATIENT.email, /Recibimos tu solicitud/).length,
      { message: "the 'we received your request' email" }).toBeGreaterThan(0);
  });

  await test.step("2. the desk sees what the AI read and the photo, and approves", async () => {
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row")).toHaveCount(1);
    const detail = await openRequest(page);
    await expect(detail).toContainText("Olga Orden", { useInnerText: true });
    await expect(detail, "the AI's reading of the referral is shown").toContainText("Lo que dice la orden", { useInnerText: true });
    await expect(detail).toContainText("Electrocardiograma", { useInnerText: true });
    // …and the values it read, not only the heading: the signing doctor and license (editable inputs, so the
    // desk can correct a misread), the practice, and the health plan (plain text).
    const read = detail.locator(".campos");
    await expect(read, "the fields the AI read are laid out").toBeVisible();
    await expect(read.locator('input[data-campo="medico"]'), "the doctor named on the referral").toHaveValue("Dr. Ejemplo");
    await expect(read.locator('input[data-campo="matricula"]'), "…his license number").toHaveValue("MP 12345");
    await expect(read, "the practice asked for").toContainText(SERVICE.nombre, { useInnerText: true });
    await expect(read, "the health plan read").toContainText("OSDE", { useInnerText: true });
    // The photo itself, to check the AI against.
    const photo = detail.locator("img[src^='/api/admin/order/']").first();
    await expect(photo).toBeAttached();
    const image = await desk.context.request.get(await photo.getAttribute("src"));
    expect(image.status()).toBe(200);
    expect(image.headers()["content-type"] || "").toMatch(/^image\//);

    // The panel never offers a doctor of another specialty; the server refuses one asked for directly.
    const wrongDoctor = await desk.context.request.post(`/api/admin/requests/${run.requestId}/approve`, {
      data: { medico: OTHER_SPECIALTY_DOCTOR, turno_dt: `${run.slot.dia}T${run.slot.hora}:00-03:00` },
      failOnStatusCode: false });
    expect(wrongDoctor.status(), `the server refuses a doctor of another specialty (got ${await wrongDoctor.text()})`).toBe(409);
    expect((await wrongDoctor.json()).error).toBe("medico_no_atiende_servicio");
    expect((await runFixture(app, "request", { id: String(run.requestId) })).request.estado,
      "…and the request is still waiting").toBe("pendiente");

    const combo = doctorCombo(detail);
    // K2: an electrocardiogram offers the service's cardiologists, not the whole catalog.
    await expect.poll(async () => (await combo.names()).sort(),
      { message: "the desk is offered the service's cardiologists and nobody else" }).toEqual([...CARDIOLOGISTS].sort());
    await combo.pick("Camila Levin");
    await expect(detail.locator("input.turno"), "the approval proposes the time the patient picked")
      .toHaveValue(`${run.slot.dia}T${run.slot.hora}`);
    await approve(page, detail);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.medico_asignado).toBe("Camila Levin");
    run.turnoDt = request.turno_dt;
    run.when = artParts(request.turno_dt);
    expect(run.when.date).toBe(run.slot.dia);
    expect(run.when.time, "the stored appointment is the time the patient picked").toBe(run.slot.hora);
  });

  await test.step("3. the patient is told by email and by WhatsApp: same service, doctor and time", async () => {
    await expect.poll(() => mailTo(PATIENT.email, /confirmado/).length, { message: "the confirmation email" }).toBeGreaterThan(0);
    const mail = mailTo(PATIENT.email, /confirmado/)[0];
    expect(mail.subject).toContain(SERVICE.nombre);
    expect(mail.text, `the email carries ${run.when.time}`).toContain(run.when.time);
    expect(mail.text, "…and where").toContain("Dónde:");
    expect(mail.text, "…the center by name").toContain(CENTER.name);
    expect(mail.text, "…and its address").toContain(CENTER.address);
    await expect.poll(() => whatsappTo(WA, /quedó confirmado/).length, { message: "the WhatsApp confirmation" }).toBeGreaterThan(0);
    const wa = whatsappTo(WA, /quedó confirmado/)[0];
    expect(wa.text.includes(SERVICE.nombre) && wa.text.includes("Camila Levin") && wa.text.includes(run.when.time),
      "the WhatsApp message names service, doctor and time").toBe(true);
    expect(wa.chatId, "…and goes to the number the patient gave").toBe(`${WA}@c.us`);
    expect(wa.text.includes(CENTER.name) && wa.text.includes(CENTER.address),
      "…and the WhatsApp message names the center and its address").toBe(true);
  });

  await test.step("4. the day before, one reminder per channel -- and never a second", async () => {
    // Noon (installation time) of the day before the appointment, as the sweep reads the clock.
    const dayBefore = new Date(new Date(run.turnoDt).getTime() - 86_400_000);
    const parts = artParts(dayBefore.toISOString());
    run.sweepAt = new Date(`${parts.date}T12:00:00-03:00`).toISOString();
    const first = await runFixture(app, "tick-reminders", { now: run.sweepAt }, sink.env);
    expect(first.processed, "the sweep reminds this one appointment").toBe(1);
    await expect.poll(() => mailTo(PATIENT.email, /^Mañana/).length, { message: "the reminder email" }).toBeGreaterThan(0);
    const reminder = mailTo(PATIENT.email, /^Mañana/)[0];
    expect(reminder.subject, `the reminder names the service, not its id (got "${reminder.subject}")`).toContain(SERVICE.nombre);
    expect(reminder.text.includes("Camila Levin") && reminder.text.includes(run.when.time)).toBe(true);
    const links = [...reminder.text.matchAll(/https?:\/\/\S+/g)].map((m) => new URL(m[0]));
    run.yes = links.find((u) => /\/appointment\/confirm\//.test(u.pathname) && u.searchParams.get("r") === "si");
    run.no = links.find((u) => /\/appointment\/confirm\//.test(u.pathname) && u.searchParams.get("r") === "no");
    expect(run.yes && run.no, "the reminder carries the one-click yes and no links").toBeTruthy();
    await expect.poll(() => whatsappTo(WA, /Te recordamos que mañana/).length, { message: "the WhatsApp reminder" }).toBeGreaterThan(0);
    const wa = whatsappTo(WA, /Te recordamos que mañana/)[0];
    expect(wa.text.includes(SERVICE.nombre) && wa.text.includes(run.when.time)).toBe(true);
    expect(wa.text, "…asking for the answer").toMatch(/Respondé SÍ/);

    const second = await runFixture(app, "tick-reminders", { now: run.sweepAt }, sink.env);
    expect(second.processed, "a second sweep finds nothing left to remind").toBe(0);
    expect(mailTo(PATIENT.email, /^Mañana/).length, "one reminder email").toBe(1);
    expect(whatsappTo(WA, /Te recordamos que mañana/).length, "one reminder WhatsApp").toBe(1);
  });

  await test.step("5. the patient answers yes with one click, and the desk's queue shows it", async () => {
    const page = patient.page;
    const link = run.yes.pathname + run.yes.search; // built for the public address; the click goes to this server
    await page.context().clearCookies();
    await page.goto(link, { waitUntil: "load" });
    await expect(page.locator("body")).toContainText(/Listo, queda confirmado/, { useInnerText: true });
    await expect(page.locator("body")).toContainText(run.when.time, { useInnerText: true });

    const dayRow = async () => {
      const day = await desk.context.request.get(`/api/admin/day-queue?dia=${run.when.date}`);
      expect(day.status()).toBe(200);
      return (await day.json()).grupos.flatMap((g) => g.turnos || []).find((r) => String(r.id) === String(run.requestId));
    };
    const row = await dayRow();
    expect(row, "the appointment is in the day queue").toBeTruthy();
    expect(row.confirmacion, "the queue marks the patient as confirmed").toBe("si");

    // …and the desk sees it on screen: the row says who comes and to whom, the reminder
    // answer («Confirmó») is in the appointment card the name opens.
    const queue = desk.page;
    await queue.goto("/admin/cola", { waitUntil: "load" });
    const search = queue.locator("[data-cola-buscar]");
    await expect(search).toBeVisible();
    await search.fill(`${parseInt(run.when.date.slice(8), 10)}/${parseInt(run.when.date.slice(5, 7), 10)}`);
    const line = queue.locator("#sec-cola table tbody tr", { hasText: "Olga Orden" }).first();
    await expect(line).toBeVisible();
    await line.locator(".queue-pat-link").click();
    const card = queue.locator(".queue-card");
    await expect(card).toBeVisible();
    // (not \b: "í" is not a word character to JavaScript)
    await expect(card, "the appointment card says the patient confirmed").toContainText(/Confirmó\s+Sí(\s|$)/, { useInnerText: true });
    await card.locator(".solmod-x").click();
    await expect(queue.locator(".queue-card")).toHaveCount(0);

    // A second click on the same link changes nothing (a mail client that opens it again).
    await page.goto(link, { waitUntil: "load" });
    await expect(page.locator("body"), "the page says the appointment is confirmed, as it already was")
      .toContainText(/Confirmado: te esperamos|Listo, queda confirmado/, { useInnerText: true });
    expect((await dayRow()).confirmacion).toBe("si");
  });

  expect(patient.errors, "no uncaught page errors for the patient").toEqual([]);
  expect(desk.errors, "no uncaught page errors for the desk").toEqual([]);
});

test("J16 — a study with no specialist at its center is not handed to a doctor of another specialty", async ({ app, role }) => {
  // Ecocardiograma at the Diagnostic Center: the pack has no cardiologist there.
  const account = await seedPatient(app, { dni: "30555002", nombre: "Elena", apellido: "Eco", email: "elena.eco@example.invalid", contacto: "+5492254500002" });
  const patient = await role({ session: account.session });
  const desk = await role();

  await test.step("1. the patient asks for an echocardiogram", async () => {
    const page = patient.page;
    await page.goto("/service/ecocardiograma-anexo/book", { waitUntil: "load" });
    await expect(page.locator("#svc-title")).toHaveText("Subí tu orden");
    await page.locator("#wz-file").setInputFiles(REFERRAL);
    await pickLaterSlot(page);
    await page.locator("#wz-send").click();
    await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada", { timeout: 25_000 });
  });

  await test.step("2. the desk is not preselected, nor offered, a doctor of another specialty", async () => {
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    const detail = await openRequest(page, { hasText: "Elena Eco" });
    // The pane paints its form first and fills the doctor's suggestion once its requests settle:
    // read the combo only then (an absent suggestion proves nothing before that).
    await expect(detail.locator('[data-act="aprobar"]')).toBeVisible();
    await page.waitForLoadState("networkidle");
    const combo = doctorCombo(detail);
    await expect(combo.box, "the doctor combo is on the pane").toHaveCount(1);
    expect(await combo.value(), "no doctor is preselected: nobody of the service's specialty works at that center").toBe("");
    expect(await combo.names(), "…and no doctor of another specialty is offered for it").toEqual([]);
  });
  expect(patient.errors).toEqual([]);
  expect(desk.errors).toEqual([]);
});
