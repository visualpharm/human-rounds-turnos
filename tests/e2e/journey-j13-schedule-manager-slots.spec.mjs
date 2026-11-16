// tests/e2e/journey-j13-schedule-manager-slots.spec.mjs -- J13 ("The schedule
// manager opens a doctor's hours, the patient sees them, books, moves and cancels,
// and the freed time goes back to the public") cross-role journey acceptance.
//
// The roles: an admin who creates a doctor-owned service and its doctor, the
// "gestor de agendas" who builds the doctor's weekly hours (the one role whose
// whole job is the agenda), two patients on the public site, and the front desk
// who approves.
//
// Terminal outcome: a time the patient cancelled (or moved away from) is offered
// to the next patient again, and a time somebody holds is offered to nobody.
//
// Steps:
//   1. Admin creates a service whose hours belong to its doctors, and a doctor
//   2. Patient A opens the booking wizard: nothing to book yet, said plainly
//   3. The gestor opens the doctor, builds a daily 10:00-12:00 window tagged to
//      the service, saves -- and none of the doctor's screens fails or denies (K8)
//   4. Patient A now sees exactly those times (and only those)
//   5. Patient A books the first one; patient B no longer sees it
//   6. The front desk approves it with the service's doctor, at the asked time
//      (and is offered only the doctors of the service's pool, K2)
//   7. Patient A moves the appointment to the second time (the API behind the
//      chat assistant's "reprogramar" -- the account page has no button for it);
//      the first time is offered again, the second is taken
//   8. Patient A cancels from the account page; the second time is offered again
// and, in its own test, a pending request cancelled by its patient gives the time back (N1).
//
// External services: none (mail is not configured on this server: sending is a
// silent skip, as in production without a provider key).
//
// Port of tests/e2e/journey-j13-schedule-manager-slots.e2e.mjs (2 tests, same titles).
//
// Differences from the node:test original:
// - The app and the staff accounts come from the worker fixtures (`appOptions` + `journey`); the
//   two tests hand state to each other (the service and the doctor's hours of the first are used by
//   the second), so they are a `serial` group: a failure skips the rest, a retry re-runs both.
// - Checks that waited for the K8 (every tab of the doctor's card opens for the gestor) and K2 (the
//   desk is offered only the service's pool) fixes are plain steps now, both are on main. The
//   original's two fixed sleeps around the tab loop are gone: each tab is clicked and the card is
//   awaited until its requests settle (`networkidle`) before the refused requests are counted.
// - The «freed time is offered again» loop is `expect.poll` over a reload of the wizard (the
//   server's slot cache lets go of a freed time after a couple of seconds).
import {
  test, expect, runFixture, seedPatient, signInStaff, openRequest, doctorCombo, approve,
  offeredSlots, hasSlot as has, expectOfferedAgain, sendRequest, cancelFromAccount,
  DESK, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS,
} from "./lib/journey.mjs";

const GESTOR = { usuario: "e2e-gestor", password: LOGIN_PHRASE, rol: "gestor_agendas" };
const SERVICE_ID = "diagnostico-por-imagenes-j13";
const SERVICE = { nombre: "Diagnóstico por imágenes J13", centro: "maternity-center", modo: "solicitud", cluster: "diagnostico", horario: "Según el médico" };
// A specialty with no service of its own in the pack at this center, so the doctor's hours are
// edited in the doctor's own editor (every pack service that has doctors owns its schedule).
const DOCTOR = { id: "irene-imagen", nombre: "Irene Imagen", form: { nombre_pila: "Irene", apellido: "Imagen", especialidad: "Diagnóstico por imágenes", centro: "maternity-center" } };
const PATIENT_A = { dni: "30222001", nombre: "Marta", apellido: "Viajera", email: "marta.viajera@example.invalid", contacto: "+5492254200001" };
const PATIENT_B = { dni: "30222002", nombre: "Bruno", apellido: "Vecino", email: "bruno.vecino@example.invalid", contacto: "+5492254200002" };
const EXPECTED_TIMES = ["10:00", "10:30", "11:00", "11:30"];

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_journey_j13" },
  journey: { staff: [GESTOR, DESK] },
});

