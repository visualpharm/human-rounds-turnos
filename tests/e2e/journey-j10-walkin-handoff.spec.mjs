// tests/e2e/journey-j10-walkin-handoff.spec.mjs — J10 ("Receptionist books a walk-in
// for a phone patient") end-to-end journey acceptance.
//
// Terminal outcome (workflow-acceptance.md): appointment created by staff (not
// subject to patient booking limits), and it APPEARS IN THE DOCTOR'S DAY QUEUE.
//
// Steps:
//   1. Admin creates an admisionista account (staff role)
//   2. Login as admisionista
//   3. Navigate to /admin/cola (day queue)
//   4. Pick a doctor from the queue
//   5. Click "Crear turno" to open the appointment-creation overlay
//   6. Complete the overlay: fictional patient → service → slot → confirm
//   7. Verify the appointment was CREATED (via fixture.py query)
//   8. Verify it APPEARS in the doctor's day queue after reload
//   9. Booking-limit gate: create a SECOND appointment for the SAME patient
//      + service through the staff path and assert it succeeds (patient
//      wizard would get 409 limite_turnos_activos; staff must not)
//      9b. Same claim for a patient WITH a record and DNI (the limit counts by DNI, a noted walk-in
//      can never trip it): booked twice for one service by the desk, both approved; her own
//      request for that service is refused with 409 limite_turnos_activos
//  10. Screenshots at desktop 1440x900 and phone 390x844 of major states
//  11. Assert no page errors, no console.error, no horizontal overflow
//
// Port of tests/e2e/journey-j10-walkin-handoff.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app is the worker-scoped `app` fixture (same dbName, `queue: true`); the desktop
//   session is the runner's `page` at 1440x900 (`test.use`), the phone session is a second
//   context (`newContext`). `t.after(context.close)` and the `finally` blocks are the fixtures'
//   own teardown.
// - The `console.log("Step …")` progress lines are `test.step`s, so the report shows where a
//   failure happened. Screenshots go through `saveShot` into the same directory
//   (`<artifacts>/workflow-test-and-fix/20260915/j10`, via `artifactPath`; the original
//   hard-coded `~/.claude/artifacts`, the same default) and are attached to the report.
// - The two sleeps are state waits. After typing the service query (400 ms) the test waits for
//   the suggestions to be listed (`not.toHaveCount(0)`, same «found services matching»
//   message) before reading them. After typing the booked day in the queue's own search
//   (500 ms), the walk-in's row is awaited with a retrying `toContainText` BEFORE the
//   screenshot, and the «two rows for the same walk-in» count is an `expect.poll` (>= 2);
//   the assertions themselves (name appears, at least two rows) are the original's.
// - `waitForFunction` polls over text/ids became retrying assertions (`toContainText`
//   with `useInnerText`, `toHaveCount`) where they only wait for a state; the compound
//   «finished or still open» condition of `confirmAppointment` stays a `waitForFunction`.
// - Locators: ids/classes/data-* stay CSS (#usr-*, #ct-*, #sec-cola, button.queue-doctor-link,
//   #adm-crear-turno, [data-cola-buscar]): they are the admin screens' markup contract.
import { stat } from "node:fs/promises";
import { join } from "node:path";
import {
  test, expect, ADMIN_USER, ADMIN_PASS, queryNota, saveShot, artifactPath, patientCookie,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";
import { runFixture, seedPatient } from "./lib/journey.mjs";
import { pickQueueDoctor } from "./lib/queue-doctor.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j10", queue: true },
  viewport: { width: 1440, height: 900 },
});

const SCREENSHOT_BASE = artifactPath("workflow-test-and-fix", "20260915", "j10");

// The walk-in typed as a free-text note (no record, no DNI) ...
const WALK_IN_NAME = "García Mariana";
// ... and a patient WITH a record and a DNI, for the booking-limit claim: the limit counts by
// final clinical DNI, so a noted walk-in (dni NULL) can never trip it and proves nothing.
const LIMIT_PATIENT = {
  dni: "38123456", nombre: "Mariana", apellido: "Quiroga", email: "mariana.quiroga@example.invalid", contacto: "+5492254810001",
};

// A seeded queue doctor to target (example-town pack, cardiologia).
// The --queue fixture seeds Sergio Ruiz with an approved appointment.
const QUEUE_DOCTOR_ID = "ruiz-sergio";
const QUEUE_DOCTOR_NAME = "Sergio Ruiz";

