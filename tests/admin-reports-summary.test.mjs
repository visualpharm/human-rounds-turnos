import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const js = readFileSync(join(ROOT, 'static', 'platform', 'admin-reports.mjs'), 'utf8');
const html = readFileSync(join(ROOT, 'templates', 'admin', 'base.html'), 'utf8');
const directCss = readFileSync(join(ROOT, 'static', 'root', 'admin-direct.css'), 'utf8');
const reportesTpl = readFileSync(join(ROOT, 'templates', 'admin', 'reportes.html'), 'utf8');

test('summary opens with actionable cards and no superficial hero', () => {
  assert.ok(!js.includes('heroResumenHtml'));
  assert.match(js, /mediana_horas_pendiente/);
  assert.match(js, /slots_14d/);
  assert.match(js, /actividad_mensual/);
  assert.match(js, /run_rate_mensual_usd/);
});

// Round 2026-07-22: cargaBandas (el invento "<50%/>=50%") murió — el responsable del producto lo
// vetó explícitamente ("no inventes una división de 50%"). El reemplazo es
// la misma curva de densidad compartida (Charts.densidad) que usa
// Gestión para las esperas pendientes.
test('cargaBandas invented split is gone; médicos card uses the shared density curve', () => {
  assert.ok(!js.includes('cargaBandas'));
  assert.ok(!/rep-stack\b/.test(html));
  assert.ok(!/rep-load-chart/.test(html));
  assert.ok(!/rep-load-labels/.test(html));
  assert.match(js, /cargaPorMedico/);
  assert.match(js, /Charts\.densidad/);
});

test('summary cards are flex columns with the chart pinned to the bottom', () => {
  assert.match(directCss, /\.rep-sumcard\{[^}]*display:flex/);
  assert.match(directCss, /\.rep-card-chart\{[^}]*margin-top:auto/);
});

