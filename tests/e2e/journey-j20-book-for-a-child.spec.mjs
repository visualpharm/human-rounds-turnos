// tests/e2e/journey-j20-book-for-a-child.spec.mjs -- J20 ("A parent books pediatric dentistry for
// her child: she adds the child inside the booking, the desk approves, the notice reaches the
// parent, the doctor sees the child, and nobody else can book for that child") cross-role
// journey acceptance.
//
// Terminal outcome: one request exists whose PATIENT is the child (name, DNI) and whose REQUESTER is
// the parent's account; the desk reads the child's name and DNI; the approval email and WhatsApp go to
// the parent (the child has no account) and greet the child; the parent's account lists the child under
// «Personas a cargo» and the appointment with the child as its patient; the doctor's roster holds
// the child; the same child cannot be booked twice for the service; and a stranger's account can neither
// book for the child nor see the appointment.
//
// Steps:
//   1. The parent picks a time of a pediatric service (children up to 12): the wizard asks for the child
//      (the adult is not offered), refuses a form without the sworn statement, the parent's own DNI and a
//      date that is not a date, then takes the child and shows Confirmá addressed to the child
//   2. She sends the request: it is the child's, filed by the parent's account
//   3. The desk finds the child's name and DNI, is offered the service's doctor and approves
//   4. The parent is told by email and by WhatsApp (greeting the child) and sees the appointment in her account
//   5. The child is listed in her account; booking again for the child is refused with the limit's own text
//   6. The doctor finds the child in «Mis pacientes» and «Mis turnos»
//   7. A stranger's account cannot book for the child (403) and does not see the appointment
//
// External services: the mail provider and the WhatsApp gateway are a local receiver
// (lib/journey.mjs, `journey: { sink: true }`). No key, no network.
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  offeredSlots, DESK, LOGIN_PHRASE, showSlot,
} from "./lib/journey.mjs";

const SERVICE = { id: "odontopediatria-maternity", nombre: "Odontopediatría" };
const DOCTOR = { id: "funes-natalia", nombre: "Natalia Funes", usuario: "e2e-funes", password: LOGIN_PHRASE };
const PARENT = {
  dni: "30888001", nombre: "Marta", apellido: "Madre", email: "marta.madre@example.invalid", contacto: "+5492254800001",
};
const STRANGER = { dni: "30888002", nombre: "Sergio", apellido: "Ajeno", email: "sergio.ajeno@example.invalid", contacto: "+5492254800002" };
// Birth dates are measured back from today, in the middle of a month: a fixed date turns a child a year older on
// its birthday and the "7 años" below goes red that day. Six months past the birthday keeps the whole-years age
// stable for the next six months of runs.
function bornYearsAgo(years) {
  const today = new Date();
  const born = new Date(today.getFullYear() - years, today.getMonth() - 6, 15);
  const [y, m, d] = [born.getFullYear(), born.getMonth() + 1, born.getDate()].map((n) => String(n).padStart(2, "0"));
  return { iso: `${y}-${m}-${d}`, es: `${d}/${m}/${y}` };
}
/** The nth time of the grid's SECOND day: today's first times may be minutes away or already past, so a spec that
 * takes one would depend on the hour the suite runs. */
function laterSlot(slots, nth) {
  const days = [...new Set(slots.map((s) => s.dia))];
  return slots.filter((s) => s.dia === days[1])[nth];
}
const CHILD_AGE = 7;
const CHILD = { dni: "50888001", nombre: "Tomás", apellido: "Madre", nacimiento: bornYearsAgo(CHILD_AGE).es, sexo: "Masculino" };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j20" },
  journey: {
    sink: true,
    staff: [DESK, { usuario: DOCTOR.usuario, password: DOCTOR.password, rol: "medico", medico_id: DOCTOR.id }],
  },
});

