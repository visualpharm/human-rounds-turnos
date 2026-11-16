/* search-match.test.mjs — match + relevancia de la búsqueda de la home
   (static/platform/search.mjs:buscar — estudio de búsqueda 2026-07-16).

   Correr:  node --test tests/search-match.test.mjs   (desde la raíz)

   Dos partes: unitarios del stemming/tiers con filas sintéticas, y casos de
   aceptación contra webdata/servicios.json REAL (los bugs que encontró la
   batería: "gine" mostraba Medicina general primero, "ginecólogo" daba cero). */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as B from "../static/platform/search.mjs";

// ---- unitarios: variantes morfológicas -------------------------------------

test("profesión → especialidad: -ólogo/-óloga stemmea a -olog", () => {
  assert.ok(B.variantes("ginecologo").includes("ginecolog"));
  assert.ok(B.variantes("cardiologa").includes("cardiolog"));
  assert.ok(B.variantes("urologo").includes("urolog")); // prefijo corto (ur-)
});

test("especialidad → profesión: -ología también stemmea (el dato puede decir Cardiólogo)", () => {
  assert.ok(B.variantes("cardiologia").includes("cardiolog"));
  assert.ok(B.variantes("psiquiatria").includes("psiquiatr"));
});

test("-iatra, -grafía/-grafista e irregulares", () => {
  assert.ok(B.variantes("pediatra").includes("pediatr"));
  assert.ok(B.variantes("ecografia").includes("ecograf"));
  assert.ok(B.variantes("ecografista").includes("ecograf"));
  assert.ok(B.variantes("obstetra").includes("obstetr"));   // ⊂ "obstetricia"
  assert.ok(B.variantes("diabetes").includes("diabet"));    // ⊂ "diabetología"
});

test("plural → singular como variante extra", () => {
  assert.ok(B.variantes("vacunas").includes("vacuna"));
  assert.ok(B.variantes("ojos").includes("ojo"));
});

// ---- unitarios: tiers de relevancia ----------------------------------------

const filas = [
  { nombre: "Medicina general", horas: 36, palabras: ["ginecología", "pediatra"] },
  { nombre: "Ginecología", horas: 4, palabras: [] },
  { nombre: "Electrocardiograma", horas: 147, palabras: [] },
  { nombre: "Cardiología", horas: 9, palabras: [] },
  { nombre: "Pediatría", horas: 10, palabras: [] },
  { nombre: "Demanda espontánea pediatría", horas: 23, palabras: [] },
];
const campos = (s) => ({ principal: [s.nombre], resto: s.palabras });

test("match en el nombre gana a match en palabras aunque tenga menos horas", () => {
  const out = B.buscar(filas, "gine", campos).map((r) => r.nombre);
  assert.equal(out[0], "Ginecología");
  assert.ok(out.includes("Medicina general")); // sigue apareciendo, después
});

// buscarConPuntaje (2026-07-22, admin-reports.js "Búsquedas"): igual que
// buscar() pero expone el puntaje de cada fila — lo usa el reporte admin
// para comparar el mejor match de servicios contra el de médicos.
test("buscarConPuntaje expone {row,score} y ordena por puntaje asc (menor = más relevante)", () => {
  const out = B.buscarConPuntaje(filas, "gine", campos);
  assert.equal(out[0].row.nombre, "Ginecología");
  assert.equal(out[0].score, 0); // el nombre EMPIEZA con "gine"
  const medicina = out.find((r) => r.row.nombre === "Medicina general");
  assert.ok(medicina.score > out[0].score); // matchea por palabras, no por nombre
});

test("buscarConPuntaje devuelve [] si ninguna fila matchea", () => {
  assert.deepEqual(B.buscarConPuntaje(filas, "xyzxyz", campos), []);
});

test("nombre que EMPIEZA con la palabra gana al substring (cardio)", () => {
  const out = B.buscar(filas, "cardio", campos).map((r) => r.nombre);
  assert.equal(out[0], "Cardiología");
  assert.equal(out[1], "Electrocardiograma");
});

test("empieza-con gana a arranque de otra palabra del nombre (pediatra)", () => {
  const out = B.buscar(filas, "pediatra", campos).map((r) => r.nombre);
  assert.equal(out[0], "Pediatría");
  assert.equal(out[1], "Demanda espontánea pediatría");
});

test("todas las palabras del query tienen que matchear (AND)", () => {
  assert.equal(B.buscar(filas, "cardiología inexistente", campos).length, 0);
});

test("stopwords y dr/dra no cuentan como palabras del query", () => {
  const meds = [{ nombre: "Matias Moyano", horas: 1, palabras: [] }];
  const out = B.buscar(meds, "dra moyano", campos);
  assert.equal(out.length, 1);
});

