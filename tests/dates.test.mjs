// tests/dates.test.mjs — formato amigable de fechas (Sana Turnos). Correr: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import * as I18n from "../static/platform/i18n.mjs";
const F = await import("../static/platform/dates.mjs");

// "Ahora" fijo: viernes 2026-07-10, 11:00 (verificado: 10/07/2026 es viernes).
// Instante en ART (UTC-3, el huso de la instalación): "hoy" no depende del huso de quien corre el test.
const NOW = new Date("2026-07-10T11:00:00-03:00");

// Sin `window` (arriba), T() siempre cae al fallback castellano — lo que
// prueba que el castellano no se movió. Para probar el INGLÉS de verdad hay
// Multi-idioma: catálogo real via setCatalog on the shared canonical module.
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
    run(F);
  } finally {
    I18n.setCatalog({});
    global.window = prevWindow;
  }
}

test("hoy", () => {
  assert.equal(F.amigable("2026-07-10", "14:40", NOW), "Hoy, 14:40");
});

test("mañana", () => {
  assert.equal(F.amigable("2026-07-11", "09:00", NOW), "Mañana, sábado 11, 09:00");
  // la palabra sola sigue para quien la compara o la recorta
  assert.equal(F.dia("2026-07-11", NOW), "Mañana");
  assert.equal(F.diaFechado("2026-07-11", NOW), "Mañana, sábado 11");
  assert.equal(F.diaFechado("2026-07-10", NOW), "Hoy");
  assert.equal(F.diaFechado("2026-07-14", NOW), "Martes 14 de julio");
});

test("mañana cruza fin de mes", () => {
  const now = new Date("2026-07-31T20:00:00-03:00");
  assert.equal(F.amigable("2026-08-01", "08:15", now), "Mañana, sábado 1, 08:15");
});

test("resto de la semana: día + fecha completa", () => {
  // martes 14 de julio de 2026
  assert.equal(F.amigable("2026-07-14", "14:40", NOW), "Martes 14 de julio, 14:40");
});

test("sin hora: sólo la fecha", () => {
  assert.equal(F.amigable("2026-07-14", "", NOW), "Martes 14 de julio");
  assert.equal(F.amigable("2026-07-14", null, NOW), "Martes 14 de julio");
});

test("otro año agrega el año", () => {
  assert.equal(F.amigable("2027-01-05", "10:00", NOW), "Martes 5 de enero de 2027, 10:00");
});

test("acepta timestamp ISO largo", () => {
  assert.equal(F.dia("2026-07-11T00:00:00", NOW), "Mañana");
});

test("día inválido devuelve cadena vacía", () => {
  assert.equal(F.amigable("14/07/2026", "10:00", NOW), "");
  assert.equal(F.amigable("", "10:00", NOW), "");
  assert.equal(F.amigable(null, null, NOW), "");
});

test("dia() sin hora funciona igual que amigable sin hora", () => {
  assert.equal(F.dia("2026-07-10", NOW), "Hoy");
  assert.equal(F.dia("2026-07-16", NOW), "Jueves 16 de julio");
});

test("larga() nunca es relativa — para texto guardado", () => {
  assert.equal(F.larga("2026-07-10", "14:40", NOW), "Viernes 10 de julio, 14:40");
  assert.equal(F.larga("2026-07-11", "09:00", NOW), "Sábado 11 de julio, 09:00");
  assert.equal(F.larga("2027-01-05", null, NOW), "Martes 5 de enero de 2027");
  assert.equal(F.larga("basura", "10:00", NOW), "");
});

test("relativo(): recién / minutos / horas / fecha amigable", () => {
  const now = new Date(2026, 6, 11, 14, 30, 0); // vie 11 jul 14:30
  assert.equal(F.relativo("2026-07-11T14:29:50", now), "recién");
  assert.equal(F.relativo("2026-07-11T14:10:00", now), "hace 20 minutos");
  assert.equal(F.relativo("2026-07-11T14:29:00", now), "hace 1 minuto");
  assert.equal(F.relativo("2026-07-11T11:30:00", now), "hace 3 horas");
  // más de 6 h atrás → fecha amigable con hora
  assert.equal(F.relativo("2026-07-11T05:00:00", now), "Hoy, 05:00");
  assert.equal(F.relativo("2026-07-09T09:15:00", now), "Jueves 9 de julio, 09:15");
  assert.equal(F.relativo("basura", now), "");
});

