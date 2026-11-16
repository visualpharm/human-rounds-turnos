// nav-lang-switch.test.mjs — nav.js's langSwitchHtml(): the language links
// live INSIDE .stznav-menu, last, on the same line as the rest of the menu
// items (responsable del producto, 2026-08-01 — revoca la sub-fila propia de la fase 3a), and
// render only when window.SANA_I18N says mode="nav" and >1 language is
// enabled. Every other combination (absent, mode!=="nav", <2 languages) must
// render EXACTLY the pre-i18n markup — that's the "zero behavior change on
// every other page" contract the module comment makes.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as Nav from "../static/platform/nav.mjs";

function loadNav(sanaI18n) {
  globalThis.window = {};
  if (sanaI18n !== undefined) window.SANA_I18N = sanaI18n;
  return Nav;
}

const STATE = { loggedIn: false, hasSolicitudes: false, iaDown: true, iniciales: "", authMode: "password" };

test("sin window.SANA_I18N no agrega el switcher", () => {
  const SiteNav = loadNav(undefined);
  const html = SiteNav.html("/about.html", "", STATE);
  assert.ok(!html.includes("stznav-langs"));
});

test('con mode "off" no agrega el switcher aunque haya 2+ idiomas', () => {
  const SiteNav = loadNav({ active: "es", enabled: ["es", "en"], mode: "off", default: "es" });
  const html = SiteNav.html("/about.html", "", STATE);
  assert.ok(!html.includes("stznav-langs"));
});

test("con un solo idioma habilitado no agrega el switcher aunque mode sea nav", () => {
  const SiteNav = loadNav({ active: "es", enabled: ["es"], mode: "nav", default: "es" });
  const html = SiteNav.html("/about.html", "", STATE);
  assert.ok(!html.includes("stznav-langs"));
});

test('nav mode renders one compact select inside the menu, with all enabled languages', () => {
  const SiteNav = loadNav({ active: "es", enabled: ["es", "en"], mode: "nav", default: "es" });
  const html = SiteNav.html("/about.html", "", STATE);
  assert.ok(html.includes('<div class="stznav-langs">'));
  assert.match(html, /<option value="es" lang="es" selected>español<\/option>/);
  assert.match(html, /<option value="en" lang="en">English<\/option>/);
  assert.equal((html.match(/<select /g) || []).length, 1);
  assert.doesNotMatch(html, /class="stznav-link stznav-lang/);
  // HERMANO del menú, no hijo: si viviera adentro, cada vez que layout()
  // colapsa la barra (labels largos + 10 idiomas pasa hasta en 1440) el
  // switcher se escondía con el burger y el visitante perdía el idioma de
  // un vistazo. Afuera queda en la barra a TODO ancho, entre el menú y el
  // burger (the product owner, 2026-09-16).
  const menuEnd = html.indexOf("</nav>");
  const switchStart = html.indexOf('<div class="stznav-langs">');
  const burgerStart = html.indexOf('class="stznav-burger"');
  assert.ok(switchStart > menuEnd, "el switcher va después del menú, fuera de él");
  assert.ok(burgerStart > switchStart, "el switcher va antes del burger, ambos en la barra");
  assert.match(html, /<div class="stznav-langs"><select[^>]*aria-label="Idioma"/);
});

test("all ten languages remain available without a horizontal code row", () => {
  const codes = ["es", "en", "pt", "fr", "it", "de", "ru", "uk", "ja", "zh"];
  const SiteNav = loadNav({ active: "es", enabled: codes, mode: "nav", default: "es" });
  const html = SiteNav.html("/about.html", "", STATE);
  assert.equal((html.match(/<option /g) || []).length, codes.length);
  for (const code of codes) assert.ok(html.includes('value="' + code + '"'));
});

test("the active language is selected", () => {
  const SiteNav = loadNav({ active: "en", enabled: ["es", "en"], mode: "nav", default: "es" });
  const html = SiteNav.html("/about.html", "", STATE);
  assert.match(html, /<option value="en" lang="en" selected>English<\/option>/);
  assert.equal((html.match(/ selected/g) || []).length, 1);
});
