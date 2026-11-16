// tests/e2e/doctor-center-window.spec.mjs -- the booking window a doctor has AT a
// center (hospital TASK-35): typed in the doctor editor's center block, saved,
// shown again after a cold reload, and left alone by an empty field. Phone and
// desktop widths; a screenshot of each width lands in the test's output folder.
import { test, expect, loginAdmin } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_doctor_center_window" } });

async function makeDoctor(context, baseUrl, id) {
  const centerId = id + "-center";
  const doctors = await (await context.request.get(baseUrl + "/api/admin/content/doctors")).json();
  const template = doctors.items.find((item) => item.id === "gonzalez-juan");
  expect(template).toBeTruthy();
  const center = await context.request.post(baseUrl + "/api/admin/content/centers", {
    data: { slug: centerId, name: "Synthetic Window Center" },
  });
  expect(center.status()).toBe(200);
  const doctor = { ...template, id, nombre: "Synthetic Windower", nombre_pila: "Synthetic", apellido: "Windower",
    centros: [{ centro: centerId, franjas: [{ dias: [1], desde: "08:00", hasta: "12:00", cada_min: 20 }], excepciones: [] }] };
  for (const key of Object.keys(doctor)) if (key.startsWith("_")) delete doctor[key];
  const created = await context.request.post(baseUrl + "/api/admin/content/doctors", { data: doctor });
  expect(created.status()).toBe(200);
  return centerId;
}

for (const [label, viewport] of [["phone", { width: 390, height: 844 }], ["desktop", { width: 1280, height: 900 }]]) {
  test(`the center's booking window saves, survives a cold reload and clears (${label})`, async ({ app, newContext }, testInfo) => {
    const errors = [];
    const context = await newContext({ viewport });
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(String(e)));
    await loginAdmin(page, app.baseUrl);
    const id = "window-" + label + "-" + Date.now();
    const centerId = await makeDoctor(context, app.baseUrl, id);
    const url = app.baseUrl + "/admin/doctors?item=" + id;
    const field = () => page.locator("#med-ci0-dias-anticipacion");
    // The center's one line says where its number comes from, and swaps as the
    // field is typed into or emptied.
    const origin = () => page.locator('.cnt-centro-block[data-ci="0"] .hor-limits--center [data-limit-origin]');
    async function open() {
      await page.goto(url, { waitUntil: "load" });
      await page.locator('[data-tab="agenda"]').click();
      await expect(field()).toBeVisible();
    }
    async function save() {
      const response = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/content/doctors/" + id && r.request().method() === "PUT");
      await page.locator('button[form="cnt-form"][type="submit"]').click();
      expect((await response).status()).toBe(200);
      await expect(page.locator("#cnt-save-status")).toHaveText(/^Guardado\.?$/);
    }
    await open();
    await expect(field()).toHaveValue("");
    await expect(page.locator('label[for="med-ci0-dias-anticipacion"]')).toHaveText("Días de anticipación en este centro");
    await expect(origin()).toHaveText(/^Heredado( del (médico|servicio .+|sitio))?: \d+\.$/);
    await field().fill("14");
    await expect(origin()).toHaveText(/^Propio de este centro\. Vacío: hereda \d+\.$/);
    await save();
    await open();
    await expect(field()).toHaveValue("14");
    await expect(origin()).toHaveText(/^Propio de este centro\. Vacío: hereda \d+\.$/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await field().scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`doctor-center-window-${label}.png`) });
    const stored = await (await context.request.get(app.baseUrl + "/api/admin/content/doctors")).json();
    const saved = stored.items.find((item) => item.id === id);
    expect(saved.centros.find((c) => c.centro === centerId).dias_anticipacion).toBe(14);
    await field().fill("");
    await save();
    await open();
    await expect(field()).toHaveValue("");
    expect(errors.filter((e) => !e.includes("favicon"))).toEqual([]);
  });
}
