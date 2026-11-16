// tests/hours.test.mjs — parser de horarios (Sana Turnos). Correr: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import * as I18n from "../static/platform/i18n.mjs";
import * as H from "../static/platform/hours.mjs";
const require = createRequire(import.meta.url);

// Referencias de día (verificadas): lunes 2026-07-06, martes 07, miércoles 08, sábado 11, domingo 12.
const LUN_10 = new Date("2026-07-06T10:00:00-03:00"); // lunes 10:00
const LUN_07 = new Date("2026-07-06T07:00:00-03:00"); // lunes 07:00 (antes de abrir)
const LUN_15 = new Date("2026-07-06T15:00:00-03:00"); // lunes 15:00
const MAR_10 = new Date("2026-07-07T10:00:00-03:00"); // martes 10:00
const SAB_10 = new Date("2026-07-11T10:00:00-03:00"); // sábado 10:00

// Multi-idioma (responsable del producto, 2026-08-04): catálogo real + i18n.js real, mismo realm
// (`runInThisContext`, no `createContext`) para que `now instanceof Date`
// siga funcionando dentro de hours.mjs con las fechas fijas de los tests.
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function loadCatalog(lang) {
  const dir = join(ROOT, "locales", lang);
  const catalog = {};
  for (const fname of readdirSync(dir).filter((f) => f.endsWith(".json"))) {
    const rows = JSON.parse(readFileSync(join(dir, fname), "utf8"));
    for (const [k, v] of Object.entries(rows)) if (k !== "_comment") catalog[k] = v;
  }
  return catalog;
}

function withLang(lang, run) {
  const catalog = loadCatalog(lang);
  const prevWindow = global.window;
  I18n.setCatalog(catalog);
  global.window = {
    document: { documentElement: { getAttribute: () => lang } },
  };
  try {
    run(H);
  } finally {
    I18n.setCatalog({});
    global.window = prevWindow;
  }
}

test("intervalo simple: abierto dentro del horario", () => {
  const r = H.estado("Lunes a viernes de 8 a 13 h", LUN_10);
  assert.equal(r.abierto, true);
  assert.equal(r.proxima, null);
  // abierto trae la hora de cierre del tramo actual ("hasta las 13:00")
  assert.equal(r.hasta, 13 * 60);
});

test("abierto con dos franjas: `hasta` es el cierre de la franja ACTUAL", () => {
  // martes de 8 a 12 y de 16 a 20 → a las 10 el cierre que vale es 12:00
  const r = H.estado("Martes de 8 a 12 y de 16 a 20 h", MAR_10);
  assert.equal(r.abierto, true);
  assert.equal(r.hasta, 12 * 60);
});

test("intervalo simple: cerrado antes de abrir -> próxima apertura hoy", () => {
  const r = H.estado("Lunes a viernes de 8 a 13 h", LUN_07);
  assert.equal(r.abierto, false);
  assert.equal(r.proxima, "lu 8:00");
});

test("intervalo simple: cerrado el fin de semana -> abre el lunes", () => {
  const r = H.estado("Lunes a viernes de 8 a 13 h", SAB_10);
  assert.equal(r.abierto, false);
  assert.equal(r.proxima, "lu 8:00");
});

test("cerrado luego de la última franja -> próximo día", () => {
  // lunes 15:00, cierra 13:00 -> abre martes 8:00
  const r = H.estado("Lunes a viernes de 8 a 13 h", LUN_15);
  assert.equal(r.abierto, false);
  assert.equal(r.proxima, "ma 8:00");
});

test("formato terso L-V 8-13 se parsea igual", () => {
  const r = H.estado("L-V 8-13", LUN_10);
  assert.equal(r.abierto, true);
  const wk = H.parse("L-V 8-13");
  assert.deepEqual(wk[1], [{ start: 480, end: 780 }]);
  assert.equal(wk[6], undefined); // sábado no
});

