// tests/e2e/journey-j23-redirect-and-batch-decisions.spec.mjs -- J23 ("The desk sends a request to the
// service the patient really needs, and decides several requests at once -- and every patient is
// told what was decided about HER request") cross-role journey acceptance.
//
// Two tests, one per way the desk works through the queue:
//   «Derivar»  a patient asked for General medicine but needs a cardiologist: the desk redirects the
//              request to Cardiología, the doctor list changes to the new service's doctors, the old
//              doctor does not survive, and the approval, the email, the centre and the patient's
//              account all name the NEW service; redirecting to the laboratory takes the doctor away.
//   batch      the desk ticks several requests and decides them together: approve (each gets its own
//              time), reject with one reason, resolve by recommendation (approve what is clean, reject
//              what the reading refused); a row that cannot be approved does not stop the others and the
//              desk is told which one and why; every patient gets the mail of her own decision.
//   The approval route itself also refuses a doctor of another service and drops a doctor named on a
//   laboratory request, whatever the screen offers (last step of the first test).
//
// External services: the mail provider is a local receiver (lib/journey.mjs, `journey: { sink: true }`).
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, artParts,
  offeredSlots, sendRequest, DESK,
} from "./lib/journey.mjs";

const GENERAL = { id: "medicina-general-riverside-clinic", nombre: "Medicina general" };
const CARDIO = { id: "cardiologia-sampleton-hospital", nombre: "Cardiología", centro: "sampleton-hospital" };
const CARDIOLOGISTS = ["Camila Levin", "Diego Walsh", "Florencia Acosta", "Sergio Ruiz"];

const person = (n, nombre, apellido) => ({
  dni: String(30666000 + n), nombre, apellido,
  email: `${nombre}.${apellido}@example.invalid`.toLowerCase(), contacto: `+54922546600${String(n).padStart(2, "0")}`,
});

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j23" },
  journey: { sink: true, staff: [DESK] },
});

/** The service combo «Derivar a» of a request's detail pane. */
function serviceCombo(detail) {
  const box = detail.locator(".svc-edit .cmb");
  return {
    box,
    value: () => box.locator("input.svc-sel").inputValue(),
    async pick(name) {
      await box.locator(".cmb-in").focus();
      // the item starts with the service's name (a centre may follow it when two services share the name)
      await box.locator(".cmb-it", { hasText: new RegExp("^" + name) }).first().dispatchEvent("pointerdown");
    },
  };
}

/**
 * The desk's list of times is filled from the agenda of the service and doctor chosen, a moment after
 * the choice. Runs `choose` (the click that changes service or doctor), waits for the answer of the
 * agenda to be painted -- the list holds exactly the times of the answer -- and returns them.
 * `mayBeEmpty` lets the agenda answer with no time at all: a same-day opening (the laboratory, 7 to 10)
 * has none left once its hours have passed, and the desk is then left with the typed time.
 * @param {import("@playwright/test").Page} page
 * @param {import("@playwright/test").Locator} detail the request's detail pane
 * @param {string} fragment text of the agenda request's URL that tells it apart (service or doctor)
 * @param {() => Promise<void>} choose
 * @param {{ mayBeEmpty?: boolean }} [opts]
 */
async function timesAfter(page, detail, fragment, choose, { mayBeEmpty = false } = {}) {
  const answered = page.waitForResponse((r) => /\/api\/admin\/appointment-slots\?/.test(r.url()) && r.url().includes(fragment));
  await choose();
  const offered = (await (await answered).json()).libres.map((t) => `${t.dia}T${t.hora}`);
  if (!mayBeEmpty) expect(offered.length, "the agenda offers times").toBeGreaterThan(0);
  await expect.poll(() => detail.locator("select.turno-sel option").evaluateAll((o) => o.map((x) => x.value).filter((v) => v && v !== "__otro")),
    { message: "the list of times is the one the agenda answered" }).toEqual(offered);
  return offered;
}

test.describe.configure({ mode: "serial" });

