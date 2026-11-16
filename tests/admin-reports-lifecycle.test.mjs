// Whole-module runtime tests for the native Reports controller. The real
// static/platform/admin-reports.mjs is imported and executed end to end;
// only the external boundaries are faked: the dependency modules arrive as
// data: URLs through window.SANA_STATIC (exactly how importStatic loads
// them), the DOM host is an Element stub, and fetch routes by URL. No
// production logic is copied here, and an unexpected request URL fails the
// test loudly instead of passing silently.
import assert from "node:assert/strict";
import test from "node:test";
import { mountReports } from "../static/platform/admin-reports.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 12) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }
const ok = body => Promise.resolve(response(200, body));
const fail = status => Promise.resolve(response(status));

// The charts dependency fake records the setAncho→charts measurement order
// into a per-test global; every builder returns a short marker so tests can
// prove the real render wired the dependency without reimplementing charts.
const CHARTS_SRC = [
  "const ops=()=>globalThis.__reportsChartsOps;",
  "export function setAncho(w){ops().push(['setAncho',w])}",
  "export function scatter(r,o){ops().push(['scatter']);return '[chart:scatter]'}",
  "export function heatmap(g,o){ops().push(['heatmap']);return '[chart:heatmap]'}",
  "export function treemap(i,o){ops().push(['treemap']);return '[chart:treemap]'}",
  "export function slope(r,o){ops().push(['slope']);return '[chart:slope]'}",
  "export function lollipop(r,o){ops().push(['lollipop']);return '[chart:lollipop]'}",
  "export function linea(s,o){ops().push(['linea']);return '[chart:linea]'}",
  "export function densidad(v,o){ops().push(['densidad']);return '[chart:densidad]'}",
  "export function piramide(l,o){ops().push(['piramide']);return '[chart:piramide]'}",
  "export function tile(o){ops().push(['tile',o]);return '[tile:'+(o&&o.etiqueta||'')+']'+(o&&o.sub?'[sub:'+o.sub+']':'')}",
  "export function hero(f){ops().push(['hero']);return '[chart:hero]'}",
  "export function frase(f){return f.texto}",
].join(";");

// Minimal element stub: setting innerHTML registers every id="..." it
// contains as a queryable child, which is all the renders need from the DOM
// (#rep-body host plus the periodo/mes selects they wire listeners to).
class Element {
  constructor(id = "") { this.id = id; this.isConnected = true; this._html = ""; this.style = {}; this.listeners = new Map(); this.children = new Map(); this.value = ""; this.clientWidth = 800; }
  get innerHTML() { return this._html; }
  set innerHTML(html) { this._html = String(html); this.children = new Map(); const re = /id="([^"]+)"/g; let m; while ((m = re.exec(this._html))) this.children.set(m[1], new Element(m[1])); }
  querySelector(selector) { return selector[0] === "#" ? (this.children.get(selector.slice(1)) || null) : null; }
  querySelectorAll() { return []; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
}

function fixture({ search = "", routes, confirmar = () => false, staticOverrides = {} }, t) {
  const host = new Element("sec-reportes");
  const keys = ["window", "fetch", "location", "__reportsConfirm", "__reportsChartsOps", "__reportsNavigated"];
  const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.__reportsConfirm = confirmar;
  globalThis.__reportsChartsOps = [];
  globalThis.location = { href: "https://example.test/admin/reportes", pathname: "/admin/reportes", search };
  globalThis.window = {
    SANA_STATIC: Object.assign({
      "platform/dates.mjs": "data:text/javascript,export const dia=(iso)=>iso",
      // Same live-owner contract as the real admin-session.mjs: a boundary
      // answer only exists for the CURRENT owner; a disposed owner's 401/403
      // resolves false and never reaches the redirect side effect.
      // Encode the complete source: Node 20 treats an unescaped question
      // mark as the URL query and truncates this ternary expression.
      "platform/admin-session.mjs": "data:text/javascript," + encodeURIComponent("export const confirmar=(response,parsedBody,isCurrent)=>(!response||!isCurrent())?Promise.resolve(false):globalThis.__reportsConfirm(response)"),
      "platform/admin-charts.mjs": "data:text/javascript," + encodeURIComponent(CHARTS_SRC),
      "platform/search.mjs": "data:text/javascript,export const buscarConPuntaje=()=>[]",
      "platform/i18n.mjs": new URL("../static/platform/i18n.mjs", import.meta.url).href,
    }, staticOverrides),
  };
  const requests = [];
  const signals = [];
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    if (options.signal) signals.push(options.signal);
    assert.ok(typeof routes === "function", "unexpected Reports fetch: " + url);
    const next = routes(url, requests.length);
    assert.ok(next && typeof next.then === "function", "unrouted Reports fetch: " + url);
    return next;
  };
  const disposes = [];
  t.after(() => {
    disposes.forEach(dispose => dispose());
    for (const [key, descriptor] of Object.entries(previous)) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key];
  });
  const run = options => { const dispose = mountReports(Object.assign({ root: host }, options)); disposes.push(dispose); return dispose; };
  return { host, windowRef: globalThis.window, requests, signals, run, get repBody() { return host.children.get("rep-body"); } };
}

