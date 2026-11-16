// tests/e2e/journey-j19-new-patient-signup-booking.spec.mjs -- J19 ("A new patient books
// without an account: she registers in the middle of the booking, the desk finds her and
// her request with exactly the data she typed, approves it, and she signs in again with the
// phrase she chose") cross-role journey acceptance.
//
// J11 starts from an account that already exists. This is the other front door: a visitor
// with no account who picks a time first -- the funnel the public site is built for.
//
// Terminal outcome: one person exists for the desk (roster and request) with the DNI, name,
// phone and email she typed in the wizard; the request she sent is the one the desk approves;
// the confirmation reaches the email she gave; and she can sign in with her DNI and phrase.
//
// Steps:
//   1. The visitor picks a time, is asked for an account, registers inside the wizard, skips the
//      «hijos» offer, sees Confirmá addressed to her and sends the request
//   2. The desk sees the request under her name and approves it with a doctor, at the time asked
//   3. She is told by email and sees doctor and time in the account the wizard opened for her
//   4. The desk finds her in «Pacientes» by DNI with the contact data she typed
//   5. In another browser she signs in with DNI and phrase and finds the same appointment
//
// External services: the mail provider is a local receiver (lib/journey.mjs,
// `journey: { sink: true }`). No key, no network.
import {
  test, expect, runFixture, signInStaff, signInPatient, openRequest, doctorCombo, approve, artParts,
  offeredSlots,
  DESK, showSlot,
} from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const DOCTOR = "Camila Levin";
// A throwaway phrase for an account that only lives in this test's database.
const SIGNUP_PHRASE = "e2e-signup-throwaway";
const PERSON = {
  dni: "30999001", nombre: "Lucía", apellido: "Nueva", email: "lucia.nueva@example.invalid",
  telefono: "2254 90-0001", telefonoGuardado: "+542254900001",
};

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j19" },
  journey: { sink: true, staff: [DESK] },
});

