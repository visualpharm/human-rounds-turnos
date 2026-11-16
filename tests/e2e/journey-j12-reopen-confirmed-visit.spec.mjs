// tests/e2e/journey-j12-reopen-confirmed-visit.spec.mjs -- J12 ("A doctor confirms
// a visit, then comes back to it") cross-role journey acceptance.
//
// The doctor confirms a consultation: a prescription and an order are issued
// and the patient can open them. What happens when the doctor (or a second
// click, or a retry) comes back to the SAME visit is a patient-safety rule,
// not a screen detail:
//
//   * a confirmed visit reopens showing WHAT WAS ISSUED, not an empty form;
//   * the day queue stops offering "Iniciar consulta" for it;
//   * a second confirmation does not silently create a second valid
//     prescription (double dispensing); a corrected set only comes from an
//     explicit "Reemplazar", and the old link keeps opening, marked as
//     replaced and pointing at the new document (the product owner's hub, 2026-10-23: the
//     old /r/ and /o/ link is never a 404).
//
// Each test seeds a patient with an approved appointment today, signs the
// doctor in and runs the visit up to its FIRST confirmation through the real
// screens (day queue -> "Iniciar consulta" -> consultation -> confirm); that
// part is the setup. The behaviour above is then checked step by step.
//
// Port of tests/e2e/journey-j12-reopen-confirmed-visit.e2e.mjs (3 tests, same titles).
//
// Differences from the node:test original:
// - The app and the doctor's account come from the worker fixtures (`appOptions` + `journey`);
//   each test signs the doctor in on its own `role()` context.
// - The nested `t.test` subtests are `test.step`s of their test: the setup is the first step,
//   so a failed step stops that test (the next test starts from its own visit).
// - «One valid prescription per visit» (K1, !48) is on main: all six checks are plain gates.
// - No wait for the server's 3 s catalog cache by sleeping: the queue is loaded until it offers
//   the seeded visit (`gotoUntil`).
import {
  test, expect, runFixture, signInStaff, gotoUntil, issueDocuments, resendConfirm, LOGIN_PHRASE,
} from "./lib/journey.mjs";

const DOCTOR = { id: "levin-camila", name: "Camila Levin", usuario: "e2e-dr-levin", password: LOGIN_PHRASE };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j12" },
  journey: {
    staff: [{ usuario: DOCTOR.usuario, password: DOCTOR.password, rol: "medico", medico_id: DOCTOR.id }],
  },
});

let sequence = 0;

/** A fresh patient with an approved appointment TODAY at the doctor, and the interview she finished. */
async function newVisit(app, nombre, apellido) {
  sequence += 1;
  return runFixture(app, "seed-visit", { spec: {
    dni: String(30222100 + sequence), nombre, apellido, doctor_id: DOCTOR.id, doctor_name: DOCTOR.name,
    servicio_id: "cardiologia-sampleton-hospital", centro: "sampleton-hospital", when: "today",
    // one appointment per doctor per minute: spread the visits 20 minutes apart
    at: `${String(Math.floor((540 + sequence * 20) / 60)).padStart(2, "0")}:${String((540 + sequence * 20) % 60).padStart(2, "0")}`,
    account: true,
  } });
}

async function doctorPage(role) {
  const doctor = await role();
  await signInStaff(doctor.page, DOCTOR.usuario, DOCTOR.password);
  return doctor;
}

/** From the day queue, the way the doctor really starts: "Iniciar consulta" on the patient's row. */
async function startFromQueue(page, visit) {
  const start = page.locator(`[data-iniciar-consulta="${visit.interview_id}"]`);
  await gotoUntil(page, "/admin/cola", () => expect(start).toBeVisible({ timeout: 2000 }));
  await start.click();
  await page.waitForURL(`**/admin/consult?interview=${visit.interview_id}`);
}

