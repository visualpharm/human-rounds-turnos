// tests/age.test.mjs — ruteo por edad (Sana Turnos). Correr: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import * as edad from "../static/platform/age.mjs";
import { setCatalog } from "../static/platform/i18n.mjs";
const servicios = [
  { id: "child-imaging", nombre: "Ecografía infantil", edad: { min: null, max: 15 }, categoria: "imaging", centro: "example-hospital" },
  { id: "adult-imaging", nombre: "Ecografía", edad: { min: null, max: null }, categoria: "imaging", centro: "example-hospital" },
  { id: "older-adult-care", nombre: "Geriatría", edad: { min: 60, max: null }, categoria: "medicine", centro: "example-hospital" },
  { id: "cardiology", nombre: "Cardiología", edad: { min: null, max: null }, categoria: "medicine", centro: "example-hospital" },
  { id: "adult-psychiatry", nombre: "Psiquiatría", edad: { min: 16, max: null }, categoria: "mental-health", centro: "example-center" },
  { id: "child-psychiatry", nombre: "Psiquiatría infantil", edad: { min: null, max: 15 }, categoria: "mental-health", centro: "example-center" },
  { id: "general-medicine", nombre: "Medicina general", edad: { min: null, max: null }, categoria: "medicine", centro: "example-hospital" },
];
const HOY = new Date("2026-07-08T12:00:00");

const byId = (id) => servicios.find((s) => s.id === id);

test("edadDesde: años cumplidos, cumpleaños ya pasado", () => {
  assert.equal(edad.edadDesde("2000-01-15", HOY), 26);
});
test("edadDesde: cumpleaños todavía no cumplido este año", () => {
  assert.equal(edad.edadDesde("2000-12-31", HOY), 25);
});
test("edadDesde: nacido hoy => 0", () => {
  assert.equal(edad.edadDesde("2026-07-08", HOY), 0);
});
test("edadDesde: entrada inválida => null", () => {
  assert.equal(edad.edadDesde("", HOY), null);
  assert.equal(edad.edadDesde("no-es-fecha", HOY), null);
});

test("aptoPorEdad: sin edad conocida no bloquea", () => {
  const r = edad.aptoPorEdad(byId("child-imaging"), null);
  assert.equal(r.ok, true);
});
test("aptoPorEdad: adulto NO apto para servicio infantil (max 15)", () => {
  const r = edad.aptoPorEdad(byId("child-imaging"), 40);
  assert.equal(r.ok, false);
  assert.match(r.motivo, /15/);
});
test("aptoPorEdad: chico SÍ apto para servicio infantil", () => {
  const r = edad.aptoPorEdad(byId("child-imaging"), 10);
  assert.equal(r.ok, true);
});
test("aptoPorEdad: joven NO apto para servicio con mínimo (Geriatría 60+)", () => {
  const r = edad.aptoPorEdad(byId("older-adult-care"), 40);
  assert.equal(r.ok, false);
  assert.match(r.motivo, /60/);
});
test("aptoPorEdad: adulto mayor SÍ apto para Geriatría", () => {
  assert.equal(edad.aptoPorEdad(byId("older-adult-care"), 72).ok, true);
});
test("aptoPorEdad: servicio sin límites siempre apto", () => {
  assert.equal(edad.aptoPorEdad(byId("cardiology"), 5).ok, true);
  assert.equal(edad.aptoPorEdad(byId("cardiology"), 90).ok, true);
});

test("alternativaPorEdad: adulto en 'Ecografía infantil' => 'Ecografía'", () => {
  const alt = edad.alternativaPorEdad(servicios, byId("child-imaging"), 40);
  assert.ok(alt, "debe proponer una alternativa");
  assert.equal(alt.servicio.id, "adult-imaging");
  assert.equal(edad.aptoPorEdad(alt.servicio, 40).ok, true);
});
test("alternativaPorEdad: chico en 'Psiquiatría' (16+) => variante infantil apta", () => {
  const alt = edad.alternativaPorEdad(servicios, byId("adult-psychiatry"), 8);
  assert.ok(alt);
  assert.match(alt.servicio.nombre.toLowerCase(), /psiquiatr/);
  assert.equal(edad.aptoPorEdad(alt.servicio, 8).ok, true);
});
test("alternativaPorEdad: chico en 'Geriatría' => red de contención apta a su edad", () => {
  const alt = edad.alternativaPorEdad(servicios, byId("older-adult-care"), 8);
  assert.ok(alt);
  assert.equal(edad.aptoPorEdad(alt.servicio, 8).ok, true);
});
test("alternativaPorEdad: si el servicio ya es apto => null", () => {
  assert.equal(edad.alternativaPorEdad(servicios, byId("cardiology"), 40), null);
});

