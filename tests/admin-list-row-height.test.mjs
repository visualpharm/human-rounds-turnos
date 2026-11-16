// tests/admin-list-row-height.test.mjs — one height for every list's search
// row (2026-11-06).
//
// The desktop row used to mix a 40px search, 36px filters and buttons and the
// 44px column gear; Pacientes alone had fixed it in its own injected CSS. The
// height now lives ONCE in admin-direct.css for every row that holds a list
// search, and no screen keeps a height of its own.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const read = (rel) => fs.readFileSync(new URL("../" + rel, import.meta.url), "utf8");
const css = read("static/root/admin-direct.css");
const ROWS = ":is(.adm-band,.cnt-toolbar,.queue-controls,.medcal-filters,.kn-toolbar)";
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Every screen file that renders a list search, and the row that holds it.
const SCREENS = [
  ["templates/admin/solicitudes.html", '<div class="adm-band">', "Solicitudes"],
  ["static/platform/admin-queue.mjs", '<div class="queue-controls">', "Cola, Mis turnos"],
  ["static/platform/admin-patients.mjs", '<div class="cnt-toolbar">', "Pacientes"],
  ["static/platform/admin-content.mjs", '<div class="cnt-toolbar">', "Médicos, Servicios, Centros"],
  ["static/platform/admin-content.mjs", '<div class="medcal-filters">', "calendario del médico"],
  ["static/platform/admin-doctor-patients.mjs", '<div class="adm-band">', "Mis pacientes"],
  ["static/platform/admin-users.mjs", '<div class="cnt-toolbar">', "Usuarios"],
  ["app/modules/intake/admin-entrevistas.mjs", '<div class="cnt-toolbar ent-tools">', "Entrevistas"],
  ["static/platform/admin-knowledge.mjs", '<div class="kn-toolbar">', "Wiki"],
];
// A search that is not a list's: the consult form's diagnosis/study picker.
const NOT_A_LIST = new Set(["app/modules/intake/admin-consult.mjs"]);

test("the search row of every list is 44px: search, filters, column gear and buttons", () => {
  assert.match(css, /:root\{--adm-row-h:44px;\}/);
  const fields = new RegExp("\\.adm5-main " + esc(ROWS) + " :is\\(input\\[type=search\\],select\\.adm-sel\\)\\{\\s*"
    + "height:var\\(--adm-row-h\\);min-height:var\\(--adm-row-h\\);box-sizing:border-box;padding-top:0;padding-bottom:0;\\}");
  assert.match(css, fields);
  assert.match(css, new RegExp("\\.adm5-main " + esc(ROWS) + " :is\\(\\.btn,\\.cnt-gear-btn\\)\\{min-height:var\\(--adm-row-h\\);\\}"));
  // One type for the search too: a .cnt-in search (Pacientes, Mis pacientes) is 20px elsewhere.
  assert.match(css, new RegExp("\\.adm5-main " + esc(ROWS) + " input\\[type=search\\]\\{font-size:16px;\\}"));
});

test("the row rule comes after the 36px filter rule it overrides at equal specificity", () => {
  const filters = css.indexOf(".adm5-main :is(.adm-filter-fields,.cnt-toolbar-filters,.queue-controls) select.adm-sel{");
  const row = css.indexOf("--adm-row-h:44px");
  assert.ok(filters !== -1 && row !== -1);
  assert.ok(row > filters, "the 44px row rule must follow the adm5 filter rule");
});

test("every list search sits in a row the shared rule covers", () => {
  for (const [file, marker, name] of SCREENS) {
    assert.ok(read(file).includes(marker), name + ": " + file + " renders its search inside " + marker);
  }
  const listed = new Set(SCREENS.map(([file]) => file));
  const dirs = ["static/platform", "templates/admin", "app/modules/intake", "app/modules/chat", "app/modules/turnos"];
  const found = [];
  for (const dir of dirs) {
    const abs = new URL("../" + dir + "/", import.meta.url);
    if (!fs.existsSync(abs)) continue;
    for (const name of fs.readdirSync(abs)) {
      if (!/^admin-.*\.mjs$/.test(name) && !(dir === "templates/admin" && name.endsWith(".html"))) continue;
      const rel = dir + "/" + name;
      if (/<input[^>]*type=["']?search/.test(read(rel))) found.push(rel);
    }
  }
  for (const rel of found) {
    assert.ok(listed.has(rel) || NOT_A_LIST.has(rel),
      rel + " renders a search: name its row in SCREENS (and in the admin-direct.css row rule)");
  }
});

test("no screen keeps a toolbar height of its own", () => {
  const patients = read("static/platform/admin-patients.mjs");
  assert.doesNotMatch(patients, /#sec-pacientes \.cnt-toolbar[^{]*\{[^}]*height:44px/);
  assert.doesNotMatch(patients, /#sec-pacientes \.cnt-toolbar \.btn\{min-height/);
  const wiki = read("static/platform/admin-knowledge.mjs");
  assert.doesNotMatch(wiki, /\.kn-toolbar \.btn\{min-height/);
  assert.doesNotMatch(wiki, /\.kn-search\{[^}]*min-height/);
});
