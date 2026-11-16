import test from "node:test";
import assert from "node:assert/strict";

const styles = new Map();
globalThis.document = {
  readyState: "loading",
  addEventListener() {},
  querySelectorAll() { return []; },
  getElementById(id) { return styles.get(id) || null; },
  createElement(tag) {
    return { tagName: tag.toUpperCase(), id: "", textContent: "" };
  },
  head: {
    appendChild(node) { styles.set(node.id, node); },
  },
};
globalThis.window = {};

const Tiles = await import("../static/platform/tiles.mjs");

function host() {
  const classes = new Set();
  return {
    classList: { add(name) { classes.add(name); } },
    innerHTML: "",
    classes,
  };
}

test("tiles.mjs is the renderer, not a compatibility global", () => {
  assert.equal(window.SanaTiles, undefined);
  assert.equal(typeof Tiles.render, "function");
  assert.equal(typeof Tiles.boot, "function");
});

test("render creates one escaped whole-tile link and injects shared styles once", () => {
  const mount = host();
  Tiles.render(mount, [{
    slug: "riverside-clinic",
    nombre: "CAPS <Norte>",
    direccion: "Av. Bunge & Shaw",
    jtbd: "Atención primaria",
  }]);
  assert.ok(mount.classes.has("tiles-grid"));
  assert.match(mount.innerHTML, /^<a class="tile tile--nophoto" href="\/center\/riverside-clinic">/);
  assert.match(mount.innerHTML, /CAPS &lt;Norte&gt;/);
  assert.match(mount.innerHTML, /Av\. Bunge &amp; Shaw/);
  assert.equal((mount.innerHTML.match(/<a\b/g) || []).length, 1);
  assert.ok(styles.has("tiles-style"));

  const second = host();
  Tiles.render(second, []);
  assert.equal(styles.size, 1);
});

// Demo review 2026-11-15 (P35): a card is ONE link; the address is dark text even
// when a payload still carries a maps_url — no second link inside the card.
test("a tile with an address is one link and its address plain text, even with a map link", () => {
  const mount = host();
  Tiles.render(mount, [{
    slug: "riverside-clinic",
    nombre: "CAPS Norte",
    direccion: "Av. Bunge & Shaw",
    maps_url: "https://www.google.com/maps/search/?api=1&query=Av.%20Bunge",
    jtbd: "Atención primaria",
  }]);
  assert.match(mount.innerHTML, /^<a class="tile tile--nophoto" href="\/center\/riverside-clinic">/);
  assert.match(mount.innerHTML, /<span class="tile-addr">Av\. Bunge &amp; Shaw<\/span>/);
  assert.equal((mount.innerHTML.match(/<a\b/g) || []).length, 1);
  assert.doesNotMatch(mount.innerHTML, /google\.com\/maps/);
});

test("a temporary address keeps its date as its own plain line under the place (demo review 2026-11-15, P23)", () => {
  const mount = host();
  Tiles.render(mount, [{
    slug: "caps-movil",
    nombre: "CAPS Móvil",
    direccion: "Escuela N°6",
    direccion_nota: "Hasta el 30 de noviembre",
  }]);
  assert.match(mount.innerHTML, /<span class="tile-addr">Escuela N°6<\/span><span class="tile-addr">Hasta el 30 de noviembre<\/span>/);
});

test("informational workshop stays non-clickable while its venue remains a link", () => {
  const mount = host();
  Tiles.render(mount, [{
    href: "",
    nombre: "Taller de espalda sana",
    jtbd: "CAPS Ostende",
    jtbd_href: "/center/caps-ostende",
    notas: ["Martes, 10:00"],
  }]);
  assert.match(mount.innerHTML, /^<div class="tile tile--static tile--nophoto">/);
  assert.equal((mount.innerHTML.match(/<a\b/g) || []).length, 1);
  assert.match(mount.innerHTML, /class="tile-lede-a" href="\/center\/caps-ostende"/);
});

// ---- the home's compact list (renderList) --------------------------------