// Payloads: one per endpoint, shaped exactly as reports.py/costos_ia.py
// serve them — every field the renders touch is present so a missing-field
// TypeError surfaces as a loud rejection instead of a silent skip.
const GESTION_30 = {
  dias: 30, hero: [],
  kpis: { pendientes: 12, solicitudes_periodo: 120, pct_aprobadas: 80, mediana_horas_resolucion: 10, mediana_dias_turno: 5, mediana_horas_pendiente: 36, max_horas_pendiente: 100, esperas_pendientes_horas: [10, 36, 100] },
  oferta_demanda: { clusters: [], equilibrio: null },
  heatmap: { dias: [], horas: [], demanda: [], desajuste: [] },
  palancas: { grupos: [], hero: "Palancas hero" },
  funnel: {
    holds_creados: 10, solicitudes: 8, aprobadas: 6, rechazadas: 1, canceladas: 1, asistencia: { presente: 3, ausente: 1 },
    pasos: [{ key: "pedido", label: "Pidieron turno", n: 8 }, { key: "aprobada", label: "Les dieron turno", n: 6 }],
    frases: [{ tono: "atencion", texto: "Donde más se pierde: entre «Pidieron turno» y «Les dieron turno» se quedaron 2, el 25%." }],
  },
  lista_espera: { turnos_recuperados: 2 },
  volumen: [],
  ahorro: { disponible: true, horas_operador: 2.5, ahorro_ars: 12500, roi: 1.2, operaciones: 375, supuestos: ["s1"] },
};
const gestionBody = dias => Object.assign({}, GESTION_30, { dias, kpis: Object.assign({}, GESTION_30.kpis) });
// Two specialties: a treemap of one part is not drawn (2026-11-08, E9).
const DOCTORS_STATS = { medicos: {
  "dra-garcia": { horas_semana: 20, turnos_14d: 30, slots_14d: 40, ocupacion_14d: 75, tiene_foto: true, tiene_login: true },
  "dr-perez": { horas_semana: 8, turnos_14d: 4, slots_14d: 16, ocupacion_14d: 25, tiene_foto: true, tiene_login: true },
} };
const DOCTORS_CONTENT = { items: [
  { id: "dra-garcia", nombre: "Dra. Garcia", especialidad: "Clínica", centro: "caps-central" },
  { id: "dr-perez", nombre: "Dr. Perez", especialidad: "Pediatría", centro: "caps-central" },
] };
const PATIENTS_30 = {
  dias: 30, kpis: { total_cuentas: 100, nuevas_periodo: 10, pct_con_email: 80, pct_con_domicilio: 50 },
  cobertura: { pct_con_obra_social: 60, pct_con_prepaga: 10, top_obras_sociales: [] },
  dependientes: { total: 5, adultos_con_dependientes: 2 },
  actividad: { pacientes_con_solicitud: 25, promedio_por_paciente: 1.5 },
  volumen: [], edades: [],
};
const SEARCHES_30 = { top: [{ q: "cardio", veces: 5 }] };
const SERVICES = { servicios: [{ id: "cardio", nombre: "Cardiología", centro: "caps-central" }] };
const DOCTORS_API = { medicos: [] };
const CENTERS_API = { centros: [{ slug: "caps-central", name: "CAPS Central" }] };
const REVIEWS = { centers: {} };
// A centers payload with one analyzable center, so the Centros view's real
// card path (treemap + named staff + triage + quotes) renders instead of its
// empty-state line.
const REVIEWS_CARD = {
  centers: {
    "caps-central": {
      name: "CAPS Central", count: 12,
      hero: ["Las reseñas destacan el trato."],
      themes: {
        trato: { name: "Trato del personal", n: 5, avg_rating: 4.2, category: "trato", quotes: [{ text: "Me atendieron de diez", rating: 5 }] },
        espera: { name: "Tiempo de espera", n: 3, avg_rating: 2.8, category: "espera", quotes: [] },
      },
      named_staff: [{ name: "Dra. Garcia", n: 3, quote: "Una genia" }],
      triage: { fix: [{ topic: "Turnos y acceso telefónico", action: "Publicar el teléfono correcto." }], decline: [] },
    },
  },
};
const RECUPERO_90 = { dias: 90, kpis: { consultas_con_cobertura: 20, pacientes_con_cobertura: 15, presentes: 18, ausentes: 2, pct_del_total: 40 }, por_obra_social: [], tendencia: [] };
const SUMAR_90 = { dias: 90, kpis: { prestaciones: 10, pacientes: 8, pct_del_total: 20 }, trazadoras: [], tendencia: [] };
const statisticsBody = mes => ({ mes, kpis: { consultas_totales: 50, presentes: 40, ausentes: 5, sin_marca: 5, pct_asistencia: 88 }, por_especialidad: [], edades: [], por_centro: [] });
// 90 %: «sumar horas» is written only from the hero's threshold up (admin-reports-disclosure pins the rule).
const CAPACITY = { hero: [{ texto: "", tono: "atencion", regla: "atencion" }], ocupacion: [{ label: "Clínica", pct: 90, ocupados_14d: 9, capacidad_14d: 10 }], demanda_semanal: [], sin_oferta: [], temporada: { disponible: false } };
const COSTS_30 = {
  dias: 30,
  resumen: { total_usd: 1.5, run_rate_mensual_usd: 1.5, costo_prom_op: 0.004, operaciones: 375, tasa_reconocimiento: 90, no_reconocidas: 37, ips: 100, costo_mediano_por_ip: 0.01, por_1000_pacientes_usd: 5, dias_completos: 5 },
  por_operacion: [{ label: "dni", cantidad: 300, costo_usd: 1.2, costo_prom: 0.004 }],
  diario: [], sospechosos: [], abuso_activo: false, top_ips: [],
};
const LOGIN_30 = { dias: 30, sesiones: 40, logins: 33, errores: 12, sesiones_con_error: 9, ms_mediana_login: 4200 };
const RECORRIDO_30 = {
  dias: 30, filtros: {}, opciones: { centros: [], servicios: [], medicos: [] },
  pasos_config: [{ key: "pedido", on: true, label: "Pidieron turno" }, { key: "presente", on: true, label: "Fueron al turno" }],
  pasos: [
    { key: "pedido", label: "Pidieron turno", n: 10, pct: 100, perdidos: 0, en_curso: 0, motivos: [] },
    { key: "presente", label: "Fueron al turno", n: 4, pct: 40, perdidos: 6, en_curso: 0, motivos: [{ key: "ausente", n: 6, texto: "6 no fueron", en_curso: false }] },
  ],
  mayor_perdida: { desde: "pedido", hasta: "presente", n: 6, pct: 60, motivo: "ausente" },
  hero: [{ tono: "ok", texto: "4 de 10 llegaron hasta «Fueron al turno»." }],
  sin_datos: [{ key: "visitas", label: "Entrar al sitio" }],
};

