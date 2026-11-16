// Product owner, 2026-11-07: «in the panel I saw tables where names are badly cut». At 1440
// «Cómo prepararse» cut every long name («Maximiliano Domínguez Etcheg…») while the
// column «En una línea», empty in every row, kept 220 px; and a name that does not
// fit carried no full text anywhere. The shared engine (platform/admin-table.mjs)
// now gives an all-empty column its heading's width, gives spare pixels to the
// column whose text they uncover, and puts the full text in `title` of whatever
// still cuts. This file holds those three on a real table of the panel.
import { test, expect, loginAdmin, saveShot } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_admin_table_names_whole" }, viewport: { width: 1440, height: 900 } });

const PREP_API = "/api/admin/content/preparations";
const NAMES = [
  "Ecografía abdominal completa con vejiga llena y preparación intestinal",
  "Videocolonoscopía con sedación y dieta sin residuos de tres días",
  "Laboratorio",
  "Mamografía bilateral",
  "Glucemia",
  "Holter de 24 horas",
];
// Every row carries a long word list, as on the demo: the column beside the
// names then asks for far more than the row has, and the names still win.
const WORDS = ["ecografia transvaginal con vejiga llena", "monitoreo ambulatorio de presion arterial", "videoendoscopia digestiva alta", "doppler de miembros inferiores", "electroencefalograma con privacion de sueno"];
const ITEMS = NAMES.map((nombre, i) => ({
  id: "prep-" + i, nombre, resumen: "", match: WORDS.map((w) => w + " " + i),
  ayuno: { required: i < 2, horas: 8, agua_permitida: true, nota: "" },
  antes: [], traer: [], avisar: [], no_hace_falta: [], despues: [], fuentes: [], sin_preparacion: false,
}));
// The engine fits on a rAF after the rows land: two frames are its settle point.
const frames = (page) => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

