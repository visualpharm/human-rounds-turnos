/* search-initials.test.mjs — iniciales a través de palabras del NOMBRE
   (static/platform/search.mjs, tier 5). The product owner, 2026-10-21: «IB debe
   encontrar Ilya Belov» (ambos en cirílico) — un query de iniciales matchea la corrida
   CONSECUTIVA de iniciales de las palabras del nombre, en el MISMO
   alfabeto (sin transliteración), y SIEMPRE por debajo de un match
   exacto/prefijo/substring existente. Filas sintéticas, sin datos reales.

   Correr:  node --test tests/search-initials.test.mjs   (desde la raíz) */
import test from "node:test";
import assert from "node:assert/strict";
import * as B from "../static/platform/search.mjs";

// Cyrillic rows are \u-escaped (no Cyrillic literals outside locales/ru|uk):
// Ilya Belov, Ilya Petrovich Belov, Irina Borisovna Volkova.
const filas = [
  { nombre: "Ilya Belov", horas: 5, palabras: [] },
  { nombre: "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432", horas: 5, palabras: [] },
  { nombre: "\u0418\u043b\u044c\u044f \u041f\u0435\u0442\u0440\u043e\u0432\u0438\u0447 \u0411\u0435\u043b\u043e\u0432", horas: 4, palabras: [] },
  { nombre: "\u0418\u0440\u0438\u043d\u0430 \u0411\u043e\u0440\u0438\u0441\u043e\u0432\u043d\u0430 \u0412\u043e\u043b\u043a\u043e\u0432\u0430", horas: 9, palabras: [] },
  { nombre: "Éric Böhm", horas: 2, palabras: [] },
];
const campos = (s) => ({ principal: [s.nombre], resto: s.palabras });
const nombres = (rows, q) => B.buscar(rows, q, campos).map((r) => r.nombre);

test("«IB» (cirílico) encuentra «Ilya Belov» (cirílico)", () => {
  // También trae «Irina Borisovna Volkova» (cirílico): sus primeras DOS palabras son
  // la corrida consecutiva «ib» (cirílico). Orden por horas dentro del mismo tier.
  assert.deepEqual(nombres(filas, "\u0418\u0411"), ["\u0418\u0440\u0438\u043d\u0430 \u0411\u043e\u0440\u0438\u0441\u043e\u0432\u043d\u0430 \u0412\u043e\u043b\u043a\u043e\u0432\u0430", "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"]);
});

test("«ib» (latín) encuentra «Ilya Belov»", () => {
  assert.deepEqual(nombres(filas, "ib"), ["Ilya Belov"]);
});

test("sin transliteración: las iniciales cirílicas no matchean nombres latinos ni al revés", () => {
  assert.equal(nombres(filas, "\u0418\u0411").includes("Ilya Belov"), false);
  assert.equal(nombres(filas, "ib").includes("\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"), false);
});

test("case-insensitive y sin acentos: «iB» vale como «ib» (cirílico), «EB» como «eb»", () => {
  assert.ok(nombres(filas, "\u0438\u0411").includes("\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"));
  assert.ok(nombres(filas, "EB").includes("Éric Böhm")); // É→e, ö→o en ambos lados
});

test("sólo corridas CONSECUTIVAS: «ib» (cirílico) no matchea «Ilya Petrovich Belov»", () => {
  const out = nombres(filas, "\u0438\u0431");
  assert.ok(out.includes("\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"));
  assert.equal(out.includes("\u0418\u043b\u044c\u044f \u041f\u0435\u0442\u0440\u043e\u0432\u0438\u0447 \u0411\u0435\u043b\u043e\u0432"), false);
});

test("las iniciales de CUALQUIER corrida consecutiva valen (cirílico «ipb», «pb», «ibv»)", () => {
  assert.deepEqual(nombres(filas, "\u0438\u043f\u0431"), ["\u0418\u043b\u044c\u044f \u041f\u0435\u0442\u0440\u043e\u0432\u0438\u0447 \u0411\u0435\u043b\u043e\u0432"]);
  assert.deepEqual(nombres(filas, "\u043f\u0431"), ["\u0418\u043b\u044c\u044f \u041f\u0435\u0442\u0440\u043e\u0432\u0438\u0447 \u0411\u0435\u043b\u043e\u0432"]);
  assert.deepEqual(nombres(filas, "\u0438\u0431\u0432"), ["\u0418\u0440\u0438\u043d\u0430 \u0411\u043e\u0440\u0438\u0441\u043e\u0432\u043d\u0430 \u0412\u043e\u043b\u043a\u043e\u0432\u0430"]);
});