// Full portada route table (the 13-way fanout's 17 HTTP calls). `over`
// replaces or holds any URL; the statistics endpoint echoes the requested
// month because the default month is computed from the live clock.
function defaultRoutes(over = {}) {
  const table = {
    "/api/admin/reports?days=30": ok(GESTION_30),
    "/api/admin/doctors/stats": ok(DOCTORS_STATS),
    "/api/admin/content/doctors": ok(DOCTORS_CONTENT),
    "/api/admin/reports/patients?days=30": ok(PATIENTS_30),
    "/api/admin/reports/searches?days=30": ok(SEARCHES_30),
    "/api/services": ok(SERVICES),
    "/api/doctors": ok(DOCTORS_API),
    "/api/centers": ok(CENTERS_API),
    "/api/admin/reports/reviews": ok(REVIEWS),
    "/api/admin/reports/cost-recovery?days=90": ok(RECUPERO_90),
    "/api/admin/reports/sumar?days=90": ok(SUMAR_90),
    "/api/admin/reports/capacity": ok(CAPACITY),
    "/api/admin/costs?days=30": ok(COSTS_30),
    "/api/admin/reports/login-panel?days=30": ok(LOGIN_30),
    "/api/admin/reports/funnel?days=30": ok(RECORRIDO_30),
    "/api/admin/reports/visits?days=30": ok({ dias: 30, visitas: 0, embudo: [], serie: [] }),
  };
  return url => {
    if (url.startsWith("/api/admin/reports/statistics?mes=")) return ok(statisticsBody(url.slice(url.indexOf("?mes=") + 5)));
    if (Object.prototype.hasOwnProperty.call(over, url)) return over[url];
    const hit = table[url];
    assert.ok(hit, "unexpected Reports fetch: " + url);
    return hit;
  };
}

function gestionRoutes(over = {}) {
  return url => {
    if (Object.prototype.hasOwnProperty.call(over, url)) return over[url];
    assert.ok(url.startsWith("/api/admin/reports?days="), "unexpected Gestión fetch: " + url);
    return ok(gestionBody(parseInt(url.slice(url.indexOf("?days=") + 6), 10)));
  };
}

// Echo routes for the two CSV-bearing sub-reports: the requested window comes
// back as the payload's `dias`, exactly like reports.py serves it, so the
// painted CSV link can be proven against the period the server just answered.
function recuperoRoutes() {
  return url => {
    assert.ok(url.startsWith("/api/admin/reports/cost-recovery?days="), "unexpected Recupero fetch: " + url);
    const dias = parseInt(url.slice(url.indexOf("?days=") + 6), 10);
    return ok(Object.assign({}, RECUPERO_90, { dias, kpis: Object.assign({}, RECUPERO_90.kpis) }));
  };
}
function sumarRoutes() {
  return url => {
    assert.ok(url.startsWith("/api/admin/reports/sumar?days="), "unexpected SUMAR+ fetch: " + url);
    const dias = parseInt(url.slice(url.indexOf("?days=") + 6), 10);
    return ok(Object.assign({}, SUMAR_90, { dias, kpis: Object.assign({}, SUMAR_90.kpis) }));
  };
}

const changePeriod = (page, value, id = "rep-periodo") => { const sel = page.repBody.children.get(id); assert.ok(sel, "period select missing: " + id); sel.value = value; sel.listeners.get("change")(); };

