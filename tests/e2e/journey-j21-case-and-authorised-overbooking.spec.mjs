// tests/e2e/journey-j21-case-and-authorised-overbooking.spec.mjs -- J21 ("A patient who cannot
// wait tells the hospital her case; the desk fits her in on top of a taken time -- but only where
// the doctor's agenda allows it, and only as many as it allows") cross-role journey acceptance.
//
// The roles: an admin who creates a doctor-owned service and its doctor, the schedule manager who
// builds the doctor's hours (web booking off: only the staff gives times) and later switches
// «Sobreturnos» on, three patients on the public site, and the front desk who approves.
//
// Terminal outcome: with web booking off the public wizard offers no time and asks for the patient's
// case instead; the desk reads each case and gives a time; a second patient on a taken time is refused
// until the agenda authorises overbooking and then fits (flagged as an authorised overbooking, same
// doctor, same minute); the next one is refused again once the allowance is used up and stays pending.
//
// Steps:
//   1. Admin creates a service whose hours belong to its doctor, and the doctor
//   2. The schedule manager builds a daily 10:00-11:00 window and switches web booking OFF (overbooking
//      still off), saves
//   3. Three patients open the wizard: no times, «Contanos tu caso» is required, then each sends her case
//   4. The desk reads the cases and approves the first patient at 10:00 on a day it chooses
//   5. The second patient on the same minute: refused («Ese médico ya tiene un turno…»), the overbooking
//      box appears, ticked it is refused again («Ese horario no tiene sobreturnos configurados.»)
//   6. The schedule manager switches «Sobreturnos» on (up to 50 % more per day) and saves; it is still
//      there after a reload
//   7. The desk approves the second patient as an authorised overbooking: same doctor, same minute
//   8. The third patient on that minute is refused (allowance used up) and stays pending
//   9. The first two patients are told by email at the same time; the third is told nothing
//
// External services: the mail provider is a local receiver (lib/journey.mjs, `journey: { sink: true }`).
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS,
} from "./lib/journey.mjs";

const GESTOR = { usuario: "e2e-gestor", password: LOGIN_PHRASE, rol: "gestor_agendas" };
const SERVICE_ID = "diagnostico-por-imagenes-j21";
const SERVICE = { nombre: "Diagnóstico por imágenes J21", centro: "maternity-center", modo: "solicitud", cluster: "diagnostico", horario: "Según el médico" };
const DOCTOR = { id: "irene-imagen", nombre: "Irene Imagen", form: { nombre_pila: "Irene", apellido: "Imagen", especialidad: "Diagnóstico por imágenes", centro: "maternity-center" } };
const PEOPLE = [
  { key: "first", dni: "30999101", nombre: "Elisa", apellido: "Primera", email: "elisa.primera@example.invalid", contacto: "+5492254910001", caso: "Tengo un control que no puede esperar a la semana próxima." },
  { key: "second", dni: "30999102", nombre: "Felipe", apellido: "Segundo", email: "felipe.segundo@example.invalid", contacto: "+5492254910002", caso: "Me operan el lunes y necesito el estudio antes." },
  { key: "third", dni: "30999103", nombre: "Gabriela", apellido: "Tercera", email: "gabriela.tercera@example.invalid", contacto: "+5492254910003", caso: "Viajo el miércoles y necesito el resultado." },
];
const REFUSALS = {
  // A taken minute on «Aprobar» also points at the refreshed agenda (admin-app.js, TURNO_TOMADO).
  taken: "Ese médico ya tiene un turno a esa hora. Marcá sobreturno solo si fue autorizado. O elegí otro horario de la agenda actualizada.",
  notConfigured: "Ese horario no tiene sobreturnos configurados.",
  exhausted: "Se agotó el cupo de sobreturnos de esta agenda.",
};

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j21" },
  journey: { sink: true, staff: [GESTOR, DESK] },
});

