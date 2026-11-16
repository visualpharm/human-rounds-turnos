// tests/e2e/journey-j30-calendar-feeds-and-printouts.spec.mjs -- J30 ("What leaves the
// screen: calendar files, calendar feeds and printouts") cross-role journey acceptance.
//
// Three things a patient, a doctor or the front desk carries OUT of the panel/site and
// trusts without looking again; a wrong byte in any of them is wrong in the real world:
//
//   A. The patient's calendar file. Her phone puts the turno where the .ics says: the
//      «Al calendario» link of the confirmation e-mail (token, no session), the link on her
//      account card (session) and her subscription feed must all say the approved time IN
//      THE INSTALLATION'S TIME ZONE (America/Argentina/Buenos_Aires, core/tz.py invariant
//      «Datetimes are ALWAYS converted to the installation timezone») -- a 22:30 turno is the
//      next UTC day, the case a UTC mistake breaks -- name the service and the center's
//      address, carry no identifier beyond the patient's name, and never serve another
//      patient's turno. A cancelled turno leaves the feed and its links die (calendar_feed.py:
//      «a cancelled/rescheduled request just 404s, it never serves a stale event»); a forged
//      token is a 404, not a 500 and not an empty calendar.
//   B. The doctor's feed (/feed/doctor/<token>.ics, from «Mis turnos»): his own approved
//      appointments only, titled «Turno · <service>» with no name or DNI of the patient (the
//      feed leaves for an outside calendar; a link opens the turno in the panel). The token
//      is the whole authorization, so a
//      doctor whose account the admin deactivated must not keep reading patients through it
//      (the patient feed already dies with the archived account, 2026-10-23).
//   C. The paper: the door poster of «Entrevista previa» (its QR is the doctor's OWN entry URL;
//      the printed address, the HTML poster and the PDF the panel links all agree; the old poster
//      dies when the token is regenerated) and the print view of a receta / orden (the app chrome
//      is hidden when printed, every copy names patient, doctor, matrícula and the lines).
//
// External services: AI is the app's own fake; mail is a local receiver (lib/journey.mjs).
// Nothing real is sent anywhere.
import { createHash } from "node:crypto";
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve, issueDocuments,
  offeredSlots, sendRequest, cancelFromAccount, gotoUntil, artParts,
  DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS,
} from "./lib/journey.mjs";

const ART = "America/Argentina/Buenos_Aires";
const SERVICE_ID = "cardiologia-sampleton-hospital";
const SERVICE_NAME = "Cardiología";
const CENTER_NAME = "Sampleton Hospital";
// Center address (pack example-town) + the installation's city and province, the way the product writes a LOCATION.
const LOCATION = "Main Rd. 123, Sampleton, Greenfield, Argentina";
const LEVIN = { id: "levin-camila", name: "Camila Levin", matricula: "42595", usuario: "e2e-dr-levin", password: LOGIN_PHRASE };
// The doctor of the feed test (his account gets deactivated there, so no other test uses it) and "another doctor".
const WALSH = { id: "walsh-diego", name: "Diego Walsh", usuario: "e2e-dr-walsh", password: LOGIN_PHRASE };
const ACOSTA = { id: "acosta-florencia", name: "Florencia Acosta" };
const PATIENT = {
  dni: "30777001", nombre: "Marta", apellido: "Viajera", nacimiento: "1984-03-02", sexo: "F",
  contacto: "+5492254777001", email: "marta.viajera.j30@example.invalid",
};
const NIGHT = { dni: "30777002", nombre: "Noelia", apellido: "Nocturna", contacto: "+5492254777002", email: "noelia.nocturna.j30@example.invalid" };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_j30" },
  journey: {
    sink: true,
    staff: [
      DESK,
      { usuario: LEVIN.usuario, password: LEVIN.password, rol: "medico", medico_id: LEVIN.id },
      { usuario: WALSH.usuario, password: WALSH.password, rol: "medico", medico_id: WALSH.id },
    ],
    intakeDoctors: [LEVIN.id],
  },
});

// ---- a tiny iCalendar reader (RFC 5545 subset: unfolding, parameters, text unescaping) -----------------------

