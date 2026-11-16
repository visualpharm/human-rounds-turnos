// Product owner, 2026-10-29 (historial audit): in «Pacientes» the «Asistencia» cell «Falta a veces»
// was cut at 1280 and, at 1100, «Asistencia» and «Estado» came out 0 px wide. The shared
// column engine (platform/admin-table.mjs) is the owner; this file measures every visible
// column of the panel's tables at 1100 / 1280 / 1440 with ALL columns switched on, so a
// column that collapses or a cell whose text is cut is caught the way R9b catches a
// clipped heading.
import { test, expect, loginAdmin, saveShot } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_admin_table_columns_fit" }, viewport: { width: 1280, height: 900 } });

const ALL_PATIENT_COLUMNS = ["nombre", "dni", "edad", "nacimiento", "sexo", "contacto", "email", "servicio",
  "localidad", "ranking", "turnos", "ultimo", "creado", "estado"];
const BANDS = [
  { banda: "viene-siempre", etiqueta: "Viene siempre", nivel: "good", puntaje: 92, hechos: 12, ultimo: "2026-10-20", motivos: ["Asistió a 11 de 12 turnos"] },
  { banda: "falta-a-veces", etiqueta: "Falta a veces", nivel: "warn", puntaje: 58, hechos: 4, ultimo: "2026-09-02", motivos: ["1 ausencia"] },
  { banda: "falta-seguido", etiqueta: "Falta seguido", nivel: "bad", puntaje: 21, hechos: 5, ultimo: "2026-08-14", motivos: ["3 ausencias"] },
  { banda: "sin-historial", etiqueta: "Sin historial", nivel: "none", puntaje: 70, hechos: 0, sin_historial: true, motivos: [] },
];
// «Servicio» principal (responsable del producto, 2026-11-04): un nombre largo, uno corto y dos celdas vacías.
const SERVICIOS = ["Cardiología", "Consulta de clínica médica y controles de salud", "", ""];
const PEOPLE = [
  ["30111222", "Varela", "Alejo", "1978-10-28", "M", "2254445566", "alejo.varela@example.test", "PAMI", "Pinamar"],
  ["28777666", "López de la Fuente", "María Josefina", "1985-03-12", "F", "2254555444", "maria.josefina.lopez.delafuente@example.test", "OSDE 210", "Ostende"],
  ["41222333", "Gómez", "Tomás", "2012-06-01", "M", "2254111222", "", "", "Valeria del Mar"],
  ["19888777", "Fernández Echeverría", "Ana", "1950-01-30", "F", "2254999000", "ana@example.test", "Sin cobertura declarada", "Cariló"],
].map(([dni, apellido, nombre, nacimiento, sexo, contacto, email, cob, localidad], i) => ({
  dni, apellido, nombre, nacimiento, sexo, contacto, email, localidad, activo: i !== 2, created: "2026-07-0" + (i + 1),
  cobertura_nombre: cob, servicio: SERVICIOS[i], ranking: BANDS[i],
}));

const WIDTHS = [1100, 1280, 1440];
// Free text may end in an ellipsis (the cell carries the full value in its globe); a
// date, a phone, a number or a state may not.
const FREE_TEXT = /^(nombre|email|cobertura|servicio|localidad|address|centro|modo|horario|especialidad|preparaci|en una l|se reconoce)/i;
// The engine fits columns on a rAF after the data lands; two frames are its settle point.
const frames = (page) => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