test("abreviatura + apertura puntual: 'ma 7'", () => {
  const wk = H.parse("ma 7");
  assert.deepEqual(wk[2], [{ start: 420, end: null }]);
  // apertura puntual nunca marca 'abierto ahora'
  const r = H.estado("ma 7", MAR_10);
  assert.equal(r.abierto, false);
});

test("intervalo con franja extra: 'L-V 8-11 y 16'", () => {
  const wk = H.parse("L-V 8-11 y 16");
  assert.deepEqual(wk[1], [{ start: 480, end: 660 }, { start: 960, end: null }]);
});

test("franja con minutos: de 7:30 a 10", () => {
  const wk = H.parse("Lunes a viernes de 7:30 a 10 h");
  assert.deepEqual(wk[1], [{ start: 450, end: 600 }]);
});

test("multi-cláusula con ';' y aperturas puntuales", () => {
  // 'Martes 8 y 16 h; jueves 8 y 14 h'
  const wk = H.parse("Martes 8 y 16 h; jueves 8 y 14 h");
  assert.deepEqual(wk[2], [{ start: 480, end: null }, { start: 960, end: null }]);
  assert.deepEqual(wk[4], [{ start: 480, end: null }, { start: 840, end: null }]);
});

test("lista de días con 'y': 'Lunes y viernes a las 8 h'", () => {
  const wk = H.parse("Lunes y viernes a las 8 h");
  assert.deepEqual(wk[1], [{ start: 480, end: null }]);
  assert.deepEqual(wk[5], [{ start: 480, end: null }]);
  assert.equal(wk[2], undefined);
});

test("dos días distintos con intervalos distintos", () => {
  const wk = H.parse("Miércoles de 12 a 16 h y viernes de 8 a 12 h");
  assert.deepEqual(wk[3], [{ start: 720, end: 960 }]);
  assert.deepEqual(wk[5], [{ start: 480, end: 720 }]);
});

test("no interpretable -> null (agenda)", () => {
  assert.equal(H.estado("Con turno, según agenda"), null);
  assert.equal(H.parse("Con turno, según agenda"), null);
});

// ---- recurrencias mensuales (responsable del producto, 2026-07-10: la Admisión de Psicología
// debe decir CUÁNDO es la próxima, en el listado y en su página) ----

test("mensual: primer día hábil de cada mes -> próxima ocurrencia con fecha", () => {
  // viernes 2026-07-10: el 1º hábil de julio ya pasó; ago: sá 1, do 2 -> lu 3
  const r = H.estado("El primer día hábil de cada mes", new Date("2026-07-10T12:00:00-03:00"));
  assert.equal(r.abierto, false);
  assert.equal(r.fecha, "2026-08-03");
  assert.equal(r.dia, 1);      // lunes
  assert.equal(r.off, 24);
  assert.equal(r.min, null);   // el cartel no dice hora
  assert.equal(H.frase(r, new Date("2026-07-10T12:00:00-03:00")), "Lunes 3 de agosto");
});

test("mensual: el mismo día cuenta como hoy", () => {
  const now = new Date("2026-08-03T09:00:00-03:00");
  const r = H.estado("El primer día hábil de cada mes", now);
  assert.equal(r.off, 0);
  assert.equal(r.fecha, "2026-08-03");
  assert.equal(H.frase(r, now), "Es hoy");
});

test("mensual: salta feriados inamovibles (1/1) y fin de semana", () => {
  // 2027: vi 1/1 feriado, sá 2, do 3 -> primer hábil = lunes 4/1/2027
  const r = H.estado("El primer día hábil de cada mes", new Date("2026-12-28T09:00:00-03:00"));
  assert.equal(r.fecha, "2027-01-04");
  // año distinto al actual -> la frase lo dice
  assert.equal(H.frase(r, new Date("2026-12-28T09:00:00-03:00")), "Lunes 4 de enero de 2027");
});

test("mensual: n-ésimo día de semana con hora ('el primer lunes … a las 9 h')", () => {
  const now = new Date("2026-07-10T12:00:00-03:00");
  const r = H.estado("El primer lunes de cada mes a las 9 h", now);
  assert.equal(r.fecha, "2026-08-03");
  assert.equal(r.min, 540);
  assert.equal(H.frase(r, now), "Atiende el lunes 3 de agosto, 09:00");
});