const DIR = "https://www.google.com/maps/dir/?api=1&destination=";
const HOME = [
  { slug: "hospital-pinamar", name: "Hospital de Pinamar", locality: "Pinamar", place: "av. shaw 255", place_main: true, directions_url: DIR + "Av.%20Shaw%20255" },
  { slug: "caps-pinamar", name: "CAPS Pinamar", locality: "Pinamar", place: "intermedanos y calamares", directions_url: DIR + "Intermedanos" },
  { slug: "caps-ostende", name: "CAPS Ostende", locality: "Ostende", place: "misiones y av. del parque", directions_url: DIR + "Misiones" },
  // listed after the hospital in the data, same street address, no area of its own
  { slug: "servicio-de-hemoterapia", name: "Servicio de Hemoterapia", locality: "Pinamar", place: "av. shaw 255", directions_url: DIR + "Av.%20Shaw%20255%2C%20Hospital" },
  { slug: "servicio-de-oncologia-hospital-de-dia", name: "Servicio de Oncología - Hospital de Día", locality: "Pinamar", place: "av. shaw 255", directions_url: DIR + "Av.%20Shaw%20255%2C%20Hospital" },
  { slug: "suelto", name: "Servicio suelto de Algo", locality: "Ostende", place: "calle suelta 1", directions_url: DIR + "Suelta" },
  { slug: "zoonosis", name: "Zoonosis", href: "/zoonosis", locality: "Ostende", place: "boulevard", directions_url: DIR + "Boulevard" },
  { slug: "caps-valeria", name: "CAPS <Valeria> & Mar", locality: "Valeria del Mar", place: "guanca 300", directions_url: DIR + "Guanca" },
  { slug: "caps-movil", name: "CAPS Móvil", locality: "", directions_url: "" },
  { slug: "sin-zona", name: "Centro sin zona", locality: "", place: "calle 1", directions_url: DIR + "Calle1" },
];

test("the list groups by area in order of appearance, the centers without one last and untitled", () => {
  const sections = Tiles.listSections(HOME);
  assert.deepEqual(sections.map((s) => s.title), ["Pinamar", "Ostende", "Valeria del Mar", ""]);
  assert.deepEqual(sections[3].rows.map((r) => r.items[0].slug), ["caps-movil", "sin-zona"]);
});

test("centers at one address share ONE row and ONE route button, the main center first", () => {
  const [pinamar] = Tiles.listSections(HOME);
  assert.equal(pinamar.rows.length, 2);
  assert.deepEqual(pinamar.rows[0].items.map((c) => c.slug), ["hospital-pinamar", "servicio-de-hemoterapia", "servicio-de-oncologia-hospital-de-dia"]);
  const html = Tiles.listHtml(HOME);
  const hospitalRow = html.split('<li class="cl-row">').find((r) => r.includes("Hospital de Pinamar"));
  assert.equal((hospitalRow.match(/class="cl-go"/g) || []).length, 1);
  assert.match(hospitalRow, /class="cl-name cl-name--main" href="\/center\/hospital-pinamar">Hospital de Pinamar</);
  assert.match(hospitalRow, /class="cl-name cl-name--sub" href="\/center\/servicio-de-hemoterapia">Hemoterapia</);
  assert.ok(hospitalRow.indexOf("Hospital de Pinamar") < hospitalRow.indexOf("Hemoterapia"));
});

test("the services under the hospital lose «Servicio de», the dash becomes a comma and short words glue to the next", () => {
  assert.equal(Tiles.subName("Servicio de Hemoterapia"), "Hemoterapia");
  assert.equal(Tiles.subName("Servicio de Oncología - Hospital de Día"), "Oncología, Hospital de Día");
  assert.equal(Tiles.subName("Servicio de Atención de la Mujer"), "Atención de la Mujer");
  assert.ok(!/\s-\s|^-/.test(Tiles.subName("Servicio de Oncología – Hospital de Día")), "no dangling dash");
  const html = Tiles.listHtml(HOME);
  const hospitalRow = html.split('<li class="cl-row">').find((r) => r.includes("Hospital de Pinamar"));
  assert.match(hospitalRow, /class="cl-name cl-name--sub" href="\/center\/servicio-de-oncologia-hospital-de-dia">Oncología, Hospital de Día</);
  assert.equal((hospitalRow.match(/class="cl-go"/g) || []).length, 1, "one route button for the whole group");
  // a center alone on its address keeps its name as written
  assert.match(html, />Servicio suelto de Algo</);
});

