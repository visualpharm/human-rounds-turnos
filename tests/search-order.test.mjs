/* search-order.test.mjs — ranking de popularidad de la búsqueda de la home
   (static/platform/search.mjs:ordenarPorPopularidad, product owner 2026-07-12).

   Correr:  node --test tests/search-order.test.mjs   (desde la raíz)

   Caso de aceptación real: buscar "medicina gene" debe rankear "Medicina
   general" plano (3 centros, muchas horas semanales) PRIMERO, antes de "-
   Salud de la mujer" y "- Diversidad" (pocas horas). */
import test from "node:test";
import assert from "node:assert/strict";
import * as B from "../static/platform/search.mjs";

const rows = [
  { nombre: "Medicina general - Salud de la mujer", horas: 4 },
  { nombre: "Medicina general", horas: 20 },          // hospital-pinamar
  { nombre: "Medicina general - Diversidad", horas: 3.5 },
  { nombre: "Medicina general", horas: 18 },          // caps-ostende
  { nombre: "Medicina general", horas: 15 },          // caps-valeria
];

test("grupo plano (sin sufijo) va primero y queda contiguo", () => {
  const out = B.ordenarPorPopularidad(rows);
  const nombres = out.map((r) => r.nombre);
  assert.equal(nombres[0], "Medicina general");
  assert.equal(nombres[1], "Medicina general");
  assert.equal(nombres[2], "Medicina general");
  // los sufijados quedan después, no intercalados
  assert.ok(nombres.slice(3).every((n) => n !== "Medicina general"));
});

test("dentro del grupo, ordena por horas propias desc", () => {
  const out = B.ordenarPorPopularidad(rows).filter((r) => r.nombre === "Medicina general");
  assert.deepEqual(out.map((r) => r.horas), [20, 18, 15]);
});

test("estable en empates (conserva orden de entrada)", () => {
  const empatadas = [{ nombre: "X", horas: 5 }, { nombre: "X", horas: 5 }];
  const out = B.ordenarPorPopularidad(empatadas);
  assert.equal(out[0], empatadas[0]);
  assert.equal(out[1], empatadas[1]);
});

test("normaliza nombre (acentos/mayúsculas/espacios) para agrupar", () => {
  const out = B.ordenarPorPopularidad([
    { nombre: "Medicina  General", horas: 1 },
    { nombre: "medicina general", horas: 2 },
  ]);
  assert.equal(out[0].horas, 2); // mismo grupo, ordenado por horas
});
