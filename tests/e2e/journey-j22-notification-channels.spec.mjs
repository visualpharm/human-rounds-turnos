// tests/e2e/journey-j22-notification-channels.spec.mjs -- J22 ("A patient chooses how the
// hospital may reach her -- WhatsApp, email, or neither -- and from then on every message
// of her appointment follows that choice") cross-role journey acceptance.
//
// Three patients book the same service on the same day and the desk approves all three.
// Before booking, each one sets «Contacto y avisos» in her account the way the page offers
// it: A turns WhatsApp off, B turns email off, C turns both off (the opt-out).
//
// Terminal outcome: a channel the patient switched off is never used, for any message of the
// appointment (request received, confirmed, the reminder of the day before); the channel she
// left on carries all of them; with both off nothing is sent at all and the appointment still
// exists for the desk and for her.
//
// Steps:
//   1. Each patient opens «Contacto y avisos», sees both channels on, switches hers off, and
//      the choice is still there after a reload
//   2. Each one books the same service on the same day (the wizard) and the desk approves the
//      three requests
//   3. Confirmation messages: only on the channels that were left on
//   4. The sweep of the day before sends the reminder only on the channels left on, once
//   5. Patient C still finds her appointment in her account; switching a channel back on
//      brings it back for the next message (a rejection sent after she turned email on again)
//
// External services: the mail provider and the WhatsApp gateway are a local receiver
// (lib/journey.mjs, `journey: { sink: true }`), selected by the same environment keys
// production uses. No real key, no network.
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve,
  offeredSlots, sendRequest, artParts, DESK,
} from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const DOCTOR = "Camila Levin";
const PATIENTS = {
  a: { dni: "30777001", nombre: "Alba", apellido: "SinWhats", email: "alba.sinwhats@example.invalid", contacto: "+5492254700001", off: ["wa"] },
  b: { dni: "30777002", nombre: "Boris", apellido: "SinCorreo", email: "boris.sincorreo@example.invalid", contacto: "+5492254700002", off: ["email"] },
  c: { dni: "30777003", nombre: "Clara", apellido: "SinAvisos", email: "clara.sinavisos@example.invalid", contacto: "+5492254700003", off: ["wa", "email"] },
};
const wa = (p) => p.contacto.replace(/\D/g, "");

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j22" },
  journey: { sink: true, staff: [DESK] },
});