// First service with slots available in the queue doctor's agenda (cardiologia).
const SERVICE_QUERY = "cardio";

async function captureScreenshot(testInfo, page, filename) {
  await page.evaluate(() => scrollTo(0, 0));
  await page.evaluate(() =>
    new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))
    )
  );
  const path = join(SCREENSHOT_BASE, filename);
  const shot = await saveShot(testInfo, page, path, { fullPage: false });
  return { path, bytes: (await stat(path)).size, ...shot };
}

async function loginAdmin(page) {
  await page.goto("/admin/login", {
    waitUntil: "load",
    timeout: 15000,
  });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  await page.fill("#gate-user", ADMIN_USER);
  await page.fill("#gate-pass", ADMIN_PASS);
  await page.click("#gate-btn");
  await page.waitForFunction(
    () =>
      location.pathname.startsWith("/admin/") &&
      location.pathname !== "/admin/login",
    null,
    { timeout: 10000 }
  );
}

async function createAdmisionistaAccount(page) {
  // Navigate to the admin users screen's "Nuevo usuario" form (the list's
  // accent button links to /admin/users?new=1) and create an admisionista
  // account through the REAL /admin/users form.
  await page.goto("/admin/users?new=1", {
    waitUntil: "load",
    timeout: 15000,
  });
  await expect(page.locator("#usr-form")).toBeVisible({ timeout: 10000 });

  // Fill in the form: usuario, email, password
  await page.fill("#usr-usuario", "99123456");
  await page.fill("#usr-email", "adm99123456@example-test.gob.ar");
  await page.fill("#usr-pass", "admtest123456");

  // Select role: admisionista (radio group inside .cnt-modo-list)
  await page
    .locator('input[name="usr-rol"][value="admisionista"]')
    .check();

  // Submit the form
  await page.locator('button[type="submit"][form="usr-form"]').click();

  // A successful save returns to the list; the new row shows the username.
  await expect(page.locator("#usr-tbody")).toBeVisible({ timeout: 10000 });
  await expect(page.locator("body")).toContainText("99123456", { timeout: 10000, useInnerText: true });
}

async function loginAsAdmisionista(page) {
  await page.goto("/admin/login", {
    waitUntil: "load",
    timeout: 15000,
  });
  await expect(page.locator("#gate-form")).toBeVisible({ timeout: 8000 });
  await page.fill("#gate-user", "99123456");
  await page.fill("#gate-pass", "admtest123456");
  await page.click("#gate-btn");
  await page.waitForFunction(
    () =>
      location.pathname.startsWith("/admin/") &&
      location.pathname !== "/admin/login",
    null,
    { timeout: 10000 }
  );
}

async function openDayQueue(page) {
  await page.goto("/admin/cola", {
    waitUntil: "load",
    timeout: 20000,
  });
  await expect(page.locator("#sec-cola")).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
}

async function pickDoctorInQueue(page, doctorId) {
  // The doctor's link in the day's one list narrows it to that doctor.
  await pickQueueDoctor(page, doctorId);
  await expect(page.locator("#adm-crear-turno")).toBeVisible({ timeout: 10000 });
}

async function openCreateAppointmentOverlay(page) {
  // The title's «Crear turno» books for the doctor the list is narrowed to.
  const crearBtn = page.locator("#adm-crear-turno");
  await crearBtn.click();
  await expect(page.locator("#ct-modal")).toBeVisible({ timeout: 10000 });
}

async function fillPatientStep(page, name) {
  // The overlay starts at the patient selection step
  await expect(page.locator("#ct-pac")).toBeVisible({ timeout: 10000 });

  // Clear and fill the patient search box
  await page.fill("#ct-pac", name);

  // Wait for the "anotar" (add new note) option to appear
  await expect(page.locator("#ct-pac-sug li[data-nota]")).toBeVisible({ timeout: 10000 });

  // Click "anotar" to add as a new patient note (no pre-existing patient)
  await page.click("#ct-pac-sug li[data-nota]");

  // Advance to service selection step
  await expect(page.locator("#ct-svc")).toBeVisible({ timeout: 10000 });
}

async function pickPatientByDni(page, patient) {
  // A patient with a record: the search finds her by DNI and the desk takes the record, not a note.
  await expect(page.locator("#ct-pac")).toBeVisible({ timeout: 10000 });
  await page.fill("#ct-pac", patient.dni);
  const found = page.locator(`#ct-pac-sug li[data-dni="${patient.dni}"]`);
  await expect(found, "the desk finds the patient by her DNI").toBeVisible({ timeout: 10000 });
  await found.click();
  await expect(page.locator("#ct-svc")).toBeVisible({ timeout: 10000 });
}

