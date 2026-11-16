// admin-content-direct.test.mjs — Tier 5 direct catalog editors
// (/admin/services, /admin/centers, /admin/doctors) source contracts. Same
// regex-on-source pattern as admin-knowledge-direct.test.mjs: node --test
// over static/platform/admin-content.mjs, templates/admin/base.html (the shared shell's
// stylesheet) and templates/admin/content.html, no browser, no network.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JS = readFileSync(join(ROOT, "static/platform/admin-content.mjs"), "utf8");
const BASE = readFileSync(join(ROOT, "templates", "admin", "base.html"), "utf8");
const TPL = readFileSync(join(ROOT, "templates", "admin", "content.html"), "utf8");
const SCREEN = readFileSync(join(ROOT, "app", "admin_panel", "admin_content_screen.py"), "utf8");
const MANIFEST = JSON.parse(readFileSync(join(ROOT, "app", "modules", "turnos", "module.json"), "utf8"));

function cohort(tpl) {
  return [...tpl.matchAll(/static\('([^']+)'\)/g)].map((m) => m[1]);
}

test("direct item state is the canonical ?item= query with history navigation", () => {
  assert.match(JS, /var DIRECT_PATH = \{ servicios: "\/admin\/services", centros: "\/admin\/centers", medicos: "\/admin\/doctors" \};/);
  assert.match(JS, /var DIRECT_NEW = "new";/);
  assert.match(JS, /function directScreen\(\) \{[\s\S]*?window\.SANA_ADMIN_SCREEN[\s\S]*?return DIRECT_PATH\[id\] \? id : "";/);
  assert.match(JS, /new URLSearchParams\(location\.search\)\.get\("item"\)/);
  assert.match(JS, /var id = raw === DIRECT_NEW \? "nuevo" : raw;/);
  assert.match(JS, /history\.replaceState\(\{\}, "", canonical\)/);
  assert.match(JS, /location\.pathname \+ location\.search !== canonical/);
  assert.match(JS, /history\.pushState\(\{\}, "", listHref\(tipo\)\)/);
  assert.match(JS, /history\.pushState\(\{\}, "", href\)/);
  assert.match(JS, /var needsHoursEditor = window\.SANA_ADMIN_SCREEN === "servicios" \|\| window\.SANA_ADMIN_SCREEN === "medicos"/);
  assert.match(JS, /if \(needsHoursEditor\) deps\.push\(hoursEditorReady\)/);
  assert.match(JS, /if \(needsUsers\) deps\.push\(usersReady\)/);
  assert.match(JS, /function loadUsers\(\) \{[\s\S]*?importStatic\("platform\/admin-users\.mjs"\)[\s\S]*?Users = m;/);
  assert.match(JS, /user_load_failed[\s\S]*?data-med-user-reload[\s\S]*?location\.reload\(\)/);
  assert.match(JS, /if \(directScreen\(\) === "medicos"\) deps\.push\(islandsReady\)/);
  assert.match(JS, /importStatic\("platform\/islands\.mjs"\)/);
  assert.doesNotMatch(JS, /window\.SanaIslands/);
  assert.match(JS, /return Promise\.all\(deps\)\.then\(function \(\) \{\s*if \(!current\(routeVersion\)\) return;\s*localizeDoctorColumns\(\);\s*return route\(routeVersion\);/);
  assert.match(JS, /function localizeDoctorColumns\(\)/);
  assert.match(JS, /listen\(window, "popstate", readyRoute\)/);
  assert.match(JS, /dispose\.ready = directScreen\(\) \? readyRoute\(\)/);
  assert.match(JS, /var parsed = directState\(\);/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
});

test("direct helper URLs are the only catalog state contract", () => {
  assert.match(JS, /var TIPO_URL = \{ servicios: "services", centros: "centers", medicos: "doctors" \};/);
  assert.match(JS, /return DIRECT_PATH\[tipo\] \+ "\?item=" \+ encodeURIComponent\(id === "nuevo" \? DIRECT_NEW : id\);/);
  assert.match(JS, /if \(parsed\.id === "nuevo"\) p = renderForm\(sec, null\);/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
});

test("every in-screen and cross-editor link goes through the one href helper", () => {
  assert.match(JS, /function itemHref\(tipo, id\) \{/);
  assert.match(JS, /function listHref\(tipo\) \{/);
  assert.match(JS, /function goList\(tipo\) \{/);
  assert.match(JS, /href="' \+ esc\(itemHref\(tipo, id\)\) \+ '"/);
  assert.match(JS, /href="' \+ esc\(itemHref\("centros", slug\)\) \+ '"/);
  assert.match(JS, /href="' \+ esc\(itemHref\("medicos", idOf\("medicos", it\)\)\) \+ '"/);
  // «Nuevo …» lives on the title's own line (one screen header, 2026-11-08).
  assert.match(JS, /var createAction = puedeAltaBaja\(tipo\) \? titleAction\(itemHref\(tipo, "nuevo"\), addLbl\) : null;/);
  assert.match(JS, /a\.className = "btn btn-accent adm-title-action";/);
  assert.doesNotMatch(JS, /class="btn btn-ok cnt-add"/);
  assert.match(JS, /class="adm-crumb" href="' \+ esc\(listHref\(tipo\)\) \+ '"/);
  assert.doesNotMatch(JS, /href="#centers\//);
  assert.doesNotMatch(JS, /href="#doctors\//);
  assert.doesNotMatch(JS, /\/nuevo">/);
  assert.equal((JS.match(/goList\(tipo\);/g) || []).length, 3, "create, disable and restore return through goList; edits keep the canonical record visible");
  assert.doesNotMatch(JS, /\.then\(function \(\) \{ location\.hash = /);
});

test("direct shell confirms expiry from each response without an admin-app gate", () => {
  assert.match(JS, /function sessionGate\(response\) \{\s*if \(!response\) return Promise\.resolve\(false\);\s*var version = mountVersion;\s*return sessionReady\.then\(function \(mod\) \{\s*if \(!current\(version\)\) throw cancelled\(\);\s*return mod\.confirmar\(response, undefined, function \(\) \{ return current\(version\); \}\);/);
  assert.match(JS, /function afterSessionGate\(response, continueWithResponse\) \{[\s\S]*?\? sessionGate\(response\) : Promise\.resolve\(false\);[\s\S]*?if \(transitioned\) throw new Error\("redirect"\);/);
  assert.doesNotMatch(JS, /showGate/);
  assert.match(JS, /if \(window\.toast\) \{ window\.toast\(msg\); return; \}/);
  assert.doesNotMatch(JS, /t\.textContent = msg; t\.classList\.add\("on"\);/);
  assert.match(JS, /\.cnt-status--error\{color:var\(--danger,#b3261e\);\}/);
  assert.match(TPL, /id="content-status" class="adm-empty" role="status" aria-live="polite" hidden/);
  assert.doesNotMatch(TPL, /id="toast"/);
  assert.match(TPL, /window\.CURRENT_ROLE = "\{\{ rol \}\}";/);
  const scripts = TPL.slice(TPL.indexOf("{% block scripts %}"));
  assert.doesNotMatch(scripts, /admin-app\.js/);
  assert.doesNotMatch(TPL, /location\.hash\s*=/);
});

test("content.html gives services the shared capacity helper and keeps the doctor payload isolated", () => {
  assert.match(TPL, /extends "admin\/base\.html"/);
  assert.match(TPL, /<div id="sec-\{\{ editor\.id \}\}"><\/div>/);
  assert.match(TPL, /<p class="adm-sub" id="adm-sub" style="display:none;"><\/p>/);
  const scripts = TPL.slice(TPL.indexOf("{% block scripts %}"));
  const all = cohort(scripts);
  const shared = ["platform/admin-control-fit.mjs", "platform/admin-content.mjs"];
  const doctorsOnly = ["islands/MedCal.mjs"];
  assert.deepEqual(all, ["platform/admin-control-fit.mjs", "islands/MedCal.mjs",
    "platform/screen-boot.mjs", "platform/admin-content.mjs"]);
  assertSharedShellContract(assert, TPL, all.filter((asset) => asset !== "platform/screen-boot.mjs"));
  // Account/user, appointment, and the MedCal island stay doctor-gated.
  // Search arrives through create-appointment's native import, not a classic script.
  const doctorBranches = [...scripts.matchAll(/\{% if editor\.doctors %\}([\s\S]*?)\{% endif %\}/g)].map((m) => m[1]);
  const doctorGated = doctorBranches.flatMap(cohort);
  assert.deepEqual(doctorGated, doctorsOnly);
  for (const asset of shared) assert.ok(!doctorGated.includes(asset), asset + " must not be doctor-gated");
  assert.match(JS, /importStatic\("platform\/admin-hours-editor\.mjs"\)/);
  assert.match(JS, /importStatic\("platform\/admin-users\.mjs"\)/);
  assert.match(JS, /importStatic\("platform\/admin-create-appointment\.mjs"\)/);
  assert.match(JS, /if \(needsUsers\) deps\.push\(usersReady\)/);
  assert.doesNotMatch(JS, /SanaUsers|admin-users\.js/);
  assert.match(JS, /function hoursEditor\(\) \{[\s\S]*?HoursEditor/);
  assert.doesNotMatch(JS, /SanaHoursEditor/);
  assert.doesNotMatch(TPL, /admin-hours-editor\.js/);
  assert.match(SCREEN, /"hours_editor": section_id in \{"servicios", "medicos"\}/);
  assert.doesNotMatch(TPL, /admin-combo\.js/);
  assert.match(JS, /importStatic\("platform\/admin-combo\.mjs"\)/);
  assert.match(JS, /importStatic\("platform\/admin-preferences\.mjs"\)/);
  assert.doesNotMatch(JS, /SanaPreferences/);
  assert.match(TPL, /\{% if editor\.doctors %\}\s*<script type="importmap">/);
  assert.match(TPL, /"sana\/calendar-grid": "\{\{ static\('platform\/calendar-grid\.mjs'\) \}\}"/);
  assert.match(TPL, /window\.SANA_ISLAND_URLS=\{"MedCal":"\{\{ static\('islands\/MedCal\.mjs'\) \}\}"\}/);
  assert.doesNotMatch(TPL, /\?v=/);
  assert.doesNotMatch(TPL, /<hr\b|border-top|border-bottom/);
  assert.doesNotMatch(TPL, /admin-router\.js|admin-sections\.js|photo-zoom\.js/);
  assert.match(JS, /function fitCatalogTable\(tipo, sec\)/);
  assert.match(JS, /Table\.crear\(\{/);
  assert.match(JS, /CATALOG_TABLE_FIT\[tipo\]\.programarFit\(\)/);
  assert.match(JS, /function catalogTableColumns\(tipo, sec\)/);
  const rowsStart = JS.indexOf("function renderListaRows(tipo) {");
  const rowsEnd = JS.indexOf("  // =====================================================================\n  // FORMULARIOS", rowsStart);
  assert.ok(rowsStart >= 0 && rowsEnd > rowsStart, "catalog row renderer must exist");
  const body = JS.slice(rowsStart, rowsEnd);
  const fit = body.lastIndexOf("fitCatalogTable(tipo, sec);");
  assert.ok(fit > body.lastIndexOf("tbody.innerHTML = rowsHtml;"), "new rows must refit after replacing table DOM");
  assert.ok(fit > body.lastIndexOf("tbody.innerHTML = '<tr><td colspan='"), "the empty branch must also reschedule the table instance");
});

test("manifest declares the three editors once, on Spanish ids with English direct paths", () => {
  const sections = MANIFEST.admin_sections;
  const ids = sections.map((s) => (typeof s === "string" ? s : s.id));
  for (const alias of ["services", "centers", "doctors"]) assert.ok(!ids.includes(alias), alias + " alias must be gone");
  const byId = Object.fromEntries(sections.filter((s) => typeof s !== "string").map((s) => [s.id, s]));
  assert.equal(byId.servicios.screen.path, "/admin/services");
  assert.equal(byId.centros.screen.path, "/admin/centers");
  assert.equal(byId.medicos.screen.path, "/admin/doctors");
  const routers = new Set(["servicios", "centros", "medicos"].map((id) => byId[id].screen.router_attr));
  assert.equal(routers.size, 3, "three distinct router attributes");
  for (const id of ["servicios", "centros", "medicos"]) {
    assert.equal(byId[id].screen.module, "app.admin_panel.admin_content_screen");
    assert.equal(byId[id].label_key, "admin.shell.nav." + id);
    assert.ok(!("roles" in byId[id]), id + " stays admin-only");
  }
  assert.equal(ids.filter((id) => ["servicios", "centros", "medicos"].includes(id)).length, 3);
});

test("same-screen clicks stay in-page; other-screen links and modified clicks navigate", () => {
  assert.match(JS, /if \(e\.defaultPrevented \|\| e\.button !== 0 \|\| e\.metaKey \|\| e\.ctrlKey \|\| e\.shiftKey \|\| e\.altKey\) return;/);
  assert.match(JS, /if \(!a \|\| a\.target === "_blank"\) return;/);
  assert.match(JS, /if \(href\.indexOf\(ownPath\) !== 0 \|\| \(href\.length > ownPath\.length && href\.charAt\(ownPath\.length\) !== "\?"\)\) return;/);
  assert.match(JS, /e\.preventDefault\(\);\s*if \(href !== location\.pathname \+ location\.search\) history\.pushState/);
});

test("catalog inputs stay at least 16px and narrow tables become business-card rows", () => {
  const CSS = readFileSync(join(ROOT, "static", "root", "admin-direct.css"), "utf8");
  assert.match(CSS, /\.cnt-toolbar-filters \.adm-sel\{[^}]*font-size:16px/);
  assert.match(JS, /\.cnt-toolbar-filters \.adm-sel\{width:var\(--control-fit-width,auto\)/);
  assert.match(JS, /\.cnt-toolbar-filters \.adm-sel\{width:100%;min-width:0;\}/);
  assert.match(JS, /@media\(max-width:1024px\)\{\.cnt-toolbar-filters\{display:grid/);
  assert.match(JS, /\.adm-content-wrap:has\(> #sec-servicios:not\(\[style\*='none'\]\)\)[^}]*\{width:100%;min-width:0;\}/);
  assert.match(JS, /\.cnt-table-wrap\{width:100%;min-width:0;\}/);
  assert.match(JS, /@media\(max-width:1024px\)/);
  assert.match(JS, /#sec-servicios \.cnt-table thead,#sec-centros \.cnt-table thead,#sec-medicos \.cnt-table--medicos thead\{display:none;\}/);
  // The doctors' cards carry hours, next day and occupancy without a label:
  // their three names are said once, on one line over the list (ui-expert 08.11).
  assert.match(JS, /#sec-medicos \.cnt-table--medicos thead\{display:block;width:100%;\}/);
  assert.match(JS, /#sec-medicos \.cnt-table--medicos thead th\{display:none;\}/);
  for (const col of ["horas_semana", "proximo_dia", "ocupacion_14d"]) {
    assert.match(JS, new RegExp(`thead th\\[data-col=${col}\\][^{]*\\{display:block;`));
  }
  // Readable size, the panel's label ink (ui-expert 08.11, product owner 04.11).
  assert.match(JS, /thead th\[data-col=ocupacion_14d\]\{display:block;[^}]*font-size:14px;[^}]*color:var\(--hint-ink/);
  // Those phone rules belong to the doctors' LIST only: the Calendario day
  // table lives in the same section and, caught by them, stretched a 390
  // phone page to 1066 px (pinamar-turnos 04.11). It restacks on its own.
  assert.doesNotMatch(JS, /#sec-medicos \.cnt-table\{display:block/);
  assert.match(JS, /#sec-medicos \.cnt-table-wrap:has\(>\.cnt-table--medicos\)\{overflow:visible;contain:none;\}/);
  assert.match(JS, /"@media\(max-width:1024px\)\{" \+\s+"#med-cal \.medcal-table\{display:block;width:100%!important;min-width:0!important;/);
  assert.match(JS, /#med-cal \.medcal-table td\[data-col=hora\]\{order:1;[^}]*margin-left:-68px;/);
  assert.match(JS, /#med-cal \.medcal-table td\.medcal-vacio\{display:none;\}/);
  // The calendar row reads like «Mis turnos»: «Name, 73» on the phone, no
  // «DNI» caption, no «N años» line, and a cell without data stays blank.
  assert.match(JS, /#med-cal \.medcal-table td\[data-col=edad\]\{display:none;\}/);
  assert.match(JS, /#med-cal \.medcal-table \.medcal-edad-junto\{display:inline;/);
  assert.doesNotMatch(JS, /td\[data-col=dni\]::before/);
  assert.doesNotMatch(JS, /medcal-table td\[data-col=edad\]\[data-unit\]/);
  assert.match(JS, /pac \+= '<span class="medcal-edad-junto" data-export-omit>, ' \+ edad/);
  assert.match(JS, /\(vacia \? "" : html\)/);
  assert.doesNotMatch(JS, /celda\("(?:servicio|centro|dni|edad)", [^)]*"—"/);
  assert.match(JS, /#med-cal \.medcal-table td\[data-col=paciente\],#med-cal \.medcal-table td\[data-col=servicio\]\{white-space:normal;overflow:visible;text-overflow:clip;/);
  // Product owner 2026-09-15: name on its own line, the rest inline without labels,
  // a label only where a bare number would be ambiguous (dates, hours).
  assert.match(JS, /td\[data-col=nombre\]\{display:block;/);
  assert.match(JS, /td\[data-label\]:before\{content:none;\}/);
  assert.match(JS, /td\[data-col=created\]:before[^{]*\{content:attr\(data-label\) \\" \\";/);
  assert.match(JS, /data-label="' \+ esc\(c\.label\) \+ '"/);
  assert.match(JS, /\.cnt-td-acts:empty[^}]*\{display:none;\}/);
  // ONE gutter token (the product owner 05.09, 15.09, 16.10: «one left edge»): the
  // catalogs, Wiki, AI instructions, Settings and every other section with
  // no rows of its own take the phone gutter from ONE shared list, never a
  // literal. The geometry itself is pinned by tests/e2e/admin-left-edge.spec.mjs.
  assert.match(CSS, /:root\{--adm-gutter:16px;\}/);
  assert.match(CSS, /\.adm-content-wrap>:is\(#sec-servicios,#sec-centros,#sec-medicos,#sec-usuarios,#sec-knowledge,#sec-prompts,#sec-settings,\s*#sec-reportes,#sec-legal,#sec-sistema,#sec-preparaciones,#sec-roles,#roles-status,#sec-plan,#sec-mis-turnos\),[^{]*\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);box-sizing:border-box;\}/);
  // The AI notice and status line beside those sections and the queue share
  // the same phone edge (the product owner 2026-10-19: the notice sat at 0px on
  // Médicos, then on Cola, Wiki, Instrucciones de la IA and Datos e idiomas).
  assert.match(CSS, /\.adm-content-wrap:has\(> :is\(#sec-servicios,#sec-centros,#sec-medicos,#sec-usuarios,#sec-cola,#sec-knowledge,#sec-prompts,#sec-settings,#sec-plan\)\)>:not\(\[id\^="sec-"\]\)\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);box-sizing:border-box;\}/);
  // Solicitudes keeps its wrap unpadded at every width: the notice inside the
  // section takes the filter band's gutter (it sat 16px left of it on desktop).
  assert.match(CSS, /#sec-solicitudes>p\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);\}/);
  // A table's first cell has no inset of its own: its text starts on the edge.
  assert.match(CSS, /\.adm-content-wrap>:not\(#sec-cola\) :is\(\.cnt-table,\.sis-tabla\) :is\(th,td\):first-child\{padding-left:0;\}/);
  // The settings sub-nav lines up with the title at both widths.
  assert.match(readFileSync(join(ROOT, "templates", "admin", "settings.html"), "utf8"), /\.set-subnav\{[^}]*padding:0 16px;/);
});

test("the doctor calendar writes the patient's phones like «Mis turnos»: whose each one is", () => {
  // core/patient_contact.py feeds /api/admin/doctors/<id>/appointments too;
  // the calendar row hands the whole turno to the shared renderer, so the
  // account's different number and both labels show up here as well.
  assert.match(JS, /function medCalContacto\(t\) \{[\s\S]*?return Format\.contactoQuienHtml\(t, ""\);/);
  assert.match(JS, /celda\("contacto", medCalContacto\(t\)\)/);
  // The request modal shows its phone as the shared icon, never as text.
  assert.match(JS, /s\.contacto \? ' · <span class="cnt-muted">' \+ Format\.contactoIconoHtml\(s\.contacto\)/);
});
