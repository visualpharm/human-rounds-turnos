// tests/admin-charts.test.mjs — native admin chart library (Human Rounds).
// Correr: node --test tests/admin-charts.test.mjs
import { describe, test } from "node:test";
import assert from "node:assert/strict";
import { setCatalog } from "../static/platform/i18n.mjs";
import * as C from "../static/platform/admin-charts.mjs";
const I = C._internal;

// Chart copy reads the shared i18n singleton. Catalog-mutating cases must
// not run beside the Spanish-fallback assertions in the same file.

// =====================================================================
// formateo de números (es-AR: miles con ".", sin decimales falsos)
// =====================================================================

test("fmtInt: miles con punto, sin decimales", () => {
  assert.equal(I.fmtInt(1234), "1.234");
  assert.equal(I.fmtInt(1234567), "1.234.567");
  assert.equal(I.fmtInt(7), "7");
});

test("fmtInt: nulo/NaN da 's/d', nunca guión largo", () => {
  assert.equal(I.fmtInt(null), "s/d");
  assert.equal(I.fmtInt(NaN), "s/d");
});

test("fmtNum: entero se muestra entero, fraccionario con 1 decimal", () => {
  assert.equal(I.fmtNum(42), "42");
  assert.equal(I.fmtNum(42.0), "42");
  assert.equal(I.fmtNum(3.14159), "3,1");
  assert.equal(I.fmtNum(1234.5), "1.234,5");
});

test("fmtDec: hasta los decimales pedidos, sin ceros de relleno", () => {
  assert.equal(I.fmtDec(3.14159, 2), "3,14");
  assert.equal(I.fmtDec(3, 2), "3");
  assert.equal(I.fmtInt(4188), "4.188");
});

// =====================================================================
// niceTicks — redondeo humano de ejes
// =====================================================================

test("niceTicks: dominio simple 0..100 da un paso redondo", () => {
  const t = I.niceTicks(0, 97, 4);
  assert.equal(t.min, 0);
  assert.ok(t.max >= 97);
  assert.ok([1, 2, 2.5, 5, 10, 20, 25, 50, 100].some((s) => Math.abs(t.step - s) < 1e-6) || t.step % 10 === 0);
  assert.ok(t.ticks.length >= 3);
});

test("niceTicks: min===max no cuelga y produce un rango con ancho positivo", () => {
  const t = I.niceTicks(5, 5, 4);
  assert.ok(t.max > t.min);
});

test("niceTicks: min y max invertidos se reordenan solos", () => {
  const t = I.niceTicks(50, 0, 4);
  assert.ok(t.min <= t.max);
});

// =====================================================================
// truncado de etiquetas largas
// =====================================================================

test("truncateToWidth: etiqueta corta no se toca", () => {
  const r = I.truncateToWidth("Cardio", 200, 12);
  assert.equal(r.truncated, false);
  assert.equal(r.text, "Cardio");
});

test("truncateToWidth: etiqueta larga se corta con elipsis", () => {
  const larga = "Consultorio de Medicina General y Clínica Ambulatoria Integral";
  const r = I.truncateToWidth(larga, 80, 12);
  assert.equal(r.truncated, true);
  assert.ok(r.text.endsWith("…"));
  assert.ok(r.text.length < larga.length);
});

// =====================================================================
// scatter — línea de referencia diagonal
// =====================================================================

test("scatter: línea diagonal con pendiente conocida llega al punto y=pendiente*x", () => {
  const rows = [
    { x: 10, y: 8, etiqueta: "Cardiología" },
    { x: 20, y: 5, etiqueta: "Clínica médica" },
  ];
  const html = C.scatter(rows, { linea: { tipo: "diagonal", pendiente: 1, etiqueta: "1x" }, ejeX: "Demanda", ejeY: "Oferta" });
  // se dibuja una <line> punteada (stroke-dasharray) para la referencia
  const m = html.match(/<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)" style="stroke:var\(--text-3\);stroke-width:1\.5;stroke-dasharray/);
  assert.ok(m, "debe existir una línea de referencia punteada");
  // el eje Y en pixeles crece hacia abajo: y1 (extremo en x=xMin) debe ser >= y2 (extremo en x=xMax)
  // cuando la pendiente es positiva, porque a mayor x, mayor y de dato -> menor y de pixel.
  const y1 = parseFloat(m[2]), y2 = parseFloat(m[4]);
  assert.ok(y1 >= y2, "con pendiente positiva el extremo derecho debe estar más arriba (y de pixel menor)");
});