/** Per visible column of the table: width, and every cell whose text is wider than its box. */
const measure = (page, selector) => page.evaluate((sel) => {
  const table = document.querySelector(sel);
  const ths = [...table.tHead.rows[0].cells].filter((th) => th.offsetParent);
  const wrap = table.closest(".cnt-table-wrap") || table.parentElement;
  const out = { columns: [], clipped: [], overflowX: wrap.scrollWidth - wrap.clientWidth, wrapW: wrap.clientWidth, tableW: Math.round(table.getBoundingClientRect().width) };
  for (const th of ths) {
    const i = th.cellIndex;
    out.columns.push({ col: th.dataset.col || th.textContent.trim(), w: Math.round(th.getBoundingClientRect().width) });
    for (const row of table.tBodies[0]?.rows || []) {
      const td = row.cells[i];
      if (!td || !td.offsetParent || !td.textContent.trim()) continue;
      const boxes = [td, ...td.querySelectorAll("*")];
      const cut = boxes.find((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== "inline");
      if (cut) out.clipped.push({ col: th.dataset.col || th.textContent.trim(), text: td.textContent.trim().slice(0, 40), sw: cut.scrollWidth, cw: cut.clientWidth });
    }
    if (th.scrollWidth > th.clientWidth + 1) out.clipped.push({ col: th.dataset.col || th.textContent.trim(), text: "[heading] " + th.textContent.trim(), sw: th.scrollWidth, cw: th.clientWidth });
  }
  return out;
}, selector);

const SCREENS = ["solicitudes", "pacientes", "preparations", "services", "centers", "doctors", "roles", "hsi", "knowledge", "prompts", "users", "reportes"];
const sweep = (page) => page.evaluate(() => {
  const out = [];
  for (const table of document.querySelectorAll("table")) {
    if (!table.offsetParent || !table.tHead || !table.tBodies[0]?.rows.length) continue;
    const ths = [...table.tHead.rows[0].cells].filter((th) => th.offsetParent);
    const cols = ths.map((th) => ({ col: (th.dataset.col || th.textContent).trim().slice(0, 24), w: Math.round(th.getBoundingClientRect().width) }));
    const clipped = [];
    for (const th of ths) {
      for (const row of table.tBodies[0].rows) {
        const td = row.cells[th.cellIndex];
        if (!td || !td.offsetParent || !td.textContent.trim()) continue;
        const cut = [td, ...td.querySelectorAll("*")].find((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== "inline");
        if (cut) clipped.push((th.dataset.col || th.textContent.trim()).slice(0, 24) + "=" + td.textContent.trim().slice(0, 30));
      }
    }
    out.push({ id: table.id || table.className, rows: table.tBodies[0].rows.length, cols, clipped: [...new Set(clipped)] });
  }
  return out;
});
for (const width of WIDTHS) {
  test(`sweep ${width}px`, async ({ page, app }) => {
    test.setTimeout(120_000);
    await loginAdmin(page, app.baseUrl);
    await page.setViewportSize({ width, height: 900 });
    for (const screen of SCREENS) {
      await page.goto(`/admin/${screen}`, { waitUntil: "load" });
      // Rows arrive from the API and the engine fits after them: wait until two
      // measurements two frames apart agree, never a fixed pause.
      let last = "";
      await expect.poll(async () => { const now = JSON.stringify(await sweep(page)); await frames(page); const same = now === last; last = now; return same; }, { timeout: 15_000 }).toBe(true);
      for (const table of await sweep(page)) {
        const where = `${screen} (${table.id}) at ${width}px, columns ${table.cols.map((c) => c.col + ":" + c.w).join(" ")}`;
        expect.soft(table.cols.filter((c) => c.w < 40), `a column collapsed in ${where}`).toEqual([]);
        expect.soft(table.clipped.filter((c) => !FREE_TEXT.test(c)), `fixed text cut in ${where}`).toEqual([]);
      }
    }
  });
}

test.describe("tables of the panel keep every column open and its text whole", () => {
  for (const width of WIDTHS) {
    test(`Pacientes, all columns, ${width}px`, async ({ page, app }, testInfo) => {
      await loginAdmin(page, app.baseUrl);
      for (const [key, value] of [["table.columns.patients", ALL_PATIENT_COLUMNS], ["table.widths.st_adm_anchos-pacientes", {}]]) {
        expect((await page.request.put("/api/admin/preferences", { data: { key, value } })).status()).toBe(200);
      }
      const reply = (route) => route.fulfill({ json: { items: PEOPLE, total: PEOPLE.length, offset: 0, limit: 200, modo: "recientes", medicos: [] } });
      await page.route("**/api/admin/patients?**", reply);
      await page.route("**/api/admin/patients", reply);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/admin/pacientes", { waitUntil: "load" });
      await expect(page.locator("#pat-table table tbody tr")).toHaveCount(PEOPLE.length, { timeout: 15_000 });
      await page.evaluate(() => document.fonts.ready);
      await saveShot(testInfo, page.locator("#sec-pacientes"), testInfo.outputPath(`pacientes-${width}.png`));
      await frames(page);
      const m = await measure(page, "#pat-table table");
      expect.soft(m.columns.filter((c) => c.w < 40), "a column collapsed").toEqual([]);
      expect.soft(m.clipped.filter((c) => !FREE_TEXT.test(c.col)), "fixed text cut").toEqual([]);
    });
  }
});
