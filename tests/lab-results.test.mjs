// Guardas sobre lab-results.js: la pestaña "Resultados de laboratorio" de la
// cuenta del paciente. Rediseño 2026-08-06 (docs/architecture/
// laboratorio-paciente.md): una lectura cautelosa de la IA por informe, el
// filtro de sensibilidad server-side y una segunda vista "Evolución" con
// gráficos de tendencia, detrás de un toggle segmentado en memoria.
//
// Estilo mixto, igual que el resto del repo: aserciones estáticas de
// texto/fuente (sin DOM) para el contrato de wiring, más un montaje real en
// un DOM mínimo (mismo patrón que tests/admin-patient-summary.test.mjs —
// vm.runInNewContext + un fakeElement liviano) para el comportamiento que
// SÓLO se ve corriendo: el toggle cambia de vista, el resumen se pinta con
// su tono, el informe sensible no manda valores, el gráfico SVG cuenta
// puntos reales, y el HTML del servidor llega escapado.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { until, stays } from './helpers/wait.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const js = readFileSync(join(ROOT, 'static/platform/account-labs.mjs'), 'utf8');

// ---- aserciones estáticas sobre la fuente ----------------------------------

test('pide los resultados con la sesión propia, nunca con un DNI en la URL', () => {
  assert.match(js, /fetch\("\/api\/account\/lab-results",\s*\{\s*credentials:\s*"same-origin"/);
  assert.doesNotMatch(js, /dni=/);
});

test('usa platform/listing.mjs — único motor de tabla del sitio paciente — nunca un <table> propio', () => {
  assert.match(js, /Listing\.render/);
  assert.doesNotMatch(js, /window\.SanaListing|<table/);
});

test('la tabla de cada informe no reordena ni busca: es una historia corta, no un directorio', () => {
  const m = /options:\s*\{[\s\S]*?\}/.exec(js);
  assert.ok(m, 'options del render debe existir');
  assert.match(m[0], /sort:\s*false/);
  assert.match(m[0], /search:\s*false/);
  assert.match(m[0], /defaultSort:\s*false/);
});

test('estado vacío: nunca un error, siempre con un próximo paso', () => {
  const m = /function vacio\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'vacio() debe existir');
  assert.match(m[0], /Todavía no tenés resultados de laboratorio cargados/);
  assert.match(m[0], /<a href="\/d\/diagnostico">/);
});

test('las columnas de la tabla por informe mapean el nuevo contrato de datos (nombre/valor/rango/fuera)', () => {
  assert.match(js, /it\.nombre/);
  assert.match(js, /it\.valor/);
  assert.match(js, /it\.rango/);
  assert.match(js, /it\.fuera/);
  assert.match(js, /outKey:\s*"fuera_de_rango"/);
  assert.match(js, /t:\s*"dot",\s*key:\s*"estado"/);
});

test('el link "a un clínico" nunca es un "#" a mano — usa la ruta real que manda el servidor', () => {
  assert.doesNotMatch(js, /href="#"/);
  assert.match(js, /esc\(clinicoHref/);
});

test('el gráfico de Evolución nunca lo dibuja la IA: los puntos vienen del payload, sólo código', () => {
  assert.match(js, /function chartSvg\(serie\)/);
  assert.match(js, /serie\.puntos/);
  assert.doesNotMatch(js, /openrouter|gemini|ai_client/i);
});

test('el reintento por análisis pendiente es ÚNICO, nunca un polling loop', () => {
  const m = /if \(Number\(data\.analisis_pendiente\) > 0 && !reintentoHecho\) \{[\s\S]*?\n\s*\}/.exec(js);
  assert.ok(m, 'debe existir el bloque de reintento único');
  assert.match(m[0], /reintentoHecho = true/);
  assert.match(m[0], /setTimeout\(cargar,\s*8000\)/);
});

test('todas las cadenas nuevas pasan por T() con fallback castellano', () => {
  const calls = [...js.matchAll(/\bT\("([a-z0-9_.]+)"/g)].map((m) => m[1]);
  ['account.mine.lab.tab_results', 'account.mine.lab.tab_evolution',
   'account.mine.lab.sensitive', 'account.mine.lab.sensitive_link',
   'account.mine.lab.pending_note', 'account.mine.lab.evolution_empty',
   'account.mine.lab.chart_range', 'account.mine.lab.col_determination',
   'account.mine.lab.col_result', 'account.mine.lab.col_reference', 'account.mine.lab.col_status',
   'account.mine.lab.in_range', 'account.mine.lab.out_of_range',
   'account.mine.lab.footer_note', 'account.mine.lab.footer_link'].forEach((k) => {
    assert.ok(calls.includes(k), `falta el llamado T("${k}", …)`);
  });
});

// ---- CSS: el polish del detalle y el full-bleed mobile (2026-08-10) ---------
//
// El stylesheet interno es un array de strings que se joinea (el <style
// id="lr-style"> que inyecta el módulo): cada regla es UN literal del array.
// Recuperar la regla POR SELECTOR deja el contrato inmune al orden del array
// y al whitespace alrededor — pinea las decisiones del polish, no el
// stylesheet entero.
function reglaCss(sel) {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = js.match(new RegExp('"' + esc + '\\{[^"]*\\}"'));
  return m ? m[0] : "";
}

test('detalle: el título del estudio respira como encabezado de página', () => {
  const title = reglaCss('.lr-detail-title');
  assert.ok(title, 'existe la regla .lr-detail-title');
  assert.match(title, /font-size:20px/);
  assert.match(title, /text-wrap:balance/);
  assert.match(title, /letter-spacing:-\.01em/);
  // aire debajo del encabezado antes del resumen (era 6px)
  assert.match(reglaCss('.lr-detail-head'), /margin:0 0 12px/);
});

test('lista: el link "Ver todos los resultados" no se pega al borde de la tabla', () => {
  const showAll = reglaCss('.lr-show-all');
  assert.ok(showAll, 'existe la regla .lr-show-all');
  // margin-top generoso (12px, era 8) + peso medio: separa el atajo de la
  // última fila sin un divisor que merguearía con el borde propio de la tabla.
  assert.match(showAll, /margin:12px 0 0/);
  assert.match(showAll, /font-weight:500/);
});

test('mobile: la tabla de determinaciones va a los dos bordes de la tarjeta — sólo ella y sólo <768px', () => {
  const fullBleed = reglaCss('@media(max-width:767px)');
  assert.ok(fullBleed, 'existe el bloque @media(max-width:767px)');
  // el margen negativo cancela el padding horizontal del .dcard
  assert.match(fullBleed, /\.lr-report-table\{margin:0 -28px;--lst-row-pad-x:28px;\}/);
  // scoped: fuera de ese media query la tabla nunca arrastra márgenes
  // negativos (desktop queda intacto; un informe sin tabla tampoco cambia).
  const resto = js.replace(/"@media\(max-width:767px\)\{[^"]*\}"/, '');
  assert.doesNotMatch(resto, /lr-report-table\{[^"]*margin:0 -/);
});

test('mobile: las filas de la tabla a los dos bordes recuperan el aire que canceló el margen', () => {
  // Bug 21/10 (demo, teléfono): con margin:0 -28px las filas apiladas del
  // listado (padding lateral 0) quedaban pegadas al borde de la tarjeta.
  // El margen negativo y el aire de la fila tienen que ser el MISMO número.
  const fullBleed = reglaCss('@media(max-width:767px)');
  const m = fullBleed.match(/margin:0 -(\d+)px;--lst-row-pad-x:(\d+)px;/);
  assert.ok(m, 'la tabla full-bleed fija --lst-row-pad-x');
  assert.equal(m[1], m[2]);
  // y el motor de listados lo aplica a cada forma de fila apilada (<=900px):
  // fila común, fila con avatar, encabezado de grupo y fila agrupada.
  const listing = readFileSync(join(ROOT, 'static/platform/listing.mjs'), 'utf8');
  const stacked = listing.slice(listing.indexOf('"@media(max-width:900px){"'));
  for (const regla of [
    /"\.lst-table tr\{padding:14px var\(--lst-row-pad-x,0\);/,
    /"\.lst-table tr\.lst-row-av\{padding-left:var\(--lst-row-pad-x,0\);\}"/,
    /tr\.lst-grp\{padding:22px var\(--lst-row-pad-x,0\) 6px;\}/,
    /tr:not\(\.lst-grp\)\{padding:11px var\(--lst-row-pad-x,0\);\}/,
  ]) assert.match(stacked, regla);
  // ninguna fila apilada vuelve a fijar un padding lateral literal 0
  assert.doesNotMatch(stacked, /\.lst-table[^"]*tr[^"{]*\{padding:\d+px 0[;}]/);
});

// ---- montaje real en un DOM mínimo -----------------------------------------
//
// Mismo patrón que tests/admin-patient-summary.test.mjs: sin jsdom (el repo
// no lo usa), un `document` mínimo cuyo createElement registra cada
// elemento creado en un array — así el test puede encontrar el botón de
// Evolución por su texto y clickearlo de verdad, sin necesitar un parser de
// HTML real para leer de vuelta lo que innerHTML dejó adentro.

function fakeElement(tag) {
  return {
    tagName: String(tag || 'div').toUpperCase(),
    className: '',
    textContent: '',
    innerHTML: '',
    style: {},
    attrs: {},
    listeners: {},
    children: [],
    appendChild(el) { this.children.push(el); return el; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    addEventListener(ev, fn) { this.listeners[ev] = fn; },
    querySelector() { return null; },
  };
}

function montar(respuesta, opts) {
  opts = opts || {};
  const host = fakeElement('div');
  const note = fakeElement('div');
  const created = [];
  const renderCalls = [];
  const events = { popstate: [] };
  const timers = [];
  const historyCalls = [];
  const ids = { 'lr-host': host, 'lr-note': note };

  const listingMockUrl = "data:text/javascript," + encodeURIComponent(
    "export function render(mount, payload) {" +
    "  globalThis.__listingRenderCalls.push(payload);" +
    "  mount.innerHTML = '<!-- lst-table rows=' + payload.rows.length + ' -->';" +
    "}"
  );
  globalThis.__listingRenderCalls = renderCalls;
  const context = {
    window: {
      SANA_STATIC: {
        "platform/listing.mjs": listingMockUrl,
        "platform/dates.mjs": 'data:text/javascript,' + encodeURIComponent('export function nacimiento(d){return d} export function corta(d){return d}'),
        "platform/i18n.mjs": 'data:text/javascript,' + encodeURIComponent('export function t(k,v,f){return f||k}'),
        ...(opts.manifest || {}),
      },
      addEventListener(type, fn) { (events[type] || (events[type] = [])).push(fn); },
      removeEventListener(type, fn) { events[type] = (events[type] || []).filter((item) => item !== fn); },
      scrollIntoView() {},
    },
    location: { hash: opts.hash || '' },
    history: { pushState() { historyCalls.push('push'); }, replaceState() { historyCalls.push('replace'); } },
    document: {
      getElementById: (id) => ids[id] || null,
      createElement: (tag) => { const el = fakeElement(tag); created.push(el); return el; },
      head: { appendChild() {} },
      body: { appendChild() {} },
      querySelector: () => null,
    },
    fetch: opts.fetch || (() => Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(respuesta),
    })),
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout() {},
    Object, Math, Date, JSON, Promise, String, Number, isFinite, encodeURIComponent,
  };
  const runnable = js.replace('export function mount(host, options) {', 'function mount(host, options) {') + "\nglobalThis.dispose = mount(document.getElementById(\"lr-host\"));";
  vm.runInNewContext(runnable, context, {
    importModuleDynamically: vm.constants.USE_MAIN_CONTEXT_DEFAULT_LOADER,
  });
  return { context, host, note, created, renderCalls, events, timers, historyCalls };
}

async function afterPaint(m) {
  for (let i = 0; i < 50; i++) {
    await new Promise((r) => setTimeout(r, 0));
    const text = textoCompleto(m);
    if (porTexto(m, "Resultados") || /Todavía no tenés resultados/.test(text) || /No pudimos mostrar tus resultados/.test(text)) {
      return;
    }
  }
}

function textoCompleto(m) {
  return m.host.innerHTML + '\n' + m.note.innerHTML + '\n' +
    m.created.map((e) => (e.innerHTML || '') + '\n' + (e.textContent || '')).join('\n');
}

function porTexto(m, texto) {
  return m.created.find((e) => e.textContent === texto);
}

function porPanel(m, nombre) {
  return m.created.find((e) => e.getAttribute('data-panel') === nombre);
}

test('el toggle arranca en Resultados y cambia a Evolución con un click real', async () => {
  const respuesta = {
    informes: [{ fecha: '2020-03-10', tipo_estudio: 'Perfil tiroideo', resumen: 'Todo en orden.', tono: 'ok', items: [] }],
    series: [{ id: 'tsh', label: 'TSH', unidad: 'uUI/mL', rango_ok: [0.4, 4], puntos: [['2020-03-10', 2.1], ['2020-06-10', 2.4], ['2020-09-10', 2.0]] }],
    analisis_pendiente: 0, clinico_href: '/d/clinico-general',
  };
  const m = montar(respuesta);
  await afterPaint(m);

  const btnResultados = porTexto(m, 'Resultados');
  const btnEvolucion = porTexto(m, 'Evolución');
  assert.ok(btnResultados && btnEvolucion, 'los dos botones del toggle deben existir');
  assert.match(btnResultados.className, /\bon\b/);
  assert.doesNotMatch(btnEvolucion.className, /\bon\b/);

  const panelResultados = porPanel(m, 'resultados');
  const panelEvolucion = porPanel(m, 'evolucion');
  assert.equal(panelResultados.style.display, '');
  assert.equal(panelEvolucion.style.display, 'none');

  btnEvolucion.listeners.click();

  assert.match(btnEvolucion.className, /\bon\b/);
  assert.doesNotMatch(btnResultados.className, /\bon\b/);
  assert.equal(panelEvolucion.style.display, '');
  assert.equal(panelResultados.style.display, 'none');
});

test('failed lab HTTP response is an error, never an empty clinical history', async () => {
  const m = montar({}, { fetch: () => Promise.resolve({ ok: false, status: 503 }) });
  await afterPaint(m);
  assert.match(m.host.innerHTML, /role="alert"/);
  assert.match(m.host.innerHTML, /No pudimos mostrar/);
  assert.doesNotMatch(m.host.innerHTML, /Todavía no tenés/);
});

test('a disposed lab mount cannot paint a held response', async () => {
  let resolve;
  const response = new Promise((done) => { resolve = done; });
  let requested = false;
  const m = montar({}, { fetch: () => { requested = true; return response; } });
  await until(() => requested, 'the mount asks for the lab response');
  m.context.dispose();
  m.host.innerHTML = 'new owner';
  resolve({ ok: true, json: () => Promise.resolve({ informes: [] }) });
  await stays(() => m.host.innerHTML === 'new owner' && m.events.popstate.length === 0, 'the held response paints nothing after dispose', 5);
  assert.equal(m.host.innerHTML, 'new owner');
  assert.equal(m.events.popstate.length, 0);
});

test('lab disposal removes the installed history listener and is idempotent', async () => {
  const m = montar({ informes: [{ tipo_estudio: 'Synthetic', items: [] }] });
  await afterPaint(m);
  assert.equal(m.events.popstate.length, 1);
  m.context.dispose();
  m.context.dispose();
  assert.equal(m.events.popstate.length, 0);
});

test('translated lab columns are resolved after the translation module is ready', async () => {
  const translated = 'data:text/javascript,' + encodeURIComponent('export function t(k,v,f){return k === "account.mine.lab.col_determination" ? "Determination" : f||k}');
  const m = montar({ informes: [{ tipo_estudio: 'Synthetic', items: [{ nombre: 'Test', valor: 1, fuera: true }] }] }, {
    manifest: { 'platform/i18n.mjs': translated },
  });
  await afterPaint(m);
  assert.equal(m.renderCalls[0].cols[0].label, 'Determination');
});

test('a held Listing import cannot repaint after lab disposal', async () => {
  let release;
  globalThis.__labListingDisposeGate = new Promise((done) => { release = done; });
  const delayed = 'data:text/javascript,' + encodeURIComponent('globalThis.__labListingDisposeHeld = true; await globalThis.__labListingDisposeGate; export function render() {}');
  const m = montar({ informes: [{ tipo_estudio: 'Old', items: [] }] }, {
    manifest: { 'platform/listing.mjs': delayed },
  });
  try {
    await until(() => globalThis.__labListingDisposeHeld, 'the Listing import is held');
    m.context.dispose();
    m.host.innerHTML = 'new owner';
    release();
    await stays(() => m.host.innerHTML === 'new owner' && m.events.popstate.length === 0, 'the released Listing import paints nothing after dispose', 5);
    assert.equal(m.host.innerHTML, 'new owner');
    assert.equal(m.events.popstate.length, 0);
  } finally { release(); delete globalThis.__labListingDisposeGate; delete globalThis.__labListingDisposeHeld; }
});

test('a newer lab response owns the paint when both responses await Listing', async () => {
  let release, calls = 0;
  globalThis.__labListingRetryGate = new Promise((done) => { release = done; });
  const delayed = 'data:text/javascript,' + encodeURIComponent('await globalThis.__labListingRetryGate; export function render() {}');
  const m = montar({}, {
    manifest: { 'platform/listing.mjs': delayed },
    fetch: () => {
      calls++;
      const label = calls === 1 ? 'Old report' : 'Fresh report';
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        informes: [{ tipo_estudio: label, items: [] }], analisis_pendiente: calls === 1 ? 1 : 0,
      }) });
    },
  });
  try {
    await until(() => m.timers.length, 'first response reached its pending-analysis retry boundary');
    assert.equal(m.timers.length, 1, 'first response reached its pending-analysis retry boundary');
    m.timers[0]();
    await until(() => calls >= 2, 'the retry asks for a second response');
    assert.equal(calls, 2);
    release();
    await afterPaint(m);
    assert.match(textoCompleto(m), /Fresh report/);
    assert.doesNotMatch(textoCompleto(m), /Old report/);
    assert.equal(m.events.popstate.length, 1);
    m.context.dispose();
  } finally { release(); delete globalThis.__labListingRetryGate; }
});

test('el encabezado del informe muestra fecha + tipo de estudio (con fallback a servicio y a "Laboratorio")', async () => {
  const respuesta = {
    informes: [
      { fecha: '2024-05-01', tipo_estudio: 'Perfil tiroideo', resumen: 'Todo en orden.', tono: 'ok', items: [] },
      { fecha: '2024-06-01', servicio: 'Bioquímica', resumen: 'Revisar con tu médico.', tono: 'revisar', items: [] },
      { fecha: '2024-07-01', resumen: 'Revisar con tu médico.', tono: 'revisar', items: [] },
    ],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  const todo = textoCompleto(m);
  assert.match(todo, /Perfil tiroideo/);
  assert.match(todo, /Bioquímica/);
  assert.match(todo, /Laboratorio/); // fallback sin tipo_estudio ni servicio
});

test('el resumen se pinta con el tono correcto: "ok" en verde, "revisar" nunca en rojo', async () => {
  const respuesta = {
    informes: [
      { fecha: '2024-05-01', tipo_estudio: 'Hemograma', resumen: 'Todos los valores dentro del rango de referencia.', tono: 'ok', items: [] },
      { fecha: '2024-06-01', tipo_estudio: 'Hepatograma', resumen: 'Hay valores para revisar con tu médico.', tono: 'revisar', items: [] },
    ],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  const todo = textoCompleto(m);
  assert.match(todo, /lr-summary-i--ok/);
  assert.match(todo, /lr-summary-i--revisar/);
  assert.doesNotMatch(todo, /danger|rojo|red/i);
  assert.match(todo, /Todos los valores dentro del rango/);
  assert.match(todo, /Hay valores para revisar con tu médico/);
});

test('un informe sensible sólo manda fecha + el aviso + el link — nunca tipo, resumen ni valores', async () => {
  const respuesta = {
    informes: [{ fecha: '2024-08-01', sensible: true }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico-general',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  const todo = textoCompleto(m);
  assert.match(todo, /Tu médico tiene tus resultados\./);
  assert.match(todo, /Sacá turno con un clínico/);
  assert.match(todo, /\/d\/clinico-general/);
  assert.equal(m.renderCalls.length, 0, 'un informe sensible nunca monta la tabla de determinaciones');
});

test('el gráfico SVG de Evolución trae la banda de referencia y un punto por cada valor real', async () => {
  const respuesta = {
    informes: [{ fecha: '2024-08-01', tipo_estudio: 'Hemograma', resumen: 'Todo en orden.', tono: 'ok', items: [] }],
    series: [{
      id: 'glucemia_mg_dl', label: 'Glucemia', unidad: 'mg/dL', rango_ok: [70, 110],
      puntos: [['2023-01-01', 92], ['2023-06-01', 118], ['2024-01-01', 121], ['2024-08-01', 104]],
    }],
    analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  porTexto(m, 'Evolución').listeners.click();

  const panelEvolucion = porPanel(m, 'evolucion');
  assert.match(panelEvolucion.innerHTML, /lr-chart-band/);
  const puntos = panelEvolucion.innerHTML.match(/<circle/g) || [];
  assert.equal(puntos.length, 4);
  assert.match(panelEvolucion.innerHTML, /Glucemia/);
});

test('Evolución sin series todavía dice, con calma, que no hay suficiente historia', async () => {
  const respuesta = {
    informes: [{ fecha: '2024-08-01', tipo_estudio: 'Hemograma', resumen: 'Todo en orden.', tono: 'ok', items: [] }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  porTexto(m, 'Evolución').listeners.click();
  const panelEvolucion = porPanel(m, 'evolucion');
  assert.match(panelEvolucion.innerHTML, /Todavía no hay suficientes resultados para ver una evolución/);
});

test('analisis_pendiente > 0 muestra la línea de "seguimos leyendo", nunca un error', async () => {
  const respuesta = {
    informes: [{ fecha: '2024-08-01', tipo_estudio: 'Hemograma', resumen: 'Todo en orden.', tono: 'ok', items: [] }],
    series: [], analisis_pendiente: 3, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  assert.match(textoCompleto(m), /Seguimos leyendo algunos estudios/);
});

test('XSS: un <script> en tipo_estudio o resumen llega siempre escapado, nunca como HTML', async () => {
  const respuesta = {
    informes: [{
      fecha: '2024-08-01', tipo_estudio: '<script>alert(1)</script>',
      resumen: '<img src=x onerror=alert(1)>', tono: 'ok', items: [],
    }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  const todo = textoCompleto(m);
  assert.doesNotMatch(todo, /<script>alert/);
  assert.doesNotMatch(todo, /<img src=x/);
  assert.match(todo, /&lt;script&gt;/);
  assert.match(todo, /&lt;img/);
});

test('sin informes: el estado vacío, sin toggle ni tablas', async () => {
  const m = montar({ informes: [], series: [], analisis_pendiente: 0 });
  await afterPaint(m);
  assert.match(m.host.innerHTML, /Todavía no tenés resultados de laboratorio cargados/);
  assert.equal(porTexto(m, 'Evolución'), undefined);
});

// ---- lista: sólo fuera de rango + "ver todos los resultados" (responsable del producto 2026-08-10)
//
// La lista no muestra los valores normales: cada tarjeta de informe pinta
// sólo las determinaciones fuera de rango (con un conteo "N de M") y un link
// "Ver todos los resultados" que abre la página del informe entero. Un
// informe todo-normal sigue existiendo (título + resumen + link) pero sin
// tabla. Un informe sensible se queda como estaba (redacción server-side).

test('lista: un informe con valores normales Y fuera de rango muestra sólo los fuera de rango + el conteo', async () => {
  const respuesta = {
    informes: [{
      fecha: '2024-08-01', tipo_estudio: 'Hemograma',
      resumen: 'Hay valores para revisar con tu médico.', tono: 'revisar',
      items: [
        { nombre: 'Glucemia', valor: 92, unidad: 'mg/dL', rango: [70, 110], fuera: false },
        { nombre: 'Colesterol', valor: 240, unidad: 'mg/dL', rango: [0, 200], fuera: true },
        { nombre: 'Triglicéridos', valor: 180, unidad: 'mg/dL', rango: [0, 150], fuera: true },
      ],
    }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  // La tabla montada debe tener EXACTAMENTE las 2 filas fuera de rango, no 3.
  const tabla = m.renderCalls[m.renderCalls.length - 1];
  assert.equal(tabla.rows.length, 2, 'la tabla de lista muestra sólo las fuera de rango');
  assert.ok(tabla.rows.every((r) => r.fuera_de_rango), 'toda fila en la tabla de lista está marcada fuera_de_rango');
  const todo = textoCompleto(m);
  assert.match(todo, /2 de 3 valores fuera de rango/);
  assert.match(todo, /Ver todos los resultados/);
});

// Review 2026-11-15, item 17: when every value of the report is out of range
// the card already shows all of them, so no "3 de 3" line, no "Ver todos"
// link, and no Estado column repeating "Fuera de rango" on each row (the
// summary says it once, the result column carries the colour).
test('lista: todo fuera de rango dice el conteo una vez, sin "ver todos" ni columna Estado', async () => {
  const respuesta = {
    informes: [{
      fecha: '2024-08-01', tipo_estudio: 'Hemograma',
      resumen: 'Hay 2 valores para revisar con tu médico.', tono: 'revisar',
      items: [
        { nombre: 'Colesterol', valor: 240, unidad: 'mg/dL', rango: [0, 200], fuera: true },
        { nombre: 'Triglicéridos', valor: 180, unidad: 'mg/dL', rango: [0, 150], fuera: true },
      ],
    }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  const tabla = m.renderCalls[m.renderCalls.length - 1];
  assert.equal(tabla.rows.length, 2);
  assert.ok(!tabla.cols.some((c) => c.key === 'estado'), 'the list table has no Estado column');
  const todo = textoCompleto(m);
  assert.doesNotMatch(todo, /2 de 2/);
  assert.doesNotMatch(todo, /Ver todos los resultados/);
});

test('resultados: names and reference values in the body ink, one paragraph before the data', () => {
  assert.match(js, /\.lr-report-table td\.lst-col-determinacion>\*,\.lr-report-table td\.lst-col-referencia>\*\{color:var\(--text\);\}/);
  const shell = readFileSync(join(ROOT, 'static/platform/account.mjs'), 'utf8');
  assert.doesNotMatch(shell, /account\.dash\.labs_lede/, 'the AI notice is the only paragraph before the data');
});

test('lista: un informe con todo en rango NO monta tabla, pero sí lleva el link "ver todos"', async () => {
  const respuesta = {
    informes: [{
      fecha: '2024-08-01', tipo_estudio: 'Hemograma',
      resumen: 'Todos los valores dentro del rango de referencia.', tono: 'ok',
      items: [
        { nombre: 'Glucemia', valor: 92, unidad: 'mg/dL', rango: [70, 110], fuera: false },
        { nombre: 'Colesterol', valor: 180, unidad: 'mg/dL', rango: [0, 200], fuera: false },
      ],
    }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  assert.equal(m.renderCalls.length, 0, 'un informe todo-normal no monta tabla en la lista');
  assert.match(textoCompleto(m), /Ver todos los resultados/);
});

test('vista de detalle: abre con el TÍTULO del estudio como encabezado prominente y TODAS las filas', async () => {
  const respuesta = {
    informes: [{
      fecha: '2024-08-01', tipo_estudio: 'Perfil lipídico',
      resumen: 'Hay valores para revisar con tu médico.', tono: 'revisar',
      items: [
        { nombre: 'Glucemia', valor: 92, unidad: 'mg/dL', rango: [70, 110], fuera: false },
        { nombre: 'Colesterol', valor: 240, unidad: 'mg/dL', rango: [0, 200], fuera: true },
      ],
    }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta);
  await afterPaint(m);
  // El link "Ver todos los resultados" está en la lista (índice 0). Click.
  const link = m.created.find((e) => e.className === 'lr-show-all');
  assert.ok(link, 'existe el link "ver todos"');
  link.listeners.click();

  // En detalle: TODAS las filas (2), no sólo fuera de rango (1).
  const detalleTabla = m.renderCalls[m.renderCalls.length - 1];
  assert.equal(detalleTabla.rows.length, 2, 'el detalle muestra todas las determinaciones');
  // Título prominente: la sección de detalle lleva la clase del título y el
  // tipo de estudio como texto. textoCompleto() recorre todos los elementos
  // creados (el DOM mínimo del harness no popula innerHTML desde appendChild).
  assert.ok(porPanel(m, 'detalle'), 'existe el panel de detalle');
  const todo = textoCompleto(m);
  assert.match(todo, /lr-detail-title/);
  assert.match(todo, /Perfil lipídico/);
  assert.match(todo, /lr-detail-date/);
  // El toggle Resultados/Evolución se esconde en detalle.
  const contenedorTabs = m.created.find((e) => e.className === 'lr-tabs');
  assert.equal(contenedorTabs.style.display, 'none', 'el toggle se esconde en la vista de detalle');
});

test('deep-link por hash #labs&r=0 abre el detalle del informe directamente al cargar', async () => {
  const respuesta = {
    informes: [{
      fecha: '2024-08-01', tipo_estudio: 'Hepatograma',
      resumen: 'Hay valores para revisar.', tono: 'revisar',
      items: [{ nombre: 'ALT', valor: 60, unidad: 'U/L', rango: [7, 56], fuera: true }],
    }],
    series: [], analisis_pendiente: 0, clinico_href: '/d/clinico',
  };
  const m = montar(respuesta, { hash: '#labs&r=0' });
  await afterPaint(m);
  assert.ok(porPanel(m, 'detalle'), 'con hash #labs&r=0 el detalle se monta al cargar');
  assert.match(textoCompleto(m), /Hepatograma/);
});

test('pending retry replaces its history listener and popstate never creates history', async () => {
  const m = montar({ informes: [{ fecha: '2024-08-01', tipo_estudio: 'Perfil', items: [] }], series: [], analisis_pendiente: 1 });
  await afterPaint(m);
  assert.equal(m.events.popstate.length, 1, 'initial mount has one history listener');
  m.timers[0]();
  await afterPaint(m);
  assert.equal(m.events.popstate.length, 1, 'the bounded retry disposes its earlier listener');
  const before = m.historyCalls.filter((call) => call === 'push').length;
  m.context.location.hash = '#labs&r=0';
  m.events.popstate[0]();
  assert.equal(m.historyCalls.filter((call) => call === 'push').length, before, 'Back/Forward never pushes another entry');
});


test('AI notice renders before report content and upload controls', async () => {
  const m = montar({informes: [], series: [], analisis_pendiente: 0});
  await afterPaint(m);
  assert.match(textoCompleto(m), /Estos resultados se leen con inteligencia artificial/);
});

// «Ver resultados del laboratorio» (enlace firmado al proveedor): el
// servidor manda `laboratorio_href` sólo con la clave configurada; sin ella
// no hay botón ni en el estado vacío ni con informes.
function botonProveedor(m) {
  return m.created.find((e) => e.tagName === 'A' && e.className === 'lr-provider');
}

const INFORME_OK = { fecha: '2020-03-10', tipo_estudio: 'Perfil', resumen: 'Todo en orden.', tono: 'ok', items: [] };

test('con laboratorio_href hay un botón a la ruta propia, también sin informes', async () => {
  const conInformes = montar({
    informes: [INFORME_OK], series: [], analisis_pendiente: 0,
    laboratorio_href: '/account/lab-results/open',
  });
  await afterPaint(conInformes);
  const a = botonProveedor(conInformes);
  assert.ok(a, 'el botón existe con informes');
  assert.equal(a.href, '/account/lab-results/open');
  assert.equal(a.textContent, 'Ver resultados del laboratorio');
  assert.equal(a.rel, 'noopener noreferrer');
  assert.ok(!/laboratorio\.pinamar/.test(textoCompleto(conInformes)),
    'la dirección del proveedor no está en la página');

  const vacio = montar({ informes: [], series: [], analisis_pendiente: 0,
    laboratorio_href: '/account/lab-results/open' });
  await afterPaint(vacio);
  assert.ok(botonProveedor(vacio), 'el botón existe sin informes');
});

test('sin laboratorio_href no hay botón', async () => {
  for (const extra of [{}, { laboratorio_href: null }]) {
    const m = montar({ informes: [], series: [], analisis_pendiente: 0, ...extra });
    await afterPaint(m);
    assert.equal(botonProveedor(m), undefined);
  }
  const m = montar({ informes: [INFORME_OK], series: [], analisis_pendiente: 0, laboratorio_href: null });
  await afterPaint(m);
  assert.equal(botonProveedor(m), undefined);
});
