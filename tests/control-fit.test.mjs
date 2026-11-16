import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const directCss = fs.readFileSync(new URL("../static/root/admin-direct.css", import.meta.url), "utf8");
const solicitudesTemplate = fs.readFileSync(new URL("../templates/admin/solicitudes.html", import.meta.url), "utf8");
const admin = fs.readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");
const content = fs.readFileSync(new URL("../static/platform/admin-content.mjs", import.meta.url), "utf8");
const patients = fs.readFileSync(new URL("../static/platform/admin-patients.mjs", import.meta.url), "utf8");
const listing = fs.readFileSync(new URL("../static/platform/listing.mjs", import.meta.url), "utf8");
const fitter = fs.readFileSync(new URL("../static/platform/admin-control-fit.mjs", import.meta.url), "utf8");

test("custom admin chevrons reserve the icon plus an eight pixel text gap", () => {
  assert.match(directCss, /\.adm-sel\{[^}]*padding:7px 36px 7px 12px[^}]*background-position:right 10px center/);
  assert.match(directCss, /\.cmb-in\{[^}]*padding:8px 30px 8px 11px[^}]*background-position:right 9px center/);
});

test("admin list filters take the selected option's width and reflow as a group", () => {
  assert.match(content, /\.cnt-toolbar-filters \.adm-sel\{width:var\(--control-fit-width,auto\);min-width:var\(--control-fit-width,max-content\);flex:0 0 auto;\}/);
  assert.match(content, /@media\(max-width:520px\)\{\.cnt-toolbar-filters\{grid-template-columns:minmax\(0,1fr\);\}/);
  // El buscador de Pacientes NO lleva un ancho fijo inline: admin-content.mjs
  // ya lo hace ocupar toda la fila de la grilla (".cnt-toolbar-filters:has(
  // #pac-q) #pac-q") tanto en escritorio como, con otro grid-template, en
  // celular. Un `style="min-width:40ch"` puesto en el <input> le ganaba
  // siempre a esa regla externa (inline > clase) y, al no encoger nunca,
  // desbordaba la página a 360px (responsable del producto, 2026-08-04).
  assert.doesNotMatch(patients, /id="pac-q"[^>]*style=/);
  assert.match(content, /\.cnt-toolbar-filters:has\(#pac-q\) #pac-q\{grid-column:1\/-1;width:100%;min-width:0;box-sizing:border-box;\}/);
  assert.match(solicitudesTemplate, /type="module" src="\{\{ static\('platform\/admin-control-fit\.mjs'\) \}\}"/);
  assert.match(fitter, /options\[select\.selectedIndex\]/);
  assert.match(fitter, /ctx\.measureText\(\(chosen && chosen\.text\) \|\| ""\)\.width/);
  assert.match(fitter, /paddingRight/);
});

test("public listing filters stay together while their selected labels fit", () => {
  assert.match(listing, /matchMedia\("\(max-width:900px\)"\)\.matches/);
  assert.match(listing, /padding:10px 26px 10px 8px[^}]*background-position:right 7px center/);
  assert.match(listing, /classList\.remove\("lst-selects--stacked"\)/);
  assert.match(listing, /total \+ gaps > avail \+ 1/);
  assert.match(listing, /classList\.add\("lst-selects--stacked"\)/);
  assert.match(listing, /\.lst-selects\{display:flex;flex-wrap:nowrap;flex:1 1 100%;gap:8px;min-width:0;\}/);
  assert.match(listing, /\.lst-selects>\.lst-select\{flex:1 1 auto;\}/);
  assert.match(listing, /\.lst-selects\.lst-selects--stacked\{display:grid;grid-template-columns:minmax\(0,1fr\);\}/);
});

test("long rejection reasons and editable values retain readable width", () => {
  // El select de motivo se dimensiona por su contenido medido, con techo.
  // Antes esto se afirmaba sobre `.act-reject>.motivo-sel`, de la tarjeta
  // vieja: esa clase ya no se renderiza en ningún lado (la pantalla es el
  // panel `.sol-res-row`), así que la guarda mira la regla que SÍ está viva.
  assert.match(directCss, /\.motivo-sel\{max-width:100%;width:var\(--control-fit-width,260px\);\}/);
  assert.match(admin, /Math\.min\(52,Math\.max\(minimo\|\|3,Math\.ceil\(n\+3\.5\)\)\)/);
});
