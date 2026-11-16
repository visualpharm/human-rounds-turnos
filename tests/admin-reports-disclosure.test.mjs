// Progressive disclosure of the Reports area (the product owner, 2026-10: "handle
// gracefully when people still don't have data... just don't show the
// items"). The real static/platform/admin-reports.mjs is mounted end to end
// with the same boundary fakes as admin-reports-lifecycle.test.mjs (data: URL
// dependencies, Element stub, fetch routed by URL) plus a sessionStorage
// stub, and the SAME fixture is fed twice: once with data, once without.
// Nothing from the controller is reimplemented here.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { mountReports } from "../static/platform/admin-reports.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 12) { for (let i = 0; i < turns; i += 1) await tick(); }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }
const ok = body => Promise.resolve(response(200, body));
const fail = status => Promise.resolve(response(status));

const CHARTS_SRC = [
  "export function setAncho(w){}",
  "export function scatter(r,o){return '[chart:scatter]'}",
  "export function heatmap(g,o){return '[chart:heatmap]'}",
  "export function treemap(i,o){return '[chart:treemap]'}",
  "export function slope(r,o){return '[chart:slope]'}",
  "export function lollipop(r,o){return '[chart:lollipop]'+(r||[]).map(function(x){return '{'+x.nombre+'='+x.valor+'}'}).join('')}",
  "export function linea(s,o){return '[chart:linea]'}",
  "export function densidad(v,o){return '[chart:densidad]'}",
  "export function piramide(l,o){return '[chart:piramide]'}",
  "export function tile(o){return '[tile:'+(o&&o.etiqueta||'')+(o&&o.pill?'|pill:'+o.pill.texto:'')+']'}",
  "export function hero(f){return (f&&f.length)?'[chart:hero'+f.map(function(x){return x.enlace?'>'+x.enlace.href:''}).join('')+']':''}",
  "export function frase(f){return f.texto+(f.enlace?'[link:'+f.enlace.href+']':'')}",
].join(";");

class Element {
  constructor(id = "") { this.id = id; this.isConnected = true; this._html = ""; this.style = {}; this.listeners = new Map(); this.children = new Map(); this.value = ""; this.clientWidth = 800; }
  get innerHTML() { return this._html; }
  set innerHTML(html) { this._html = String(html); this.children = new Map(); const re = /id="([^"]+)"/g; let m; while ((m = re.exec(this._html))) this.children.set(m[1], new Element(m[1])); }
  querySelector(selector) { return selector[0] === "#" ? (this.children.get(selector.slice(1)) || null) : null; }
  querySelectorAll() { return []; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
}

// In-memory sessionStorage: what the portada learns about which reports
// carry data is a per-viewer convenience, read back by the sub-report pages.
class MemoryStorage {
  constructor() { this.map = new Map(); }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
}

function fixture({ search = "", routes, storage = new MemoryStorage() }, t) {
  const host = new Element("sec-reportes");
  const keys = ["window", "fetch", "location", "sessionStorage"];
  const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.location = { href: "https://example.test/admin/reportes", pathname: "/admin/reportes", search };
  if (storage) globalThis.sessionStorage = storage; else delete globalThis.sessionStorage;
  globalThis.window = {
    SANA_STATIC: {
      "platform/dates.mjs": "data:text/javascript,export const dia=(iso)=>iso",
      "platform/admin-session.mjs": "data:text/javascript," + encodeURIComponent("export const confirmar=()=>Promise.resolve(false)"),
      "platform/admin-charts.mjs": "data:text/javascript," + encodeURIComponent(CHARTS_SRC),
      "platform/search.mjs": "data:text/javascript,export const buscarConPuntaje=(rows,q)=>rows.length?[{row:rows[0],score:1}]:[]",
      "platform/i18n.mjs": new URL("../static/platform/i18n.mjs", import.meta.url).href,
    },
  };
  const requests = [];
  let currentRoutes = routes;
  globalThis.fetch = (url, options = {}) => {
    requests.push(url);
    const next = currentRoutes(url);
    assert.ok(next && typeof next.then === "function", "unrouted Reports fetch: " + url);
    return next;
  };
  const disposes = [];
  t.after(() => {
    disposes.forEach(dispose => dispose());
    for (const [key, descriptor] of Object.entries(previous)) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key];
  });
  const run = () => { const dispose = mountReports({ root: host }); disposes.push(dispose); return dispose; };
  const navigate = (nextSearch, nextRoutes) => { globalThis.location.search = nextSearch; if (nextRoutes) currentRoutes = nextRoutes; };
  return { host, requests, run, storage, navigate, get repBody() { return host.children.get("rep-body"); } };
}

