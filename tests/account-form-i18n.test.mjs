// Real-import tests for static/platform/account-form.mjs. The register /
// login / recover forms and the account editor render in English when the
// catalog says so, in Spanish (unchanged) otherwise, and the date-of-birth
// word-echo assembles from the SHARED date.month_full.* catalog (not a
// fourth local month table) with the right connector words per language
// ("6 August 1957", never "6 de August de 1957").
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as I18n from "../static/platform/i18n.mjs";
import * as Account from "../static/platform/account-form.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = readFileSync(join(ROOT, "static/platform/account-form.mjs"), "utf8");

/** Merge every locales/<lang>/*.json into one flat dict, like i18n.py does. */
function catalogFor(lang) {
  const dir = join(ROOT, "locales", lang);
  const out = {};
  for (const fname of readdirSync(dir).filter((f) => f.endsWith(".json"))) {
    const rows = JSON.parse(readFileSync(join(dir, fname), "utf8"));
    for (const [k, v] of Object.entries(rows)) if (k !== "_comment") out[k] = v;
  }
  return out;
}

async function adoptCatalog(lang) {
  I18n.setCatalog(lang ? catalogFor(lang) : {});
}

/** A fake DOM surface: just enough for injectStyles()/wireDob() to run
 * without throwing — this repo has no jsdom, and Account was never
 * written to need one for its pure logic (parseFlexibleDate, isoEnPalabras
 * via dobField). */
function fakeDocument() {
  const byId = {};
  return {
    getElementById: (id) => byId[id] || null,
    createElement: () => ({}),
    head: { appendChild: (el) => { byId[el.id] = el; } },
    documentElement: { lang: "es" },
  };
}

function fakeInput(value) {
  return { value: value || "", dataset: {}, addEventListener() {}, focus() {} };
}
function fakeEcho() {
  return { className: "", textContent: "" };
}

/** A permissive host: every element lookup a bare miss (renderAuth's wire()
 * guards every one of them with `if (el) ...`, so a uniform null/[] is
 * enough to run the full render()+wire() without a real DOM), and innerHTML
 * captured as plain text so the test can inspect the rendered labels. */