test("J12 — a confirmed visit reopens showing what was issued, and the queue stops offering it", async ({ app, role }) => {
  const visit = await newVisit(app, "Ana", "Reabre");
  const doctor = await doctorPage(role);
  const page = doctor.page;
  const issued = {};

  await test.step("1. doctor starts from the queue and confirms", async () => {
    await startFromQueue(page, visit);
    Object.assign(issued, await issueDocuments(page, { medication: "Enalapril 10 mg", notes: "Control en 7 días (J12)." }));
    const { consultations } = await runFixture(app, "consultations", { interview: String(visit.interview_id) });
    expect(consultations.length >= 1 && consultations[consultations.length - 1].receta, "the first confirmation issued a prescription").toBeTruthy();
    expect(consultations[consultations.length - 1].orden, "…and an order").toBe(true);

    // The order is a document with content, not only a token: what the doctor ticked is stored on it and
    // the patient, with no session, opens it and reads her doctor, her name and that diagnosis.
    const { consultations: stored } = await runFixture(app, "consultation-contents", { interview: String(visit.interview_id) });
    expect(stored.length, "one consultation").toBe(1);
    expect((stored[0].orden.diagnosticos ?? []).length,"the issued order carries the diagnosis the doctor ticked").toBeGreaterThan(0);
    const reader = await role();
    const answer = await reader.page.goto(issued.ordenUrl, { waitUntil: "load" });
    expect(answer.status()).toBe(200);
    const order = reader.page.locator("body");
    await expect(order, "the order names the doctor").toContainText(DOCTOR.name, { useInnerText: true });
    await expect(order, "…the patient").toContainText("Ana Reabre", { useInnerText: true });
    await expect(order, "…and the diagnosis").toContainText(stored[0].diagnoses[0].label, { useInnerText: true });
  });

  await test.step("2. reopening the visit shows what was issued, not an empty form", async () => {
    await page.reload({ waitUntil: "load" });
    // Either screen the app may paint: the blank form or the issued visit.
    await expect(page.locator("#cst-review, #cst-emitida").first()).toBeVisible();
    await expect(page.locator("#cst-review"), "a confirmed visit does not reopen as a blank form that can issue a second set").toHaveCount(0);
    const consulta = page.locator("#sec-consulta");
    await expect(consulta, "the issued medication is on screen").toContainText("Enalapril 10 mg", { useInnerText: true });
    await expect(consulta, "the issued instructions are on screen").toContainText("Control en 7 días (J12).", { useInnerText: true });
    await expect(page.locator(`#sec-consulta a[href="${issued.recetaUrl}"]`), "the link to the issued prescription").toHaveCount(1);
    await expect(page.locator(`#sec-consulta a[href="${issued.ordenUrl}"]`), "the link to the issued order").toHaveCount(1);
  });

  await test.step("3. the day queue no longer offers to start this visit", async () => {
    await page.goto("/admin/cola", { waitUntil: "load" });
    const row = page.locator("#sec-cola table tbody tr", { hasText: "Ana Reabre" }).first();
    await expect(row).toBeVisible();
    // The row paints first and the visit's state a moment later: wait until the queue has said
    // something about this visit (its entry exists) before asserting the offer is gone -- an
    // absent button alone proves nothing.
    await expect(row.locator(`[data-iniciar-consulta="${visit.interview_id}"]`)).toBeVisible();
    await expect(row, "the queue does not invite the doctor to start a visit already confirmed").not.toContainText(/Iniciar consulta/, { useInnerText: true });
    await expect(row, "…it says the visit was attended (and still opens it, read-only)").toContainText(/Atendido/, { useInnerText: true });
  });
  expect(doctor.errors, "no uncaught page errors").toEqual([]);
});

test("J12 — a second confirmation of the same visit does not issue a second prescription", async ({ app, role }) => {
  const visit = await newVisit(app, "Beatriz", "Duplica");
  const doctor = await doctorPage(role);
  const page = doctor.page;
  const issued = {};

  await test.step("1. doctor confirms the visit", async () => {
    await startFromQueue(page, visit);
    Object.assign(issued, await issueDocuments(page, { medication: "Losartán 50 mg", notes: "Una vez por día (J12)." }));
  });

  await test.step("2. the same confirmation arrives again (double click, retry, second tab)", async () => {
    const again = await resendConfirm(page, issued.confirm);
    expect(again.status, "the server refuses a second set for a visit already confirmed").toBe(409);
  });

  await test.step("3. one visit has one prescription and one order", async () => {
    const { consultations } = await runFixture(app, "consultations", { interview: String(visit.interview_id) });
    expect(consultations.length, "no second consultation row was created").toBe(1);
  });

  await test.step("4. the first prescription still opens for the patient", async () => {
    const reader = await role();
    const first = await reader.page.goto(issued.recetaUrl, { waitUntil: "load" });
    expect(first.status()).toBe(200);
    await expect(reader.page.locator("body")).toContainText("Losartán 50 mg", { useInnerText: true });
  });
});