test('meta-palabras del trámite se ignoran si hay algo más ("turno con cardiologo")', () => {
  const out = B.buscar(filas, "turno con cardiologo", campos).map((r) => r.nombre);
  assert.equal(out[0], "Cardiología");
});

test('"turno" a secas sigue siendo una búsqueda literal', () => {
  const conTurno = [{ nombre: "Turnos protegidos", horas: 1, palabras: [] }];
  assert.equal(B.buscar(conTurno, "turno", campos).length, 1);
  assert.equal(B.buscar(filas, "turno", campos).length, 0);
});

test('adjetivo↔sustantivo: "medico clinico" encuentra "Clínica Médica"', () => {
  const cm = [{ nombre: "Clínica Médica", horas: 3, palabras: [] }];
  assert.equal(B.buscar(cm, "medico clinico", campos).length, 1);
});

// ---- acceptance against the shipped public example catalog -----------------

const SERV = JSON.parse(readFileSync(new URL("../packs/example-town/servicios.json", import.meta.url)))
  .servicios;
const camposServ = (s) => ({
  principal: [s.nombre],
  resto: [s.categoria, s.sede, s.horario].concat(s.palabras || []),
  excluidas: s.palabras_excluidas || [],
});
const buscar = (q) => B.buscar(SERV, q, camposServ).map((r) => r.nombre);

test('“gine” → Ginecología primero, no Medicina general', () => {
  assert.match(buscar("gine")[0], /^Ginecología/);
});

test('“ginecólogo” encuentra Ginecología (antes daba CERO)', () => {
  const out = buscar("ginecólogo");
  assert.ok(out.length > 0);
  assert.match(out[0], /^Ginecología/);
});

test('“cardiólogo” y “traumatólogo” encuentran su especialidad', () => {
  assert.match(buscar("cardiólogo")[0], /^Cardiología/);
  assert.match(buscar("traumatólogo")[0], /^Traumatología/);
});

test('“trauma” → Traumatología first', () => {
  assert.match(buscar("trauma")[0], /^Traumatología/);
});

test('“obstetra” → the combined obstetrics service first', () => {
  assert.equal(buscar("obstetra")[0], "Ginecología y Obstetricia");
});

test('“diabetes” → a diabetes-specific service first', () => {
  assert.match(buscar("diabetes")[0], /diabetes/i);
});

test('“vacuna” → Vacunación first', () => {
  assert.equal(buscar("vacuna")[0], "Vacunación");
});

test('“ojos” finds Oftalmología through the public example keywords', () => {
  assert.equal(buscar("ojos")[0], "Oftalmología");
});

test("sin duplicados stale: un solo servicio de ansiedad / adicciones", () => {
  assert.equal(SERV.filter((s) => /ansiedad/i.test(s.nombre)).length, 1);
  assert.equal(SERV.filter((s) => /adicciones/i.test(s.nombre)).length, 1);
});

test("the public example catalog's search examples return results", () => {
  for (const q of ["Cardiología", "Ecografía", "Laboratorio", "Vacunas", "Psicología"]) {
    assert.ok(B.buscar(SERV, q, camposServ).length > 0, q + " sin resultados");
  }
});

// ---- buscarFrase: la práctica de una orden real, con la letra del médico ----

test('buscarFrase: "Ergometría de 12 derivaciones" → el servicio Ergometría PRIMERO', () => {
  // buscar() a secas da cero (AND con "derivaciones") y la degradación
  // ingenua ("primer nivel no vacío") se frenaba en "Ergometría de 12", que
  // matchea Cardiología por la keyword "ergometría" en `palabras` + "12" en
  // el horario — enterrando al match por NOMBRE. buscarFrase elige el nivel
  // con mejor tier promedio (2026-07-16, verificación en navegador).
  const out = B.buscarFrase(SERV, "Ergometría de 12 derivaciones", camposServ)
    .map((r) => r.nombre);
  assert.ok(out.length > 0, "sin resultados");
  assert.match(out[0], /^Ergometría/);
});

test("buscarFrase: una frase sin nada del catálogo devuelve []", () => {
  assert.deepEqual(B.buscarFrase(SERV, "trámite totalmente inexistente xyz", camposServ), []);
});

test("buscarFrase: query simple se comporta como buscar()", () => {
  assert.deepEqual(
    B.buscarFrase(SERV, "Cardiología", camposServ).map((r) => r.id),
    B.buscar(SERV, "Cardiología", camposServ).map((r) => r.id));
});

// ---- palabras excluidas (reunión municipal 2026-07-17) ---------------------
// "Podés elegir que su consultorio no aparezca a PAP": una fila con
// `palabras_excluidas` desaparece de ESA búsqueda y de ninguna otra.