function readIcs(text) {
  const raw = text.replace(/\r\n[ \t]/g, "").split("\r\n");
  const lines = raw.filter((l) => l !== "");
  const unescape = (v) => v.replace(/\\n/g, "\n").replace(/\\([,;\\])/g, "$1");
  const events = [];
  const calendar = {};
  let event = null;
  let zone = null;
  for (const line of lines) {
    if (line === "BEGIN:VEVENT") { event = {}; continue; }
    if (line === "END:VEVENT") { events.push(event); event = null; continue; }
    if (line === "BEGIN:VTIMEZONE") { zone = {}; continue; }
    if (line === "END:VTIMEZONE") { calendar.zone = zone; zone = null; continue; }
    const at = line.indexOf(":");
    const [name, ...params] = line.slice(0, at).split(";");
    const value = line.slice(at + 1);
    const target = event || zone || calendar;
    if (target[name] === undefined) target[name] = { value: unescape(value), params: Object.fromEntries(params.map((p) => p.split("="))) };
  }
  return { lines, calendar, events, rawEndsWithCrlf: text.endsWith("\r\n") };
}

/** "20261029T223000" for an `artParts` result. */
const icsLocal = (parts) => parts.local.replace(/[-:]/g, "") + "00";
/** The same wall-clock minute, 40 minutes later (every appointment lasts 40 minutes in the file). */
const plus40 = (iso) => artParts(new Date(Date.parse(iso) + 40 * 60_000).toISOString());

/** GETs `path` with the context's cookies; returns { status, type, text }. */
async function get(context, path) {
  const response = await context.request.get(path, { maxRedirects: 0 });
  return { status: response.status(), type: response.headers()["content-type"] || "", text: await response.text() };
}

/** A 200 calendar: the right media type and one well-formed VCALENDAR. */
function expectCalendar(res, what) {
  expect(res.status, `${what}: served`).toBe(200);
  expect(res.type, `${what}: a calendar file`).toMatch(/^text\/calendar/);
  const ics = readIcs(res.text);
  expect(ics.lines[0], `${what}: opens a VCALENDAR`).toBe("BEGIN:VCALENDAR");
  expect(ics.lines.at(-1), `${what}: closes it`).toBe("END:VCALENDAR");
  expect(ics.rawEndsWithCrlf, `${what}: CRLF line endings`).toBe(true);
  expect(ics.calendar.VERSION.value).toBe("2.0");
  expect(ics.calendar["X-WR-TIMEZONE"].value, `${what}: the calendar names the installation's zone`).toBe(ART);
  expect(ics.calendar.zone.TZID.value).toBe(ART);
  expect(ics.calendar.zone.TZOFFSETTO.value, `${what}: Buenos Aires is UTC-3 all year`).toBe("-0300");
  return ics;
}

/** One VEVENT stands for the approved time `iso`, in installation time. */
function expectEventAt(event, iso, what) {
  const start = artParts(iso);
  expect(event.DTSTART.params.TZID, `${what}: the start carries the installation zone`).toBe(ART);
  expect(event.DTSTART.value, `${what}: starts at ${start.local} installation time`).toBe(icsLocal(start));
  expect(event.DTEND.params.TZID).toBe(ART);
  expect(event.DTEND.value, `${what}: lasts 40 minutes`).toBe(icsLocal(plus40(iso)));
  expect(event.UID.value).toMatch(/^turno-\d+@sanaturnos$/);
}

/** A not-found answer: a 404 with the app's JSON error -- never a calendar, never a 500. */
function expectGone(res, what) {
  expect(res.status, `${what}: not found`).toBe(404);
  expect(res.type, `${what}: an error, not a calendar`).toMatch(/^application\/json/);
  expect(JSON.parse(res.text), `${what}: says so`).toEqual({ error: "no_encontrado" });
}

// -------------------------------------------------------------------------------------------------------------

