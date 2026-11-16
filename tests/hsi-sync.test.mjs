// static/platform/hsi-sync.mjs — the panel's one «¿está en HSI?» control
// (the product owner, 2026-10-27, d1122568). Four states with one look: a button, a
// turning icon while it works, a check once HSI has it, and the reason when it
// failed. These tests pin the markup each surface draws and the two painters
// (server push, no extension) on hand-made elements; the extension painting
// the same hook is driven for real in tests/e2e/hsi-sync-entries.spec.mjs.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// The DNI in a hint goes through the panel's one formatter (format.mjs), which
// needs the data pack's phone rules before it loads.
globalThis.SANA_COUNTRY_PHONE = JSON.parse(readFileSync(new URL("../packs/ar/country.json", import.meta.url), "utf8")).phone;
const HsiSync = await import("../static/platform/hsi-sync.mjs");

// The smallest element the painters touch: dataset, text, disabled, closest()
// up to the control's box and querySelector() down to its hint.
function control() {
  const aviso = { textContent: "", innerHTML: "", hidden: true };
  const caja = { querySelector: (s) => (s === ".hsi-sync-aviso" || s === ".hr-hsi-aviso" ? aviso : null) };
  const boton = { dataset: {}, textContent: "", disabled: false, closest: (s) => (s === ".hsi-sync" ? caja : null) };
  return { boton, aviso };
}

// Node's own navigator has no clipboard: give it one for this test only.
function clipboard(t, value) {
  Object.defineProperty(globalThis.navigator, "clipboard", { configurable: true, value });
  t.after(() => { delete globalThis.navigator.clipboard; });
}

test("the extension's hook carries the id, the day and the auto mark the extension reads", () => {
  const html = HsiSync.extensionHtml({ id: 42, dia: "2026-10-28", auto: true, primario: true, attrs: ' id="ct-hsi"' });
  assert.match(html, /data-hr-hsi="42"/);
  assert.match(html, /data-hr-hsi-dia="2026-10-28"/);
  assert.match(html, /data-hr-hsi-auto/);
  assert.match(html, /id="ct-hsi"/);
  assert.match(html, /class="hsi-sync-btn btn btn-accent"/);
  assert.match(html, /data-estado="reposo">Cargar en HSI<\/button>/);
  // The extension writes its hint into the sibling it already knows.
  assert.match(html, /<\/button><span class="hr-hsi-aviso" role="status"><\/span>/);
});

test("a plain hook has no day, no auto mark and no accent", () => {
  const html = HsiSync.extensionHtml({ id: 7 });
  assert.doesNotMatch(html, /data-hr-hsi-dia/);
  assert.doesNotMatch(html, /data-hr-hsi-auto/);
  assert.doesNotMatch(html, /btn-accent/);
  assert.doesNotMatch(html, /hsi-sync-linea/);
  assert.match(HsiSync.extensionHtml({ id: 7, linea: true }), /class="hsi-sync hr-hsi-panel hsi-sync-linea"/);
});

test("an id with markup in it is escaped", () => {
  assert.match(HsiSync.extensionHtml({ id: '1"><b>' }), /data-hr-hsi="1&quot;&gt;&lt;b&gt;"/);
});

test("server push: every state the server keeps has its one look", () => {
  assert.match(HsiSync.serverHtml("ok", { id: 1 }), /data-estado="guardado">En HSI</);
  // The journal resends a pending one by itself: the automatic-send label, no button.
  const pend = HsiSync.serverHtml("pendiente", { id: 1 });
  assert.match(pend, /data-estado="enviando">Envío automático a HSI</);
  assert.doesNotMatch(pend, /<button/);
  // HSI does not take a turno that already happened, and a row without an id cannot be sent.
  assert.match(HsiSync.serverHtml("error", { id: 1, futuro: false }), /data-estado="no">No está en HSI</);
  assert.match(HsiSync.serverHtml("error", { futuro: true }), /data-estado="no"/);
  assert.match(HsiSync.serverHtml("error", { id: 9, futuro: true }), /<button[^>]*data-estado="error" data-hsi-retry="9">Reintentar envío a HSI</);
  assert.match(HsiSync.serverHtml("skip", { id: 9, futuro: true }), /<button[^>]*data-estado="reposo" data-hsi-retry="9">Enviar a HSI</);
  assert.match(HsiSync.serverHtml("ok", { id: 1, linea: true }), /class="hsi-sync hsi-sync-linea"/);
});

