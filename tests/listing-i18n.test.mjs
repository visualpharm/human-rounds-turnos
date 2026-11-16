// tests/listing-i18n.test.mjs — static/platform/listing.mjs with the real
// catalog of another language (responsable del producto, 2026-08-05). Nace del defecto que sólo
// aparece cuando dos PRs del batch de i18n se integran: PR14 vacía
// date.article para inglés (correcto, el inglés no lleva artículo), PR13 arma
// la pastilla de turno pegando article + " " + día a mano — cada uno solo,
// correcto; juntos, el inglés queda con doble espacio ("Sacar" -> "Book
// Tuesday"). El fix real fue mover esa frase a UNA entrada de catálogo
// (date.weekday_with_article) en vez de parchear el string final; este test
// la ejercita con el catálogo real, no con un T() mockeado (que no
// reproduciría el bug: el fallback castellano no se movió).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import * as I18n from "../static/platform/i18n.mjs";
import * as L from "../static/platform/listing.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function loadCatalog(lang) {
  const dir = join(ROOT, "locales", lang);
  const catalog = {};
  for (const fname of readdirSync(dir).filter((f) => f.endsWith(".json"))) {
    const rows = JSON.parse(readFileSync(join(dir, fname), "utf8"));
    for (const [k, v] of Object.entries(rows)) if (k !== "_comment") catalog[k] = v;
  }
  return catalog;
}

function withLang(lang, run) {
  const catalog = loadCatalog(lang);
  I18n.setCatalog(catalog);
  try {
    run(L);
  } finally {
    I18n.setCatalog({});
  }
}

test("timePill en inglés: sin doble espacio (día de semana con artículo vacío)", () => {
  withLang("en", (mod) => {
    const html = mod.timePill("semana", "Tuesday", "July 14", "3:00 PM", "/service/x/book");
    assert.doesNotMatch(html, /  /, `doble espacio en la pastilla: ${html}`);
    assert.match(html, />Book Tuesday July 14, 3:00 PM<\/a>$/);
  });
});

test("timePill en inglés: día relativo (Today/Tomorrow) tampoco duplica espacios", () => {
  withLang("en", (mod) => {
    const html = mod.timePill("ya", "Tomorrow", "", "08:00", "#");
    assert.doesNotMatch(html, /  /, `doble espacio en la pastilla: ${html}`);
    assert.match(html, />Book tomorrow, 08:00<\/a>$/);
  });
});

test("timePill en castellano sigue diciendo 'Sacar el martes...' (no se movió)", () => {
  withLang("es", (mod) => {
    const html = mod.timePill("semana", "Martes", "14 de julio", "15:00", "/servicio/x/turno");
    assert.match(html, />Sacar el martes 14 de julio, 15:00<\/a>$/);
    assert.doesNotMatch(html, /  /, `doble espacio en la pastilla: ${html}`);
  });
});

test("withWeekdayArticle en inglés: capitaliza siempre, sin artículo", () => {
  withLang("en", (mod) => {
    assert.equal(mod.withWeekdayArticle("Tuesday"), "Tuesday");
    assert.equal(mod.withWeekdayArticle("Saturday"), "Saturday");
  });
});

test("withWeekdayArticle en castellano: antepone 'el' y baja el día", () => {
  withLang("es", (mod) => {
    assert.equal(mod.withWeekdayArticle("Martes"), "el martes");
    assert.equal(mod.withWeekdayArticle("Domingo"), "el domingo");
  });
});
