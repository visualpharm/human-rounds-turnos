// One button system in the panel, lists instead of boxes (critical review
// 2026-11-08, themes 9 and 14; the product owner R4 «same font size and same design»,
// R29 «it shows it in the boxes», R33 «way less bounding boxes»).
//
// Every rule below is pinned by its selector: a conflict resolution that
// drops one (e7ede1da9 dropped two) turns this file red.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL("../" + path, import.meta.url), "utf8");
const css = read("static/root/admin-direct.css");
const dwCss = read("static/platform/doctor-workspace.css");
const dwHtml = read("templates/admin/doctor-workspace.html");
const hours = read("static/platform/admin-hours-editor.mjs");
const settings = read("static/platform/admin-settings.mjs");
const solicitudes = read("static/platform/admin-solicitudes.mjs");
const appearance = read("static/platform/admin-appearance.mjs");

const SECONDARY = ".adm5-main :is(.hor-add:not(.cnt-remove),.set-mini,.dw-add-slot,.dw-copy-btn,.dw-holidays>summary)";

test("the screens' own secondary buttons share the panel's secondary button", () => {
  const at = css.indexOf(SECONDARY + "{");
  assert.ok(at >= 0, "shared secondary rule present");
  const rule = css.slice(at, css.indexOf("}", at));
  for (const decl of ["min-height:var(--adm-btn-h)", "border:1px solid var(--border-strong)", "border-radius:var(--adm-btn-radius)",
    "background:transparent", "font-size:var(--adm-btn-font)", "font-weight:500"]) {
    assert.ok(rule.includes(decl), "secondary rule has " + decl);
  }
  assert.ok(css.includes(SECONDARY + ":hover{background:var(--bg-2);"), "hover fills, rest has no fill");
  assert.ok(css.includes("@media(pointer:coarse){" + SECONDARY + "{min-height:44px;}}"), "44px on touch");
  // «Quitar centro» stays a text control, not a framed button.
  assert.match(css, /\.adm5-main \.hor-add\.cnt-remove\{min-height:44px;padding:0;border:0;background:none;/);
});

test("no screen module keeps a geometry of its own for those buttons", () => {
  assert.doesNotMatch(hours, /\.hor-add\{/);
  assert.doesNotMatch(settings, /\.set-mini\{/);
  assert.doesNotMatch(solicitudes, /\.sol-grp-btn\{min-height:28px/);
  assert.doesNotMatch(solicitudes, /#sec-solicitudes \.adm-filter-toggle\{/);
  assert.doesNotMatch(dwCss, /\.dw-add-slot,\.dw-copy-btn\{/);
  assert.doesNotMatch(dwCss, /\.dw-workspace \.btn\{min-height:40px/);
  assert.match(dwCss, /\.dw-workspace \.btn\{white-space:normal\}/);
});

test("buttons that used to be 40–48px tall take the panel's height", () => {
  assert.match(css, /\.cnt-actions \.btn,\.cnt-actions-danger \.btn\{min-height:var\(--adm-btn-h,36px\);\}/);
  assert.match(css, /\.cnt-foto-actions \.btn\{min-height:var\(--adm-btn-h,36px\);width:max-content;\}/);
  assert.match(css, /\.med-import-file-row \.btn\{min-height:var\(--adm-btn-h,36px\);width:max-content;\}/);
  // The touch rule that lifts them to 44px comes after them in the file, at
  // the same specificity, so it wins on a phone.
  const touch = css.indexOf("@media(pointer:coarse){.adm5-main :is(.btn,.adm-filter-toggle){min-height:44px;}}");
  assert.ok(touch > css.indexOf(".cnt-actions .btn,.cnt-actions-danger .btn{"), "touch rule after Guardar");
  assert.ok(touch > css.indexOf(".cnt-foto-actions .btn{"), "touch rule after the portrait buttons");
  assert.ok(touch > css.indexOf(".med-import-file-row .btn{"), "touch rule after the import picker");
});

test("Appearance: the logo is picked with the panel's button, the dictation block names no service key", () => {
  assert.match(appearance, /<input type="file" id="cfg-mk-file" class="cnt-foto-file"/);
  assert.match(appearance, /<label class="btn btn-quiet" for="cfg-mk-file" id="cfg-mk-pick">/);
  assert.doesNotMatch(appearance, /esc\(voiceModel\)|cfg-model--locked/);
  assert.doesNotMatch(appearance, /TURNOS_/, "no environment variable names on the screen");
});

test("report numbers are a row of figures, not a box per figure", () => {
  assert.match(css, /\.rep-kpi\{padding:0;border:0;border-radius:0;background:none;\}/);
  assert.match(css, /\.rep-centro-card\{min-width:0;border:0;border-radius:0;padding:0;background:none;\}/);
  assert.match(css, /\.rep-staff-card\{padding:0;border-radius:0;background:none;\}/);
  assert.match(css, /\.rep-quote\{[^}]*padding:0;[^}]*background:none;/);
  // The quote text carries its own quotation marks: no second, decorative one.
  assert.doesNotMatch(css, /\.rep-quote::before/);
});

test("Mis agendas: the admin's agendas are one table, the doctor's view keeps its cards", () => {
  assert.match(dwHtml, /\{% set admin_list = not onboarding and rol == 'admin' and data\.doctors\|length > 1 %\}/);
  assert.match(dwHtml, /<table class="dw-list" data-agenda-list>/);
  assert.match(dwHtml, /<a href="\/admin\/doctors\?item=\{\{ row\.doctor_id\|urlencode \}\}">/);
  assert.match(dwCss, /\.dw-list\{width:100%;border-collapse:collapse;/);
  assert.match(dwCss, /\.dw-list tbody tr:first-child,\.dw-list tbody tr\.dw-same\{border-top:0\}/);
});

test("on a phone a list's column chooser opens leftwards from its button, inside the screen", () => {
  // The button stands after the search at the right end of the row
  // (e331c6f5d); opening to the right put the list 92px off a 360px screen.
  assert.ok(css.includes(".cnt-gear-pop[data-tblcol-pop]{left:auto;right:0;max-width:calc(100vw - 32px);}"));
  assert.ok(css.includes(".cnt-toolbar-right .cnt-gear-pop:not([data-tblcol-pop]){left:0;right:auto;}"));
  assert.ok(!css.includes(".cnt-toolbar-right .cnt-gear-pop{left:0;right:auto;}"));
});
