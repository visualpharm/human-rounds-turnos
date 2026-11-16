// tests/e2e/journey-j25-invited-doctor-first-day.spec.mjs -- J25 ("An invited doctor's first day")
// cross-role journey acceptance.
//
// On launch day every doctor is created by the admin and invited; nothing before this journey covered what the
// doctor meets AFTER the invitation (admin-forms-audit stops at «sets a password on the link»; J13 is a schedule
// MANAGER editing windows). The rules pinned, with their sources:
//   - «Invitar médico» needs no field (responsable del producto, 2026-10-19; docs/architecture/admin-panel-y-auth.md): a name alone creates
//     the doctor AND its account, and with no email the admin gets the one-use link to copy; with an email the link
//     goes out by mail.
//   - A doctor missing name, specialty or center is `incompleto` and never reaches a public surface (docs/architecture/admin-panel-y-auth.md
//     invariants); the onboarding refuses to finish until name, specialty, email and a center exist, with the exact
//     «Antes de empezar, completá …» message (admin_panel/doctor_workspace.py), and lands where the product says
//     (`next`: the account's security step while there is no second factor).
//   - The doctor owns his own weekly hours and closures at his center (admin_content.edit_item): what he saves is what
//     the public wizard offers, and what the wizard stops offering after a closure; he sees the closure worded in
//     «Mis agendas». His reach stops at himself: another doctor's agenda is «no_encontrado», a center that is not
//     his is `centro_no_permitido`, a schedule a service owns is `agenda_servicio_aplica`, and the onboarding is the
//     session doctor's alone (admin gets 403).
//
// Roles: the admin, the invited doctor (first on the link, then through the login form), a second invited doctor
// (by mail), a doctor whose service owns the schedule, a patient on the public site, the front desk.
//
// Steps:
//   test 1  the invitation and the first sign-in: link to copy / mail, set a password, onboarding refuses, an
//           incomplete doctor is not public
//   test 2  the first agenda: onboarding completes, hours reach the wizard, a closure leaves the wizard, a patient
//           books, the desk approves, the doctor reads it in «Mis turnos»
//   test 3  the boundary: not another doctor's agenda, not another center, not a service-owned schedule, not the
//           onboarding of someone else
//
// External services: the mail provider and the WhatsApp gateway are local receivers (`sink`).
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS, showSlot, gridSlots,
} from "./lib/journey.mjs";

const SERVICE_ID = "diagnostico-por-imagenes-j25";
const SERVICE = { nombre: "Diagnóstico por imágenes J25", centro: "maternity-center", modo: "solicitud", cluster: "diagnostico", horario: "Según el médico" };
const CENTER = "maternity-center";
const OTHER_CENTER = "diagnostic-center";
const SPECIALTY = "Diagnóstico por imágenes";
const NEWCOMER = { nombre_pila: "Valeria", apellido: "Primerdia", nombre: "Valeria Primerdia", id: "valeria-primerdia", usuario: "valeria.primerdia", email: "valeria.primerdia@example.invalid" };
const COLLEAGUE = { nombre_pila: "Bruno", apellido: "Colega", nombre: "Bruno Colega", id: "bruno-colega", usuario: "bruno.colega", email: "bruno.colega@example.invalid" };
// A pack doctor whose hours belong to her service (J15 uses the same one): the doctor-side refusal of a schedule edit.
const OWNED = { id: "levin-camila", usuario: "e2e-dr-levin" };
const PATIENT = { dni: "30250001", nombre: "Marta", apellido: "Pacienta", email: "marta.pacienta@example.invalid", contacto: "+5492254250001" };
const EXPECTED_TIMES = ["10:00", "10:30", "11:00", "11:30"];
const COMPLETE_URL = "/api/admin/onboarding/complete";
const NEXT_STEP = "/admin/account";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_j25" },
  journey: {
    sink: true,
    staff: [DESK, { usuario: OWNED.usuario, password: LOGIN_PHRASE, rol: "medico", medico_id: OWNED.id }],
  },
});

test.describe.configure({ mode: "serial" });

// State the three tests hand to each other (they run in order).
const run = {};

