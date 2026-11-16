/* listing-review-1115.test.mjs — the patient-site table after the
   2026-11-15 demo review (specialty page /d/<slug>, center page
   /center/<slug>, /doctors). Each test fails on the pre-review listing.mjs.

   Run:  node --test tests/listing-review-1115.test.mjs */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const L = await import("../static/platform/listing.mjs");
const SRC = readFileSync(new URL("../static/platform/listing.mjs", import.meta.url), "utf8");
const ES = JSON.parse(readFileSync(new URL("../locales/es/listing.json", import.meta.url), "utf8"));
const EN = JSON.parse(readFileSync(new URL("../locales/en/listing.json", import.meta.url), "utf8"));

// D26: Manrope's tabular «1» is a «0»-wide glyph, so «12:30» read «1 2:30».
test("D26: no tabular digits anywhere in the listing", () => {
  assert.doesNotMatch(SRC, /tabular-nums/);
});

// D34/P13: icons are Icons8 files, never drawn.
test("icons: no inline svg, no «i» letter in a CSS circle", () => {
  assert.doesNotMatch(SRC, /'<svg|"<svg|data:image\/svg/);
  assert.doesNotMatch(SRC, /lst-orden-i" aria-hidden="true">i</);
  assert.doesNotMatch(SRC, /\.lst-orden-i\{[^}]*border-radius:50%/);
  for (const name of ["search", "info", "filter"]) {
    assert.match(SRC, new RegExp("\\.lst-ico--" + name + "\\{--ico:url\\(/static/icons/" + name + "-sf-regular\\.svg\\)"));
  }
});

// Main fact: dark and 16px; «Con orden» / «Sin orden» the same weight and colour.
test("row facts are 16px ink; Con orden and Sin orden look alike", () => {
  assert.match(SRC, /\.lst-table td\{[^}]*font-size:16px/);
  assert.match(SRC, /\.lst-orden\{[^}]*font-size:16px;font-weight:400;color:var\(--text\)/);
  assert.match(SRC, /\.lst-orden-sin\{font-size:16px;font-weight:400;color:var\(--text\)/);
  assert.match(SRC, /\.lst-time-note\{font-size:16px;color:var\(--text\)/);
  assert.match(SRC, /\.lst-note,\.lst-empty\{font-size:16px;color:var\(--text\)/);
  // every fact-bearing selector at 16px: headers (th/.lst-sort), avatar
  // initials and the uppercase demo tag are labels, not facts
  const small = [...SRC.matchAll(/"([^"{]*)\{[^"}]*font-size:1[345](\.5)?px/g)].map((m) => m[1].trim())
    .filter((sel) => !/^(\.lst-table th|\.lst-sort|\.lst-av|\.lst-tag|\.lst-table th,\.lst-sort)/.test(sel));
  assert.deepEqual(small, [], "13–15px print left in the table");
});

// Buttons ≥44px; ONE filled primary colour; the secondary is an outline.
test("controls are 44px; one primary fill; «Más horarios» is an outline", () => {
  assert.match(SRC, /a\.lst-time\{min-height:44px;border:none;padding:0 16px;color:var\(--btn-fg,#fff\);background:var\(--btn-bg,var\(--accent/);
  assert.match(SRC, /\.lst-more\{display:inline-flex;align-items:center;box-sizing:border-box;min-height:44px;/);
  assert.match(SRC, /"border:1px solid var\(--border-strong\);border-radius:9px;background:var\(--panel\);"/);
  assert.match(SRC, /\.lst-orden\{[^}]*min-height:44px/);
  assert.match(SRC, /\.lst-filtros\{[^}]*min-height:44px/);
  // the wait no longer tints the button: only the walk-in status span keeps the steps
  assert.doesNotMatch(SRC, /"a" \+ c \+ "\{background:var\(--slot-c\)/);
  assert.match(SRC, /a\.lst-time--pedir\{background:var\(--btn-bg/);
});

// P37: one neutral avatar, not a palette.
test("avatars: one neutral initials style", () => {
  assert.doesNotMatch(SRC, /\.lst-av--[0-5]\{background/);
  assert.match(SRC, /\.lst-av:not\(\.lst-av--ph\)\{background:var\(--av-ph/);
});

test("a doctor with neither photo nor initials still gets initials", () => {
  assert.equal(L.iniciales("Dra. Ana María Pérez"), "AP");
  assert.equal(L.iniciales("Lic. Ñato Gómez"), "ÑG");
  assert.equal(L.iniciales("Juan"), "J");
  assert.equal(L.iniciales(""), "");
  assert.match(SRC, /r\.avatar_ini !== "" \? r\.avatar_ini : iniciales\(r\[col\.key\]\)/);
});

// P6/D17: Orden is a column on a phone too.
test("Orden is no longer hidden on a phone", () => {
  const orden = L.expandCols(["medico", "orden", "proximo"]).find((c) => c.t === "orden");
  assert.ok(!orden.hideSm);
  assert.match(SRC, /tr\.lst-row-av td\.lst-col-centro\+td\.lst-col-orden::before\{content:\\"·\\"/);
});

// D17: «Más horarios» always on its own line under the button on a phone.
test("phone: «Más horarios» has one fixed place in every row", () => {
  assert.match(SRC, /\.lst-timecell\{display:grid;grid-template-columns:auto minmax\(0,1fr\)/);
  assert.match(SRC, /\.lst-timecell>\.lst-more\{grid-column:1\/-1;justify-self:start/);
});

// D9/D20/D25: one date format, the verb on the button, no «Primer turno:».
test("one date shape and no «Primer turno:» prefix", () => {
  const pill = L.timePill("w0", "Jueves", "19 de noviembre", "08:00", "/x", "Sacar jue 19 nov, 08:00");
  assert.equal(pill, '<a class="lst-time lst-time--w0" href="/x">Sacar jue 19 nov, 08:00</a>');
  const orden = L.orderPill("w0", "/x", "Jue 19 nov, 08:00");
  assert.doesNotMatch(orden, /Primer turno/);
});

// P8/item 8: a column uniform across the page is not repeated per row.
test("a center or specialty shared by every row collapses", () => {
  const cols = L.expandCols(["medico", "especialidad", "centro", "orden", "proximo"]);
  const row = (n, centro) => ({
    nombre: "Dr " + n, href: "/doctor/" + n, especialidad: "Cardiología", esp_href: "/d/cardio",
    centro, centro_href: "/center/" + centro, orden: true,
    proximo: { v: "Jue", kind: "turno", dia: "2026-11-19", sort: "0x" + n },
  });
  const unoSolo = [row(1, "norte"), row(2, "norte")];
  const u = L.colapso(cols, unoSolo, unoSolo);
  const skipped = cols.filter((c, i) => u.skip[i]).map((c) => c.key);
  assert.deepEqual(skipped, ["especialidad", "centro"]);
  assert.deepEqual(u.tipos, [], "no note for a collapsed context column");
  const dos = [row(1, "norte"), row(2, "sur")];
  const v = L.colapso(cols, dos, dos);
  assert.deepEqual(cols.filter((c, i) => v.skip[i]).map((c) => c.key), ["especialidad"]);
  // one row: nothing to collapse against
  assert.deepEqual(Object.keys(L.colapso(cols, [row(1, "norte")], [row(1, "norte")]).skip), []);
});

test("«Sin orden» on every row becomes one sentence; unknown never counts", () => {
  const cols = L.expandCols(["servicio", "orden", "proximo"]);
  const r = (o) => ({ nombre: "S", href: "/s", orden: o, proximo: { v: "x", kind: "turno", dia: "2026-11-19", sort: "0" } });
  assert.deepEqual(L.colapso(cols, [r(false), r(false)], [r(false), r(false)]).tipos, ["sin_orden"]);
  assert.deepEqual(L.colapso(cols, [r(false), r(null)], [r(false), r(null)]).tipos, []);
  assert.deepEqual(L.colapso(cols, [r(false), r(true)], [r(false), r(true)]).tipos, []);
  assert.equal(L.nota("sin_orden", 2, false), "No hace falta orden médica.");
});

// Item 9 / remedies: no turnos → the next step, never a dead end.
test("no turnos: the server's remedy wins, else the specialty, else the center", () => {
  const serverRm = L.habilitaCell({ estado: "sin_fechas", remedio: { href: "/d/x", texto: "Pedí turno con otro" } }, {});
  assert.match(serverRm, /<a class="lst-closed-next" href="\/d\/x">Pedí turno con otro<\/a>/);
  const esp = L.habilitaCell({ estado: "sin_fechas" },
    { especialidad: "Pediatría", esp_href: "/d/pediatria", centro: "CAPS", centro_href: "/center/caps" });
  assert.match(esp, /href="\/d\/pediatria">Ver otros médicos de la especialidad</);
  // a gendered title is never pasted into the sentence
  const titulo = L.habilitaCell({ estado: "sin_fechas" }, { especialidad: "Cardióloga", esp_href: "/d/cardio" });
  assert.doesNotMatch(titulo, /Cardióloga/);
  const centro = L.habilitaCell({ estado: "sin_fechas" }, { centro: "CAPS Norte", centro_href: "/center/caps" });
  assert.match(centro, /href="\/center\/caps">Ver los turnos de CAPS Norte</);
  assert.match(SRC, /\.lst-closed-next\{display:flex;align-items:center;min-height:44px;\}/);
  // the server sends the remedy on `proximo.remedio`, beside `proximo.habilita`
  assert.match(SRC, /habilitaCell\(p\.remedio \? Object\.assign\(\{\}, p\.habilita, \{ remedio: p\.remedio \}\) : p\.habilita, r\)/);
});

// P19: the walk-in is «sin turno, por llegada», never «por orden de llegada».
test("P19: walk-in wording never says «orden de llegada»", () => {
  for (const [k, v] of Object.entries(ES)) {
    if (/walkin/.test(k)) assert.doesNotMatch(v, /orden de llegada/, k);
  }
  assert.doesNotMatch(SRC, /"[^"]*por orden de llegada[^"]*"\)/, "no fallback string says it");
});

// P29/D34: phone filters fold behind one control; a long list gets a sort select.
test("phone: one «Filtros» control and a sort select", () => {
  assert.match(SRC, /class="lst-filtros/);
  assert.match(SRC, /data-sortsel/);
  assert.match(SRC, /\.lst-tools--filters:not\(\.lst-tools--open\) \.lst-selects\{display:none;\}/);
  assert.match(SRC, /\.lst-phonebar\{display:none;\}/);
  for (const k of ["listing.filters.toggle", "listing.filters.count", "listing.sort.by", "listing.sort.aria",
                   "listing.sort.suggested", "listing.noslots.center", "listing.noslots.other_doctors",
                   "listing.note.no_order"]) {
    assert.ok(ES[k], "es " + k);
    assert.ok(EN[k], "en " + k);
  }
});
