// Solicitudes list, product owner 2026-11-08: an order is an attachment, not a "no
// order" label on every row; no long page by default; approve, reject and
// "keep talking" as the actions; no dividers. Pins each visual rule by its
// selector (a neighbour's conflict
// resolution has silently dropped CSS rules before) and the grouping logic.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(new URL("../packs/ar/country.json", import.meta.url), "utf8")).phone;
const Sol = await import("../static/platform/admin-solicitudes.mjs");
const Format = await import("../static/platform/format.mjs");
const APP = readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");

function rule(selector) {
  const at = Sol.css.indexOf(selector + "{");
  assert.ok(at >= 0, "no CSS rule for " + selector);
  return Sol.css.slice(at, Sol.css.indexOf("}", at) + 1);
}

function fnSource(name) {
  const start = APP.indexOf("function " + name + "(");
  assert.ok(start >= 0, name + " not found in admin-app.js");
  return APP.slice(start, APP.indexOf("\n}\n", start) + 3);
}

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

test("no divider lines between rows, between groups, or between Aprobar and Rechazar", () => {
  assert.doesNotMatch(rule(".sol-row"), /border-bottom|border-top/);
  assert.doesNotMatch(rule(".sol-grp"), /border-bottom|border-top/);
  assert.doesNotMatch(rule(".sol-res-sep"), /border/);
  assert.match(Sol.css, /\.sol-row \+ \.sol-grp,\.sol-grp-more \+ \.sol-grp\{margin-top:12px;\}/);
});

test("the paperclip is the Icons8 attach icon, drawn by a mask in the text colour", () => {
  const clip = rule(".sol-clip");
  assert.match(clip, /mask:url\(\/static\/icons\/chat-attach-sf-regular\.svg\)/);
  assert.match(clip, /background:var\(--text\)/);
  // the grouped queue drops the reading track from every fixed layout
  assert.match(rule(".sol-q-clip"), /--sol-head:44px/);
  assert.doesNotMatch(Sol.css, /grid-template-columns:44px 140px/);
  assert.equal(Sol.css.split("grid-template-columns:var(--sol-head,44px 140px) ").length - 1, 4);
  assert.match(rule(".sol-nm .sol-clip"), /margin-left:8px/);
  // on a phone the date under the name starts where the name starts
  const phone = Sol.css.slice(Sol.css.indexOf("@media (max-width:760px){"));
  assert.match(phone, /\.sol-q-clip \.sol-row>\.sol-cell-cuando\{margin-left:0;\}/);
});

test("under an open request's sheet the page does not scroll", () => {
  const narrow = Sol.css.slice(Sol.css.indexOf("@media (max-width:1200px){"));
  assert.match(narrow, /html:has\(\.sol-det\.card\[data-id\]\),body:has\(\.sol-det\.card\[data-id\]\)\{overflow:hidden;\}/);
});

test("a pending row: paperclip with an order, nothing without one, the reason only when given", () => {
  const cols = Sol.columns().filter((c) => ["veredicto", "paciente", "cuando"].includes(c.key));
  const base = { id: 1, paciente: "Ana Pérez", cuando: "Hoy 10:00", checkbox: true, dot: "" };
  const con = Sol.filaHtml({ ...base, adjunto: true, veredicto: "" }, cols);
  assert.match(con, /class="sol-clip" role="img" aria-label="Con orden adjunta"/);
  assert.doesNotMatch(con, /sol-dot|sol-vd-t/);
  const sin = Sol.filaHtml({ ...base, adjunto: false, veredicto: "" }, cols);
  assert.doesNotMatch(sin, /sol-clip|sol-dot|sol-vd-t|Sin orden/);
  const dudosa = Sol.filaHtml({ ...base, adjunto: true, veredicto: "Dudosa" }, cols);
  assert.match(dudosa, /sol-clip[\s\S]*<span class="sol-vd-t">Dudosa<\/span>/);
  // a resolved row keeps its dot and its state
  const res = Sol.filaHtml({ ...base, adjunto: null, veredicto: "Aprobada", dot: "sol-d-ok", checkbox: false }, cols);
  assert.match(res, /sol-dot sol-d-ok[\s\S]*Aprobada/);
});

