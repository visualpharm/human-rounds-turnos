// tests/e2e/journey-j11-booking-to-prescription.spec.mjs -- J11 ("A patient books
// online, the front desk approves her to a doctor of the right specialty, the
// doctor sees her and issues a prescription and an order, the patient opens the
// prescription and finds her own doctor on it") cross-role journey acceptance.
//
// Every other e2e file drives ONE screen. This one hands the same appointment
// across four people through one real server and one throwaway database:
//   patient (public site)  ->  front desk (panel)  ->  patient (account, interview)
//   ->  doctor (panel)  ->  patient again (the /r/ and /o/ links, no login).
//
// Terminal outcome: the prescription and the order the doctor issued open for
// the patient WITHOUT signing in, and they name the doctor the desk chose, the
// patient and what the doctor wrote.
//
// Steps:
//   1. Patient books Cardiología from the service page (account already open;
//      the sign-up form itself is covered by account-*.spec.mjs)
//   2. Front desk opens the request, sees only cardiologists, picks one and a
//      time, approves
//   3. The approval is stored as one fact (doctor id + name + time) and the
//      patient is told by email
//   4. Patient sees the doctor and the time in her account, opens the
//      pre-visit interview and answers
//   5. The doctor signs in, finds the patient in "Mis pacientes" and "Mis
//      turnos" at the approved time
//   6. The doctor opens the consultation, chooses a diagnosis and a study,
//      writes a medication, confirms
//   7. The patient opens /r/… and /o/… with no session: her doctor, her name,
//      what was written
//   8. The admin corrects the doctor's surname; 9. his patient and her
//      appointment follow the new name (K3: a doctor is one id)
// and, in its own test, the doctor's two screens lead with the same phone (K4).
//
// External services: AI is the app's own fake (TURNOS_FAKE_AI=1); mail is a
// local receiver (lib/journey.mjs, `journey: { sink: true }`). No key, no network.
//
// Port of tests/e2e/journey-j11-booking-to-prescription.e2e.mjs (2 tests, same titles).
//
// Differences from the node:test original:
// - The app, the staff accounts, the mail receiver and the doctor's interview switch come from the
//   worker fixtures (`appOptions` + `journey`); every person is a `role()` context of the runner.
// - The steps are `test.step`s; every wait is a retrying `expect` (or `expect.poll` on the mail
//   receiver / the interview page's status), never a sleep.
// - Checks 9 (K3, renamed doctor) and the phone test (K4) were `todo` while their fixes were open;
//   K3 (!56) and K4 (!51) are on main, so both are plain checks now.
// - Locators stay CSS: the panel's ids/classes/data-* are its markup contract.
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts, dayLabel,
  offeredSlots,
  DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS, showSlot,
} from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const DOCTOR = { id: "levin-camila", name: "Camila Levin", usuario: "e2e-dr-levin", password: LOGIN_PHRASE };
// The four cardiologists of the synthetic pack's Cardiología service.
const CARDIOLOGISTS = ["Camila Levin", "Diego Walsh", "Florencia Acosta", "Sergio Ruiz"];
const PATIENT = {
  dni: "30222001", nombre: "Marta", apellido: "Viajera", nacimiento: "1984-03-02", sexo: "F",
  contacto: "+5492254200001", email: "marta.viajera@example.invalid",
};
// For the phone test below: the number given for the turn differs from the one in the account.
const REQUEST_PHONE = "+5492254300002";
const REQUEST_PHONE_SHOWN = "2254 30-0002";
const ACCOUNT_PHONE = "+5492254200003";
const ACCOUNT_PHONE_SHOWN = "2254 20-0003";
const RENAMED = "Camila Levín Ruiz";
const MEDICATION = "Amoxicilina 500 mg";
const NOTES = "Control en una semana (J11).";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j11" },
  journey: {
    sink: true,
    staff: [DESK, { usuario: DOCTOR.usuario, password: DOCTOR.password, rol: "medico", medico_id: DOCTOR.id }],
    intakeDoctors: [DOCTOR.id],
  },
});