async function fillServiceStep(page, serviceQuery) {
  // Search for a service matching the doctor's cluster
  await page.fill("#ct-svc", serviceQuery);

  // Wait for suggestions
  await expect(
    page.locator("#ct-svc-sug li[data-svc]:not([data-vacio])"),
    `found services matching "${serviceQuery}"`,
  ).not.toHaveCount(0, { timeout: 10000 });

  // Click the first matching service
  const firstService = page.locator("#ct-svc-sug li[data-svc]").first();
  await firstService.click();

  // The slot step auto-proposes the first free slot (".ct-prop"); a manual
  // pick would open the calendar via #ct-vermas, but the proposal is the
  // designed happy path for a receptionist booking a walk-in.
  await expect(page.locator(".ct-prop-cuando").first()).toBeVisible({ timeout: 10000 });
}

async function fillSlotStep(page) {
  // The proposal above IS the selected slot ("lo elegido a mano, o el primer
  // horario libre"); read it back for the record.
  const proposal = page.locator(".ct-prop-cuando").first();
  await expect(proposal).toBeVisible({ timeout: 10000 });
  const cuando = (await proposal.textContent()).trim();
  expect(cuando.length, "slot proposal shows a date and time").toBeGreaterThan(0);
  return { slotId: cuando };
}

// The proposal reads like "martes 16 de septiembre, 08:30" (Dates.amigable).
// The day queue's own search parses "d/m" dates (fechaDeTexto), so the
// visibility gates below re-find the booked day without hardcoding it.
const MESES_ES = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

async function diaMesDesdeCuando(page, cuando) {
  // Dates.amigable names the two nearest days ("Hoy, 09:30" / "Mañana,
  // 08:10") instead of spelling the date, and it decides which is which on the
  // calendar of the INSTALLATION (dates.mjs: America/Argentina/Buenos_Aires),
  // whatever the browser's own timezone is. Resolve them the same way, in the
  // page: the CI browser runs in the container's UTC, so between 00:00 and
  // 03:00 UTC the UTC calendar names the day after the Buenos Aires one.
  // «Mañana» carries its weekday and number since 2026-11-10 ("Mañana,
  // miércoles 11, 08:10"); «Hoy» stays bare.
  const relativo = cuando.match(/^(Hoy|Mañana)(?:,\s*[a-záéíóúñ]+\s+\d{1,2})?,\s*\d{1,2}:\d{2}$/i);
  if (relativo) {
    return page.evaluate((manana) => {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Argentina/Buenos_Aires",
        year: "numeric", month: "2-digit", day: "2-digit",
      }).formatToParts(new Date());
      const v = Object.fromEntries(parts.map((p) => [p.type, +p.value]));
      const d = new Date(Date.UTC(v.year, v.month - 1, v.day + (manana ? 1 : 0)));
      return `${d.getUTCDate()}/${d.getUTCMonth() + 1}`;
    }, relativo[1] === "Mañana");
  }
  const m = cuando.match(/(\d{1,2})\s+de\s+([a-záéíóúñ]+)/i);
  expect(m, `proposal text carries a Spanish date: "${cuando}"`).toBeTruthy();
  const mes = MESES_ES.findIndex((nombre) => nombre.startsWith(m[2].slice(0, 4).toLowerCase()));
  expect(mes, `recognized month "${m[2]}"`).toBeGreaterThanOrEqual(0);
  return `${parseInt(m[1], 10)}/${mes + 1}`;
}

async function buscarDiaEnCola(page, diaMes) {
  const input = page.locator("[data-cola-buscar]");
  await expect(input).toBeVisible({ timeout: 10000 });
  await input.fill(diaMes);
}

async function confirmAppointment(page) {
  // #ct-ok creates the appointment directly (no separate confirm step). A
  // turno with a patient record ends on «Turno creado» with its HSI line
  // (2026-10-19); «Listo» closes it. A turno noted without DNI closes at once.
  const okBtn = page.locator("#ct-ok");
  await expect(okBtn).toBeVisible({ timeout: 10000 });
  await okBtn.click();
  await page.waitForFunction(
    () => !document.getElementById("ct-back") || !!document.getElementById("ct-listo"),
    null,
    { timeout: 10000 }
  );
  const listo = page.locator("#ct-listo");
  if (await listo.count()) {
    await expect(page.locator("#ct-modal h2")).toHaveText(/creado/, { useInnerText: true });
    await listo.click();
  }
  await expect(page.locator("#ct-back")).toHaveCount(0, { timeout: 10000 });
  await expect(page.locator("#ct-modal")).toHaveCount(0, { timeout: 10000 });
  await expect(page.locator("body")).toContainText("Turno creado", { timeout: 10000, useInnerText: true });
}

