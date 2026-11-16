// tests/e2e/journey-j26-limits-and-contention.spec.mjs -- J26 ("how many turnos one patient may hold, two
// patients after the same last time, and the double click") cross-role journey acceptance.
//
// Why it exists: on the first day the popular doctors' first times are wanted by several people at once, and
// elderly patients press «Pedir el turno» twice or press «Atrás». Three product rules decide what happens
// (AGENTS.md invariants; modules/turnos/booking_limits.py; docs/architecture/wizard-y-servicio.md):
//   - how many appointments one patient may hold at once is a NUMBER resolved doctor -> service -> site
//     (default 1; kinesiologia 10). Only PENDING and APPROVED-FUTURE appointments of the SAME clinical DNI
//     and service count; the limit is enforced only on the patient's own POST, never on the front desk's;
//   - the patient gets the SOLUTION, never the error: a refusal carries the next concrete step (a link to
//     «Mis turnos», the other times of the grid), never a raw code or the homepage;
//   - one request per tap: the wizard sends the hold token as idem_key, so a doubled tap or a retry answers
//     with the request that already exists (no second row, no second «Recibimos» mail, no false limit 409).
//
// Terminal outcomes
//   1. A patient at her limit is refused in the wizard with the limit's own sentence and a link to «Mis
//      turnos»; nothing is created. A cancelled request, a rejected one and one whose approved time is past
//      hold no place; another service, a dependent (another clinical DNI) are separate counts; an approved
//      FUTURE appointment does count. Kinesiologia takes ten and refuses the eleventh in the plural.
//   2. Three patients see the same last time. The one who picks it first keeps it; the one whose own hold
//      lapsed meanwhile is sent back to the grid with «<día, hora>: otra persona acaba de tomar ese turno…» (no raw error, no
//      request row for the loser, the rest already loaded), the one who clicks late is told the same
//      named time. The time ends up on exactly one request. Two other patients who press
//      the same time in the same instant (both calls started together) end with one hold granted, one refused,
//      one request and one email.
//   3. A double click on «Pedir el turno» and a replay of the same request make exactly one request and one
//      «Recibimos tu solicitud» email, never a limit refusal; «Atrás» from the receipt never shows an enabled
//      send button again.
//
// External services: the mail provider is a local receiver (lib/journey.mjs, `journey: { sink: true }`).
import {
  test, expect, runFixture, seedPatient, offeredSlots, hasSlot, sendRequest, cancelFromAccount, showSlot, gridSlots,
} from "./lib/journey.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_j26" },
  journey: { sink: true },
});

test.describe.configure({ mode: "serial" });

const CARDIO = { id: "cardiologia-sampleton-hospital", nombre: "Cardiología", centro: "sampleton-hospital", doctorId: "levin-camila" };
const PSYCHO = { id: "psicologia-lakeview-clinic", nombre: "Psicología adultos", centro: "lakeview-clinic" };
const KINE = { id: "kinesiologia-lakeview-clinic", nombre: "Kinesiología y rehabilitación", centro: "lakeview-clinic" };
const GENERAL = { id: "medicina-general-riverside-clinic", nombre: "Medicina general", centro: "riverside-clinic" };
const DENTAL = { id: "odontologia-riverside-clinic", nombre: "Odontología general", centro: "riverside-clinic" };

const patient = (n, nombre, apellido) => ({
  dni: `30266${String(n).padStart(3, "0")}`, nombre, apellido,
  email: `${nombre}.${apellido}`.toLowerCase().normalize("NFD").replace(/[^a-z.]/g, "") + "@example.invalid",
  contacto: `+54922541${String(n).padStart(5, "0")}`,
});

/** The nth time of the day-th day of a grid (day 0 is today and may be minutes away: callers start at 1). */
function slotOf(slots, dayIndex, nth = 0) {
  const days = [...new Set(slots.map((s) => s.dia))];
  expect(days.length, "the grid offers several days").toBeGreaterThan(dayIndex);
  return slots.filter((s) => s.dia === days[dayIndex])[nth];
}

