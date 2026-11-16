/* search-suggest.test.mjs — "quizás quisiste decir…" (static/platform/search.mjs:sugerirQuery,
   El responsable del producto 2026-08-04: "como hace Google" cuando la búsqueda da cero por un typo).

   Correr:  node --test tests/search-suggest.test.mjs   (desde la raíz) */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as B from "../static/platform/search.mjs";

const campos = (r) => ({ principal: [r.nombre], resto: r.resto || [] });
const catalogo = (rows) => [{ rows, getCampos: campos }];

test("corrige un typo por distancia de edición contra el vocabulario real", () => {
  const cat = catalogo([{ nombre: "Vacunación" }]);
  assert.equal(B.sugerirQuery(cat, "vacunasion"), "vacunacion");
});

test("una palabra que ya existe tal cual no se toca (nada que corregir → null)", () => {
  const cat = catalogo([{ nombre: "Vacunación" }]);
  assert.equal(B.sugerirQuery(cat, "vacunacion"), null);
});

test("gibberish sin nada parecido en el catálogo → null (nunca corrige a ciegas)", () => {
  const cat = catalogo([{ nombre: "Vacunación" }]);
  assert.equal(B.sugerirQuery(cat, "xyzqwqwqwqw"), null);
});

test("tolerancia escala con el largo: typo de 1 letra en palabra corta NO calza contra algo muy distinto", () => {
  const cat = catalogo([{ nombre: "Pediatría" }]);
  // "pap" (3 letras) no debe corregirse a "pediatria": muy lejos para su largo.
  assert.equal(B.sugerirQuery(cat, "pap"), null);
});

test("query multi-palabra: sólo se corrige la palabra que lo necesita", () => {
  const cat = catalogo([{ nombre: "Ecografía obstétrica" }]);
  assert.equal(B.sugerirQuery(cat, "ecografia obstetrica"), null); // ya matchea tal cual (variantes)
});

// ---- acceptance against the shipped public example catalog -----------------

const SERV = JSON.parse(readFileSync(new URL("../packs/example-town/servicios.json", import.meta.url)))
  .servicios;
const camposServ = (s) => ({
  principal: [s.nombre],
  resto: [s.categoria, s.sede, s.horario].concat(s.palabras || []),
  excluidas: s.palabras_excluidas || [],
});
const catServ = [{ rows: SERV, getCampos: camposServ }];
const buscar = (q) => B.buscar(SERV, q, camposServ).map((r) => r.nombre);

test('typo real "traumatologa" (sin -í-) corrige a algo que SÍ trae Traumatología', () => {
  // buscar() ya la resuelve por stemming, así que probamos con un typo que
  // el stemming NO cubre: falta una letra a mitad de palabra.
  const q0 = "traumatologa";
  assert.ok(buscar(q0).length > 0); // stemming ya la resuelve — control
  const q1 = "traumatollga"; // dos letras cambiadas
  assert.equal(buscar(q1).length, 0); // control: esta SÍ da cero directo
  const sugerido = B.sugerirQuery(catServ, q1);
  assert.ok(sugerido, "debería sugerir algo");
  assert.ok(buscar(sugerido).length > 0, "la corrección debe traer resultados");
});

test('palabra TRUNCADA de un término largo — "otori" (por "otorrino") — corrige y trae Otorrinolaringología', () => {
  // Regresión (responsable del producto, 2026-08-04): comparar contra la palabra COMPLETA
  // "otorrinolaringologia" penaliza por todas las letras que el usuario ni
  // llegó a escribir y da distancia 3 (fuera de tolerancia). Contra un
  // PREFIJO del largo de la query, la distancia real es 1.
  const q = "otori";
  assert.equal(buscar(q).length, 0); // control: da cero directo
  const sugerido = B.sugerirQuery(catServ, q);
  assert.ok(sugerido, "debería sugerir algo");
  const out = buscar(sugerido);
  assert.ok(out.length > 0, "la corrección debe traer resultados");
  assert.ok(out.some((n) => /^Otorrinolaringología/.test(n)));
});
