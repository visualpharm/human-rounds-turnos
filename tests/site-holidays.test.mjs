// Site holidays (the reference-deployment counterpart, hospital, 2026-10-15): one «Feriados» list in Settings
// closes those dates on every agenda; the hours editors show them read-only
// next to the agenda's own closures. Backend pins: test_site_holidays.py.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import * as HoursEditor from "../static/platform/admin-hours-editor.mjs";

const settings = fs.readFileSync(new URL("../static/platform/admin-settings.mjs", import.meta.url), "utf8");
const content = fs.readFileSync(new URL("../static/platform/admin-content.mjs", import.meta.url), "utf8");
const workspace = fs.readFileSync(new URL("../templates/admin/doctor-workspace.html", import.meta.url), "utf8");
const locale = (lang, ns) => JSON.parse(fs.readFileSync(new URL(`../locales/${lang}/${ns}.json`, import.meta.url), "utf8"));

test("hours editor renders site holidays once, read-only, with the server's friendly date", () => {
  const feriados = [{ fecha: "2026-12-08", nombre: "Inmaculada Concepción de María", dia: "Martes 8 de diciembre" },
    { fecha: "2026-12-25", nombre: "Navidad", dia: "Viernes 25 de diciembre" }];
  const html = HoursEditor.editorHtml([
    { centro: "a", franjas: [{ dias: [2], desde: "08:00", hasta: "09:00" }], excepciones: [] },
    { centro: "b", franjas: [{ dias: [5], desde: "08:00", hasta: "09:00" }], excepciones: [] },
  ], { feriados, feriadosHref: "/admin/settings" });
  // Once for the whole editor, not once per center.
  assert.equal((html.match(/data-site-holidays/g) || []).length, 1);
  // Date first, then the name — never «Feriado:» on every row (the product owner, 2026-10-27).
  assert.match(html, /Viernes 25 de diciembre<\/span><span>Navidad<\/span>/);
  assert.doesNotMatch(html, /Feriado:/);
  assert.match(html, /<a href="\/admin\/settings">Feriados sin turnos<\/a>/);
  // Read-only: no inputs, no remove control inside the holiday block.
  const block = html.slice(html.indexOf("data-site-holidays"));
  const end = block.indexOf("</ul>");
  assert.doesNotMatch(block.slice(0, end), /<input|data-remove/);
});

test("no holidays, no block; a doctor session gets no settings link", () => {
  assert.equal(HoursEditor.holidayRowsHtml([], "/admin/settings"), "");
  const html = HoursEditor.holidayRowsHtml([{ fecha: "2026-12-25", nombre: "Navidad" }], "");
  assert.doesNotMatch(html, /<a /);
  assert.match(html, /Feriados sin turnos/);
});

test("holiday names are escaped", () => {
  const html = HoursEditor.holidayRowsHtml([{ fecha: "2026-12-25", nombre: "<b>x</b>" }]);
  assert.doesNotMatch(html, /<b>x<\/b>/);
});

test("settings general view carries the Feriados card next to Turnos", () => {
  assert.match(settings, /renderTurnos\(\) \+ renderFeriados\(\) \+ renderSeguridad\(\)/);
  assert.match(settings, /data-grupo="feriados"/);
  assert.match(settings, /data-feriados-nacionales/);
  assert.match(settings, /grupo: "feriados", valores: valores/);
  // The national list loads with what was typed, so no edit is lost.
  assert.match(settings, /if \(nacionales\) valores\.nacionales = true;/);
  // Appointments already given are shown per date, never cancelled.
  assert.match(settings, /admin\.settings\.holidays\.appointments/);
  assert.match(settings, /feriadoTurnosHtml\(item\.turnos\)/);
});

test("catalog editors and the doctor workspace show the site holidays", () => {
  assert.match(content, /FERIADOS_SITIO = j\.feriados_sitio/);
  assert.match(content, /feriados: FERIADOS_SITIO/);
  assert.match(content, /feriadosHtml\(\) \+\s*'<\/div>'/);
  assert.match(workspace, /data\.holidays/);
});

test("es and en hold every new string", () => {
  const keys = {
    "admin.settings": ["title", "lede", "empty", "date", "name", "remove", "add", "load_national",
      "appointments_one", "appointments", "invalid", "bad_date", "no_name", "long_name", "too_many"]
      .map((k) => "admin.settings.holidays." + k),
    "admin.content": ["admin.content.hours.holidays.label"],
    "admin.doctor": ["admin.doctor.holidays.line", "admin.doctor.holidays.one", "admin.doctor.holidays.more"],
    "date": ["date.day_month_year"],
  };
  for (const lang of ["es", "en"]) {
    for (const [ns, list] of Object.entries(keys)) {
      const cat = locale(lang, ns);
      for (const k of list) assert.ok(cat[k], `${lang}/${ns}: ${k}`);
    }
  }
});
