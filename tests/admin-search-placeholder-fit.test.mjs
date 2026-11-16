// tests/admin-search-placeholder-fit.test.mjs — every list search of the panel
// shows its whole placeholder on a 320px phone (2026-11-09).
//
// On 320 the catalog search said «Buscar serv» and the wiki, Mis pacientes and
// the doctor's calendar cut theirs too. The placeholder now says, in a few
// words, what to type («DNI o nombre», «Paciente o DNI») or just «Buscar» where
// the row is narrow, and an empty search no longer keeps Chrome's clear-button
// place (admin-direct.css). ROOM is the text room of each search at 320 with a
// touch screen, measured on the live demo (box width minus its padding); the
// estimate below stays within 4% of a browser's canvas measurement on the panel's 16px
// type, and the check keeps 5% on top. Pacientes de hoy (/admin/cola) is held by
// tests/e2e/doctor-queue-header.spec.mjs, not here.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const read = (rel) => fs.readFileSync(new URL("../" + rel, import.meta.url), "utf8");
const catalog = (lang, file) => JSON.parse(read("locales/" + lang + "/" + file));

// [screen, key, catalog file, source that renders it, text room at 320 px]
const SEARCHES = [
  ["Solicitudes", "admin.queue.filters.buscar_placeholder", "admin.queue.json", "templates/admin/solicitudes.html", 192],
  ["Solicitudes del médico", "admin.queue.filters.buscar_placeholder_medico", "admin.queue.json", "templates/admin/solicitudes.html", 192],
  ["Pacientes", "admin.queue.filters.buscar_placeholder", "admin.queue.json", "static/platform/admin-patients.mjs", 192],
  ["Médicos, Servicios, Centros", "admin.content.catalog.list.search_placeholder", "admin.content.json", "static/platform/admin-content.mjs", 100],
  ["calendario del médico", "admin.content.catalog.doctors.calendar_search_placeholder", "admin.content.json", "static/platform/admin-content.mjs", 180], // 28ch box less its padding: no calendar on the demo is long enough to show it
  ["Mis pacientes", "admin.queue.patients.search_placeholder", "admin.queue.json", "static/platform/admin-doctor-patients.mjs", 142],
  ["Usuarios", "admin.system.users.search_placeholder", "admin.system.json", "static/platform/admin-users.mjs", 225],
  ["Entrevistas", "admin.reports.entrevistas.buscar", "admin.reports.json", "app/modules/intake/admin-entrevistas.mjs", 244],
  ["Wiki", "admin.content.knowledge.buscar_placeholder", "admin.content.json", "static/platform/admin-knowledge.mjs", 149],
];