// Progressive disclosure (2026-10): a failed endpoint (Recupero here) and an
// empty payload (REVIEWS has no centers) each cost exactly their own card,
// never a placeholder card saying so.
test("mounted portada paints every data-bearing summary card and drops the failed and empty ones", async t => {
  const page = fixture({ routes: defaultRoutes({ "/api/admin/reports/cost-recovery?days=90": fail(500) }) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.equal((html.match(/class="rep-sumcard[ "]/g) || []).length, 10);
  assert.equal((html.match(/Sin datos disponibles todavía\./g) || []).length, 0);
  assert.doesNotMatch(html, /reportes\?view=recupero/);
  assert.doesNotMatch(html, /reportes\?view=centros/);
  assert.doesNotMatch(html, /Cargando/);
  assert.match(html, /Horas devueltas|horas de trabajo al equipo/);
  assert.match(html, /sesiones de login chocaron con un error/);
  assert.ok(globalThis.__reportsChartsOps.some(op => op[0] === "densidad"));
  const sanaKeys = Object.keys(page.windowRef).filter(key => /sana/i.test(key));
  assert.deepEqual(sanaKeys, ["SANA_STATIC"]);
});

// Critical review 08.11, E7: the overview tile said 68 and the Pacientes
// report opened on 117 under another name. The tile and the report's first
// number are one figure with one name (figuraPacientes).
test("the overview Pacientes tile and the report's first number are the same figure and name", async t => {
  const portada = fixture({ routes: defaultRoutes() }, t);
  await portada.run().ready;
  await settle();
  const tile = portada.repBody.innerHTML.match(/href="\/admin\/reportes\?view=pacientes">[\s\S]*?<div class="rep-sumcard-value">([^<]*)<\/div><div class="rep-sumcard-find">([^<]*)<\/div>/);
  assert.ok(tile, "Pacientes tile missing");
  const report = fixture({ search: "?view=pacientes", routes: defaultRoutes() }, t);
  await report.run().ready;
  await settle();
  const first = report.repBody.innerHTML.match(/<div class="rep-kpis"><div class="rep-kpi"><div class="rep-kpi-value">([^<]*)<\/div><div class="rep-kpi-label">([^<]*)<\/div>/);
  assert.ok(first, "first KPI missing");
  assert.deepEqual([tile[1], tile[2]], [first[1], first[2]]);
  assert.deepEqual([first[1], first[2]], ["25", "Pacientes que pidieron turno en 30 días"]);
  assert.match(report.repBody.innerHTML, /<div class="rep-kpi-value">100<\/div><div class="rep-kpi-label">Cuentas registradas<\/div>/);
});

test("Gestión deep link fetches once, keeps the Charts measurement order and the CSV contract", async t => {
  const page = fixture({ search: "?view=gestion", routes: gestionRoutes() }, t);
  await page.run().ready;
  await settle();
  assert.equal(page.requests.length, 1);
  assert.equal(page.requests[0].url, "/api/admin/reports?days=30");
  const html = page.repBody.innerHTML;
  assert.match(html, /<a href="\/admin\/reportes">Reportes<\/a> \/ <details class="rep-switch"><summary>Gestión</);
  assert.match(html, /href="\/api\/admin\/reports\/requests\.csv"/);
  assert.match(html, /\[chart:hero\]/);
  const ops = globalThis.__reportsChartsOps;
  assert.equal(ops[0][0], "setAncho");
  assert.equal(ops[0][1], 800);
  assert.ok(ops.length > 1);
  assert.ok(page.repBody.children.has("rep-periodo"));
  // E7: the funnel steps carry the server's Recorrido names, and the
  // percentage under each step names the step it is a share of.
  const tiles = ops.filter(([kind]) => kind === "tile").map(([, a]) => [a.etiqueta, a.valor, a.sub]);
  const start = tiles.findIndex(([label]) => label === "Eligieron un horario");
  assert.ok(start >= 0, "funnel tiles missing");
  assert.deepEqual(tiles.slice(start, start + 3), [
    ["Eligieron un horario", "10", ""],
    ["Pidieron turno", "8", "80% de «Eligieron un horario»"],
    ["Les dieron turno", "6", "75% de «Pidieron turno»"],
  ]);
  assert.match(html, /<p class="rep-action">Donde más se pierde: entre «Pidieron turno» y «Les dieron turno» se quedaron 2, el 25%\.<\/p>/);
  assert.doesNotMatch(html, /Se aprobaron|Se presentaron|Total pedidas/);
});

test("the funnel never prints a share of the holds against another first step", async t => {
  // «Eligieron un horario» pairs only with «Pidieron turno» (the request a
  // hold became). With the steps reordered or «Pidieron turno» off (a setting
  // the admin can save), the first step counts a population the holds do not
  // contain, so no percentage is printed there — the share would read like a
  // conversion it is not (code review 09.11).
  const body = Object.assign({}, GESTION_30, {
    funnel: Object.assign({}, GESTION_30.funnel, {
      pasos: [{ key: "aprobada", label: "Les dieron turno", n: 6 }, { key: "presente", label: "Fueron al turno", n: 3 }],
    }),
  });
  const page = fixture({ search: "?view=gestion", routes: gestionRoutes({ "/api/admin/reports?days=30": ok(body) }) }, t);
  await page.run().ready;
  await settle();
  const tiles = globalThis.__reportsChartsOps.filter(([kind]) => kind === "tile").map(([, a]) => [a.etiqueta, a.valor, a.sub]);
  const start = tiles.findIndex(([label]) => label === "Eligieron un horario");
  assert.ok(start >= 0, "funnel tiles missing");
  assert.deepEqual(tiles.slice(start, start + 3), [
    ["Eligieron un horario", "10", ""],
    ["Les dieron turno", "6", ""],
    ["Fueron al turno", "3", "50% de «Les dieron turno»"],
  ]);
});

test("the mount's supplied country currency controls actual savings formatting", async t => {
  const page = fixture({ search: "?view=ia", routes: defaultRoutes() }, t);
  await page.run({ currency: { code: "USD", symbol: "$", locale: "en-US", iso2: "US" } }).ready;
  const savings = globalThis.__reportsChartsOps.find(([kind, args]) =>
    kind === 'tile' && args.etiqueta === 'Equivalente en horas de operador');
  assert.equal(savings[1].valor, 'US$ 12,500');
  assert.match(page.repBody.innerHTML, /US\$ 1\.50/);
  assert.doesNotMatch(page.repBody.innerHTML, /AR\$/);
});

test("a live period change refetches and repaints the new selection", async t => {
  const page = fixture({ search: "?view=gestion", routes: gestionRoutes() }, t);
  await page.run().ready;
  await settle();
  changePeriod(page, "7");
  await settle();
  assert.equal(page.requests.filter(r => r.url === "/api/admin/reports?days=7").length, 1);
  assert.match(page.repBody.innerHTML, /Solicitudes en 7 días/);
  assert.doesNotMatch(page.repBody.innerHTML, /Solicitudes en 30 días/);
});

test("a delayed stale period response loses to the newer selection", async t => {
  const seven = deferred(), ninety = deferred();
  const page = fixture({
    search: "?view=gestion",
    routes: gestionRoutes({
      "/api/admin/reports?days=7": seven.promise,
      "/api/admin/reports?days=90": ninety.promise,
    }),
  }, t);
  await page.run().ready;
  await settle();
  changePeriod(page, "7");
  changePeriod(page, "90");
  ninety.resolve(response(200, gestionBody(90)));
  await settle();
  assert.match(page.repBody.innerHTML, /Solicitudes en 90 días/);
  seven.resolve(response(200, gestionBody(7)));
  await settle();
  assert.doesNotMatch(page.repBody.innerHTML, /Solicitudes en 7 días/);
  assert.match(page.repBody.innerHTML, /Solicitudes en 90 días/);
  assert.equal(page.signals[1].aborted, true);
  assert.equal(page.signals[2].aborted, false);
});

test("revisiting the cached period beats the still-pending older request", async t => {
  const seven = deferred();
  const page = fixture({
    search: "?view=gestion",
    routes: gestionRoutes({ "/api/admin/reports?days=7": seven.promise }),
  }, t);
  await page.run().ready;
  await settle();
  changePeriod(page, "7");
  changePeriod(page, "30");
  await settle();
  assert.match(page.repBody.innerHTML, /Solicitudes en 30 días/);
  seven.resolve(response(200, gestionBody(7)));
  await settle();
  assert.doesNotMatch(page.repBody.innerHTML, /Solicitudes en 7 días/);
});

test("a superseded period response cannot redirect the still-mounted owner", async t => {
  const stale = deferred();
  const page = fixture({ search: "?view=gestion",
    confirmar: () => { globalThis.__reportsNavigated = true; return Promise.resolve(true); },
    routes: gestionRoutes({ "/api/admin/reports?days=7": stale.promise }),
  }, t);
  await page.run().ready;
  changePeriod(page, "7");
  changePeriod(page, "30");
  await settle();
  stale.resolve(response(401));
  await settle();
  assert.notEqual(globalThis.__reportsNavigated, true);
  assert.match(page.repBody.innerHTML, /Solicitudes en 30 días/);
});

test("an aborted period response never publishes a stale cache entry", async t => {
  const stale = deferred();
  let sevens = 0;
  const page = fixture({ search: "?view=gestion", routes: url => {
    if (url.endsWith("days=7")) return ++sevens === 1 ? stale.promise : ok(gestionBody(7));
    return ok(gestionBody(30));
  } }, t);
  await page.run().ready;
  changePeriod(page, "7");
  changePeriod(page, "30");
  await settle();
  stale.resolve(response(200, gestionBody(7)));
  await settle();
  changePeriod(page, "7");
  await settle();
  assert.equal(sevens, 2, "aborted response must not become the next load's cache");
  assert.match(page.repBody.innerHTML, /Solicitudes en 7 días/);
});

test("a live Estadística month change refetches and rewrites the CSV link", async t => {
  const page = fixture({ search: "?view=estadistica", routes: defaultRoutes() }, t);
  await page.run().ready;
  await settle();
  const firstMes = page.requests[0].url.slice(page.requests[0].url.indexOf("?mes=") + 5);
  assert.match(page.repBody.innerHTML, new RegExp("statistics\\.csv\\?mes=" + firstMes));
  const [y, m] = firstMes.split("-").map(Number);
  const secondMes = m === 1 ? (y - 1) + "-12" : y + "-" + String(m - 1).padStart(2, "0");
  const sel = page.repBody.children.get("est-mes");
  assert.ok(sel, "month select missing");
  sel.value = secondMes;
  sel.listeners.get("change")();
  await settle();
  assert.ok(page.requests.some(r => r.url === "/api/admin/reports/statistics?mes=" + secondMes));
  assert.match(page.repBody.innerHTML, new RegExp("statistics\\.csv\\?mes=" + secondMes));
});

test("the disposer makes retained event callbacks inert and is idempotent", async t => {
  const page = fixture({ search: "?view=gestion", routes: gestionRoutes() }, t);
  const dispose = page.run();
  await dispose.ready;
  await settle();
  dispose();
  dispose();
  changePeriod(page, "7");
  await settle();
  assert.equal(page.requests.length, 1);
  assert.doesNotMatch(page.repBody.innerHTML, /Solicitudes en 7 días/);
});

test("data failure offers deliberate reload and its retained click dies with the owner", async t => {
  const page = fixture({ search: "?view=gestion", routes: gestionRoutes({ "/api/admin/reports?days=30": fail(503) }) }, t);
  let reloads = 0;
  globalThis.location.reload = () => { reloads += 1; };
  const dispose = page.run();
  await dispose.ready;
  assert.match(page.repBody.innerHTML, /La base de datos no está disponible/);
  assert.match(page.repBody.innerHTML, /role="alert"/);
  assert.match(page.repBody.innerHTML, /id="reports-retry">Recargar<\/button>/);
  const retry = page.host.listeners.get("click");
  retry({ target: { id: "unrelated" } });
  assert.equal(reloads, 0);
  retry({ target: { id: "reports-retry" } });
  assert.equal(reloads, 1);
  dispose();
  assert.equal(page.host.listeners.has("click"), false);
  retry({ target: { id: "reports-retry" } });
  assert.equal(reloads, 1);
});

test("a failed load recovers through a remount (retry)", async t => {
  let calls = 0;
  const page = fixture({
    search: "?view=gestion",
    routes: url => {
      assert.ok(url.startsWith("/api/admin/reports?days="), "unexpected retry fetch: " + url);
      calls += 1;
      return calls === 1 ? Promise.reject(new Error("boom")) : ok(gestionBody(30));
    },
  }, t);
  const first = page.run();
  // A failed data load is HANDLED (visible recovery line), so .ready settles.
  await first.ready;
  await settle();
  assert.match(page.repBody.innerHTML, /No se pudo cargar\. Reintentá\./);
  const second = page.run();
  await second.ready;
  await settle();
  assert.match(page.repBody.innerHTML, /Solicitudes en 30 días/);
});

test("a remount's paint survives the disposed owner's late completion", async t => {
  const stale = deferred();
  let calls = 0;
  const page = fixture({
    search: "?view=gestion",
    routes: url => {
      assert.ok(url.startsWith("/api/admin/reports?days="), "unexpected remount fetch: " + url);
      calls += 1;
      return calls === 1 ? stale.promise : ok(gestionBody(30));
    },
  }, t);
  page.run();
  await settle();
  const latest = page.run();
  await latest.ready;
  await settle();
  assert.match(page.repBody.innerHTML, /Solicitudes en 30 días/);
  stale.resolve(response(200, gestionBody(7)));
  await settle();
  assert.doesNotMatch(page.repBody.innerHTML, /Solicitudes en 7 días/);
  assert.equal(page.signals[0].aborted, true);
});

test("a disposed owner's 401 never reaches the session redirect", async t => {
  const stale401 = deferred();
  let calls = 0;
  const confirmar = () => { globalThis.__reportsNavigated = true; return Promise.resolve(true); };
  const page = fixture({
    search: "?view=gestion",
    confirmar,
    routes: url => {
      assert.ok(url.startsWith("/api/admin/reports?days="), "unexpected 401-race fetch: " + url);
      calls += 1;
      return calls === 1 ? stale401.promise : ok(gestionBody(30));
    },
  }, t);
  page.run();
  await settle();
  const latest = page.run();
  await latest.ready;
  await settle();
  stale401.resolve(response(401));
  await settle();
  assert.notEqual(globalThis.__reportsNavigated, true);
  assert.match(page.repBody.innerHTML, /Solicitudes en 30 días/);
});

test("a confirmed 401 on the live owner keeps the prior content and stops the owner", async t => {
  const page = fixture({
    search: "?view=gestion",
    confirmar: () => { globalThis.__reportsNavigated = true; return Promise.resolve(true); },
    routes: gestionRoutes({ "/api/admin/reports?days=7": fail(401) }),
  }, t);
  await page.run().ready;
  await settle();
  changePeriod(page, "7");
  await settle();
  assert.equal(globalThis.__reportsNavigated, true);
  assert.match(page.repBody.innerHTML, /Solicitudes en 30 días/);
  assert.doesNotMatch(page.repBody.innerHTML, /Reintentá/);
  changePeriod(page, "7");
  await settle();
  assert.equal(page.requests.length, 2);
});

// An unconfirmed 401 (site Basic Auth, a proxy) must reach the same load-error
// line with its reload button in every sub-report, never an empty body. The
// summary degrades card by card ("s/d"), so it has no whole-page error body.
for (const view of ["gestion", "recorrido", "medicos", "pacientes", "busquedas", "centros", "recupero", "sumar", "estadistica", "capacidad", "ia", "login"]) {
  test("an unconfirmed 401 in ?view=" + view + " shows the load error with its reload button", async t => {
    const page = fixture({ search: "?view=" + view, routes: () => fail(401), confirmar: () => Promise.resolve(false) }, t);
    await page.run().ready;
    await settle();
    assert.match(page.repBody.innerHTML, /No se pudo cargar/);
    assert.ok(page.repBody.innerHTML.includes("reports-retry"));
  });
}

test("a missing dependency rejects through .ready, paints a visible line and fetches nothing", async t => {
  const page = fixture({
    routes: defaultRoutes(),
    staticOverrides: { "platform/admin-charts.mjs": undefined },
  }, t);
  await assert.rejects(page.run().ready, /Missing Reports dependency: platform\/admin-charts\.mjs/);
  await settle();
  assert.equal(page.requests.length, 0);
  assert.match(page.host.innerHTML, /role="alert"/);
  assert.match(page.host.innerHTML, /No se pudo cargar\. Reintentá\./);
});

test("a hanging dependency keeps .ready pending, paints nothing and fetches nothing", async t => {
  const page = fixture({
    routes: defaultRoutes(),
    staticOverrides: { "platform/dates.mjs": "data:text/javascript,await new Promise(() => {})" },
  }, t);
  const dispose = page.run();
  const sentinel = "dates module still hanging";
  await assert.rejects(Promise.race([
    dispose.ready,
    new Promise((_, reject) => setTimeout(() => reject(new Error(sentinel)), 120)),
  ]), new RegExp(sentinel));
  await settle();
  assert.equal(page.requests.length, 0);
  assert.equal(page.host.innerHTML, "");
});

test(".loaded settles true once dependencies are in, while the first data answer is still pending", async t => {
  const page = fixture({
    search: "?view=gestion",
    routes: defaultRoutes({ "/api/admin/reports?days=30": new Promise(() => {}) }),
  }, t);
  const dispose = page.run();
  assert.equal(await dispose.loaded, true);
  await settle();
  assert.match(page.host.innerHTML, /role="status">Cargando…/);
  assert.doesNotMatch(page.host.innerHTML, /No se pudo cargar/);
  assert.equal(page.requests.length, 1);
});

test(".loaded settles false when a dependency is missing", async t => {
  const page = fixture({ routes: defaultRoutes(), staticOverrides: { "platform/dates.mjs": undefined } }, t);
  const dispose = page.run();
  dispose.ready.catch(() => {});
  assert.equal(await dispose.loaded, false);
});

test(".loaded never settles while a dependency hangs", async t => {
  const page = fixture({
    routes: defaultRoutes(),
    staticOverrides: { "platform/dates.mjs": "data:text/javascript,await new Promise(() => {})" },
  }, t);
  const dispose = page.run();
  const sentinel = "still pending";
  await assert.rejects(Promise.race([
    dispose.loaded,
    new Promise((_, reject) => setTimeout(() => reject(new Error(sentinel)), 120)),
  ]), new RegExp(sentinel));
});

test("the IA view degrades only its savings section when the savings fetch fails", async t => {
  const page = fixture({
    search: "?view=ia",
    routes: defaultRoutes({ "/api/admin/reports?days=30": Promise.reject(new Error("ahorro down")) }),
  }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.match(html, /En qué se va el gasto/);
  assert.doesNotMatch(html, /Lo que la automatización le devuelve al equipo/);
});

// =====================================================================
// Deep-link matrix: the thirteen supported ?view keys plus the unknown-view
// fallback. Each entry pins the EXACT multiset of requested endpoints
// (`urls`; the statistics month is normalized because it is computed from
// the live clock at mount — a separate membership check proves it is a
// recent COMPLETE calendar month), the breadcrumb line, the default
// period/month selection where the view has one (and the absence of a
// controls row where it must not), and renderer output only the real
// render path can produce. Any request outside the multiset also fails
// loudly through defaultRoutes' unknown-URL assertion.
// =====================================================================
const MES_URL = "/api/admin/reports/statistics?mes=";
const normalizeUrl = url => (url.startsWith(MES_URL) ? MES_URL + "<mes>" : url);
const CRUMB_A = '<a href="/admin/reportes">Reportes</a> / ';

// Last n complete calendar months, computed the same way mesesOpciones()
// does (day 0 of the current month). 13 entries at assertion time covers a
// month boundary rolling over between the mount and this check.
function mesesCompletos(n) {
  const ref = new Date(new Date().getFullYear(), new Date().getMonth(), 0);
  let y = ref.getFullYear(), m = ref.getMonth() + 1;
  const out = [];
  for (let i = 0; i < n; i += 1) { out.push(y + "-" + String(m).padStart(2, "0")); m -= 1; if (m === 0) { m = 12; y -= 1; } }
  return out;
}

const PORTADA_URLS = [
  "/api/admin/reports?days=30",
  "/api/admin/doctors/stats",
  "/api/admin/content/doctors",
  "/api/admin/reports/patients?days=30",
  "/api/admin/reports/searches?days=30",
  "/api/services",
  "/api/doctors",
  "/api/centers",
  "/api/admin/reports/reviews",
  "/api/admin/reports/cost-recovery?days=90",
  "/api/admin/reports/sumar?days=90",
  MES_URL + "<mes>",
  "/api/admin/reports/capacity",
  "/api/admin/costs?days=30",
  "/api/admin/reports?days=30", // the IA tab's savings fetch is a second copy
  "/api/admin/reports/login-panel?days=30",
  "/api/admin/reports/funnel?days=30",
  "/api/admin/reports/visits?days=30",
];

const MATRIX = [
  {
    title: "?view=resumen", search: "?view=resumen", urls: PORTADA_URLS, crumb: null,
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /rep-sumgrid/);
      // REVIEWS carries no centers, so Centros is the one card not painted.
      assert.equal((html.match(/class="rep-sumcard[ "]/g) || []).length, 11);
      assert.match(html, /9 de 40 sesiones de login chocaron con un error en los últimos 30 días/);
    },
  },
  {
    title: "?view=gestion", search: "?view=gestion", urls: ["/api/admin/reports?days=30"], crumb: "Gestión",
    periodo: { id: "rep-periodo", selected: "30" },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /\[chart:hero\]/);
      assert.match(html, /Solicitudes en 30 días/);
      // The Gestión CSV is a fixed 90-day export: no ?days echo of the selector.
      assert.match(html, /href="\/api\/admin\/reports\/requests\.csv"/);
      assert.doesNotMatch(html, /requests\.csv\?/);
    },
  },
  {
    title: "?view=recorrido", search: "?view=recorrido", urls: ["/api/admin/reports/funnel?days=30"], crumb: "Recorrido del paciente",
    periodo: { id: "rep-periodo", selected: "30" },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /\[chart:hero\]/);
      assert.match(html, /\[chart:lollipop\]/);
      assert.match(html, /«Fueron al turno»: 4 de 10\./);
    },
  },
  {
    title: "?view=medicos", search: "?view=medicos", urls: ["/api/admin/doctors/stats", "/api/admin/content/doctors"], crumb: "Médicos",
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /Médicos en el catálogo/);
      assert.match(html, /Dra\. Garcia ya absorbe 30 turnos/);
      assert.match(html, /\[chart:densidad\]/);
      assert.match(html, /\[chart:scatter\]/);
      assert.match(html, /\[chart:treemap\]/);
    },
  },
  {
    title: "?view=pacientes", search: "?view=pacientes", urls: ["/api/admin/reports/patients?days=30"], crumb: "Pacientes",
    periodo: { id: "pac-periodo", selected: "30" },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /Cuentas registradas/);
      assert.match(html, /Nuevas en 30 días/);
    },
  },
  {
    title: "?view=busquedas", search: "?view=busquedas",
    urls: ["/api/admin/reports/searches?days=30", "/api/services", "/api/doctors", "/api/centers"], crumb: "Búsquedas",
    periodo: { id: "busq-periodo", selected: "30" },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /\[chart:hero\]/);
      // The search matcher fake returns no match, so the one logged query
      // lands in the catálogo-no-nombra table — the real clustering path.
      assert.match(html, /Lo que la gente busca y el catálogo no nombra/);
      assert.match(html, /<td>cardio<\/td>/);
    },
  },
  {
    title: "?view=centros", search: "?view=centros", urls: ["/api/admin/reports/reviews"], crumb: "Centros",
    over: { "/api/admin/reports/reviews": ok(REVIEWS_CARD) },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /CAPS Central/);
      assert.match(html, /12 reseñas/);
      assert.match(html, /\[chart:treemap\]/);
      assert.match(html, /Reconocidos por su nombre/);
      assert.match(html, /Una genia/);
      assert.match(html, /Me atendieron de diez/);
      assert.match(html, /Para aliviar al equipo/);
    },
  },
  {
    title: "?view=recupero", search: "?view=recupero", urls: ["/api/admin/reports/cost-recovery?days=90"], crumb: "Recupero de costos",
    periodo: { id: "rec-periodo", selected: "90" },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /\[tile:Consultas con cobertura\]/);
      // The hero already says the count and its share: no third «N% del total» under the tile.
      assert.doesNotMatch(html, /\[tile:Consultas con cobertura\]\[sub:/);
      assert.match(html, /cost-recovery\.csv\?days=90"/);
    },
  },
  {
    title: "?view=sumar", search: "?view=sumar", urls: ["/api/admin/reports/sumar?days=90"], crumb: "SUMAR+",
    periodo: { id: "sum-periodo", selected: "90" },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /\[tile:Prestaciones\]/);
      assert.doesNotMatch(html, /\[tile:Prestaciones\]\[sub:/);
      assert.match(html, /sumar\.csv\?days=90"/);
    },
  },
  {
    title: "?view=estadistica", search: "?view=estadistica", urls: [MES_URL + "<mes>"], crumb: "Estadística mensual",
    mesSelect: "est-mes",
    check(page, mesReqs) {
      const html = page.repBody.innerHTML;
      assert.equal(mesReqs.length, 1);
      assert.match(html, /\[tile:Consultas\]/);
      assert.match(html, new RegExp("statistics\\.csv\\?mes=" + mesReqs[0] + '"'));
      assert.ok(page.repBody.children.has("est-mes"), "month select missing");
      assert.match(html, new RegExp('value="' + mesReqs[0] + '" selected'));
    },
  },
  {
    title: "?view=capacidad", search: "?view=capacidad", urls: ["/api/admin/reports/capacity"], crumb: "Capacidad y temporada",
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /\[chart:lollipop\]/);
      assert.match(html, /Ocupación de los próximos 14 días/);
      // One percent format: no decimal at 10 % and above (ui-expert 15.11).
      assert.match(html, /Clínica está al 90% de su capacidad de los próximos 14 días \(9 de 10 turnos\)/);
    },
  },
  {
    title: "?view=ia", search: "?view=ia", urls: ["/api/admin/costs?days=30", "/api/admin/reports?days=30"], crumb: "Costos de IA",
    periodo: { id: "ia-periodo", selected: "30" },
    check(page) {
      const html = page.repBody.innerHTML;
      assert.match(html, /En qué se va el gasto/);
      assert.match(html, /Lo que la automatización le devuelve al equipo/);
      assert.match(html, /rep-bar-item/);
    },
  },
  {
    title: "?view=login", search: "?view=login", urls: ["/api/admin/reports/login-panel?days=30"], crumb: "Panel de login",
    check(page) {
      const html = page.repBody.innerHTML;
      // The closed gate experiment is gone; the friction story is tiles + action.
      assert.match(html, /\[chart:hero\]/);
      assert.match(html, /\[tile:Sesiones con intento de login\]/);
      assert.match(html, /\[tile:Chocaron con un error\]/);
      // «N de M» is the hero's phrase; under the percentage it was said a third time.
      assert.doesNotMatch(html, /\[tile:Chocaron con un error\]\[sub:/);
      assert.match(html, /\[tile:Reintentos por sesión que falla\]/);
      assert.match(html, /Cada sesión con error es alguien del equipo/);
      assert.doesNotMatch(html, /Humor de guardia/);
      assert.doesNotMatch(html, /conic-gradient/);
    },
  },
  ...["nonexistent-view", "__proto__", "constructor", "toString", "hasOwnProperty"].map(view => ({
    title: "unknown view " + view + " falls back to the portada", search: "?view=" + view, urls: PORTADA_URLS, crumb: null,
    check(page) {
      const html = page.repBody.innerHTML;
      assert.equal((html.match(/class="rep-sumcard[ "]/g) || []).length, 11);
      assert.match(html, /9 de 40 sesiones de login chocaron con un error en los últimos 30 días/);
    },
  })),
];