test("mensual: 'el último viernes del mes'", () => {
  const r = H.estado("El último viernes del mes", new Date("2026-07-10T12:00:00-03:00"));
  assert.equal(r.fecha, "2026-07-31");
  assert.equal(r.off, 21);
});

test("frase: semanal sigue igual (no rompe lo que ya estaba)", () => {
  assert.equal(H.frase(H.estado("Lunes a viernes de 8 a 13 h", LUN_10)),
    "Atiende ahora · hasta las 13:00");
  assert.equal(H.frase(H.estado("Jueves a las 8 h", LUN_10)), "Atiende el jueves, 08:00");
  assert.equal(H.frase(null), null);
});

test("días sin horario -> null (no adivinamos)", () => {
  // 'Lunes a viernes' sin ninguna hora no alcanza para un estado
  assert.equal(H.estado("Lunes a viernes"), null);
  assert.equal(H.estado("Martes y viernes por la mañana"), null);
});

test("entrada vacía / basura -> null", () => {
  assert.equal(H.estado(""), null);
  assert.equal(H.estado(null), null);
  assert.equal(H.parse("xxxxx"), null);
});

test("all shipped example schedules parse or return null without throwing", () => {
  const { servicios } = require("../packs/example-town/servicios.json");
  for (const s of servicios) {
    const r = H.estado(s.horario, LUN_10);
    assert.ok(r === null || (typeof r.abierto === "boolean"), s.horario);
  }
});

// ---- multi-idioma (the product owner, 2026-08-04): mismo horario ("Jueves a las 8 h" /
// la recurrencia mensual), frase() en inglés — sin artículo, con mayúscula —
// y en castellano con el catálogo puesto, para probar que activarlo no
// cambia una coma del render de siempre.

test("frase en inglés: sin artículo, día y mes con mayúscula", () => {
  withLang("en", (H2) => {
    assert.equal(H2.frase(H2.estado("Lunes a viernes de 8 a 13 h", LUN_10)),
      "Open now · until 13:00");
    assert.equal(H2.frase(H2.estado("Jueves a las 8 h", LUN_10)), "Open Thursday, 08:00");
  });
});

test("frase en inglés: recurrencia mensual, con y sin hora", () => {
  withLang("en", (H2) => {
    const now = new Date("2026-07-10T12:00:00-03:00");
    const r = H2.estado("El primer día hábil de cada mes", now);
    assert.equal(H2.frase(r, now), "Monday 3 August");
    const r2 = H2.estado("El primer lunes de cada mes a las 9 h", now);
    assert.equal(H2.frase(r2, now), "Open Monday 3 August, 09:00");
    const r3 = H2.estado("El primer día hábil de cada mes", new Date("2026-12-28T09:00:00-03:00"));
    assert.equal(H2.frase(r3, new Date("2026-12-28T09:00:00-03:00")), "Monday 4 January 2027");
  });
});

test("frase en inglés: hoy/mañana siguen diciendo eso, no el nombre del día", () => {
  withLang("en", (H2) => {
    const now = new Date("2026-08-03T09:00:00-03:00");
    const r = H2.estado("El primer día hábil de cada mes", now);
    assert.equal(H2.frase(r, now), "Today");
  });
});

test("castellano con el catálogo cargado: frase() no se mueve", () => {
  withLang("es", (H2) => {
    assert.equal(H2.frase(H2.estado("Lunes a viernes de 8 a 13 h", LUN_10)),
      "Atiende ahora · hasta las 13:00");
    assert.equal(H2.frase(H2.estado("Jueves a las 8 h", LUN_10)), "Atiende el jueves, 08:00");
    const now = new Date("2026-07-10T12:00:00-03:00");
    assert.equal(H2.frase(H2.estado("El primer lunes de cada mes a las 9 h", now), now),
      "Atiende el lunes 3 de agosto, 09:00");
  });
});