const isRequestsPost = (r) => new URL(r.url()).pathname === "/api/requests" && r.request().method() === "POST";
const received = (sink, to) => sink.mails.filter((m) => m.to === to && /Recibimos tu solicitud/.test(m.subject));

/** A POST /api/requests the way the wizard sends it, from the patient's own session (a second route to the same rule). */
async function postRequest(page, body) {
  return page.evaluate(async (payload) => {
    const r = await fetch("/api/requests", {
      method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body: JSON.stringify(payload),
    });
    return { status: r.status, body: await r.json().catch(() => ({})) };
  }, body);
}

test("J26 — the limit counts only what holds a place, and the patient is told the way out", async ({ app, sink, role }) => {
  test.setTimeout(300_000);
  const LIMIT_ONE = (nombre) => `Ya tenés 1 turno de ${nombre} pedido sin atender, que es el máximo. Para pedir otro horario, cancelá el que tenés en Mis turnos y pedí uno nuevo.`;
  const P = patient(1, "Paula", "Limite");
  const Q = patient(2, "Quique", "Pasado");
  const R = patient(3, "Rosa", "Futuro");
  const K = patient(4, "Karina", "Kine");
  const p = await role({ session: (await seedPatient(app, P)).session });
  const q = await role({ session: (await seedPatient(app, Q)).session });
  const r = await role({ session: (await seedPatient(app, R)).session });
  const k = await role({ session: (await seedPatient(app, K)).session });
  const run = {};

  await test.step("1. the first request of a service goes through the wizard and holds the patient's one place", async () => {
    run.slots = await offeredSlots(p.page, CARDIO.id);
    run.first = slotOf(run.slots, 1, 0);
    await sendRequest(p.page, run.first);
    const { requests } = await runFixture(app, "requests", { dni: P.dni });
    expect(requests.map((x) => [x.servicio_id, x.estado])).toEqual([[CARDIO.id, "pendiente"]]);
    run.firstId = requests[0].id;
    await expect.poll(() => received(sink, P.email).length, { message: "one «received» email" }).toBe(1);
  });

  await test.step("2. a second time of the same service is refused with the limit's own sentence and a link to «Mis turnos»; nothing is created", async () => {
    const page = p.page;
    run.second = slotOf(run.slots, 2, 0);
    await offeredSlots(page, CARDIO.id);
    await (await showSlot(page, run.second)).click();
    await expect(page.locator("#wz-send")).toBeVisible();
    const answered = page.waitForResponse(isRequestsPost);
    await page.locator("#wz-send").click();
    const response = await answered;
    expect(response.status(), "the limit is a 409").toBe(409);
    const body = await response.json();
    expect([body.error, body.limite, body.abiertas, body.servicio_id]).toEqual(["limite_turnos_activos", 1, 1, CARDIO.id]);
    expect(body.accion, "the way out is a link to Mis turnos").toEqual({ texto: "Mis turnos", url: "/account#turnos" });

    const message = page.locator("#wz-msg");
    await expect(message, "the sentence says how many, of what, and what to do").toContainText(LIMIT_ONE(CARDIO.nombre), { useInnerText: true });
    const link = message.getByRole("link", { name: "Mis turnos" });
    await expect(link).toHaveAttribute("href", "/account#turnos");
    await expect(page.locator("body"), "no raw error code on screen").not.toContainText("limite_turnos_activos", { useInnerText: true });
    await expect(page.locator("#wz-send"), "the patient stays on Confirmá, with the wizard intact").toBeEnabled();
    expect((await runFixture(app, "requests", { dni: P.dni })).requests.length, "the refusal created nothing").toBe(1);
    expect(received(sink, P.email).length, "and sent nothing").toBe(1);
  });

  await test.step("3. the link leads to the request she holds; after cancelling it the same time is accepted (a cancelled request holds no place)", async () => {
    const page = p.page;
    await page.locator("#wz-msg").getByRole("link", { name: "Mis turnos" }).click();
    await page.waitForURL((url) => url.pathname === "/account");
    await expect(page.locator("[data-cancel]").first()).toBeVisible();
    await cancelFromAccount(page);
    expect((await runFixture(app, "requests", { dni: P.dni })).requests[0].estado).toBe("cancelada");
    await offeredSlots(page, CARDIO.id);
    await sendRequest(page, run.second);
    const rows = (await runFixture(app, "requests", { dni: P.dni })).requests;
    expect(rows.map((x) => x.estado), "one cancelled, one pending").toEqual(["pendiente", "cancelada"]);
    run.secondId = rows[0].id;
  });

  await test.step("4. a request the desk rejected holds no place either", async () => {
    await runFixture(app, "reject", { id: String(run.secondId), motivo: "Falta la orden" });
    const page = p.page;
    run.third = slotOf(await offeredSlots(page, CARDIO.id), 3, 0);
    await sendRequest(page, run.third);
    const rows = (await runFixture(app, "requests", { dni: P.dni })).requests;
    expect(rows.map((x) => x.estado), "pending, rejected, cancelled").toEqual(["pendiente", "rechazada", "cancelada"]);
  });

  await test.step("5. another service, and a dependent (another clinical DNI), have their own count", async () => {
    const page = p.page;
    const psycho = slotOf(await offeredSlots(page, PSYCHO.id), 1, 0);
    await sendRequest(page, psycho);
    expect((await runFixture(app, "requests", { dni: P.dni })).requests.filter((x) => x.estado === "pendiente").map((x) => x.servicio_id).sort(),
      "a pending Cardiología and a pending Psicología").toEqual([CARDIO.id, PSYCHO.id].sort());

    const child = { dni: "50266001", nombre: "Pablito", apellido: "Limite" };
    await runFixture(app, "seed-dependent", { spec: { holder: P.dni, nacimiento: "2015-05-05", ...child } });
    const forChild = await postRequest(page, {
      servicio_id: CARDIO.id, centro_slug: CARDIO.centro, paciente_dni: child.dni,
      slot_dia: slotOf(run.slots, 4, 0).dia, slot_hora: slotOf(run.slots, 4, 0).hora,
    });
    expect(forChild.status, "the dependent's Cardiología is not the adult's").toBe(200);
    const childRows = (await runFixture(app, "requests", { dni: child.dni })).requests;
    expect([childRows.length, childRows[0].solicitante_dni, childRows[0].estado]).toEqual([1, P.dni, "pendiente"]);
    const again = await postRequest(page, {
      servicio_id: CARDIO.id, centro_slug: CARDIO.centro, paciente_dni: child.dni,
      slot_dia: slotOf(run.slots, 4, 1).dia, slot_hora: slotOf(run.slots, 4, 1).hora,
    });
    expect([again.status, again.body.error], "…and the dependent has her own limit of one").toEqual([409, "limite_turnos_activos"]);
  });

  await test.step("6. an approved appointment whose time is past holds no place; an approved FUTURE one does", async () => {
    // Yesterday 10:00 is past at any hour of the day; +3 days is future at any hour.
    await runFixture(app, "seed-visit", { spec: {
      dni: Q.dni, nombre: Q.nombre, apellido: Q.apellido, doctor_id: CARDIO.doctorId, doctor_name: "Camila Levin",
      servicio_id: CARDIO.id, centro: CARDIO.centro, when: "+-1", interview: false } });
    const qSlot = slotOf(await offeredSlots(q.page, CARDIO.id), 1, 2);
    await sendRequest(q.page, qSlot);
    expect((await runFixture(app, "requests", { dni: Q.dni })).requests.map((x) => x.estado), "the new request next to the attended one")
      .toEqual(["pendiente", "aprobada"]);

    await runFixture(app, "seed-visit", { spec: {
      dni: R.dni, nombre: R.nombre, apellido: R.apellido, doctor_id: CARDIO.doctorId, doctor_name: "Camila Levin",
      servicio_id: CARDIO.id, centro: CARDIO.centro, when: "+3", at: "10:20", interview: false } });
    const rSlot = slotOf(await offeredSlots(r.page, CARDIO.id), 1, 4);
    await (await showSlot(r.page, rSlot)).click();
    await expect(r.page.locator("#wz-send")).toBeVisible();
    await r.page.locator("#wz-send").click();
    await expect(r.page.locator("#wz-msg"), "the approved future appointment occupies her place").toContainText(LIMIT_ONE(CARDIO.nombre), { useInnerText: true });
    expect((await runFixture(app, "requests", { dni: R.dni })).requests.length, "only the approved one exists").toBe(1);
  });

  await test.step("7. kinesiología takes ten at once and refuses the eleventh in the plural", async () => {
    const page = k.page;
    const slots = await offeredSlots(page, KINE.id);
    const days = [...new Set(slots.map((s) => s.dia))];
    const chosen = days.slice(1).flatMap((day) => slots.filter((s) => s.dia === day).slice(0, 3)).slice(0, 11);
    expect(chosen.length, "the grid has eleven times to try").toBe(11);
    for (const slot of chosen.slice(0, 10)) {
      const result = await postRequest(page, { servicio_id: KINE.id, centro_slug: KINE.centro, slot_dia: slot.dia, slot_hora: slot.hora });
      expect(result.status, `${slot.dia} ${slot.hora} accepted`).toBe(200);
    }
    const eleventh = await postRequest(page, { servicio_id: KINE.id, centro_slug: KINE.centro, slot_dia: chosen[10].dia, slot_hora: chosen[10].hora });
    expect([eleventh.status, eleventh.body.error, eleventh.body.limite, eleventh.body.abiertas]).toEqual([409, "limite_turnos_activos", 10, 10]);
    expect(eleventh.body.detalle).toBe("Ya tenés 10 turnos de Kinesiología y rehabilitación pedidos sin atender, que es el máximo. Para pedir otro, cancelá uno de los que tenés en Mis turnos y pedí uno nuevo.");
    expect((await runFixture(app, "requests", { dni: K.dni })).requests.length, "ten requests, no more").toBe(10);
  });

  for (const [who, label] of [[p, "Paula"], [q, "Quique"], [r, "Rosa"], [k, "Karina"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

test("J26 — two patients after the same last time: the first keeps it, the others are sent back to the grid with the rest loaded", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const A = patient(11, "Ana", "Primera");
  const B = patient(12, "Beto", "Segundo");
  const C = patient(13, "Cora", "Tarde");
  const a = await role({ session: (await seedPatient(app, A)).session });
  const b = await role({ session: (await seedPatient(app, B)).session });
  const c = await role({ session: (await seedPatient(app, C)).session });
  const run = {};
  const chip = (page, s) => page.locator(`.wz-seg-opt[data-dia="${s.dia}"][data-hora="${s.hora}"]`);
  // The wizard keeps its hold alive with fire-and-forget posts on each step (Tu cuenta, Confirmá), so Confirmá can
  // be on screen while one of them is still on its way; a hold released before it lands is taken again by the
  // same token. Count B's hold posts that have not answered yet, to let the hold lapse only once they have.
  const holdPosts = new Set();
  const isHoldPost = (req) => new URL(req.url()).pathname === "/api/slot-hold" && req.method() === "POST";
  b.page.on("request", (req) => { if (isHoldPost(req)) holdPosts.add(req); });
  b.page.on("requestfinished", (req) => holdPosts.delete(req));
  b.page.on("requestfailed", (req) => holdPosts.delete(req));

  await test.step("1. all three have the same time on screen", async () => {
    const [sa, sb, sc] = [await offeredSlots(a.page, GENERAL.id), await offeredSlots(b.page, GENERAL.id), await offeredSlots(c.page, GENERAL.id)];
    run.last = slotOf(sa, 1, 0);
    run.other = slotOf(sa, 1, 1); // same day as run.last, so it stays among the two days the grid lists
    run.another = slotOf(sa, 3, 0);
    for (const slots of [sa, sb, sc]) expect(slots.some((s) => s.dia === run.last.dia && s.hora === run.last.hora)).toBe(true);
  });

  await test.step("2. B picks it first, then B's hold lapses while the form is open (the three minutes of a logged-in patient)", async () => {
    await chip(b.page, run.last).click();
    await expect(b.page.locator("#wz-send")).toBeVisible();
    await expect.poll(() => holdPosts.size, { message: "B's own hold posts have all answered before the hold lapses" }).toBe(0);
    // The hold is released exactly as its expiry does: the server forgets the token. B's page does not know.
    const released = await b.page.evaluate(async () => {
      const token = localStorage.getItem("st_hold_token");
      const r = await fetch(`/api/slot-hold/${encodeURIComponent(token)}`, { method: "DELETE", credentials: "include" });
      return { token: Boolean(token), status: r.status };
    });
    expect(released).toEqual({ token: true, status: 200 });
  });

  await test.step("3. A picks the same time and sends: it is hers", async () => {
    await sendRequest(a.page, run.last);
    const rows = (await runFixture(app, "requests", { dni: A.dni })).requests;
    expect(rows.map((x) => [x.servicio_id, x.estado])).toEqual([[GENERAL.id, "pendiente"]]);
    run.aId = rows[0].id;
    await expect.poll(() => received(sink, A.email).length).toBe(1);
  });

  await test.step("4. C, who clicks late, is told the time was just taken and stays on the grid; nothing is created", async () => {
    const held = c.page.waitForResponse((res) => new URL(res.url()).pathname === "/api/slot-hold" && res.request().method() === "POST");
    await chip(c.page, run.last).click();
    expect((await held).status(), "the hold is refused").toBe(409);
    await expect(c.page.locator("#svc-form-host"), "said in plain words").toContainText("otra persona acaba de tomar ese turno. Elegí otro horario", { useInnerText: true });
    await expect(c.page.locator("#wz-send"), "there is nothing to send").toHaveCount(0);
    await expect(chip(c.page, run.other), "the other times are still there").toBeVisible();
    expect((await runFixture(app, "requests", { dni: C.dni })).requests, "C has no request").toEqual([]);
  });

  await test.step("5. B presses send on the time she no longer holds: back to the grid with the soft notice, no raw error, no request", async () => {
    const answered = b.page.waitForResponse(isRequestsPost);
    await b.page.locator("#wz-send").click();
    const response = await answered;
    expect([response.status(), (await response.json()).error], "the server refuses the lost time").toEqual([409, "turno_tomado"]);
    await expect(b.page.locator("#svc-form-host")).toContainText("otra persona acaba de tomar ese turno. Elegí otro horario", { useInnerText: true });
    await expect(chip(b.page, run.other), "the grid is back with other times to choose").toBeVisible();
    await expect(b.page.locator("body"), "no raw code").not.toContainText("turno_tomado", { useInnerText: true });
    await expect(b.page.locator("#wz-send")).toHaveCount(0);
    expect((await runFixture(app, "requests", { dni: B.dni })).requests, "the loser has no request row").toEqual([]);
    expect(received(sink, B.email).length, "and no email").toBe(0);
  });

  await test.step("6. B takes another time with one click and send; the lost time stays on A's request alone", async () => {
    await sendRequest(b.page, run.other);
    const rowsB = (await runFixture(app, "requests", { dni: B.dni })).requests;
    expect(rowsB.map((x) => x.estado), "exactly one request for B").toEqual(["pendiente"]);
    await expect.poll(() => received(sink, B.email).length).toBe(1);
    const holds = [];
    for (const person of [a, b]) {
      const listed = await person.page.evaluate(async () => (await (await fetch("/api/requests", { credentials: "same-origin" })).json()).solicitudes);
      holds.push(...listed.map((x) => x.hold ? `${x.hold.dia}T${x.hold.hora}` : x.turno_dt));
    }
    expect(holds.filter((x) => x === `${run.last.dia}T${run.last.hora}`), "the lost time is on one request only").toHaveLength(1);
    expect(holds, "A holds the contested time, B the other").toEqual([`${run.last.dia}T${run.last.hora}`, `${run.other.dia}T${run.other.hora}`]);
  });

  for (const [who, label] of [[a, "A"], [b, "B"], [c, "C"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

test("J26 — two patients press the same time in the same instant: one hold is granted, one request exists", async ({ app, sink, role }) => {
  test.setTimeout(180_000);
  await test.step("1. both ask for the hold and send, started together: exactly one of them gets the time", async () => {
    const F = patient(14, "Flor", "Veloz");
    const G = patient(15, "Gael", "Rapido");
    const f = await role({ session: (await seedPatient(app, F)).session });
    const g = await role({ session: (await seedPatient(app, G)).session });
    const slot = slotOf(await offeredSlots(f.page, GENERAL.id), 4, 0);
    expect(hasSlot(await offeredSlots(g.page, GENERAL.id), slot), "both see the time").toBe(true);
    // The whole path of the wizard in one browser call each, started together: hold the time, then send the request.
    const press = (page) => page.evaluate(async ({ service, s }) => {
      const json = { "content-type": "application/json" };
      const held = await fetch("/api/slot-hold", {
        method: "POST", credentials: "same-origin", headers: json, body: JSON.stringify({ servicio_id: service.id, dia: s.dia, hora: s.hora }),
      });
      const hold = await held.json().catch(() => ({}));
      if (!held.ok) return { hold: held.status, send: null };
      const sent = await fetch("/api/requests", {
        method: "POST", credentials: "same-origin", headers: json,
        body: JSON.stringify({ servicio_id: service.id, centro_slug: service.centro, slot_dia: s.dia, slot_hora: s.hora, hold_token: hold.token, idem_key: hold.token }),
      });
      return { hold: held.status, send: { status: sent.status, body: await sent.json().catch(() => ({})) } };
    }, { service: GENERAL, s: slot });
    const results = await Promise.all([press(f.page), press(g.page)]);
    expect(results.map((x) => x.hold).sort(), "one hold is granted, the other refused").toEqual([200, 409]);
    const winner = results.find((x) => x.hold === 200);
    expect([winner.send.status, winner.send.body.ok], "the one holding the time sends it").toEqual([200, true]);
    const rows = [...(await runFixture(app, "requests", { dni: F.dni })).requests, ...(await runFixture(app, "requests", { dni: G.dni })).requests];
    expect(rows.map((x) => x.estado), "exactly one request exists for the contested time").toEqual(["pendiente"]);
    await expect.poll(() => received(sink, F.email).length + received(sink, G.email).length, { message: "and one email" }).toBe(1);
    expect([...f.errors, ...g.errors]).toEqual([]);
  });
});

test("J26 — a double click and a replay of «Pedir el turno» make one request and one email; «Atrás» never offers the button again", async ({ app, sink, role }) => {
  test.setTimeout(180_000);
  const D = patient(21, "Dora", "Apurada");
  const d = await role({ session: (await seedPatient(app, D)).session });
  const page = d.page;
  const posts = [];
  page.on("response", async (res) => {
    if (isRequestsPost(res)) posts.push(res.json().then((body) => ({ status: res.status(), body }), () => ({ status: res.status(), body: {} })));
  });
  const run = {};

  await test.step("1. two quick clicks on send: every answer is a success for the same request, never the limit", async () => {
    run.slot = slotOf(await offeredSlots(page, DENTAL.id), 1, 0);
    await (await showSlot(page, run.slot)).click();
    await expect(page.locator("#wz-send")).toBeVisible();
    run.sent = page.waitForRequest((req) => req.url().endsWith("/api/requests") && req.method() === "POST");
    await page.locator("#wz-send").dblclick();
    run.payload = (await run.sent).postData();
    await expect(page.locator("#svc-title")).toHaveText("Solicitud enviada");
    // The page disables the button on the first tap, so the browser sends one post or two; every answer is a success.
    await expect.poll(() => posts.length, { message: "the browser sent at least one post" }).toBeGreaterThanOrEqual(1);
    const answers = await Promise.all(posts);
    for (const post of answers) expect([post.status, post.body.ok], "a success each time, never the limit refusal").toEqual([200, true]);
    expect(new Set(answers.map((x) => x.body.id)).size, "all of them name the same request").toBe(1);
    expect(JSON.parse(run.payload).idem_key, "the tap carries its idempotency key").toBeTruthy();
    const rows = (await runFixture(app, "requests", { dni: D.dni })).requests;
    expect(rows.map((x) => [x.servicio_id, x.estado]), "exactly one request row").toEqual([[DENTAL.id, "pendiente"]]);
    run.id = rows[0].id;
  });

  await test.step("2. a later replay of the same request (a retry after a slow network) answers with the same request", async () => {
    const replay = await page.evaluate(async (body) => {
      const r = await fetch("/api/requests", { method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" }, body });
      return { status: r.status, body: await r.json().catch(() => ({})) };
    }, run.payload);
    expect([replay.status, replay.body.ok, replay.body.id]).toEqual([200, true, run.id]);
    expect((await runFixture(app, "requests", { dni: D.dni })).requests.length, "still one row").toBe(1);
  });

  await test.step("3. «Atrás» from the receipt does not bring back an enabled send button", async () => {
    await page.goBack();
    await expect(page.locator("#svc-title"), "she is back at the choice of a time").toHaveText("Elegí un turno");
    await expect(page.locator("#wz-send"), "no way to send the same form twice").toHaveCount(0);
    expect((await runFixture(app, "requests", { dni: D.dni })).requests.length, "still one row").toBe(1);
  });

  await test.step("4. choosing another time after «Atrás» and sending gets the limit's sentence, not a second request", async () => {
    const other = slotOf(await gridSlots(page, DENTAL.id), 2, 0);
    await (await showSlot(page, other)).click();
    await expect(page.locator("#wz-send")).toBeVisible();
    await page.locator("#wz-send").click();
    await expect(page.locator("#wz-msg")).toContainText(
      `Ya tenés 1 turno de ${DENTAL.nombre} pedido sin atender, que es el máximo. Para pedir otro horario, cancelá el que tenés en Mis turnos y pedí uno nuevo.`,
      { useInnerText: true });
    expect((await runFixture(app, "requests", { dni: D.dni })).requests.length, "still one row").toBe(1);
  });

  await test.step("5. one «Recibimos tu solicitud» email in the end", async () => {
    await expect.poll(() => received(sink, D.email).length).toBe(1);
    expect(received(sink, D.email)[0].text, "it names the service").toContain(DENTAL.nombre);
    expect(sink.mails.filter((m) => m.to === D.email).length, "and it is the only email").toBe(1);
  });

  await test.step("6. two sends of one key at the very same moment (a tablet that lags and a second tap) make one request and one email", async () => {
    const E = patient(22, "Eva", "Simultanea");
    const e = await role({ session: (await seedPatient(app, E)).session });
    const slot = slotOf(await offeredSlots(e.page, GENERAL.id), 2, 1);
    const both = await e.page.evaluate(async ({ service, slot: s }) => {
      const json = { "content-type": "application/json" };
      const hold = await (await fetch("/api/slot-hold", {
        method: "POST", credentials: "same-origin", headers: json, body: JSON.stringify({ servicio_id: service.id, dia: s.dia, hora: s.hora }),
      })).json();
      const body = JSON.stringify({ servicio_id: service.id, centro_slug: service.centro, slot_dia: s.dia, slot_hora: s.hora, hold_token: hold.token, idem_key: hold.token });
      const sends = await Promise.all([1, 2, 3].map(() => fetch("/api/requests", { method: "POST", credentials: "same-origin", headers: json, body })
        .then(async (r) => ({ status: r.status, body: await r.json().catch(() => ({})) }))));
      return { held: Boolean(hold.token), sends };
    }, { service: GENERAL, slot });
    expect(both.held).toBe(true);
    expect(both.sends.map((x) => [x.status, x.body.ok]), "three simultaneous sends, three successes").toEqual([[200, true], [200, true], [200, true]]);
    expect(new Set(both.sends.map((x) => x.body.id)).size, "all naming one request").toBe(1);
    const rows = (await runFixture(app, "requests", { dni: E.dni })).requests;
    expect(rows.map((x) => [x.id, x.estado]), "one row").toEqual([[both.sends[0].body.id, "pendiente"]]);
    await expect.poll(() => received(sink, E.email).length, { message: "one «received» email" }).toBe(1);
    // No second email arrives afterwards: the mail of the duplicate calls would be sent right behind the first.
    await expect.poll(() => sink.mails.filter((m) => m.to === E.email).length).toBe(1);
    expect(e.errors).toEqual([]);
  });
  expect(d.errors).toEqual([]);
});
