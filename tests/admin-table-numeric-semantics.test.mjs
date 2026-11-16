import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const root = new URL("..", import.meta.url);
const read = (name) => fs.readFileSync(new URL(name, root), "utf8");
const patients = read("static/platform/admin-patients.mjs");
const mine = read("static/platform/admin-my-appointments.mjs");
const preparations = read("static/platform/admin-preparations.mjs");

test("numeric alignment follows declared meaning in the patient registries", () => {
  // The patient registry has no age column any more: the age is part of the
  // person line («Nombre Apellido, 48»), so only the appointment count is numeric.
  assert.doesNotMatch(patients, /key: "edad"/);
  assert.match(patients, /key: "turnos"[\s\S]*?numeric: true/);
  assert.match(mine, /key: "edad"[\s\S]*?numeric: true/);
  assert.match(preparations, /key: "ayuno"[\s\S]*?numeric: true/);
  for (const source of [patients, mine]) assert.match(source, /c\.numeric \? " cnt-numeric"/);
});

test("digit-shaped identifiers, phones, and dates stay out of numeric alignment", () => {
  assert.doesNotMatch(patients, /key: "dni"[\s\S]{0,180}numeric: true/);
  assert.doesNotMatch(patients, /key: "contacto"[\s\S]{0,180}numeric: true/);
  assert.doesNotMatch(patients, /key: "nacimiento"[\s\S]{0,180}numeric: true/);
  assert.doesNotMatch(mine, /key: "dni"[\s\S]{0,180}numeric: true/);
  assert.doesNotMatch(mine, /key: "contacto"[\s\S]{0,180}numeric: true/);
});