test("J21 — the case of a patient who cannot wait, and the desk's authorised overbooking", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const people = {};
  for (const p of PEOPLE) {
    const account = await seedPatient(app, p);
    people[p.key] = { ...p, role: await role({ session: account.session }) };
  }
  const admin = await role();
  const gestor = await role();
  const desk = await role();
  const run = {};

  // The day the desk gives: three days ahead (installation clock), at 10:00 -- inside the daily window.
  run.day = artParts(new Date(Date.now() + 3 * 86_400_000).toISOString()).date;
  run.turno = `${run.day}T10:00`;

  /** Opens the doctor's hours editor as the schedule manager and returns the panel. */
  const openAgenda = async () => {
    const page = gestor.page;
    await page.goto(`/admin/doctors?item=${DOCTOR.id}`, { waitUntil: "load" });
    const agenda = page.locator("[data-tabpanel=agenda]");
    await expect(agenda.locator("input[type=checkbox][value='1']").first()).toBeVisible();
    return agenda;
  };
  const save = async () => {
    const page = gestor.page;
    const saved = page.waitForResponse((r) => new URL(r.url()).pathname.startsWith("/api/admin/content/doctors")
      && r.request().method() !== "GET");
    await page.locator("button.btn-accent", { hasText: "Guardar" }).click();
    expect((await saved).status()).toBe(200);
    await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible();
  };

  await test.step("1. admin creates a service whose hours belong to its doctor, and the doctor", async () => {
    await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
    const service = await admin.page.request.post("/api/admin/content/services", { data: SERVICE });
    expect(service.status(), await service.text()).toBe(200);
    expect((await service.json()).item.id).toBe(SERVICE_ID);
    const doctor = await admin.page.request.post("/api/admin/content/doctors", { data: DOCTOR.form });
    expect(doctor.status(), await doctor.text()).toBe(200);
    expect((await doctor.json()).item.id).toBe(DOCTOR.id);
  });

  await test.step("2. the schedule manager builds a daily 10:00-11:00 window with web booking OFF", async () => {
    await signInStaff(gestor.page, GESTOR.usuario, GESTOR.password);
    const agenda = await openAgenda();
    for (const day of ["1", "2", "3", "4", "5", "6", "0"]) await agenda.locator(`input[type=checkbox][value='${day}']`).first().check();
    const times = agenda.locator("input.hor-in[type=text]");
    await times.nth(0).fill("10:00");
    await times.nth(1).fill("11:00");
    await agenda.locator("input.hor-in--num").first().fill("30");
    const service = agenda.locator(".cmb:has(.fr-svc-sel)").first();
    await service.locator(".cmb-in").focus();
    await service.locator(".cmb-it", { hasText: "J21" }).dispatchEvent("pointerdown");
    await expect(service.locator("input.fr-svc-sel"), "the window is tagged to the service").toHaveValue(SERVICE_ID);
    // «Reserva por la web» off: only the staff gives times. «Sobreturnos» stays off for now.
    const web = agenda.locator("[data-limits-web]");
    await expect(web, "web booking is on until the manager switches it off").toBeChecked();
    await agenda.locator("label.hor-switch:has([data-limits-web])").click();
    await expect(web).not.toBeChecked();
    await expect(agenda.locator("[data-limits-overbooking]"), "overbooking is off by default").not.toBeChecked();
    await save();
  });

  await test.step("3. the public wizard offers no time and asks each patient for her case", async () => {
    for (const p of Object.values(people)) {
      const page = p.role.page;
      await page.goto(`/service/${SERVICE_ID}/book?slots=1`, { waitUntil: "load" });
      // No times: «Contanos tu caso» is a short conversation, not an empty box (the product owner, 2026-11-07).
      const chat = page.locator("#wz-casochat");
      await expect(chat.locator(".wzc-msg--asst").first(), "no times: the case conversation opens instead").toBeVisible();
      await expect(page.locator("#wz-caso"), "the plain box stays for when the AI is off").toBeHidden();
      await expect(page.locator(".wz-seg-opt"), "no time to pick").toHaveCount(0);
      await expect(page.locator("#svc-form-host")).toContainText("Si es una urgencia, llamá al 107", { useInnerText: true });
      // The case is the request: without an answer the wizard does not go on.
      await page.locator("#wz-horario-next").click();
      await expect(page.locator("#wz-caso-msg")).toHaveText("Contestá en la conversación para que el hospital evalúe tu caso.");
      await chat.locator(".wzc-input").fill(p.caso);
      await chat.locator(".wzc-send").click();
      await expect(chat.locator(".wzc-msg--user"), "the answer stays in the conversation").toContainText(p.caso);
      await expect(chat.locator(".wzc-msg--asst"), "one clarifying question follows").toHaveCount(2);
      // The patient may stop early: Continuar sends what they said so far.
      await page.locator("#wz-horario-next").click();
      await expect(page.locator("#svc-form-host"), "Confirmá repeats the case the hospital will read").toContainText(p.caso, { useInnerText: true });
      await page.locator("#wz-send").click();
      await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada");
      await expect(page.locator("#svc-form-host")).toContainText("El hospital te va a contactar para evaluar tu caso.", { useInnerText: true });
      const { requests } = await runFixture(app, "requests", { dni: p.dni });
      expect(requests.length).toBe(1);
      expect(requests[0].estado).toBe("pendiente");
      expect(requests[0].nota_paciente, "the case travels with the request").toBe(p.caso);
      p.requestId = requests[0].id;
    }
  });

  /** On the desk's list: opens the patient's request, reads her case and gives doctor and time. Returns the detail pane. */
  const prepare = async (p) => {
    const page = desk.page;
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    const detail = await openRequest(page, { hasText: `${p.nombre} ${p.apellido}` });
    await expect(detail, "the desk reads the patient's case").toContainText(p.caso, { useInnerText: true });
    await doctorCombo(detail).pick(DOCTOR.nombre);
    // The list of times is filled from the doctor's agenda a moment after the choice and would overwrite what is
    // typed before: wait for it to settle. With web booking off the desk still sees the hours hidden from the web (decision 2026-11,
    // test_desk_hidden_hours): the window's 10:00 and 10:30, then «Otro horario…». The case here is not a grid
    // hour: the desk picks «Otro horario…» and types the minute itself.
    // One retrying poll on a single read: the list has settled on the doctor's hours (never the loading option),
    // each of them the window's 10:00 or 10:30, at least one, and «Otro horario…» last.
    await expect.poll(() => detail.locator("select.turno-sel").evaluate((sel) => {
      const values = [...sel.options].map((x) => x.value);
      const hours = values.slice(0, -1);
      return !sel.disabled && values.at(-1) === "__otro" && hours.length > 0 && hours.every((h) => /T10:(00|30)$/.test(h));
    }), { message: "the list follows the doctor: the window's hidden hours, then «Otro horario…»" }).toBe(true);
    await detail.locator("select.turno-sel").selectOption("__otro");
    await expect(detail.locator("input.turno-free")).toBeVisible();
    await detail.locator("input.turno-free").fill(run.turno);
    return detail;
  };
  /** Presses «Aprobar» on a prepared pane; resolves with the server's answer. */
  const press = async (detail) => {
    const answered = desk.page.waitForResponse((r) => /\/api\/admin\/requests\/\d+\/approve$/.test(new URL(r.url()).pathname) && r.request().method() === "POST");
    await detail.locator('[data-act="aprobar"]').click();
    return answered;
  };
  /** The overbooking box only exists after a refusal for a taken minute: ticks it. */
  const tickOverbooking = async (detail) => {
    const box = detail.locator("input.sobreturno");
    await expect(box, "the overbooking box appeared with the refusal").toBeVisible();
    await box.check();
  };

  await test.step("4. the desk reads the cases and approves the first patient at 10:00", async () => {
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    const detail = await prepare(people.first);
    // The summary first; the whole conversation one tap away.
    const ver = detail.locator("[data-caso-ver]");
    await expect(ver).toHaveText("Ver conversación");
    await ver.click();
    const conversation = detail.locator(".sol-caso-chat");
    await expect(conversation, "the desk reads what the patient answered").toContainText(people.first.caso, { useInnerText: true });
    await expect(conversation, "and what the assistant asked").toContainText("¿Desde cuándo te pasa?", { useInnerText: true });
    await expect(ver).toHaveText("Ocultar conversación");
    const response = await press(detail);
    expect(response.status(), "the first patient takes the free minute").toBe(200);
    await expect(desk.page.locator("#toast")).toContainText("Aprobada.");
    const { request } = await runFixture(app, "request", { id: String(people.first.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.sobreturno, "an ordinary appointment").toBe(false);
    expect(request.medico_asignado_id).toBe(DOCTOR.id);
    run.when = artParts(request.turno_dt);
    expect(run.when.local).toBe(run.turno);
  });

  await test.step("5. the second patient on the same minute is refused, ticked or not", async () => {
    const detail = await prepare(people.second);
    const first = await press(detail);
    expect(first.status()).toBe(409);
    await expect(detail.locator(".sol-act-err")).toHaveText(REFUSALS.taken);
    await tickOverbooking(detail);
    const second = await press(detail);
    expect(second.status()).toBe(409);
    expect((await second.json()).error).toBe("sobreturno_no_configurado");
    await expect(detail.locator(".sol-act-err")).toHaveText(REFUSALS.notConfigured);
    const { request } = await runFixture(app, "request", { id: String(people.second.requestId) });
    expect(request.estado, "still waiting").toBe("pendiente");
  });

  await test.step("6. the schedule manager switches «Sobreturnos» on (up to 50 % more per day); it stays after a reload", async () => {
    const agenda = await openAgenda();
    const overbooking = agenda.locator("[data-limits-overbooking]");
    await expect(overbooking).not.toBeChecked();
    await agenda.locator("label.hor-switch:has([data-limits-overbooking])").click();
    await expect(overbooking).toBeChecked();
    const percent = agenda.locator("[data-limits-overbooking-percent]");
    await expect(percent).toBeEnabled();
    await percent.fill("50");
    await percent.dispatchEvent("change");
    await save();
    const reloaded = await openAgenda();
    await expect(reloaded.locator("[data-limits-overbooking]")).toBeChecked();
    await expect(reloaded.locator("[data-limits-overbooking-percent]")).toHaveValue("50");
    await expect(reloaded.locator("[data-limits-web]"), "web booking is still off").not.toBeChecked();
    const doctors = await (await admin.page.request.get("/api/admin/content/doctors")).json();
    const franja = doctors.items.find((d) => d.id === DOCTOR.id).centros[0].franjas[0];
    expect(franja.cupos.overbooking_percent, "the agenda stores the allowance").toBe(50);
  });

  await test.step("7. the desk fits the second patient on top of the first, as an authorised overbooking", async () => {
    const detail = await prepare(people.second);
    expect((await press(detail)).status(), "without the box it is still refused").toBe(409);
    await tickOverbooking(detail);
    const response = await press(detail);
    expect(response.status()).toBe(200);
    await expect(desk.page.locator("#toast")).toContainText("Aprobada como sobreturno autorizado.");
    const { request } = await runFixture(app, "request", { id: String(people.second.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.sobreturno, "flagged as an authorised overbooking").toBe(true);
    expect(request.medico_asignado_id, "same doctor").toBe(DOCTOR.id);
    expect(artParts(request.turno_dt).local, "same minute").toBe(run.turno);
    // The flag is read back through the product's own read paths, apart from the write: the approval's
    // answer, and the doctor's day queue, which shows both patients on the minute and marks only the second.
    expect((await response.json()).solicitud.sobreturno, "the approval's answer says it is an overbooking").toBe(true);
    const queue = await admin.page.request.get(`/api/admin/day-queue?dia=${run.day}&medico=${DOCTOR.id}`);
    expect(queue.status()).toBe(200);
    const onTheMinute = (await queue.json()).grupos.flatMap((g) => g.turnos).filter((t) => t.hora === "10:00");
    expect(onTheMinute.map((t) => [t.paciente, t.sobreturno]), "the day queue holds both on the minute, flagged only the extra one")
      .toEqual([[`${people.first.nombre} ${people.first.apellido}`, false], [`${people.second.nombre} ${people.second.apellido}`, true]]);
  });

  await test.step("8. the third patient is refused: the day's allowance is used up", async () => {
    const detail = await prepare(people.third);
    const plain = await press(detail);
    expect(plain.status()).toBe(409);
    await expect(detail.locator(".sol-act-err")).toHaveText(REFUSALS.taken);
    await tickOverbooking(detail);
    const response = await press(detail);
    expect(response.status()).toBe(409);
    expect((await response.json()).error).toBe("sobreturno_cupo_agotado");
    await expect(detail.locator(".sol-act-err")).toHaveText(REFUSALS.exhausted);
    const { request } = await runFixture(app, "request", { id: String(people.third.requestId) });
    expect(request.estado, "she stays pending for the desk to call").toBe("pendiente");
  });

  await test.step("9. the two patients with a time are told it by email; the third is told nothing yet", async () => {
    for (const p of [people.first, people.second]) {
      await expect.poll(() => sink.mails.filter((m) => m.to === p.email && /confirmado/.test(m.subject)).length,
        { message: `${p.nombre}'s confirmation email` }).toBe(1);
      expect(sink.mails.find((m) => m.to === p.email && /confirmado/.test(m.subject)).text, "…with the same time").toContain(run.when.time);
    }
    expect(sink.mails.filter((m) => m.to === people.third.email && /confirmado/.test(m.subject)).length).toBe(0);
  });

  for (const [who, label] of [[admin, "admin"], [gestor, "gestor"], [desk, "desk"], ...Object.entries(people).map(([k, p]) => [p.role, `patient ${k}`])]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});