// Width of a string in the panel's 16px sans, in px.
function width(text) {
  let w = 0;
  for (const ch of text) {
    if (/[　-鿿＀-￯]/.test(ch)) w += 16;
    else if (/[ ,.;:'…-]/.test(ch)) w += 4.5;
    else if (/[iljtfrI]/.test(ch)) w += 5.5;
    else if (/[mwMW]/.test(ch)) w += 12.5;
    else if (ch !== ch.toLowerCase()) w += 11;
    else w += 8.5;
  }
  return w;
}

test("the width estimate agrees with the live demo's canvas measurement", () => {
  // Measured on demo.humanrounds.org at 320 (Chromium, 16px panel type).
  for (const [text, px] of [["DNI, paciente o médico", 168], ["Médico o especialidad", 164], ["Buscar", 52], ["DNI o nombre", 99], ["Buscar servicios…", 131]]) {
    assert.ok(Math.abs(width(text) - px) <= px * 0.04, text + ": estimate " + width(text) + " vs measured " + px);
  }
});

// Every shipped language: German in Solicitudes and Japanese in Usuarios cut
// theirs on 320 while Spanish and English fit (2026-11-09). A generated
// language that does not fit is fixed through the Spanish, never by hand.
const LANGS = fs.readdirSync(new URL("../locales/", import.meta.url), { withFileTypes: true })
  .filter((d) => d.isDirectory()).map((d) => d.name);

test("the fit check covers all ten shipped languages", () => {
  assert.deepEqual([...LANGS].sort(), ["de", "en", "es", "fr", "it", "ja", "pt", "ru", "uk", "zh"]);
});

test("every list search shows its whole placeholder on a 320px phone, in every language", () => {
  for (const [screen, key, file, , room] of SEARCHES) {
    for (const lang of LANGS) {
      const text = catalog(lang, file)[key];
      assert.ok(text, screen + ": " + lang + " catalog has " + key);
      assert.ok(width(text) * 1.05 <= room,
        screen + " (" + lang + "): «" + text + "» needs ~" + Math.round(width(text)) + "px, the box has " + room + "px on 320 — say less");
    }
  }
});

test("the Spanish page shows the catalog's words: the source's fallback is the same text", () => {
  for (const [screen, key, file, src] of SEARCHES) {
    const es = catalog("es", file)[key];
    const code = read(src);
    const k = key.replace(/\./g, "\\.");
    const v = es.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const js = new RegExp('"' + k + '",\\s*null,\\s*"' + v + '"');
    const jinja = new RegExp("t\\('" + k + "', '" + v + "'\\)");
    assert.ok(js.test(code) || jinja.test(code), screen + ": " + src + " renders " + key + " with the fallback «" + es + "»");
  }
});

// Critical review 08.11, A11: Solicitudes said «Paciente o DNI» and Pacientes
// «Paciente o médico» for the same kind of box. One key, one text, the one
// Pacientes de hoy uses: all three find a patient by DNI or name and by the
// doctor (Solicitudes by the assigned doctor, Pacientes through q=, the product owner
// 2026-11-02 — tests/admin-patients-lifecycle.test.mjs).
test("Solicitudes and Pacientes say the same thing in their search", () => {
  const [sol, pac] = ["Solicitudes", "Pacientes"].map((s) => SEARCHES.find((r) => r[0] === s));
  assert.equal(sol[1], pac[1]);
  assert.equal(sol[1], "admin.queue.filters.buscar_placeholder");
  assert.equal(catalog("es", sol[2])[sol[1]], "Paciente o médico");
  // A doctor sees only its own requests, so «o médico» finds nothing there:
  // it reads «Paciente o DNI», as in its Pacientes de hoy.
  assert.match(read(sol[3]), /id="q" class="adm-search" type="search" autocomplete="off"\s+placeholder="\{% if rol == 'medico' %\}\{\{ t\('admin\.queue\.filters\.buscar_placeholder_medico', 'Paciente o DNI'\) \}\}\{% else %\}\{\{ t\('admin\.queue\.filters\.buscar_placeholder', 'Paciente o médico'\) \}\}\{% endif %\}"/);
  assert.match(read(pac[3]), /id="pac-q" type="search" placeholder="' \+ esc\(T\("admin\.queue\.filters\.buscar_placeholder"/);
  assert.match(read("static/platform/admin-queue.mjs"), /"admin\.queue\.filters\.buscar_placeholder",\s+null, propio \? "Paciente o DNI" : "Paciente o médico"/);
});

test("an empty list search gives its placeholder the clear button's place", () => {
  const css = read("static/root/admin-direct.css").replace(/\/\*[\s\S]*?\*\//g, " ");
  assert.ok(css.includes(".adm5-main :is(.adm-band,.cnt-toolbar,.queue-controls,.medcal-filters,.kn-toolbar) input[type=search]:placeholder-shown::-webkit-search-cancel-button{display:none;}"));
});

test("every screen that renders a list search is in SEARCHES", () => {
  const listed = new Set(SEARCHES.map((s) => s[3]));
  // Pacientes de hoy is held by its own e2e; the consult form's pickers are not lists.
  const elsewhere = new Set(["static/platform/admin-queue.mjs", "app/modules/intake/admin-consult.mjs"]);
  for (const dir of ["static/platform", "templates/admin", "app/modules/intake", "app/modules/chat", "app/modules/turnos"]) {
    const abs = new URL("../" + dir + "/", import.meta.url);
    if (!fs.existsSync(abs)) continue;
    for (const name of fs.readdirSync(abs)) {
      if (!/^admin-.*\.mjs$/.test(name) && !(dir === "templates/admin" && name.endsWith(".html"))) continue;
      const rel = dir + "/" + name;
      if (!/<input[^>]*type=["']?search/.test(read(rel))) continue;
      assert.ok(listed.has(rel) || elsewhere.has(rel), rel + " renders a search: add its placeholder key and its room at 320 to SEARCHES");
    }
  }
});