// A turno for a patient WITH a record: the dialog is taken down while the request is in flight and put
// back as «Turno creado» when the server answers 200 (a refusal puts it back with the reason instead),
// so "the dialog is gone" is true before the answer; wait for the dialog's own «Turno creado» and close it.
async function confirmAppointmentWithRecord(page) {
  await page.locator("#ct-ok").click();
  await expect(page.locator("#ct-modal h2"), "the server created the turno").toHaveText(/creado/, { useInnerText: true, timeout: 10000 });
  await page.locator("#ct-listo").click();
  await expect(page.locator("#ct-back")).toHaveCount(0, { timeout: 10000 });
}

function assertNoPageErrors(errors) {
  const fatal = errors.filter((e) => !e.includes("favicon"));
  expect(fatal, `no page errors: ${fatal.length} error(s) found`).toEqual([]);
}

async function assertNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth
  );
  expect(overflow, `no horizontal overflow at phone width: ${overflow}px`).toBeLessThanOrEqual(1);
}

// The overlay is `position: fixed`: it never widens the document, so the page-level check above
// cannot see a modal wider than the phone. Measure the dialog itself and the controls inside it.
async function assertModalFitsViewport(page, what) {
  const viewport = page.viewportSize();
  const modal = await stableBox(page.locator("#ct-modal"), `${what}: the dialog`);
  expect(modal, `${what}: the create-appointment dialog is on screen`).not.toBeNull();
  expect(modal.x, `${what}: the dialog starts inside the viewport (x=${modal.x})`).toBeGreaterThanOrEqual(0);
  expect(modal.x + modal.width, `${what}: the dialog ends inside the viewport (right edge ${modal.x + modal.width} of ${viewport.width})`)
    .toBeLessThanOrEqual(viewport.width);
  for (const selector of ["#ct-pac", "#ct-svc", ".ct-close", ".ct-prop"]) {
    const control = page.locator(`#ct-modal ${selector}`).first();
    if (!(await control.count())) continue;
    const box = await stableBox(control, `${what}: ${selector}`);
    expect(box.x, `${what}: ${selector} is not clipped on the left`).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, `${what}: ${selector} is not clipped on the right`).toBeLessThanOrEqual(viewport.width);
  }
}

