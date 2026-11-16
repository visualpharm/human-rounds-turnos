/* search-guardia.test.mjs — usability study 2026-10-15 (P5): two patients
   searched «guardia» / «hospital» and found nothing that said where the
   hospital's guardia is. Pins the three repairs:
     - «urgencias»/«emergencia» also reach the Guardia (a variant, not a
       replacement: «urgencias odontológicas» keeps its own service);
     - a center is searchable by its name and address (centroRow/centroCampos);
     - the demo catalog (packs/example-town) has the hospital's Guardia, a
       walk-in service that never offers appointments.
   Run: node --test tests/search-guardia.test.mjs */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as S from "../static/platform/search.mjs";

const servicios = [
  { nombre: "Guardia", horas: 14, palabras: [] },
  { nombre: "Odontología - Urgencias", horas: 3, palabras: [] },
  { nombre: "Medicina General - Anticoncepción", horas: 9, palabras: ["anticoncepción de emergencia"] },
  { nombre: "Cardiología", horas: 9, palabras: [] },
];
const campos = (s) => ({ principal: [s.nombre], resto: s.palabras });

test("«urgencias» and «emergencia» lead to the Guardia first", () => {
  for (const q of ["urgencias", "urgencia", "emergencia", "emergencias"]) {
    const out = S.buscar(servicios, q, campos, "es").map((r) => r.nombre);
    assert.equal(out[0], "Guardia", q);
  }
});

test("the synonym is a variant: the services that name the word still match", () => {
  assert.ok(S.buscar(servicios, "urgencias", campos, "es").some((r) => r.nombre === "Odontología - Urgencias"));
  assert.ok(S.buscar(servicios, "emergencia", campos, "es").some((r) => r.nombre.startsWith("Medicina General")));
  assert.deepEqual(S.buscar(servicios, "urgencias odontologia", campos, "es").map((r) => r.nombre),
    ["Odontología - Urgencias"]);
});

test("a center is found by its name and address, and links to its own page", () => {
  const centros = [
    { slug: "hospital-pinamar", name: "Hospital de Pinamar", address: "Av. Shaw 255", description: "Guardia, internación" },
    { slug: "caps-ostende", name: "CAPS Ostende", address: "Misiones y Av. del Parque", description: "Guardia" },
  ];
  const rows = centros.map(S.centroRow);
  const hit = S.buscar(rows, "hospital", S.centroCampos, "es");
  assert.deepEqual(hit.map((r) => r.href), ["/center/hospital-pinamar"]);
  assert.equal(hit[0].centro, "Av. Shaw 255");
  assert.deepEqual(S.buscar(rows, "shaw", S.centroCampos, "es").map((r) => r.nombre), ["Hospital de Pinamar"]);
  // the description is never searched: «guardia» does not list every center
  assert.equal(S.buscar(rows, "guardia", S.centroCampos, "es").length, 0);
});

test("the demo catalog has the hospital's Guardia as a walk-in service", () => {
  const pack = JSON.parse(readFileSync(new URL("../packs/example-town/servicios.json", import.meta.url), "utf8"));
  const guardia = pack.servicios.find((s) => s.id === "guardia-sampleton-hospital");
  assert.ok(guardia);
  assert.equal(guardia.nombre, "Guardia");
  assert.equal(guardia.centro, "sampleton-hospital");
  assert.equal(guardia.modo, "walkin");
  assert.equal(guardia.agenda, undefined);
  const out = S.buscar(pack.servicios, "guardia", campos, "es");
  assert.equal(out[0].id, "guardia-sampleton-hospital");
});