test.describe.configure({ mode: "serial" });

const slotsNow = (page) => offeredSlots(page, SERVICE_ID);
const offeredAgain = (page, slot, what) => expectOfferedAgain(page, SERVICE_ID, slot, what);

test("J13 — the schedule manager's hours reach the patient, and a freed time goes back to the public", async ({ app, role }) => {
  test.setTimeout(180_000);
  const a = await seedPatient(app, PATIENT_A);
  const b = await seedPatient(app, PATIENT_B);
  const admin = await role();
  const gestor = await role();
  const desk = await role();
  const patientA = await role({ session: a.session });
  const patientB = await role({ session: b.session });
  const run = {};

  await test.step("1. admin creates a service whose hours belong to its doctors, and a doctor", async () => {
    await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
    const service = await admin.page.request.post("/api/admin/content/services", { data: SERVICE });
    expect(service.status(), await service.text()).toBe(200);
    expect((await service.json()).item.id).toBe(SERVICE_ID);
    const doctor = await admin.page.request.post("/api/admin/content/doctors", { data: DOCTOR.form });
    expect(doctor.status(), await doctor.text()).toBe(200);
    expect((await doctor.json()).item.id).toBe(DOCTOR.id);
  });

  await test.step("2. with no hours built, the patient is told there is nothing to book yet", async () => {
    expect(await slotsNow(patientA.page), "no times are offered before anyone built the doctor's hours").toEqual([]);
    await expect(patientA.page.locator("#svc-sub")).toContainText("Ahora no hay turnos", { useInnerText: true });
  });

  await test.step("3. the gestor builds a daily 10:00-12:00 window for the service and saves", async () => {
    const page = gestor.page;
    const denied = [];
    page.on("response", (r) => {
      const url = new URL(r.url());
      if (r.status() === 403 && (url.pathname.startsWith("/api/") || url.pathname.startsWith("/admin"))) {
        denied.push(`${r.request().method()} ${url.pathname}`);
      }
    });
    await signInStaff(page, GESTOR.usuario, GESTOR.password);
    await page.goto(`/admin/doctors?item=${DOCTOR.id}`, { waitUntil: "load" });
    const agenda = page.locator("[data-tabpanel=agenda]");
    await expect(agenda.locator("input[type=checkbox][value='1']").first()).toBeVisible();
    for (const day of ["1", "2", "3", "4", "5", "6", "0"]) await agenda.locator(`input[type=checkbox][value='${day}']`).first().check();
    const times = agenda.locator("input.hor-in[type=text]");
    await times.nth(0).fill("10:00");
    await times.nth(1).fill("12:00");
    await agenda.locator("input.hor-in--num").first().fill("30");
    const service = agenda.locator(".cmb:has(.fr-svc-sel)").first();
    await service.locator(".cmb-in").focus();
    await service.locator(".cmb-it", { hasText: "J13" }).dispatchEvent("pointerdown");
    await expect(service.locator("input.fr-svc-sel"), "the window is tagged to the service").toHaveValue(SERVICE_ID);

    const saved = page.waitForResponse((r) => new URL(r.url()).pathname.startsWith("/api/admin/content/doctors")
      && r.request().method() !== "GET");
    await page.locator("button.btn-accent", { hasText: "Guardar" }).click();
    expect((await saved).status()).toBe(200);
    await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible();

    // What was saved is what the gestor built: every weekday, 10:00 to 12:00 (not 11:00), every 30 minutes, on
    // this service -- read back from the store right here, not only inferred from the times the patient sees later.
    const { found, windows } = await runFixture(app, "doctor-windows", { doctor: DOCTOR.id });
    expect(found, "the doctor exists").toBe(true);
    expect(windows.length, "one window was saved").toBe(1);
    expect(windows[0].dias, "the window covers all seven weekdays").toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect([windows[0].desde, windows[0].hasta], "…from 10:00 to 12:00").toEqual(["10:00", "12:00"]);
    expect(windows[0].cada_min, "…every 30 minutes").toBe(30);
    expect(windows[0].servicio, "…tagged to the service").toBe(SERVICE_ID);

    // K8: every tab the card offers opens for this role, and no request it makes is refused.
    await page.reload({ waitUntil: "load" });
    const tabs = page.locator(".cnt-tabs [data-tab]");
    await expect(tabs.first()).toBeVisible();
    const names = await tabs.evaluateAll((buttons) => buttons.map((x) => x.getAttribute("data-tab")));
    for (const name of names) {
      await page.click(`.cnt-tabs [data-tab="${name}"]`);
      await expect(page.locator(`[data-tabpanel="${name}"]`)).toBeVisible();
      await page.waitForLoadState("networkidle");
    }
    // …and so does the doctors LIST the gestor lands on (its per-doctor figures come from /api/admin/doctors/stats).
    const stats = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/doctors/stats");
    await page.goto("/admin/doctors", { waitUntil: "load" });
    expect((await stats).status(), "the list's figures load for the schedule manager").toBe(200);
    await page.waitForLoadState("networkidle");
    expect([...new Set(denied)], "no request of the doctor's card is refused for the schedule manager").toEqual([]);
    await expect(page.locator("body")).not.toContainText(
      /No tenés permiso|No se pudieron cargar los datos de acceso|No se pudo cargar el acceso del médico/, { useInnerText: true });
  });

  await test.step("4. the patient now sees exactly the times the gestor built", async () => {
    let slots = [];
    await expect.poll(async () => { slots = await slotsNow(patientA.page); return slots.length; },
      { message: "times are offered", intervals: [500, 1000] }).toBeGreaterThanOrEqual(4);
    expect([...new Set(slots.map((s) => s.hora))].sort(), "only the 10:00-12:00 half-hours").toEqual(EXPECTED_TIMES);
    // The second day of the grid, not the first times offered: from 10:30 to 11:30 (Buenos Aires) today has
    // one time left (or none), and the times of today go by while the test runs.
    const days = [...new Set(slots.map((s) => s.dia))];
    expect(days.length, "the grid reaches past today").toBeGreaterThanOrEqual(2);
    [run.first, run.second] = slots.filter((s) => s.dia === days[1]);
  });

  await test.step("5. patient A books the first time; patient B no longer sees it", async () => {
    await sendRequest(patientA.page, run.first);
    const { requests } = await runFixture(app, "requests", { dni: PATIENT_A.dni });
    expect(requests.length).toBe(1);
    run.requestId = requests[0].id;
    expect(requests[0].estado).toBe("pendiente");
    expect(requests[0].servicio_id).toBe(SERVICE_ID);

    const seenByB = await slotsNow(patientB.page);
    expect(has(seenByB, run.first), "a time somebody asked for is not offered to the next patient").toBe(false);
    expect(has(seenByB, run.second), "the next time is still offered").toBe(true);
  });

  await test.step("6. the front desk approves it with the service's doctor, at the time asked for", async () => {
    const page = desk.page;
    await signInStaff(page, DESK.usuario, DESK.password);
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#list .sol-row")).toHaveCount(1);
    const detail = await openRequest(page);
    const combo = doctorCombo(detail);
    // A doctor of the same specialty at ANOTHER center (Milagros Greco, Diagnostic Center) is not
    // offered for a service at Maternity Center: the pool is the service's, as on the web.
    await expect.poll(() => combo.names(), { message: "the desk is offered only the doctors of the service's pool, as the web is" })
      .toEqual([DOCTOR.nombre]);
    await combo.pick(DOCTOR.nombre);
    await expect(detail.locator("input.turno"), "the approval proposes the time the patient asked for")
      .toHaveValue(`${run.first.dia}T${run.first.hora}`);
    await approve(page, detail);

    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("aprobada");
    expect(request.medico_asignado_id).toBe(DOCTOR.id);
    expect(request.medico_asignado).toBe(DOCTOR.nombre);
  });

  await test.step("7. patient A moves the appointment: the first time returns, the second is taken", async () => {
    const page = patientA.page;
    await page.goto("/account#turnos", { waitUntil: "load" });
    await expect(page.locator("#ms-host").getByText(DOCTOR.nombre, { exact: true }).first()).toBeVisible();
    const moved = await page.evaluate(async ({ id, dia, hora }) => {
      const response = await fetch(`/api/requests/${id}/reprogramar`, {
        method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json" }, body: JSON.stringify({ dia, hora }),
      });
      return { status: response.status, body: await response.json().catch(() => null) };
    }, { id: run.requestId, dia: run.second.dia, hora: run.second.hora });
    expect(moved.status, JSON.stringify(moved.body)).toBe(200);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado, "a moved appointment stays approved").toBe("aprobada");
    expect(request.medico_asignado_id, "…with the same doctor").toBe(DOCTOR.id);

    const seenByB = await offeredAgain(patientB.page, run.first, "the time the patient moved away from is offered to the public again");
    expect(has(seenByB, run.second), "the time she moved to is taken").toBe(false);
  });

  await test.step("8. patient A cancels from the account page; the second time returns", async () => {
    await cancelFromAccount(patientA.page);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("cancelada");

    const seenByB = await slotsNow(patientB.page);
    expect(has(seenByB, run.first), "the first time (left by the move) is still offered").toBe(true);
    // N1: the patient's own cancel releases the hold an approved (moved) request keeps on its time.
    await offeredAgain(patientB.page, run.second, "the time of a moved-then-cancelled appointment");
  });

  for (const [who, label] of [[admin, "admin"], [gestor, "gestor"], [desk, "desk"], [patientA, "patient A"], [patientB, "patient B"]]) {
    expect(who.errors, `no uncaught page errors for ${label}`).toEqual([]);
  }
});

