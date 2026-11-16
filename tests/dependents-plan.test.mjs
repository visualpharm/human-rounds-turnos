import { test } from "node:test";
import assert from "node:assert/strict";
import { plan, aptosPorEdad, esPediatrico } from "../static/platform/dependents.mjs";

const HOY = new Date("2026-07-08T12:00:00");
const usuario = { dni: "27888999", nombre: "Ana", apellido: "López", nacimiento: "1979-06-01" };
const svcAdulto = { edad: { min: null, max: null } };
const svcPediatrico = { edad: { min: null, max: 15 } };
const svcGeriatria = { edad: { min: 60, max: null } };
const hijo = (edad, suf) => ({ dni: "5000000" + suf, nombre: "Chico" + suf, apellido: "Test",
  nacimiento: `${HOY.getFullYear() - edad}-06-15` });

test("esPediatrico: max<18 pediátrico; max null o >=18 no", () => {
  assert.equal(esPediatrico({ edad: { max: 15 } }), true);
  assert.equal(esPediatrico({ edad: { max: 18 } }), false);
  assert.equal(esPediatrico({ edad: { max: null } }), false);
  assert.equal(esPediatrico({}), false);
});

test("plan: no pediátrico -> titular, sin paso", () => {
  assert.deepEqual(plan([], svcAdulto, usuario, HOY), { paciente: usuario, paso: null });
});

test("plan: no pediátrico con hijos igual devuelve al titular", () => {
  const r = plan([hijo(8, "1")], svcAdulto, usuario, HOY);
  assert.equal(r.paciente, usuario);
  assert.equal(r.paso, null);
});

test("plan: pediátrico, 0 hijos -> paso agregar", () => {
  const r = plan([], svcPediatrico, usuario, HOY);
  assert.equal(r.paciente, null);
  assert.equal(r.paso, "agregar");
});

test("plan: pediátrico, 1 hijo apto -> auto-selección sin paso", () => {
  const h = hijo(8, "1");
  const r = plan([h], svcPediatrico, usuario, HOY);
  assert.equal(r.paciente, h);
  assert.equal(r.paso, null);
});

test("plan: pediátrico, hijo fuera de rango cuenta como 0 aptos -> agregar", () => {
  const r = plan([hijo(20, "2")], svcPediatrico, usuario, HOY);
  assert.equal(r.paciente, null);
  assert.equal(r.paso, "agregar");
});

test("plan: pediátrico, 2+ aptos -> paso elegir", () => {
  const r = plan([hijo(5, "1"), hijo(10, "2")], svcPediatrico, usuario, HOY);
  assert.equal(r.paciente, null);
  assert.equal(r.paso, "elegir");
});

test("plan: pediátrico, 1 apto + 1 fuera de rango -> sólo cuenta el apto", () => {
  const apto = hijo(8, "1");
  const r = plan([apto, hijo(20, "2")], svcPediatrico, usuario, HOY);
  assert.equal(r.paciente, apto);
  assert.equal(r.paso, null);
});

test("plan: geriatría bloquea al titular joven y permite una persona mayor a cargo", () => {
  const mayor = { dni: "20000111", nombre: "Elena", nacimiento: "1940-04-10" };
  const r = plan([mayor], svcGeriatria, usuario, HOY);
  assert.equal(r.paciente, mayor);
  assert.equal(r.paso, null);
});

test("aptosPorEdad: edad desconocida no bloquea", () => {
  const aptos = aptosPorEdad([{ dni: "1", nombre: "X" }], svcPediatrico, HOY);
  assert.equal(aptos.length, 1);
});
