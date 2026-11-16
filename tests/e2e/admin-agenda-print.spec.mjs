// «Imprimir» on «Pacientes de hoy» (2026-11-07): the desk picks a doctor in the day
// queue and prints that doctor's day on the sheet HSI prints («Turnos programados»).
//
// Real ASGI app, real templates and assets, a throwaway database with the synthetic
// queue fixture (`queue: true`: four approved turnos today, one of them on the catalog
// doctor Sergio Ruiz). The print dialog is stubbed (window.print counts its calls) so
// the run never blocks on a native dialog.
//
// What it pins: the icon exists only while the list is one doctor's day, opens the print page in a
// new tab with that doctor and day, the page auto-opens the print dialog, carries the
// header and the nine columns of HSI's sheet, holds only that doctor's patients, and in
// print media shows the sheet alone (no button bar, no panel chrome).
import {
  test, expect, loginAdmin, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_agenda_print", queue: true },
  viewport: { width: 1280, height: 900 },
});

const COLUMNS = ["Hora", "ID Paciente", "Documento", "Nombres", "Apellidos", "Cobertura médica",
  "Nro. de credencial", "Teléfono", "Estado del turno"];

test("Pacientes de hoy: «Imprimir» opens the chosen doctor's day as HSI's A4 sheet", async ({ page, app }) => {
  await page.context().addInitScript(() => {
    window.__printCalls = 0;
    window.print = () => { window.__printCalls += 1; };
  });
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/cola");
  // The day is one list (2026-11-08): every doctor is a link in the doctor
  // column that narrows the list to that doctor's day.
  const doctor = page.locator("#sec-cola button.queue-doctor-link", { hasText: "Sergio Ruiz" }).first();
  await expect(doctor).toBeVisible();
  // All doctors at once: nothing to print.
  await expect(page.locator("[data-cola-imprimir]")).toHaveCount(0);

  // The rows without a doctor (laboratory) have nothing to print: no doctor, no sheet.
  const unassigned = page.locator("#sec-cola button.queue-doctor-link", { hasText: "Sin médico asignado" }).first();
  if (await unassigned.count()) {
    await unassigned.click();
    await expect(page.locator(".queue-doctor-head h2")).toHaveText("Sin médico asignado");
    await expect(page.locator("[data-cola-imprimir]")).toHaveCount(0);
    await page.locator(".queue-doctor-all").click();
  }

  await doctor.click();
  await expect(page.locator(".queue-doctor-head h2")).toHaveText("Sergio Ruiz");
  const button = page.locator("#sec-cola [data-cola-imprimir]");
  await expect(button).toHaveAttribute("aria-label", "Imprimir");
  const href = await button.getAttribute("href");
  const url = new URL(href, app.baseUrl);
  expect(url.pathname).toBe("/admin/cola/print");
  expect(url.searchParams.get("medico")).toBe("ruiz-sergio");
  expect(url.searchParams.get("dia")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  expect(url.searchParams.get("auto")).toBe("1");

  const [sheet] = await Promise.all([page.context().waitForEvent("page"), button.click()]);
  await sheet.waitForLoadState("load");
  await expect(sheet.locator("h1")).toHaveText("Turnos programados");
  await expect.poll(() => sheet.evaluate(() => window.__printCalls)).toBe(1);

  const head = sheet.locator("dl.head");
  await expect(head.locator("dt")).toHaveText(["Nombre del profesional", "Fecha consultada", "Fecha y horario de impresión"]);
  await expect(head.locator("dd").nth(0)).toHaveText("Sergio Ruiz");
  await expect(head.locator("dd").nth(1)).toHaveText(/\d{4}$/);
  await expect(sheet.locator("h2").first()).toHaveText(/^Turnos (programados \(\d\d:\d\d a \d\d:\d\d\)|fuera de la agenda)$/);
  await expect(sheet.locator("thead").first().locator("th")).toHaveText(COLUMNS);

  // Only this doctor's patient: the three laboratory turnos stay off the sheet.
  const rows = sheet.locator("tbody tr");
  await expect(rows).toHaveCount(1);
  const cells = rows.first().locator("td");
  await expect(cells).toHaveCount(9);
  await expect(cells.nth(0)).toHaveText("12:00");
  await expect(cells.nth(2)).toHaveText("DNI 30111303");
  await expect(cells.nth(8)).toHaveText("Asignado");
  // Unknown fields are empty cells, never a dash or a zero.
  for (const i of [5, 6]) await expect(cells.nth(i)).toHaveText("");
  await expect(sheet.locator("tbody")).not.toContainText("—");

  // On paper: the sheet alone.
  await expect(sheet.locator(".print-bar")).toBeVisible();
  await sheet.emulateMedia({ media: "print" });
  await expect(sheet.locator(".print-bar")).toBeHidden();
  await expect(sheet.locator("main.sheet")).toBeVisible();
  await expect(sheet.locator("nav, .adm5-side, #adm-nav")).toHaveCount(0);
});
