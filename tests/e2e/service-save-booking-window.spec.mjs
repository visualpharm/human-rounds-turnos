// Agenda creators (2026-10-14): changing only «Días de anticipación» / «Hora de
// apertura» of a service that already has its schedule must save at once — it
// must not ask to reset doctors' schedules, and must not touch them.
//
// Port of tests/e2e/service-save-booking-window.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//   page replaces the hand-made context (viewport 1280x900 via test.use) and the sign-in goes
//   through the shared loginAdmin() helper.
// - Waits are web-first: the editor form, the saved status and the reloaded fields are
//   retrying expects with the original 15 s timeout where the original had one. The «no reset
//   confirmation dialog» check stays a point-in-time count after the save response (a retrying
//   check would also pass a dialog that appears late).
// - Locators left as CSS: the editor ids (#cnt-form, #svc-*, #cnt-save-status) and the
//   `dialog[open]` state, which are the markup contract the test exercises.
import { test, expect, loginAdmin, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_service_booking_window" },
  viewport: { width: 1280, height: 900 },
});

test("a booking-window-only save of a scheduled service asks nothing and keeps the doctors", async ({ page, app }) => {
  const writes = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/admin/content/") && response.request().method() !== "GET") {
      writes.push({ method: response.request().method(), path: url.pathname, status: response.status() });
    }
  });
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/account");

  const doctorsBefore = await (await page.request.get("/api/admin/content/doctors")).json();
  const services = await (await page.request.get("/api/admin/content/services")).json();
  const scheduled = services.items.find((item) => item.id === "medicina-general-riverside-clinic");
  expect(scheduled && scheduled.agenda, "the fixture service owns a schedule").toBeTruthy();

  await page.goto("/admin/services?item=" + encodeURIComponent(scheduled.id), { waitUntil: "load" });
  await expect(page.locator("#cnt-form")).toBeVisible();
  // an existing service keeps «Configuración interna» folded
  await expect(page.locator("#svc-dias-anticipacion")).toBeHidden();
  await page.locator("#svc-internal > summary").click();
  await page.locator("#svc-dias-anticipacion").fill("15");
  await page.locator("#svc-apertura-turnos").fill("08:00");
  const writeResponse = page.waitForResponse((response) => new URL(response.url()).pathname.startsWith("/api/admin/content/services")
    && response.request().method() !== "GET", { timeout: 15000 });
  await page.locator('button[type="submit"]').click();
  const response = await writeResponse;
  const body = await response.text();
  if (response.status() !== 200) {
    const sent = JSON.parse(response.request().postData() || "{}");
    console.log("STORED agenda   :", JSON.stringify(scheduled.agenda));
    console.log("SENT agenda     :", JSON.stringify(sent.agenda));
    for (const key of ["centro", "cluster", "nombre"]) console.log(key, JSON.stringify(scheduled[key]), JSON.stringify(sent[key]));
  }
  expect(response.status(), "save accepted without a reset confirmation: " + body.slice(0, 300)).toBe(200);
  expect(await page.locator("dialog[open]").count(), "no reset confirmation was shown").toBe(0);
  await expect(page.locator("#cnt-save-status").filter({ hasText: "Guardado." })).toBeVisible({ timeout: 15000 });
  const saves = writes.filter((write) => write.path.includes("/services/"));
  expect(saves.length > 0 && saves.every((write) => write.status === 200), JSON.stringify(writes)).toBe(true);

  const after = await (await page.request.get("/api/admin/content/services")).json();
  const saved = after.items.find((item) => item.id === scheduled.id);
  expect(saved.dias_anticipacion).toBe(15);
  expect(saved.apertura_turnos).toBe("08:00");
  // The editor writes the default quotas back; days, hours and step are untouched.
  const { cupos, ...savedWindow } = saved.agenda;
  expect(savedWindow, "the service schedule is untouched").toEqual(scheduled.agenda);
  expect(cupos || { web: 1, reservado: 0, sobreturnos: 0 }).toEqual({ web: 1, reservado: 0, sobreturnos: 0 });
  const doctorsAfter = await (await page.request.get("/api/admin/content/doctors")).json();
  const pick = (payload) => Object.fromEntries(payload.items.map((d) => [d.id, JSON.stringify(d.centros || d.agenda || null)]));
  expect(pick(doctorsAfter), "no doctor schedule was reset").toEqual(pick(doctorsBefore));
  await page.reload();
  await page.locator("#svc-internal > summary").click();
  await expect(page.locator("#svc-dias-anticipacion")).toBeVisible();
  await expect(page.locator("#svc-dias-anticipacion")).toHaveValue("15");
  await expect(page.locator("#svc-apertura-turnos")).toHaveValue("08:00");
});