// ---- payloads WITH data (shaped as reports.py / costos_ia.py serve them) ----
const WITH = {
  gestion: {
    dias: 30, hero: [{ texto: "El equipo resolvió 6 solicitudes.", tono: "ok" }],
    kpis: { pendientes: 12, solicitudes_periodo: 120, pct_aprobadas: 80, mediana_horas_resolucion: 10, mediana_dias_turno: 5, mediana_horas_pendiente: 36, max_horas_pendiente: 100, esperas_pendientes_horas: [10, 36, 100] },
    oferta_demanda: { clusters: [{ nombre: "Cardiología", horas_semana: 10, solicitudes: 30, solicitudes_por_hora: 3, sugerencia_horas: 4, sin_oferta: false }], equilibrio: 2 },
    heatmap: { dias: ["lun"], horas: [9], demanda: [[3]], desajuste: [] },
    palancas: { grupos: [{ id: "prevenible_antes", nombre: "Orden ilegible", n: 4 }], hero: "Palancas hero" },
    funnel: {
      holds_creados: 10, solicitudes: 8, aprobadas: 6, rechazadas: 1, canceladas: 1, asistencia: { presente: 3, ausente: 1 },
      pasos: [{ key: "pedido", label: "Pidieron turno", n: 8 }, { key: "aprobada", label: "Les dieron turno", n: 6 }],
      frases: [{ tono: "atencion", texto: "Donde más se pierde: entre «Pidieron turno» y «Les dieron turno» se quedaron 2, el 25%." }],
    },
    lista_espera: { turnos_recuperados: 2 },
    volumen: [{ fecha: "2026-09-01", total: 4, aprobadas: 3 }, { fecha: "2026-09-02", total: 5, aprobadas: 2 }],
    ahorro: { disponible: true, horas_operador: 2.5, ahorro_ars: 12500, roi: 1.2, operaciones: 375, supuestos: ["s1"] },
  },
  doctorsStats: { medicos: { "dra-garcia": { horas_semana: 20, turnos_14d: 30, slots_14d: 40, ocupacion_14d: 75, tiene_foto: true, tiene_login: true } } },
  doctorsContent: { items: [{ id: "dra-garcia", nombre: "Dra. Garcia", especialidad: "Clínica", centro: "caps-central" }] },
  patients: {
    dias: 30, kpis: { total_cuentas: 100, nuevas_periodo: 10, pct_con_email: 80, pct_con_domicilio: 50 },
    cobertura: { pct_con_obra_social: 60, pct_con_prepaga: 10, top_obras_sociales: [{ nombre: "IOMA", cantidad: 20 }] },
    dependientes: { total: 5, adultos_con_dependientes: 2 },
    actividad: { pacientes_con_solicitud: 25, promedio_por_paciente: 1.5 },
    volumen: [{ fecha: "2026-09-01", total: 2 }], edades: [{ rango: "30-39", cantidad: 8, mujeres: 5, varones: 3 }],
    actividad_mensual: [],
  },
  searches: { top: [{ q: "cardio", veces: 5 }] },
  services: { servicios: [{ id: "cardio", nombre: "Cardiología", centro: "caps-central", cluster: "clinica" }] },
  doctorsApi: { medicos: [] },
  centers: { centros: [{ slug: "caps-central", name: "CAPS Central" }] },
  reviews: { centers: { "caps-central": { name: "CAPS Central", count: 12, hero: ["Destacan el trato."], themes: {}, named_staff: [], triage: { fix: [], decline: [] } } } },
  recupero: { dias: 90, hero: [], kpis: { consultas_con_cobertura: 20, pacientes_con_cobertura: 15, presentes: 18, ausentes: 2, pct_del_total: 40 }, por_obra_social: [{ nombre: "IOMA", consultas: 12, pacientes: 9 }], tendencia: [] },
  sumar: { dias: 90, hero: [], kpis: { prestaciones: 10, pacientes: 8, pct_del_total: 20 }, trazadoras: [{ label: "Embarazo", consultas: 4 }], tendencia: [] },
  statistics: mes => ({ mes, hero: [], kpis: { consultas_totales: 50, presentes: 40, ausentes: 5, sin_marca: 5, pct_asistencia: 88 }, por_especialidad: [{ label: "Clínica", consultas: 50, presentes: 40, ausentes: 5, sin_marca: 5, horas_ofrecidas: 20, consultas_por_hora: 2.5 }], edades: [], por_centro: [] }),
  capacity: { hero: [], ocupacion: [{ label: "Clínica", pct: 80, ocupados_14d: 8, capacidad_14d: 10 }], demanda_semanal: [], sin_oferta: [], temporada: { disponible: false } },
  costs: {
    dias: 30,
    resumen: { total_usd: 1.5, run_rate_mensual_usd: 1.5, costo_prom_op: 0.004, operaciones: 375, tasa_reconocimiento: 90, no_reconocidas: 37, ips: 100, costo_mediano_por_ip: 0.01, por_1000_pacientes_usd: 5, dias_completos: 5 },
    por_operacion: [{ label: "dni", cantidad: 300, costo_usd: 1.2, costo_prom: 0.004 }],
    diario: [], sospechosos: [], abuso_activo: false, top_ips: [],
  },
  login: { dias: 30, sesiones: 40, logins: 33, errores: 12, sesiones_con_error: 9, ms_mediana_login: 4200 },
  recorrido: {
    dias: 30, filtros: {}, opciones: { centros: [], servicios: [], medicos: [] },
    pasos_config: [{ key: "pedido", on: true, label: "Pidieron turno" }, { key: "presente", on: true, label: "Fueron al turno" }],
    pasos: [
      { key: "pedido", label: "Pidieron turno", n: 10, pct: 100, perdidos: 0, en_curso: 0, motivos: [] },
      { key: "presente", label: "Fueron al turno", n: 4, pct: 40, perdidos: 6, en_curso: 0, motivos: [{ key: "ausente", n: 6, texto: "6 no fueron", en_curso: false }] },
    ],
    mayor_perdida: { desde: "pedido", hasta: "presente", n: 6, pct: 60, motivo: "ausente" },
    hero: [{ tono: "ok", texto: "4 de 10 llegaron hasta «Fueron al turno»." }],
    sin_datos: [{ key: "visitas", label: "Entrar al sitio" }],
  },
  visitas: { dias: 30, visitas: 40, embudo: [{ paso: "visit", n: 40 }, { paso: "done", n: 4 }], serie: [] },
};

// ---- the same endpoints on a fresh installation: zeros and empty lists ----
const WITHOUT = {
  gestion: {
    dias: 30, hero: [],
    kpis: { pendientes: 0, solicitudes_periodo: 0, pct_aprobadas: null, mediana_horas_resolucion: null, mediana_dias_turno: null, mediana_horas_pendiente: null, max_horas_pendiente: null, esperas_pendientes_horas: [] },
    oferta_demanda: { clusters: [], equilibrio: null },
    heatmap: { dias: [], horas: [], demanda: [], desajuste: [] },
    palancas: { grupos: [], hero: "" },
    funnel: { holds_creados: 0, solicitudes: 0, aprobadas: 0, rechazadas: 0, canceladas: 0, asistencia: { presente: 0, ausente: 0 } },
    lista_espera: { turnos_recuperados: 0 },
    volumen: [],
    ahorro: { disponible: false },
  },
  doctorsStats: { medicos: {} },
  doctorsContent: { items: [] },
  patients: {
    dias: 30, kpis: { total_cuentas: 0, nuevas_periodo: 0, pct_con_email: null, pct_con_domicilio: null },
    cobertura: { pct_con_obra_social: null, pct_con_prepaga: null, top_obras_sociales: [] },
    dependientes: { total: 0, adultos_con_dependientes: 0 },
    actividad: { pacientes_con_solicitud: 0, promedio_por_paciente: null },
    volumen: [], edades: [], actividad_mensual: [],
  },
  searches: { top: [] },
  services: { servicios: [] },
  doctorsApi: { medicos: [] },
  centers: { centros: [] },
  reviews: { centers: {} },
  recupero: { dias: 90, hero: [], kpis: { consultas_con_cobertura: 0, pacientes_con_cobertura: 0, presentes: 0, ausentes: 0, pct_del_total: null }, por_obra_social: [], tendencia: [] },
  sumar: { dias: 90, hero: [], kpis: { prestaciones: 0, pacientes: 0, pct_del_total: null }, trazadoras: [], tendencia: [] },
  statistics: mes => ({ mes, hero: [], kpis: { consultas_totales: 0, presentes: 0, ausentes: 0, sin_marca: 0, pct_asistencia: null }, por_especialidad: [], edades: [], por_centro: [] }),
  capacity: { hero: [], ocupacion: [], demanda_semanal: [], sin_oferta: [], temporada: { disponible: false } },
  costs: { dias: 30, resumen: { total_usd: 0, run_rate_mensual_usd: 0, costo_prom_op: null, operaciones: 0, tasa_reconocimiento: null, no_reconocidas: 0, ips: 0, costo_mediano_por_ip: null, por_1000_pacientes_usd: null, dias_completos: 0 }, por_operacion: [], diario: [], sospechosos: [], abuso_activo: true, top_ips: [] },
  login: { dias: 30, tabs: [] },
  recorrido: {
    dias: 30, filtros: {}, opciones: { centros: [], servicios: [], medicos: [] },
    pasos_config: [{ key: "pedido", on: true, label: "Pidieron turno" }],
    pasos: [{ key: "pedido", label: "Pidieron turno", n: 0, pct: null, perdidos: 0, en_curso: 0, motivos: [] }],
    mayor_perdida: null, hero: [], sin_datos: [{ key: "visitas", label: "Entrar al sitio" }],
  },
  visitas: { dias: 30, visitas: 0, embudo: [], serie: [] },
};