test("server push: «Enviar» turns while it runs, then a check, or the reason next to it", () => {
  const { boton, aviso } = control();
  HsiSync.pintarServidor(boton, "enviando");
  assert.deepEqual([boton.dataset.estado, boton.disabled, boton.textContent], ["enviando", true, "Enviando a HSI…"]);
  HsiSync.pintarServidor(boton, "guardado");
  assert.deepEqual([boton.dataset.estado, boton.disabled, boton.textContent, aviso.textContent], ["guardado", true, "En HSI", ""]);
  HsiSync.pintarServidor(boton, "error", "HSI no respondió: probá en un rato.");
  assert.deepEqual([boton.dataset.estado, boton.disabled, boton.textContent], ["error", false, "Reintentar envío a HSI"]);
  assert.equal(aviso.textContent, "HSI no respondió: probá en un rato.");
  HsiSync.pintarServidor(null, "error"); // a control already gone is a no-op
});

test("without the extension a click copies the DNI and offers the install link", async (t) => {
  const copied = [];
  clipboard(t, { writeText: (s) => { copied.push(s); return Promise.resolve(); } });
  const { boton, aviso } = control();
  await HsiSync.sinExtension(boton, { dni: "30111222", tienda: "https://chromewebstore.google.com/detail/x" });
  assert.deepEqual(copied, ["30111222"]);
  assert.equal(aviso.hidden, false);
  assert.match(aviso.innerHTML, /^Copiamos el DNI 30\.111\.222: pegalo en HSI\. <a href="https:\/\/chromewebstore\.google\.com\/detail\/x" target="_blank" rel="noopener">Instalá HSI Humanizador/);
});

test("a clipboard that refuses still shows the DNI to type, and no store means no link", async (t) => {
  clipboard(t, { writeText: () => Promise.reject(new Error("denied")) });
  const { boton, aviso } = control();
  await HsiSync.sinExtension(boton, { dni: "30111222" });
  assert.equal(aviso.innerHTML, "DNI 30.111.222: cargalo en HSI. ");
  // A separate hint (the «Turno creado» help paragraph) wins over the box's own.
  const otro = { innerHTML: "", hidden: true };
  await HsiSync.sinExtension(boton, { dni: "", tienda: "https://x.test/", aviso: otro });
  assert.match(otro.innerHTML, /^<a href="https:\/\/x\.test\/"/);
});