test("J19 — a new patient registers inside the booking; the desk, her email and a later sign-in all agree", async ({ app, sink, role }) => {
  test.setTimeout(180_000);
  const visitor = await role();
  const desk = await role();
  const run = {};

  await test.step("1. the visitor picks a time, registers inside the wizard and sends the request", async () => {
    const page = visitor.page;
    // The second day of the grid, never the first time offered: that one can be minutes away (a 07:00 time
    // at 06:58) and refused by the time the desk approves it.
    const slots = await offeredSlots(page, SERVICE_ID);
    const days = [...new Set(slots.map((s) => s.dia))];
    expect(days.length, "the grid reaches past today").toBeGreaterThanOrEqual(2);
    run.slot = slots.find((s) => s.dia === days[1]);
    await (await showSlot(page, run.slot)).click();
    await expect(page.locator("#wz-auth #sc-form"), "with no account the wizard asks for one").toBeVisible();
    await page.locator("#sc-dni").fill(PERSON.dni);
    await page.locator("#sc-nombre").fill(PERSON.nombre);
    await page.locator("#sc-apellido").fill(PERSON.apellido);
    await page.locator("#sc-nac").fill("02/03/1984");
    await page.locator("#sc-sexo-seg").getByText("Femenino", { exact: true }).click();
    await page.locator("#sc-contacto").fill(PERSON.telefono);
    await page.locator("#sc-email").fill(PERSON.email);
    await page.locator("#sc-pass").fill(SIGNUP_PHRASE);
    await page.locator("#sc-submit").click();
    // A first-time account is offered to add the people it books for; she books for herself.
    await page.getByRole("button", { name: "Ahora no" }).click();

    const summary = page.locator("#svc-form-host");
    await expect(page.locator("#wz-send")).toBeVisible();
    await expect(summary, "Confirmá is addressed to the person who just registered").toContainText("Lucía Nueva", { useInnerText: true });
    await expect(summary).toContainText("30.999.001", { useInnerText: true });
    await expect(summary).toContainText(PERSON.email, { useInnerText: true });
    await page.locator("#wz-send").click();
    await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada");

    const { requests } = await runFixture(app, "requests", { dni: PERSON.dni });
    expect(requests.length, "exactly one request").toBe(1);
    expect(requests[0].estado).toBe("pendiente");
    expect(requests[0].servicio_id).toBe(SERVICE_ID);
    expect([requests[0].nombre, requests[0].apellido], "the request carries the registered name").toEqual([PERSON.nombre, PERSON.apellido]);
    expect(requests[0].contacto, "…and the phone, normalised").toBe(PERSON.telefonoGuardado);
    run.requestId = requests[0].id;
    await expect.poll(() => sink.mails.filter((m) => m.to === PERSON.email && /Recibimos tu solicitud/.test(m.subject)).length,
      { message: "the 'we received your request' email, to the address she typed" }).toBe(1);
  });

  await test.step("2. the desk sees the request under her name and approves it", async () => {
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row")).toHaveCount(1);
    const detail = await openRequest(page, { hasText: "Lucía Nueva" });
    await expect(detail).toHaveAttribute("data-id", String(run.requestId));
    await expect(detail).toContainText("Cardiología", { useInnerText: true });
    // The DNI is the copyable figure of the card: dotted on screen, bare digits on the clipboard.
    await expect(detail.locator('[data-copy-dni="30999001"]').first(), "the desk reads the DNI she typed").toHaveText("30.999.001");
    // The contact is an icon next to the name: the call goes to the number she typed.
    await expect(detail.locator(".sol-det-top a.cnt-ico-link"), "…and the phone")
      .toHaveAttribute("href", "tel:" + PERSON.telefonoGuardado);
    await doctorCombo(detail).pick(DOCTOR);
    await expect(detail.locator("input.turno"), "the approval proposes the time she asked for").toHaveValue(`${run.slot.dia}T${run.slot.hora}`);
    await approve(page, detail);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.medico_asignado).toBe(DOCTOR);
    run.when = artParts(request.turno_dt);
    expect(run.when.local).toBe(`${run.slot.dia}T${run.slot.hora}`);
  });

  await test.step("3. she is told by email and sees doctor and time in the account the wizard opened", async () => {
    await expect.poll(() => sink.mails.filter((m) => m.to === PERSON.email && /confirmado/.test(m.subject)).length,
      { message: "the confirmation email" }).toBe(1);
    expect(sink.mails.find((m) => m.to === PERSON.email && /confirmado/.test(m.subject)).text).toContain(run.when.time);
    await visitor.page.goto("/account#turnos", { waitUntil: "load" });
    await expect(visitor.page.locator("#ms-host").getByText(DOCTOR, { exact: true }).first()).toBeVisible();
    await expect(visitor.page.locator("#ms-host")).toContainText(run.when.time, { useInnerText: true });
  });

  await test.step("4. the desk finds her in «Pacientes» with the contact data she typed", async () => {
    const page = desk.page;
    await page.goto(`/admin/pacientes?patient=${PERSON.dni}`, { waitUntil: "load" });
    // The page h1 stays «Pacientes»; the open ficha brings its own title, «Nombre Apellido, edad».
    await expect(page.locator(".pat-head-name")).toHaveText(/^Lucía Nueva, \d+\s*$/);
    await expect(page.locator("#pat-name")).toHaveValue(PERSON.nombre);
    await expect(page.locator("#pac-apellido")).toHaveValue(PERSON.apellido);
    await expect(page.locator("#pat-birth"), "the birth date she typed").toHaveValue("02/03/1984");
    await expect(page.locator("#pat-sex")).toHaveValue("F");
    await expect(page.locator("#pac-contacto"), "the phone she typed").toHaveValue(PERSON.telefonoGuardado);
    await expect(page.locator("#pat-email")).toHaveValue(PERSON.email);
  });

  await test.step("5. in another browser she signs in with DNI and the phrase she chose", async () => {
    const other = await role();
    await signInPatient(other.page, PERSON.dni, SIGNUP_PHRASE);
    await other.page.goto("/account#turnos", { waitUntil: "load" });
    await expect(other.page.locator("#ms-host").getByText(DOCTOR, { exact: true }).first()).toBeVisible();
    await expect(other.page.locator("#ms-host")).toContainText(run.when.time, { useInnerText: true });
    expect(other.errors).toEqual([]);
  });

  for (const [who, label] of [[visitor, "the visitor"], [desk, "the desk"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});
