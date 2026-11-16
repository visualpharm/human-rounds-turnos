// Home directory after the demo review of 2026-11-15:
// «Más buscadas» cards all have the same shape (title, one dark line, a band
// of faces or the rubro's icon) — no paragraph card, no «+N» badge; «Más
// especialidades» rows carry no bare number, no rotating background colours,
// and flow in columns so the last row never leaves an empty grid cell.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const styles = new Map();
globalThis.document = {
  getElementById(id) { return styles.get(id) || null; },
  createElement(tag) { return { tagName: tag.toUpperCase(), id: "", textContent: "" }; },
  head: { appendChild(node) { styles.set(node.id, node); } },
};
globalThis.window = {};

const Directory = await import("../static/platform/directory.mjs");
const SRC = readFileSync(new URL("../static/platform/directory.mjs", import.meta.url), "utf8");

const fotos = (n) => Array.from({ length: n }, (_, i) => ({ ph: "background-position:-" + (i * 44) + "px 0" }));

async function renderOf(clusters, compact) {
  globalThis.fetch = async () => ({ async json() { return { clusters }; } });
  const host = { innerHTML: "" };
  await Directory.render(host, { compact: !!compact });
  return host.innerHTML;
}

test("no card carries a +N badge, whatever the number of doctors", async () => {
  for (const [total, portraits] of [[7, 6], [12, 6], [4, 2], [6, 5]]) {
    const html = await renderOf([{ id: "c", nombre: "C", top: 1, n_medicos: total, avatares: fotos(portraits) }], true);
    assert.doesNotMatch(html, /dc-av--more/);
    assert.doesNotMatch(html, />\+\d+</);
    assert.equal((html.match(/class="dc-av dc-av--ph"/g) || []).length, portraits);
    assert.match(html, new RegExp(total + " médicos</div>"));
  }
  assert.doesNotMatch(SRC, /dc-av--more/);
});

test("the walk-in card has the shape of the others: one line and the rubro's icon, no paragraph", async () => {
  const html = await renderOf([
    { id: "a", nombre: "A", top: 1, n_medicos: 2, avatares: fotos(2) },
    { id: "demanda", nombre: "Demanda espontánea", top: 2, walkin: true, n_medicos: 0, avatares: [],
      icon: "/static/icons/clinica.png", desc: "Un párrafo largo del pack que explica todo." },
  ], true);
  assert.doesNotMatch(html, /dc-desc/);
  assert.doesNotMatch(html, /Un párrafo largo/);
  const card = html.slice(html.indexOf('href="/d/demanda"'));
  assert.match(card, /<div class="dc-sub">Sin turno, por llegada<\/div>/);
  assert.match(card, /<div class="dc-avs"><span class="dc-av dc-av--ico"><img src="\/static\/icons\/clinica\.png\?v=23" alt=""><\/span><\/div>/);
});

test("the card's line is dark and readable, never small grey print", () => {
  const rule = SRC.match(/\.dir-card \.dc-sub\{[^}]*\}/)[0];
  assert.match(rule, /color:var\(--text\)/);
  const size = Number(rule.match(/font-size:(\d+)px/)[1]);
  assert.ok(size >= 15, rule);
});

test("«Más especialidades» rows: no bare number, no rotating colours, one icon style", async () => {
  const html = await renderOf([
    { id: "a", nombre: "A", n_medicos: 3, n_servicios: 2, avatares: [], icon: "/static/icons/otras.png" },
    { id: "b", nombre: "B", n_medicos: 1, n_servicios: 0, avatares: [], icon: "/static/icons/grupos.png" },
  ], false);
  assert.doesNotMatch(html, /class="dr-n"/);
  assert.doesNotMatch(html, /class="dr-chip" style=/);
  assert.doesNotMatch(SRC, /CHIP_BG/);
  assert.doesNotMatch(SRC, /\[data-theme=humanrounds\] \.dr-chip/);
});

test("«Más especialidades» flows in columns, so a short last row leaves no empty cell", () => {
  const rule = SRC.match(/"\.dir-all\{[^}]*\}/)[0];
  assert.doesNotMatch(rule, /display:grid/);
  assert.match(rule, /columns:/);
  assert.match(SRC, /\.dir-row\{[^}]*break-inside:avoid/);
});