test("J10 — Receptionist books a walk-in for a phone patient (staff)", async ({ page, app, newContext, baseURL }, testInfo) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console.error: ${m.text()}`);
  });

  // Step 1: Admin creates an admisionista account through the real /admin/users UI
  await test.step("Step 1: Creating admisionista account as admin", async () => {
    await loginAdmin(page);
    await createAdmisionistaAccount(page);
  });

  // Step 2: Login as the new admisionista
  await test.step("Step 2: Logging in as admisionista", async () => {
    await page.goto("/admin/login", {
      waitUntil: "load",
      timeout: 15000,
    });
    await loginAsAdmisionista(page);

    // Screenshot: admisionista default landing (Solicitudes)
    await captureScreenshot(testInfo, page, "01-admisionista-login-desktop-1440x900.png");
  });

  // Step 3: Navigate to /admin/cola (day queue)
  await test.step("Step 3: Opening day queue", async () => {
    await openDayQueue(page);

    // Screenshot: day queue overview
    await captureScreenshot(testInfo, page, "02-queue-overview-desktop-1440x900.png");
  });

  // Step 4: Pick a doctor from the queue
  await test.step(`Step 4: Picking doctor ${QUEUE_DOCTOR_NAME} (${QUEUE_DOCTOR_ID})`, async () => {
    await pickDoctorInQueue(page, QUEUE_DOCTOR_ID);

    // Screenshot: doctor selected in queue
    await captureScreenshot(testInfo, page, "03-doctor-selected-desktop-1440x900.png");
  });

  // Step 5: Click "Crear turno" to open the overlay
  await test.step("Step 5: Opening 'Crear turno' overlay", async () => {
    await openCreateAppointmentOverlay(page);

    // Screenshot: overlay opened
    await captureScreenshot(testInfo, page, "04-overlay-opened-desktop-1440x900.png");
  });

  // Step 6a: Fill patient step
  await test.step("Step 6a: Filling patient information", async () => {
    await fillPatientStep(page, WALK_IN_NAME);

    // Screenshot: patient filled
    await captureScreenshot(testInfo, page, "05-patient-filled-desktop-1440x900.png");
  });

  // Step 6b: Fill service step
  await test.step("Step 6b: Selecting service", async () => {
    await fillServiceStep(page, SERVICE_QUERY);

    // Screenshot: service selected
    await captureScreenshot(testInfo, page, "06-service-selected-desktop-1440x900.png");
  });

  // Step 6c: Fill slot step
  let slotId;
  await test.step("Step 6c: Selecting appointment slot", async () => {
    ({ slotId } = await fillSlotStep(page));
    expect(slotId, "slot selected successfully").toBeTruthy();

    // Screenshot: slot confirmed
    await captureScreenshot(testInfo, page, "07-slot-selected-desktop-1440x900.png");
  });

  // Step 6d: Confirm appointment
  await test.step("Step 6d: Confirming appointment creation", async () => {
    await confirmAppointment(page);

    // Screenshot: after confirmation
    await captureScreenshot(testInfo, page, "08-after-confirm-desktop-1440x900.png");
  });

  // GATE: State — Verify the appointment was created through the APP's own
  // read path (fixture.py query-nota): a staff walk-in stores the typed
  // note as the row's nombre with dni NULL.
  await test.step("GATE (State 1): Verifying appointment was created", async () => {
    const notaCheck = await queryNota(app.dbName, WALK_IN_NAME);
    expect(
      notaCheck.found,
      `walk-in nota "${WALK_IN_NAME}" appears in booking_requests`
    ).toBeTruthy();
    expect(
      notaCheck.estado === "aprobada",
      `staff walk-in is created approved, not pending (estado=${notaCheck.estado})`
    ).toBeTruthy();
  });

  // Step 7: Verify the appointment persists: the walk-in was booked on the
  // first free slot's day (evening runs make today's slots past), so
  // navigate the queue to THAT day through its own search. With the doctor
  // still selected the queue shows his agenda directly — the walk-in's
  // row must be visible there.
  const diaMes = await diaMesDesdeCuando(page, slotId);
  await test.step("Step 7: Finding the booked day in /admin/cola", async () => {
    await buscarDiaEnCola(page, diaMes);

    // Verify the newly created appointment appears in the queue view
    await expect(
      page.locator("body"),
      `newly created appointment with ${WALK_IN_NAME} appears in queue (${diaMes})`,
    ).toContainText(WALK_IN_NAME, { useInnerText: true });

    // Screenshot: queue on the booked day
    await captureScreenshot(testInfo, page, "09-queue-after-reload-desktop-1440x900.png");
  });

  // Step 8: Booking-limit gate — Create a SECOND appointment for the SAME
  // patient + service through the staff path. This must succeed (staff
  // bypasses booking limits).
  await test.step("Step 8 (Booking-limit gate): Creating second appointment for same patient+service", async () => {
    // Re-open the overlay for the second booking
    await openCreateAppointmentOverlay(page);

    // Fill patient, service, slot again
    await fillPatientStep(page, WALK_IN_NAME);
    await fillServiceStep(page, SERVICE_QUERY);
    const second = await fillSlotStep(page);
    const diaMes2 = await diaMesDesdeCuando(page, second.slotId);

    // Confirm the second appointment
    await confirmAppointment(page);

    // Verify the second appointment was also created: the queue on the
    // booked day now shows TWO entries for the same walk-in (staff bypasses
    // booking limits). The second one takes the next free slot, which is on
    // the following day when the first took the last slot of its day (seen
    // at 11:40: «Hoy, 11:40» then «Mañana, 08:00»): each day then shows one row.
    const countOccurrences = async () =>
      (await page.evaluate(() => document.body.innerText)).split(WALK_IN_NAME).length - 1;
    const rowsPerDay = diaMes2 === diaMes ? [[diaMes, 2]] : [[diaMes, 1], [diaMes2, 1]];
    for (const [day, rows] of rowsPerDay) {
      await buscarDiaEnCola(page, day);
      await expect.poll(
        countOccurrences,
        { message: `second appointment for the same walk-in also created (staff bypasses booking limits; queue rows for ${WALK_IN_NAME} on ${day})`, timeout: 10000 },
      ).toBeGreaterThanOrEqual(rows);
    }

    // Screenshot: queue with both appointments
    await captureScreenshot(testInfo, page, "10-queue-two-appts-desktop-1440x900.png");
  });

  // Step 8b: the same claim for a patient WITH a record. The limit counts by DNI, so only a patient
  // who has one can prove the desk is exempt: she is booked twice for the same service and both
  // stand; her own request for that service (the patient's wizard) is refused by the limit.
  await test.step("Step 8b (Booking-limit gate): a patient with a DNI is booked twice for the same service by the desk", async () => {
    const holder = await seedPatient(app, LIMIT_PATIENT);
    for (const round of [1, 2]) {
      await openCreateAppointmentOverlay(page);
      await pickPatientByDni(page, LIMIT_PATIENT);
      await fillServiceStep(page, SERVICE_QUERY);
      await fillSlotStep(page);
      await confirmAppointmentWithRecord(page);
      const { requests } = await runFixture(app, "requests", { dni: LIMIT_PATIENT.dni });
      expect(requests.length, `desk booking ${round} for the patient with a DNI is stored`).toBe(round);
    }
    const { requests } = await runFixture(app, "requests", { dni: LIMIT_PATIENT.dni });
    expect(requests.map((r) => r.estado), "both desk bookings are approved, the second is not held back by the limit")
      .toEqual(["aprobada", "aprobada"]);
    expect(new Set(requests.map((r) => r.servicio_id)).size, "both are for the same service").toBe(1);
    expect(new Set(requests.map((r) => r.turno_dt)).size, "…at two different times").toBe(2);

    // The limit exists: the same patient asking for the same service herself is told no.
    const ownContext = await newContext();
    await ownContext.addCookies([patientCookie(baseURL, holder.session)]);
    const own = await ownContext.newPage();
    await own.goto("/account", { waitUntil: "load" });
    const refused = await own.evaluate(async ({ servicio, centro }) => {
      const post = await fetch("/api/requests", {
        method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
        body: JSON.stringify({ servicio_id: servicio, centro_slug: centro }),
      });
      return { status: post.status, body: await post.json().catch(() => ({})) };
    }, { servicio: requests[0].servicio_id, centro: requests[0].centro_slug });
    expect([refused.status, refused.body.error], "the patient's own request is stopped by the limit the desk is exempt from")
      .toEqual([409, "limite_turnos_activos"]);
    expect(refused.body.abiertas, "the limit counts the two the desk booked").toBe(2);
    expect((await runFixture(app, "requests", { dni: LIMIT_PATIENT.dni })).requests.length, "the refusal created nothing").toBe(2);
  });

  // GATE: UX & Visual — Phone-width screenshots (390x844)
  await test.step("Taking phone-width screenshots (390x844)", async () => {
    const phoneContext = await newContext({ viewport: { width: 390, height: 844 } });
    const phoneErrors = [];
    const phonePage = await phoneContext.newPage();
    phonePage.on("pageerror", (e) => phoneErrors.push(String(e)));
    phonePage.on("console", (m) => {
      if (m.type() === "error") phoneErrors.push(`console.error: ${m.text()}`);
    });

    // Phone: login and navigate to queue
    await loginAdmin(phonePage);
    await openDayQueue(phonePage);
    await captureScreenshot(testInfo, phonePage, "11-queue-overview-phone-390x844.png");

    await pickDoctorInQueue(phonePage, QUEUE_DOCTOR_ID);
    await openCreateAppointmentOverlay(phonePage);
    await captureScreenshot(testInfo, phonePage, "12-overlay-opened-phone-390x844.png");

    // Verify no horizontal overflow at phone width, and that the dialog itself fits the phone
    // (opened, and again with the service and the proposed time on screen: the widest step).
    await assertNoHorizontalOverflow(phonePage);
    await assertModalFitsViewport(phonePage, "dialog just opened");
    await fillPatientStep(phonePage, WALK_IN_NAME);
    await fillServiceStep(phonePage, SERVICE_QUERY);
    await assertModalFitsViewport(phonePage, "dialog with the proposed time");

    // Verify no page errors
    assertNoPageErrors(phoneErrors);
  });

  // GATE: Function — Appointment was created (verified above via queryNota)
  // GATE: State — Appointment persists after reload (verified above)
  // GATE: UX — Screenshots taken at both widths, no confusion observed
  // GATE: Visual — No page errors, no horizontal overflow

  // Final assertion: no page errors on the primary session
  assertNoPageErrors(errors);
});