/** The (day, time) chips the booking wizard of the service offers, and none when it paints a «no turnos» notice. */
async function offered(page) {
  await page.goto(`/service/${SERVICE_ID}/book?slots=1`, { waitUntil: "load" });
  await expect(page.locator("#svc-title")).toBeVisible();
  const chips = page.locator(".wz-seg-opt");
  // «Ahora no hay turnos.» for a service nobody attends yet, «No hay turnos disponibles» for one whose doctor has none.
  const none = page.getByText(/Ahora no hay turnos|No hay turnos disponibles/);
  await expect(chips.first().or(none)).toBeVisible();
  return (await chips.count()) ? gridSlots(page, SERVICE_ID) : [];
}

/** Whole days ahead of today (installation clock) of an ISO date. */
function daysAhead(iso) {
  const today = artParts(new Date().toISOString()).date;
  return Math.round((Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000);
}

const dayNumber = (iso) => String(parseInt(iso.slice(8, 10), 10));

/** The doctor ids the public doctors API lists (what the patient site can ever show). */
async function publicDoctorIds(page) {
  const response = await page.request.get("/api/doctors");
  expect(response.status()).toBe(200);
  return (await response.json()).medicos.map((m) => m.id);
}

/** The doctors API as the admin reads it: { id: doctor }. */
async function catalogDoctors(page) {
  const response = await page.request.get("/api/admin/content/doctors");
  expect(response.status()).toBe(200);
  return Object.fromEntries((await response.json()).items.map((d) => [d.id, d]));
}

const accountOf = async (page, doctorId) =>
  (await (await page.request.get("/api/admin/users")).json()).items.find((u) => u.medico_id === doctorId);

test("J25 — the admin invites a doctor with a name only; the doctor signs in on the link and the onboarding will not finish half-filled", async ({ app, role, sink }) => {
  test.setTimeout(180_000);
  const admin = await role();
  const doctor = await role();
  const stranger = await role();

  await test.step("1. the admin creates the service the doctor will attend", async () => {
    await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
    const service = await admin.page.request.post("/api/admin/content/services", { data: SERVICE });
    expect(service.status(), await service.text()).toBe(200);
    expect((await service.json()).item.id).toBe(SERVICE_ID);
  });

  await test.step("2. «Invitar médico» with only a name creates the doctor and an invited account, and hands back the one-use link to copy", async () => {
    await admin.page.goto("/admin/doctors", { waitUntil: "load" });
    await admin.page.click("#med-invite-open");
    await admin.page.fill("#med-inv-nombre", NEWCOMER.nombre_pila);
    await admin.page.fill("#med-inv-apellido", NEWCOMER.apellido);
    await admin.page.locator('#med-invite-form button[type="submit"]').click();
    await expect(admin.page.locator("#med-invite-result")).toContainText("Copiá el enlace y mandáselo al médico. Sirve una sola vez, durante 7 días.");
    await expect(admin.page.locator(".doctor-user-invite-username"), "the admin is told the username to pass on with the link").toHaveText(NEWCOMER.usuario);
    run.link = await admin.page.locator(".doctor-user-link-input").inputValue();
    expect(new URL(run.link).pathname).toBe("/admin/reset-password");
    expect(sink.mails.filter((m) => m.to.includes(NEWCOMER.usuario)), "no email was known, so nothing was mailed").toEqual([]);

    const created = (await catalogDoctors(admin.page))[NEWCOMER.id];
    expect(created.incompleto, "a doctor with a name only is flagged incomplete").toBe(true);
    const account = await accountOf(admin.page, NEWCOMER.id);
    expect(account.usuario).toBe(NEWCOMER.usuario);
    expect(account.cuenta_estado, "the account is invited, not yet entered").toBe("invitado");
    expect(account.onboarding_completed_at).toBeNull();
    await admin.page.reload({ waitUntil: "load" });
    await expect(admin.page.locator("tr", { hasText: NEWCOMER.nombre }).locator('td[data-col="cuenta"]')).toHaveText("Invitado");
  });

  await test.step("3. with an email, the invitation is mailed instead: recipient, link and username in the body", async () => {
    const created = await admin.page.request.post("/api/admin/content/doctors", { data: {
      nombre_pila: COLLEAGUE.nombre_pila, apellido: COLLEAGUE.apellido, especialidad: "Psiquiatría", centro: CENTER, email: COLLEAGUE.email, _invitar: true } });
    expect(created.status(), await created.text()).toBe(200);
    const body = await created.json();
    expect(body.item.id).toBe(COLLEAGUE.id);
    expect(body.cuenta.email_enviado, "the invitation left by mail").toBe(true);
    expect(body.cuenta.enlace, "a mailed invitation does not also hand the link to the admin").toBeUndefined();
    await expect.poll(() => sink.mails.filter((m) => m.to.includes(COLLEAGUE.email)).length).toBe(1);
    const mail = sink.mails.find((m) => m.to.includes(COLLEAGUE.email));
    expect(mail.text).toMatch(/\/admin\/reset-password\?token=\S+/);
    expect(mail.text).toContain(`Usuario: ${COLLEAGUE.usuario}`);
    expect(mail.text).toContain("válido por 7 días");
  });

  await test.step("4. the doctor opens the link, sees his username, sets a password and is taken to the onboarding", async () => {
    const link = new URL(run.link);
    await doctor.page.goto(link.pathname + link.search, { waitUntil: "load" });
    await expect(doctor.page.locator(".reset-user")).toHaveText(`Tu usuario: ${NEWCOMER.usuario}`);
    await doctor.page.fill("#reset-pass", LOGIN_PHRASE);
    await doctor.page.fill("#reset-confirm", LOGIN_PHRASE);
    await doctor.page.locator('#reset-form button[type="submit"]').click();
    await doctor.page.waitForURL((url) => url.pathname === "/admin/onboarding");
    await expect(doctor.page.locator('input[name="nombre_pila"]')).toHaveValue(NEWCOMER.nombre_pila);
    await expect(doctor.page.locator('input[name="apellido"]')).toHaveValue(NEWCOMER.apellido);
    expect((await accountOf(admin.page, NEWCOMER.id)).cuenta_estado, "after the first entry the admin sees «Ingresó»").toBe("ingreso");
  });

  await test.step("5. the link is single-use: opened again it says it expired and offers no password form", async () => {
    const link = new URL(run.link);
    await stranger.page.goto(link.pathname + link.search, { waitUntil: "load" });
    await expect(stranger.page.getByRole("heading", { name: "El enlace venció" })).toBeVisible();
    await expect(stranger.page.locator("#reset-form")).toHaveCount(0);
  });

  await test.step("6. signing in through the login form with the new password lands on the onboarding, not on the queue", async () => {
    const again = await role();
    await signInStaff(again.page, NEWCOMER.usuario, LOGIN_PHRASE);
    expect(new URL(again.page.url()).pathname).toBe("/admin/onboarding");
    expect(again.errors).toEqual([]);
  });

  await test.step("7. an empty onboarding form sends nothing: the doctor stays on the page", async () => {
    let sent = 0;
    doctor.page.on("request", (r) => { if (r.url().includes(COMPLETE_URL)) sent++; });
    await expect(doctor.page.locator("#doctor-workspace")).not.toHaveAttribute("inert", /.*/);
    await doctor.page.locator("[data-finish]").click();
    await expect(doctor.page.locator('select[name="especialidad"]'), "the browser stops on the first empty required field").toBeFocused();
    expect(sent, "no completion request left the page").toBe(0);
    expect(new URL(doctor.page.url()).pathname).toBe("/admin/onboarding");
  });

  await test.step("8. the server refuses to finish and names exactly what is missing; filling specialty and email narrows it to the center", async () => {
    const refused = await doctor.page.request.post(COMPLETE_URL, { data: {}, failOnStatusCode: false });
    expect(refused.status()).toBe(400);
    expect(await refused.json()).toEqual({
      error: "Antes de empezar, completá tu especialidad, tu email, dónde atendés.",
      faltan: ["especialidad", "email", "centro"],
    });
    // The doctor's own self-service save of the two fields (the onboarding form sends the same PUT).
    const saved = await doctor.page.request.put(`/api/admin/content/doctors/${NEWCOMER.id}`, {
      data: { especialidad: SPECIALTY, email: NEWCOMER.email }, failOnStatusCode: false });
    expect(saved.status(), await saved.text()).toBe(200);
    const narrowed = await doctor.page.request.post(COMPLETE_URL, { data: {}, failOnStatusCode: false });
    expect(narrowed.status()).toBe(400);
    expect(await narrowed.json()).toEqual({ error: "Antes de empezar, completá dónde atendés.", faltan: ["centro"] });
    expect((await accountOf(admin.page, NEWCOMER.id)).onboarding_completed_at, "a refused completion leaves the account unfinished").toBeNull();
  });

  await test.step("9. an incomplete doctor is on no public surface", async () => {
    expect(await publicDoctorIds(stranger.page)).not.toContain(NEWCOMER.id);
    expect(await publicDoctorIds(stranger.page), "the complete colleague is public").toContain(COLLEAGUE.id);
    // A doctor page that does not exist for the public sends the visitor back to the list instead.
    const hidden = await stranger.page.request.get(`/doctor/${NEWCOMER.id}`, { maxRedirects: 0, failOnStatusCode: false });
    expect(hidden.status(), "his page is not served").toBeGreaterThanOrEqual(300);
    expect(hidden.status()).toBeLessThan(400);
    expect(hidden.headers().location).toBe("/doctors");
    expect((await stranger.page.request.get(`/doctor/${COLLEAGUE.id}`, { maxRedirects: 0, failOnStatusCode: false })).status(),
      "the complete colleague's page is served").toBe(200);
  });

  for (const [who, label] of [[admin, "admin"], [doctor, "doctor"], [stranger, "visitor"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});

test("J25 — the first agenda: the onboarding completes, the hours reach the wizard, a closure leaves it, and the booked patient shows up in «Mis turnos»", async ({ app, role }) => {
  test.setTimeout(240_000);
  const patientAccount = await seedPatient(app, PATIENT);
  const admin = await role();
  const doctor = await role();
  const desk = await role();
  const patient = await role({ session: patientAccount.session });

  await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
  await signInStaff(doctor.page, NEWCOMER.usuario, LOGIN_PHRASE);

  await test.step("1. before the doctor has any hours (and still no center) the patient is offered nothing for the service", async () => {
    expect(await offered(patient.page), "no times are offered").toEqual([]);
  });

  await test.step("2. the doctor picks his center and weekly hours on the onboarding and finishes: the answer says where he goes next", async () => {
    const page = doctor.page;
    await page.goto("/admin/onboarding", { waitUntil: "load" });
    await expect(page.locator('select[name="especialidad"]'), "what he already gave is kept").toHaveValue(SPECIALTY);
    await expect(page.locator('input[name="email"]')).toHaveValue(NEWCOMER.email);
    await page.locator("[data-center-select]").selectOption(CENTER);
    const section = page.locator("[data-center-new]");
    for (const day of ["1", "2", "3", "4", "5", "6", "0"]) await section.locator(`input[data-day="${day}"]`).check();
    const rows = section.locator(".dw-day");
    await expect(rows).toHaveCount(7);
    for (let i = 0; i < 7; i++) {
      const row = rows.nth(i);
      await row.locator("[data-add-slot]").click();
      await row.locator('[data-time="desde"]').fill("10:00");
      await row.locator('[data-time="hasta"]').fill("12:00");
      await row.locator('[data-time="cada_min"]').fill("30");
    }
    let completion = null;
    await page.route(`**${COMPLETE_URL}`, async (route) => {
      const response = await route.fetch();
      completion = { status: response.status(), body: await response.json() };
      await route.fulfill({ response });
    });
    await page.locator("[data-finish]").click();
    await expect.poll(() => completion, { message: "the completion request was answered" }).not.toBeNull();
    expect(completion.status).toBe(200);
    expect(completion.body, "with no second factor yet, the next step is the account's security page").toEqual({ ok: true, next: NEXT_STEP });
    await page.waitForURL((url) => url.pathname === NEXT_STEP);
    const account = await accountOf(admin.page, NEWCOMER.id);
    expect(account.onboarding_completed_at, "the onboarding is stamped done").not.toBeNull();
    expect(account.cuenta_estado).toBe("perfil_completo");
  });

  await test.step("3. the doctor is now complete and public, and the wizard offers exactly the hours he gave", async () => {
    const stored = (await catalogDoctors(admin.page))[NEWCOMER.id];
    expect(stored.incompleto, "complete: the flag is gone").toBeUndefined();
    expect(stored.centros.map((c) => c.centro)).toEqual([CENTER]);
    expect(await publicDoctorIds(patient.page)).toContain(NEWCOMER.id);
    let slots = [];
    await expect.poll(async () => { slots = await offered(patient.page); return slots.length; },
      { message: "the hours reach the public wizard", intervals: [500, 1000], timeout: 20_000 }).toBeGreaterThan(0);
    expect([...new Set(slots.map((s) => s.hora))].sort(), "only the 10:00-12:00 half-hours he typed").toEqual(EXPECTED_TIMES);
    const future = [...new Set(slots.map((s) => s.dia))].filter((d) => daysAhead(d) >= 1);
    expect(future.length, "the grid reaches several days ahead").toBeGreaterThanOrEqual(3);
    for (const day of future) {
      expect(slots.filter((s) => s.dia === day).map((s) => s.hora), `every offered day carries the same four times (${day})`).toEqual(EXPECTED_TIMES);
    }
    run.before = slots;
  });

  await test.step("4. «Mis agendas» shows the saved week after a reload: seven days, 10:00 to 12:00 every 30 minutes", async () => {
    await doctor.page.goto("/admin/my-schedules", { waitUntil: "load" });
    const summaries = await doctor.page.locator(".dw-summary").allInnerTexts();
    expect(summaries.length).toBe(7);
    // The shared length is said once above the cards (the product owner, 02.11).
    for (const line of summaries) expect(line).toMatch(/10:00 a 12:00$/);
    await expect(doctor.page.locator(".dw-common")).toHaveText("Turnos de 30 min");
    expect(summaries.map((s) => s.split(",")[0]).sort()).toEqual(["Dom", "Jue", "Lun", "Mar", "Mié", "Sáb", "Vie"].sort());
    await expect(doctor.page.locator(".dw-closure"), "no closure yet").toHaveCount(0);
  });

  await test.step("5. the doctor ties the hours to the service and adds a vacation range; saved, both survive a reload", async () => {
    const page = doctor.page;
    // Two consecutive offered days at least two days ahead, so the closure reads as dates (not «Hoy» / «Mañana»).
    const days = [...new Set(run.before.map((s) => s.dia))].filter((d) => daysAhead(d) >= 2);
    [run.closedFrom, run.closedTo] = days;
    expect(daysAhead(run.closedTo) - daysAhead(run.closedFrom), "the two offered days are consecutive").toBe(1);
    await page.getByRole("link", { name: "Editar horarios" }).click();
    await page.waitForURL((url) => url.pathname === "/admin/doctors" && url.searchParams.get("item") === NEWCOMER.id);
    await expect(page.locator("#cnt-form")).toBeVisible();
    const service = page.locator(".cmb:has(.fr-svc-sel)").first();
    await service.locator(".cmb-in").focus();
    await service.locator(".cmb-it", { hasText: "J25" }).dispatchEvent("pointerdown");
    await expect(service.locator("input.fr-svc-sel"), "the window is tagged to the service").toHaveValue(SERVICE_ID);
    await page.locator("[data-add-excepcion]").first().click();
    await page.locator("[data-exc-desde-fecha]").first().fill(run.closedFrom);
    await page.locator("[data-exc-hasta-fecha]").first().fill(run.closedTo);
    await page.locator("[data-exc-motivo]").first().fill("Vacaciones");
    const saved = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/admin/content/doctors/${NEWCOMER.id}` && r.request().method() === "PUT");
    await page.locator('button[form="cnt-form"][type="submit"]').click();
    expect((await saved).status()).toBe(200);
    await expect(page.locator("#cnt-save-status")).toHaveText(/Guardado/);
    const stored = (await catalogDoctors(admin.page))[NEWCOMER.id];
    const center = stored.centros.find((c) => c.centro === CENTER);
    expect(center.excepciones).toEqual([{ tipo: "cerrado", desde_fecha: run.closedFrom, hasta_fecha: run.closedTo, motivo: "Vacaciones" }]);
    expect(center.franjas.every((f) => f.servicio === SERVICE_ID), "every window names the service").toBe(true);
  });

  await test.step("6. «Mis agendas» words the closure for the doctor", async () => {
    await doctor.page.goto("/admin/my-schedules", { waitUntil: "load" });
    const closure = doctor.page.locator(".dw-closure").first();
    await expect(closure).toHaveText(new RegExp(`^Sin atención: .*\\b${dayNumber(run.closedFrom)}\\b.* a .*\\b${dayNumber(run.closedTo)}\\b.* \\(Vacaciones\\)$`));
  });

  await test.step("7. the wizard stops offering the closed dates and keeps every other day", async () => {
    let slots = [];
    await expect.poll(async () => {
      slots = await offered(patient.page);
      return slots.some((s) => s.dia === run.closedFrom || s.dia === run.closedTo) ? "still offered" : "closed";
    }, { message: "the closed dates leave the wizard", intervals: [1000], timeout: 20_000 }).toBe("closed");
    const days = new Set(slots.map((s) => s.dia));
    for (const day of new Set(run.before.map((s) => s.dia))) {
      if (day === run.closedFrom || day === run.closedTo || daysAhead(day) < 1) continue;
      expect(days.has(day), `${day} is not closed and is still offered`).toBe(true);
    }
    run.after = slots;
  });

  await test.step("8. the patient books the first time of an open day; the request waits for the desk on this doctor's service", async () => {
    const day = [...new Set(run.after.map((s) => s.dia))].filter((d) => daysAhead(d) >= 1)[0];
    run.slot = run.after.find((s) => s.dia === day && s.hora === EXPECTED_TIMES[0]);
    expect(run.slot, "the first time of that day is offered").toBeTruthy();
    await (await showSlot(patient.page, run.slot)).click();
    await patient.page.locator("#wz-send").click();
    await expect(patient.page.locator("#svc-title")).toHaveText("Solicitud enviada");
    const { requests } = await runFixture(app, "requests", { dni: PATIENT.dni });
    expect(requests.length).toBe(1);
    expect(requests[0].estado).toBe("pendiente");
    expect(requests[0].servicio_id).toBe(SERVICE_ID);
    run.requestId = requests[0].id;
  });

  await test.step("9. the desk is offered the new doctor for this request and approves at the time asked for", async () => {
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    await desk.page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(desk.page.locator("#list .sol-row")).toHaveCount(1);
    const detail = await openRequest(desk.page);
    const combo = doctorCombo(detail);
    await expect.poll(() => combo.names(), { message: "the pool of the service is the doctor who just gave his hours" }).toEqual([NEWCOMER.nombre]);
    await combo.pick(NEWCOMER.nombre);
    await expect(detail.locator("input.turno")).toHaveValue(`${run.slot.dia}T${run.slot.hora}`);
    await approve(desk.page, detail);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.medico_asignado_id).toBe(NEWCOMER.id);
    expect(request.medico_asignado).toBe(NEWCOMER.nombre);
  });

  await test.step("10. «Mis turnos» of the doctor lists that patient at that day and time, and the API agrees", async () => {
    const answered = doctor.page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/my-appointments");
    await doctor.page.goto("/admin/mis-turnos", { waitUntil: "load" });
    expect((await answered).status()).toBe(200);
    const host = doctor.page.locator("#sec-mis-turnos");
    await expect(host).toContainText(`${PATIENT.nombre} ${PATIENT.apellido}`, { useInnerText: true });
    await expect(host).toContainText(run.slot.hora, { useInnerText: true });
    const mine = await (await doctor.page.request.get("/api/admin/my-appointments?days=14")).json();
    const row = mine.turnos.find((t) => String(t.id) === String(run.requestId));
    expect(row, "the approved appointment is in the doctor's own list").toBeTruthy();
    expect(JSON.stringify(row)).toContain(run.slot.hora);
  });

  for (const [who, label] of [[admin, "admin"], [doctor, "doctor"], [desk, "desk"], [patient, "patient"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});

test("J25 — the boundary: a doctor reaches only himself, a service-owned schedule stays the service's, and the onboarding is the session doctor's alone", async ({ app, role }) => {
  test.setTimeout(180_000);
  const admin = await role();
  const doctor = await role();
  const owned = await role();
  await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
  await signInStaff(doctor.page, NEWCOMER.usuario, LOGIN_PHRASE);
  const bruno = (await catalogDoctors(admin.page))[COLLEAGUE.id];

  await test.step("1. the doctor's catalog list holds his own card and nobody else's", async () => {
    const list = await (await doctor.page.request.get("/api/admin/content/doctors")).json();
    expect(list.items.map((d) => d.id)).toEqual([NEWCOMER.id]);
  });

  await test.step("2. another doctor's agenda cannot be edited: the answer is «no_encontrado» and his hours stay as they were", async () => {
    const attempt = await doctor.page.request.put(`/api/admin/content/doctors/${COLLEAGUE.id}`, {
      data: { centros: [{ centro: CENTER, franjas: [{ dias: [1], desde: "08:00", hasta: "09:00", cada_min: 20 }], excepciones: [] }] },
      failOnStatusCode: false });
    expect(attempt.status()).toBe(404);
    expect(await attempt.json()).toEqual({ error: "no_encontrado" });
    expect((await catalogDoctors(admin.page))[COLLEAGUE.id].centros, "his colleague's hours are untouched").toEqual(bruno.centros);
  });

  await test.step("3. he cannot move his own hours to a center that is not his: «centro_no_permitido»", async () => {
    const attempt = await doctor.page.request.put(`/api/admin/content/doctors/${NEWCOMER.id}`, {
      data: { centros: [{ centro: OTHER_CENTER, franjas: [{ dias: [1], desde: "10:00", hasta: "12:00", cada_min: 30 }], excepciones: [] }] },
      failOnStatusCode: false });
    expect(attempt.status()).toBe(403);
    expect(await attempt.json()).toEqual({ error: "centro_no_permitido" });
    const stored = (await catalogDoctors(admin.page))[NEWCOMER.id];
    expect(stored.centros.map((c) => c.centro), "his center did not change").toEqual([CENTER]);
  });

  await test.step("4. a doctor whose service owns the schedule is told so and cannot edit it, by the page or the API", async () => {
    await signInStaff(owned.page, OWNED.usuario, LOGIN_PHRASE);
    await owned.page.goto("/admin/my-schedules", { waitUntil: "load" });
    await expect(owned.page.locator("#doctor-workspace")).toContainText(
      "El horario lo define el servicio. Lo cambia el administrador.", { useInnerText: true });
    await expect(owned.page.getByRole("link", { name: "Editar horarios" }), "no edit door for a schedule that is not his").toHaveCount(0);
    const before = (await catalogDoctors(admin.page))[OWNED.id];
    const attempt = await owned.page.request.put(`/api/admin/content/doctors/${OWNED.id}`, {
      data: { centros: [{ centro: before.centros[0].centro, franjas: [{ dias: [1], desde: "07:00", hasta: "08:00", cada_min: 20 }], excepciones: [] }] },
      failOnStatusCode: false });
    expect(attempt.status()).toBe(409);
    expect((await attempt.json()).error).toBe("agenda_servicio_aplica");
    expect((await catalogDoctors(admin.page))[OWNED.id].centros, "the service's hours are unchanged").toEqual(before.centros);
  });

  await test.step("5. the onboarding is the session doctor's: asked for a colleague by query it still shows his own data", async () => {
    await doctor.page.goto(`/admin/onboarding?doctor=${COLLEAGUE.id}&item=${COLLEAGUE.id}`, { waitUntil: "load" });
    await expect(doctor.page.locator('input[name="nombre_pila"]')).toHaveValue(NEWCOMER.nombre_pila);
    await expect(doctor.page.locator('input[name="apellido"]')).toHaveValue(NEWCOMER.apellido);
  });

  await test.step("6. the admin cannot open or complete a doctor's onboarding: 403 on both, and the colleague's account stays unfinished", async () => {
    expect((await admin.page.request.get("/admin/onboarding", { failOnStatusCode: false })).status()).toBe(403);
    const attempt = await admin.page.request.post(COMPLETE_URL, { data: {}, failOnStatusCode: false });
    expect(attempt.status()).toBe(403);
    expect(await attempt.json()).toEqual({ error: "solo_medico" });
    const account = await accountOf(admin.page, COLLEAGUE.id);
    expect(account.onboarding_completed_at).toBeNull();
    expect(account.cuenta_estado, "completing his own onboarding did not touch the colleague's account").toBe("invitado");
  });

  for (const [who, label] of [[admin, "admin"], [doctor, "doctor"], [owned, "service-owned doctor"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});