test('summary charts still name their denominators and avoid fake zero bars', () => {
  assert.match(js, /turnosTxt \+ " sobre " \+ lugaresTxt/);
  assert.match(js, /"\{n\} turno", "\{n\} turnos"/);
  // i18n (2026-08): el aria-label ya no es un literal HTML seguido, pasa por
  // T() — se verifica que el texto en español siga siendo el fallback.
  assert.match(js, /aria-label="' \+ esc\(T\("admin\.reports\.reports\.aria_pacientes_activos_por_mes", null, "Pacientes activos por mes, últimos 12 meses"\)\)/);
  assert.match(js, /var h = r\.pacientes \? Math\.max\(4,/);
  assert.match(directCss, /\.rep-mini-axis\{/);
});

// Centros: el ranking "mejor valorado por estrellas" murió (report-design:
// "el mimo al paciente no es el objetivo") — la tarjeta usa la tarjeta
// editorial de eficiencia que ya arma reportes.py.
test('centros card drops the star ranking for the efficiency-angle tarjeta', () => {
  assert.ok(!js.includes('bestSlug'));
  assert.match(js, /comparison && c\.comparison\.card/);
});

test('formaRowsHtml (5-star ranked bars) and its CSS are gone', () => {
  assert.ok(!js.includes('formaRowsHtml'));
  assert.ok(!/rep-centro-forma/.test(html));
});

test('pacienteBarras trims leading zero months', () => {
  assert.match(js, /while \(i0 < series\.length && !\(series\[i0\]\.pacientes > 0\)\) i0\+\+;/);
});

// Round 2026-07-22 — Médicos: los dos top-10 de una sola dimensión mueren
// (report-design ley 3, "one dimension graphs...are not interesting"),
// reemplazados por un scatter horas×turnos coloreado por centro y un
// treemap de horas por especialidad.
test('chartTopMedicos (two one-dimensional top-10 bar lists) is gone; replaced by scatter + treemap', () => {
  assert.ok(!js.includes('chartTopMedicos'));
  assert.ok(!js.includes('Más horas de agenda por semana'));
  assert.ok(!js.includes('Más turnos en los próximos 14 días'));
  assert.match(js, /seccionAgendaMedicos/);
  assert.match(js, /seccionHorasEspecialidad/);
  assert.match(js, /Charts\.scatter\(puntos, \{/);
  assert.match(js, /agrupar: true/);
});

test('fetchMedicos captures centro/especialidad for the scatter grouping and treemap tiles', () => {
  assert.match(js, /centroNombre: m\.centro \? slugTitulo\(m\.centro\) : ""/);
  assert.match(js, /especialidad: m\.especialidad \|\| ""/);
});

// Pacientes: Edades pasa a pirámide mujeres/varones, Obras sociales a
// treemap de composición — ninguno de los dos vuelve a ser un ranking de
// barras de una sola dimensión.
test('Pacientes: edades use Charts.piramide, obras sociales use Charts.treemap', () => {
  assert.ok(!js.includes('chartEdades'));
  assert.ok(!js.includes('chartObrasSociales'));
  assert.match(js, /Charts\.piramide\(list/);
  assert.match(js, /function seccionObrasSociales/);
});

// Centros: ley 14 (report-design) — ninguna estrella visible en el reporte.
// Los comentarios de código SÍ pueden mencionar el "★" viejo para explicar
// qué se sacó (contexto histórico, nunca se renderiza) — la aserción se hace
// sobre el código con los comentarios de línea despojados, no sobre el
// archivo entero.
test('Centros: no visible star rating anywhere (header or quote suffix)', () => {
  const codeOnly = js.split('\n').map((line) => line.replace(/\/\/.*$/, '')).join('\n');
  assert.ok(!codeOnly.includes('★'));
  assert.ok(!js.includes('rep-quote-rating'));
});

// Centros round 2026-07-22 (revisión del responsable del producto sobre la tarjeta por centro):
// el sparkline "Reseñas por mes" murió, el treemap pasa a color de
// sentimiento continuo, "Personal nombrado" gana su propia sección cálida,
// y la triage deja las dos columnas por texto de una sola columna con
// emoji. Cada punto se verifica por separado para que una regresión futura
// señale exactamente qué parte del rework se rompió.
test('Centros: the "Reseñas por mes" sparkline is gone, "Ver en Google" survives', () => {
  // los comentarios SÍ pueden nombrar "Reseñas por mes" para explicar qué se
  // sacó (contexto histórico, nunca se renderiza) — mismo criterio que el
  // guard de estrellas de más arriba: se despoja el comentario de línea.
  const codeOnly = js.split('\n').map((line) => line.replace(/\/\/.*$/, '')).join('\n');
  assert.ok(!js.includes('tendenciaMiniHtml'));
  assert.ok(!codeOnly.includes('Reseñas por mes'));
  assert.ok(!/rep-centro-tendencia/.test(js));
  assert.match(js, /Ver en Google/);
});

test('Centros: treemap de temas usa sentimentColor (escala continua), no el color de alerta del triage', () => {
  assert.match(js, /function sentimentColor/);
  assert.match(js, /color: sentimentColor\(t\.avg_rating\)/);
  // ya no arma el mapa de "temas que decidió perseguir el triage" para colorear
  assert.ok(!/temasTreemapHtml\(temas, triage\)/.test(js));
  assert.match(js, /function temasTreemapHtml\(temas\)/);
});

test('Centros: sentimentColor apaga los extremos, interpola entre paradas y cae a gris en null', () => {
  assert.match(js, /SENT_LOW/);
  assert.match(js, /SENT_MID/);
  assert.match(js, /SENT_HIGH/);
  assert.match(js, /SENT_NEUTRAL/);
});

test('Centros: personal_nombrado gets its own warm recognition section, the old buried paragraph is gone', () => {
  const codeOnly = js.split('\n').map((line) => line.replace(/\/\/.*$/, '')).join('\n');
  assert.ok(!codeOnly.includes('Las reseñas agradecen por su nombre a'));
  assert.match(js, /Reconocidos por su nombre/);
  assert.match(js, /rep-staff-card/);
  assert.match(js, /p\.quote/);
  assert.match(js, /filter\(function \(p\) \{ return !!p\.quote; \}\)/);
  assert.ok(!js.includes('Aparece en '));
});

test('Centros: triage stops being two columns — no rep-triage-cols, single flowing column with icon subtitles', () => {
  assert.ok(!/rep-triage-cols/.test(js));
  assert.match(js, /function triageIconHtml/);
  assert.match(js, /TRIAGE_ICON/);
  assert.match(js, /rep-triage-topic-h/);
  // los dos títulos de grupo se mantienen
  assert.match(js, /Para aliviar al equipo/);
  assert.match(js, /Se decide no perseguir/);
  assert.match(js, /TRIAGE_GROUP_ICON/);
  assert.match(js, /<h3 class="rep-triage-grupo">' \+ triageIconHtml\(/);
});

// 2026-10-21: one Icons8 sf-regular icon per topic, never a pictograph; every
// mapped name (and the clipboard fallback) ships as a file next to the others.
test('Centros: triage icons are Icons8 files of the project pack, no emoji', () => {
  const block = js.slice(js.indexOf('var TRIAGE_ICON'), js.indexOf('function triageTopicHtml'));
  assert.doesNotMatch(block.replace(/\/\/.*$/gm, ''), /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u);
  const names = [...block.matchAll(/:\s*"([a-z-]+)"/g)].map((m) => m[1]).concat(['clipboard']);
  assert.equal(names.length, 10);
  for (const name of names) {
    assert.ok(existsSync(join(ROOT, 'static', 'icons', `report-${name}-sf-regular.png`)), name);
  }
});

test('Centros: jerarquía semántica H2 centro, H3 grupos y H4 temas', () => {
  assert.match(js, /<h2 class="rep-centro-title">/);
  assert.match(js, /<h3 class="rep-triage-grupo">/);
  assert.match(js, /<h4 class="rep-triage-topic-h">/);
});

test('Centros: card composition order is head, hero line, treemap, staff, triage, quotes', () => {
  const m = js.match(/return '<div class="rep-centro-card">' \+ head \+ heroLine\s*\n\s*\+ temasTreemapHtml\(c\.themes\)\s*\n\s*\+ personalNombradoHtml\(c\.named_staff\)\s*\n\s*\+ triageHtml\(c\.triage\)\s*\n\s*\+ quotesHtml\(c\)/);
  assert.ok(m, 'centroCardHtml debe componer en ese orden exacto');
});

// Gestión: heatmap "carpeted 0,2" — the cells are explained once, inside the
// chart's one reading line, not in a second note under it (critical review
// 08.11, E13). A missing number is not «lower pressure»: on a phone the
// darkest cells drop theirs too (review 15.11), so the line reads the tint.
test('Gestión heatmap: the cells are read by their tint inside the one reading line', () => {
  assert.match(js, /read_heatmap", null, "[^"]*cuanto más oscura, mayor/);
  assert.ok(!/read_heatmap", null, "[^"]*sin número/.test(js));
  assert.ok(!js.includes('nota_celdas_tenues'));
});

// A section is its title and the chart under it: no gray subtitle and no gray
// lead paragraph opening a report (the product owner R11, critical review 08.11, E12).
test('reports draw no gray subtitle under a title and no lead paragraph', () => {
  assert.ok(!js.includes('rep-sub"'));
  assert.ok(!js.includes('rep-lede'));
  assert.match(js, /function seccion\(titulo, bodyHtml\)/);
});

// Búsquedas (2026-07-22): las dos tablas de búsqueda se mudaron FUERA de
// Gestión a su propio sub-reporte con clustering client-side reusando el
// matcher de producción (platform/search.mjs) — nunca una heurística propia.
test('the two search tables left Gestión: tablaBusquedas and data.busquedas are gone from renderGestion', () => {
  assert.ok(!js.includes('tablaBusquedas'));
  assert.ok(!js.includes('data.busquedas'));
});

test('Búsquedas is its own sub-report, after Pacientes in the SUBS nav', () => {
  assert.match(js, /key:\s*"busquedas",\s*label:\s*"Búsquedas",\s*labelKey:\s*"admin\.reports\.reports\.sub_busquedas"/);
  const iPac = js.indexOf('key: "pacientes"');
  const iBusq = js.indexOf('key: "busquedas"');
  const iCent = js.indexOf('key: "centros"');
  assert.ok(iPac < iBusq && iBusq < iCent, 'busquedas debe ir después de pacientes y antes de centros en SUBS');
});

test('clustering reuses Search.buscarConPuntaje (production matcher) against /api/services + /api/doctors', () => {
  assert.match(js, /function clusterizarBusquedas/);
  assert.match(js, /Search\.buscarConPuntaje/);
  assert.match(js, /fetchJson\("\/api\/services", signal\)/);
  assert.match(js, /fetchJson\("\/api\/doctors", signal\)/);
  assert.match(js, /\/api\/admin\/reports\/searches/);
  assert.doesNotMatch(js, /SanaSearch|search\.js/);
});

test('clustering groups into the four intents with no-match keeping the old empty-state copy', () => {
  assert.match(js, /"Especialidades y servicios"/);
  assert.match(js, /"Estudios y análisis"/);
  assert.match(js, /"Personas"/);
  assert.match(js, /"Lo que la gente busca y el catálogo no nombra"/);
  assert.match(js, /Sin datos todavía: el registro de búsquedas arrancó el 2026-07-16\./);
});

test('Resumen gains a Búsquedas card reusing the shared clustering function, not a forked copy', () => {
  assert.match(js, /function sumTileBusquedas/);
  assert.match(js, /sumTileBusquedas\(p\.busquedas, p\.catalogoBusquedas\)/);
  // No debe reimplementar el agrupado: usa clusterizarBusquedas() como el sub-reporte completo.
  const sumTileBody = js.slice(js.indexOf('function sumTileBusquedas'), js.indexOf('function sumTileBusquedas') + 900);
  assert.match(sumTileBody, /clusterizarBusquedas\(/);
});

test('la pantalla de Reportes importa platform/search.mjs y espera las seis dependencias antes de pintar', () => {
  assert.match(js, /importStatic\(["']platform\/search\.mjs["']\)/);
  assert.match(js, /searchReady/);
  // All six dependencies (the table engine for «Descargar» too) settle before the first route/fetch.
  assert.match(js, /var dependencies = Promise\.all\(\[i18nReady, datesReady, chartsReady, searchReady, sessionReady, tableReady\]\);/);
  assert.match(js, /dispose\.ready = dependencies\.then\(function \(\) \{\s*\n\s*if \(!alive\(\)\) return null;\s*\n\s*ready = true;\s*\n\s*return route\(\);/);
  // The template's load deadline ends on .loaded (dependencies in), not on .ready (first data answer).
  assert.match(js, /dispose\.loaded = dependencies\.then\(function \(\) \{ return true; \}, function \(\) \{ return false; \}\);/);
  // The shared loader honours .loaded (tests/screen-boot.test.mjs); the
  // template boots through it.
  assert.match(reportesTpl, /bootScreen\(\{/);
  assert.match(readFileSync(join(ROOT, 'static', 'platform', 'screen-boot.mjs'), 'utf8'),
    /controller\.loaded\.then\(function \(ok\) \{ if \(ok\) settle\(\); \}/);
  assert.doesNotMatch(reportesTpl, /dispose\.ready;\s*\n\s*\}\)\.then\(function \(\) \{ if \(owner === generation\) clearTimeout\(timer\)/);
  assert.doesNotMatch(reportesTpl, /static\(['"]search\.js['"]\)/);
  assert.doesNotMatch(reportesTpl, /SanaSearch/);
});

// Directorio-primero (2026-07-24, decisión #9 del plan "doctor day-queue +
// reports expansion"): el sub-nav de tabs persistente muere, #reportes bare
// pasa a ser SOLO la portada, y cada sub-reporte abre con un breadcrumb de
// una línea en vez de tabs siempre visibles arriba.
test('the persistent #rep-subnav tabs are gone: no subnav markup, no subnav CSS, no marcarSubActiva', () => {
  assert.ok(!js.includes('rep-subnav" id="rep-subnav"'));
  assert.ok(!js.includes('marcarSubActiva'));
  assert.ok(!/\.rep-subnav\{/.test(html));
  // The content host is created once per mount, not for each load.
  // Charts measure that same #rep-body contract.
  // It starts with the loading line, which the first render or the data-failure message replaces.
  assert.match(js, /function ensureShell\(\) \{\s*\n\s*if \(root\.querySelector\("#rep-body"\)\) return;[\s\S]*?root\.innerHTML = '<div id="rep-body"><p class="rep-quiet" role="status">'\s*\n\s*\+ esc\(T\("admin\.reports\.entrevistas\.cargando", null, "Cargando…"\)\) \+ "<\/p><\/div>";\s*\n\s*\}/);
});

test('every sub-report page renders a one-line breadcrumb via crumbHtml(), reusing .cnt-crumb', () => {
  assert.match(js, /function crumbHtml\(key\)/);
  // i18n (2026-08): "Reportes" pasa por T() — el fallback en español sigue
  // siendo el mismo texto, sólo cambia la forma del literal en el source.
  assert.match(js, /'<a href="' \+ reportHref\("resumen"\) \+ '">' \+ esc\(T\("admin\.reports\.reports\.crumb_reportes", null, "Reportes"\)\) \+ '<\/a> \/ '/);
  assert.match(js, /'<p class="cnt-crumb">' \+ inicio \+ esc\(label\) \+ "<\/p>"/);
  // Cada render* de sub-reporte (todos menos Resumen) llama crumbHtml con su
  // propia key — si alguno se pierde en un rework futuro, este test lo marca.
  ['gestion', 'medicos', 'pacientes', 'busquedas', 'centros', 'recupero', 'sumar', 'estadistica', 'capacidad', 'ia', 'login']
    .forEach((key) => {
      assert.ok(js.includes('crumbHtml("' + key + '")'), `crumbHtml("${key}") debe aparecer en static/platform/admin-reports.mjs`);
    });
  // Resumen (la portada) nunca se dibuja a sí misma un breadcrumb hacia #reportes.
  assert.ok(!js.includes('crumbHtml("resumen")'));
});

// Cuatro sub-reportes nuevos (2026-07-24, Phase 2b/3 del mismo plan):
// Recupero de costos, SUMAR+, Estadística mensual, Capacidad y temporada.
test('SUBS gains the four new billing/statistics sub-reports with the right ?view= keys and labels', () => {
  assert.match(js, /key:\s*"recupero",\s*label:\s*"Recupero de costos",\s*labelKey:\s*"admin\.reports\.reports\.sub_recupero"/);
  assert.match(js, /key:\s*"sumar",\s*label:\s*"SUMAR\+",\s*labelKey:\s*"admin\.reports\.reports\.sub_sumar"/);
  assert.match(js, /key:\s*"estadistica",\s*label:\s*"Estadística mensual",\s*labelKey:\s*"admin\.reports\.reports\.sub_estadistica"/);
  assert.match(js, /key:\s*"capacidad",\s*label:\s*"Capacidad y temporada",\s*labelKey:\s*"admin\.reports\.reports\.sub_capacidad"/);
});

test('Resumen gains one summary card per new sub-report, each reusing its own sub-report fetch/cache', () => {
  assert.match(js, /sumTileRecupero\(p\.recupero\)/);
  assert.match(js, /sumTileSumar\(p\.sumar\)/);
  assert.match(js, /sumTileEstadistica\(p\.estadistica\)/);
  assert.match(js, /sumTileCapacidad\(p\.capacidad\)/);
  assert.match(js, /fetchRecupero\(DIAS_RECUPERO, false, signal\)\.catch/);
  assert.match(js, /fetchSumar\(DIAS_SUMAR, false, signal\)\.catch/);
  assert.match(js, /fetchEstadistica\(MES_ESTADISTICA, false, signal\)\.catch/);
  assert.match(js, /fetchCapacidad\(false, signal\)\.catch/);
});

// Recupero/SUMAR+: PII deliberada SOLO en el CSV, nunca en la página.
test('Recupero and SUMAR+ CSV links state the PII carve-out in plain text', () => {
  // i18n (2026-08): las etiquetas de los links pasan por T() — se verifica
  // que el fallback en español siga nombrando el carve-out de PII tal cual.
  assert.match(js, /cost-recovery\.csv\?days=' \+ data\.dias\s*\n\s*\+ '">' \+ esc\(T\("admin\.reports\.reports\.descargar_csv_facturacion", null, "Descargar CSV para facturación \(con datos personales\)"\)\) \+ '<\/a>/);
  assert.match(js, /sumar\.csv\?days=' \+ data\.dias\s*\n\s*\+ '">' \+ esc\(T\("admin\.reports\.reports\.descargar_csv_sumar", null, "Descargar CSV para SUMAR\+ \(con datos personales\)"\)\) \+ '<\/a>/);
  assert.match(js, /statistics\.csv\?mes=' \+ esc\(data\.mes\)\s*\n\s*\+ '">' \+ esc\(T\("admin\.reports\.reports\.descargar_csv_estadistica", null, "Descargar CSV para estadística \(sin datos personales\)"\)\) \+ '<\/a>/);
});

// Estadística: selector de MES real (<select>, nunca pills — ui-rules), no
// un selector de días como los demás sub-reportes con período.
test('Estadística uses a real month <select>, never pills, and syncs to the backend default', () => {
  assert.match(js, /function mesSelectHtml\(id, mesActual\)/);
  assert.match(js, /function mesesOpciones\(\)/);
  assert.ok(!js.includes('mes-pill'));
  assert.match(js, /MES_ESTADISTICA = data\.mes; \/\/ sincroniza/);
});

// Capacidad: sin selector de período (el payload siempre mira "ahora").
test('Capacidad has no period selector — the payload has no ?days= param', () => {
  const start = js.indexOf('function fetchCapacidad');
  const end = js.indexOf('function renderCapacidad');
  const seccionFetch = js.slice(start, end);
  assert.ok(!/\?days=/.test(seccionFetch));
});

// Ley 3 (report-design): ni lollipop ni treemap son barras/columnas
// vetadas — los cuatro sub-reportes nuevos deben usar las formas permitidas,
// nunca inventar un gráfico de barras verticales/columnas propio.
test('the four new sub-reports use only vetted chart forms: lollipop, treemap, linea, piramide, tile — no new column/bar chart', () => {
  const nuevo = js.slice(js.indexOf('sub-reporte: Recupero de costos'), js.indexOf('sub-reporte: Costos de IA'));
  // treemaps go through treemapDeVarios (one part is no composition, 2026-11-08 E9)
  assert.match(nuevo, /treemapDeVarios\(/);
  assert.match(js, /function treemapDeVarios\(items, opts\) \{[\s\S]*?Charts\.treemap\(/);
  assert.match(nuevo, /Charts\.lollipop/);
  assert.match(nuevo, /Charts\.linea/);
  assert.match(nuevo, /Charts\.piramide/);
  assert.match(nuevo, /Charts\.tile/);
  assert.ok(!/chartBarrasDiarias\(/.test(nuevo));
});


test('report tables align counts, rates, and costs with the shared numeric contract', () => {
  const sections = [
    js.slice(js.indexOf('function filaClusterBusquedaHtml'), js.indexOf('function renderBusquedas')),
    js.slice(js.indexOf('function seccionPorEspecialidadTabla'), js.indexOf('function medianaDe')),
    js.slice(js.indexOf('function tablaTopIps'), js.indexOf('function renderIa')),
  ];
  for (const section of sections) {
    assert.match(section, /cnt-numeric/);
  }
});

// Analytics review 13.11: two different «approved» numbers sit on the Gestión page; each says what it counts.
test('Gestión names its two approved counts apart', () => {
  assert.match(js, /"Aprobadas ahora, de las resueltas"/);
  assert.match(js, /admin\.reports\.reports\.funnel_dos_aprobadas/);
  assert.match(js, /aprobadas y canceladas después/);
});