test("J13 — a patient who cancels before the desk decides gives the time back to the public", async ({ app, role }) => {
  // The service and the doctor's hours exist from the journey above (the tests of this file run in order).
  const c = await seedPatient(app, { dni: "30222003", nombre: "Carmen", apellido: "Arrepentida", email: "carmen.arrepentida@example.invalid", contacto: "+5492254200003" });
  const d = await seedPatient(app, { dni: "30222004", nombre: "Daniel", apellido: "Siguiente", email: "daniel.siguiente@example.invalid", contacto: "+5492254200004" });
  const patientC = await role({ session: c.session });
  const patientD = await role({ session: d.session });
  const run = {};

  await test.step("1. patient C books a time and the request waits for the desk", async () => {
    // The third day of the grid (the first test used the second), never the first time offered: that one can
    // be minutes away and gone by the time patient D looks.
    const slots = await slotsNow(patientC.page);
    const days = [...new Set(slots.map((s) => s.dia))];
    expect(days.length, "times are offered on several days").toBeGreaterThanOrEqual(3);
    run.slot = slots.find((s) => s.dia === days[2]);
    await sendRequest(patientC.page, run.slot);
    const { requests } = await runFixture(app, "requests", { dni: "30222003" });
    expect(requests[0].estado).toBe("pendiente");
    run.requestId = requests[0].id;
    expect(has(await slotsNow(patientD.page), run.slot), "while the request waits, the time is not offered to patient D").toBe(false);
  });

  await test.step("2. patient C changes her mind and cancels from her account", async () => {
    await cancelFromAccount(patientC.page);
    const { request } = await runFixture(app, "request", { id: String(run.requestId) });
    expect(request.estado).toBe("cancelada");
  });

  await test.step("3. patient D is offered the time again", async () => {
    await offeredAgain(patientD.page, run.slot, "the time of a cancelled pending request");
  });
  expect(patientC.errors).toEqual([]);
  expect(patientD.errors).toEqual([]);
});