const measure = (page) => page.evaluate(() => {
  const table = document.querySelector("#sec-preparaciones table");
  const ths = [...table.tHead.rows[0].cells];
  const cols = ths.map((th) => ({ label: th.textContent.trim(), w: Math.round(th.getBoundingClientRect().width), shown: !!th.offsetParent }));
  const names = [...table.tBodies[0].rows].map((row) => {
    const td = row.cells[0];
    const cut = [td, ...td.querySelectorAll("*")].find((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== "inline");
    const titled = [td, ...td.querySelectorAll("[title]")].map((el) => el.getAttribute("title") || "").find(Boolean) || "";
    return { text: td.textContent.trim(), cut: !!cut, title: titled, ellipsis: !!cut && getComputedStyle(cut).textOverflow === "ellipsis" };
  });
  const wrap = table.closest(".cnt-table-wrap") || table.parentElement;
  return { cols, names, room: Math.round(wrap.clientWidth - table.getBoundingClientRect().width), overflowX: wrap.scrollWidth - wrap.clientWidth, wrapW: Math.round(wrap.clientWidth) };
});

async function open(page, app, width) {
  await loginAdmin(page, app.baseUrl);
  // a stored manual width would decide the column by itself
  expect((await page.request.put("/api/admin/preferences", { data: { key: "table.widths.st_adm_anchos-preparaciones", value: {} } })).status()).toBe(200);
  await page.route((url) => url.pathname === PREP_API, (route) => route.fulfill({ json: { items: ITEMS } }));
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/admin/preparations", { waitUntil: "load" });
  await expect(page.locator("#sec-preparaciones table tbody tr")).toHaveCount(ITEMS.length, { timeout: 15_000 });
  await page.evaluate(() => document.fonts.ready);
  let last = "";
  await expect.poll(async () => { const now = JSON.stringify(await measure(page)); await frames(page); const same = now === last; last = now; return same; }, { timeout: 15_000 }).toBe(true);
  return measure(page);
}

test("1440: a name is never cut while an empty column holds the room", async ({ page, app }, testInfo) => {
  const m = await open(page, app, 1440);
  await saveShot(testInfo, page.locator("#sec-preparaciones"), testInfo.outputPath("preparations-1440.png"));
  const empty = m.cols.find((c) => /En una l/i.test(c.label));
  // empty in every row: as wide as its heading, not its 220 px floor
  if (empty && empty.shown) expect(empty.w, JSON.stringify(m.cols)).toBeLessThan(160);
  expect(m.names.filter((n) => n.cut), JSON.stringify(m)).toEqual([]);
  expect(m.overflowX).toBeLessThanOrEqual(1);
  // the list takes the page's width like the catalog lists, not the 1040 px page
  expect(m.wrapW, JSON.stringify(m)).toBeGreaterThan(1100);
});

test("390: a column empty in every row falls before a column with data", async ({ page, app }, testInfo) => {
  const m = await open(page, app, 390);
  await saveShot(testInfo, page.locator("#sec-preparaciones"), testInfo.outputPath("preparations-390.png"));
  const empty = m.cols.find((c) => /En una l/i.test(c.label));
  const hiddenWithData = m.cols.filter((c) => !c.shown && !/En una l/i.test(c.label));
  if (hiddenWithData.length) expect(empty.shown, JSON.stringify(m.cols)).toBe(false);
  for (const n of m.names.filter((x) => x.cut)) expect(n.ellipsis, JSON.stringify(n)).toBe(true);
  expect(m.overflowX).toBeLessThanOrEqual(1);
});

test("768: a name that still does not fit ends in an ellipsis and carries the full text", async ({ page, app }, testInfo) => {
  const m = await open(page, app, 768);
  await saveShot(testInfo, page.locator("#sec-preparaciones"), testInfo.outputPath("preparations-768.png"));
  for (const n of m.names.filter((x) => x.cut)) {
    expect(n.ellipsis, JSON.stringify(n)).toBe(true);
    expect(n.title.replace(/\s+/g, " ")).toContain(n.text.replace(/\s+/g, " ").slice(0, 40));
  }
  // and when it is cut, nothing beside it is left empty: no free room in the row
  if (m.names.some((n) => n.cut)) expect(m.room, JSON.stringify(m)).toBeLessThanOrEqual(2);
});

// Pacientes at 768: «Juan Gonzále» with the «+1» cut away — the ellipsis took the number.
const PEOPLE = [
  ["30111222", "Fernández de la Torre", "María de los Ángeles", [{ nombre: "Maximiliano Domínguez Etchegaray", turnos: 3 }, { nombre: "Ana Pérez", turnos: 1 }]],
  ["28777666", "Gómez", "Tomás", [{ nombre: "Juan González", turnos: 2 }, { nombre: "Guadalupe Rodríguez Iturraspe", turnos: 1 }]],
  ["41222333", "Varela", "Alejo", [{ nombre: "Ana Pérez", turnos: 1 }]],
].map(([dni, apellido, nombre, medicos], i) => ({
  dni, apellido, nombre, nacimiento: "1980-01-0" + (i + 1), sexo: "F", contacto: "225444556" + i, email: "", localidad: "Pinamar",
  activo: true, created: "2026-07-0" + (i + 1), cobertura_nombre: "PAMI", servicio: "Cardiología", medicos,
  ranking: { banda: "sin-historial", etiqueta: "Sin historial", nivel: "none", puntaje: 70, hechos: 0, sin_historial: true, motivos: [] },
}));

for (const width of [768, 1440]) {
  test(`Pacientes ${width}: a cut doctor keeps its «+N» in sight`, async ({ page, app }, testInfo) => {
    await loginAdmin(page, app.baseUrl);
    const reply = (route) => route.fulfill({ json: { items: PEOPLE, total: PEOPLE.length, offset: 0, limit: 200, modo: "recientes", medicos: [] } });
    await page.route("**/api/admin/patients?**", reply);
    await page.route("**/api/admin/patients", reply);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/admin/pacientes", { waitUntil: "load" });
    await expect(page.locator("#pat-table table tbody tr")).toHaveCount(PEOPLE.length, { timeout: 15_000 });
    await page.evaluate(() => document.fonts.ready);
    await frames(page);
    await frames(page);
    await saveShot(testInfo, page.locator("#sec-pacientes"), testInfo.outputPath(`pacientes-${width}.png`));
    const cells = await page.evaluate(() => [...document.querySelectorAll("#pat-table .pat-docs")].filter((el) => el.offsetParent).map((el) => {
      const box = el.getBoundingClientRect();
      const more = el.querySelector(".pat-docs-more");
      const shown = [...el.querySelectorAll(".pat-doc")].filter((d) => !d.hidden).length;
      const total = el.querySelectorAll(".pat-doc").length;
      const mr = more && !more.hidden ? more.getBoundingClientRect() : null;
      return { text: el.textContent, shown, total, more: more && !more.hidden ? more.textContent : "", moreInside: !mr || (mr.right <= box.right + 1 && mr.width > 0) };
    }));
    // a patient name cut by its cell («María de los Ángeles Fernánd» at 768) ends in an
    // ellipsis and carries the whole name
    const names = await page.evaluate(() => [...document.querySelectorAll("#pat-table .pat-nameline")].map((el) => ({
      text: el.textContent.trim(), cut: el.scrollWidth > el.clientWidth + 1 || el.closest("td").scrollWidth > el.closest("td").clientWidth + 1,
      ellipsis: getComputedStyle(el).textOverflow === "ellipsis" && getComputedStyle(el).overflowX === "hidden",
      title: el.getAttribute("title") || el.closest("td").getAttribute("title") || "",
    })));
    for (const n of names.filter((x) => x.cut)) {
      expect(n.ellipsis, JSON.stringify(n)).toBe(true);
      expect(n.title).toContain(n.text.slice(0, 20));
    }
    for (const c of cells) {
      if (c.shown < c.total) {
        expect(c.more, JSON.stringify(c)).toBe("+" + (c.total - c.shown));
        expect(c.moreInside, JSON.stringify(c)).toBe(true);
      }
    }
  });
}