test("J23 — «Derivar»: the request goes to the service the patient needs, with that service's doctors", async ({ app, sink, role }) => {
  test.setTimeout(180_000);
  const dora = person(1, "Dora", "Derivada");
  const lia = person(2, "Lia", "Laboratorio");
  const doraAccount = await seedPatient(app, dora);
  const liaAccount = await seedPatient(app, lia);
  const patient = await role({ session: doraAccount.session });
  const patient2 = await role({ session: liaAccount.session });
  const desk = await role();
  const run = {};

  await test.step("1. two patients ask for General medicine", async () => {
    for (const [who, p] of [[patient, dora], [patient2, lia]]) {
      const slots = await offeredSlots(who.page, GENERAL.id);
      // The first two times of the grid's second day (today's may be minutes away or past by the time of the approval).
      const later = slots.filter((s) => s.dia === [...new Set(slots.map((x) => x.dia))][1]);
      p.slot = later[p === dora ? 0 : 1];
      await sendRequest(who.page, p.slot);
      const { requests } = await runFixture(app, "requests", { dni: p.dni });
      p.requestId = requests[0].id;
      expect(requests[0].servicio_id).toBe(GENERAL.id);
    }
  });

  await test.step("2. the desk redirects Dora to Cardiología: the doctors become the cardiologists, the old doctor is gone", async () => {
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row")).toHaveCount(2);
    const detail = await openRequest(page, { hasText: "Dora Derivada" });
    await expect(detail.locator(".sol-pide-line1 a")).toHaveText(GENERAL.nombre);
    const doctors = doctorCombo(detail);
    run.before = await doctors.names();
    expect(run.before.length, "General medicine has doctors of its own").toBeGreaterThan(0);
    for (const name of CARDIOLOGISTS) expect(run.before, "…and they are not cardiologists").not.toContain(name);
    await doctors.pick(run.before[0]);
    await expect(doctors.box.locator("input.med-sel")).toHaveValue(run.before[0]);

    const service = serviceCombo(detail);
    await detail.locator("button.svc-derive").click();
    await expect(service.box).toBeVisible();
    await service.pick(CARDIO.nombre);
    await expect(detail.locator(".sol-pide-line1 a"), "the pane now names the service that will be approved").toHaveText(CARDIO.nombre);
    expect(await doctors.value(), "the doctor of the previous service does not survive the redirection").toBe("");
    await expect.poll(async () => (await doctors.names()).sort(), { message: "the desk is offered the cardiologists" }).toEqual([...CARDIOLOGISTS].sort());
    // Times offered now are the cardiologist's agenda, not the general practitioner's.
    const offered = await timesAfter(page, detail, "medico=Camila", () => doctors.pick("Camila Levin"));
    run.proposed = await detail.locator("select.turno-sel").inputValue();
    expect(offered, "the time proposed is one of the cardiologist's").toContain(run.proposed);
    await approve(page, detail);

    const { request } = await runFixture(app, "request", { id: String(dora.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.servicio_id, "the request now belongs to the new service").toBe(CARDIO.id);
    expect(request.medico_asignado).toBe("Camila Levin");
    run.when = artParts(request.turno_dt);
    expect(run.when.local, "the stored time is the one proposed").toBe(run.proposed);
  });

  await test.step("3. Dora is told about Cardiología, and her account shows it", async () => {
    await expect.poll(() => sink.mails.filter((m) => m.to === dora.email && /confirmado/.test(m.subject)).length,
      { message: "the confirmation email" }).toBe(1);
    const mail = sink.mails.find((m) => m.to === dora.email && /confirmado/.test(m.subject));
    expect(mail.subject, "the subject names the new service").toContain(CARDIO.nombre);
    expect(mail.subject).not.toContain(GENERAL.nombre);
    expect(mail.text).toContain(run.when.time);
    expect(mail.text, "…and the new centre").toContain("Sampleton");
    expect(mail.text, "…not the one of the service she asked for").not.toContain("Riverside");

    const page = patient.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    await expect(page.locator("#ms-host").getByText("Camila Levin", { exact: true }).first()).toBeVisible();
    await expect(page.locator("#ms-host")).toContainText(CARDIO.nombre, { useInnerText: true });
    await expect(page.locator("#ms-host")).not.toContainText(GENERAL.nombre, { useInnerText: true });
  });

  await test.step("4. redirecting Lia to the laboratory takes the doctor away; she is approved with none", async () => {
    const page = desk.page;
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    const detail = await openRequest(page, { hasText: "Lia Laboratorio" });
    const doctors = doctorCombo(detail);
    await expect(doctors.box).toBeVisible();
    await detail.locator("button.svc-derive").click();
    // The laboratory's own agenda replaces the list of times; no doctor is asked for.
    // The laboratory opens the same day only (7 to 10): from the late morning its agenda has no time left,
    // and the desk is left with the free field, which holds the time the patient asked for.
    const offered = await timesAfter(page, detail, "servicio_id=laboratorio-anexo", () => serviceCombo(detail).pick("Laboratorio"), { mayBeEmpty: true });
    await expect(detail.locator(".sol-pide-line1 a")).toHaveText("Laboratorio");
    await expect(doctors.box, "a laboratory has no doctor to assign").toBeHidden();
    let proposed;
    if (offered.length) {
      proposed = await detail.locator("select.turno-sel").inputValue();
      expect(offered, "the time proposed is one of the laboratory's").toContain(proposed);
    } else {
      await expect(detail.locator("select.turno-sel"), "no agenda: only the typed time is left").toHaveValue("__otro");
      proposed = await detail.locator("input.turno-free").inputValue();
      expect(proposed, "the typed time is the one the patient asked for").toBe(`${lia.slot.dia}T${lia.slot.hora}`);
    }
    await approve(page, detail);
    run.lab = proposed;
    const { request } = await runFixture(app, "request", { id: String(lia.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.servicio_id).toBe("laboratorio-anexo");
    expect(request.medico_asignado, "no doctor on a laboratory appointment").toBeFalsy();
    expect(artParts(request.turno_dt).local, "the stored time is the one proposed").toBe(run.lab);
  });

  await test.step("5. the server keeps the line the screen draws: a doctor of another service is refused, a laboratory takes none", async () => {
    // What the panel never offers, a hand-made request still can send: the approval route must refuse
    // (or, for a laboratory, quietly drop) the doctor itself. Two fresh pending requests, approved
    // straight through the route as the signed-in desk.
    const clinic = [["Wanda", CARDIO.id, CARDIO.centro], ["Lola", "laboratorio-anexo", "diagnostic-center"]];
    const seeded = [];
    for (const [i, [nombre, servicio, centro]] of clinic.entries()) {
      const p = person(3 + i, nombre, "Servidor");
      seeded.push((await runFixture(app, "seed-visit", { spec: { dni: p.dni, nombre: p.nombre, apellido: p.apellido, servicio_id: servicio, centro, pending: true } })).request_id);
    }
    const [wrongId, labId] = seeded;
    const when = `${artParts(new Date(Date.now() + 3 * 86_400_000).toISOString()).date}T09:00:00-03:00`;
    const approveRaw = (id, body) => desk.context.request.post(`/api/admin/requests/${id}/approve`, { data: { turno_dt: when, ...body } });

    // A general practitioner on a cardiology request: refused, and the request is untouched.
    const refused = await approveRaw(wrongId, { medico: run.before[0] });
    expect(refused.status(), "a doctor who does not serve the service cannot be assigned to it").toBe(409);
    expect((await refused.json()).error).toBe("medico_no_atiende_servicio");
    const untouched = (await runFixture(app, "request", { id: String(wrongId) })).request;
    expect(untouched.estado, "the refused approval changed nothing").toBe("pendiente");
    expect(untouched.medico_asignado, "…and assigned nobody").toBeFalsy();

    // A doctor named on a laboratory request: approved, and the doctor is dropped by the server.
    const lab = await approveRaw(labId, { medico: "Camila Levin" });
    expect(lab.status(), "a laboratory approval carrying a doctor is accepted").toBe(200);
    const stored = (await runFixture(app, "request", { id: String(labId) })).request;
    expect(stored.estado).toBe("aprobada");
    expect(stored.servicio_id).toBe("laboratorio-anexo");
    expect(stored.medico_asignado, "no doctor is stored on a laboratory appointment").toBeFalsy();
    expect(stored.medico_asignado_id).toBeFalsy();

    // The refused request is still pending: close it, so the next test starts from an empty queue.
    const closed = await desk.context.request.post(`/api/admin/requests/${wrongId}/reject`, { data: { motivo: "Datos incompletos" } });
    expect(closed.status()).toBe(200);
  });

  expect(patient.errors).toEqual([]);
  expect(patient2.errors).toEqual([]);
  expect(desk.errors).toEqual([]);
});

test("J23 — deciding several requests at once: approve, reject with a reason, recommendation, and a row that cannot be approved", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const people = [
    person(11, "Aldo", "Lote"), person(12, "Berta", "Lote"), person(13, "Cira", "Lote"),
    person(14, "Dario", "Lote"), person(15, "Emma", "Lote"), person(17, "Gina", "Lote"),
  ];
  const [aldo, berta, cira, dario, emma, gina] = people;
  const roles = {};
  for (const p of people) roles[p.dni] = await role({ session: (await seedPatient(app, p)).session });
  const desk = await role();
  const mailTo = (p, pattern) => sink.mails.filter((m) => m.to === p.email && pattern.test(m.subject));

  await test.step("1. six patients ask for a cardiology appointment, each at a time of her own", async () => {
    const slots = (await offeredSlots(roles[aldo.dni].page, CARDIO.id));
    // Never today (its times may be minutes away or past by the time the desk approves them): the first LATER day
    // that has eight times.
    const day = [...new Set(slots.map((s) => s.dia))].slice(1).find((d) => slots.filter((s) => s.dia === d).length >= 8);
    expect(day, "some day offers eight times").toBeTruthy();
    const times = slots.filter((s) => s.dia === day);
    for (const [i, p] of people.entries()) {
      p.slot = times[i];
      await roles[p.dni].page.goto(`/service/${CARDIO.id}/book?slots=1`, { waitUntil: "load" });
      await sendRequest(roles[p.dni].page, p.slot);
      p.requestId = (await runFixture(app, "requests", { dni: p.dni })).requests[0].id;
    }
    await signInStaff(desk.page, DESK.usuario, DESK.password);
    await desk.page.goto("/admin/solicitudes", { waitUntil: "load" });
    // A group shows its first five; the rest load inside the group itself.
    await expect(desk.page.locator("#list .sol-row")).toHaveCount(5);
    await desk.page.locator("#list .sol-grp-mas").filter({ hasText: "Cargar 1 más" }).click();
    await expect(desk.page.locator("#list .sol-row")).toHaveCount(6);
    await expect(desk.page.locator("#list .sol-grp-mas")).toHaveCount(0);
  });

  const rowOf = (p) => desk.page.locator("#list [data-sol-row]").filter({ hasText: `${p.nombre} ${p.apellido}` });
  const tick = (p) => rowOf(p).locator(".sol-ck").check();
  const toast = () => desk.page.locator("#toast");

  await test.step("2. two ticked rows approved together: each at the time she asked for, each told", async () => {
    await tick(aldo);
    await tick(berta);
    await expect(desk.page.locator("[data-bulk-bar]")).toContainText("2 seleccionadas", { useInnerText: true });
    await desk.page.locator('[data-bulk-action="aprobar"]').click();
    await expect(toast()).toContainText("2 aprobadas.");
    for (const p of [aldo, berta]) {
      const { request } = await runFixture(app, "request", { id: String(p.requestId) });
      expect(request.estado, `${p.nombre} is approved`).toBe("aprobada");
      expect(artParts(request.turno_dt).local, "…at the time she asked for").toBe(`${p.slot.dia}T${p.slot.hora}`);
      await expect.poll(() => mailTo(p, /confirmado/).length, { message: `${p.nombre}'s confirmation` }).toBe(1);
      expect(mailTo(p, /confirmado/)[0].text, "…names her own time").toContain(p.slot.hora);
    }
    await expect(desk.page.locator("#list .sol-row"), "the approved rows left the pending list").toHaveCount(4);
    await expect(desk.page.locator("#hsi-recientes .hsi-recientes-f"), "…and are listed as just approved").toHaveCount(2);
  });

  await test.step("3. two rows rejected together with one reason: each patient reads it", async () => {
    await tick(cira);
    await tick(dario);
    await desk.page.locator('[data-bulk-action="rechazar"]').click();
    await expect(desk.page.locator("#bulk-reject-dialog")).toBeVisible();
    await expect(desk.page.locator("#bulk-reject-count")).toHaveText("Se aplicará a 2 solicitudes.");
    // Without a reason the dialog refuses to go on.
    await desk.page.locator("#bulk-reject-confirm").click();
    await expect(toast()).toContainText("Elegí o escribí el motivo del rechazo.");
    // (not the first reason: that is what a rejection gets when nobody chooses)
    const reason = await desk.page.locator("#bulk-reject-reason option").nth(2).getAttribute("value");
    expect(reason).toBeTruthy();
    await desk.page.locator("#bulk-reject-reason").selectOption(reason);
    await desk.page.locator("#bulk-reject-confirm").click();
    await expect(toast()).toContainText("2 rechazadas.");
    for (const p of [cira, dario]) {
      const { request } = await runFixture(app, "request", { id: String(p.requestId) });
      expect(request.estado).toBe("rechazada");
      expect(request.motivo).toBe(reason);
      await expect.poll(() => sink.mails.filter((m) => m.to === p.email && !/confirmado|Recibimos/.test(m.subject)).length,
        { message: `${p.nombre}'s rejection mail` }).toBe(1);
      expect(sink.mails.find((m) => m.to === p.email && !/confirmado|Recibimos/.test(m.subject)).text, "…carries the reason").toContain(reason);
      expect(mailTo(p, /confirmado/).length, "…and no confirmation").toBe(0);
    }
    // The patient sees the same reason in her account.
    const page = roles[cira.dni].page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    await expect(page.locator("#ms-host")).toContainText(reason, { useInnerText: true });
  });

  await test.step("4. a row a colleague has just decided is reported, and the others still go through", async () => {
    // The queue is open on this desk's screen; a colleague on another computer rejects Emma's request.
    await desk.page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(rowOf(emma)).toHaveCount(1);
    const colleague = await role();
    await signInStaff(colleague.page, DESK.usuario, DESK.password);
    const rejected = await colleague.context.request.post(`/api/admin/requests/${emma.requestId}/reject`, { data: { motivo: "Datos incompletos" } });
    expect(rejected.status()).toBe(200);

    // This screen still shows Emma as pending: she is ticked together with Gina.
    await tick(emma);
    await tick(gina);
    await desk.page.locator('[data-bulk-action="aprobar"]').click();
    await expect(toast(), "the batch says what could not be done and why, and carries on").toContainText("1 aprobada · 1 sin procesar: La solicitud ya fue resuelta.");
    expect((await runFixture(app, "request", { id: String(gina.requestId) })).request.estado, "the other row went through").toBe("aprobada");
    expect((await runFixture(app, "request", { id: String(emma.requestId) })).request.estado, "Emma stays as her colleague decided").toBe("rechazada");
    await expect.poll(() => mailTo(gina, /confirmado/).length, { message: "Gina is told" }).toBe(1);
    expect(mailTo(emma, /confirmado/).length, "Emma is not told she was approved").toBe(0);
    await expect(rowOf(emma), "…and the reloaded queue no longer lists her as pending").toHaveCount(0);
  });

  await test.step("5. «Resolver según recomendación» decides what the system recommends for the ticked rows", async () => {
    // A request with no referral has nothing to doubt: the recommendation is to approve it.
    const hugo = person(18, "Hugo", "Lote");
    const hugoRole = await role({ session: (await seedPatient(app, hugo)).session });
    const slots = await offeredSlots(hugoRole.page, CARDIO.id);
    const free = slots.find((s) => !people.some((p) => p.slot.dia === s.dia && p.slot.hora === s.hora));
    await sendRequest(hugoRole.page, free);
    hugo.requestId = (await runFixture(app, "requests", { dni: hugo.dni })).requests[0].id;

    await desk.page.goto("/admin/solicitudes", { waitUntil: "load" });
    await tick(hugo);
    await desk.page.locator('[data-bulk-action="sugerencia"]').click();
    await expect(toast()).toContainText("1 aprobada · 0 rechazadas.");
    const { request } = await runFixture(app, "request", { id: String(hugo.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(CARDIOLOGISTS, "the recommendation names a doctor of the service, not just any doctor").toContain(request.medico_asignado);
    const catalog = (await runFixture(app, "doctors")).doctors;
    expect(catalog.find((d) => d.id === request.medico_asignado_id)?.nombre, "the stored doctor id is the same doctor as the stored name").toBe(request.medico_asignado);
    await expect.poll(() => mailTo(hugo, /confirmado/).length, { message: "Hugo is told" }).toBe(1);
  });

  await test.step("6. «Resolver según recomendación» rejects what the system recommends rejecting, with the reason of the reading", async () => {
    // A request whose referral the AI could not read: the recommendation is to reject, with that reason.
    const ines = person(19, "Ines", "Lote");
    await seedPatient(app, ines);
    ines.requestId = (await runFixture(app, "seed-unreadable-referral", {
      spec: { dni: ines.dni, nombre: ines.nombre, apellido: ines.apellido, contacto: ines.contacto, servicio_id: CARDIO.id, centro: CARDIO.centro },
    })).request_id;
    await desk.page.goto("/admin/solicitudes", { waitUntil: "load" });
    await tick(ines);
    await desk.page.locator('[data-bulk-action="sugerencia"]').click();
    await expect(toast()).toContainText("0 aprobadas · 1 rechazada.");
    const { request } = await runFixture(app, "request", { id: String(ines.requestId) });
    expect(request.estado, "the recommendation was to reject").toBe("rechazada");
    expect(request.motivo, "…with the reason of the reading").toBe("La orden no se lee con claridad.");
    await expect.poll(() => sink.mails.filter((m) => m.to === ines.email && !/confirmado|Recibimos/.test(m.subject)).length,
      { message: "Ines is told of the rejection" }).toBe(1);
    expect(sink.mails.find((m) => m.to === ines.email && !/confirmado|Recibimos/.test(m.subject)).text, "…with its reason").toContain("La orden no se lee con claridad.");
    expect(mailTo(ines, /confirmado/).length, "…and not that she was approved").toBe(0);
  });

  expect(desk.errors).toEqual([]);
});