test("J11 — online booking, desk approval, doctor consultation, prescription in the patient's hands", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const account = await seedPatient(app, PATIENT);
  const patient = await role({ session: account.session });
  const desk = await role();
  const doctor = await role();
  const reader = await role(); // the patient's phone: no session at all
  const admin = await role();
  const run = {};

  await test.step("1. patient books Cardiología from the service page", async () => {
    const page = patient.page;
    await page.goto(`/service/${SERVICE_ID}`, { waitUntil: "load" });
    await page.locator("a.mp-more").click();
    await page.waitForURL(`**/service/${SERVICE_ID}/book?slots=1`);
    // A time on the second day of the grid, never the first time offered: that one can be minutes away
    // (a 07:00 time at 06:58) and refused by the time the desk approves it.
    const slots = await offeredSlots(page, SERVICE_ID);
    const days = [...new Set(slots.map((s) => s.dia))];
    expect(days.length, "the grid reaches past today").toBeGreaterThanOrEqual(2);
    const slot = slots.find((s) => s.dia === days[1]);
    await (await showSlot(page, slot)).click();
    await expect(page.locator("#wz-send")).toBeVisible();
    const summary = page.locator("#svc-form-host");
    await expect(summary, "the request is for the signed-in patient").toContainText("Marta Viajera", { useInnerText: true });
    await expect(summary).toContainText("Cardiología", { useInnerText: true });
    await page.locator("#wz-send").click();
    await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada");

    const { requests } = await runFixture(app, "requests", { dni: PATIENT.dni });
    expect(requests.length, "exactly one request was created").toBe(1);
    run.requestId = requests[0].id;
    expect(requests[0].estado).toBe("pendiente");
    expect(requests[0].servicio_id).toBe(SERVICE_ID);
    expect(requests[0].medico_asignado_id, "nobody is assigned before the desk decides").toBeNull();
    await expect.poll(() => sink.mails.some((m) => /Recibimos tu solicitud/.test(m.subject)),
      { message: "the 'we received your request' email" }).toBe(true);
  });

  await test.step("2. front desk sees only cardiologists, picks one and a time, approves", async () => {
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row")).toHaveCount(1);
    await expect(page.locator("#list .sol-row").first()).toContainText("Marta Viajera", { useInnerText: true });
    const detail = await openRequest(page);
    await expect(detail, "the desk opened the patient's request").toHaveAttribute("data-id", String(run.requestId));
    await expect(detail).toContainText("Cardiología", { useInnerText: true });

    // The doctor list the desk chooses from: the service's doctors, nobody else.
    const combo = doctorCombo(detail);
    await expect.poll(async () => (await combo.names()).sort(),
      { message: "a cardiology request offers exactly the cardiologists" }).toEqual([...CARDIOLOGISTS].sort());
    await combo.pick(DOCTOR.name);
    await expect(detail.locator("input.med-sel")).toHaveValue(DOCTOR.name);

    // Not the default time: a decision of the desk.
    await detail.locator("select.turno-sel").selectOption({ index: 2 });
    run.chosenLocal = await detail.locator("input.turno").inputValue(); // 2026-10-26T08:40 (installation time)
    expect(run.chosenLocal).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);

    await approve(page, detail);
    // The approved row leaves the queue for the «Recién aprobadas» card above it.
    await expect(page.locator("#list .sol-row")).toHaveCount(0);
    await expect(page.locator("#hsi-recientes .hsi-recientes-f")).toHaveCount(1);
    await expect(page.locator("#toast")).toContainText(/Aprobada/, { useInnerText: true });
  });

  await test.step("3. the approval is one stored fact and the patient is told", async () => {
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.medico_asignado_id, "the stored doctor id is the one the desk chose").toBe(DOCTOR.id);
    expect(request.medico_asignado, "the stored doctor name matches that id").toBe(DOCTOR.name);
    run.when = artParts(request.turno_dt);
    expect(run.when.local, "the stored time is the one the desk chose, in installation time").toBe(run.chosenLocal);
    await expect.poll(() => sink.mails.find((m) => /confirmado/.test(m.subject)), { message: "the 'confirmed' email" }).toBeTruthy();
    const mail = sink.mails.find((m) => /confirmado/.test(m.subject));
    expect(mail.to).toBe(PATIENT.email);
    expect(mail.text, `the email carries the time ${run.when.time}`).toContain(run.when.time);
  });

  await test.step("4. patient sees her doctor and time, opens the interview and answers", async () => {
    const page = patient.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    const card = page.locator("#ms-host");
    await expect(card.getByText(DOCTOR.name, { exact: true }).first()).toBeVisible();
    await expect(card, `the account shows ${run.when.time}`).toContainText(run.when.time, { useInnerText: true });
    await expect(card, "the account shows the day").toContainText(dayLabel(run.when), { useInnerText: true });
    await expect(card.locator(`a[href="/doctor/${DOCTOR.id}"]`), "the doctor's name links to the doctor's page").toHaveCount(1);

    // The doctor's switch is read through the catalog cache (3 s): wait for the
    // page to stop redirecting to /my-requests instead of sleeping.
    await expect.poll(async () => (await patient.context.request.get(`/entrevista/${run.requestId}`, { maxRedirects: 0 })).status(),
      { message: "the pre-visit interview page opens for this request", timeout: 15_000 }).toBe(200);
    await page.goto(`/entrevista/${run.requestId}`, { waitUntil: "load" });
    await expect(page.locator(".iv-input")).toBeVisible();
    await page.fill(".iv-input", "Tengo palpitaciones desde hace dos semanas.");
    await page.click(".iv-send");
    await expect(page.locator("#iv-msgs")).toContainText("¿Desde cuándo tenés esto?", { useInnerText: true });
    const { interviews } = await runFixture(app, "interview-of", { request: String(run.requestId) });
    expect(interviews.length).toBe(1);
    expect(interviews[0].doctor_id, "the interview belongs to the doctor the desk chose").toBe(DOCTOR.id);
    run.interviewId = interviews[0].id;
  });

  await test.step("5. the doctor finds the patient in Mis pacientes and Mis turnos at the approved time", async () => {
    const page = doctor.page;
    await signInStaff(page, DOCTOR.usuario, DOCTOR.password);
    await page.goto("/admin/my-patients", { waitUntil: "load" });
    const rows = page.locator("#sec-mis-pacientes table tbody tr");
    await expect(rows.first()).toBeVisible();
    await expect(rows, "the doctor has exactly this one patient").toHaveCount(1);
    await expect(rows.first()).toContainText("Marta Viajera", { useInnerText: true });
    await expect(rows.first(), `Mis pacientes names the next appointment (${run.when.time})`).toContainText(run.when.time, { useInnerText: true });
    await expect(page.locator("#sec-mis-pacientes .dp-iv-link"), "the roster links to this patient's interview")
      .toHaveAttribute("href", `/admin/consult?interview=${run.interviewId}`);

    await page.goto("/admin/mis-turnos", { waitUntil: "load" });
    const week = page.locator("#sec-mis-turnos .mt-day table tbody tr");
    await expect(week.first()).toBeVisible();
    await expect(week.first()).toContainText("Marta Viajera", { useInnerText: true });
    await expect(week.first(), "Mis turnos lists her at the approved time").toContainText(run.when.time, { useInnerText: true });
  });

  await test.step("6. the doctor consults: diagnosis, study, medication, confirm", async () => {
    const page = doctor.page;
    await page.goto("/admin/my-patients", { waitUntil: "load" });
    await page.locator("#sec-mis-pacientes .dp-iv-link").click();
    await expect(page.locator("#cst-review")).toBeVisible();
    await expect(page.locator("#sec-consulta .cst-head")).toContainText("Marta Viajera", { useInnerText: true });
    run.diagnosis = (await page.locator("[data-dx]").first().locator("xpath=following-sibling::span").first().innerText()).trim();
    run.study = (await page.locator("[data-exam]").first().locator("xpath=following-sibling::span").first().innerText()).trim();
    await page.locator("[data-dx]").first().check();
    await page.locator("[data-exam]").first().check();
    await page.click("[data-add-med]");
    await page.fill('[data-med-field="nombre"]', MEDICATION);
    await page.fill('[data-med-field="dosis"]', "1 comprimido");
    await page.fill('[data-med-field="frecuencia"]', "cada 8 horas");
    await page.fill('[data-med-field="duracion"]', "7 días");
    await page.fill("#cst-notes", NOTES);
    await page.click("#cst-review");
    const review = page.locator("#cst-review-card");
    await expect(review).toContainText("Marta Viajera", { useInnerText: true });
    await expect(review, "the review names the doctor who will sign").toContainText(DOCTOR.name, { useInnerText: true });
    await expect(review).toContainText(MEDICATION, { useInnerText: true });

    const confirmed = page.waitForResponse((r) => r.url().endsWith("/confirm") && r.request().method() === "POST");
    await page.click("#cst-confirmar");
    const response = await confirmed;
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.receta_url).toMatch(/^\/r\/[\w-]+$/);
    expect(body.orden_url).toMatch(/^\/o\/[\w-]+$/);
    run.recetaUrl = body.receta_url;
    run.ordenUrl = body.orden_url;
    await expect(page.locator(".cst-done")).toBeVisible();
    const { consultations } = await runFixture(app, "consultations", { interview: String(run.interviewId) });
    expect(consultations.length).toBe(1);
    expect(consultations[0].doctor_id).toBe(DOCTOR.id);
    expect(consultations[0].receta).toBe(true);
    expect(consultations[0].orden).toBe(true);

    // What the consultation STORED is what the doctor ticked and wrote -- not only that documents exist.
    const { consultations: stored } = await runFixture(app, "consultation-contents", { interview: String(run.interviewId) });
    expect(stored.length).toBe(1);
    const [contents] = stored;
    expect(contents.diagnoses.map((d) => d.label), "the stored diagnosis is the one the doctor ticked")
      .toEqual([expect.stringContaining(run.diagnosis)]);
    expect(contents.exams.map((e) => e.label), "the stored study is the one the doctor ticked")
      .toEqual([expect.stringContaining(run.study)]);
    expect(contents.meds.map((m) => m.nombre), "the stored medication is the one the doctor wrote").toEqual([MEDICATION]);
    expect(contents.notes, "the stored instructions are the doctor's").toBe(NOTES);
    expect(contents.orden.diagnosticos.join(" "), "the order document carries the diagnosis").toContain(run.diagnosis);
    expect(contents.orden.estudios.join(" "), "the order document carries the study").toContain(run.study);
  });

  await test.step("7. the patient opens the prescription and the order with no session", async () => {
    const page = reader.page;
    const body = page.locator("body");
    const receta = await page.goto(run.recetaUrl, { waitUntil: "load" });
    expect(receta.status()).toBe(200);
    await expect(body, "the prescription names the doctor the desk chose").toContainText(DOCTOR.name, { useInnerText: true });
    await expect(body, "…with the specialty").toContainText("Cardiología", { useInnerText: true });
    await expect(body, "…and the patient").toContainText("Marta Viajera", { useInnerText: true });
    await expect(body, "…with her DNI").toContainText(PATIENT.dni, { useInnerText: true });
    await expect(body, "…and what the doctor prescribed").toContainText(MEDICATION, { useInnerText: true });
    await expect(body, "…and the instructions").toContainText(NOTES, { useInnerText: true });

    const orden = await page.goto(run.ordenUrl, { waitUntil: "load" });
    expect(orden.status()).toBe(200);
    await expect(body).toContainText(DOCTOR.name, { useInnerText: true });
    await expect(body).toContainText("Marta Viajera", { useInnerText: true });
    await expect(body, "the order lists the study the doctor ticked").toContainText(run.study, { useInnerText: true });
    await expect(body, "the order names the diagnosis the doctor ticked").toContainText("Diagnóstico presuntivo", { useInnerText: true });
    await expect(body, "…the very one the doctor ticked").toContainText(run.diagnosis, { useInnerText: true });

    // The link is the whole authorization: a link with one character changed opens nothing.
    const forged = await page.goto(run.recetaUrl + "x", { waitUntil: "load" });
    expect(forged.status()).toBe(404);
    await expect(body, "a wrong link shows no patient data").not.toContainText("Marta Viajera", { useInnerText: true });
  });

  await test.step("8. the admin corrects the doctor's surname in the catalog", async () => {
    const page = admin.page;
    await signInStaff(page, ADMIN_USER, ADMIN_PASS);
    await page.goto(`/admin/doctors?item=${DOCTOR.id}`, { waitUntil: "load" });
    await page.locator("button.cnt-tab[data-tab='info']").click();
    await page.fill("#med-apellido", "Levín Ruiz");
    const saved = page.waitForResponse((r) => new URL(r.url()).pathname.startsWith("/api/admin/content/doctors")
      && r.request().method() !== "GET");
    await page.locator("button.btn-accent", { hasText: "Guardar" }).click();
    expect((await saved).status()).toBe(200);
    await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible();
  });

  await test.step("9. renamed, the doctor still has his patient and the patient still sees her appointment, under the new name", async () => {
    const page = doctor.page;
    await page.goto("/admin/my-patients", { waitUntil: "load" });
    await expect(page.locator("#sec-mis-pacientes table tbody tr").first()).toBeVisible();
    await expect(page.locator("#sec-mis-pacientes"), "the roster still has the patient").toContainText("Marta Viajera", { useInnerText: true });
    await page.goto("/admin/mis-turnos", { waitUntil: "load" });
    await expect(page.locator("#sec-mis-turnos .mt-day table tbody tr").first()).toBeVisible();
    await expect(page.locator("#sec-mis-turnos"), "Mis turnos still has her appointment").toContainText("Marta Viajera", { useInnerText: true });

    await patient.page.goto("/account#turnos", { waitUntil: "load" });
    await expect(patient.page.locator("#ms-host").getByText(RENAMED, { exact: true }).first()).toBeVisible();
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.medico_asignado_id, "the stored doctor is still the same id").toBe(DOCTOR.id);
    expect(request.medico_asignado, "…and the stored name is the catalog's current name").toBe(RENAMED);
  });

  for (const [who, label] of [[patient, "patient"], [desk, "desk"], [doctor, "doctor"], [reader, "reader"], [admin, "admin"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});

// The booking wizard saves a number the patient edits into her account too, so the two numbers can only
// differ when the turn was given another one (the desk typed it, or a relative booked for her). Then the
// doctor who must call her sees two screens (Mis pacientes, Mis turnos): they must lead with the same
// number -- the one given for the turn -- and show the account's next to it, saying whose it is.
test("J11 — the doctor sees the same number on both screens: the turn's first, the account's labelled", async ({ app, role }) => {
  await runFixture(app, "seed-visit", { spec: {
    dni: "30222050", nombre: "Nora", apellido: "Telefono", doctor_id: DOCTOR.id, doctor_name: DOCTOR.name,
    servicio_id: SERVICE_ID, centro: "sampleton-hospital", when: "+2", at: "09:00", account: true, interview: false,
    contacto: REQUEST_PHONE, account_contacto: ACCOUNT_PHONE } });
  const doctor = await role();
  const page = doctor.page;
  await signInStaff(page, DOCTOR.usuario, DOCTOR.password);

  await page.goto("/admin/my-patients", { waitUntil: "load" });
  const roster = page.locator("#sec-mis-pacientes table tbody tr", { hasText: "Nora Telefono" }).first();
  await expect(roster).toBeVisible();
  await page.goto("/admin/mis-turnos", { waitUntil: "load" });
  const agenda = page.locator("#sec-mis-turnos .mt-day table tbody tr", { hasText: "Nora Telefono" }).first();
  await expect(agenda).toBeVisible();
  // A list shows a contact as an icon (a `tel:` link): the number is the link's href and its label for
  // screen readers (number plus whose it is), never written in the row.
  const phones = async (r) => r.locator('a[href^="tel:"]').evaluateAll((els) => els.map((a) => ({
    href: a.getAttribute("href"), label: a.getAttribute("aria-label") || "", copy: a.getAttribute("data-copy") || "",
  })));
  const seen = { "Mis turnos": await phones(agenda) };
  await page.goto("/admin/my-patients", { waitUntil: "load" });
  const row = page.locator("#sec-mis-pacientes table tbody tr", { hasText: "Nora Telefono" }).first();
  await expect(row).toBeVisible();
  seen["Mis pacientes"] = await phones(row);

  for (const [screen, links] of Object.entries(seen)) {
    expect(links.map((l) => l.href), `${screen} leads with the turn's number, then the account's`)
      .toEqual([`tel:${REQUEST_PHONE}`, `tel:${ACCOUNT_PHONE}`]);
    expect(links[0].label, `${screen} reads the number given for the turn`).toContain(REQUEST_PHONE_SHOWN);
    expect(links[0].copy, `${screen}: the first icon copies the turn's number`).toContain(REQUEST_PHONE_SHOWN);
    expect(links[1].label, `${screen} reads the account's number`).toContain(ACCOUNT_PHONE_SHOWN);
    expect(links[1].label, `${screen} says whose the second number is`).toMatch(/cuenta/i);
  }
  expect(doctor.errors).toEqual([]);
});