for (const entry of MATRIX) {
  test("deep-link matrix: " + entry.title + " requests exactly its endpoints and renders its view", async t => {
    const page = fixture({ search: entry.search, routes: defaultRoutes(entry.over || {}) }, t);
    await page.run().ready;
    await settle();
    assert.deepEqual(page.requests.map(r => normalizeUrl(r.url)).sort(), entry.urls.slice().sort());
    const mesReqs = page.requests.filter(r => r.url.startsWith(MES_URL)).map(r => r.url.slice(MES_URL.length));
    const validMonths = new Set(mesesCompletos(13));
    mesReqs.forEach(m => {
      assert.match(m, /^\d{4}-(0[1-9]|1[0-2])$/);
      assert.ok(validMonths.has(m), "not a recent complete calendar month: " + m);
    });
    const html = page.repBody.innerHTML;
    // The report's name is plain text when there is no other report to go to,
    // and the summary of the report list otherwise.
    if (entry.crumb) assert.ok(html.includes(CRUMB_A + entry.crumb + "</p>")
      || html.includes(CRUMB_A + '<details class="rep-switch"><summary>' + entry.crumb + "<span"), "breadcrumb missing: " + entry.crumb);
    else assert.doesNotMatch(html, /cnt-crumb/);
    if (entry.periodo) {
      assert.ok(page.repBody.children.has(entry.periodo.id), entry.periodo.id + " missing");
      assert.match(html, new RegExp('value="' + entry.periodo.selected + '" selected'));
    } else if (entry.mesSelect) {
      // Month view: the controls row exists too, but the selection is a
      // calendar month (asserted against the requested mes inside check).
      assert.ok(page.repBody.children.has(entry.mesSelect), entry.mesSelect + " missing");
    } else {
      assert.doesNotMatch(html, /class="rep-controls"/);
    }
    entry.check(page, mesReqs);
  });
}