describe("chart copy follows the active catalog", { concurrency: false }, () => {
test("scatter: sin datos devuelve un estado vacío, no revienta", () => {
  const html = C.scatter([], {});
  assert.ok(html.includes("Sin datos"));
});

test("scatter: resaltar pinta el punto con var(--warn) y el resto con var(--accent)", () => {
  const rows = [{ x: 1, y: 1, etiqueta: "A" }, { x: 2, y: 2, etiqueta: "B" }];
  const html = C.scatter(rows, { resaltar: ["B"] });
  assert.match(html, /style="fill:var\(--warn\)"/);
  assert.match(html, /style="fill:var\(--accent\)"/);
});

// round 2026-07-22: un punto en el ~40% derecho del plot ancla su etiqueta a
// la izquierda (anchor "end") para no desbordar el borde derecho; uno en el
// resto del plot ancla a la derecha (anchor "start") — nunca "middle", que
// era lo que hacía que una etiqueta cerca del eje Y se desbordara hacia
// afuera del área de trazado.
test("scatter: un punto a la derecha ancla su etiqueta a 'end', uno a la izquierda a 'start'", () => {
  const rows = [{ x: 1, y: 1, etiqueta: "Cerca del eje" }, { x: 99, y: 1, etiqueta: "Cerca del borde derecho" }];
  const html = C.scatter(rows, { baseCero: false });
  assert.match(html, /text-anchor="start"[^>]*>Cerca del eje/);
  assert.match(html, /text-anchor="end"[^>]*>Cerca del borde derecho/);
  // los ticks del eje X sí usan anchor "middle" (no son etiquetas de punto);
  // ninguna de las dos etiquetas de punto lo usa.
  assert.ok(!/text-anchor="middle"[^>]*>Cerca del/.test(html), "nunca debe anclar una etiqueta de punto al medio");
});

// round 2026-07-22 (2ª pasada): al chocar, la segunda etiqueta APILA hacia
// ARRIBA — bajarla atravesaba la fila densa de puntos pegados al eje X y los
// números de los ticks (bug real en "Médicos"). Contrato: nunca encimadas, y
// la segunda queda MÁS ARRIBA que la primera.
test("scatter: dos puntos casi superpuestos no enciman etiquetas: la segunda apila hacia arriba", () => {
  const rows = [{ x: 10, y: 10, etiqueta: "Primero" }, { x: 10.2, y: 10.1, etiqueta: "Segundo" }];
  const html = C.scatter(rows);
  const ys = [...html.matchAll(/<text x="[\d.]+" y="([\d.]+)"[^>]*>(Primero|Segundo)/g)].map((m) => [m[2], parseFloat(m[1])]);
  const yPrimero = ys.find((p) => p[0] === "Primero")[1];
  const ySegundo = ys.find((p) => p[0] === "Segundo")[1];
  assert.notEqual(ySegundo, yPrimero, "las etiquetas no pueden quedar encimadas");
  assert.ok(ySegundo < yPrimero, "la segunda etiqueta apila hacia arriba, nunca hacia la fila de puntos del eje");
});

// ui-expert 2026-11-15 (demo, report Médicos): «Camila Levin», raised two
// steps, sat on Emiliano Molina's point. A name is drawn nearer its own point
// than any other point, or it carries a leader line to its point.
test("scatter: every name sits nearer its own point than any other, or has a leader", () => {
  const rows = [
    { x: 84, y: 310, etiqueta: "Laura Katz" }, { x: 58, y: 224, etiqueta: "Emiliano Molina" },
    { x: 53, y: 195, etiqueta: "Camila Levin" }, { x: 58, y: 190, etiqueta: "Gustavo Ibarra" },
    { x: 84, y: 182, etiqueta: "Juan González" }, { x: 84, y: 155, etiqueta: "Miguel Kowalski" },
    { x: 53, y: 170 }, { x: 53, y: 140 }, { x: 53, y: 115 }, { x: 58, y: 128 }, { x: 35, y: 155 }, { x: 35, y: 100 },
  ];
  const html = C.scatter(rows, { ejeX: "Horas semanales ofrecidas", ejeY: "Turnos", linea: { tipo: "diagonal", pendiente: 2.9, etiqueta: "mediana turnos/hora" } });
  const pts = [...html.matchAll(/<circle cx="([\d.]+)" cy="([\d.]+)"[^>]*><title>([^:<]*):/g)].map(m => ({ x: +m[1], y: +m[2], name: m[3] }));
  const leaders = [...html.matchAll(/class="sana-sc-leader" x1="[\d.]+" y1="[\d.]+" x2="([\d.]+)" y2="([\d.]+)"/g)].map(m => ({ x: +m[1], y: +m[2] }));
  // the numbered list under the chart: «n» → name
  const listados = new Map([...html.matchAll(/<b class="sana-sc-num">(\d+)<\/b>([^<]+)<\/li>/g)].map(m => [m[2], m[1]]));
  for (const r of rows.filter(r => r.etiqueta)) {
    const own = pts.find(p => p.name === r.etiqueta);
    const num = listados.get(r.etiqueta);
    if (num) {
      // a number in place of the name: the number itself sits nearest its own point
      const t = html.match(new RegExp('<text x="([\\d.]+)" y="([\\d.]+)"[^>]*font-weight:700[^>]*>' + num + "<"));
      assert.ok(t, r.etiqueta + " has its number " + num + " on the chart");
      const d = p => Math.hypot(p.x - +t[1], p.y - (+t[2] - 4));
      const nearest = pts.reduce((a, b) => (d(b) < d(a) ? b : a));
      assert.equal(nearest.name, r.etiqueta, "number " + num + " reads as " + nearest.name + "'s");
      continue;
    }
    // one line, or two lines as <tspan>s (the first word is the anchor)
    const t = html.match(new RegExp('<text x="([\\d.]+)" y="([\\d.]+)"[^>]*text-anchor="(start|end)"[^>]*>' + r.etiqueta + "<"))
      || html.match(new RegExp('<text text-anchor="(?:start|end)"[^>]*><tspan x="([\\d.]+)" y="([\\d.]+)">' + r.etiqueta.split(" ")[0] + "<"));
    assert.ok(t, r.etiqueta + " is drawn, or listed with a number");
    const ax = +t[1], ay = +t[2] - 5;
    const d = p => Math.hypot(p.x - ax, p.y - ay);
    const nearest = pts.reduce((a, b) => (d(b) < d(a) ? b : a));
    const led = leaders.some(l => Math.abs(l.x - own.x) < 0.5);
    assert.ok(nearest === own || led, r.etiqueta + " reads as " + nearest.name + "'s name");
  }
  // the median's caption sits clear of every point (ui-expert 15.11: it ran
  // through Laura Katz's point at 390)
  const cap = html.match(/<text x="([\d.]+)" y="([\d.]+)"[^>]*>mediana turnos\/hora</);
  assert.ok(cap, "the median is captioned");
  const cx1 = +cap[1], cy = +cap[2];
  const capW = "mediana turnos/hora".length * 6.5;
  for (const p of pts) {
    const inside = p.x > cx1 - capW - 4 && p.x < cx1 + 4 && p.y > cy - 15 && p.y < cy + 7;
    assert.ok(!inside, "the median caption covers " + (p.name || "a point"));
  }
  // the X-axis title is drawn inside the chart, at the end of the axis
  assert.match(html, /text-anchor="end"[^>]*>Horas semanales ofrecidas</);
});

// round 2026-07-22: opts.agrupar colorea por r.grupo con la paleta
// categórica e imprime una leyenda debajo del gráfico (usado por "Médicos"
// para colorear por centro).
test("scatter: opts.agrupar colorea por grupo y dibuja una leyenda", () => {
  const rows = [
    { x: 1, y: 1, etiqueta: "A", grupo: "Hospital" },
    { x: 2, y: 2, etiqueta: "B", grupo: "CAPS Ostende" },
  ];
  const html = C.scatter(rows, { agrupar: true });
  assert.match(html, /sana-legend/);
  assert.ok(html.includes("Hospital"));
  assert.ok(html.includes("CAPS Ostende"));
  assert.match(html, /style="fill:var\(--cat-1,#2a78d6\)"/);
  assert.match(html, /style="fill:var\(--cat-2,#d95926\)"/);
});

// =====================================================================
// heatmap — umbral de impresión de valores
// =====================================================================

test("heatmap: sólo imprime el valor en celdas por encima del umbral", () => {
  const grid = { dias: ["Lunes"], horas: ["08-10", "10-12"], celdas: [[3, 8]] };
  const html = C.heatmap(grid, { umbral: 5 });
  assert.ok(html.includes(">8<"), "8 está por encima del umbral y debe imprimirse");
  assert.ok(!html.includes(">3<"), "3 está por debajo del umbral y NO debe imprimirse como texto de celda");
  // igual debe llevar el detalle completo en el tooltip
  assert.match(html, /<title>Lunes 08-10: 3<\/title>/);
});

test("heatmap: marcar dibuja el contorno de la celda con var(--warn)", () => {
  const grid = { dias: ["Martes"], horas: ["08-10"], celdas: [[4]] };
  const html = C.heatmap(grid, { marcar: [{ d: "Martes", h: "08-10" }] });
  assert.match(html, /stroke:var\(--warn\);stroke-width:2/);
});

// round 2026-07-22: "carpeted heatmap" — una celda con valor fraccionario
// (<1, típico de repartir preferencias de franja en partes iguales) sigue
// mostrando su tinte pero deja de imprimir el número; una celda con valor
// entero >=1 sigue imprimiéndose como antes.
test("heatmap: celdas con valor <1 mantienen el tinte pero no imprimen número (default)", () => {
  const grid = { dias: ["Lunes"], horas: ["08-10", "10-12"], celdas: [[0.2, 3]] };
  const html = C.heatmap(grid);
  assert.ok(!html.includes(">0,2<"), "0,2 no debe imprimirse como texto de celda");
  assert.ok(html.includes(">3<"), "3 (>=1) debe seguir imprimiéndose");
  // el tinte (fill-opacity) de la celda fraccionaria sigue siendo > 0
  assert.match(html, /fill-opacity:0\.0[6-9][^"]*"><title>Lunes 08-10: 0,2/);
});

test("heatmap: on a phone the cells narrow and the drawing keeps its width, so labels stay 13px", () => {
  const horas = [];
  for (let h = 7; h < 21; h++) horas.push(String(h).padStart(2, "0") + " h");
  const grid = { dias: ["Lunes", "Martes"], horas, celdas: [horas.map(() => 2), horas.map(() => 1)] };
  const html = C.heatmap(grid, { ancho: 340 });
  assert.match(html, /viewBox="0 0 340 /);
});

test("treemap/heatmap: white text only on a near-solid accent, dark ink on the middle tier", () => {
  const html = C.treemap([{ nombre: "Uno", valor: 50 }, { nombre: "Dos", valor: 30 }, { nombre: "Tres", valor: 20 }], { ancho: 680 });
  assert.match(html, /color:#fff">Uno</);
  assert.match(html, /color:var\(--text\)">Dos</);
  const hm = C.heatmap({ dias: ["Lunes"], horas: ["08 h", "10 h"], celdas: [[10, 7]] }, { ancho: 680 });
  assert.match(hm, /fill:var\(--text\)[^>]*>7</);
});

test("linea: two close last values never sit on top of each other", () => {
  const pts = (ys) => ys.map((y, i) => ({ x: i, etiqueta: "S" + (i + 1), y }));
  const html = C.linea([{ nombre: "A", puntos: pts([20, 30, 36]) }, { nombre: "B", puntos: pts([18, 28, 35]) }], { ancho: 680 });
  const ys = [">36<", ">35<"].map((v) => {
    const i = html.indexOf(v);
    const tag = html.slice(html.lastIndexOf("<text", i), i);
    return Number(/ y="([-\d.]+)"/.exec(tag)[1]);
  });
  assert.ok(Math.abs(ys[0] - ys[1]) >= 13, "last values " + ys.join(" / ") + " overlap");
});

test("linea: a four-digit last value prints whole, not cut to «3.2…»", () => {
  const pts = (ys) => ys.map((y, i) => ({ x: i, etiqueta: "S" + (i + 1), y }));
  const html = C.linea([{ nombre: "A", puntos: pts([1200, 2100, 3214]) }, { nombre: "B", puntos: pts([900, 1500, 2876]) }], { ancho: 1100 });
  assert.match(html, />3\.214</);
  assert.match(html, />2\.876</);
  assert.doesNotMatch(html, /…</);
});

test("piramide: the longest bar leaves room for its count inside the chart", () => {
  const html = C.piramide([{ rango: "0-17", cantidad: 664, f: 384, m: 280 }, { rango: "30-44", cantidad: 1663, f: 1010, m: 653 }], { ancho: 360 });
  const i = html.indexOf(">1.010<");
  assert.ok(i > 0, "1.010 missing");
  const tag = html.slice(html.lastIndexOf("<text", i), i);
  const x = Number(/ x="([-\d.]+)"/.exec(tag)[1]);
  // Anchored at its end: the text runs left from x and must not cross 0.
  assert.ok(x - 5 * 13 * 0.6 >= 0, "the count starts left of the chart: x=" + x);
});

test("lollipop: no value runs over the reference line, and every value sits next to its own point", () => {
  // Review 15.11: a shared column past the line left values far from their points.
  const rows = [{ nombre: "Salud mental", valor: 52.1 }, { nombre: "Otorrinolaringología y fonoaudiología", valor: 45.2 },
    { nombre: "Endocrinología, diabetes y nutrición", valor: 26.3 }, { nombre: "Diagnóstico y imagen", valor: 14 },
    { nombre: "Pediatría", valor: 96 }];
  const fmt = (v) => String(v).replace(".", ",") + "%";
  [360, 1100].forEach((ancho) => {
    const html = C.lollipop(rows, { ancho, fmtValor: fmt, ref: { valor: 100, etiqueta: "capacidad completa" } });
    const rx = Number(/<line x1="([\d.]+)"[^>]*stroke-dasharray/.exec(html)[1]);
    const dots = [...html.matchAll(/<circle cx="([\d.]+)"/g)].map((mm) => Number(mm[1]));
    rows.forEach((r, k) => {
      const v = ">" + fmt(r.valor) + "<";
      const i = html.indexOf(v);
      assert.ok(i > 0, v + " missing");
      const tag = html.slice(html.lastIndexOf("<text", i), i);
      const x = Number(/ x="([-\d.]+)"/.exec(tag)[1]);
      const end = /text-anchor="end"|anchor:end|text-anchor:end/.test(tag);
      const w = fmt(r.valor).length * 6.5;
      const x0 = end ? x - w : x, x1 = end ? x : x + w;
      assert.ok(!(x0 < rx && x1 > rx), ancho + ": " + v + " crosses the line at " + rx);
      assert.ok(Math.abs(x - dots[k]) <= 12, ancho + ": " + v + " at " + x + ", its point at " + dots[k]);
    });
  });
  // On a desk the values stay next to their points.
  const ancho = C.lollipop(rows, { ancho: 1100, fmtValor: fmt, ref: { valor: 100, etiqueta: "capacidad completa" } });
  const rx2 = Number(/<line x1="([\d.]+)"[^>]*stroke-dasharray/.exec(ancho)[1]);
  const j = ancho.indexOf(">52,1%<");
  const x2 = Number(/ x="([-\d.]+)"/.exec(ancho.slice(ancho.lastIndexOf("<text", j), j))[1]);
  assert.ok(x2 < rx2, "desk value moved past the line");
});

// =====================================================================
// treemap — áreas proporcionales al valor (±1%)
// =====================================================================

// treemap es HTML (divs posicionados por %, no SVG rects) desde 2026-07-22 —
// así un nombre largo puede partir en hasta 2 líneas (line-clamp CSS) antes
// de elipsizar, algo que el texto de SVG no hace sin envolver a mano.
test("treemap: el área de cada tile (% ancho × % alto) es proporcional a su valor dentro de ±1%", () => {
  const items = [
    { nombre: "OSDE", valor: 129 },
    { nombre: "IOMA", valor: 90 },
    { nombre: "Federada", valor: 63 },
    { nombre: "PAMI", valor: 40 },
  ];
  const html = C.treemap(items);
  // cada tile es un chunk que arranca justo después del marcador de clase;
  // partir por ese literal evita tener que balancear los <div> anidados
  // (bg/label/value) con una sola regex.
  const chunks = html.split('<div class="sana-tm-tile"').slice(1);
  assert.equal(chunks.length, items.length);
  const tiles = chunks.map((c) => {
    const m = c.match(/style="left:([\d.]+)%;top:([\d.]+)%;width:([\d.]+)%;height:([\d.]+)%"/);
    return { w: parseFloat(m[3]), h: parseFloat(m[4]), texto: c };
  });
  const totalValor = items.reduce((a, i) => a + i.valor, 0);
  const totalArea = tiles.reduce((a, t) => a + t.w * t.h, 0);
  items.forEach((it) => {
    const t = tiles.find((tt) => tt.texto.includes(it.nombre));
    assert.ok(t, "debe haber un tile para " + it.nombre);
    const area = t.w * t.h;
    const expectedFrac = it.valor / totalValor;
    const actualFrac = area / totalArea;
    assert.ok(Math.abs(expectedFrac - actualFrac) < 0.01, it.nombre + ": esperado " + expectedFrac + " obtenido " + actualFrac);
  });
});

test("treemap: un nombre que no entra entero en su tile va a la lista de abajo, nunca cortado", () => {
  const html = C.treemap([{ nombre: "Muy chico", valor: 6 }, { nombre: "Grande", valor: 94 }]);
  assert.match(html, /title="Muy chico: 6"/);
  assert.match(html, /sana-tm-tile--compact/);
  // the narrow tile carries no label of its own…
  const chico = html.split('<div class="sana-tm-tile').find((c) => c.includes('title="Muy chico: 6"'));
  assert.ok(!chico.includes("sana-tm-label"));
  // …it is listed whole, with its value, under the treemap
  assert.match(html, /<ul class="sana-tm-rest"><li class="sana-tm-rest-item">.*Muy chico 6<\/li><\/ul>/);
  assert.match(html, /aria-label="Grande: 94"/);
  assert.match(html, /<div class="sana-tm-label"[^>]*>Grande<\/div>/);
  assert.ok(!html.includes("…"));
});

// ui-expert 15.11: small tiles read «PAMI 5» on one line while the others
// stacked name over value. One layout: a short tile shows its name alone and
// its value goes to a list — the chart's own, or the report's (valoresEnLista).
test("treemap: one layout in every tile; a short tile drops its value, never runs name and value together", () => {
  const items = [{ nombre: "IOMA", valor: 60 }, { nombre: "OSDE", valor: 25 }, { nombre: "PAMI", valor: 9 },
    { nombre: "Galeno", valor: 6 }];
  for (const ancho of [340, 1200]) {
    for (const enLista of [false, true]) {
      const html = C.treemap(items, { ancho, valoresEnLista: enLista });
      for (const it of items) {
        assert.ok(!html.includes(">" + it.nombre + " " + it.valor + "</div>"), ancho + ": no tile runs «" + it.nombre + " " + it.valor + "» on one line");
        const tile = html.split('<div class="sana-tm-tile').find((c) => c.includes('title="' + it.nombre + ": " + it.valor + '"'));
        const conValor = tile.includes("sana-tm-value");
        const listado = new RegExp("sana-tm-rest-item\">.*" + it.nombre + " " + it.valor + "<").test(html);
        if (!conValor) assert.ok(enLista ? !listado || !tile.includes("sana-tm-label") : listado, ancho + ": " + it.nombre + " value is listed under the chart");
        if (conValor) assert.ok(!listado, ancho + ": " + it.nombre + " is not listed twice");
      }
    }
  }
});

// review E4 (08.11): on a phone a crowded cluster of specialties showed
// «Endocrinología, diabetes…» stacked 80 px above points it no longer touched.
// A name is whole and next to its point, or its point takes a number and the
// name is listed under the chart.
test("scatter: a crowded cluster on a phone never cuts a name; points without room take a number listed under the chart", () => {
  const nombres = ["Clínica y medicina general", "Endocrinología, diabetes y nutrición", "Otras especialidades",
    "Pediatría", "Otorrinolaringología y fonoaudiología", "Diagnóstico y tratamiento por imágenes"];
  const rows = nombres.map((n, i) => ({ x: 60 + i * 12, y: 8 + (i % 3), etiqueta: n }));
  rows.push({ x: 420, y: 14, etiqueta: "Kinesiología" });
  const html = C.scatter(rows, { ancho: 340, linea: { tipo: "diagonal", pendiente: 0.25, etiqueta: "equilibrio" } });
  const svgPart = html.split("<ul")[0];
  assert.ok(!/…/.test(html), "no label ends in an ellipsis");
  assert.match(html, /class="sana-tm-rest sana-sc-rest"/, "the names without room are listed");
  for (const n of nombres) assert.ok(html.includes(n), "every name is shown whole: " + n);
  const listados = [...html.matchAll(/<b class="sana-sc-num">(\d+)<\/b>/g)].map((m) => m[1]);
  assert.ok(listados.length > 0, "listed points carry their number");
  for (const num of listados) assert.match(svgPart, new RegExp(">" + num + "</text>"), "the number is drawn next to its point: " + num);
  const ancho = C.scatter(rows.slice(0, 2), { ancho: 1200 });
  assert.ok(!ancho.includes("sana-sc-rest"), "with room every name stays next to its point");
});

test("treemap: la decisión de entrar se toma en el ancho real del gráfico (teléfono)", () => {
  const items = [
    { nombre: "Trato del personal", valor: 50 }, { nombre: "Turnos y acceso telefónico", valor: 25 },
    { nombre: "Limpieza", valor: 15 }, { nombre: "Estacionamiento", valor: 6 }, { nombre: "Ruido", valor: 4 },
  ];
  const ancho = C.treemap(items, { ancho: 1200 });
  const angosto = C.treemap(items, { ancho: 340 });
  assert.equal((ancho.match(/class="sana-tm-label"/g) || []).length, items.length, "a desk width fits every name in its tile");
  assert.match(angosto, /sana-tm-rest/, "a phone width lists the names that no longer fit");
});

// ui-expert 08.11: «Vacunas y prevención» with 1 request filled a 260 px solid
// tile across the phone. One category is the whole composition: a short tile.
test("treemap: the height follows the number of tiles, one category alone is a short tile with its name and value", () => {
  const alto = (html) => Number(html.match(/class="sana-tm" style="height:(\d+)px"/)[1]);
  const uno = C.treemap([{ nombre: "Vacunas y prevención", valor: 1 }], { ancho: 340 });
  assert.equal(alto(uno), 80);
  assert.match(uno, /<div class="sana-tm-label"[^>]*>Vacunas y prevención<\/div>/);
  assert.match(uno, /<div class="sana-tm-value"[^>]*>1<\/div>/);
  const items = (k) => Array.from({ length: k }, (_, i) => ({ nombre: "C" + i, valor: 10 + i }));
  assert.ok(alto(C.treemap(items(2))) > 80 && alto(C.treemap(items(2))) < 260);
  assert.equal(alto(C.treemap(items(6))), 260);
});

test("treemap: el nombre completo llega sin truncar por caracteres (el wrap a 2 líneas es CSS, no recorte de texto)", () => {
  const html = C.treemap([{ nombre: "Evitable antes de enviar", valor: 10 }]);
  assert.ok(html.includes("Evitable antes de enviar"));
  assert.ok(!html.includes("…"));
});

test("treemap: la CSS inyectada de .sana-tm-label permite hasta 3 líneas enteras (line-clamp)", () => {
  const el = {};
  global.document = { createElement: () => el, head: { appendChild: () => {} } };
  try {
    C.treemap([{ nombre: "Evitable antes de enviar", valor: 10 }]);
  } finally {
    delete global.document;
  }
  assert.match(el.textContent || "", /-webkit-line-clamp:3/);
  assert.doesNotMatch(el.textContent || "", /hyphens:auto/);
});

test("treemapLayout: caso puro con 2 ítems reparte el ancho exactamente por proporción", () => {
  const layout = I.treemapLayout([{ nombre: "A", valor: 3 }, { nombre: "B", valor: 1 }], 0, 0, 400, 100, true);
  assert.equal(layout.length, 2);
  const a = layout.find((t) => t.item.nombre === "A");
  const b = layout.find((t) => t.item.nombre === "B");
  assert.ok(Math.abs(a.w - 300) < 1e-6);
  assert.ok(Math.abs(b.w - 100) < 1e-6);
  assert.equal(a.h, 100);
  assert.equal(b.h, 100);
});

// =====================================================================
// treemap — item.color explícito (escala de sentimiento de Centros, 2026-07-22)
// =====================================================================

test("treemap: item.color explícito pisa el par fijo COL_ACCENT/COL_WARN y corre a opacidad plena", () => {
  const html = C.treemap([
    { nombre: "Tema rojo", valor: 10, color: "rgb(178,94,71)" },
    { nombre: "Tema chico", valor: 1, color: "rgb(111,153,89)" },
  ]);
  // aunque "Tema chico" cae en el tier de tiles más chicos, con color propio
  // no se desvanece por tamaño — el color de sentimiento debe leerse igual
  // de fuerte sea cual sea el tile.
  assert.match(html, /background:rgb\(178,94,71\);opacity:1/);
  assert.match(html, /background:rgb\(111,153,89\);opacity:1/);
});

test("treemap: sin item.color, el esquema viejo (tono 'mal'->COL_WARN, opacidad por tier) sigue igual", () => {
  const html = C.treemap([
    { nombre: "A", valor: 10, tono: "mal" },
    { nombre: "B", valor: 5 },
  ]);
  assert.match(html, /background:var\(--warn\);opacity:0\.88/);
  assert.match(html, /background:var\(--accent\);opacity:0\.62/);
});

test("treemap: item.color decide el contraste del texto por luminancia real, no por la opacidad de la tier", () => {
  const htmlOscuro = C.treemap([{ nombre: "Tema oscuro", valor: 50, color: "rgb(60,40,30)" }]);
  assert.match(htmlOscuro, /color:#fff/);
  const htmlClaro = C.treemap([{ nombre: "Tema claro", valor: 50, color: "rgb(230,220,200)" }]);
  assert.match(htmlClaro, /color:var\(--text\)/);
});

// =====================================================================
// slope — semántica de color (mejora vs empeora)
// =====================================================================

test("slope: por defecto bajar (mejora) es var(--accent) y subir es var(--warn)", () => {
  const pares = [{ nombre: "Espera Cardiología", antes: 20, despues: 8 }, { nombre: "Espera Clínica", antes: 5, despues: 12 }];
  const html = C.slope(pares);
  // la primera línea (baja, mejora) debe usar accent; la segunda (sube, empeora) warn
  assert.match(html, /stroke:var\(--accent\);stroke-width:2/);
  assert.match(html, /stroke:var\(--warn\);stroke-width:2/);
});

test("slope: con opts.mejorSube=true, subir es var(--accent)", () => {
  const pares = [{ nombre: "Ocupación", antes: 40, despues: 70 }];
  const html = C.slope(pares, { mejorSube: true });
  assert.match(html, /stroke:var\(--accent\);stroke-width:2/);
  assert.ok(!/stroke:var\(--warn\);stroke-width:2/.test(html));
});

// =====================================================================
// bans de la doctrina: nada de guión largo, nada de gráfico de barras
// =====================================================================

test("ningún gráfico genera un guión largo (em-dash) en el HTML de salida", () => {
  const outputs = [
    C.scatter([{ x: 1, y: 2, etiqueta: "A" }], { linea: { tipo: "diagonal", pendiente: 1, etiqueta: "1x" }, read: "Lectura de prueba" }),
    C.heatmap({ dias: ["Lunes"], horas: ["08-10"], celdas: [[5]] }, { read: "Lectura" }),
    C.treemap([{ nombre: "A", valor: 10 }, { nombre: "B", valor: 5 }], { read: "Lectura" }),
    C.slope([{ nombre: "X", antes: 10, despues: 5 }], { read: "Lectura" }),
    C.lollipop([{ nombre: "X", valor: 3 }], { ref: { valor: 1, etiqueta: "1x" }, read: "Lectura" }),
    C.linea([{ nombre: "Serie", puntos: [{ x: 1, etiqueta: "L", y: 1 }, { x: 2, etiqueta: "M", y: 2 }] }], { banda: [{ x: 1, y0: 0, y1: 2 }], read: "Lectura" }),
    C.tile({ etiqueta: "KPI", valor: "42", sub: "sub", pill: { texto: "OK", tono: "ok" } }),
    C.hero([{ texto: "Frase uno", tono: "ok" }, { texto: "Frase dos", tono: "atencion" }]),
  ];
  outputs.forEach((html) => {
    assert.ok(!html.includes("—"), "no debe contener un guión largo (—)");
  });
});

test("ningún gráfico define ni usa una clase de gráfico de columnas/barras verticales", () => {
  const outputs = [
    C.scatter([{ x: 1, y: 2, etiqueta: "A" }]),
    C.heatmap({ dias: ["Lunes"], horas: ["08-10"], celdas: [[5]] }),
    C.treemap([{ nombre: "A", valor: 10 }]),
    C.slope([{ nombre: "X", antes: 10, despues: 5 }]),
    C.lollipop([{ nombre: "X", valor: 3 }]),
    C.linea([{ nombre: "S", puntos: [{ x: 1, etiqueta: "L", y: 1 }] }]),
  ];
  outputs.forEach((html) => {
    assert.ok(!/class="[^"]*\bbar\b[^"]*"/i.test(html), "no debe emitir ninguna clase que contenga 'bar'");
    assert.ok(!/rep-vol-bar/.test(html), "no debe reusar la clase de barras verticales de admin-reports.js");
  });
});

// The report prints «consultas por hora» with the table's two decimals; the
// engine's own rounding wrote 0,01 as «0,0» (critical review 08.11 E9).
test("lollipop: opts.fmtValor prints the point's value; without it the engine's own format", () => {
  const conFmt = C.lollipop([{ nombre: "Clínica", valor: 0.013 }], { fmtValor: v => v.toFixed(2).replace(".", ",") });
  assert.match(conFmt, />0,01</);
  assert.doesNotMatch(conFmt, />0,0</);
  assert.match(C.lollipop([{ nombre: "Clínica", valor: 3 }]), />3</);
});

test("scatter/heatmap/treemap/slope/lollipop/linea imprimen la línea 'Cómo leerlo:' cuando se pasa opts.read", () => {
  const withRead = [
    C.scatter([{ x: 1, y: 2, etiqueta: "A" }], { read: "Puntos arriba de la línea piden más de lo que se ofrece" }),
    C.heatmap({ dias: ["Lunes"], horas: ["08-10"], celdas: [[5]] }, { read: "Las celdas más oscuras son las horas más pedidas" }),
    C.treemap([{ nombre: "A", valor: 10 }], { read: "El bloque más grande es la obra social más frecuente" }),
    C.slope([{ nombre: "X", antes: 10, despues: 5 }], { read: "Las líneas que bajan mejoraron la espera" }),
    C.lollipop([{ nombre: "X", valor: 3 }], { read: "Los puntos a la derecha de la línea superan la referencia" }),
    C.linea([{ nombre: "S", puntos: [{ x: 1, etiqueta: "L", y: 1 }] }], { read: "La banda gris es el rango típico" }),
  ];
  withRead.forEach((html) => {
    assert.match(html, /Cómo leerlo: /);
  });
});

// =====================================================================
// densidad — curva de Gauss (KDE) con la mediana marcada
// =====================================================================

test("densidad: con menos de 3 valores no dibuja curva (sin datos)", () => {
  const html = C.densidad([5, 8]);
  assert.ok(html.includes("Sin datos"));
  const mini = C.densidad([5, 8], { mini: true });
  assert.ok(!mini.includes("<polyline"));
});

test("densidad: imprime la mediana con la etiqueta pedida y respeta opts.fmtX", () => {
  const html = C.densidad([1, 2, 3, 4, 100], { fmtX: (v) => v.toFixed(1) + " u", etiquetaMediana: "mediana" });
  assert.match(html, /mediana 3\.0 u/);
});

test("densidad: una muestra muy desigual (muchos ceros + una cola) no se aplana a una campana pareja", () => {
  const valores = Array(20).fill(0).concat([50, 60, 200]);
  const html = C.densidad(valores);
  // el pico de la curva (mayor densidad) debe caer cerca de x=0, no en el medio del dominio
  const pts = [...html.matchAll(/<polyline points="([^"]+)"/g)][0][1].trim().split(" ").map((p) => p.split(",").map(Number));
  const peak = pts.reduce((best, p) => (p[1] < best[1] ? p : best), pts[0]); // menor y de pixel = mayor densidad
  const domainStart = pts[0][0];
  assert.ok(peak[0] - domainStart < (pts[pts.length - 1][0] - domainStart) * 0.3, "el pico debe estar cerca del extremo izquierdo (los ceros), no centrado");
});

test("densidad: mini dibuja la marca de la mediana (hairline) sin texto", () => {
  const html = C.densidad([1, 2, 3, 4, 5], { mini: true });
  assert.match(html, /stroke-dasharray:3,3/);
  assert.ok(!html.includes("<text"));
});

test("densidad: no genera un guión largo (em-dash) en el HTML de salida", () => {
  const html = C.densidad([1, 2, 3, 4, 5], { read: "Lectura de prueba" });
  assert.ok(!html.includes("—"));
});

// =====================================================================
// piramide — pirámide poblacional mujeres/varones por rango etario
// =====================================================================

test("piramide: imprime los conteos de mujeres y varones de cada bucket", () => {
  const buckets = [{ rango: "0-17", cantidad: 12, f: 5, m: 7 }, { rango: "18-39", cantidad: 20, f: 11, m: 9 }];
  const html = C.piramide(buckets);
  assert.ok(html.includes(">5<"));
  assert.ok(html.includes(">7<"));
  assert.ok(html.includes(">11<"));
  assert.ok(html.includes(">9<"));
  assert.ok(html.includes("0-17"));
  assert.ok(html.includes("18-39"));
});

test("piramide: dibuja la leyenda Mujeres/Varones con dos colores distintos", () => {
  const html = C.piramide([{ rango: "18-39", cantidad: 5, f: 3, m: 2 }]);
  assert.match(html, /sana-legend/);
  assert.ok(html.includes("Mujeres"));
  assert.ok(html.includes("Varones"));
});

test("piramide: cuentas sin sexo cargado (f+m < cantidad) agregan UNA nota callada, no una por bucket", () => {
  const buckets = [{ rango: "0-17", cantidad: 12, f: 5, m: 6 }, { rango: "18-39", cantidad: 20, f: 11, m: 9 }];
  // faltan 1 (bucket 1: 12-11) + 0 (bucket 2: 20-20) = 1 en total
  const html = C.piramide(buckets);
  const matches = html.match(/sin sexo cargado/g) || [];
  assert.equal(matches.length, 1);
  assert.ok(html.includes("1 cuenta sin sexo cargado no se grafica."));
});

test("piramide: sin faltantes no imprime la nota", () => {
  const html = C.piramide([{ rango: "0-17", cantidad: 5, f: 2, m: 3 }]);
  assert.ok(!html.includes("sin sexo cargado"));
});

test("piramide: sin datos devuelve un estado vacío, no revienta", () => {
  const html = C.piramide([]);
  assert.ok(html.includes("Sin datos"));
});

// =====================================================================
// tile / hero — smoke tests de forma
// =====================================================================

test("tile: sin pill no dibuja el bloque de pill", () => {
  const html = C.tile({ etiqueta: "Pendientes ahora", valor: "12" });
  assert.ok(!html.includes("sana-tile-pill"));
});

test("tile: con pill dibuja el texto y el tono pedido", () => {
  const html = C.tile({ etiqueta: "Ocupación", valor: "92%", pill: { texto: "Alta demanda", tono: "critico" } });
  assert.match(html, /sana-tile-pill/);
  assert.ok(html.includes("Alta demanda"));
  assert.match(html, /color:var\(--danger\)/);
});

test("hero: la primera frase lleva la clase de línea líder, las siguientes no", () => {
  const html = C.hero([{ texto: "Primera", tono: "ok" }, { texto: "Segunda", tono: "atencion" }]);
  assert.match(html, /sana-hero-line--lead">.*Primera/);
  const segundaIdx = html.indexOf("Segunda");
  const leadIdx = html.indexOf("sana-hero-line--lead");
  assert.ok(segundaIdx > leadIdx);
  assert.ok(!/sana-hero-line--lead">[^<]*Segunda/.test(html));
});

test("hero: every line after the lead is dark body text, never the grey --text-2", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../static/platform/admin-charts.mjs", import.meta.url), "utf8");
  const rule = src.match(/\.sana-hero-line\{[^}]*\}/);
  assert.ok(rule, "the .sana-hero-line rule exists");
  assert.match(rule[0], /color:var\(--text\);/);
  assert.match(rule[0], /font-size:14px;/);
});

test("hero: sin frases devuelve string vacío (nunca revienta)", () => {
  assert.equal(C.hero([]), "");
  assert.equal(C.hero(), "");
});

test("hero: una frase con enlace queda en un solo span, sin romper la fila flex", () => {
  const html = C.hero([
    { texto: "El equipo resolvió 6.", tono: "ok" },
    { texto: "La asistencia se marca en Pacientes de hoy: hasta entonces no se sabe si fueron.", tono: "info",
      enlace: { href: "/admin/cola", texto: "Pacientes de hoy" } },
  ]);
  const line = html.slice(html.indexOf("La asistencia"));
  assert.match(line, /^La asistencia se marca en <a href="\/admin\/cola">Pacientes de hoy<\/a>: hasta entonces no se sabe si fueron\.<\/span><\/p>/);
  assert.ok(html.indexOf("<span>La asistencia") === html.indexOf("La asistencia") - "<span>".length, "the whole sentence sits in ONE span");
  assert.ok(!/sana-hero-line--lead">\s*<span>La asistencia/.test(html), "the linked line is not the lead");
});

// The phrase with a screen link reads the same aloud with and without the
// link: the screen's name inside the sentence IS the link, nothing is added.
test("frase: el nombre de la pantalla dentro de la frase es el enlace, el resto queda igual", () => {
  const con = { texto: "La asistencia se marca en Pacientes de hoy: hasta entonces no se sabe si fueron.",
    enlace: { href: "/admin/cola", texto: "Pacientes de hoy" } };
  assert.equal(C.frase(con),
    'La asistencia se marca en <a href="/admin/cola">Pacientes de hoy</a>: hasta entonces no se sabe si fueron.');
  assert.equal(C.frase({ texto: "Frase sin enlace." }), "Frase sin enlace.");
  assert.equal(C.frase(), "");
});

test("frase: sólo enlaza a rutas del panel y sólo si el nombre está en la frase", () => {
  const fuera = { texto: "Ver el sitio público.", enlace: { href: "https://example.test/", texto: "sitio público" } };
  assert.equal(C.frase(fuera), "Ver el sitio público.", "an external href is not linked");
  const ausente = { texto: "Se marca en la pantalla de hoy.", enlace: { href: "/admin/cola", texto: "Otro nombre" } };
  assert.equal(C.frase(ausente), "Se marca en la pantalla de hoy.", "the name must be part of the sentence");
  const raro = { texto: "Ruta con & y \"comillas\".", enlace: { href: "/admin/cola", texto: "Ruta" } };
  assert.equal(C.frase(raro), '<a href="/admin/cola">Ruta</a> con &amp; y &quot;comillas&quot;.');
});

test("native import exposes the public chart functions and no SanaCharts global", () => {
  assert.equal(typeof C.scatter, "function");
  assert.equal(typeof C.heatmap, "function");
  assert.equal(typeof C.treemap, "function");
  assert.equal(typeof C.slope, "function");
  assert.equal(typeof C.lollipop, "function");
  assert.equal(typeof C.linea, "function");
  assert.equal(typeof C.densidad, "function");
  assert.equal(typeof C.piramide, "function");
  assert.equal(typeof C.tile, "function");
  assert.equal(typeof C.hero, "function");
  assert.equal(typeof C.frase, "function");
  assert.equal(typeof C.setAncho, "function");
  assert.equal(typeof globalThis.SanaCharts, "undefined");
});

test("empty scatter follows the active catalog, not a frozen Spanish fallback", () => {
  try {
    setCatalog({ "admin.reports.charts.sin_datos": "No data yet." });
    const html = C.scatter([], {});
    assert.ok(html.includes("No data yet."));
    assert.ok(!html.includes("Sin datos"));
  } finally {
    setCatalog({});
  }
});

test("read-line prefix follows the active catalog", () => {
  try {
    setCatalog({ "admin.reports.charts.como_leerlo": "How to read it: " });
    const html = C.scatter([{ x: 1, y: 2, etiqueta: "A" }], { read: "above the line" });
    assert.match(html, /How to read it: /);
    assert.ok(!html.includes("Cómo leerlo"));
  } finally {
    setCatalog({});
  }
});
});

// Conteos: el eje de una línea de enteros no tiene marcas fraccionarias.
test("niceTicks entero: un máximo de 1 da marcas 0 y 1, nunca 0,5", () => {
  const t = I.niceTicks(0, 1, 4, true);
  assert.deepEqual(t.ticks, [0, 1]);
});

test("linea: una serie de conteos no imprime 0,5 en el eje", () => {
  const html = C.linea([{ nombre: "Visitas", puntos: [{ x: 0, etiqueta: "a", y: 0 }, { x: 1, etiqueta: "b", y: 1 }] }]);
  assert.ok(!html.includes("0,5"));
});

test("linea: una serie con decimales conserva marcas fraccionarias", () => {
  const html = C.linea([{ nombre: "Tasa", puntos: [{ x: 0, etiqueta: "a", y: 0 }, { x: 1, etiqueta: "b", y: 0.9 }] }]);
  assert.ok(html.includes("0,5"));
});

test("scatter: la etiqueta que sólo entra cruzando la línea de referencia lleva halo", () => {
  // Gustavo Ibarra queda entre la etiqueta de Emiliano Molina, el punto de
  // Camila Levin y la diagonal: su único lugar cruza la mediana (demo 15.11).
  const rows = [
    { x: 84, y: 310, etiqueta: "Laura Katz" }, { x: 58, y: 225, etiqueta: "Emiliano Molina" },
    { x: 53, y: 195, etiqueta: "Camila Levin" }, { x: 58, y: 189, etiqueta: "Gustavo Ibarra" },
    { x: 84, y: 182, etiqueta: "Juan González" }, { x: 84, y: 156, etiqueta: "Miguel Kowalski" },
    { x: 53, y: 169 }, { x: 20, y: 0 }, { x: 3, y: 6 },
  ];
  const html = C.scatter(rows, { ancho: 1176, ejeX: "Horas", ejeY: "Turnos", linea: { tipo: "diagonal", pendiente: 2.9, etiqueta: "mediana" } });
  const nombres = html.match(/<text[^>]*fill:var\(--text\)[^>]*>(?:<tspan[^>]*>)?[A-Z]/g) || [];
  const conHalo = nombres.filter(t => /paint-order:stroke/.test(t)).length;
  assert.ok(nombres.length >= 5);
  assert.ok(conHalo < nombres.length, "sólo las que cruzan la línea, no todas");
});

// ui-expert 15.11 (medicos-chart2 at 390): «Camila Levin» stood at the height
// of Emiliano Molina's point, hers lower. A text that names a point
// (data-punto) is, by its middle line, no nearer in height to a point beside
// it than to its own; otherwise the point takes a number and the name goes to
// the list. Checked on the demo's doctors at the phone and desk widths.
test("scatter: every name or number is nearer in height to its own point than to a point beside it", () => {
  const rows = [
    { x: 84, y: 310, etiqueta: "Laura Katz" }, { x: 58, y: 224, etiqueta: "Emiliano Molina" },
    { x: 53, y: 195, etiqueta: "Camila Levin" }, { x: 58, y: 190, etiqueta: "Gustavo Ibarra" },
    { x: 84, y: 182, etiqueta: "Juan González" }, { x: 84, y: 155, etiqueta: "Miguel Kowalski" },
    { x: 53, y: 170, etiqueta: "Ana Ruiz" }, { x: 53, y: 140, etiqueta: "Pablo Sosa" }, { x: 53, y: 115 },
    { x: 58, y: 128, etiqueta: "Lucía Paz" }, { x: 35, y: 155 }, { x: 35, y: 100, etiqueta: "Marta Gil" },
  ];
  for (const ancho of [340, 390, 1100, 1440]) {
    const html = C.scatter(rows, { ancho, ejeX: "Horas", ejeY: "Turnos", linea: { tipo: "diagonal", pendiente: 2.9, etiqueta: "mediana turnos/hora" } });
    const pts = [...html.matchAll(/<circle cx="([\d.]+)" cy="([\d.]+)"/g)].map(m => ({ x: +m[1], y: +m[2] }));
    const textos = [...html.matchAll(/<text( x="([\d.]+)" y="([\d.]+)")? text-anchor="(start|end|middle)" data-punto="([\d.]+),([\d.]+)"[^>]*>(.*?)<\/text>/g)];
    assert.ok(textos.length >= 6, "names and numbers carry their point at " + ancho);
    for (const t of textos) {
      const own = { x: +t[5], y: +t[6] };
      const tspans = [...t[7].matchAll(/<tspan x="([\d.]+)" y="([\d.]+)">([^<]*)</g)];
      const x = tspans.length ? +tspans[0][1] : +t[2];
      const ys = tspans.length ? tspans.map(s => +s[2]) : [+t[3]];
      const texto = tspans.length ? tspans.map(s => s[3]).join(" ") : t[7].replace(/<[^>]+>.*$/, "");
      const w = Math.max(...(tspans.length ? tspans.map(s => s[3].length) : [texto.length])) * 7.3;
      const x0 = t[4] === "start" ? x : t[4] === "end" ? x - w : x - w / 2, x1 = x0 + w;
      const cy = ys.reduce((a, b) => a + b, 0) / ys.length - 5;
      const propia = Math.abs(cy - own.y);
      for (const q of pts) {
        if (Math.abs(q.x - own.x) < 0.01 && Math.abs(q.y - own.y) < 0.01) continue;
        const dx = Math.max(x0 - q.x, 0, q.x - x1);
        if (dx > 40) continue;
        assert.ok(Math.abs(cy - q.y) > propia,
          "«" + texto + "» at " + ancho + " is level with another point (" + q.x + "," + q.y + "), not its own (" + own.x + "," + own.y + ")");
      }
    }
  }
});
