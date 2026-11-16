// tests/esm-facade-parity.test.mjs — executable import guards for canonical
// ES modules. The former format facade/global is deliberately absent.
//
// calendar-grid.mjs is now canonical ESM rather than a facade. Its direct
// import smoke remains here, while global-parity assertions correctly do not.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const country = JSON.parse(
  readFileSync(new URL("../packs/ar/country.json", import.meta.url), "utf8")
);
globalThis.SANA_COUNTRY_PHONE = country.phone;

const facade = await import("../static/platform/format.mjs");
const calendarFacade = await import("../static/platform/calendar-grid.mjs");
const datesFacade = await import("../static/platform/dates.mjs");
const hoursFacade = await import("../static/platform/hours.mjs");

test("format.mjs is canonical ESM with the complete named API and no compatibility global", () => {
  const expected = [
    "contactoHtml", "contactoIconoHtml", "contactoQuienHtml", "digits", "dni", "dniHtml", "edad", "edadTexto", "emailHtml", "fechaCorta", "medicosLista", "medicosTexto", "medicosTitulo",
    "nombre", "numero", "personaEdad", "porcentaje", "resueltaPor", "scheduleBand", "telE164", "telHref", "telHtml", "telNacional", "telPartes", "telTexto", "telValido", "telWa",
  ];
  assert.deepEqual(Object.keys(facade).filter((k) => k !== "default").sort(), expected);
  assert.deepEqual(Object.keys(facade.default).sort(), expected);
  assert.equal(globalThis["Sana" + "Format"], undefined);
});

test("import { telTexto } from \"../static/platform/format.mjs\" funciona de verdad", () => {
  assert.equal(facade.telTexto("+5491135477302"), "+54 9 11 3547-7302");
  assert.equal(facade.telTexto("1143215678"), "+54 11 4321-5678");
  assert.equal(facade.nombre("ALEJO VARELA"), "Alejo Varela");
});

// =====================================================================
// calendar-grid.mjs — Tier 5 slice 3 (el facade que envuelve el island
// MedCal, static/islands/MedCal.mjs)
// =====================================================================

test("import { gridHtml } from \"../static/platform/calendar-grid.mjs\" funciona de verdad", () => {
  const html = calendarFacade.gridHtml(
    [{ dia: "2026-08-12", className: "medcal-libre" }],
    { from: "2026-08-12", to: "2026-08-12", today: "2026-08-04", dateAttr: "data-med-date" }
  );
  assert.match(html, /data-med-date="2026-08-12"/);
  assert.match(html, /medcal-libre/);
});

// =====================================================================
// dates.mjs / hours.mjs — canonical ESM implementations. Neither creates a
// compatibility global. The smoke tests exercise their real named exports.
// =====================================================================

test("dates.mjs is canonical ESM with the complete named API and no compatibility global", () => {
  assert.equal(existsSync(new URL(["../dates", ".js"].join(""), import.meta.url)), false);
  assert.deepEqual(
    Object.keys(datesFacade).filter((k) => k !== "default").sort(),
    ["ahoraInstalacion", "amigable", "conHusoInstalacion", "corta", "cortaFija", "delServidor", "dia", "diaFechado", "hoyIsoInstalacion",
     "instanteServidor", "larga", "nacimiento", "paredInstalacion", "pasada", "relativo"],
  );
  assert.deepEqual(
    Object.keys(datesFacade.default).sort(),
    ["ahoraInstalacion", "amigable", "conHusoInstalacion", "corta", "cortaFija", "delServidor", "dia", "diaFechado", "hoyIsoInstalacion",
     "instanteServidor", "larga", "nacimiento", "paredInstalacion", "pasada", "relativo"],
  );
  assert.equal(globalThis["Sana" + "Dates"], undefined);
});

test("import { amigable } from \"../static/platform/dates.mjs\" funciona de verdad, igual que el UMD real", () => {
  assert.equal(
    datesFacade.amigable("2026-07-10", "14:40", new Date("2026-07-10T11:00:00")),
    "Hoy, 14:40"
  );
});

test("hours.mjs is canonical ESM with the complete named API and no compatibility global", () => {
  assert.equal(existsSync(new URL(["../hours", ".js"].join(""), import.meta.url)), false);
  assert.deepEqual(
    Object.keys(hoursFacade).filter((k) => k !== "default").sort(),
    ["estado", "frase", "parse"],
  );
  assert.deepEqual(Object.keys(hoursFacade.default).sort(), ["estado", "frase", "parse"]);
  assert.equal(globalThis["Sana" + "Schedules"], undefined);
});

test("import { estado } from \"../static/platform/hours.mjs\" funciona de verdad, igual que el UMD real", () => {
  // deepEqual contra el objeto COMPLETO del estado abierto: `proxima: null`
  // viaja en la forma pública de retorno de hours.mjs y lo pinea
  // tests/hours.test.mjs — nunca un subconjunto.
  assert.deepEqual(
    hoursFacade.estado("Lunes a viernes de 8 a 13 h", new Date("2026-07-06T10:00:00-03:00")),
    { abierto: true, proxima: null, hasta: 780 }
  );
});
