// tests/listing-groups.test.mjs — capacidad genérica de "fila de grupo" de
// listing.js (responsable del producto, 2026-08-05 — pull del rediseño de «Resultados de
// laboratorio»): un <tr class="lst-grp"><th colspan> intercalado en el
// <tbody> cuando una fila trae `grupo:{id,primary,secondary}`. Genérica y
// opt-in por fila — ninguna tabla existente (médicos, estudios, mis-turnos)
// pasa `grupo`, así que su salida no puede cambiar por esta capacidad.
//
// SanaListing.render() necesita un <document> real para dibujar una tabla
// completa (éste es el motor de tabla del sitio, no un componente aislado),
// así que — mismo patrón que listing-order.test.mjs/listing-i18n.test.mjs —
// se testea la función PURA que arma la fila de grupo (L.grupoRow, expuesta
// igual que L.timePill/L.openText/L.moreLink) más guardas de fuente sobre
// las partes que si dependen del host (data-l por columna, el fix de
// Cargando…, el scoping de CSS).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import * as L from "../static/platform/listing.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = readFileSync(join(ROOT, "static/platform/listing.mjs"), "utf8");

test("grupoRow: fecha en tinta fuerte + laboratorio atenuado, separados por ' · '", () => {
  const html = L.grupoRow({ id: "g0", primary: "26 de julio de 2026", secondary: "Laboratorio del Hospital" }, 4);
  assert.match(html, /^<tr class="lst-grp"><th colspan="4">/);
  assert.match(html, /<span class="lst-grp-d">26 de julio de 2026<\/span>/);
  assert.match(html, /<span class="lst-grp-l"> · Laboratorio del Hospital<\/span>/);
});

test("grupoRow: sin secondary no deja un ' · ' colgando", () => {
  const html = L.grupoRow({ id: "g0", primary: "26 de julio de 2026" }, 4);
  assert.doesNotMatch(html, /lst-grp-l/);
  assert.doesNotMatch(html, / · /);
});

test("grupoRow: escapa HTML del laboratorio y de la fecha", () => {
  const html = L.grupoRow({ id: "g0", primary: "<b>x</b>", secondary: "A & B" }, 3);
  assert.doesNotMatch(html, /<b>x<\/b>/);
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.match(html, /A &amp; B/);
});

test("grupoRow: colspan real de la tabla, no fijo", () => {
  assert.match(L.grupoRow({ primary: "x" }, 6), /colspan="6"/);
});

// ---- guardas de fuente sobre el resto de la capacidad (necesitan el host
// real de tableHtml()/initMount(), que sólo corre con un <document>) --------

test("initMount limpia el host ANTES de dibujar (fix del bug de Cargando…)", () => {
  const m = /function initMount\(mount, payload\) \{[\s\S]*?\n  \}/.exec(SRC);
  assert.ok(m, "initMount debe existir");
  assert.match(m[0], /mount\.innerHTML = "";/,
    "sin esto, un host con contenido previo (p.ej. lab-results.js dejando " +
    '"Cargando…") lo conserva debajo de la tabla nueva para siempre');
});

test("sólo la tabla que agrupa (rows con .grupo) dibuja data-l — el resto no cambia", () => {
  assert.match(SRC, /var hasGrupos = rows\.some\(function \(r\) \{ return r\.grupo; \}\);/);
  // la PRIMERA columna nunca lleva data-l (es la identidad de la fila, el
  // mockup la agranda en vez de etiquetarla) — ci > 0, no ci >= 0.
  assert.match(SRC, /hasGrupos && ci > 0/);
});

test("outKey pinta un valor de alerta sin acoplar el motor a vocabulario de una pantalla", () => {
  assert.match(SRC, /col\.outKey && r\[col\.outKey\]/);
  assert.doesNotMatch(SRC, /fuera_de_rango/, "listing.mjs no debe conocer el nombre del campo — eso lo decide el caller vía outKey");
});

test("el CSS de grupo/estado vive bajo :has(tr.lst-grp) — no toca una tabla que nunca agrupa", () => {
  assert.match(SRC, /\.lst-table:has\(tr\.lst-grp\)/);
  assert.match(SRC, /\.lst-grp th\{/);
  assert.match(SRC, /\.lst-dot--ok/);
  assert.match(SRC, /\.lst-dot--out/);
});
