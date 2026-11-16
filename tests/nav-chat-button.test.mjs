// nav-chat-button.test.mjs — nav.js's header CTA (2026-08-01): "Subí tu
// orden" (a link to /upload-order) became "Chateá con IA" (a button that
// OPENS the site's floating chat widget instead of navigating). Same
// vm-executed pattern as nav-lang-switch.test.mjs: run the real nav.js and
// call its pure SiteNav.html() rather than regexing markup by hand.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as Nav from "../static/platform/nav.mjs";
import { patientSiteSource } from "./helpers/patient-site-source.mjs";

const src = readFileSync(new URL("../static/platform/nav.mjs", import.meta.url), "utf8");

function loadNav() {
  globalThis.window = {};
  delete globalThis.document;
  return Nav;
}

const STATE_ON = { loggedIn: false, hasSolicitudes: false, iaDown: false, iniciales: "", authMode: "password" };
const STATE_IA_DOWN = { ...STATE_ON, iaDown: true };

test('the menu offers upload and a chat button without a second widget', () => {
  const SiteNav = loadNav();
  const html = SiteNav.html("/", "", STATE_ON);
  assert.match(html, />Subí tu orden<\/a>/);
  // Robot + "Chat" and nothing else; the full phrase stays the accessible name.
  assert.match(html, /<button type="button"[^>]*data-nav-accion="abrir-chat"[^>]*aria-label="Chateá con IA"[^>]*><img[^>]*chat-robot-sf-regular[^>]*>Chat<\/button>/);
  assert.doesNotMatch(html, />Chateá con IA</);
  assert.doesNotMatch(html, /id="stz-chat-panel"/);
});

test("workshops are not a top-menu item (they are a tab in Centros)", () => {
  const SiteNav = loadNav();
  const html = SiteNav.html("/d/grupos", "", STATE_ON);
  assert.doesNotMatch(html, /data-nav-key="talleres"/);
  assert.match(html, /class="stznav-link on" data-nav-key="centros"/);
});

test("a document without a theme uses the public Human Rounds brand", () => {
  const SiteNav = loadNav();
  const html = SiteNav.html("/", "", STATE_ON);
  assert.match(html, /class="stznav-name">Human Rounds<\/span>/);
  assert.doesNotMatch(html, /class="stznav-name">Turnos de salud pública en Pinamar<\/span>/);
});

// A real page HAS a document whose <html> may or may not carry data-theme;
// loadNav() only covers the document-less context. This stubs the attribute
// itself: null (page sin tema), "pinamar", o un valor desconocido.
function loadNavConTema(theme) {
  globalThis.window = {};
  globalThis.document = {
      documentElement: { getAttribute: (n) => (n === "data-theme" ? theme : null) },
      addEventListener() {},
  };
  return Nav;
}

test("sin data-theme (o con uno desconocido) la marca pública sigue siendo Human Rounds", () => {
  assert.match(loadNavConTema(null).html("/", "", STATE_ON), /class="stznav-name">Human Rounds<\/span>/);
  assert.match(loadNavConTema("no-existe").html("/", "", STATE_ON), /class="stznav-name">Human Rounds<\/span>/);
  // "pinamar" es la única variante que cambia la marca: la barra dice el
  // nombre del sitio «Turnos de salud pública en Pinamar» (2026-10-27), el
  // mismo de la pestaña, en dos renglones — ni el lema de prensa del
  // 2026-10-22 ni el logotipo dibujado.
  const pinamar = loadNavConTema("pinamar").html("/", "", STATE_ON);
  assert.match(pinamar, /<span class="stznav-name-long" lang="es">Turnos de salud pública en Pinamar<\/span>/);
  assert.doesNotMatch(pinamar, /Portal de turnos/);
  assert.doesNotMatch(pinamar, /stznav-wordmark|salud-pinamar\.svg/);
  assert.doesNotMatch(pinamar, /class="stznav-name"/);
});

