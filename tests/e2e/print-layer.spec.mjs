// The shared print layer (human-rounds.css @media print + platform/print-head.mjs), the product owner
// 2026-11-07: «make the pages printable». On paper a panel screen loses its menu and its
// controls, a table repeats its head on every sheet and never splits a row, ink is black on
// white, and every sheet's head says which screen, which filter and when it was printed. The
// patient site prints without its navigation bar.
import { test, expect, loginAdmin } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_print_layer", requests: 7 } });

const printHead = (page) => page.evaluate(() =>
  getComputedStyle(document.documentElement).getPropertyValue("--print-head").trim());

test("panel: on paper the menu and the controls go, the table head repeats, the sheet says what and when", async ({ page, app }) => {
  await loginAdmin(page, app.baseUrl);
  await page.goto(app.baseUrl + "/admin/solicitudes");
  await expect(page.locator(".adm5-side"), "the menu is there on screen").toBeVisible();
  const search = page.locator("#q");
  await expect(search).toBeVisible();
  await search.fill("Zz");
  // The head is read on screen (the print layer hides the controls it names).
  await expect.poll(() => printHead(page), { message: "the head names the screen and the search" })
    .toMatch(/^"Solicitudes de turno · «Zz» · Impreso: .+"$/);

  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".adm5-side"), "printed: no menu").toBeHidden();
  await expect(search, "printed: no search box").toBeHidden();
  await expect(page.locator("body"), "printed: white paper").toHaveCSS("background-color", "rgb(255, 255, 255)");
  await expect.poll(() => printHead(page), { message: "the head survives the print media turning on" })
    .toContain("«Zz»");
  await page.emulateMedia({ media: null });

  // The throwaway database has requests but nobody registered: the list gets two people.
  const people = [["30111222", "Varela", "Alejo"], ["28777666", "López", "María"]].map(([dni, apellido, nombre], i) => ({
    dni, apellido, nombre, nacimiento: "1978-10-2" + i, sexo: "F", contacto: "225444556" + i, email: "", localidad: "Pinamar",
    activo: true, created: "2026-07-0" + (i + 1), cobertura_nombre: "PAMI", servicio: "Cardiología",
    ranking: { banda: "sin-historial", etiqueta: "Sin historial", nivel: "none", puntaje: 70, hechos: 0, sin_historial: true, motivos: [] },
  }));
  const reply = (route) => route.fulfill({ json: { items: people, total: people.length, offset: 0, limit: 200, modo: "recientes", medicos: [] } });
  await page.route("**/api/admin/patients?**", reply);
  await page.route("**/api/admin/patients", reply);
  await page.goto(app.baseUrl + "/admin/pacientes");
  const table = page.locator("#pat-table table");
  await expect(table.locator("tbody tr").first(), "the patients list has rows").toBeVisible();
  await expect.poll(() => printHead(page)).toMatch(/^"Pacientes · Impreso: /);
  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".adm5-side")).toBeHidden();
  await expect(table.locator("thead"), "printed: the head repeats on every sheet").toHaveCSS("display", "table-header-group");
  await expect(table.locator("tbody tr").first(), "printed: a row never splits across sheets").toHaveCSS("break-inside", "avoid");
  await expect(table.locator("tbody td").first(), "printed: black ink").toHaveCSS("color", "rgb(0, 0, 0)");
  await expect(table, "printed: the table keeps to the sheet's width").toHaveCSS("table-layout", "auto");
  const pdf = await page.pdf({ format: "A4", preferCSSPageSize: true });
  expect(pdf.subarray(0, 5).toString(), "the screen prints to a PDF").toBe("%PDF-");
  await page.emulateMedia({ media: null });
});

