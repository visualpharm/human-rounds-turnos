// tests/week.test.mjs — grilla semanal (fusión de días iguales). Correr: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import * as I18n from "../static/platform/i18n.mjs";
import * as H from "../static/platform/hours.mjs";
import * as S from "../static/platform/week.mjs";

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

async function withLang(lang, run) {
  const catalog = loadCatalog(lang);
  I18n.setCatalog(catalog);
  const prevWindow = global.window;
  global.window = {
    document: {
      documentElement: { getAttribute: () => lang },
      readyState: "complete",
      querySelectorAll: () => [],
    },
  };
  try {
    run(S);
  } finally {
    I18n.setCatalog({});
    global.window = prevWindow;
  }
}

test("aperturas puntuales: martes y jueves separados, resto fusionado", () => {
  const bs = S.bloques(H.parse("Martes 8 y 16 h; jueves 8 y 14 h"));
  assert.deepEqual(bs, [
    { label: "Lunes", dias: 1, horas: [] },
    { label: "Martes", dias: 1, horas: ["8:00", "16:00"] },
    { label: "Miércoles", dias: 1, horas: [] },
    { label: "Jueves", dias: 1, horas: ["8:00", "14:00"] },
    { label: "Viernes a domingo", dias: 3, horas: [] },
  ]);
});

test("intervalo L-V: un bloque de 5 días + finde fusionado", () => {
  const bs = S.bloques(H.parse("Lunes a viernes de 8 a 13 h"));
  assert.deepEqual(bs, [
    { label: "Lunes a viernes", dias: 5, horas: ["8:00 – 13:00"] },
    { label: "Sábado a domingo", dias: 2, horas: [] },
  ]);
});

test("un solo día con intervalo", () => {
  const bs = S.bloques(H.parse("Martes de 8 a 12 h"));
  assert.equal(bs.length, 3);
  assert.deepEqual(bs[1], { label: "Martes", dias: 1, horas: ["8:00 – 12:00"] });
  assert.deepEqual(bs.map(b => b.dias), [1, 1, 5]);
});

test("una única hora publicada se presenta como cita, no como apertura sin cierre", () => {
  const bs = S.bloques(H.parse("Lunes a viernes a las 6:15 h"));
  assert.deepEqual(bs, [
    { label: "Lunes a viernes", dias: 5, horas: ["A las 6:15"] },
    { label: "Sábado a domingo", dias: 2, horas: [] },
  ]);
});

test("dos franjas el mismo día quedan ordenadas por hora", () => {
  const bs = S.bloques(H.parse("Martes de 16 a 20 y de 8 a 12 h"));
  assert.deepEqual(bs[1].horas, ["8:00 – 12:00", "16:00 – 20:00"]);
});

test("semana entera igual = un único bloque", () => {
  const bs = S.bloques(H.parse("Lunes a domingo de 8 a 20 h"));
  assert.deepEqual(bs, [
    { label: "Lunes a domingo", dias: 7, horas: ["8:00 – 20:00"] },
  ]);
});

test("week null/vacía: siete días sin atención fusionados", () => {
  const bs = S.bloques(null);
  assert.deepEqual(bs, [{ label: "Lunes a domingo", dias: 7, horas: [] }]);
});

test("all shipped example schedules cover seven days", () => {
  const { servicios } = JSON.parse(readFileSync(join(ROOT, "packs/example-town/servicios.json"), "utf8"));
  for (const s of servicios) {
    const wk = H.parse(s.horario);
    if (!wk) continue;
    const bs = S.bloques(wk);
    assert.equal(bs.reduce((n, b) => n + b.dias, 0), 7, s.horario);
  }
});

test("con sidebar angosto la semana usa filas de día y horario", () => {
  const src = readFileSync(new URL("../static/platform/week.mjs", import.meta.url), "utf8");
  assert.match(src, /@container stzc-page \(max-width:900px\)/);
  assert.match(src, /grid-template-columns:minmax\(120px,1fr\) minmax\(0,1\.4fr\)/);
});

// ---- multi-idioma (the product owner, 2026-08-04): "Lunes a viernes" -> "Monday to
// Friday" — inglés capitaliza LOS DOS nombres (castellano baja el segundo a
// minúscula mid-frase), y "A las 6:15" -> "At 6:15".

test("bloques en inglés: los dos nombres capitalizados, con 'to'", async () => {
  await withLang("en", (S2) => {
    const bs = S2.bloques(H.parse("Lunes a viernes de 8 a 13 h"));
    assert.deepEqual(bs, [
      { label: "Monday to Friday", dias: 5, horas: ["8:00 – 13:00"] },
      { label: "Saturday to Sunday", dias: 2, horas: [] },
    ]);
  });
});

test("bloques en inglés: una única hora publicada dice 'At', no 'A las'", async () => {
  await withLang("en", (S2) => {
    const bs = S2.bloques(H.parse("Lunes a viernes a las 6:15 h"));
    assert.deepEqual(bs, [
      { label: "Monday to Friday", dias: 5, horas: ["At 6:15"] },
      { label: "Saturday to Sunday", dias: 2, horas: [] },
    ]);
  });
});

test("castellano con el catálogo cargado: bloques() no se mueve", async () => {
  await withLang("es", (S2) => {
    const bs = S2.bloques(H.parse("Lunes a viernes de 8 a 13 h"));
    assert.deepEqual(bs, [
      { label: "Lunes a viernes", dias: 5, horas: ["8:00 – 13:00"] },
      { label: "Sábado a domingo", dias: 2, horas: [] },
    ]);
  });
});

test("the native week renderer owns its real dependencies and exposes no compatibility global", () => {
  const src = readFileSync(new URL("../static/platform/week.mjs", import.meta.url), "utf8");
  assert.match(src, /importDep\("platform\/i18n\.mjs"/);
  assert.match(src, /importDep\("platform\/hours\.mjs"/);
  assert.match(src, /export \{ CSS, activeLang, bloques, init, render \}/);
  assert.doesNotMatch(src, /SanaWeek|module\.exports/);
});

test("closed days use the active language catalog", async () => {
  await withLang("en", (Week) => {
    const host = {
      getAttribute: (name) => name === "data-horario" ? "Lunes de 8 a 12 h" : "",
      ownerDocument: { getElementById: () => ({}), createElement: () => ({}), head: { appendChild() {} } },
      innerHTML: "",
    };
    assert.equal(Week.render(host), true);
    assert.match(host.innerHTML, /Closed/);
  });
});