const ROWS_EXC = [
  { nombre: "Ginecología", horas: 10, palabras: ["mujer"], palabras_excluidas: ["PAP"] },
  { nombre: "PAP", horas: 5, palabras: ["papanicolaou"] },
];

test("palabras_excluidas: la fila no aparece en la búsqueda excluida", () => {
  const out = B.buscar(ROWS_EXC, "pap", camposServ).map((r) => r.nombre);
  assert.deepEqual(out, ["PAP"]);
});

test("palabras_excluidas: el resto de las búsquedas no cambia", () => {
  assert.equal(B.buscar(ROWS_EXC, "ginecología", camposServ)[0].nombre, "Ginecología");
  assert.equal(B.buscar(ROWS_EXC, "mujer", camposServ)[0].nombre, "Ginecología");
});

test("exclusión por prefijo en las dos direcciones: 'pap' también tapa 'papanicolaou'", () => {
  const out = B.buscar(ROWS_EXC, "papanicolaou", camposServ).map((r) => r.nombre);
  assert.deepEqual(out, ["PAP"]);
});

test("una palabra excluida tapa la fila aunque las demás palabras matcheen", () => {
  // "ginecología pap": la ginecóloga matchea por nombre, pero "pap" está
  // excluido → fuera. (La fila PAP tampoco matchea "ginecología" — AND.)
  const nombres = B.buscar(ROWS_EXC, "ginecología pap", camposServ).map((r) => r.nombre);
  assert.ok(!nombres.includes("Ginecología"));
});

test("buscarFrase también respeta las exclusiones", () => {
  // La frase degrada por la cola hasta "PAP" solo: la ginecóloga excluida
  // no puede reaparecer por ningún nivel de degradación.
  const out = B.buscarFrase(ROWS_EXC, "PAP de control", camposServ).map((r) => r.nombre);
  assert.ok(out.includes("PAP"));
  assert.ok(!out.includes("Ginecología"));
});

test("un query de 1-2 letras no dispara la exclusión (dirección query⊂exclusión)", () => {
  // "pa" es prefijo de "pap" pero con <3 letras no tapa nada; matchea
  // "palabras"/"papanicolaou" por substring como siempre.
  const rows = [{ nombre: "Ginecología", palabras: ["patología"], palabras_excluidas: ["pap"] }];
  assert.equal(B.buscar(rows, "pa", camposServ).length, 1);
});

// ---- exclusión por FRASE (contraparte técnica, 2026-10-19: «puse fiebre, tos y
// telemedicina no aparecía como primera») -------------------------------------
// Las exclusiones de guardia son frases («fiebre con confusión», «tose
// sangre»). Una frase tapa la fila sólo cuando el query la dice entera; una
// palabra suelta del query que apenas EMPIEZA una frase no la tapa.

const TELE = [{
  nombre: "Telemedicina",
  palabras: ["fiebre", "tos", "dolor de cabeza"],
  palabras_excluidas: ["fiebre con confusión", "tose sangre", "dolor de pecho", "pap"],
}];

test("una frase excluida no tapa la palabra suelta con la que empieza", () => {
  assert.equal(B.buscar(TELE, "fiebre", camposServ).length, 1);
  assert.equal(B.buscar(TELE, "tos", camposServ).length, 1);
  assert.equal(B.buscar(TELE, "fiebre, tos", camposServ).length, 1);
  assert.equal(B.buscar(TELE, "fiebre tos", camposServ).length, 1);
});

test("la frase excluida dicha entera sí tapa la fila", () => {
  assert.equal(B.buscar(TELE, "fiebre con confusión", camposServ).length, 0);
  assert.equal(B.buscar(TELE, "mi hijo tose sangre", camposServ).length, 0);
  assert.equal(B.buscar(TELE, "dolor de pecho fuerte", camposServ).length, 0);
});

test("una exclusión de una sola palabra sigue tapando por prefijo", () => {
  assert.equal(B.buscar(TELE, "pap", camposServ).length, 0);
  assert.equal(B.buscar(TELE, "papanicolaou", camposServ).length, 0);
});

test("native import exposes the public search functions and no SanaSearch global", () => {
  assert.equal(typeof B.buscar, "function");
  assert.equal(typeof B.buscarConPuntaje, "function");
  assert.equal(typeof B.buscarFrase, "function");
  assert.equal(typeof B.ordenarPorPopularidad, "function");
  assert.equal(typeof B.sugerirQuery, "function");
  assert.equal(typeof B.servRow, "function");
  assert.equal(typeof globalThis.SanaSearch, "undefined");
});