test("delServidor(): el reloj del texto del server, nunca el huso del navegador", () => {
  const tz = process.env.TZ;
  process.env.TZ = "UTC"; // una compu del médico en UTC
  try {
    const now = new Date("2026-07-11T14:30:00-03:00");
    assert.equal(F.delServidor("2026-07-11T09:00:00-03:00", now), "Hoy, 09:00");
    assert.equal(F.delServidor("2026-07-12 18:40:00-03:00", now), "Mañana, domingo 12, 18:40");
    assert.equal(F.delServidor("2026-07-09T09:15:00-03:00", now), "Jueves 9 de julio, 09:15");
    assert.equal(F.delServidor("2026-07-09", now), "");
    assert.equal(F.delServidor(null, now), "");
  } finally {
    if (tz === undefined) delete process.env.TZ; else process.env.TZ = tz;
  }
});

test("instanteServidor(): fresh is relative, older is the installation's day and hour on any clock", () => {
  const tz = process.env.TZ;
  process.env.TZ = "UTC"; // an administrator reading from another timezone
  try {
    const now = new Date("2026-07-11T22:30:00-03:00");
    assert.equal(F.instanteServidor("2026-07-11T22:10:00-03:00", now), "hace 20 minutos");
    assert.equal(F.instanteServidor("2026-07-11T19:30:00-03:00", now), "hace 3 horas");
    // Six hours on: the text of the server, where relativo() prints the reader's 17:09.
    assert.equal(F.instanteServidor("2026-07-11T14:09:00-03:00", now), "Hoy, 14:09");
    assert.equal(F.relativo("2026-07-11T14:09:00-03:00", now).endsWith("17:09"), true);
    assert.equal(F.instanteServidor("2026-07-09T09:15:00-03:00", now), "Jueves 9 de julio, 09:15");
    assert.equal(F.instanteServidor("basura", now), "");
    assert.equal(F.instanteServidor(null, now), "");
  } finally {
    if (tz === undefined) delete process.env.TZ; else process.env.TZ = tz;
  }
});

