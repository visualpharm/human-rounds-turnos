// Staff feedback (2026-10-14): «No podemos editar ningún profesional». A doctor
// whose service owns the schedule must still be editable (data, photo, limits).
//
// Port of tests/e2e/doctor-edit-service-owned.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//   page replaces the hand-made context (viewport 1280x900 via test.use) and the sign-in goes
//   through the shared loginAdmin() helper, which waits for the post-login landing page.
// - The fixed 2.5 s sleep after pressing Save is replaced by waiting for the actual
//   save response of the doctors endpoint; the statuses collected by the response listener are
//   still all checked, and the stored e-mail is then polled from the API.
// - The «schedule is owned by the service» text check keeps its meaning (either of the two
//   texts is on the page) as a retrying count on the two getByText locators.
// - Locators left as CSS: ids and `data-tab` of the doctor editor form (the markup contract
//   the test exercises).
import { test, expect, loginAdmin, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_doctor_edit_service_owned" },
  viewport: { width: 1280, height: 900 },
});

test("a doctor under a service-owned schedule saves their basic data", async ({ page, app }) => {
  const responses = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/admin/content/doctors") && response.request().method() !== "GET") {
      responses.push({ status: response.status(), url: url.pathname, method: response.request().method() });
    }
  });
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/account");

  await page.goto("/admin/doctors?item=gonzalez-juan", { waitUntil: "load" });
  await expect(page.locator("#cnt-form")).toBeVisible();
  await expect(
    page.getByText("El horario lo define el servicio para todos sus médicos."),
    "the fixture doctor is under a service-owned schedule",
  ).not.toHaveCount(0);
  await page.locator('[data-tab="info"]').click();
  const email = "prueba." + Date.now() + "@example.org";
  await page.fill("#med-email", email);
  const saveResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.startsWith("/api/admin/content/doctors") && response.request().method() !== "GET");
  await page.locator('button[type="submit"]').click();
  await saveResponse;
  const saved = responses.filter((r) => r.method !== "GET");
  expect(saved.length, "a save request was sent").toBeGreaterThan(0);
  expect(saved.map((r) => r.status), "save accepted: " + JSON.stringify(saved)).toEqual(saved.map(() => 200));
  await expect.poll(async () => {
    const list = await (await page.request.get("/api/admin/content/doctors")).json();
    return list.items.find((row) => row.id === "gonzalez-juan").email;
  }).toBe(email);
  await page.goto("/admin/doctors?item=gonzalez-juan");
  await page.reload();
  await page.locator('[data-tab="info"]').click();
  await expect(page.locator("#med-email")).toHaveValue(email);
});