// ---- i18n: la trampa de pluralización "años"/"año" vs "years"/"year" -----
// (docs/architecture/i18n.md — age.mjs resuelve la elección singular/plural
// eligiendo la CLAVE (_one/_other), nunca concatenando un sufijo a mano).

function withMockCatalog(catalogo, fn) {
  setCatalog(catalogo);
  try { fn(); } finally { setCatalog({}); }
}

const EN_AGE = {
  "intake.age.min_or_more_one": "is for people {n} year old and over",
  "intake.age.min_or_more_other": "is for people {n} years old and over",
  "intake.age.max_up_to_one": "is for children up to {n} year old",
  "intake.age.max_up_to_other": "is for children up to {n} years old",
  "intake.age.all_ages": "sees people of all ages",
  "intake.age.same_age_version": "is the version for your age",
  "intake.age.matches_age": "does match your age",
};

test("aptoPorEdad (EN): mínimo = 1 usa singular, nunca '1 years old'", () => {
  withMockCatalog(EN_AGE, () => {
    const servicio = { id: "unit-min-1", edad: { min: 1, max: null } };
    const r = edad.aptoPorEdad(servicio, 0);
    assert.equal(r.ok, false);
    assert.equal(r.motivo, "is for people 1 year old and over");
    assert.doesNotMatch(r.motivo, /1 years/);
  });
});

test("aptoPorEdad (EN): mínimo > 1 usa plural", () => {
  withMockCatalog(EN_AGE, () => {
    const r = edad.aptoPorEdad(byId("older-adult-care"), 40);
    assert.equal(r.ok, false);
    assert.equal(r.motivo, "is for people 60 years old and over");
  });
});

test("aptoPorEdad (EN): máximo = 1 usa singular, nunca '1 years old'", () => {
  withMockCatalog(EN_AGE, () => {
    const servicio = { id: "unit-max-1", edad: { min: null, max: 1 } };
    const r = edad.aptoPorEdad(servicio, 2);
    assert.equal(r.ok, false);
    assert.equal(r.motivo, "is for children up to 1 year old");
    assert.doesNotMatch(r.motivo, /1 years/);
  });
});

test("aptoPorEdad (EN): máximo > 1 usa plural", () => {
  withMockCatalog(EN_AGE, () => {
    const r = edad.aptoPorEdad(byId("child-imaging"), 40);
    assert.equal(r.ok, false);
    assert.equal(r.motivo, "is for children up to 15 years old");
  });
});

test("sin catálogo (Node, sin window): motivo cae al castellano exacto de siempre", () => {
  const servicio = { id: "unit-min-1", edad: { min: 1, max: null } };
  const r = edad.aptoPorEdad(servicio, 0);
  // El castellano nunca distinguió singular/plural acá ("1 años o más") — el
  // fallback tiene que seguir siendo BYTE-IDÉNTICO a lo de siempre.
  assert.equal(r.motivo, "es para personas de 1 años o más");
});

test("alternativaPorEdad (EN): sin límite de edad => 'sees people of all ages'", () => {
  withMockCatalog(EN_AGE, () => {
    // "Ecografía" (la alternativa real de "Ecografía infantil") no tiene
    // límites de edad, así que motivo entra por la rama _sinLimite ANTES que
    // por la de "misma versión" — mismo camino que corre en producción.
    const alt = edad.alternativaPorEdad(servicios, byId("child-imaging"), 40);
    assert.ok(alt);
    assert.equal(alt.motivo, "sees people of all ages");
  });
});

test("alternativaPorEdad (EN): misma versión del nombre, con límites en ambas puntas", () => {
  withMockCatalog(EN_AGE, () => {
    const original = { id: "a", nombre: "Chequeo infantil", edad: { min: null, max: 15 } };
    const alternativa = { id: "b", nombre: "Chequeo adultos", edad: { min: 16, max: null } };
    const alt = edad.alternativaPorEdad([original, alternativa], original, 40);
    assert.ok(alt);
    assert.equal(alt.motivo, "is the version for your age");
  });
});