test("J22 — the channels a patient switches off are not used for any message of her appointment", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const mailTo = (p, pattern) => sink.mails.filter((m) => m.to === p.email && pattern.test(m.subject));
  const waTo = (p, pattern) => sink.whatsapp.filter((m) => String(m.chatId) === `${wa(p)}@c.us` && pattern.test(m.text));
  const people = {};
  for (const [key, p] of Object.entries(PATIENTS)) {
    const account = await seedPatient(app, p);
    people[key] = { ...p, role: await role({ session: account.session }) };
  }
  const desk = await role();
  const run = { when: {} };

  await test.step("1. each patient switches her channels in «Contacto y avisos»; the choice survives a reload", async () => {
    for (const p of Object.values(people)) {
      const page = p.role.page;
      await page.goto("/account#contacto", { waitUntil: "load" });
      const whatsapp = page.locator("#sc-av-wa");
      const email = page.locator("#sc-av-email");
      await expect(whatsapp, "WhatsApp is on until she switches it off").toHaveAttribute("aria-checked", "true");
      await expect(email, "email is on until she switches it off").toHaveAttribute("aria-checked", "true");
      for (const channel of p.off) {
        const saved = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/account/update" && r.request().method() === "POST");
        await page.locator(channel === "wa" ? "#sc-av-wa" : "#sc-av-email").click();
        expect((await saved).status(), "the choice is saved by the page itself").toBe(200);
      }
      await page.reload({ waitUntil: "load" });
      await expect(page.locator("#sc-av-wa")).toHaveAttribute("aria-checked", p.off.includes("wa") ? "false" : "true");
      await expect(page.locator("#sc-av-email")).toHaveAttribute("aria-checked", p.off.includes("email") ? "false" : "true");
      // The other fields were not touched by the switches.
      await expect(page.locator("#sc-email")).toHaveValue(p.email);
    }
  });

  await test.step("2. all three book the same day and the desk approves them", async () => {
    const slots = await offeredSlots(people.a.role.page, SERVICE_ID);
    // Never today (its times may be minutes away or past by the time the desk approves them): the first LATER day
    // that has five times.
    const day = [...new Set(slots.map((s) => s.dia))].slice(1).find((d) => slots.filter((s) => s.dia === d).length >= 5);
    expect(day, "some day offers five times").toBeTruthy();
    const sameDay = slots.filter((s) => s.dia === day);
    const picks = { a: sameDay[0], b: sameDay[2], c: sameDay[4] };
    for (const [key, p] of Object.entries(people)) {
      const page = p.role.page;
      await page.goto(`/service/${SERVICE_ID}/book?slots=1`, { waitUntil: "load" });
      await expect(page.locator("#svc-title")).toBeVisible();
      await sendRequest(page, picks[key]);
      p.slot = picks[key];
    }
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row")).toHaveCount(3);
    for (const p of Object.values(people)) {
      const detail = await openRequest(page, { hasText: `${p.nombre} ${p.apellido}` });
      await doctorCombo(detail).pick(DOCTOR);
      await expect(detail.locator("input.turno"), "the approval proposes the time she picked").toHaveValue(`${p.slot.dia}T${p.slot.hora}`);
      await approve(page, detail);
      const { requests } = await runFixture(app, "requests", { dni: p.dni });
      expect(requests[0].estado).toBe("aprobada");
      p.requestId = requests[0].id;
      p.turnoDt = requests[0].turno_dt;
      run.when[p.dni] = artParts(p.turnoDt);
    }
  });

  await test.step("3. the confirmation and the «received» notice go only where she left a channel on", async () => {
    const { a, b, c } = people;
    // Positive evidence first: what A and B keep. Then, with every approval already answered, the absences.
    await expect.poll(() => mailTo(a, /confirmado/).length, { message: "A (WhatsApp off) is told by email" }).toBe(1);
    await expect.poll(() => waTo(b, /quedó confirmado/).length, { message: "B (email off) is told by WhatsApp" }).toBe(1);
    expect(mailTo(a, /Recibimos tu solicitud/).length, "A's «we received your request» email").toBe(1);
    expect(mailTo(a, /confirmado/)[0].text, "A's email carries the time").toContain(run.when[a.dni].time);
    expect(waTo(b, /quedó confirmado/)[0].text, "B's WhatsApp carries the time").toContain(run.when[b.dni].time);

    expect(waTo(a, /./).length, "A switched WhatsApp off: not a single WhatsApp message").toBe(0);
    expect(sink.mails.filter((m) => m.to === b.email).length, "B switched email off: not a single email").toBe(0);
    expect(sink.mails.filter((m) => m.to === c.email).length, "C switched both off: no email").toBe(0);
    expect(waTo(c, /./).length, "C switched both off: no WhatsApp").toBe(0);
  });

  await test.step("4. the reminder of the day before follows the same choice, once", async () => {
    const { a, b, c } = people;
    const when = run.when[a.dni];
    expect(run.when[b.dni].date, "the three appointments share the day").toBe(when.date);
    const dayBefore = artParts(new Date(new Date(a.turnoDt).getTime() - 86_400_000).toISOString());
    run.sweepAt = new Date(`${dayBefore.date}T12:00:00-03:00`).toISOString();
    const first = await runFixture(app, "tick-reminders", { now: run.sweepAt }, sink.env);
    // C switched every channel off: nothing to send is not a reminder sent (the product owner's hub, 2026-11-10).
    expect(first.processed, "the sweep reminds A and B; C has nothing to send").toBe(2);

    await expect.poll(() => mailTo(a, /^Mañana/).length, { message: "A's reminder email" }).toBe(1);
    await expect.poll(() => waTo(b, /Te recordamos que mañana/).length, { message: "B's reminder WhatsApp" }).toBe(1);
    expect(mailTo(a, /^Mañana/)[0].text, "A's reminder carries the one-click answer").toMatch(/\/appointment\/confirm\//);
    expect(waTo(b, /Te recordamos que mañana/)[0].text).toMatch(/Respondé SÍ/);
    expect(waTo(a, /./).length, "A: still no WhatsApp").toBe(0);
    expect(sink.mails.filter((m) => m.to === b.email).length, "B: still no email").toBe(0);
    expect(sink.mails.filter((m) => m.to === c.email).length + waTo(c, /./).length, "C: nothing at all").toBe(0);

    const second = await runFixture(app, "tick-reminders", { now: run.sweepAt }, sink.env);
    expect(second.processed, "a second sweep finds nothing left to remind").toBe(0);
    expect(mailTo(a, /^Mañana/).length + waTo(b, /Te recordamos que mañana/).length, "one reminder each").toBe(2);
  });

  await test.step("5. with every channel off she still has her appointment; switching a channel on again brings the messages back", async () => {
    const { c } = people;
    const page = c.role.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    await expect(page.locator("#ms-host").getByText(DOCTOR, { exact: true }).first()).toBeVisible();
    await expect(page.locator("#ms-host")).toContainText(run.when[c.dni].time, { useInnerText: true });

    await page.goto("/account#contacto", { waitUntil: "load" });
    const saved = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/account/update" && r.request().method() === "POST");
    await page.locator("#sc-av-email").click();
    expect((await saved).status()).toBe(200);
    await expect(page.locator("#sc-av-email")).toHaveAttribute("aria-checked", "true");

    // The desk cancels two of the appointments (a phone call): B still has email off, C has just turned it on.
    // B first, C second -- the notice is sent while the cancel is answered, so C's email arriving proves B's was decided.
    for (const p of [people.b, c]) {
      const cancelled = await desk.context.request.post(`/api/admin/requests/${p.requestId}/cancel`, { data: {} });
      expect(cancelled.status(), `the desk cancels ${p.nombre}'s appointment`).toBe(200);
    }
    await expect.poll(() => mailTo(c, /Se canceló tu turno/).length, { message: "C's cancellation notice, now that email is on again" }).toBe(1);
    expect(mailTo(c, /Se canceló tu turno/)[0].text, "…names the time").toContain(run.when[c.dni].time);
    expect(sink.mails.filter((m) => m.to === people.b.email).length, "B switched email off: the cancellation is not mailed").toBe(0);
    expect(waTo(c, /./).length, "C's WhatsApp stays off").toBe(0);
  });

  for (const [key, p] of Object.entries(people)) {
    expect(p.role.errors, `no uncaught page errors for patient ${key}`).toEqual([]);
  }
  expect(desk.errors, "no uncaught page errors for the desk").toEqual([]);
});
