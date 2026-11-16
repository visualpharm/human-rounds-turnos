// tests/listing-search-all.test.mjs — a search with no match on a center's page
// ends in a next step, not in an empty box: a link that repeats the same query
// in the all-centers search (remedies rule: the patient gets the solution).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import * as L from "../static/platform/listing.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = readFileSync(join(ROOT, "static/platform/listing.mjs"), "utf8");

test("searchAllHtml: link a la búsqueda general con la misma consulta", () => {
  const html = L.searchAllHtml("pediatría", "/doctors");
  assert.match(html, /href="\/doctors\?q=pediatr%C3%ADa"/);
  assert.match(html, /Buscar «pediatría» en todos los centros/);
});

test("searchAllHtml: escapa HTML y codifica la URL", () => {
  const html = L.searchAllHtml('<b>"x" & y', "/doctors");
  assert.doesNotMatch(html, /<b>/);
  assert.match(html, /q=%3Cb%3E%22x%22%20%26%20y/);
});

test("searchAllHtml: sin consulta o sin destino no dibuja nada", () => {
  assert.equal(L.searchAllHtml("", "/doctors"), "");
  assert.equal(L.searchAllHtml("x", ""), "");
});

test("la tabla vacía y el driver de grupo vacío usan el link", () => {
  assert.match(SRC, /searchAllHtml\(state\.q, opts\.searchAllHref\)/);
  assert.match(SRC, /function groupAllEmpty/);
});