// The product owner's hub, 2026-11-07: on paper «Laura Abdala, 39» came out in three lines and the doctors
// in three, beside a wide «Contacto» and an empty «Servicio más usado». The columns are sized
// for paper (platform/print-head.mjs): a name and each doctor stay on one line, nothing runs
// past the sheet, a column empty in the whole table is not printed.
test("panel: on paper a name is one line, no cell runs past its column, an empty column is not printed", async ({ page, app }) => {
  await loginAdmin(page, app.baseUrl);
  const doctors = ["Agustina Conti", "Julieta Cáceres", "Milagros Greco", "Pablo Sánchez"].map((nombre, n) => ({ nombre, n: 4 - n }));
  const people = [["30003973", "Bustos", "Leandro"], ["30011645", "Domínguez", "Maximiliano"], ["30010412", "Abdala", "Laura"]]
    .map(([dni, apellido, nombre], i) => ({
      dni, apellido, nombre, nacimiento: "1987-10-2" + i, sexo: "F", contacto: "922544100" + i,
      email: nombre.toLowerCase() + "." + apellido.toLowerCase() + "@demo.humanrounds.org", localidad: "Pinamar",
      activo: true, created: "2026-07-0" + (i + 1), cobertura_nombre: "PAMI", servicio: "", medicos: doctors, turnos: 2,
      ranking: { banda: "sin-historial", etiqueta: "Sin historial", nivel: "none", puntaje: 70, hechos: 0, sin_historial: true, motivos: [] },
    }));
  const reply = (route) => route.fulfill({ json: { items: people, total: people.length, offset: 0, limit: 200, modo: "recientes", medicos: [] } });
  await page.route("**/api/admin/patients?**", reply);
  await page.route("**/api/admin/patients", reply);
  await page.goto(app.baseUrl + "/admin/pacientes");
  const table = page.locator("#pat-table table");
  await expect(table.locator(".pat-person").first(), "the patients list has rows").toBeVisible();
  // A column nobody filled, in a table of the same page.
  await page.evaluate(() => document.querySelector(".adm5-main, main, body").insertAdjacentHTML("beforeend",
    '<table id="t-empty"><thead><tr><th>Fecha</th><th>Nota</th></tr></thead>'
    + "<tbody><tr><td>Lun 2 nov, 10:00</td><td>—</td></tr><tr><td>Mar 3 nov, 11:30</td><td></td></tr></tbody></table>"));
  // The screen's own layout stays what it was when the print started (a print does not
  // re-run the screen's scripts): no resize here.
  await page.emulateMedia({ media: "print" });
  await expect(page.locator("#t-empty th").nth(1), "printed: the empty column is not printed").toBeHidden();
  await expect(page.locator("#t-empty th").first()).toBeVisible();
  const lines = (sel) => table.locator(sel).evaluateAll((els) => els.map((el) => el.getClientRects().length));
  await expect.poll(() => lines(".pat-person"), { message: "printed: a person and the age on one line" }).toEqual([1, 1, 1]);
  expect(await lines(".pat-doc-n"), "printed: every doctor's name on one line").toEqual(Array(12).fill(1));
  await expect(table.locator("td[data-col=nombre]").first(), "printed: the name column is as wide as its names")
    .toHaveAttribute("data-print-nowrap", "");
  const cut = await table.evaluate((t) => Array.from(t.querySelectorAll("td"))
    .filter((td) => td.scrollWidth > td.clientWidth + 1).map((td) => td.textContent.trim()));
  expect(cut, "printed: no cell runs past its column").toEqual([]);
  const pdf = await page.pdf({ format: "A4", preferCSSPageSize: true });
  expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
  await page.emulateMedia({ media: null });
});

test("patient site: on paper no navigation bar and no underlined links", async ({ page, app }) => {
  await page.goto(app.baseUrl + "/centers");
  const nav = page.locator("#stznav");
  await expect(nav, "the navigation bar is there on screen").toBeVisible();
  await page.emulateMedia({ media: "print" });
  await expect(nav, "printed: no navigation bar").toBeHidden();
  const links = page.locator("body a:visible");
  await expect.poll(() => links.count(), { message: "the page prints links" }).toBeGreaterThan(0);
  const underlined = await links.evaluateAll((els) =>
    els.filter((a) => getComputedStyle(a).textDecorationLine.includes("underline")).map((a) => a.textContent.trim()));
  expect(underlined, "printed: no underlined link").toEqual([]);
  await page.emulateMedia({ media: null });
});