test("the services sit under the main center, indented, in regular weight and full ink; the zones go two to a row from 1280", () => {
  const css = (Tiles.listHtml(HOME), styles.get("tiles-style").textContent);
  const sub = /\.cl-name--sub\{[^}]*\}/.exec(css)[0];
  assert.match(sub, /font-weight:400/);
  assert.match(sub, /padding:2px 0 2px 18px/);
  assert.ok(!/color:/.test(sub), "ink stays the full --text of .cl-name");
  assert.match(css, /@media\(min-width:1280px\)\{\.cl-grid\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  const mount = host();
  Tiles.renderList(mount, HOME);
  assert.ok(mount.classes.has("cl-grid"));
});

test("the route button sits at the TOP of a multi-line row, level with the main name (the product owner, 2026-10-28), not centred", () => {
  const css = (Tiles.listHtml(HOME), styles.get("tiles-style").textContent);
  const row = /\.cl-row\{[^}]*\}/.exec(css)[0];
  assert.match(row, /align-items:flex-start/);
  assert.ok(!/align-items:center/.test(row), "a centred button floats mid-row beside the services");
  // 44px button centred on the 22px first line: (44 - 22) / 2 = 11px above the names
  assert.match(/\.cl-names\{[^}]*\}/.exec(css)[0], /padding:11px 0/);
});

test("the route button is icon-only with its own accessible name, opens the maps route, and is absent without a fixed place", () => {
  const html = Tiles.listHtml(HOME);
  const rows = html.split('<li class="cl-row">').slice(1);
  const movil = rows.find((r) => r.includes("CAPS Móvil"));
  assert.ok(!movil.includes("cl-go"), "CAPS Móvil has no route button");
  const caps = rows.find((r) => r.includes(">CAPS Pinamar<"));
  assert.match(caps, new RegExp('<a class="cl-go" href="' + DIR.replace(/[?&.]/g, "\\$&").replace(/&/g, "&amp;") + 'Intermedanos" target="_blank" rel="noopener noreferrer" aria-label="Cómo llegar a CAPS Pinamar" title="Cómo llegar a CAPS Pinamar"><img src="/static/icons/directions-sf-regular\\.png\\?v=1"'));
  // the button carries no text: only the icon (alt="" — the name is the aria-label)
  const go = caps.slice(caps.indexOf('<a class="cl-go"'), caps.indexOf("</a>", caps.indexOf('<a class="cl-go"')) + 4);
  assert.equal(go.replace(/<[^>]*>/g, ""), "");
  // the page's own Zoonosis link wins over /center/<slug>
  assert.match(html, /href="\/zoonosis">Zoonosis</);
});

test("the list escapes names and uses the page's T() for the button name", () => {
  const html = Tiles.listHtml(HOME, (key, vars, fallback) => key === "home.centers.directions" ? "Directions to " + vars.name : fallback);
  assert.match(html, /CAPS &lt;Valeria&gt; &amp; Mar</);
  assert.match(html, /aria-label="Directions to CAPS &lt;Valeria&gt; &amp; Mar"/);
  const mount = host();
  Tiles.renderList(mount, HOME);
  assert.match(mount.innerHTML, /^<ul class="cl"><li class="cl-h">Pinamar<\/li>/);
  assert.ok(styles.get("tiles-style").textContent.includes(".cl-go{"));
});

test("the route button is at least 44px and its ink is dark", () => {
  const css = styles.get("tiles-style").textContent;
  const rule = /\.cl-go\{[^}]*\}/.exec(css)[0];
  assert.match(rule, /width:44px;height:44px/);
  assert.match(rule, /border:1\.5px solid var\(--text-2\)/);
  assert.match(rule, /background:transparent/, "outline button, not filled");
});

test("a row without a route but with an address writes it under the name (CAPS Móvil, product owner 2026-11-05); a routed row never does", () => {
  const list = HOME.map((c) => c.slug === "caps-movil" ? { ...c, address: "Víctor Hugo y Anspach, <hasta> el 30" }
    : c.slug === "caps-pinamar" ? { ...c, address: "Intermédanos y Calamares" } : c);
  const rows = Tiles.listHtml(list).split('<li class="cl-row">').slice(1);
  const movil = rows.find((r) => r.includes("CAPS Móvil"));
  assert.match(movil, /<span class="cl-addr">Víctor Hugo y Anspach, &lt;hasta&gt; el 30<\/span>/);
  assert.ok(movil.indexOf("CAPS Móvil") < movil.indexOf("cl-addr"));
  const caps = rows.find((r) => r.includes(">CAPS Pinamar<"));
  assert.ok(!caps.includes("cl-addr"), "the map and the route button already say where it is");
  const rule = /\.cl-addr\{[^}]*\}/.exec(styles.get("tiles-style").textContent)[0];
  assert.match(rule, /font-size:16px/);
  assert.match(rule, /color:var\(--text\)/);
});