test("J20 — a parent books a pediatric service for her child; the notice, the account and the doctor all agree on who the patient is", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const parentAccount = await seedPatient(app, PARENT);
  const strangerAccount = await seedPatient(app, STRANGER);
  const parent = await role({ session: parentAccount.session });
  const stranger = await role({ session: strangerAccount.session });
  const desk = await role();
  const doctor = await role();
  const run = {};

  await test.step("1. the wizard asks for the child, refuses a bad form, and confirms for the child", async () => {
    const page = parent.page;
    run.slot = laterSlot(await offeredSlots(page, SERVICE.id), 0);
    await (await showSlot(page, run.slot)).click();

    // The parent is an adult and the service takes children: she is asked for the child, nothing else.
    await expect(page.getByText("Pediatría atiende a menores: agregalo para pedir el turno.")).toBeVisible();
    const form = page.locator("#dep-form");
    await expect(form).toBeVisible();
    const save = form.locator(".dep-save");
    await expect(save, "the form is usable once its date control has loaded").toBeEnabled();
    const message = page.locator("#dep-msg");
    const fill = async ({ dni = CHILD.dni, nacimiento = CHILD.nacimiento } = {}) => {
      await page.locator("#dep-dni").fill(dni);
      await page.locator("#dep-nombre").fill(CHILD.nombre);
      await page.locator("#dep-apellido").fill(CHILD.apellido);
      await page.locator("#dep-nac").fill(nacimiento);
      await form.locator(".dep-seg").getByText(CHILD.sexo, { exact: true }).click();
      await page.locator("#dep-vinculo").selectOption("hijo");
    };

    // …a form without the sworn statement is refused with the server's own sentence;
    await fill();
    await save.click();
    await expect(message, "no sworn statement: refused, said plainly").toHaveText("Confirmá que los datos son correctos");
    // …her own DNI is not the child's;
    await page.locator("#dep-ddjj").check();
    await page.locator("#dep-dni").fill(PARENT.dni);
    await save.click();
    await expect(message).toHaveText("Ese DNI ya es el titular de la cuenta");
    // …and a date that is not a date never reaches the server.
    await page.locator("#dep-dni").fill(CHILD.dni);
    await page.locator("#dep-nac").fill("31/02/2019");
    await save.click();
    await expect(message).toHaveText("Revisá la fecha de nacimiento (DD/MM/AAAA).");
    expect((await runFixture(app, "dependents", { dni: PARENT.dni })).dependents, "nothing was saved by the refusals").toEqual([]);

    await page.locator("#dep-nac").fill(CHILD.nacimiento);
    await save.click();
    const summary = page.locator("#svc-form-host");
    await expect(page.locator("#wz-send")).toBeVisible();
    await expect(summary, "Confirmá is addressed to the child").toContainText("Tomás Madre", { useInnerText: true });
    await expect(summary).toContainText("50.888.001", { useInnerText: true });
    await expect(summary, "…and not to the parent").not.toContainText("30.888.001", { useInnerText: true });
    await expect.poll(async () => (await runFixture(app, "dependents", { dni: PARENT.dni })).dependents.map((d) => d.dni),
      { message: "the child is saved on the parent's account" }).toEqual([CHILD.dni]);
  });

  await test.step("2. the request is the child's, filed by the parent's account", async () => {
    const page = parent.page;
    await page.locator("#wz-send").click();
    await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada");
    const { requests } = await runFixture(app, "requests", { dni: CHILD.dni });
    expect(requests.length, "exactly one request").toBe(1);
    expect(requests[0].estado).toBe("pendiente");
    expect(requests[0].servicio_id).toBe(SERVICE.id);
    expect([requests[0].dni, requests[0].nombre, requests[0].apellido], "the patient is the child").toEqual([CHILD.dni, CHILD.nombre, CHILD.apellido]);
    expect(requests[0].solicitante_dni, "the requester is the parent's account").toBe(PARENT.dni);
    expect(requests[0].contacto, "the contact is the parent's phone").toBe(PARENT.contacto);
    run.requestId = requests[0].id;
    await expect.poll(() => sink.mails.filter((m) => m.to === PARENT.email && /Recibimos tu solicitud/.test(m.subject)).length,
      { message: "the «received» email goes to the parent" }).toBe(1);
  });

  await test.step("3. the desk reads the child's name and DNI and approves with the service's doctor", async () => {
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row")).toHaveCount(1);
    const detail = await openRequest(page, { hasText: "Tomás Madre" });
    await expect(detail).toHaveAttribute("data-id", String(run.requestId));
    // The DNI is the copyable figure of the card: dotted on screen, bare digits on the clipboard.
    await expect(detail.locator('[data-copy-dni="50888001"]').first(), "the desk reads the child's DNI").toHaveText("50.888.001");
    await expect(detail).toContainText(SERVICE.nombre, { useInnerText: true });
    const combo = doctorCombo(detail);
    await expect.poll(() => combo.names(), { message: "the desk is offered the doctors of the service only" }).toEqual([DOCTOR.nombre]);
    await combo.pick(DOCTOR.nombre);
    await expect(detail.locator("input.turno"), "the approval proposes the time the parent picked").toHaveValue(`${run.slot.dia}T${run.slot.hora}`);
    await approve(page, detail);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.medico_asignado).toBe(DOCTOR.nombre);
    run.when = artParts(request.turno_dt);
    expect(run.when.local).toBe(`${run.slot.dia}T${run.slot.hora}`);
  });

  await test.step("4. the parent is told by email and WhatsApp, greeting the child, and sees the appointment", async () => {
    await expect.poll(() => sink.mails.filter((m) => m.to === PARENT.email && /confirmado/.test(m.subject)).length,
      { message: "the confirmation email, to the parent" }).toBe(1);
    const mail = sink.mails.find((m) => m.to === PARENT.email && /confirmado/.test(m.subject));
    expect(mail.subject).toContain(SERVICE.nombre);
    expect(mail.text, "the email greets the child").toContain("Tomás Madre");
    expect(mail.text, "…and carries the time").toContain(run.when.time);
    const waDigits = PARENT.contacto.replace(/\D/g, "");
    await expect.poll(() => sink.whatsapp.filter((m) => m.chatId === `${waDigits}@c.us` && /quedó confirmado/.test(m.text)).length,
      { message: "the WhatsApp confirmation, to the parent's number" }).toBe(1);
    expect(sink.mails.filter((m) => m.to === STRANGER.email).length, "nothing reaches anybody else").toBe(0);

    const page = parent.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    await expect(page.locator("#ms-host").getByText(DOCTOR.nombre, { exact: true }).first()).toBeVisible();
    await expect(page.locator("#ms-host")).toContainText(run.when.time, { useInnerText: true });
    const listed = await page.evaluate(async () => (await (await fetch("/api/requests", { credentials: "same-origin" })).json()).solicitudes);
    expect(listed.map((r) => [r.id, r.paciente_nombre, r.paciente_dni]), "the account's list names the child as the patient")
      .toEqual([[run.requestId, "Tomás Madre", CHILD.dni]]);
  });

  await test.step("5. the child is listed in the account, and a second request for the child is refused", async () => {
    const page = parent.page;
    await page.goto("/account#deps", { waitUntil: "load" });
    const rows = page.locator("#acc-dependientes .dep-row");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("Tomás Madre", { useInnerText: true });
    await expect(rows.first()).toContainText(`${CHILD_AGE} años`, { useInnerText: true });

    // Booking again: the child is already the only person the service takes, so Confirmá comes straight up.
    const slots = await offeredSlots(page, SERVICE.id);
    const other = slots.find((s) => s.dia !== run.slot.dia || s.hora !== run.slot.hora);
    await (await showSlot(page, other)).click();
    await expect(page.locator("#wz-send")).toBeVisible();
    await expect(page.locator("#svc-form-host")).toContainText("Tomás Madre", { useInnerText: true });
    await page.locator("#wz-send").click();
    await expect(page.locator("#svc-form-host"), "the limit is told in its own words")
      .toContainText("Ya tenés 1 turno de Odontopediatría pedido sin atender, que es el máximo.", { useInnerText: true });
    expect((await runFixture(app, "requests", { dni: CHILD.dni })).requests.length, "still one request").toBe(1);
  });

  await test.step("6. the doctor finds the child in «Mis pacientes» and «Mis turnos»", async () => {
    const page = doctor.page;
    await signInStaff(page, DOCTOR.usuario, DOCTOR.password);
    await page.goto("/admin/my-patients", { waitUntil: "load" });
    const rows = page.locator("#sec-mis-pacientes table tbody tr");
    await expect(rows.first()).toBeVisible();
    await expect(rows, "the doctor has exactly this patient").toHaveCount(1);
    await expect(rows.first()).toContainText("Tomás Madre", { useInnerText: true });
    await expect(rows.first()).toContainText(run.when.time, { useInnerText: true });
    await page.goto("/admin/mis-turnos", { waitUntil: "load" });
    const week = page.locator("#sec-mis-turnos .mt-day table tbody tr");
    await expect(week.first()).toContainText("Tomás Madre", { useInnerText: true });
    await expect(week.first()).toContainText(run.when.time, { useInnerText: true });
  });

  await test.step("7. a stranger's account can neither book for the child nor see the appointment", async () => {
    const page = stranger.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    const result = await page.evaluate(async ({ child, service }) => {
      const post = await fetch("/api/requests", {
        method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
        body: JSON.stringify({ servicio_id: service.id, centro_slug: "maternity-center", paciente_dni: child }),
      });
      const list = await (await fetch("/api/requests", { credentials: "same-origin" })).json();
      return { status: post.status, body: await post.json().catch(() => ({})), listed: (list.solicitudes || []).length };
    }, { child: CHILD.dni, service: SERVICE });
    expect(result.status, "booking for somebody else's child is refused").toBe(403);
    expect(result.body.error).toBe("paciente_no_vinculado");
    expect(result.listed, "the stranger's account lists nothing").toBe(0);
    expect((await runFixture(app, "requests", { dni: CHILD.dni })).requests.length, "the refusal created nothing").toBe(1);
  });

  for (const [who, label] of [[parent, "the parent"], [stranger, "the stranger"], [desk, "the desk"], [doctor, "the doctor"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

// The age limits of the service apply to the person who will be seen, wherever the form is.
test("J20 — a child outside the service's age range is told so, and no route books her", async ({ app, role }) => {
  test.setTimeout(120_000);
  const mother = { dni: "30888003", nombre: "Irene", apellido: "Mayor", email: "irene.mayor@example.invalid", contacto: "+5492254800003" };
  const account = await seedPatient(app, mother);
  const person = await role({ session: account.session });
  const page = person.page;

  await test.step("1. the wizard says plainly that this service does not take a 14-year-old, and offers no way to send", async () => {
    const slot = laterSlot(await offeredSlots(page, SERVICE.id), 1);
    await (await showSlot(page, slot)).click();
    const form = page.locator("#dep-form");
    await expect(form).toBeVisible();
    await expect(form.locator(".dep-save")).toBeEnabled();
    await page.locator("#dep-dni").fill("50888003");
    await page.locator("#dep-nombre").fill("Mateo");
    await page.locator("#dep-apellido").fill("Mayor");
    await page.locator("#dep-nac").fill("01/03/2012");
    await form.locator(".dep-seg").getByText("Masculino", { exact: true }).click();
    await page.locator("#dep-vinculo").selectOption("hijo");
    await page.locator("#dep-ddjj").check();
    await form.locator(".dep-save").click();
    const gate = page.locator("#svc-form-host .gate");
    await expect(gate).toContainText("No podés pedir este turno para esta persona.", { useInnerText: true });
    await expect(gate, "the notice names the service").toContainText("Odontopediatría", { useInnerText: true });
    await expect(page.locator("#wz-send"), "there is nothing to send").toHaveCount(0);
  });

  await test.step("2. the server refuses it too: the adult herself and the 14-year-old on her account", async () => {
    const answers = await page.evaluate(async (service) => {
      const post = async (body) => {
        const r = await fetch("/api/requests", {
          method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
          body: JSON.stringify({ servicio_id: service.id, centro_slug: "maternity-center", ...body }),
        });
        return { status: r.status, body: await r.json().catch(() => ({})) };
      };
      return { herself: await post({}), child: await post({ paciente_dni: "50888003" }) };
    }, SERVICE);
    expect([answers.herself.status, answers.herself.body.error], "the adult cannot book pediatrics for herself").toEqual([400, "edad_fuera_de_rango"]);
    expect([answers.child.status, answers.child.body.error], "…nor the child who is over the limit").toEqual([400, "edad_fuera_de_rango"]);
    expect((await runFixture(app, "requests", { dni: "50888003" })).requests, "nothing was created").toEqual([]);
    expect((await runFixture(app, "requests", { dni: mother.dni })).requests).toEqual([]);
  });
  expect(person.errors).toEqual([]);
});

// Two children the service takes and a third who is too old: the wizard asks which one.
test("J20 — with two children the service takes, the parent chooses one; the one over the limit is not offered", async ({ app, role }) => {
  test.setTimeout(120_000);
  const mother = { dni: "30888004", nombre: "Paula", apellido: "Hijos", email: "paula.hijos@example.invalid", contacto: "+5492254800004" };
  const account = await seedPatient(app, mother);
  for (const child of [
    { dni: "50888011", nombre: "Lucas", nacimiento: bornYearsAgo(6).iso },
    { dni: "50888012", nombre: "Sofía", nacimiento: bornYearsAgo(10).iso },
    { dni: "50888013", nombre: "Mateo", nacimiento: "2012-03-01" },
  ]) {
    await runFixture(app, "seed-dependent", { spec: { holder: mother.dni, apellido: "Hijos", ...child } });
  }
  const person = await role({ session: account.session });
  const page = person.page;
  const slot = laterSlot(await offeredSlots(page, SERVICE.id), 2);
  await (await showSlot(page, slot)).click();

  const choices = page.locator(".dep-choice");
  await expect(choices, "only the children the service takes are offered").toHaveCount(2);
  await expect(choices.nth(0)).toContainText("Lucas Hijos", { useInnerText: true });
  await expect(choices.nth(1)).toContainText("Sofía Hijos", { useInnerText: true });
  await expect(page.locator("body")).not.toContainText("Mateo", { useInnerText: true });
  await choices.nth(1).click();
  await expect(page.locator("#wz-send")).toBeVisible();
  await expect(page.locator("#svc-form-host"), "Confirmá is addressed to the child chosen").toContainText("Sofía Hijos", { useInnerText: true });
  await page.locator("#wz-send").click();
  await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada");

  const sofia = (await runFixture(app, "requests", { dni: "50888012" })).requests;
  expect(sofia.length).toBe(1);
  expect([sofia[0].dni, sofia[0].solicitante_dni, sofia[0].nombre]).toEqual(["50888012", mother.dni, "Sofía"]);
  expect((await runFixture(app, "requests", { dni: "50888011" })).requests, "the other child has no request").toEqual([]);
  expect(person.errors).toEqual([]);
});