function routesFor(P, over = {}) {
  const table = {
    "/api/admin/reports?days=30": () => ok(P.gestion),
    "/api/admin/doctors/stats": () => ok(P.doctorsStats),
    "/api/admin/content/doctors": () => ok(P.doctorsContent),
    "/api/admin/reports/patients?days=30": () => ok(P.patients),
    "/api/admin/reports/searches?days=30": () => ok(P.searches),
    "/api/services": () => ok(P.services),
    "/api/doctors": () => ok(P.doctorsApi),
    "/api/centers": () => ok(P.centers),
    "/api/admin/reports/reviews": () => ok(P.reviews),
    "/api/admin/reports/cost-recovery?days=90": () => ok(P.recupero),
    "/api/admin/reports/sumar?days=90": () => ok(P.sumar),
    "/api/admin/reports/capacity": () => ok(P.capacity),
    "/api/admin/costs?days=30": () => ok(P.costs),
    "/api/admin/reports/login-panel?days=30": () => ok(P.login),
    "/api/admin/reports/funnel?days=30": () => ok(P.recorrido),
    "/api/admin/reports/visits?days=30": () => ok(P.visitas),
  };
  return url => {
    if (Object.prototype.hasOwnProperty.call(over, url)) return over[url]();
    if (url.startsWith("/api/admin/reports/statistics?mes=")) return ok(P.statistics(url.slice(url.indexOf("?mes=") + 5)));
    const hit = table[url];
    assert.ok(hit, "unexpected Reports fetch: " + url);
    return hit();
  };
}

const ALL_KEYS = ["gestion", "recorrido", "medicos", "pacientes", "busquedas", "centros", "recupero", "sumar", "estadistica", "capacidad", "ia", "login", "visitas"];
const count = (html, re) => (html.match(re) || []).length;
const stored = page => JSON.parse(page.storage.getItem("hr.reportes.disponibles"));

