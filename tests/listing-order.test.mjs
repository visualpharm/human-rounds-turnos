/* listing-order.test.mjs — calibración de orden y color de listing.js
   (SanaListing.orden, responsable del producto 2026-07-11 12:27) + registro canónico entidad-
   primero (expandCols, 2026-07-11 13:40).

   Correr:  node --test tests/listing-order.test.mjs   (desde la raíz)

   Reglas testeadas:
   - tier "0" EXCLUSIVO de turnos reservables: un walk-in ABIERTO AHORA ordena
     detrás de cualquier turno con fecha (revoca "abierto gana a cualquier
     fecha");
   - dentro del tier walk-in: abierto ahora → apertura por fecha → genéricos
     ("1a" vení en el día / "1b" por orden de llegada) → "2" te confirma;
   - buckets de color por cercanía (sólo reservables): ≤7 días verde ("ya"),
     8-30 ámbar ("semana"), >30 gris ("lejos");
   - expandCols reordena cualquier payload al orden canónico entidad-primero. */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const L = await import("../static/platform/listing.mjs");
const LISTING_SOURCE = readFileSync(new URL("../static/platform/listing.mjs", import.meta.url), "utf8");

// sort de un turno reservable, como lo emite site_availability._next_from
const reservable = (iso, hora) => "0" + iso + hora;

test("reservable SIEMPRE antes que walk-in abierto ahora", () => {
  const abierto = L.orden.abierto("1a");
  // hasta el turno reservable más lejano gana al walk-in abierto en este momento
  assert.ok(reservable("2027-12-31", "23:40") < abierto);
  assert.ok(reservable("2026-07-11", "08:00") < abierto);
});

test("tiers walk-in: abierto → apertura por fecha → genéricos → te confirma", () => {
  const abierto = L.orden.abierto("1a");
  const abreCerca = L.orden.abre("2026-07-12", "08:00");
  const abreLejos = L.orden.abre("2026-09-01", "08:00");
  assert.ok(abierto < abreCerca, "abierto ahora primero entre los walk-in");
  assert.ok(abreCerca < abreLejos, "aperturas ordenadas por fecha");
  assert.ok(abreLejos < "1a", "toda apertura con fecha antes que el genérico 'Vení en el día'");
  assert.ok("1a" < "1b", "vení en el día antes que por orden de llegada");
  assert.ok("1b" < "2", "walk-in antes que 'te confirma el centro'");
});

test("color por lejanía: un solo verde que vira de a poco al azul (responsable del producto 19/10, sin dorado ni rojo)", () => {
  // paso de 3 días, 0..10; desde los 30 días todo es el extremo azulado
  assert.equal(L.orden.bucket(0), "w0");
  assert.equal(L.orden.bucket(-2), "w0");
  assert.equal(L.orden.bucket(1), "w0");
  assert.equal(L.orden.bucket(7), "w2");
  assert.equal(L.orden.bucket(8), "w3");
  assert.equal(L.orden.bucket(15), "w5");
  assert.equal(L.orden.bucket(30), "w10");
  assert.equal(L.orden.bucket(90), "w10");
  assert.equal(L.orden.bucket(null), "w10");
  // sin escalón: dos días vecinos nunca saltan más de un paso
  for (let d = 0; d < 40; d++) {
    const a = +L.orden.bucket(d).slice(1), b = +L.orden.bucket(d + 1).slice(1);
    assert.ok(b - a >= 0 && b - a <= 1, "salto brusco entre " + d + " y " + (d + 1));
  }
});