test("J30 — the patient's calendar file says the approved time in installation time, names the place, and dies with the turno", async ({ app, sink, role }) => {
  test.setTimeout(240_000);
  const account = await seedPatient(app, PATIENT);
  const night = await runFixture(app, "seed-visit", { spec: {
    dni: NIGHT.dni, nombre: NIGHT.nombre, apellido: NIGHT.apellido, doctor_id: LEVIN.id, doctor_name: LEVIN.name,
    servicio_id: SERVICE_ID, centro: "sampleton-hospital", when: "+3", at: "22:30", account: true, interview: false,
    contacto: NIGHT.contacto, email: NIGHT.email } });
  const patient = await role({ session: account.session });
  const nightPatient = await role({ session: night.session });
  const desk = await role();
  const reader = await role(); // a phone calendar app: no session at all
  const run = {};

  await test.step("1. the patient books Cardiología and the desk approves it to Camila Levin", async () => {
    // The first time of the grid's SECOND day: today's first time may be minutes away or already past by the time
    // the desk approves it, and a past turno is not in a subscription feed (it lists what is still to come).
    const slots = await offeredSlots(patient.page, SERVICE_ID);
    const slot = slots.find((s) => s.dia === [...new Set(slots.map((x) => x.dia))][1]);
    await sendRequest(patient.page, slot);
    const { requests } = await runFixture(app, "requests", { dni: PATIENT.dni });
    run.requestId = requests[0].id;

    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    const detail = await openRequest(page, { hasText: "Marta Viajera" });
    await doctorCombo(detail).pick(LEVIN.name);
    await approve(page, detail);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("aprobada");
    run.turno = request.turno_dt;
    run.when = artParts(run.turno);
  });

  await test.step("2. the «Al calendario» link of the confirmation e-mail opens the turno with no session, at the approved time", async () => {
    await expect.poll(() => sink.mails.some((m) => m.to === PATIENT.email && /confirmado/.test(m.subject)),
      { message: "the confirmation e-mail" }).toBe(true);
    const mail = sink.mails.find((m) => m.to === PATIENT.email && /confirmado/.test(m.subject));
    const link = mail.text.match(/Al calendario: (\S+)/);
    expect(link, "the e-mail carries the «Al calendario» link").toBeTruthy();
    run.mailPath = new URL(link[1]).pathname;
    expect(run.mailPath).toMatch(/^\/appointment\/[\w-]+\.ics$/);

    const res = await get(reader.context, run.mailPath);
    const ics = expectCalendar(res, "the e-mail's file");
    expect(ics.events, "one turno, one event").toHaveLength(1);
    const event = ics.events[0];
    expectEventAt(event, run.turno, "the e-mail's file");
    expect(event.UID.value, "the event is this request").toBe(`turno-${run.requestId}@sanaturnos`);
    expect(event.SUMMARY.value, "the title names the service and the center").toBe(`${SERVICE_NAME} en ${CENTER_NAME}`);
    expect(event.LOCATION.value, "the location is the center's address").toBe(LOCATION);
    expect(event.DESCRIPTION.value, "the patient is recognised by name").toMatch(/^Turno de Marta Viajera\. Gestionado por .+\.$/);
    for (const secret of [PATIENT.dni, PATIENT.email, PATIENT.contacto.slice(-7)]) {
      expect(res.text, "the file carries no DNI, e-mail or phone").not.toContain(secret);
    }
  });

  await test.step("3. the account card's «Al calendario» link serves the same event to her, and to nobody else", async () => {
    const page = patient.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    const link = page.locator("#ms-host a.pt-calendar-link");
    await expect(link).toHaveCount(1);
    const href = await link.getAttribute("href");
    expect(href).toBe(`/api/appointment/${run.requestId}.ics`);

    const mine = expectCalendar(await get(patient.context, href), "the account's file");
    expectEventAt(mine.events[0], run.turno, "the account's file");
    const viaMail = readIcs((await get(reader.context, run.mailPath)).text);
    expect(mine.events[0].UID.value, "both routes name the same event").toBe(viaMail.events[0].UID.value);
    expect(mine.events[0].SUMMARY.value).toBe(viaMail.events[0].SUMMARY.value);

    expect((await get(reader.context, href)).status, "no session: sign in first").toBe(401);
    expect((await get(nightPatient.context, href)).status, "another patient's session does not open her turno").toBe(404);
  });

  await test.step("4. a 22:30 turno is still that evening in the file, though it is already tomorrow in UTC", async () => {
    const { requests } = await runFixture(app, "requests", { dni: NIGHT.dni });
    const stored = requests[0].turno_dt;
    const parts = artParts(stored);
    expect(parts.time, "the seeded turno is at night").toBe("22:30");
    expect(new Date(stored).toISOString().slice(0, 10), "…and its UTC date is the next day").not.toBe(parts.date);

    await nightPatient.page.goto("/account#turnos", { waitUntil: "load" });
    const href = await nightPatient.page.locator("#ms-host a.pt-calendar-link").getAttribute("href");
    const ics = expectCalendar(await get(nightPatient.context, href), "the night turno's file");
    expectEventAt(ics.events[0], stored, "the night turno's file");
    expect(ics.events[0].DTSTART.value, "the evening of the installation's day").toBe(`${parts.date.replace(/-/g, "")}T223000`);
  });

  await test.step("5. her subscription feed lists her turno and not the other patient's", async () => {
    const page = patient.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    const feed = page.locator("#ms-host button.ms-feed-link");
    await expect(feed).toHaveCount(1);
    await feed.click();
    const address = page.locator("#ms-cal-url");
    await expect(address).toBeVisible();
    await expect(page.locator(".ms-cal-pop")).toContainText("no se vuelve a mostrar");
    run.feedPath = new URL(await address.inputValue()).pathname;
    expect(run.feedPath).toMatch(/^\/feed\/appointments\/[\w-]+\.ics$/);

    const res = await get(reader.context, run.feedPath);
    const ics = expectCalendar(res, "the feed");
    expect(ics.events.map((e) => e.UID.value), "exactly her own turno").toEqual([`turno-${run.requestId}@sanaturnos`]);
    expectEventAt(ics.events[0], run.turno, "the feed");
    expect(res.text, "the other patient's name is not in her feed").not.toContain(NIGHT.apellido);
    await page.locator(".ms-cal-x").click();
    await expect(page.locator(".ms-cal-overlay")).toHaveCount(0);
  });

  await test.step("5b. the address is shown once: later she sees its last 4 characters, and a new one replaces it", async () => {
    // The database keeps only its hash (alembic 0039): neither the page nor the account API has it again.
    const page = patient.page;
    const token = run.feedPath.match(/\/feed\/appointments\/([\w-]+)\.ics/)[1];
    const again = await get(patient.context, "/api/calendar/feed");
    expect(JSON.parse(again.text), "the second GET names only the end").toEqual({ url: null, hint: token.slice(-4) });
    const account = await get(patient.context, "/api/account/me");
    expect(account.text, "the account API carries no feed token").not.toContain(token);

    // She comes back to her account later: a fresh load, not the page still holding the address in memory.
    await page.reload({ waitUntil: "load" });
    const feed = page.locator("#ms-host button.ms-feed-link");
    await expect(feed).toHaveCount(1);
    await feed.click();
    await expect(page.locator(".ms-cal-pop")).toContainText(`la que termina en ${token.slice(-4)}`);
    await expect(page.locator("#ms-cal-url")).toHaveCount(0);
    expect(await page.content(), "the old address is not on the page").not.toContain(token);
    expectCalendar(await get(reader.context, run.feedPath), "the subscribed address still answers");

    await page.click("#ms-cal-regen");
    const address = page.locator("#ms-cal-url");
    await expect(address).toBeVisible();
    const fresh = new URL(await address.inputValue()).pathname;
    expect(fresh).not.toBe(run.feedPath);
    expectGone(await get(reader.context, run.feedPath), "the replaced address");
    const ics = expectCalendar(await get(reader.context, fresh), "the new address");
    expect(ics.events.map((e) => e.UID.value), "the new address lists her turno").toEqual([`turno-${run.requestId}@sanaturnos`]);
    run.feedPath = fresh;
    await page.locator(".ms-cal-x").click();
    await expect(page.locator(".ms-cal-overlay")).toHaveCount(0);
  });

  await test.step("6. cancelled, the turno leaves the feed and both links stop answering", async () => {
    await cancelFromAccount(patient.page);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado, "the cancellation is stored").not.toBe("aprobada");

    const feed = expectCalendar(await get(reader.context, run.feedPath), "the feed after the cancellation");
    expect(feed.events, "the cancelled turno is not in the feed any more").toHaveLength(0);
    expectGone(await get(reader.context, run.mailPath), "the e-mail's link after the cancellation");
    expectGone(await get(patient.context, `/api/appointment/${run.requestId}.ics`), "the account's link after the cancellation");
  });

  await test.step("7. a forged or malformed token is a 404 on every calendar route", async () => {
    for (const path of [
      "/appointment/forged-token-0000000000000.ics", "/feed/appointments/forged-token-0000000000000.ics",
      "/feed/doctor/forged-token-0000000000000.ics", `/appointment/${"x".repeat(200)}.ics`, "/appointment/%20%27.ics",
    ]) {
      expectGone(await get(reader.context, path), path.slice(0, 40));
    }
    expect((await get(patient.context, "/api/appointment/abc.ics")).status, "a non-numeric id is refused").toBe(400);
  });

  for (const [who, label] of [[patient, "patient"], [nightPatient, "night patient"], [desk, "desk"], [reader, "reader"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});

// -------------------------------------------------------------------------------------------------------------

test("J30 — the doctor's feed lists only his approved turnos, without the patient's identity, and dies with his account", async ({ app, role }) => {
  test.setTimeout(180_000);
  const seed = (spec) => runFixture(app, "seed-visit", { spec: {
    servicio_id: SERVICE_ID, centro: "sampleton-hospital", interview: false, ...spec } });
  await seed({ dni: "30777011", nombre: "Ana", apellido: "Primera", doctor_id: WALSH.id, doctor_name: WALSH.name, when: "+3", at: "22:30" });
  await seed({ dni: "30777012", nombre: "Beto", apellido: "Segundo", doctor_id: WALSH.id, doctor_name: WALSH.name, when: "+4", at: "09:00" });
  // What must NOT be in his feed: another doctor's patient, and a request still waiting for the desk (parked on him).
  await seed({ dni: "30777013", nombre: "Carla", apellido: "Ajena", doctor_id: ACOSTA.id, doctor_name: ACOSTA.name, when: "+3", at: "10:00" });
  await seed({ dni: "30777014", nombre: "Dario", apellido: "Pendiente", doctor_id: WALSH.id, doctor_name: WALSH.name, pending: true });
  const patientAccount = await seedPatient(app, { dni: "30777015", nombre: "Elsa", apellido: "Suscripta", contacto: "+5492254777015" });

  const doctor = await role();
  const admin = await role();
  const reader = await role();
  const run = {};

  await test.step("1. «Mis turnos» shows the doctor his subscription address", async () => {
    const page = doctor.page;
    await signInStaff(page, WALSH.usuario, WALSH.password);
    await page.goto("/admin/mis-turnos", { waitUntil: "load" });
    // The button is in the server's first document but is heard only once the screen has mounted (a dynamic
    // import after «load»); a click before that is lost. The screen replaces its «Cargando» line when it has.
    await expect(page.locator("#sec-mis-turnos .mt-loading")).toHaveCount(0);
    await page.click("#mt-cal-open");
    const address = page.locator("#mt-cal .mt-cal-url input");
    await expect(address).toBeVisible();
    run.feedPath = new URL((await address.inputValue()).trim()).pathname;
    expect(run.feedPath).toMatch(/^\/feed\/doctor\/[\w-]+\.ics$/);
  });

  await test.step("2. the feed holds exactly his two turnos, at the right installation time, and nothing that identifies the patient", async () => {
    const res = await get(reader.context, run.feedPath);
    const ics = expectCalendar(res, "the doctor's feed");
    const ana = (await runFixture(app, "requests", { dni: "30777011" })).requests[0];
    const beto = (await runFixture(app, "requests", { dni: "30777012" })).requests[0];
    const byUid = Object.fromEntries(ics.events.map((e) => [e.UID.value, e]));
    expect(Object.keys(byUid).sort(), "his own approved turnos and nothing else").toEqual(
      [`turno-${ana.id}@sanaturnos`, `turno-${beto.id}@sanaturnos`].sort());
    const event = byUid[`turno-${ana.id}@sanaturnos`];
    expectEventAt(event, ana.turno_dt, "Ana's event");
    expect(artParts(ana.turno_dt).time, "the evening turno").toBe("22:30");
    // The feed leaves the installation for an outside calendar: the service, the place and a link back to the
    // panel, where the doctor sees who it is behind his own login (modules/turnos/calendar_feed.py, 2026-10-27).
    expect(event.SUMMARY.value, "the title names the service, not the patient").toBe(`Turno · ${SERVICE_NAME}`);
    expect(event.LOCATION.value).toBe(`${CENTER_NAME}, ${LOCATION}`);
    expect(event.URL.value, "the event links to that turno in the panel").toMatch(new RegExp(`/admin/mis-turnos#turno-${ana.id}$`));
    expect(event.DESCRIPTION.value).toContain(event.URL.value);
    for (const other of ["Ana", "Primera", "Beto", "Segundo", "30777011", "30.777.011", "Ajena", "Pendiente", "30.777.013", "30.777.014"]) {
      expect(res.text, `nothing of «${other}» in his feed`).not.toContain(other);
    }
  });

  await test.step("3. a token of the other kind opens nothing: the doctor's on the patient route and the patient's on the doctor route", async () => {
    const token = run.feedPath.match(/\/feed\/doctor\/([\w-]+)\.ics/)[1];
    expectGone(await get(reader.context, `/feed/appointments/${token}.ics`), "the doctor's token on the patient route");

    const patient = await role({ session: patientAccount.session });
    const mineFeed = await get(patient.context, "/api/calendar/feed");
    expect(mineFeed.status).toBe(200);
    const patientToken = JSON.parse(mineFeed.text).url.match(/\/feed\/appointments\/([\w-]+)\.ics/)[1];
    expectGone(await get(reader.context, `/feed/doctor/${patientToken}.ics`), "the patient's token on the doctor route");
  });

  await test.step("4. the admin deactivates the doctor's account and the feed address stops answering", async () => {
    const page = admin.page;
    await signInStaff(page, ADMIN_USER, ADMIN_PASS);
    await page.goto(`/admin/users?user=${WALSH.usuario}`, { waitUntil: "load" });
    await expect(page.locator("#usr-form")).toBeVisible();
    page.once("dialog", (dialog) => dialog.accept());
    const saved = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/admin/users/${WALSH.usuario}` && r.request().method() === "DELETE");
    await page.click("#usr-desactivar");
    expect((await saved).status(), "the deactivation is accepted").toBe(200);

    expectGone(await get(reader.context, run.feedPath), "the deactivated doctor's feed");
  });

  for (const [who, label] of [[doctor, "doctor"], [admin, "admin"], [reader, "reader"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});

// -------------------------------------------------------------------------------------------------------------

test("J30 — the door poster's QR is the doctor's own entry address, and the print view of a receta and an orden is the paper only", async ({ app, role }) => {
  test.setTimeout(240_000);
  const visit = await runFixture(app, "seed-visit", { spec: {
    dni: "30777021", nombre: "Gabriela", apellido: "Impresa", doctor_id: LEVIN.id, doctor_name: LEVIN.name,
    servicio_id: SERVICE_ID, centro: "sampleton-hospital", when: "today", at: "09:00" } });
  const admin = await role();
  const doctor = await role();
  const reader = await role();
  const run = { medication: "Atenolol 50 mg", notes: "Control en quince dias (J30)." };

  await test.step("1. the panel's «Entrevistas» list links the doctor's poster, a PDF whose QR carries his own address", async () => {
    const page = admin.page;
    await signInStaff(page, ADMIN_USER, ADMIN_PASS);
    const link = page.locator(`tr[data-medico-row="${LEVIN.id}"] a.ent-pdf`);
    // The doctor's switch (written straight into the database) reaches the page through the catalog cache (3 s).
    await gotoUntil(page, "/admin/entrevistas", () => expect(link).toBeVisible({ timeout: 2_000 }));
    run.pdfPath = await link.getAttribute("href");
    expect(run.pdfPath).toBe(`/api/admin/intake/door/${LEVIN.id}?format=pdf`);

    const pdf = await page.request.get(run.pdfPath);
    expect(pdf.status()).toBe(200);
    expect(pdf.headers()["content-type"]).toBe("application/pdf");
    const bytes = await pdf.body();
    expect(bytes.subarray(0, 5).toString(), "a real PDF").toBe("%PDF-");

    // The HTML poster: the address it prints and the QR it draws.
    const html = await page.request.get(`/api/admin/intake/door/${LEVIN.id}`);
    expect(html.status()).toBe(200);
    expect(html.headers()["content-type"]).toMatch(/^text\/html/);
    run.posterHtml = await html.text();
    const printed = run.posterHtml.match(/<p class="cartel-url">([^<]+)<\/p>/);
    expect(printed, "the poster prints the address under the QR").toBeTruthy();
    run.entryUrl = printed[1];
    const door = await runFixture(app, "door", { doctor: LEVIN.id, url: run.entryUrl });
    run.token = door.token;
    expect(new URL(run.entryUrl).pathname, "the printed address is the doctor's own door token").toBe(`/e/${run.token}`);
    expect(run.posterHtml, "the QR on the page encodes exactly that address").toContain(door.qr_svg);
    // Same two stamped dates left out as the fixture leaves out.
    const unstamped = Buffer.from(bytes.toString("latin1").replace(/\/(CreationDate|ModDate) \(D:[^)]*\)/g, ""), "latin1");
    const hash = createHash("sha256").update(unstamped).digest("hex");
    expect(hash, "the PDF is the poster of that same address").toBe(door.pdf_sha256);
  });

  await test.step("2. the poster names the doctor, the specialty and the matrícula", async () => {
    const page = admin.page;
    await page.goto(`/api/admin/intake/door/${LEVIN.id}`, { waitUntil: "load" });
    const sheet = page.locator(".hoja");
    await expect(sheet.locator(".cartel-doc")).toHaveText(LEVIN.name);
    await expect(sheet.locator(".cartel-esp")).toHaveText(`Cardiología · Mat. ${LEVIN.matricula}`);
    await expect(sheet).toContainText("Mientras esperás, contanos qué te pasa", { useInnerText: true });
    await expect(sheet.locator(".cartel-qr svg"), "a QR is drawn, not the fallback text").toHaveCount(1);
    await expect(sheet.locator(".cartel-url")).toHaveText(run.entryUrl);
    await expect(page.locator(".print-bar button"), "the toolbar is there on screen").toBeVisible();
    await page.emulateMedia({ media: "print" });
    await expect(page.locator(".print-bar"), "…and not on the paper").toBeHidden();
    await page.emulateMedia({ media: null });
  });

  await test.step("3. scanning it opens the interview entry page of THAT doctor; nobody else's poster opens his", async () => {
    const entry = await reader.context.request.get(new URL(run.entryUrl).pathname);
    expect(entry.status(), "the entry page opens with no login").toBe(200);
    expect(await entry.text()).toContain(`data-qr-start="${run.token}"`);
    const owner = await runFixture(app, "door", { doctor: LEVIN.id, token: JSON.stringify(run.token) });
    expect(owner.owner, "the token opens Camila Levin's interview, nobody else's").toBe(LEVIN.id);
    const other = await runFixture(app, "door", { doctor: WALSH.id });
    expect(other.token, "each doctor has his own token").not.toBe(run.token);

    // A doctor reads only his own poster: another's is «not found», not «forbidden».
    const page = doctor.page;
    await signInStaff(page, LEVIN.usuario, LEVIN.password);
    expect((await page.request.get(`/api/admin/intake/door/${LEVIN.id}`)).status(), "his own poster").toBe(200);
    expect((await page.request.get(`/api/admin/intake/door/${WALSH.id}`)).status(), "another doctor's poster").toBe(404);
  });

  await test.step("4. regenerating the token kills the printed poster and prints the new one", async () => {
    const regenerated = await admin.page.request.post(`/api/admin/intake/door/${LEVIN.id}/regenerate`, { data: {} });
    expect(regenerated.status()).toBe(200);
    const next = await runFixture(app, "door", { doctor: LEVIN.id });
    expect(next.token, "a new token").not.toBe(run.token);
    expect((await reader.context.request.get(`/e/${run.token}`)).status(), "the old poster opens nothing").toBe(404);
    expect((await reader.context.request.get(`/e/${next.token}`)).status(), "the new one opens the entry page").toBe(200);
    const html = await (await admin.page.request.get(`/api/admin/intake/door/${LEVIN.id}`)).text();
    expect(html, "the poster now prints the new address").toContain(`/e/${next.token}</p>`);
  });

  await test.step("5. the doctor issues a receta and an orden for the patient of the day", async () => {
    const page = doctor.page;
    await page.goto(`/admin/consult?interview=${visit.interview_id}`, { waitUntil: "load" });
    await expect(page.locator("#cst-review")).toBeVisible();
    const label = (selector) => page.locator(selector).first().locator("xpath=following-sibling::span").first().innerText();
    run.diagnosis = (await label("[data-dx]")).trim();
    run.study = (await label("[data-exam]")).trim();
    const issued = await issueDocuments(page, { medication: run.medication, notes: run.notes, study: true });
    run.recetaUrl = issued.recetaUrl;
    run.ordenUrl = issued.ordenUrl;
  });

  await test.step("6. the print view of the receta: four copies of the same facts, and only the paper when printed", async () => {
    const page = reader.page;
    await page.goto(run.recetaUrl, { waitUntil: "load" });
    await expect(page.locator(".print-bar button"), "on screen: the «Imprimir» button").toHaveText("Imprimir");
    await expect(page.locator(".copia-label")).toHaveText(["Paciente", "Farmacia", "Obra social", "Archivo"]);
    await expect(page.locator(".copia .titulo-doc span").first()).toContainText("RECETA");
    const copies = await page.locator(".copia").evaluateAll((nodes) => nodes.map((n) => ({
      doctor: n.querySelector(".medico").innerText.replace(/\s+/g, " ").trim(),
      patient: n.querySelector(".paciente-nombre").innerText.trim(),
      dni: n.querySelector(".paciente-meta").innerText.trim(),
      lines: Array.from(n.querySelectorAll(".lineas li")).map((li) => li.innerText.trim()),
      notes: (n.querySelector(".notas") || { innerText: "" }).innerText,
      sign: n.querySelector(".firma-texto").innerText.trim(),
    })));
    expect(copies, "every copy of the sheet is the same document").toHaveLength(4);
    for (const copy of copies) {
      expect(copy.doctor, "the doctor, his specialty and matrícula").toBe(`${LEVIN.name} — Cardiología · Mat. ${LEVIN.matricula}`);
      expect(copy.patient).toBe("Gabriela Impresa");
      expect(copy.dni).toBe("DNI 30777021");
      expect(copy.lines.join(" | "), "the prescribed drug").toContain(run.medication);
      expect(copy.notes).toContain(run.notes);
      expect(copy.sign, "room for the signature").toBe("Firma y sello");
    }
    expect(await page.locator(".copia").first().innerText()).toContain("Rp/");

    await page.emulateMedia({ media: "print" });
    await expect(page.locator(".print-bar"), "printed: no toolbar").toBeHidden();
    await expect(page.locator(".hoja"), "printed: no card shadow around the sheet").toHaveCSS("box-shadow", "none");
    await expect(page.locator("body"), "printed: white paper, not the grey desk").toHaveCSS("background-color", "rgb(255, 255, 255)");
    await page.emulateMedia({ media: null });
  });

  await test.step("7. the print view of the orden: original and duplicate, the study and the presumptive diagnosis", async () => {
    const page = reader.page;
    await page.goto(run.ordenUrl, { waitUntil: "load" });
    await expect(page.locator(".copia-label")).toHaveText(["Original", "Duplicado"]);
    await expect(page.locator(".copia .titulo-doc span").first()).toContainText("ORDEN DE ESTUDIOS");
    const copies = await page.locator(".copia").evaluateAll((nodes) => nodes.map((n) => ({
      doctor: n.querySelector(".medico").innerText.replace(/\s+/g, " ").trim(),
      patient: n.querySelector(".paciente-nombre").innerText.trim(),
      text: n.innerText,
    })));
    for (const copy of copies) {
      expect(copy.doctor).toBe(`${LEVIN.name} — Cardiología · Mat. ${LEVIN.matricula}`);
      expect(copy.patient).toBe("Gabriela Impresa");
      expect(copy.text, "what is asked for").toContain("Solicito:");
      expect(copy.text, "the study the doctor ticked").toContain(run.study);
      expect(copy.text, "the presumptive diagnosis").toContain("Diagnóstico presuntivo");
      expect(copy.text, "…the one the doctor ticked").toContain(run.diagnosis);
    }
    await page.emulateMedia({ media: "print" });
    await expect(page.locator(".print-bar"), "printed: no toolbar").toBeHidden();
    await page.emulateMedia({ media: null });
  });

  for (const [who, label] of [[admin, "admin"], [doctor, "doctor"], [reader, "reader"]]) {
    expect(who.errors, `no uncaught page errors for the ${label}`).toEqual([]);
  }
});