test("nav.js conserva la tabla de marca y el default de theme.brand_name()", () => {
  // La tabla existe en dos copias deliberadas de theme.brand_name() (nav.js y
  // upload-order.js) porque el JS del navegador no puede importar Python: si
  // una deriva, el panel y el sitio público muestran marcas distintas tras un
  // cambio de tema. Este gap pina la primera. (La tercera copia vivía en el
  // inline del admin.html del shell por hash, retirado con él; el panel
  // directo repinta la marca con un reload.)
  const tabla = /NOMBRES_MARCA\s*=\s*\{\s*pinamar:\s*['"]Turnos de salud pública en Pinamar['"]\s*,\s*humanrounds:\s*['"]Human Rounds['"]\s*\}/;
  const sinTema = /\|\|\s*['"]humanrounds['"]/;
  const temaDesconocido = /NOMBRES_MARCA\[tema\]\s*\|\|\s*NOMBRES_MARCA\.humanrounds/;
  assert.match(src, tabla, "nav.js conserva la tabla pinamar/humanrounds");
  assert.match(src, sinTema, "nav.js: sin data-theme el tema default es humanrounds");
  assert.match(src, temaDesconocido, "nav.js: un tema desconocido cae a la entrada humanrounds");
  // Y la copia sigue al original: el nombre de cada tema en theme.py es el
  // mismo que en nav.js (2026-10-29 la barra quedó con otra frase que la pestaña).
  const themePy = readFileSync(new URL("../app/core/theme.py", import.meta.url), "utf8");
  const pyDesde = themePy.indexOf("_BRAND_NAMES = {");
  const jsDesde = src.indexOf("NOMBRES_MARCA = {");
  assert.ok(pyDesde >= 0 && jsDesde >= 0, "las dos tablas existen");
  const pyTabla = themePy.slice(pyDesde, themePy.indexOf("}", pyDesde) + 1);
  const jsTabla = src.slice(jsDesde, src.indexOf("}", jsDesde) + 1);
  // Cada tema de theme.py, no una lista fija: un tema nuevo solo en Python
  // dejaría la barra en «Human Rounds» con la pestaña en otro nombre.
  const temas = [...pyTabla.matchAll(/"([a-z0-9_-]+)":/g)].map((m) => m[1]);
  assert.ok(temas.includes("pinamar") && temas.includes("humanrounds"), "theme.py lista los temas");
  for (const tema of temas) {
    const py = pyTabla.match(new RegExp('"' + tema + '":\\s*"([^"]+)"'));
    const js = jsTabla.match(new RegExp(tema + ':\\s*"([^"]+)"'));
    assert.ok(py && js, "theme.py y nav.js nombran el tema " + tema);
    assert.equal(js[1], py[1], "nav.js y theme.brand_name() dan el mismo nombre a " + tema);
  }
});

test('upload stays a link and chat is an action, not a navigation target', () => {
  const SiteNav = loadNav();
  const html = SiteNav.html("/", "", STATE_ON);
  assert.match(html, /data-nav-key="orden"[^>]*href="\/upload-order"/);
  assert.equal((html.match(/data-nav-accion="abrir-chat"/g) || []).length, 1);
  assert.doesNotMatch(html, /<a[^>]*data-nav-accion="abrir-chat"/);
});

test("con la IA caída, Subí tu orden no aparece (mismo gate que antes)", () => {
  const SiteNav = loadNav();
  const html = SiteNav.html("/", "", STATE_IA_DOWN);
  assert.doesNotMatch(html, /Subí tu orden/);
  assert.doesNotMatch(html, /data-nav-accion="abrir-chat"/);
});

test("el ancho intermedio conserva acciones y cuenta; el pequeño colapsa todo", () => {
  // Última corrección del responsable del producto, 2026-08-01: a 806px todavía entran ambas
  // acciones y Mi cuenta. No se duplica ningún control.
  assert.doesNotMatch(src, /stznav-featured/);
  assert.match(src, /stznav--compact/);
  assert.match(src, /stznav-priority/);
  assert.match(src, /compactNeed/);
  assert.match(src, /\.stznav--collapsed \.stznav-btn\{margin-left:0;margin-top:0;width:100%;\}/);
});

test("Subí tu orden se dibuja UNA sola vez", () => {
  // La copia duplicada (featured + menú) existió hasta el 2026-08-01 y era la
  // fuente de la regresión de "aparece dos veces / no aparece ninguna".
  const SiteNav = loadNav();
  const html = SiteNav.html("/", "", STATE_ON);
  for (const key of ["orden"]) {
    const usos = [...html.matchAll(new RegExp('data-nav-key="' + key + '"', "g"))];
    assert.equal(usos.length, 1, `el botón ${key} tiene que aparecer una sola vez`);
  }
});

test('la cuenta autenticada apunta directo a la pestaña de turnos', () => {
  const SiteNav = loadNav();
  const html = SiteNav.html("/account", "", { ...STATE_ON, loggedIn: true, iniciales: "SH" });
  assert.match(html, /href="\/account#turnos"[^>]*>[^<]*<span class="stznav-cuenta-av/);
});

test('Mi cuenta groups its entries under one heading with an icon-only logout control', () => {
  const SiteNav = loadNav();
  const html = SiteNav.html("/account", "", { ...STATE_ON, loggedIn: true, hasSolicitudes: true });
  assert.match(html, /class="stznav-account-section"/);
  assert.match(html, /class="stznav-account-heading"><span>Mi cuenta<\/span>/);
  assert.match(html, /class="stznav-account-logout" aria-label="Cerrar sesión"/);
  assert.match(html, /data-account-tab="datos"/);
  assert.doesNotMatch(html, />Cerrar sesión<\/button>/);
});

test("«Mis turnos» is always in the account menu; the account page reveals it and marks the real tab", () => {
  const SiteNav = loadNav();
  const cold = SiteNav.html("/account", "", { ...STATE_ON, loggedIn: true, hasSolicitudes: false });
  assert.match(cold, /data-account-tab="turnos" href="\/account#turnos" hidden>Mis turnos</);
  const warm = SiteNav.html("/account", "", { ...STATE_ON, loggedIn: true, hasSolicitudes: true });
  assert.match(warm, /data-account-tab="turnos" href="\/account#turnos">Mis turnos</);
  // «Mi cuenta» is marked on its own page (the product owner, 2026-10-17) with
  // the same bar under the words as every other item (demo review 2026-11-15).
  assert.match(warm, /class="stznav-cuenta on"/);
  assert.match(src, /\.stznav-cuenta\.on \.stznav-cuenta-label\{/);
  assert.match(src, /on\(window, "stz:account-tab"/);
  const acc = readFileSync(new URL("../static/platform/account.mjs", import.meta.url), "utf8");
  assert.match(acc, /new CustomEvent\("stz:account-tab"/);
});

test("the collapsed menu places the upload action before the account section", () => {
  assert.match(src, /\.stznav--collapsed \.stznav-btn\{order:2;/);
  assert.match(src, /\.stznav--collapsed\.stznav--account \.stznav-account-section\{display:block;order:3/);
});

test("upload-order.js sigue teniendo un punto de entrada propio (la ficha de estudio)", () => {
  const patientSite = patientSiteSource();
  assert.match(patientSite, /href = f'\/upload-order\?service=/);
  assert.match(patientSite, /Subí tu orden/);
});

test("the upload page title uses the deployment brand", () => {
  const upload = readFileSync(new URL("../app/modules/turnos/upload-order.mjs", import.meta.url), "utf8");
  assert.match(upload, /getAttribute\("data-theme"\) === "pinamar"/);
  assert.match(upload, /\? "Turnos de salud pública en Pinamar" : "Human Rounds"/);
  assert.match(upload, /document\.title = \(title \|\| pageBrand\) \+ " \| " \+ pageBrand/);
  assert.doesNotMatch(upload, /\| Pinamar Turnos/);
});