function fakeHost() {
  return {
    _innerHTML: "",
    get innerHTML() { return this._innerHTML; },
    set innerHTML(v) { this._innerHTML = v; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
}

function installHost(lang) {
  const previousWindow = global.window;
  const previousDocument = global.document;
  const previousFetch = global.fetch;
  const document = fakeDocument();
  document.documentElement.lang = lang || "es";
  global.window = { document };
  global.document = document;
  global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  return () => {
    I18n.setCatalog({});
    global.window = previousWindow;
    global.document = previousDocument;
    global.fetch = previousFetch;
  };
}

async function withAccount(lang, run) {
  await adoptCatalog(lang);
  const restore = installHost(lang);
  try {
    return await run(Account);
  } finally {
    restore();
  }
}

async function renderAuthHtml(lang, opts) {
  return withAccount(lang, async (mod) => {
    const host = fakeHost();
    await mod.renderAuth(host, opts || {});
    await new Promise((r) => setImmediate(r));
    return host.innerHTML;
  });
}

test("named exports exist and the retired global is gone", () => {
  for (const name of [
    "renderAuth", "renderEditar", "renderReset",
    "parseFlexibleDate", "isoToArg", "ageFromIso", "dobField", "fileToJpeg",
  ]) {
    assert.equal(typeof Account[name], "function", name);
  }
  assert.equal(typeof Account.otpHtml, "undefined");
  assert.doesNotMatch(SRC, /window\.SanaAccount\s*=/);
  assert.doesNotMatch(SRC, /module\.exports/);
  assert.match(SRC, /export \{/);
});

test("registro en inglés: labels/hint/CTA reales, nada de castellano crudo", async () => {
  const html = await renderAuthHtml("en");
  assert.ok(html.includes(">Create account<"), html);
  assert.ok(html.includes(">First name<"), html);
  assert.ok(html.includes(">Last name<"), html);
  assert.ok(html.includes("Enter at least a phone number or email"), html);
  assert.ok(!html.includes("Necesitás una cuenta"), "no debería quedar castellano sin traducir");
});

test("registro en castellano: byte-identical al texto de siempre", async () => {
  const html = await renderAuthHtml("es");
  assert.ok(html.includes(">Crear cuenta<"), html);
  assert.ok(html.includes(">Nombre<"), html);
  assert.ok(html.includes("Dejá al menos un teléfono o un email"), html);
});

test("shared renderAuth authors native labels for controls and aria-labelledby for composite groups", async () => {
  const html = await renderAuthHtml("es");
  for (const id of [
    "sc-dni", "sc-nombre", "sc-apellido", "sc-nac", "sc-dom-calle", "sc-dom-loc",
    "sc-dom-cp", "sc-contacto", "sc-email", "sc-pass",
  ]) assert.match(html, new RegExp('<label for="' + id + '">'));
  assert.match(html, /<span class="sc-label" id="sc-sexo-label">Sexo/);
  assert.match(html, /id="sc-sexo-seg" role="radiogroup" aria-labelledby="sc-sexo-label"/);
  assert.doesNotMatch(html, /<label for="sc-sexo-seg">/);
  assert.match(SRC, /\.sc-genbtn\{[^}]*min-height:44px/);
});

test("login en inglés: 'Sign in', no 'Ingresar'", async () => {
  const html = await renderAuthHtml("en", { defaultMode: "login" });
  assert.ok(html.includes(">Sign in<"), html);
  assert.ok(html.includes("Forgot your password?"), html);
  assert.ok(!html.includes("Ingresar"), html);
});

test("DOB echo en castellano: dia + de + mes + de + anio, con el mes del catálogo compartido date.*", async () => {
  await withAccount("es", (mod) => {
    const input = fakeInput("28/10/1978");
    const echo = fakeEcho();
    mod.dobField({ input, echo });
    assert.equal(input.value, "28 de octubre de 1978");
  });
});

test("DOB echo en inglés: month name capitalizado, sin 'de' — nunca '28 de October de 1978'", async () => {
  await withAccount("en", (mod) => {
    const input = fakeInput("28/10/1978");
    const echo = fakeEcho();
    mod.dobField({ input, echo });
    assert.equal(input.value, "28 October 1978");
    assert.ok(!input.value.includes(" de "), `no debería llevar "de": ${input.value}`);
  });
});

test("sin catalogo (single-language install): el eco sigue en castellano, nunca una clave cruda ni una plantilla sin sustituir", async () => {
  await withAccount(null, (mod) => {
    const input = fakeInput("28/10/1978");
    const echo = fakeEcho();
    mod.dobField({ input, echo });
    assert.equal(input.value, "28 de octubre de 1978");
    assert.ok(!input.value.includes("{day}"), `no debería quedar sin sustituir: ${input.value}`);
  });
});

test("parseFlexibleDate, isoToArg y ageFromIso no cambiaron", async () => {
  await withAccount("es", (mod) => {
    assert.equal(mod.parseFlexibleDate("28/10/1978"), "1978-10-28");
    assert.equal(mod.isoToArg("1978-10-28"), "28/10/1978");
    assert.equal(typeof mod.ageFromIso("1978-10-28"), "number");
    assert.ok(mod.ageFromIso("1978-10-28") >= 40);
  });
});

test("la fecha inválida tipeada muestra el error EN INGLÉS cuando corresponde (no 'No entendimos la fecha' crudo)", async () => {
  await withAccount("en", (mod) => {
    const input = fakeInput("99/99/9999");
    const echo = fakeEcho();
    mod.dobField({ input, echo });
    assert.match(echo.textContent, /couldn.?t understand/i);
  });
});

test("fileToJpeg is a named export, not a replacement global", () => {
  assert.equal(typeof Account.fileToJpeg, "function");
  assert.doesNotMatch(SRC, /window\.SanaAccount|window\.SanaCuenta/);
});
