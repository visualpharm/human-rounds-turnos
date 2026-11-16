// tests/e2e/doctor-schedules.spec.mjs -- the doctor's «Mis agendas» (/admin/my-schedules).
//
// Product owner, 2026-10-27, on the live screen under a doctor: «The Editar horarios button is the
// most important action, yet it is a link → make it the main button. The holiday list is
// crooked: start with the date, don't write "Feriado" on every row… one line of meaning».
//   S1  «Editar horarios» is the screen's one filled button, right under the schedule;
//   S2  the holidays are ONE sentence («En los feriados no se dan turnos…») with the
//       nearest dates, the date first and never «Feriado» on every row; the names open
//       on request;
//   S3  one left edge: the cards, the button and the holiday sentence start where the
//       screen title starts, and nothing overflows, at 390 / 1024 / 1280 / 1440.
import { test, expect, signInStaff, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS } from "./lib/journey.mjs";
import { loginAdmin, saveShot } from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

// Greco keeps her own hours (editable); Levin's come from the services she works in.
const GRECO = { id: "greco-milagros", usuario: "e2e-dr-greco-agendas", password: LOGIN_PHRASE };
const LEVIN = { id: "levin-camila", usuario: "e2e-dr-levin-agendas", password: LOGIN_PHRASE };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_doctor_schedules" },
  journey: { staff: [GRECO, LEVIN].map((d) => ({ usuario: d.usuario, password: d.password, rol: "medico", medico_id: d.id })) },
});
test.describe.configure({ mode: "serial" });

/** ISO date `days` from today in the installation's timezone. */
function artDay(days) {
  const now = new Date(Date.now() + days * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Argentina/Buenos_Aires",
    year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

test("the site has four upcoming holidays (admin, Settings)", async ({ page, app }) => {
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/settings");
  const items = [10, 20, 30, 40].map((d, i) => ({ fecha: artDay(d), nombre: "Feriado de prueba " + (i + 1) }));
  const saved = await page.request.post("/api/admin/settings", { data: { grupo: "feriados", valores: { items } } });
  expect(saved.status()).toBe(200);
});

for (const width of [390, 1024, 1280, 1440]) {
  test(`S1-S3 (${width}): main button, one holiday sentence, one left edge`, async ({ role }, testInfo) => {
    const { page, errors } = await role({ viewport: { width, height: 900 } });
    await signInStaff(page, GRECO.usuario, GRECO.password);
    await page.goto("/admin/my-schedules", { waitUntil: "load" });
    const card = page.locator(".dw-agendas .dw-card").first();
    await expect(card).toBeVisible();
    await saveShot(testInfo, page, testInfo.outputPath(`mis-agendas-${width}.png`), { fullPage: true });

    // S1: the one filled button of the screen, under the schedule cards.
    const main = page.locator("#doctor-workspace .btn-accent");
    await expect(main).toHaveCount(1);
    await expect(main).toHaveText(/^(Editar horarios|Cargar horarios)$/);
    // The panel's one button height (2026-11-08, R4): 36px under a mouse,
    // 44px on touch (tests/admin-button-system.test.mjs), no 40px of its own.
    expect((await stableBox(main, "main button")).height, "the panel's button height").toBeGreaterThanOrEqual(36);
    const lastCard = await stableBox(page.locator(".dw-agendas .dw-card").last(), "last schedule card");
    expect((await stableBox(main, "main button")).y, "the button sits under the schedule").toBeGreaterThan(lastCard.y + lastCard.height - 1);

    // S2: one sentence, the dates first, no «Feriado» on every row.
    const holidays = page.locator("[data-site-holidays]");
    await expect(holidays).toHaveCount(1);
    await expect(holidays.locator("p")).toHaveText(/^En los feriados no se dan turnos\. Los próximos: .+\.$/);
    await expect(holidays).not.toContainText("Feriado:");
    expect((await stableBox(holidays, "holidays")).y, "the holidays come after the button").toBeGreaterThan((await stableBox(main, "main button")).y);
    const list = holidays.locator("li");
    await expect(list.first()).toBeHidden();
    await holidays.locator("summary").click();
    await expect(list).toHaveCount(4);
    await expect(list.first().locator(".dw-hol-date")).toHaveText(/^\d{1,2} de \w+/);
    await expect(list.first()).toContainText("Feriado de prueba 1");

    // S3: one left edge, no overflow.
    const title = await page.locator("#adm-h1").evaluate((el) => {
      const r = document.createRange(); r.selectNodeContents(el); return r.getBoundingClientRect().left;
    });
    const edges = {
      card: (await stableBox(card, "first schedule card")).x,
      button: (await stableBox(main, "main button")).x,
      sentence: await holidays.locator("p").evaluate((el) => {
        const r = document.createRange(); r.selectNodeContents(el); return r.getBoundingClientRect().left;
      }),
    };
    for (const [name, x] of Object.entries(edges)) {
      expect(Math.abs(x - title), `${name} starts where the title starts (${JSON.stringify({ title, ...edges })})`).toBeLessThanOrEqual(1);
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    expect(overflow, "nothing scrolls sideways").toBeLessThanOrEqual(0);
    expect(errors, "no page errors").toEqual([]);
  });
}

test("a schedule owned by the service: no edit button, the note once, inside its card", async ({ role }) => {
  const { page, errors } = await role({ viewport: { width: 390, height: 900 } });
  await signInStaff(page, LEVIN.usuario, LEVIN.password);
  await page.goto("/admin/my-schedules", { waitUntil: "load" });
  await expect(page.locator(".dw-agendas .dw-card").first()).toBeVisible();
  await expect(page.locator("#doctor-workspace .btn-accent")).toHaveCount(0);
  const note = page.getByText("El horario lo define el servicio. Lo cambia el administrador.");
  await expect(note).toHaveCount(1);
  await expect(page.locator(".dw-agendas .dw-card").first()).toContainText("El horario lo define el servicio. Lo cambia el administrador.");
  await expect(page.locator("[data-site-holidays]")).toHaveCount(1);
  expect(errors, "no page errors").toEqual([]);
});