test("cost-recovery.csv?days matches the initial and the changed Recupero period", async t => {
  const page = fixture({ search: "?view=recupero", routes: recuperoRoutes() }, t);
  await page.run().ready;
  await settle();
  assert.deepEqual(page.requests.map(r => r.url), ["/api/admin/reports/cost-recovery?days=90"]);
  assert.match(page.repBody.innerHTML, /cost-recovery\.csv\?days=90"/);
  changePeriod(page, "30", "rec-periodo");
  await settle();
  assert.deepEqual(page.requests.map(r => r.url),
    ["/api/admin/reports/cost-recovery?days=90", "/api/admin/reports/cost-recovery?days=30"]);
  assert.match(page.repBody.innerHTML, /cost-recovery\.csv\?days=30"/);
  assert.doesNotMatch(page.repBody.innerHTML, /cost-recovery\.csv\?days=90/);
  assert.match(page.repBody.innerHTML, /value="30" selected/);
  // Returning to the initial period repaints from cache: the link matches
  // again without a third request.
  changePeriod(page, "90", "rec-periodo");
  await settle();
  assert.equal(page.requests.length, 2);
  assert.match(page.repBody.innerHTML, /cost-recovery\.csv\?days=90"/);
  assert.doesNotMatch(page.repBody.innerHTML, /cost-recovery\.csv\?days=30"/);
});

test("sumar.csv?days matches the initial and the changed SUMAR+ period", async t => {
  const page = fixture({ search: "?view=sumar", routes: sumarRoutes() }, t);
  await page.run().ready;
  await settle();
  assert.deepEqual(page.requests.map(r => r.url), ["/api/admin/reports/sumar?days=90"]);
  assert.match(page.repBody.innerHTML, /sumar\.csv\?days=90"/);
  changePeriod(page, "7", "sum-periodo");
  await settle();
  assert.deepEqual(page.requests.map(r => r.url), ["/api/admin/reports/sumar?days=90", "/api/admin/reports/sumar?days=7"]);
  assert.match(page.repBody.innerHTML, /sumar\.csv\?days=7"/);
  assert.doesNotMatch(page.repBody.innerHTML, /sumar\.csv\?days=90/);
  assert.match(page.repBody.innerHTML, /value="7" selected/);
  changePeriod(page, "90", "sum-periodo");
  await settle();
  assert.equal(page.requests.length, 2);
  assert.match(page.repBody.innerHTML, /sumar\.csv\?days=90"/);
  assert.doesNotMatch(page.repBody.innerHTML, /sumar\.csv\?days=7"/);
});