test("el CSS del listado no tiene dorado/ámbar ni rojo en los turnos", () => {
  assert.ok(!/#8a6d1f|#9a7b1f|#faf3df|lst-time--semana|lst-time--lejos/.test(LISTING_SOURCE));
  assert.ok(LISTING_SOURCE.includes('".lst-time--w" + i'), "los pasos w0..w10 se generan");
});

test("expandCols reordena al orden canónico entidad-primero", () => {
  const cols = L.expandCols(["orden", "proximo", "servicio", "centro"]);
  assert.deepEqual(cols.map((c) => c.label),
    ["Servicio", "Centro", "Orden", "Próxima disponibilidad"]);
  const proximo = cols.find((c) => c.t === "proximo");
  assert.equal(proximo.href, "turno_href");
  assert.equal(proximo.more, "info_href");
  assert.ok(!cols.some((c) => c.t === "go"));
  assert.ok(!cols.some((c) => c.t === "turno"));
});

// ---- colapso de columnas uniformes (responsable del producto, 2026-07-12) ----------------------
const COLS_SERV = L.expandCols(["servicio", "centro", "orden", "proximo"]);
const solicitud = (v) => ({ proximo: { v: v || "Te confirma el centro", kind: "solicitud", sort: "2" } });
const conFecha = () => ({ proximo: { v: "Lunes 14", kind: "turno", dia: "2026-07-14", sort: "02026-07-14" } });

test("colapso: 'Te confirma el centro' uniforme esconde próximo y turno queda (acción)", () => {
  const rows = [solicitud(), solicitud(), solicitud()];
  const u = L.colapso(COLS_SERV, rows);
  const skipped = COLS_SERV.filter((c, i) => u.skip[i]).map((c) => c.t);
  assert.deepEqual(skipped, ["proximo"], "sólo colapsa la columna próximo");
  assert.deepEqual(u.tipos, ["solicitud"]);
});

test("colapso: una fila con fecha real corta el colapso (lista mixta, todo por fila)", () => {
  const u = L.colapso(COLS_SERV, [solicitud(), conFecha()]);
  assert.deepEqual(u.tipos, []);
  assert.ok(!Object.keys(u.skip).length);
});

test("colapso: lista toda walk-in esconde sólo próximo, deduplica en UNA nota", () => {
  const w = { proximo: { v: "Por orden de llegada", kind: "walkin", sort: "1b" } };
  const u = L.colapso(COLS_SERV, [w, w]);
  const skipped = COLS_SERV.filter((c, i) => u.skip[i]).map((c) => c.t);
  assert.deepEqual(skipped, ["proximo"]);
  assert.deepEqual(u.tipos, ["walkin"]);
});

test("nota del colapso: elegir centro con varias filas, oración simple con una", () => {
  assert.match(L.nota("solicitud", 3, true), /^Elegí dónde atenderte/);
  assert.equal(L.nota("solicitud", 1, true), "El centro te confirma la fecha y la hora del turno.");
  assert.equal(L.nota("solicitud", 3, false), "El centro te confirma la fecha y la hora del turno.");
  // P19 (review 2026-11-15): «sin turno, por llegada», never «por orden de
  // llegada» — «orden» already means the doctor's referral on this page.
  assert.equal(L.nota("walkin", 2, true), "Se atienden sin turno, por llegada.");
  assert.doesNotMatch(L.nota("walkin", 2, true), /orden/);
});

test("expandCols: override puntual conserva el preset y objetos crudos van al final", () => {
  const cols = L.expandCols([
    { t: "text", key: "detalle", label: "Turno / motivo" },
    "estado",
    { preset: "centro", hideSm: true },
    "medico"
  ]);
  assert.deepEqual(cols.map((c) => c.label),
    ["Médico", "Centro", "Estado", "Turno / motivo"]);
  assert.equal(cols[1].hideSm, true);
  assert.equal(cols[1].href, "centro_href");
});

// ---- pastillas puras: reservable = <a>, walk-in = <span> (the product owner, 2026-07-12) --
test("timePill: reservable es <a> con el href de la fila y dice el verbo", () => {
  const html = L.timePill("ya", "Mañana", "", "08:00", "/servicio/x/turno");
  assert.match(html, /^<a class="lst-time lst-time--ya" href="\/servicio\/x\/turno"/);
  // ONE label on every width (review 2026-11-15): no desktop/mobile twins
  assert.equal(html, '<a class="lst-time lst-time--ya" href="/servicio/x/turno">Sacar mañana, 08:00</a>');
  assert.doesNotMatch(html, /lst-time-mobile|lst-time-full|aria-label/);
  // el botón dice lo que hace (mockup el responsable del producto 2026-07-14): "Sacar mañana, 08:00"
  assert.match(html, /Sacar /);
  assert.match(html, /mañana/);
  assert.match(html, /08:00/);
});

test("timePill: one date format, «Sacar jue 19 nov, 08:00» (review D9/D20)", () => {
  // the short label (fechaCorta: verb + Dates.corta) is the ONE label
  const html = L.timePill("semana", "Jueves", "19 de noviembre", "08:00", "/servicio/x/turno", "Sacar jue 19 nov, 08:00");
  assert.match(html, />Sacar jue 19 nov, 08:00<\/a>$/);
  assert.doesNotMatch(html, /de noviembre|el jueves/);
  assert.match(L.timePill("ya", "Hoy", "", "14:40", "#"), />Sacar hoy, 14:40<\/a>$/);
});

test("openText: walk-in es <span>, nunca <a>", () => {
  const html = L.openText("Abre mañana, 08:00", "por orden de llegada");
  assert.ok(!html.includes("<a "));
  assert.match(html, /^<span class="lst-open">Abre mañana, 08:00<\/span>/);
  assert.match(html, /por orden de llegada/);
});

test("openText: 'Atiende ahora' para abierto, 'Abre' para futuro", () => {
  assert.match(L.openText("Atiende ahora hasta las 12:00", ""), /Atiende ahora/);
  assert.match(L.openText("Abre el jueves, 08:00", ""), /^<span class="lst-open">Abre el jueves/);
});

test("moreLink: suprimido cuando info_href === href o vacío", () => {
  assert.equal(L.moreLink("", "/servicio/x"), "");
  assert.equal(L.moreLink("/servicio/x", "/servicio/x"), "");
  assert.match(L.moreLink("/servicio/x", "/servicio/x/turno"), /Más horarios/);
});

// ---- «con orden»: la acción es «Subí tu orden», no «Sacar turno» (contraparte técnica 19/10) --
test("orderPill: botón «Subí tu orden» con el color de su lejanía y el turno como nota", () => {
  const html = L.orderPill("w3", "/service/x/book?slot=2026-10-22T09:00", "Jue 22 oct, 09:00");
  assert.match(html, /^<a class="lst-time lst-time--w3" href="\/service\/x\/book\?slot=/);
  assert.match(html, />Subí tu orden<\/a>/);
  assert.ok(!html.includes("Sacar"));
  // the date alone, no «Primer turno:» prefix (review D25)
  assert.match(html, /class="lst-time-note lst-time-note--when">Jue 22 oct, 09:00<\/span>$/);
  assert.ok(!html.includes("Primer turno"));
  // sin fecha (la confirma el centro): botón en tinta, sin nota de turno
  const sinFecha = L.orderPill("pedir", "/service/x/book", "");
  assert.match(sinFecha, /lst-time--pedir/);
  assert.ok(!sinFecha.includes("Primer turno"));
});

test("la celda próximo usa orderPill cuando la fila es «con orden»", () => {
  assert.match(LISTING_SOURCE, /r\.orden[^\n]*\n?[^\n]*orderPill\(/);
  assert.ok((LISTING_SOURCE.match(/orderPill\(/g) || []).length >= 3, "dos ramas + definición");
});

// ---- sin turnos habilitados: mensaje, nunca «Sacar turno» (contraparte técnica 19/10) -------
test("habilitaCell: «No hay turnos disponibles» over «Se habilitan desde el …», no button", () => {
  // Fecha relativa a hoy (+30 días): una fija pasa a ser «Mañana»/«Hoy» con el tiempo.
  const apertura = new Date(Date.now() + 30 * 864e5);
  const diaApertura = apertura.getFullYear() + "-" + String(apertura.getMonth() + 1).padStart(2, "0") +
    "-" + String(apertura.getDate()).padStart(2, "0");
  const html = L.habilitaCell({ estado: "fecha", dia: diaApertura, hora: "08:00" });
  assert.ok(!html.includes("<a "), "no es un enlace ni un botón");
  assert.ok(!html.includes("Sacar"));
  assert.match(html, /No hay turnos disponibles/);
  // the column's one short date, no colon (review 2026-11-15, P21)
  assert.match(html, /Se habilitan desde el [a-zé]{3} \d{1,2} [a-z]{3}, 08:00/);
  assert.doesNotMatch(html, /a partir de:/);
  assert.match(html, new RegExp("\\b" + apertura.getDate() + "\\b"));
  // two facts on two lines (product owner, 2026-11-13): the date is its own line, so
  // no « · » joiner is left to start it
  assert.match(html, /<span class="lst-closed-when">Se habilitan/);
  const sin = L.habilitaCell({ estado: "sin_fechas" });
  assert.match(sin, /No hay turnos disponibles/);
  // sin fecha de apertura: una sola frase, sin repetirla con otras palabras (responsable del producto, 20/10)
  assert.doesNotMatch(sin, /fechas nuevas|lst-closed-when|·/);
  assert.equal((sin.match(/No hay turnos disponibles/g) || []).length, 1);
});

test("la celda próximo muestra habilitaCell antes que cualquier botón de solicitud", () => {
  const i = LISTING_SOURCE.indexOf("p.habilita");
  const j = LISTING_SOURCE.indexOf('p.kind === "solicitud" && href');
  assert.ok(i > 0 && j > 0 && i < j, "p.habilita se evalúa antes de la rama 'Sacar turno'");
});

// ---- campañas / servicios de acceso directo: p.action en la celda de acción --
// La celda vive en el renderer privado de la tabla (no exportado — tableHtml
// necesita un <document> real), así que el contrato es de fuente, y el
// contraste con la variante turnos se prueba con la pastilla exportada.
test("campaña: p.action pinta el CTA del enlace como pastilla 'ya' + nota, sin fechas", () => {
  const m = LISTING_SOURCE.match(
    /if \(p\.action && href\) \{\s*return '<span class="lst-timecell">' \+\s*'<a class="lst-time lst-time--ya" href="' \+ esc\(href\) \+ '">' \+\s*esc\(p\.action\) \+ '<\/a>' \+\s*\(p\.nota \? '<span class="lst-time-note">' \+ esc\(p\.nota\) \+ '<\/span>' : ''\) \+\s*'<\/span>';\s*\}/);
  assert.ok(m, "la rama p.action existe con esa forma exacta");
  // sin fecha, sin bucket, sin "Sacar": el label ES el CTA de la campaña
  const rama = m[0];
  assert.ok(!rama.includes("timePill"));
  assert.ok(!rama.includes("ORDEN.bucket"));
  // reusa la pastilla del turno cercano (≤7 días): mismo verde "ya", no un
  // control nuevo — un control nuevo copia el que ya hace ese trabajo
  assert.match(rama, /lst-time lst-time--ya/);
});

test("campaña y turno reservable son ramas distintas de la MISMA celda: action antes que dia", () => {
  // el slice arranca en el `col.t === "proximo"` que CONTIENE la rama de
  // campaña (el más cercano hacia atrás — el string también aparece antes
  // en el archivo, en el colapso de columnas)
  const iAction = LISTING_SOURCE.indexOf("if (p.action && href)");
  assert.ok(iAction > -1, "la rama p.action existe");
  const celda = LISTING_SOURCE.slice(
    LISTING_SOURCE.lastIndexOf('if (col.t === "proximo")', iAction));
  const iDia = celda.indexOf("if (p.dia)");
  assert.ok(iDia > -1, "la celda también tiene la rama por fecha (variante turnos)");
  assert.ok(celda.indexOf("if (p.action && href)") < iDia,
    "la campaña se resuelve antes que la pastilla por fecha");
  // la variante turnos sigue siendo la pastilla por fecha: anuncia "Sacar …",
  // color por cercanía — la campaña nunca llega a ese camino
  const turno = L.timePill("ya", "Hoy", "", "14:40", "/servicio/x/turno");
  assert.match(turno, /Sacar /);
  assert.match(turno, />Sacar hoy, 14:40<\/a>$/);
  assert.ok(!turno.includes("lst-timecell"), "el turno no usa la celda de campaña");
});

test("controles de listado usan peso regular y búsqueda empata con filtros", () => {
  assert.match(LISTING_SOURCE, /\.lst-time\{[^}]*font-weight:400/);
  assert.match(LISTING_SOURCE, /\.lst-time-h\{font-weight:400/);
  assert.match(LISTING_SOURCE, /\.lst-more\{[^}]*font-weight:400/);
  assert.match(LISTING_SOURCE, /\.lst-orden\{[^}]*font-weight:400/);
  assert.match(LISTING_SOURCE,
    /\.lst-tools--filters \.lst-searchbox,\.lst-tools--filters \.lst-select\{height:44px/);
  assert.match(LISTING_SOURCE,
    /\.lst-tools--filters \.lst-searchbox\{[^}]*border-color:var\(--border-strong\)/);
  // 16px es el piso: menos dispara el zoom automático de iOS Safari (eval 9).
  assert.match(LISTING_SOURCE, /\.lst-tools--filters \.lst-search\{font-size:16px/);
});

test("especialidad va en columna ancha y junto al nombre en pantallas angostas", () => {
  assert.match(LISTING_SOURCE, /"lst-col-" \+ sid\(col\)/);
  assert.match(LISTING_SOURCE,
    /var personSubCol = hasAv[\s\S]*?c\.key === "especialidad"/);
  assert.match(LISTING_SOURCE,
    /var TCOLS = RCOLS/);
  assert.match(LISTING_SOURCE,
    /class="lst-person-specialty"/);
  assert.match(LISTING_SOURCE,
    /class="lst-person-copy"[\s\S]*?personSub/);
  assert.match(LISTING_SOURCE,
    /\.lst-linkcell--av\{vertical-align:middle;align-items:center;\}/);
  assert.match(LISTING_SOURCE,
    /\.lst-person-copy\{[^}]*flex-direction:column[^}]*justify-content:center[^}]*gap:2px/);
  assert.match(LISTING_SOURCE,
    /\.lst-person-specialty\{display:none;/);
  assert.match(LISTING_SOURCE,
    /@media\(min-width:901px\) and \(max-width:1120px\).*?\.lst-table td\{padding:11px 7px;\}.*?\.lst-name\{font-size:17px;\}.*?\.lst-person-copy\{flex-direction:row;.*?\.lst-person-specialty\{display:inline;\}.*?\.lst-col-especialidad\{display:none;\}/s);
  assert.match(LISTING_SOURCE,
    /@media\(max-width:900px\).*?\.lst-person-copy\{flex-direction:row;.*?\.lst-person-specialty\{display:inline;/s);
  assert.match(LISTING_SOURCE,
    /@media\(max-width:360px\)\{\.lst-person-copy\{flex-direction:column;.*?\.lst-person-specialty\{display:block;/s);
  assert.match(LISTING_SOURCE,
    /\.lst-td-proximo\{margin-top:2px;\}/);
  // The old fixed negative offset covered wrapped doctor specialties.
  // Browser geometry also requires the action to follow the identity block.
  assert.doesNotMatch(LISTING_SOURCE, /lst-row-av--pegado|avPegado/);
  assert.match(LISTING_SOURCE,
    /\.lst-table tr\.lst-row-av \.lst-name\{font-size:17px/);
});

// The theme's footer rule carries an id; a margin shorthand there beat the
// listing pages' margin-left:50% while translateX(-50%) still applied, so the
// footer line started off-screen (2026-10-21).
test("the theme footer sets only vertical margins, so listing pages can center it", () => {
  const theme = readFileSync(new URL("../static/root/human-rounds.css", import.meta.url), "utf8");
  const rule = theme.match(/#stzc-page>\.wrap>footer\{[^}]*\}/)[0];
  assert.doesNotMatch(rule, /margin:/);
  assert.match(rule, /margin-top:var\(--space-4\);margin-bottom:0;/);
});

// Review 2026-11-15 (D19/P26): no listing breaks out of the content column
// any more — catalog or single item, the table shares the title's left edge.
test("no listing breaks out of the content column on desktop", () => {
  assert.doesNotMatch(LISTING_SOURCE, /width:min\(1480px/);
  assert.doesNotMatch(LISTING_SOURCE, /\[data-listado\][^{]*\{[^}]*translateX\(-50%\)/);
});

// On-site tester, 2026-10-21: green is only the booking pill. A walk-in opening is
// information in ink, and so are its notes (--text-2 is a 50% tint in the
// humanrounds theme).
test("walk-in text is ink, never the booking green; notes are not light gray", () => {
  assert.match(LISTING_SOURCE, /\.lst-open\{color:var\(--text\);/);
  assert.match(LISTING_SOURCE, /\.lst-open-note\{[^}]*color:var\(--text\);/);
  assert.match(LISTING_SOURCE, /\.lst-when--muted\{color:var\(--text\);\}/);
});