test("una sola letra NUNCA es un match de iniciales (sigue siendo el substring de siempre)", () => {
  // "i" (cirílico) matchea sólo a las filas cuyo nombre CONTIENE la letra (tier 2,
  // comportamiento previo); un tier de iniciales de 1 letra no agrega nadie:
  // "Éric Böhm" (sin esa letra) queda afuera igual que antes.
  const out = nombres(filas, "\u0438");
  assert.equal(out.includes("\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"), true);
  assert.equal(out.includes("Éric Böhm"), false);
  assert.equal(out.includes("Ilya Belov"), false);
});

test("el match de iniciales es el tier MÁS BAJO (5): exacto/prefijo/substring siguen ganando", () => {
  const score = (rows, q, nombre) =>
    B.buscarConPuntaje(rows, q, campos).find((r) => r.row.nombre === nombre)?.score;
  // Latín: el nombre completo dicho entero = tier 0 ("ivan" arranca el
  // nombre) + tier 1 ("belov" arranca la segunda palabra).
  assert.equal(score(filas, "ilya belov", "Ilya Belov"), 1);
  // Cirílico: los tiers de arranque de palabra (bordes ASCII) no lo ven,
  // matchea por substring 2+2=4 — y AÚN ASÍ queda por debajo del tier de
  // iniciales (5): el nombre dicho entero siempre gana a las iniciales.
  assert.equal(score(filas, "\u0438\u043b\u044c\u044f \u0431\u0435\u043b\u043e\u0432", "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"), 4);
  // Query de iniciales "ib" (cirílico): tier 5 — peor que cualquier match preexistente.
  assert.equal(score(filas, "\u0438\u0431", "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"), 5);
  assert.ok(score(filas, "\u0438\u043b\u044c\u044f \u0431\u0435\u043b\u043e\u0432", "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432") < score(filas, "\u0438\u0431", "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"));
  // Un substring del nombre (tier 2) outrankea las iniciales del otro.
  const conSubstring = filas.concat([{ nombre: "Cribado cardiovascular", horas: 1, palabras: [] }]);
  const out = nombres(conSubstring, "ib");
  assert.deepEqual(out, ["Cribado cardiovascular", "Ilya Belov"]); // "ib" ⊂ "cribado"
});

test("dentro del tier de iniciales manda la popularidad (horas)", () => {
  const dos = [
    { nombre: "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432", horas: 2, palabras: [] },
    { nombre: "\u0418\u0440\u0438\u043d\u0430 \u0411\u043e\u0440\u0438\u0441\u043e\u0432\u0430", horas: 9, palabras: [] },
  ];
  assert.deepEqual(nombres(dos, "\u0438\u0431"), ["\u0418\u0440\u0438\u043d\u0430 \u0411\u043e\u0440\u0438\u0441\u043e\u0432\u0430", "\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432"]);
});

test("el matching de siempre queda intacto: apellido suelto, prefijo, AND", () => {
  assert.deepEqual(nombres(filas, "\u0431\u0435\u043b\u043e\u0432"), ["\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432", "\u0418\u043b\u044c\u044f \u041f\u0435\u0442\u0440\u043e\u0432\u0438\u0447 \u0411\u0435\u043b\u043e\u0432"]);
  assert.deepEqual(nombres(filas, "belov"), ["Ilya Belov"]);
  assert.equal(nombres(filas, "\u0438\u043b\u044c\u044f inexistente").length, 0);
});

test("las iniciales participan del AND como una palabra más del query", () => {
  const conEsp = [
    { nombre: "Ilya Belov", horas: 5, palabras: ["cardiología"] },
  ];
  // "ib cardiologo": tier 5 + tier 3 ("cardiolog" arranca palabra del resto) = 8.
  const out = B.buscarConPuntaje(conEsp, "ib cardiologo", campos);
  assert.equal(out.length, 1);
  assert.equal(out[0].score, 8);
});


test('initials use Unicode word boundaries, never symbols or emoji', () => {
  const rows = [
    {nombre:'\u0418\u043b\u044c\u044f—\u0411\u0435\u043b\u043e\u0432',horas:1},
    {nombre:'✦ Éric Böhm',horas:1},
    {nombre:'Ilya-Belov',horas:1},
  ];
  assert.deepEqual(nombres(rows,'\u0418\u0411'), ['\u0418\u043b\u044c\u044f—\u0411\u0435\u043b\u043e\u0432']);
  assert.deepEqual(nombres(rows,'eb'), ['✦ Éric Böhm']);
  assert.deepEqual(nombres(rows,'ib'), ['Ilya-Belov']);
});