test("MCP «Último uso» (Mi cuenta): the installation's day and hour near midnight on a UTC clock", () => {
  const tz = process.env.TZ;
  process.env.TZ = "UTC"; // a browser (or a server) running in UTC
  try {
    // 00:30 UTC of the 14th is 21:30 of the 13th in the installation: a UTC read says the 14th.
    const now = new Date("2026-11-14T15:00:00Z");
    const shown = F.instanteServidor("2026-11-13T21:30:00-03:00", now);
    assert.ok(shown.endsWith("21:30"), shown);
    assert.ok(!shown.includes("00:30"), shown);
  } finally {
    if (tz === undefined) delete process.env.TZ; else process.env.TZ = tz;
  }
  const src = readFileSync(join(ROOT, "static/platform/account.mjs"), "utf8");
  assert.match(src, /Dates\.instanteServidor\(j\.last_used\)/);
  assert.doesNotMatch(src, /Intl\.DateTimeFormat\([^)]*\)\.format\(new Date\(j\.last_used/);
});

// Eco de fecha tipeada EN PALABRAS (responsable del producto, 2026-07-12): "28/10/1978" obliga a
// adivinar cuál número es el mes; en palabras no hay ambigüedad posible.
test("nacimiento: fecha en palabras, sin día de la semana", () => {
  assert.equal(F.nacimiento("1978-10-28"), "28 de octubre de 1978");
});

test("nacimiento: día sin cero a la izquierda", () => {
  assert.equal(F.nacimiento("1959-11-09"), "9 de noviembre de 1959");
});

test("nacimiento: fecha inválida devuelve vacío", () => {
  assert.equal(F.nacimiento("no-es-fecha"), "");
  assert.equal(F.nacimiento(""), "");
});

// corta() — versión abreviada para los controles del admin (responsable del producto, 2026-07-22).
test("corta: hoy y mañana siguen diciéndose así", () => {
  assert.equal(F.corta("2026-07-10", "12:00", NOW), "Hoy, 12:00");
  assert.equal(F.corta("2026-07-11", "09:00", NOW), "Mañana, 09:00");
});

test("corta: el resto se abrevia (Mié 29 jul, 10:00)", () => {
  assert.equal(F.corta("2026-07-29", "10:00", NOW), "Mié 29 jul, 10:00");
  assert.equal(F.corta("2026-08-05", "10:20", NOW), "Mié 5 ago, 10:20");
});

test("corta: otro año lo dice; sin hora, sólo la fecha", () => {
  assert.equal(F.corta("2027-01-04", "08:00", NOW), "Lun 4 ene 2027, 08:00");
  assert.equal(F.corta("2026-07-29", "", NOW), "Mié 29 jul");
  assert.equal(F.corta("no-es-fecha", "10:00", NOW), "");
});

// cortaFija() — the day row already says «Hoy», so the date never repeats it.
test("cortaFija: today is the date, never «Hoy»; another year says it", () => {
  assert.equal(F.cortaFija("2026-07-10", NOW), "Vie 10 jul");
  assert.equal(F.cortaFija("2026-07-11", NOW), "Sáb 11 jul");
  assert.equal(F.cortaFija("2027-01-04", NOW), "Lun 4 ene 2027");
  assert.equal(F.cortaFija("no-es-fecha", NOW), "");
});

// ---- multi-idioma (responsable del producto, 2026-08-04): el catálogo real + i18n.js real en un
// contexto vm — inglés NO es castellano con las palabras cambiadas (día y mes
// van capitalizados, sin "de"), y castellano sigue exactamente igual con el
// catálogo puesto (prueba que activar i18n no mueve el render por defecto).

test("inglés: hoy/mañana y el resto capitalizado, sin 'de'", () => {
  withLang("en", (EN) => {
    assert.equal(EN.amigable("2026-07-10", "14:40", NOW), "Today, 14:40");
    assert.equal(EN.amigable("2026-07-11", "09:00", NOW), "Tomorrow, Saturday 11, 09:00");
    assert.equal(EN.amigable("2026-07-14", "14:40", NOW), "Tuesday 14 July, 14:40");
    assert.equal(EN.amigable("2027-01-05", "10:00", NOW), "Tuesday 5 January 2027, 10:00");
  });
});

test("inglés: larga() siempre absoluta, corta() abrevia sin acentos castellanos", () => {
  withLang("en", (EN) => {
    assert.equal(EN.larga("2026-07-10", "14:40", NOW), "Friday 10 July, 14:40");
    assert.equal(EN.corta("2026-07-29", "10:00", NOW), "Wed 29 Jul, 10:00");
    assert.equal(EN.corta("2027-01-04", "08:00", NOW), "Mon 4 Jan 2027, 08:00");
  });
});

test("inglés: relativo() y nacimiento() en su propio orden de palabras", () => {
  withLang("en", (EN) => {
    const now = new Date(2026, 6, 11, 14, 30, 0);
    assert.equal(EN.relativo("2026-07-11T14:29:50", now), "just now");
    assert.equal(EN.relativo("2026-07-11T14:10:00", now), "20 minutes ago");
    assert.equal(EN.relativo("2026-07-11T14:29:00", now), "1 minute ago");
    assert.equal(EN.relativo("2026-07-11T11:30:00", now), "3 hours ago");
    assert.equal(EN.nacimiento("1978-10-28"), "28 October 1978");
  });
});

test("castellano con el catálogo cargado: el render no se mueve un byte", () => {
  withLang("es", (ES) => {
    assert.equal(ES.amigable("2026-07-14", "14:40", NOW), "Martes 14 de julio, 14:40");
    assert.equal(ES.amigable("2027-01-05", "10:00", NOW), "Martes 5 de enero de 2027, 10:00");
    assert.equal(ES.corta("2026-07-29", "10:00", NOW), "Mié 29 jul, 10:00");
    assert.equal(ES.nacimiento("1978-10-28"), "28 de octubre de 1978");
  });
});
