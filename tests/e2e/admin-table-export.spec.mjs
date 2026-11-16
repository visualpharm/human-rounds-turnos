// tests/e2e/admin-table-export.spec.mjs -- «Descargar» on a small part of the panel, end to end
// on the real «Pacientes de hoy» (/admin/cola).
//
// Product owner, 2026-11-07: «an export for every small part — reports, the patients of the day and
// so on; never the big places». What this spec pins on the live screen, with the real server:
//   E1  the file is exactly what the screen shows: after a search narrows the day to one
//       patient, the CSV carries that one row, under the visible column headings, as a
//       UTF-8 CSV with a BOM and the Spanish separator «;»; the name is the screen + the day;
//   E2  the download of clinical rows is in the access log of the patient it carried (the
//       admin's «Quién vio a este paciente»), and only of that patient;
//   E3  the big lists carry no button: Pacientes has none.
import { readFile } from "node:fs/promises";
import { test, expect, runFixture, signInStaff, LOGIN_PHRASE, ADMIN_USER, ADMIN_PASS } from "./lib/journey.mjs";

const SERVICE_ID = "cardiologia-sampleton-hospital";
const LEVIN = { id: "levin-camila", usuario: "e2e-dr-levin-export", password: LOGIN_PHRASE };
// Two turnos of the same doctor today, at two different times (one slot holds one turno).
const SHOWN = { dni: "30555011", nombre: "Ana", apellido: "Descarga", at: "09:00" };
const HIDDEN = { dni: "30555012", nombre: "Bruno", apellido: "Filtrado", at: "09:20" };
const DESKTOP = { width: 1440, height: 900 };

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_table_export" },
  journey: { staff: [{ usuario: LEVIN.usuario, password: LEVIN.password, rol: "medico", medico_id: LEVIN.id }] },
});

const seededApps = new WeakSet();
async function seedToday(app) {
  if (seededApps.has(app)) return;
  for (const p of [SHOWN, HIDDEN]) {
    await runFixture(app, "seed-visit", { spec: {
      dni: p.dni, nombre: p.nombre, apellido: p.apellido, doctor_id: LEVIN.id, doctor_name: "Camila Levin",
      servicio_id: SERVICE_ID, centro: "sampleton-hospital", when: "today", at: p.at, account: true } });
  }
  seededApps.add(app);
}

async function openQueue(role, who) {
  const r = await role({ viewport: DESKTOP });
  await signInStaff(r.page, who.usuario, who.password);
  await r.page.goto("/admin/cola", { waitUntil: "load" });
  await expect(r.page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12_000 });
  await expect(r.page.locator("#sec-cola")).not.toHaveAttribute("aria-busy", "true");
  await expect(r.page.locator("#cola-live")).toHaveText(/\S/, { timeout: 12_000 });
  return r;
}

/** The visible headings of the day table, in order, as the screen paints them. */
async function visibleHeadings(page) {
  return page.locator("#cola-results").evaluate((root) => {
    const table = [...root.querySelectorAll("table")].find((t) => t.offsetParent && !t.hasAttribute("data-export-skip"));
    return [...table.querySelectorAll("thead th")]
      .filter((th) => th.offsetParent && !th.hasAttribute("data-export-skip"))
      .map((th) => [...th.childNodes]
        .filter((n) => n.nodeType === 3 || (n.nodeType === 1 && n.tagName !== "BUTTON"
          && n.getAttribute("aria-hidden") !== "true" && getComputedStyle(n).display !== "none"))
        .map((n) => n.textContent).join(" ").replace(/\s+/g, " ").trim())
      .filter(Boolean);
  });
}

function parseCsv(text) {
  return text.replace(/\r\n$/, "").split("\r\n").map((line) => line.split(";").map((c) => c.replace(/^"|"$/g, "").replace(/""/g, '"')));
}

test("E1+E2: a searched day downloads its one visible row, and the download is in that patient's access log", async ({ app, role }) => {
  await seedToday(app);
  const { page } = await openQueue(role, LEVIN);
  await expect(page.locator("#cola-results")).toContainText(SHOWN.apellido);
  await expect(page.locator("#cola-results")).toContainText(HIDDEN.apellido);

  await page.fill(".queue-search", SHOWN.apellido);
  await expect(page.locator("#cola-results")).not.toContainText(HIDDEN.apellido);
  await expect(page.locator("#cola-results")).toContainText(SHOWN.apellido);

  const button = page.locator("#sec-cola .tbl-export-btn");
  await expect(button).toBeVisible();
  await expect(button).toHaveAttribute("aria-label", "Descargar");

  const downloading = page.waitForEvent("download");
  await button.click();
  const download = await downloading;
  // The headings of the screen the file was taken from: read after the click, because the
  // day's late loads (calendar, forecast) can still add a column between the search and it.
  const headings = await visibleHeadings(page);
  // A doctor's own day is the hour and the patient (one flat list, 2026-11-08: the doctor column
  // is there only when the list is more than one doctor's). Any further column is the
  // installation's to add («Síntomas» where symptom capture is on), so the file is checked
  // against whatever the screen shows, below.
  expect(headings.slice(0, 2), "the day table shows its headings").toEqual(["Hora", "Paciente"]);
  expect(download.suggestedFilename()).toMatch(/^Pacientes de hoy \d{4}-\d{2}-\d{2}\.csv$/);
  const bytes = await readFile(await download.path());
  expect([...bytes.subarray(0, 3)], "UTF-8 with a BOM, so Excel reads the accents").toEqual([0xef, 0xbb, 0xbf]);
  const rows = parseCsv(bytes.subarray(3).toString("utf8"));
  expect(rows[0], "the visible headings, in the screen's order").toEqual(headings);
  expect(rows.length, "one heading line and the one visible patient").toBe(2);
  expect(rows[1].join(" ")).toContain(SHOWN.apellido);
  // The name and the age read as on the screen: «Name, N años» (2026-11-09).
  expect(rows[1].join(";")).toMatch(new RegExp(`${SHOWN.apellido}, \\d+ años`));
  expect(bytes.toString("utf8")).not.toContain(HIDDEN.apellido);

  // The admin's «Quién vio a este paciente»: the download is there, by the doctor.
  const admin = await role({ viewport: DESKTOP });
  await signInStaff(admin.page, ADMIN_USER, ADMIN_PASS);
  const log = async (dni) => {
    const res = await admin.context.request.get(`/api/admin/patients/${dni}/access-log`);
    expect(res.ok()).toBe(true);
    return (await res.json()).items.filter((i) => i.accion === "descarga");
  };
  const shown = await log(SHOWN.dni);
  expect(shown.map((i) => [i.usuario, i.permitido])).toEqual([[LEVIN.usuario, true]]);
  expect(await log(HIDDEN.dni), "the patient left out of the file is not logged as downloaded").toEqual([]);
});

test("E3: the big list (Pacientes) has no «Descargar»", async ({ app, role }) => {
  await seedToday(app);
  const r = await role({ viewport: DESKTOP });
  await signInStaff(r.page, ADMIN_USER, ADMIN_PASS);
  await r.page.goto("/admin/pacientes", { waitUntil: "load" });
  // The list's search is where «Descargar» would sit beside: measured once the rows are painted.
  await expect(r.page.locator("#pac-q")).toBeVisible({ timeout: 12_000 });
  await expect(r.page.locator("#sec-pacientes")).toContainText(SHOWN.apellido, { timeout: 12_000 });
  await expect(r.page.locator(".tbl-export-btn")).toHaveCount(0);
});
