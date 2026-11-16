// tests/e2e/doctor-second-specialty.spec.mjs -- a doctor's optional second
// specialty (hospital TASK-35): chosen in the doctor editor, saved, shown again
// after a cold reload, linked from the public doctor page, and removed by
// clearing the select. Phone and desktop widths; a screenshot of each width
// lands in the test's output folder.
import { test, expect, loginAdmin } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_doctor_second_specialty" } });

async function makeDoctor(context, baseUrl, id) {
  const centerId = id + "-center";
  const doctors = await (await context.request.get(baseUrl + "/api/admin/content/doctors")).json();
  const template = doctors.items.find((item) => item.id === "gonzalez-juan");
  expect(template).toBeTruthy();
  const center = await context.request.post(baseUrl + "/api/admin/content/centers", {
    data: { slug: centerId, name: "Synthetic Twospec Center" },
  });
  expect(center.status()).toBe(200);
  const doctor = { ...template, id, nombre: "Synthetic Twospec", nombre_pila: "Synthetic", apellido: "Twospec",
    centros: [{ centro: centerId, franjas: [{ dias: [1], desde: "08:00", hasta: "12:00", cada_min: 20 }], excepciones: [] }] };
  delete doctor.especialidad_2;
  delete doctor.cluster_2;
  for (const key of Object.keys(doctor)) if (key.startsWith("_")) delete doctor[key];
  const created = await context.request.post(baseUrl + "/api/admin/content/doctors", { data: doctor });
  expect(created.status(), await created.text()).toBe(200);
  return template.especialidad;
}

for (const [label, viewport] of [["phone", { width: 390, height: 844 }], ["desktop", { width: 1280, height: 900 }]]) {
  test(`the second specialty saves, shows on the public page and clears (${label})`, async ({ app, newContext }, testInfo) => {
    const errors = [];
    const context = await newContext({ viewport });
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(String(e)));
    await loginAdmin(page, app.baseUrl);
    const id = "twospec-" + label + "-" + Date.now();
    const first = await makeDoctor(context, app.baseUrl, id);
    const url = app.baseUrl + "/admin/doctors?item=" + id;
    const select = () => page.locator("#med-especialidad-2");
    async function open() {
      await page.goto(url, { waitUntil: "load" });
      await page.locator('[data-tab="info"]').click();
      await expect(select()).toBeVisible();
    }
    async function save() {
      const response = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/content/doctors/" + id && r.request().method() === "PUT");
      await page.locator('button[form="cnt-form"][type="submit"]').click();
      expect((await response).status()).toBe(200);
      await expect(page.locator("#cnt-save-status")).toHaveText(/^Guardado\.?$/);
    }
    const publicPage = await context.newPage();
    await publicPage.goto(app.baseUrl + "/doctor/" + id, { waitUntil: "load" });
    await expect(publicPage.locator('a[href^="/doctors?specialty="]')).toHaveCount(1);
    await publicPage.screenshot({ path: testInfo.outputPath(`doctor-second-specialty-page-before-${label}.png`) });
    await open();
    await expect(select()).toHaveValue("");
    const options = await select().locator("option").evaluateAll((list) => list.map((o) => o.value).filter(Boolean));
    const second = options.find((value) => value !== first);
    expect(second).toBeTruthy();
    await select().selectOption(second);
    await expect(page.locator("#med-cluster-chip-2")).not.toBeEmpty();
    await save();
    await open();
    await expect(select()).toHaveValue(second);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await select().scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`doctor-second-specialty-editor-${label}.png`) });
    const stored = await (await context.request.get(app.baseUrl + "/api/admin/content/doctors")).json();
    const saved = stored.items.find((item) => item.id === id);
    expect(saved.especialidad_2).toBe(second);
    expect(saved.cluster_2).toBeTruthy();
    await publicPage.goto(app.baseUrl + "/doctor/" + id, { waitUntil: "load" });
    await expect(publicPage.locator('a[href^="/doctors?specialty="]')).toHaveCount(2);
    await publicPage.screenshot({ path: testInfo.outputPath(`doctor-second-specialty-page-${label}.png`) });
    await select().selectOption("");
    await save();
    await open();
    await expect(select()).toHaveValue("");
    const after = await (await context.request.get(app.baseUrl + "/api/admin/content/doctors")).json();
    const cleared = after.items.find((item) => item.id === id);
    expect(cleared.especialidad_2).toBeUndefined();
    expect(cleared.cluster_2).toBeUndefined();
    expect(errors.filter((e) => !e.includes("favicon"))).toEqual([]);
  });
}