test("the grouped pending queue: no reading column, the name by the checkbox, the paperclip after it", () => {
  const cols = Sol.columns().filter((c) => ["veredicto", "paciente"].includes(c.key));
  const filas = [{ id: 1, paciente: "Ana", adjunto: true, veredicto: "", checkbox: true },
    { id: 2, paciente: "Luis", adjunto: false, veredicto: "", checkbox: true }];
  const g = { id: "aprobar", titulo: "Aprobar", total: 2, bulk: null, filas, mas: 0 };
  const on = Sol.queueHtml({ grupos: [g], cols, soloClip: true });
  assert.match(on, /^<div class="sol-queue sol-q-clip">/);
  assert.doesNotMatch(on, /sol-ch-veredicto|sol-cell-veredicto|data-sol-sort="veredicto"/);
  assert.match(on, /style="grid-template-columns:44px minmax\(/);
  assert.match(on, /aria-label="Abrir la solicitud de Ana · Con orden adjunta">Ana<span class="sol-clip" aria-hidden="true"><\/span><\/button>/);
  assert.match(on, /aria-label="Abrir la solicitud de Luis">Luis<\/button>/);
  const off = Sol.queueHtml({ grupos: [g], cols });
  assert.match(off, /^<div class="sol-queue">/);
  assert.match(off, /data-sol-sort="veredicto"/);
  assert.match(off, /sol-cell-veredicto"><span class="sol-clip" role="img"/);
  assert.match(off, />Ana<\/button>/);
});

test("a long group loads the rest inside itself; a reason group's button carries the reason", () => {
  const cols = Sol.columns().filter((c) => c.key === "paciente");
  const html = Sol.grupoHtml({ id: "rechazar:no_es_una_orden", titulo: "No es una orden", total: 30, bulk: "rechazar",
    bulkLabel: "Rechazar todas", motivo: "no_es_una_orden", filas: [{ id: 1, paciente: "A", checkbox: true }], mas: 25, masLote: 20 }, cols);
  assert.match(html, /data-sol-grupo="rechazar" data-sol-motivo="no_es_una_orden"/);
  assert.match(html, /<button type="button" class="btn sol-grp-mas" data-sol-grp-mas="rechazar:no_es_una_orden">Cargar 20 más<\/button>/);
  const corto = Sol.grupoHtml({ id: "aprobar", titulo: "Aprobar", total: 1, bulk: "aprobar", bulkLabel: "Aprobar todas",
    filas: [{ id: 1, paciente: "A", checkbox: true }], mas: 0 }, cols);
  assert.doesNotMatch(corto, /sol-grp-mas|data-sol-motivo/);
});

test("the open request offers Contactar under Aprobar and Rechazar", () => {
  const d = { id: 7, sug: "none", nombre: "Ana", pide: [], aprobarHtml: "<b>A</b>", rechazarHtml: "<b>R</b>",
    contactarHtml: '<a class="btn sol-contactar" href="https://wa.me/5492254000000">Contactar</a>' };
  const html = Sol.detalleHtml(d);
  assert.match(html, /sol-res-aprobar[\s\S]*sol-res-sep[\s\S]*<div class="sol-res-row sol-res-contactar"><a class="btn sol-contactar"/);
  assert.doesNotMatch(Sol.detalleHtml({ ...d, contactarHtml: "" }), /sol-res-contactar/);
});

test("Contactar opens the patient's WhatsApp, mail or phone with the request named", () => {
  const ctx = { T: (k, v, f) => f, esc, Format, svcNombre: () => "Cardiología", encodeURIComponent };
  vm.runInNewContext(fnSource("contactarHtml") + ";this.contactarHtml=contactarHtml;", ctx);
  const wa = ctx.contactarHtml({ nombre: "ANA PÉREZ", contacto: "2254 123456", servicio_id: "x" });
  assert.match(wa, /href="https:\/\/wa\.me\/\d+\?text=Hola%20Ana%2C%20te%20escribimos%20por%20tu%20solicitud%20de%20turno%20de%20Cardiolog%C3%ADa\."/);
  assert.match(wa, /target="_blank" rel="noopener">Contactar<\/a>$/);
  const mail = ctx.contactarHtml({ nombre: "Ana", contacto: "ana@example.com", servicio_id: "x" });
  assert.match(mail, /href="mailto:ana@example\.com\?body=Hola%20Ana/);
  assert.doesNotMatch(mail, /target=/);
  assert.equal(ctx.contactarHtml({ nombre: "Ana", contacto: "" }), "");
});

test("Rechazar todas asks first and quotes the reason the patients will read", () => {
  const at = APP.indexOf("const grupoBulk=e.target.closest('[data-sol-grupo]')");
  assert.ok(at >= 0, "group bulk handler not found");
  const handler = APP.slice(at, APP.indexOf("await runBulkSugerencia();", at));
  assert.match(handler, /new Set\(items\.map\(defaultMotivo\)\)/);
  assert.match(handler, /confirmar_grupo_rechazar_motivo", items\.length, \{motivo: motivos\[0\]\}/);
  assert.match(handler, /if\(!confirm\(pregunta\)\) return;/);
  for (const lang of ["es", "en"]) {
    const cat = JSON.parse(readFileSync(new URL("../locales/" + lang + "/admin.queue.json", import.meta.url), "utf8"));
    assert.match(cat["admin.queue.solicitudes.confirmar_grupo_rechazar_motivo"], /\{n\}[\s\S]*\{motivo\}/);
    assert.match(cat["admin.queue.solicitudes.confirmar_grupo_rechazar_motivo_one"], /\{motivo\}/);
  }
});

test("pending groups: five rows each, the open one always shown, one group per rejection reason", () => {
  const ctx = {
    T: (k, v, f) => f, COLA_SORT: null, COLA_GRUPO_LIM: {}, COLA_GRUPO_BASE: 5, COLA_GRUPO_LOTE: 20, SEL_ID: "a8",
    ordenarFilas: (f) => f.slice(),
    bulkLabelDe: (b) => (b ? b + " todas" : ""),
    COLA_GRUPOS: [{ titulo: "Aprobar según sugerencia" }, { titulo: "Revisar a mano" }]
  };
  vm.runInNewContext(fnSource("gruposPendientes") + ";this.gruposPendientes=gruposPendientes;", ctx);
  const vm1 = (id, sug, clave, lectura) => ({ id, _sug: sug, _clave: clave, _lectura: lectura, veredicto: lectura });
  const vms = [];
  for (let i = 0; i < 9; i++) vms.push(vm1("a" + i, "aprobar", "coincide", "Coincide"));
  vms.push(vm1("n1", "none", "v:dudosa", "Dudosa"));
  vms.push(vm1("r1", "rechazar", "otra_jurisdiccion", "Otra jurisdicción"));
  for (let i = 0; i < 3; i++) vms.push(vm1("e" + i, "rechazar", "no_es_una_orden", "No es una orden"));
  const g = ctx.gruposPendientes(vms);
  // arrays built inside the vm context: compare their content, not their realm
  assert.equal(g.map((x) => x.id).join(), "aprobar,none:v:dudosa,rechazar:no_es_una_orden,rechazar:otra_jurisdiccion");
  assert.equal(g[0].filas.map((r) => r.id).join(), "a0,a1,a2,a3,a4,a8");
  assert.equal(g[0].mas, 3);
  assert.equal(g[0].masLote, 3);
  assert.equal(g[1].titulo, "Revisar a mano · Dudosa");
  assert.equal(g[1].bulk, null);
  assert.equal(g[2].titulo, "No es una orden");
  assert.equal(g[2].motivo, "no_es_una_orden");
  // the paperclip track is too narrow for words: the group names the reason, rows never do
  assert.ok(g.every((x) => x.filas.every((r) => r.veredicto === "")), "the group names the reason; rows do not repeat it");
  ctx.COLA_GRUPO_LIM.aprobar = 25;
  assert.equal(ctx.gruposPendientes(vms)[0].filas.length, 9);
});