test("J12 — an explicit replacement marks the old link as replaced and points the patient to the new one", async ({ app, role }) => {
  const visit = await newVisit(app, "Carla", "Reemplaza");
  const doctor = await doctorPage(role);
  const page = doctor.page;
  const first = {};
  const second = {};

  await test.step("1. doctor confirms the visit", async () => {
    await startFromQueue(page, visit);
    Object.assign(first, await issueDocuments(page, { medication: "Atenolol 25 mg", notes: "Dosis inicial (J12)." }));
  });

  await test.step("2. doctor reopens it and replaces the set on purpose", async () => {
    await page.reload({ waitUntil: "load" });
    const replace = page.locator("#cst-reemplazar");
    await expect(replace).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept());
    await replace.click();
    // The form comes back pre-filled with what was issued: a correction is one change, not a retyping.
    await expect(page.locator("#cst-review")).toBeVisible();
    await expect(page.locator('[data-med-field="nombre"]').first()).toHaveValue("Atenolol 25 mg");
    await page.fill("#cst-notes", "Dosis corregida (J12).");
    await page.click("#cst-review");
    await expect(page.locator("#cst-review-card")).toBeVisible();
    const answered = page.waitForResponse((r) => r.url().endsWith("/confirm") && r.request().method() === "POST");
    await page.click("#cst-confirmar");
    const response = await answered;
    expect(response.status()).toBe(200);
    const body = await response.json();
    second.recetaUrl = body.receta_url;
    second.ordenUrl = body.orden_url;
    expect(second.recetaUrl, "the replacement is a new document").not.toBe(first.recetaUrl);
  });

  await test.step("3. the old prescription link still opens, marked replaced, pointing at the new one", async () => {
    const reader = await role();
    const rp = reader.page;
    const body = rp.locator("body");
    const old = await rp.goto(first.recetaUrl, { waitUntil: "load" });
    expect(old.status(), "the old link is never a 404").toBe(200);
    await expect(body, "the old prescription says it was replaced").toContainText(/reemplazad/i, { useInnerText: true });
    await expect(body, "…and still shows what was issued").toContainText("Atenolol 25 mg", { useInnerText: true });
    await expect(rp.locator(`a[href="${second.recetaUrl}"]`), "…with a link to the new prescription").not.toHaveCount(0);

    const oldOrder = await rp.goto(first.ordenUrl, { waitUntil: "load" });
    expect(oldOrder.status()).toBe(200);
    await expect(body).toContainText(/reemplazad/i, { useInnerText: true });
    await expect(rp.locator(`a[href="${second.ordenUrl}"]`), "…with a link to the new order").not.toHaveCount(0);
    await expect(rp.locator(`a[href="${second.recetaUrl}"]`), "…and the old order does not point at the prescription").toHaveCount(0);

    const freshOrder = await rp.goto(second.ordenUrl, { waitUntil: "load" });
    expect(freshOrder.status()).toBe(200);
    await expect(body, "the new order is the valid one").not.toContainText(/reemplazad|anulad/i, { useInnerText: true });
    await expect(body, "…and it is the doctor's order").toContainText(DOCTOR.name, { useInnerText: true });

    const fresh = await rp.goto(second.recetaUrl, { waitUntil: "load" });
    expect(fresh.status()).toBe(200);
    await expect(body).toContainText("Dosis corregida (J12).", { useInnerText: true });
    await expect(body, "the new prescription is the valid one").not.toContainText(/reemplazad|anulad/i, { useInnerText: true });
  });
});