// Contraparte técnica, 2026-10-27: «nunca entendí cómo… llegábamos a esa pantalla». Next to
// «Cargar en HSI» and on «Pacientes de hoy», one line says where to install
// the extension: the store once published, else the ZIP; the guide PDF.
// Responsable del producto 2026-11-09: the line is ONE link, «Cargar turnos en HSI con un toque»;
// it opens a popup with what the extension does, the steps, the store or the
// ZIP and the PDF guide.
test("where to install the extension: one link that opens the steps, the store or the ZIP and the guide", () => {
  const tienda = HsiSync.instalarHtml({ tienda: "https://chromewebstore.google.com/detail/x", zip: null,
    guia: "/api/admin/hsi-extension/guide.pdf" }, { attrs: ' id="i"' });
  assert.match(tienda, /^<div class="hsi-instalar" id="i"><button type="button" class="hsi-instalar-link" popovertarget="i-pop" aria-haspopup="dialog">Cargar turnos en HSI con un toque<\/button>/);
  assert.match(tienda, /<div class="hsi-instalar-pop" id="i-pop" popover role="dialog" aria-label="Cargar turnos en HSI con un toque">/);
  assert.match(tienda, /popovertarget="i-pop" popovertargetaction="hide" aria-label="Cerrar"/);
  assert.match(tienda, /<ol class="hsi-instalar-pasos"><li><a href="https:\/\/chromewebstore\.google\.com\/detail\/x" target="_blank" rel="noopener">Instalá HSI Humanizador en Chrome<\/a><\/li><li>/);
  assert.match(tienda, /<p class="hsi-instalar-guia"><a href="\/api\/admin\/hsi-extension\/guide\.pdf" target="_blank" rel="noopener">Cómo se instala \(PDF\)<\/a><\/p><\/div><\/div>$/);
  // one popover per line: two lines without an id never share a name
  const a = HsiSync.instalarHtml({ tienda: "https://x.test/" }), b = HsiSync.instalarHtml({ tienda: "https://x.test/" });
  assert.notEqual(/popovertarget="([^"]+)"/.exec(a)[1], /popovertarget="([^"]+)"/.exec(b)[1]);
  // the link reads as a link, the target is 44 px
  assert.ok(HsiSync.css.includes(".hsi-instalar-link{display:inline-flex;align-items:center;min-height:44px;"));
  assert.ok(HsiSync.css.includes("color:var(--accent,#1a5fb4);cursor:pointer;}"));
  const zip = HsiSync.instalarHtml({ tienda: null, zip: "/api/admin/hsi-extension/extension.zip",
    guia: "/api/admin/hsi-extension/guide.pdf" });
  assert.match(zip, /<li><a href="\/api\/admin\/hsi-extension\/extension\.zip" download>Descargá HSI Humanizador \(ZIP\)<\/a><\/li>/);
  assert.match(zip, /Cómo se instala \(PDF\)/);
  assert.doesNotMatch(zip, /chromewebstore/);
  // no guide in the image: the install link alone
  assert.doesNotMatch(HsiSync.instalarHtml({ tienda: "https://x.test/", guia: null }), /PDF/);
  // nothing to install from, or a site the extension does not run on: no line
  for (const nada of [null, undefined, {}, { guia: "/g.pdf" }]) assert.equal(HsiSync.instalarHtml(nada), "");
  // the line is gone once the extension marks the page
  assert.ok(HsiSync.css.includes('html[data-hr-humanizador="1"] .hsi-instalar{display:none;}'));
});

test("the extension's page mark is what tells the panel the click reaches HSI", () => {
  assert.equal(HsiSync.extensionPresente({ documentElement: { dataset: { hrHumanizador: "1" } } }), true);
  assert.equal(HsiSync.extensionPresente({ documentElement: { dataset: {} } }), false);
});

// The request list's HSI column follows the same rule as every other entry
// point: the push, or the desk's extension serving this site (2026-10-28,
// d1122568). It never waits for the extension's page mark: without the
// extension the click copies the DNI and offers the install link (blind
// review 2026-11-05, C1/C2: the mark rule hid the column on the demo).
test("the request list's HSI column follows hsiActivo, not the extension's mark", () => {
  const APP = readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");
  assert.doesNotMatch(APP, /function hsiColumna\(/);
  assert.doesNotMatch(APP, /observarExtensionHsi/);
  assert.match(APP, /\.concat\(hsiActivo\(\) \? \['hsi'\] : \[\]\)/);
});

test("one look for the panel's control and the extension's row button", () => {
  // The turning icon on every working state, the check on the saved one, in both buttons.
  for (const b of [".hsi-sync-btn", ".hr-hsi-boton"]) {
    for (const e of ["abriendo", "cargando", "enviando", "en-cola"]) assert.ok(HsiSync.css.includes(`${b}[data-estado="${e}"]::before`), `${b} ${e}`);
    assert.ok(HsiSync.css.includes(`${b}[data-estado="guardado"]::before`), b);
  }
  assert.match(HsiSync.css, /hsi-sync-sf-regular\.svg/);
  assert.match(HsiSync.css, /hsi-checkmark-sf-regular\.svg/);
  assert.match(HsiSync.css, /prefers-reduced-motion:reduce/);
  const added = [];
  const doc = { getElementById: (id) => (added.some((s) => s.id === id) ? {} : null), createElement: () => ({}),
    head: { appendChild: (s) => added.push(s) } };
  HsiSync.ensureCss(doc);
  HsiSync.ensureCss(doc);
  assert.equal(added.length, 1);
  assert.equal(added[0].id, "hsi-sync-css");
});