test("portada with data: every one of the thirteen cards renders and no placeholder line", async t => {
  const page = fixture({ routes: routesFor(WITH) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.equal(count(html, /class="rep-sumcard[ "]/g), 13);
  for (const key of ALL_KEYS) assert.match(html, new RegExp('href="/admin/reportes\\?view=' + key + '"'), key + " card missing");
  assert.equal(count(html, /rep-empty/g), 0);
  assert.doesNotMatch(html, /Sin datos|s\/d/);
  assert.deepEqual(stored(page), Object.fromEntries(ALL_KEYS.map(k => [k, true])));
  // The waiting card says what its figure is and names the oldest request only
  // when it waits well beyond the median; the AI card has no projection line.
  assert.match(html, /o más sin resolver\. La más antigua lleva 4,2 días\.<\/div>/);
  assert.doesNotMatch(html, /Proyección de 30 días/);
  // The login ring does not repeat the percentage the card already shows.
  assert.doesNotMatch(html, /class="rep-donut"[^>]*><b>/);
});

test("portada without data: no card, no grid, exactly the single quiet sentence", async t => {
  // Recupero fails outright: a dead endpoint is also "no data", never a card.
  const page = fixture({ routes: routesFor(WITHOUT, { "/api/admin/reports/cost-recovery?days=90": () => fail(500) }) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.equal(count(html, /rep-sumcard/g), 0);
  assert.doesNotMatch(html, /rep-sumgrid/);
  assert.equal(html, '<p class="rep-empty">Los informes aparecen a medida que se cargan turnos y solicitudes.</p>');
  assert.deepEqual(stored(page), Object.fromEntries(ALL_KEYS.map(k => [k, false])));
});

test("portada with partial data keeps only the data-bearing cards", async t => {
  const page = fixture({ routes: routesFor(WITHOUT, {
    "/api/admin/reports?days=30": () => ok(WITH.gestion),
    "/api/admin/reports/login-panel?days=30": () => ok(WITH.login),
  }) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.equal(count(html, /class="rep-sumcard[ "]/g), 2);
  assert.match(html, /reportes\?view=gestion"/);
  assert.match(html, /reportes\?view=login"/);
  assert.equal(count(html, /rep-empty/g), 0);
  const map = stored(page);
  assert.equal(map.gestion, true);
  assert.equal(map.login, true);
  assert.equal(map.recupero, false);
});

// A zero is not data: agendas with no turno («0,0%» load), specialties with
// 0 % occupancy and a login panel nobody tried to log in to
// keep their cards off the portada, like an empty report.
const ZEROS = {
  doctorsStats: { medicos: { "dra-garcia": { horas_semana: 20, turnos_14d: 0, slots_14d: 40, ocupacion_14d: 0, tiene_foto: true, tiene_login: true } } },
  doctorsContent: WITH.doctorsContent,
  capacity: { hero: [], ocupacion: [{ label: "Clínica", pct: 0, ocupados_14d: 0, capacidad_14d: 10 }], demanda_semanal: [{ semana: "2026-09-01", solicitudes: 0, busquedas: 4 }], sin_oferta: [], temporada: { disponible: false } },
  login: { dias: 30, sesiones: 0, logins: 0, errores: 0, sesiones_con_error: 0, ms_mediana_login: null },
};

test("portada: a report whose headline number is zero is not shown", async t => {
  const page = fixture({ routes: routesFor(Object.assign({}, WITHOUT, ZEROS)) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.equal(count(html, /rep-sumcard/g), 0);
  assert.doesNotMatch(html, /0,0%|0\.0%/);
  assert.equal(html, '<p class="rep-empty">Los informes aparecen a medida que se cargan turnos y solicitudes.</p>');
  const map = stored(page);
  assert.equal(map.medicos, false);
  assert.equal(map.capacidad, false);
  assert.equal(map.login, false);
});

test("portada: one turno makes Médicos and Capacidad data, one login session makes Panel de login data", async t => {
  const page = fixture({ routes: routesFor(Object.assign({}, WITHOUT, ZEROS, {
    doctorsStats: { medicos: { "dra-garcia": Object.assign({}, ZEROS.doctorsStats.medicos["dra-garcia"], { turnos_14d: 1 }) } },
    capacity: Object.assign({}, ZEROS.capacity, { ocupacion: [{ label: "Clínica", pct: 10, ocupados_14d: 1, capacidad_14d: 10 }] }),
    login: { dias: 30, sesiones: 1, logins: 1, errores: 1, sesiones_con_error: 1, ms_mediana_login: null },
  })) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.equal(count(html, /class="rep-sumcard[ "]/g), 3);
  for (const key of ["medicos", "capacidad", "login"]) assert.match(html, new RegExp('reportes\\?view=' + key + '"'), key);
  assert.match(html, /\b10%/);
});

// «Más ocupada» needs somebody busy and one that stands out: nothing is named
// when every specialty sits at 0 %, and a tie is reported as a tie instead of
// crowning whichever specialty the list happens to start with.
const capacityWith = (ocupacion, extra = {}) => Object.assign({}, WITHOUT, {
  capacity: Object.assign({ hero: [], ocupacion, demanda_semanal: [], sin_oferta: [], temporada: { disponible: false } }, extra),
});
const especialidad = (label, pct) => ({ label, pct, ocupados_14d: pct / 5, capacidad_14d: 20 });
// The page's headline as the server writes it: its first item carries the rule
// (_capacity_rule) the card follows.
const heroRegla = (regla, texto = "") => [{ texto, tono: "ok", regla }];
const SIN_HORAS = heroRegla("sin_horas");
const accionesDe = html => (html.match(/<p class="rep-action">.*?<\/p>/g) || []).map(p => p.replace(/<[^>]+>/g, ""));
const tarjetaCapacidad = html => html.match(/<a class="rep-sumcard[^>]*reportes\?view=capacidad".*?<\/a>/)[0];

// The card and the page say one thing on the same data: the server picks the
// conclusion once and the card follows it (15.11: the card named «1 — Vacunas
// y prevención» while the page said the capacity covers the demand).
test("capacidad: the card follows the page's rule — a request below the 2 % share is not the card's finding", async t => {
  const sin_oferta = [{ label: "Vacunas y prevención", solicitudes: 1 }];
  const ok = capacityWith([especialidad("Clínica", 40)], {
    hero: heroRegla("ok", "La capacidad ofrecida cubre la demanda de los próximos 14 días en todas las especialidades con datos."), sin_oferta });
  const portada = fixture({ routes: routesFor(ok) }, t);
  await portada.run().ready;
  await settle();
  const card = tarjetaCapacidad(portada.repBody.innerHTML);
  assert.doesNotMatch(card, /Vacunas/);
  assert.match(card, /La capacidad ofrecida cubre la demanda de los próximos 14 días en todas las especialidades con datos\. Clínica es la especialidad más ocupada/);
  // Nothing busy yet and no recent requests: the card still says what the page says.
  const vacia = capacityWith([especialidad("Clínica", 0)], { hero: heroRegla("ok"), sin_oferta });
  const p0 = fixture({ routes: routesFor(vacia) }, t);
  await p0.run().ready;
  await settle();
  assert.match(tarjetaCapacidad(p0.repBody.innerHTML), /cubre la demanda de los próximos 14 días en todas las especialidades con datos/);
  // Same data, the rule says the specialty without hours leads: both name it.
  const critico = capacityWith([especialidad("Clínica", 40)], { hero: heroRegla("sin_horas", "Vacunas y prevención concentró 300 solicitudes"), sin_oferta });
  const p2 = fixture({ routes: routesFor(critico) }, t);
  await p2.run().ready;
  await settle();
  assert.match(tarjetaCapacidad(p2.repBody.innerHTML), /Vacunas y prevención tiene demanda reciente y ninguna hora de agenda/);
  // Nearly full: the card carries the page's advice.
  const atencion = capacityWith([especialidad("Clínica", 95)], { hero: heroRegla("atencion"), sin_oferta });
  const p3 = fixture({ routes: routesFor(atencion) }, t);
  await p3.run().ready;
  await settle();
  const c3 = tarjetaCapacidad(p3.repBody.innerHTML);
  assert.match(c3, /Clínica es la especialidad más ocupada de los próximos 14 días\. Sumar horas ahí es la inversión de mayor impacto\./);
  assert.doesNotMatch(c3, /Vacunas|cubre la demanda/);
  // Nothing measurable: the card does not crown a specialty the page does not.
  const sinMedida = capacityWith([], { hero: heroRegla("sin_medida"), demanda_semanal: [{ semana: "2026-09-01", solicitudes: 5, busquedas: 0 }] });
  const p4 = fixture({ routes: routesFor(sinMedida) }, t);
  await p4.run().ready;
  await settle();
  assert.match(tarjetaCapacidad(p4.repBody.innerHTML), /Todavía no hay capacidad medible/);
});

test("capacidad: every rule the server can name is one the card follows", () => {
  const client = readFileSync(new URL("../static/platform/admin-reports.mjs", import.meta.url), "utf8");
  const server = readFileSync(new URL("../app/admin_panel/reports.py", import.meta.url), "utf8");
  const regla = server.slice(server.indexOf("def _capacity_rule"));
  const reglas = new Set((regla.slice(0, regla.indexOf("\ndef ", 1)).match(/return "([a-z_]+)"/g) || []).map(m => m.slice(8, -1)));
  assert.deepEqual([...reglas].sort(), ["atencion", "ok", "sin_horas", "sin_medida"]);
  const tile = client.slice(client.indexOf("function sumTileCapacidad"));
  for (const r of reglas) assert.ok(tile.slice(0, tile.indexOf("\n  }\n")).includes('regla === "' + r + '"'), "the card ignores rule " + r);
});

test("capacidad: a tie at the top is not a «most busy» specialty (portada and page)", async t => {
  const empate = capacityWith([especialidad("Cardiología", 25), especialidad("Clínica", 25), especialidad("Pediatría", 10)]);
  const portada = fixture({ routes: routesFor(empate) }, t);
  await portada.run().ready;
  await settle();
  const card = portada.repBody.innerHTML.match(/<a class="rep-sumcard[^>]*reportes\?view=capacidad".*?<\/a>/)[0];
  assert.doesNotMatch(card, /es la especialidad más ocupada/);
  // The tie names who is tied; the figure is the card's big number, not repeated in the sentence.
  assert.match(card, /Cardiología y Clínica empatan como las más ocupadas de los próximos 14 días\./);
  assert.doesNotMatch(card, /especialidades empatan|todas al/);
  const pagina = fixture({ search: "?view=capacidad", routes: routesFor(empate) }, t);
  await pagina.run().ready;
  await settle();
  // At 25 % nobody needs more hours: no recommendation under the chart.
  assert.deepEqual(accionesDe(pagina.repBody.innerHTML), []);
});

test("capacidad: a tie of more than three names three and counts the rest", async t => {
  const empate = capacityWith(["Cardiología", "Clínica", "Pediatría", "Dermatología", "Nutrición"].map(n => especialidad(n, 95)), { hero: heroRegla("atencion") });
  const portada = fixture({ routes: routesFor(empate) }, t);
  await portada.run().ready;
  await settle();
  assert.match(portada.repBody.innerHTML, /Cardiología, Clínica, Pediatría y 2 más empatan como las más ocupadas/);
  const pagina = fixture({ search: "?view=capacidad", routes: routesFor(empate) }, t);
  await pagina.run().ready;
  await settle();
  assert.deepEqual(accionesDe(pagina.repBody.innerHTML), ["Cardiología, Clínica, Pediatría y 2 más están al 95% de su capacidad de los próximos 14 días: sumar horas ahí es la inversión de mayor impacto."]);
});

test("capacidad: a single leader is named; «sumar horas» only once its agenda is nearly full (the hero's 90 %)", async t => {
  const lider = capacityWith([especialidad("Cardiología", 25), especialidad("Clínica", 10), especialidad("Pediatría", 0)]);
  const portada = fixture({ routes: routesFor(lider) }, t);
  await portada.run().ready;
  await settle();
  assert.match(portada.repBody.innerHTML, /Cardiología es la especialidad más ocupada de los próximos 14 días\./);
  const pagina = fixture({ search: "?view=capacidad", routes: routesFor(lider) }, t);
  await pagina.run().ready;
  await settle();
  assert.deepEqual(accionesDe(pagina.repBody.innerHTML), [], "at 25 % the chart says it; no advice to add hours");
  const llena = capacityWith([especialidad("Cardiología", 95), especialidad("Clínica", 10)], { hero: heroRegla("atencion") });
  const paginaLlena = fixture({ search: "?view=capacidad", routes: routesFor(llena) }, t);
  await paginaLlena.run().ready;
  await settle();
  assert.deepEqual(accionesDe(paginaLlena.repBody.innerHTML), ["Cardiología está al 95% de su capacidad de los próximos 14 días (19 de 20 turnos): sumar horas ahí es la inversión de mayor impacto."]);
});

test("capacidad card: specialties asked for with no agenda come first, as in the page's hero", async t => {
  const data = capacityWith([especialidad("Clínica", 2.2)], { hero: SIN_HORAS, sin_oferta: [{ label: "Cardiología", solicitudes: 3 }] });
  const portada = fixture({ routes: routesFor(data) }, t);
  await portada.run().ready;
  await settle();
  const card = portada.repBody.innerHTML.match(/<a class="rep-sumcard[^>]*reportes\?view=capacidad".*?<\/a>/)[0];
  // One specialty is named, never counted as «1 especialidades».
  assert.match(card, /Cardiología tiene demanda reciente y ninguna hora de agenda\./);
  assert.doesNotMatch(card, /1 especialidades/);
  assert.doesNotMatch(card, /Clínica es la especialidad más ocupada/);
  const dos = capacityWith([especialidad("Clínica", 2.2)], { hero: SIN_HORAS, sin_oferta: [{ label: "Pediatría", solicitudes: 1 }, { label: "Cardiología", solicitudes: 3 }] });
  const portadaDos = fixture({ routes: routesFor(dos) }, t);
  await portadaDos.run().ready;
  await settle();
  assert.match(portadaDos.repBody.innerHTML, /2 especialidades con demanda reciente no tienen ninguna hora de agenda; la más pedida es Cardiología\./);
});

test("capacidad: the client keeps no «nearly full» threshold of its own; the page's advice follows the hero's rule", async t => {
  const client = readFileSync(new URL("../static/platform/admin-reports.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(client, /CAPACIDAD_CASI_LLENA|pct >= 90|pct < 90/);
  // The rule names a specialty without hours: no competing «sumar horas» at 95 %.
  const sinHoras = capacityWith([especialidad("Clínica", 95)], { hero: SIN_HORAS, sin_oferta: [{ label: "Cardiología", solicitudes: 300 }] });
  const pagina = fixture({ search: "?view=capacidad", routes: routesFor(sinHoras) }, t);
  await pagina.run().ready;
  await settle();
  assert.deepEqual(accionesDe(pagina.repBody.innerHTML).filter(a => /sumar horas/i.test(a)), []);
});

test("capacidad page: with every specialty at 0 % no specialty is singled out for «more hours»", async t => {
  const ceros = capacityWith([especialidad("Cardiología", 0), especialidad("Clínica", 0)], { demanda_semanal: [{ semana: "2026-09-01", solicitudes: 2, busquedas: 4 }] });
  const pagina = fixture({ search: "?view=capacidad", routes: routesFor(ceros) }, t);
  await pagina.run().ready;
  await settle();
  const html = pagina.repBody.innerHTML;
  assert.deepEqual(accionesDe(html), []);
  assert.doesNotMatch(html, /0,0%|0\.0%|inversión de mayor impacto/);
});

test("the «no data» line is body text, not a muted gray", () => {
  const css = readFileSync(new URL("../static/root/admin-direct.css", import.meta.url), "utf8");
  const rule = css.match(/\.rep-empty\{[^}]*\}/);
  assert.ok(rule, ".rep-empty rule missing");
  assert.match(rule[0], /color:var\(--text\);/);
});

test("a sub-report without data shows breadcrumb, period selector and ONE quiet line, no empty sections", async t => {
  const page = fixture({ search: "?view=gestion", routes: routesFor(WITHOUT) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.match(html, /<a href="\/admin\/reportes">Reportes<\/a> \/ <details class="rep-switch"><summary>Gestión</);
  assert.ok(page.repBody.children.has("rep-periodo"), "period select must survive: another period may hold data");
  assert.equal(count(html, /rep-empty/g), 1);
  assert.match(html, /Todavía no hay datos para este informe\./);
  assert.equal(count(html, /rep-section/g), 0);
  assert.equal(count(html, /rep-h2/g), 0);
  assert.doesNotMatch(html, /requests\.csv/);
  assert.equal(stored(page).gestion, false);
});

test("a sub-report with data omits only the sections that would be empty", async t => {
  const page = fixture({ search: "?view=gestion", routes: routesFor(Object.assign({}, WITH, {
    gestion: Object.assign({}, WITH.gestion, { volumen: [], heatmap: { dias: [], horas: [], demanda: [], desajuste: [] }, palancas: { grupos: [], hero: "" } }),
  })) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.match(html, /\[chart:hero\]/);
  assert.match(html, /Dónde rinde más una hora médica/);
  assert.match(html, /Del clic al consultorio/);
  assert.doesNotMatch(html, /Solicitudes por día/);
  assert.doesNotMatch(html, /Cuándo llega la gente/);
  assert.doesNotMatch(html, /Rechazos que se pueden evitar/);
  assert.equal(count(html, /rep-empty/g), 0);
  assert.equal(stored(page).gestion, true);
});

// A figure that is fine stands alone: a tile carries a pill only when the
// figure asks for a look, and no control of a report has a caption beside it.
test("gestión: a tile gets a pill only when its figure asks for a look; the period select has no caption", async t => {
  const bien = await rendered(t, "?view=gestion");
  assert.doesNotMatch(bien, /\|pill:/);
  assert.doesNotMatch(bien, /<label/);
  assert.match(bien, /<select id="rep-periodo" class="adm-sel" aria-label="Período">/);
  const mal = await rendered(t, "?view=gestion", { "/api/admin/reports?days=30": () => ok({ ...WITH.gestion,
    kpis: { ...WITH.gestion.kpis, pct_aprobadas: 40, mediana_horas_resolucion: 30 } }) });
  assert.match(mal, /\|pill:Revisar los cuellos de botella\]/);
  assert.match(mal, /\[tile:Horas hasta resolver \(mediana\)\|pill:Más de un día\]/);
  assert.match(mal, /\[tile:Días del pedido al turno \(mediana\)\]/);
});

// Recorrido: the three filters are named by their empty choice and, on a
// phone, wait behind one funnel button next to the period.
test("recorrido: filters without captions, behind the funnel button, open once one is set", async t => {
  const conOpciones = { ...WITH.recorrido, opciones: {
    centros: [{ id: "h", nombre: "Hospital" }, { id: "c", nombre: "CAPS" }], servicios: [], medicos: [] } };
  const html = await rendered(t, "?view=recorrido", { "/api/admin/reports/funnel?days=30": () => ok(conOpciones) });
  assert.match(html, /<div class="rep-controls rep-controls--rec">/);
  assert.match(html, /id="rep-rec-filtrar" aria-label="Filtrar" title="Filtrar" aria-expanded="false"/);
  assert.match(html, /<span class="rep-rec-filtros"><span class="rep-ctl"><select id="rep-rec-centro"[^>]*aria-label="Centro"><option value="">Centro<\/option>/);
  assert.doesNotMatch(html, /<label for="rep-|>Todos</);
  // Without option lists there is nothing to open: no funnel button.
  assert.doesNotMatch(await rendered(t, "?view=recorrido"), /rep-rec-filtrar/);
  const css = readFileSync(new URL("../static/root/admin-direct.css", import.meta.url), "utf8");
  // The panel's own rule shows every .cnt-gear-btn inside .adm5-main; the
  // funnel has to outweigh it to stay off the desktop.
  assert.match(css, /\.adm5-main \.rep-controls \.rep-rec-filtrar\{display:none;\}/);
  assert.match(css, /\.adm5-main \.rep-controls--rec \.rep-rec-filtrar\{display:inline-flex;\}/);
  assert.match(css, /\.rep-controls--abiertos \.rep-rec-filtros\{display:flex;\}/);
});

// Visitas: what the site records as codes is read as words, the searches are
// counted (the server names their count `veces`, the other rankings `n`), a
// section with nothing to say is not drawn, and the API keys open on demand.
test("visitas: rankings in words, searches counted, no empty section, API keys behind one line", async t => {
  const visitas = {
    dias: 30, visitas: 40, vistas: 90, serie: [],
    embudo: [{ paso: "visit", n: 40 }, { paso: "done", n: 4 }],
    busquedas: [{ q: "pediatra", veces: 7, con_resultados: 7 }],
    sin_resultados: [],
    paginas: [{ clave: "/doctor/:id|ana-perez", n: 5 }, { clave: "/service/:id|rayos-x", n: 3 }, { clave: "/study/:id|eco", n: 1 }, { clave: "/d/:id|diagnostico", n: 1 }],
    origenes: [{ clave: "direct", n: 9 }, { clave: "internal", n: 4 }, { clave: "www.google.com", n: 2 }, { clave: "google.com", n: 3 }],
    dispositivos: [{ clave: "mobile", n: 30 }, { clave: "desktop", n: 10 }],
    idiomas: [{ clave: "es", n: 35 }, { clave: "en", n: 5 }],
    agenda: { snapshot: "2026-11-15", dias: [{ day: "2026-11-16", web: 4, taken: 1, free: 3 }] },
  };
  const page = fixture({ search: "?view=visitas", routes: routesFor(WITH, {
    "/api/admin/reports/visits?days=30": () => ok(visitas),
    "/api/admin/analytics/keys": () => ok({ keys: [] }),
  }) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.match(html, /\{pediatra=7\}/);
  assert.match(html, /\{Médico: Ana perez=5\}\{Servicio: Rayos x=3\}\{Estudio: Eco=1\}\{Sección: Diagnostico=1\}/);
  // Two hosts that read as one name are one row, and the rows stay in order.
  assert.match(html, /\{Entrada directa=9\}\{google\.com=5\}\{Otra página del sitio=4\}/);
  assert.match(html, /\{Teléfono=30\}\{Computadora=10\}/);
  assert.match(html, /\{Español=35\}\{Inglés=5\}/);
  assert.doesNotMatch(html, /=undefined\}|\{direct=|\{mobile=|\{es=/);
  // Every coming day has free hours: nothing to report about the agenda.
  assert.doesNotMatch(html, /no tienen ningún horario libre/);
  assert.match(html, /<details class="rep-more rep-section"><summary>Acceso por API<\/summary>/);
});

test("recorrido with data: hero, steps chart, the loss as a phrase, the unmeasured steps named, the step setting", async t => {
  const page = fixture({ search: "?view=recorrido", routes: routesFor(WITH) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.match(html, /Reportes<\/a> \/ <details class="rep-switch"><summary>Recorrido del paciente</);
  assert.match(html, /\[chart:hero\]/);
  assert.match(html, /\[chart:lollipop\]/);
  assert.match(html, /Hasta dónde llegaron/);
  assert.match(html, /«Fueron al turno»: 4 de 10\.<\/b> 6 no fueron\./);
  assert.match(html, /No se mide, porque el sistema no lo registra: Entrar al sitio\./);
  // No option lists → no filter selects; the period select and the step gear stay.
  assert.ok(page.repBody.children.has("rep-periodo"));
  assert.ok(!page.repBody.children.has("rep-rec-centro"));
  assert.ok(page.repBody.children.has("rep-rec-gear"));
  assert.equal(count(html, /class="rep-rec-step"/g), 2);
  assert.equal(count(html, /data-rec-move=/g), 4);
  assert.equal(stored(page).recorrido, true);
  assert.ok(page.requests.includes("/api/admin/reports/funnel?days=30"));
});

// Unmarked attendance is the desk's to fix: the phrase names «Pacientes de hoy»
// and links to it for whoever has that screen in the menu. The reports-only
// role does not, and reads the same sentence with no link to a screen that
// would refuse it.
const MARCAR = {
  tono: "info", motivo: "sin_marcar", texto: "La asistencia se marca en Pacientes de hoy: hasta entonces no se sabe si fueron.",
  enlace: { href: "/admin/cola", texto: "Pacientes de hoy" },
};
const SIN_MARCAR = {
  gestion: { ...WITH.gestion, funnel: { ...WITH.gestion.funnel, frases: WITH.gestion.funnel.frases.concat([MARCAR]) } },
  recorrido: { ...WITH.recorrido, acciones: { presente: MARCAR } },
  recorridoHero: { ...WITH.recorrido, hero: WITH.recorrido.hero.concat([MARCAR]) },
};

function menu(t, hrefs, hidden = []) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  globalThis.document = {
    querySelectorAll: selector => selector === "#adm-nav a"
      ? hrefs.map(href => ({ getAttribute: () => href, style: { display: hidden.includes(href) ? "none" : "" } }))
      : [],
  };
  t.after(() => { previous ? Object.defineProperty(globalThis, "document", previous) : delete globalThis.document; });
}

async function rendered(t, search, over) {
  const page = fixture({ search, routes: routesFor(WITH, over) }, t);
  await page.run().ready;
  await settle();
  return page.repBody.innerHTML;
}

test("unmarked attendance: the desk gets the link to «Pacientes de hoy» in Gestión and in Recorrido", async t => {
  menu(t, ["/admin/solicitudes", "/admin/cola", "/admin/reportes"]);
  const gestion = await rendered(t, "?view=gestion", { "/api/admin/reports?days=30": () => ok(SIN_MARCAR.gestion) });
  assert.match(gestion, /<p class="rep-action">La asistencia se marca en Pacientes de hoy: [^<]*\[link:\/admin\/cola\]<\/p>/);
  const paso = await rendered(t, "?view=recorrido", { "/api/admin/reports/funnel?days=30": () => ok(SIN_MARCAR.recorrido) });
  assert.match(paso, /«Fueron al turno»: 4 de 10\.<\/b> 6 no fueron\. La asistencia se marca en Pacientes de hoy: [^<]*\[link:\/admin\/cola\]<\/li>/);
  const hero = await rendered(t, "?view=recorrido", { "/api/admin/reports/funnel?days=30": () => ok(SIN_MARCAR.recorridoHero) });
  assert.match(hero, /\[chart:hero>\/admin\/cola\]/);
});

test("unmarked attendance: without that screen in the menu the sentence stays and the link does not", async t => {
  for (const sinPantalla of [
    () => menu(t, ["/admin/reportes"]),
    () => menu(t, ["/admin/cola", "/admin/reportes"], ["/admin/cola"]),
    () => {},
  ]) {
    sinPantalla();
    const gestion = await rendered(t, "?view=gestion", { "/api/admin/reports?days=30": () => ok(SIN_MARCAR.gestion) });
    assert.match(gestion, /<p class="rep-action">La asistencia se marca en Pacientes de hoy: hasta entonces no se sabe si fueron\.<\/p>/);
    const paso = await rendered(t, "?view=recorrido", { "/api/admin/reports/funnel?days=30": () => ok(SIN_MARCAR.recorrido) });
    assert.match(paso, /6 no fueron\. La asistencia se marca en Pacientes de hoy: hasta entonces no se sabe si fueron\.<\/li>/);
    const hero = await rendered(t, "?view=recorrido", { "/api/admin/reports/funnel?days=30": () => ok(SIN_MARCAR.recorridoHero) });
    assert.match(hero, /\[chart:hero\]/);
  }
});

test("recorrido: the unmeasured steps are named inside the step chooser, not under the report", async t => {
  const page = fixture({ search: "?view=recorrido", routes: routesFor(WITH) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  const pop = html.slice(html.indexOf('id="rep-rec-pop"'));
  assert.match(pop.slice(0, pop.indexOf("</div></div>")), /No se mide, porque el sistema no lo registra: Entrar al sitio\./);
});

// The device table compares visitors; an internal address is the installation's
// own proxy and one address compares nothing (critical review 08.11, E8).
const ip = (addr, n) => ({ ip: addr, operaciones: n, no_reconocidas: 0, costo_usd: n / 100, sospechoso: false });
for (const [name, list, shown] of [
  ["one internal proxy address", [ip("10.0.1.6", 375)], false],
  ["only private and loopback addresses", [ip("192.168.1.4", 3), ip("172.20.0.2", 2), ip("127.0.0.1", 1)], false],
  ["a single visitor address", [ip("181.10.2.3", 5), ip("10.0.1.6", 2)], false],
  ["two visitor addresses", [ip("181.10.2.3", 5), ip("200.45.1.9", 2), ip("10.0.1.6", 1)], true],
]) {
  test(`ia: the spend-by-device table with ${name} is ${shown ? "shown" : "hidden"}`, async t => {
    const page = fixture({ search: "?view=ia", routes: routesFor(Object.assign({}, WITH, {
      costs: Object.assign({}, WITH.costs, { top_ips: list }),
    })) }, t);
    await page.run().ready;
    await settle();
    const html = page.repBody.innerHTML;
    assert.equal(html.includes('class="rep-top-ips"'), shown);
    assert.doesNotMatch(html, /10\.0\.1\.6/);
    assert.doesNotMatch(html, /rep-lede|rep-sub"/);
  });
}

test("recorrido: switching a step off saves the whole order through the steps endpoint and reloads", async t => {
  const page = fixture({ search: "?view=recorrido", routes: routesFor(WITH, {
    "/api/admin/reports/funnel/steps": () => ok({ ok: true }),
  }) }, t);
  await page.run().ready;
  await settle();
  const sent = [];
  const inner = globalThis.fetch;
  globalThis.fetch = (url, options = {}) => { sent.push({ url, options }); return inner(url, options); };
  const pop = page.repBody.children.get("rep-rec-pop");
  pop.listeners.get("change")({ target: { getAttribute: name => (name === "data-rec-on" ? "presente" : null), checked: false } });
  await settle();
  const post = sent.find(r => r.url === "/api/admin/reports/funnel/steps");
  assert.ok(post, "the toggle must POST the step setting");
  assert.equal(post.options.method, "POST");
  assert.deepEqual(JSON.parse(post.options.body), { steps: [{ key: "pedido", on: true }, { key: "presente", on: false }] });
  assert.ok(sent.some(r => r.url === "/api/admin/reports/funnel?days=30"), "saving reloads the report");
});

test("recorrido without data: one quiet line, no chart, no unmeasured-steps note", async t => {
  const page = fixture({ search: "?view=recorrido", routes: routesFor(WITHOUT) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.equal(count(html, /rep-empty/g), 1);
  assert.doesNotMatch(html, /\[chart:/);
  assert.doesNotMatch(html, /No se mide/);
  assert.equal(count(html, /rep-section/g), 0);
  assert.ok(page.repBody.children.has("rep-periodo"));
  assert.equal(stored(page).recorrido, false);
});

test("the report list under the breadcrumb names only the other data-bearing reports", async t => {
  const storage = new MemoryStorage();
  storage.setItem("hr.reportes.disponibles", JSON.stringify(Object.fromEntries(ALL_KEYS.map(k => [k, k === "gestion" || k === "login" || k === "sumar"]))));
  const page = fixture({ search: "?view=gestion", routes: routesFor(WITH), storage }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  const nav = html.slice(html.indexOf('<nav class="rep-nav"'), html.indexOf("</nav>"));
  assert.ok(nav.length > 0, "nav missing");
  // closed until the reader opens it from the report's own name
  assert.match(html, /Reportes<\/a> \/ <details class="rep-switch"><summary>Gestión<span class="adm-icon" aria-hidden="true"><\/span><\/summary><nav class="rep-nav"/);
  assert.equal(count(nav, /<a /g), 2);
  assert.match(nav, /reportes\?view=login"/);
  assert.match(nav, /reportes\?view=sumar"/);
  assert.doesNotMatch(nav, /view=gestion"/);
  assert.doesNotMatch(nav, /view=recupero"/);
  // the nav is the only place a sub-report page links to another report
  assert.equal(count(html, /reportes\?view=/g), 2);
});

test("without a stored availability map the list falls back to every other report and fetches nothing extra", async t => {
  const page = fixture({ search: "?view=gestion", routes: routesFor(WITH), storage: null }, t);
  await page.run().ready;
  await settle();
  assert.deepEqual(page.requests, ["/api/admin/reports?days=30"]);
  const html = page.repBody.innerHTML;
  const nav = html.slice(html.indexOf('<nav class="rep-nav"'), html.indexOf("</nav>"));
  assert.equal(count(nav, /<a /g), 12);
  assert.doesNotMatch(nav, /view=gestion"/);
});

test("the portada's availability map hides a report that only the portada knows is empty", async t => {
  const page = fixture({ routes: routesFor(WITHOUT, { "/api/admin/reports?days=30": () => ok(WITH.gestion) }) }, t);
  await page.run().ready;
  await settle();
  assert.equal(count(page.repBody.innerHTML, /class="rep-sumcard[ "]/g), 1);
  // Same viewer, next page load: Gestión is the only report with data, so
  // there is no other report to list and the nav is not drawn at all.
  page.navigate("?view=gestion", routesFor(WITH));
  await page.run().ready;
  await settle();
  assert.match(page.repBody.innerHTML, /Reportes<\/a> \/ Gestión/);
  assert.doesNotMatch(page.repBody.innerHTML, /<nav class="rep-nav"/);
});

// No data, no block (critical review 08.11 E9; product owner R30, 05.10: «Hide the
// empty tiles…»): a figure that prints zero, a column of zeros, a chart of
// zeros and a composition of one part are not drawn.
test("Costos de IA: tiles and spend rows that print zero are not drawn", async t => {
  const costs = Object.assign({}, WITH.costs, {
    resumen: Object.assign({}, WITH.costs.resumen, { costo_mediano_por_ip: 0.00001, por_1000_pacientes_usd: 0 }),
    por_operacion: [
      { label: "dni", cantidad: 300, costo_usd: 1.2, costo_prom: 0.004 },
      { label: "Análisis de la orden", cantidad: 13, costo_usd: 0, costo_prom: 0 },
    ],
  });
  const page = fixture({ search: "?view=ia", routes: routesFor(Object.assign({}, WITH, { costs })) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.doesNotMatch(html, /US\$ 0,0000/);
  assert.doesNotMatch(html, /Gasto por persona/);
  assert.doesNotMatch(html, /Cada 1000 pacientes/);
  assert.doesNotMatch(html, /Análisis de la orden/);
  assert.match(html, /Costo por operación/);
  assert.match(html, /En qué se va el gasto/);
  assert.match(html, />dni</);
});

test("Costos de IA: with every operation free, «En qué se va el gasto» is not drawn", async t => {
  const costs = Object.assign({}, WITH.costs, { por_operacion: [{ label: "Análisis de la orden", cantidad: 13, costo_usd: 0.00001, costo_prom: 0 }] });
  const page = fixture({ search: "?view=ia", routes: routesFor(Object.assign({}, WITH, { costs })) }, t);
  await page.run().ready;
  await settle();
  assert.doesNotMatch(page.repBody.innerHTML, /En qué se va el gasto/);
});

test("Estadística: with every visit marked, no «Sin marca» tile and no column of zeros", async t => {
  const fila = { label: "Clínica", consultas: 50, presentes: 45, ausentes: 5, sin_marca: 0, horas_ofrecidas: 20, consultas_por_hora: 2.5 };
  const statistics = mes => ({ mes, hero: [], kpis: { consultas_totales: 50, presentes: 45, ausentes: 5, sin_marca: 0, pct_asistencia: 90 },
    por_especialidad: [fila, Object.assign({}, fila, { label: "Pediatría", consultas: 20, presentes: 20, ausentes: 0 })], edades: [], por_centro: [] });
  const page = fixture({ search: "?view=estadistica", routes: routesFor(Object.assign({}, WITH, { statistics })) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.doesNotMatch(html, /\[tile:Sin marca todavía\]/);
  assert.doesNotMatch(html, /data-label="Sin marca/);
  assert.match(html, /rep-esp-table/);
  // «Ausentes» has one non-zero row: the column stays, the zero cell too
  assert.equal(count(html, /<td class="cnt-numeric" data-label="Ausentes">/g), 2);
});

test("Estadística: unmarked visits keep the tile and the column", async t => {
  const page = fixture({ search: "?view=estadistica", routes: routesFor(WITH) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.match(html, /\[tile:Sin marca todavía\]/);
  assert.equal(count(html, /<th class="cnt-th cnt-numeric">Sin marca/g), 1);
});

test("Estadística: consultas per hour that print 0,00 everywhere draw no chart", async t => {
  const fila = { label: "Clínica", consultas: 1, presentes: 1, ausentes: 0, sin_marca: 0, horas_ofrecidas: 400, consultas_por_hora: 0.001 };
  const statistics = mes => ({ mes, hero: [], kpis: { consultas_totales: 2, presentes: 2, ausentes: 0, sin_marca: 0, pct_asistencia: 100 },
    por_especialidad: [fila, Object.assign({}, fila, { label: "Pediatría", consultas_por_hora: 0.002 })], edades: [], por_centro: [] });
  const page = fixture({ search: "?view=estadistica", routes: routesFor(Object.assign({}, WITH, { statistics })) }, t);
  await page.run().ready;
  await settle();
  const html = page.repBody.innerHTML;
  assert.doesNotMatch(html, /\[chart:lollipop\]/);
  assert.doesNotMatch(html, /data-label="Consultas por hora/);
});

test("Capacidad: every specialty at 0 % draws no occupancy chart", async t => {
  const ceros = capacityWith([especialidad("Cardiología", 0), especialidad("Clínica", 0.04)], { demanda_semanal: [{ semana: "2026-09-01", solicitudes: 2, busquedas: 4 }] });
  const page = fixture({ search: "?view=capacidad", routes: routesFor(ceros) }, t);
  await page.run().ready;
  await settle();
  assert.doesNotMatch(page.repBody.innerHTML, /\[chart:lollipop\]/);
});

test("Capacidad: one specialty without agenda keeps its title and action, not a one-tile treemap; two draw it", async t => {
  const una = capacityWith([especialidad("Clínica", 40)], { sin_oferta: [{ label: "Vacunas y prevención", solicitudes: 3 }] });
  const p1 = fixture({ search: "?view=capacidad", routes: routesFor(una) }, t);
  await p1.run().ready;
  await settle();
  assert.match(p1.repBody.innerHTML, /Vacunas y prevención no tiene ninguna hora de agenda/);
  assert.match(p1.repBody.innerHTML, /Cargar agenda ahí/);
  assert.doesNotMatch(p1.repBody.innerHTML, /\[chart:treemap\]/);
  const dos = capacityWith([especialidad("Clínica", 40)], { sin_oferta: [{ label: "Vacunas y prevención", solicitudes: 3 }, { label: "Pediatría", solicitudes: 1 }] });
  const p2 = fixture({ search: "?view=capacidad", routes: routesFor(dos) }, t);
  await p2.run().ready;
  await settle();
  assert.equal(count(p2.repBody.innerHTML, /\[chart:treemap\]/g), 1);
});

test("every report treemap goes through the one-part rule", () => {
  const js = readFileSync(new URL("../static/platform/admin-reports.mjs", import.meta.url), "utf8");
  assert.equal(count(js, /Charts\.treemap\(/g), 1, "only treemapDeVarios calls the engine");
  assert.match(js, /function treemapDeVarios\(items, opts\) \{\n.*i\.valor > 0.*\n.*conValor\.length >= 2 \? Charts\.treemap\(conValor, opts\) : "";/);
});

test("Estadística: points that print 0,00 leave the consultas-per-hour chart; one point left draws none", async t => {
  const fila = { label: "Clínica", consultas: 40, presentes: 40, ausentes: 0, sin_marca: 0, horas_ofrecidas: 20, consultas_por_hora: 2 };
  const cero = (label) => Object.assign({}, fila, { label, consultas: 0, presentes: 0, consultas_por_hora: 0 });
  const stats = (filas) => (mes) => ({ mes, hero: [], kpis: { consultas_totales: 40, presentes: 40, ausentes: 0, sin_marca: 0, pct_asistencia: 100 },
    por_especialidad: filas, edades: [], por_centro: [] });
  const dos = fixture({ search: "?view=estadistica", routes: routesFor(Object.assign({}, WITH, {
    statistics: stats([fila, Object.assign({}, fila, { label: "Pediatría", consultas_por_hora: 1.5 }), cero("Dermatología"), cero("Nutrición")]) })) }, t);
  await dos.run().ready;
  await settle();
  const html = dos.repBody.innerHTML;
  assert.equal(count(html, /\[chart:lollipop\]/g), 1);
  assert.match(html, /data-label="Consultas\/hora">0</, "the zero specialty keeps its row in the table");
  const una = fixture({ search: "?view=estadistica", routes: routesFor(Object.assign({}, WITH, {
    statistics: stats([fila, cero("Dermatología"), cero("Nutrición")]) })) }, t);
  await una.run().ready;
  await settle();
  assert.doesNotMatch(una.repBody.innerHTML, /\[chart:lollipop\]/);
});

test("the consultas-per-hour chart takes only points that print non-zero", () => {
  const js = readFileSync(new URL("../static/platform/admin-reports.mjs", import.meta.url), "utf8");
  assert.match(js, /r\.consultas_por_hora != null && !muestraCero\(porHora\(r\.consultas_por_hora\)\)/);
  assert.match(js, /if \(rows\.length < 2\) return "";/);
});

// ui-expert 2026-11-15: the Médicos card on Reportes said «0,1%» and the
// report's tile «0%» — the tile averaged each doctor's own load. Now both are
// booked turnos over places of the next 14 days, all doctors together. The
// two doctors' loads differ (12/1000 vs 0/466) so an average would not match.
const TWO_DOCTORS = {
  doctorsStats: { medicos: {
    "dra-garcia": { horas_semana: 1000, turnos_14d: 12, slots_14d: 8000, ocupacion_14d: 0, tiene_foto: true, tiene_login: true },
    "dr-perez": { horas_semana: 466, turnos_14d: 0, slots_14d: 552, ocupacion_14d: 0, tiene_foto: true, tiene_login: true },
  } },
  doctorsContent: { items: [
    { id: "dra-garcia", nombre: "Dra. Garcia", especialidad: "Clínica", centro: "caps-central" },
    { id: "dr-perez", nombre: "Dr. Perez", especialidad: "Pediatría", centro: "caps-central" },
  ] },
};
const kpi = (html, label) => {
  const m = html.match(new RegExp('<div class="rep-kpi-value">([^<]*)</div><div class="rep-kpi-label">' + label));
  return m && m[1];
};

async function renderedWith(t, search, P) {
  const page = fixture({ search, routes: routesFor(Object.assign({}, WITH, P)) }, t);
  await page.run().ready;
  await settle();
  return page.repBody.innerHTML;
}

test("Médicos: the occupancy tile is the portada card's figure, hours without a trailing ,0", async t => {
  const portada = await renderedWith(t, "", TWO_DOCTORS);
  assert.match(portada, /rep-sumcard-title">Médicos<\/div><div class="rep-sumcard-value">0,1%/);
  const page = await renderedWith(t, "?view=medicos", TWO_DOCTORS);
  assert.equal(kpi(page, "Ocupación de los próximos 14 días"), "0,1%");
  assert.equal(kpi(page, "Horas/semana ofrecidas"), "1.466");
  assert.doesNotMatch(page, /1\.466,0/);
});
