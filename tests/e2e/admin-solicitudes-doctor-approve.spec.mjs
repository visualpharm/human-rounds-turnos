// A doctor approves a request from Solicitudes (critical review 2026-11-08, F1).
//
// Under a doctor the detail pane draws no service picker (`.svc-sel`): a doctor does not
// derive to another service. The agenda request read the service from that picker only, so
// it went out as `appointment-slots?servicio_id=` and the server answered 400; the pane
// said «No se pudieron cargar los horarios» and «Aprobar» stayed off for every request.
// The service now comes from the request itself when the picker is not drawn.
//
// The same detail under the admin (with the picker) is covered by
// admin-solicitudes-agenda-race.spec.mjs. Waits are retrying expects; nothing sleeps.
import { test, expect, signInStaff, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS } from "./lib/journey.mjs";
import { saveShot } from "./lib/fixtures.mjs";

const DOCTOR = { id: "ruiz-sergio", nombre: "Sergio Ruiz", usuario: "e2e-dr-ruiz-sol", password: LOGIN_PHRASE };
const SERVICE_ID = "electrocardiograma-hospital";
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1440, height: 900 };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_admin_solicitudes_doctor_approve", requests: 2 },
  journey: {
    staff: [{ usuario: DOCTOR.usuario, password: DOCTOR.password, rol: "medico", medico_id: DOCTOR.id }],
  },
});

/** The admin hands one seeded pending request to the doctor, through the panel's own endpoint. */
async function assignToDoctor(role, requestId) {
  const admin = await role({ viewport: DESKTOP });
  try {
    await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
    const res = await admin.page.request.post(`/api/admin/requests/${requestId}/assign`,
      { data: { medico_id: DOCTOR.id } });
    expect(res.status(), await res.text()).toBe(200);
  } finally {
    await admin.context.close();
  }
}

test("the doctor opens a request: the agenda loads for its service and «Aprobar» is on", async ({ app, role }, testInfo) => {
  const target = app.fixture.requests.find((r) => r.nombre.startsWith("Pilar"));
  await assignToDoctor(role, target.id);

  const doctor = await role({ viewport: DESKTOP });
  const page = doctor.page;
  await signInStaff(page, DOCTOR.usuario, DOCTOR.password);
  const slots = [];
  page.on("response", (r) => { if (r.url().includes("/api/admin/appointment-slots?")) slots.push(r); });
  await page.goto("/admin/solicitudes", { waitUntil: "load" });
  const row = page.locator("#list .sol-row", { hasText: "Pilar" });
  await expect(row).toBeVisible({ timeout: 12000 });
  await row.click();
  const detail = page.locator("aside.sol-det[data-id]");
  await expect(detail).toBeVisible();
  await expect(detail.locator(".svc-sel"), "a doctor gets no service picker").toHaveCount(0);

  const aprobar = detail.locator('[data-act="aprobar"]');
  const select = detail.locator("select.turno-sel");
  await expect(aprobar, "the agenda arrived: Aprobar is on").toBeEnabled({ timeout: 15000 });
  await expect(select).toBeEnabled();
  await expect(detail.locator(".sol-agenda-err"), "no error line").toHaveCount(0);
  const asked = slots.map((r) => new URL(r.url()).searchParams.get("servicio_id"));
  expect(asked, "the agenda was asked for the request's service").toContain(SERVICE_ID);
  expect(slots.every((r) => r.status() === 200), "every agenda request was answered").toBe(true);
  const times = await select.locator("option").evaluateAll((o) => o.map((x) => x.value).filter((v) => v && v !== "__otro"));
  expect(times.length, "the service's agenda offers times").toBeGreaterThan(0);
  expect(times, "a time is preselected").toContain(await detail.locator("input.turno").inputValue());
  await saveShot(testInfo, detail, testInfo.outputPath("medico-solicitud-1440.png"));
  await page.setViewportSize(PHONE);
  await expect(detail).toBeVisible();
  await saveShot(testInfo, detail, testInfo.outputPath("medico-solicitud-390.png"));
  expect(doctor.errors, "no page errors").toEqual([]);
});
