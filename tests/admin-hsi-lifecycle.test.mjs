// Runtime lifecycle tests for the native HSI controller.
// Imports and executes static/platform/admin-hsi.mjs. DOM, fetch, confirm,
// session/i18n/dates/format, and Response bodies are seams — no production
// functions are copied here.
import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../static/platform/admin-hsi.mjs";
import { API_ERROR_STUB } from "./fixtures/api-error-stub.mjs";

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function drain(turns = 16) {
  for (let i = 0; i < turns; i += 1) await tick();
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
function response(status = 200, body = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  };
}
function malformed(status = 200) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.reject(new Error("body not JSON")),
  };
}
const ok = (body) => Promise.resolve(response(200, body));
const fail = (status, body = {}) => Promise.resolve(response(status, body));

function parseAttrs(raw) {
  const attrs = Object.create(null);
  const re = /([:@A-Za-z_:][\w:.-]*)(?:="([^"]*)")?/g;
  let m;
  while ((m = re.exec(String(raw || "")))) {
    attrs[m[1]] = m[2] === undefined ? "" : m[2];
  }
  return attrs;
}

class Element {
  constructor(tag = "div", attrs = {}) {
    this.tagName = String(tag).toUpperCase();
    this.id = attrs.id || "";
    this.className = attrs.class || "";
    this.attrs = Object.assign(Object.create(null), attrs);
    this.dataset = Object.create(null);
    for (const [key, value] of Object.entries(attrs)) {
      if (key.slice(0, 5) === "data-") {
        this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
      }
    }
    this.children = [];
    this.listeners = new Map();
    this.parentNode = null;
    this.isConnected = true;
    this._html = "";
    this.value = attrs.value || "";
    this.disabled = Object.prototype.hasOwnProperty.call(attrs, "disabled");
    this.hidden = Object.prototype.hasOwnProperty.call(attrs, "hidden");
    this.textContent = "";
    this.style = {};
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = String(html);
    this.textContent = this._html.replace(/<[^>]+>/g, "");
    this.children = [];
    const tagRe = /<([a-zA-Z][\w-]*)\b([^>]*)>/g;
    let m;
    while ((m = tagRe.exec(this._html))) {
      const el = new Element(m[1], parseAttrs(m[2]));
      el.parentNode = this;
      this.children.push(el);
    }
  }
  matches(sel) {
    if (!sel) return false;
    if (sel[0] === "#") return this.id === sel.slice(1);
    if (sel[0] === ".") return this.className.split(/\s+/).includes(sel.slice(1));
    if (sel[0] === "[") {
      const m = /^\[([^\=\]]+)(?:=["']?([^"'\]]*)["']?)?\]$/.exec(sel);
      if (!m) return false;
      if (m[2] === undefined) return Object.prototype.hasOwnProperty.call(this.attrs, m[1]);
      return this.attrs[m[1]] === m[2];
    }
    return this.tagName === sel.toUpperCase();
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const parts = String(sel).split(",").map((part) => part.trim()).filter(Boolean);
    if (parts.length > 1) {
      const out = [];
      for (const part of parts) out.push(...this.querySelectorAll(part));
      return out;
    }
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (child.matches(sel)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  getAttribute(name) {
    const v = this.attrs[name];
    return v === undefined ? null : v;
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
  }
  appendChild(el) {
    el.parentNode = this;
    this.children.push(el);
    if (el.id) this.children[el.id] = el;
    return el;
  }
  closest(sel) {
    let node = this;
    while (node) {
      if (node.matches && node.matches(sel)) return node;
      node = node.parentNode;
    }
    return null;
  }
}

const I18N_SRC = "export const t=(key,vars,fallback)=>{var out=String(fallback==null?key:fallback);if(!vars)return out;return out.replace(/\\{(\\w+)\\}/g,(m,k)=>vars[k]===undefined?m:String(vars[k]))}" + API_ERROR_STUB;
const DATES_SRC = "export function corta(dia,hora){globalThis.__hsiDateCalls.push({fn:'corta',dia:dia,hora:hora});return 'Mié 10 sep, '+hora} export function relativo(iso){globalThis.__hsiDateCalls.push({fn:'relativo',iso:iso});return 'hace 2 h'}";
const FORMAT_SRC = "export function nombre(value){globalThis.__hsiNameCalls.push(value);return value==='ANA PEREZ'?'Ana Pérez':String(value==null?'':value)}";
const SESSION_SRC = [
  "export function confirmar(response, parsedBody, isCurrent) {",
  "  var current = typeof isCurrent === 'function' ? isCurrent : null;",
  "  globalThis.__hsiConfirmCalls.push({ status: response && response.status, body: parsedBody, hasPredicate: typeof isCurrent === 'function', current: current ? current() : null });",
  "  if (!response || !current) return Promise.resolve(false);",
  "  if (!current()) return Promise.resolve(false);",
  "  if (response.status === 403 && parsedBody && parsedBody.error === '2fa_requerido') {",
  "    var two = globalThis.__hsiWhoamiGate || Promise.resolve(true);",
  "    return two.then(function () { if (!current()) return false; globalThis.location.href = '/admin/account?setup=2fa'; return true; });",
  "  }",
  "  if (response.status !== 401) return Promise.resolve(false);",
  "  if (!parsedBody || parsedBody.error !== 'no_autenticado') return Promise.resolve(false);",
  "  var gate = globalThis.__hsiWhoamiGate || Promise.resolve(true);",
  "  return gate.then(function (confirmed) {",
  "    if (!current()) return false;",
  "    if (confirmed) globalThis.location.href = '/admin/login?next=' + encodeURIComponent((globalThis.location.pathname || '/admin/hsi') + (globalThis.location.search || ''));",
  "    return !!confirmed;",
  "  });",
  "}",
].join("\n");

const CONFLICT = {
  conflicto_id: 41,
  solicitud_id: 88,
  fecha: "2026-09-10T08:00:00",
  paciente: "ANA PEREZ",
  medico: "DRA MARTINEZ",
  problema: "El turno en HSI no coincide.",
  cluster: "drift",
  resoluciones: [
    { accion: "mover", label: "Mover al primer horario libre" },
    { accion: "sobreturno", label: "Agendar como sobreturno" },
    { accion: "rechazar", label: "Rechazar la solicitud" },
  ],
};
const PANEL = {
  metricas: {
    futuras: 20, futuras_ok: 17, pct_futuras: 85,
    total: 40, total_ok: 30, pct_total: 75, outbox: {},
  },
  abiertos: 1,
  conflictos: [CONFLICT],
  resueltos: [{
    solicitud_id: 9,
    texto: "Se reasignó automáticamente.",
    cuando: "2026-09-07T12:00:00",
  }],
};
const SYNC = {
  counts: {}, outbox: [], conflicts: [], hsi_activo: true, panel: PANEL,
};
const EMPTY_PANEL = {
  metricas: { futuras: 0, futuras_ok: 0, pct_futuras: null, total: 0, total_ok: 0, pct_total: null, outbox: {} },
  abiertos: 0, conflictos: [], resueltos: [],
};

function dataUrl(src) {
  return "data:text/javascript," + encodeURIComponent(src);
}

const STATE_URL = "/api/admin/system/hsi";

// `requests` is the journal surface (the three hsi-sync endpoints), exactly as
// before slice 7; the state read that now comes first is kept apart in
// `stateRequests`, answered by `state` (a function returning a response
// promise; the server-sending state by default), and `order` lists every URL.
function fixture({ routes, confirm = () => true, manifest = null, search = "", whoamiGate = null, state = null } = {}, t) {
  const host = new Element("div", { id: "sec-hsi" });
  host.id = "sec-hsi";
  const status = new Element("p", { id: "hsi-status", class: "adm-empty", hidden: "" });
  status.id = "hsi-status";
  status.hidden = true;
  let styleEl = null;
  const head = new Element("head");
  const byId = new Map([["sec-hsi", host], ["hsi-status", status]]);
  const doc = {
    readyState: "complete",
    head,
    getElementById(id) {
      if (id === "hsi-css") return styleEl && styleEl.id === "hsi-css" ? styleEl : null;
      return byId.get(id) || null;
    },
    createElement(tag) {
      const el = new Element(tag);
      if (tag === "style") styleEl = el;
      return el;
    },
  };
  const requests = [];
  const stateRequests = [];
  const order = [];
  const replaces = [];
  const confirmCalls = [];
  const keys = ["window", "fetch", "location", "history", "document", "confirm",
    "__hsiConfirmCalls", "__hsiDateCalls", "__hsiNameCalls", "__hsiWhoamiGate"];
  const previous = Object.fromEntries(keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.__hsiConfirmCalls = [];
  globalThis.__hsiDateCalls = [];
  globalThis.__hsiNameCalls = [];
  globalThis.__hsiWhoamiGate = whoamiGate;
  globalThis.document = doc;
  globalThis.confirm = (msg) => { confirmCalls.push(msg); return confirm(msg); };
  globalThis.location = {
    href: "https://example.test/admin/hsi" + search,
    pathname: "/admin/hsi",
    search,
  };
  globalThis.history = { replaceState(...args) { replaces.push(args); } };
  const windowBase = {
    SANA_STATIC: manifest || {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/dates.mjs": dataUrl(DATES_SRC),
      "platform/format.mjs": dataUrl(FORMAT_SRC),
    },
  };
  globalThis.window = windowBase;
  windowBase.location = globalThis.location;
  windowBase.history = globalThis.history;
  windowBase.confirm = globalThis.confirm;
  windowBase.document = doc;
  globalThis.fetch = (url, options = {}) => {
    const method = String(options.method || "GET").toUpperCase();
    order.push(url);
    if (method === "GET" && url === STATE_URL) {
      stateRequests.push({ url, method, options });
      return state ? state(stateRequests.length, options) : ok(STATE);
    }
    requests.push({ url, method, options });
    const next = routes(url, method, requests.length, options);
    assert.ok(next && typeof next.then === "function", "unrouted HSI fetch: " + method + " " + url);
    return next;
  };
  t.after(() => {
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  const disposes = [];
  const run = (opts) => {
    const dispose = mount(host, Object.assign({ status }, opts || {}));
    disposes.push(dispose);
    return dispose;
  };
  const fire = (el, type) => {
    const fn = host.listeners.get(type);
    assert.ok(typeof fn === "function", "no " + type + " listener on host");
    fn({ target: el, preventDefault() {} });
  };
  return {
    host, status, requests, stateRequests, order, replaces, confirmCalls, windowRef: windowBase, run, fire,
    child(sel) { return host.querySelector(sel); },
    children(sel) { return host.querySelectorAll(sel); },
    posts(url) { return requests.filter((r) => r.method === "POST" && r.url === url); },
    gets() { return requests.filter((r) => r.method === "GET").map((r) => r.url); },
  };
}

// The installation's HSI state, read before the journal (slice 7).
const STATE = {
  modo: "servidor", motivo: "conector", conector: "hsi",
  modo_texto: "Envío desde el servidor",
  motivo_texto: "El conector HSI está activo: el servidor envía a HSI cada turno aprobado.",
  centros: [],
};
const CENTERS = [
  { slug: "hospital", nombre: "Hospital", institucion: "1086" },
  { slug: "caps-norte", nombre: "CAPS Norte", institucion: "" },
];
const STATE_EXTENSION = {
  modo: "extension", motivo: "sin_conector", conector: null,
  modo_texto: "Carga con la extensión del navegador",
  motivo_texto: "El conector HSI no está activo, así que el servidor no envía turnos.",
  centros: CENTERS,
};

function defaultRoutes(over = {}) {
  const table = {
    "GET /api/admin/hsi-sync": ok(SYNC),
    "POST /api/admin/hsi-sync/resolver": ok({
      ok: true, resumen: { resueltos: 0 }, panel: PANEL,
    }),
    "POST /api/admin/hsi-sync/conflicts": ok({
      ok: true,
      resultados: [{ conflicto_id: 41, ok: true, detalle: "Reprogramado." }],
      panel: { ...PANEL, conflictos: [], abiertos: 0 },
    }),
  };
  return (url, method) => {
    const key = method + " " + url;
    if (Object.prototype.hasOwnProperty.call(over, key)) return over[key];
    const hit = table[key];
    assert.ok(hit, "unexpected HSI fetch: " + key);
    return hit;
  };
}

function applyButton(f) { return f.child("[data-hsi-apply]"); }
function altSelect(f) { return f.child(".hsi-alt"); }

test("import is inert: mount waits for all four manifested dependencies before any fetch", async (t) => {
  const datesGate = deferred();
  globalThis.__datesGate = datesGate.promise;
  t.after(() => { delete globalThis.__datesGate; });
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/dates.mjs": dataUrl("await globalThis.__datesGate; export function corta(d,h){return h} export function relativo(){return 'rel'}"),
      "platform/format.mjs": dataUrl(FORMAT_SRC),
    },
  }, t);
  const dispose = f.run();
  await drain(8);
  assert.equal(f.requests.length, 0, "held dates import must not start the panel GET");
  datesGate.resolve();
  await dispose.ready;
  assert.deepEqual(f.order, [STATE_URL, "/api/admin/hsi-sync"], "the state is read first, then the journal");
  assert.deepEqual(f.gets(), ["/api/admin/hsi-sync"]);
  assert.equal(f.requests.filter((r) => r.method !== "GET").length, 0);
  assert.ok(f.windowRef.SANA_STATIC);
  assert.equal(f.windowRef.loadHsiSync, undefined);
  assert.equal(f.windowRef.renderHsiSec, undefined);
  assert.equal(f.windowRef.HSI_PANEL, undefined);
  assert.equal(f.windowRef.routeHsi, undefined);
});

test("a valid panel paints hero, six columns, alternative select, patient link, dates and resolved history", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  assert.match(f.host.innerHTML, /85%/);
  assert.match(f.host.innerHTML, /de los turnos futuros aprobados están agendados en HSI/);
  assert.match(f.host.innerHTML, /<th class="cnt-th">Fecha<\/th>/);
  assert.match(f.host.innerHTML, /Paciente/);
  assert.match(f.host.innerHTML, /Médico/);
  assert.match(f.host.innerHTML, /Problema/);
  assert.match(f.host.innerHTML, /Resolución/);
  assert.match(f.host.innerHTML, /Otras opciones/);
  assert.match(f.host.innerHTML, /href="\/admin\/solicitudes"/);
  assert.match(f.host.innerHTML, /Ana Pérez/);
  assert.doesNotMatch(f.host.innerHTML, /ANA PEREZ/);
  assert.match(f.host.innerHTML, /Mié 10 sep, 08:00/);
  assert.doesNotMatch(f.host.innerHTML, /2026-09-10T08:00:00/);
  assert.equal(applyButton(f).dataset.accion, "mover");
  assert.equal(applyButton(f).dataset.cf, "41");
  const alt = altSelect(f);
  assert.equal(alt.tagName, "SELECT");
  assert.match(f.host.innerHTML, /<option value="sobreturno">Agendar como sobreturno<\/option>/);
  assert.match(f.host.innerHTML, /<option value="rechazar">Rechazar la solicitud<\/option>/);
  assert.doesNotMatch(f.host.innerHTML, /type="checkbox"/);
  assert.match(f.host.innerHTML, /id="hsi-rescan-btn"/);
  assert.match(f.host.innerHTML, /Resueltos automáticamente \(1\)/);
  assert.match(f.host.innerHTML, /hace 2 h/);
  assert.ok(f.status.hidden);
  assert.ok(globalThis.__hsiDateCalls.some((c) => c.fn === "corta"));
  assert.ok(globalThis.__hsiDateCalls.some((c) => c.fn === "relativo"));
});

test("zero future appointments and an empty conflict list keep their honest empty states", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/hsi-sync": ok({ ...SYNC, panel: EMPTY_PANEL }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.match(f.host.innerHTML, /No hay turnos futuros para sincronizar con HSI\./);
  assert.match(f.host.innerHTML, /No hay conflictos que necesiten a una persona/);
  assert.equal(f.children("[data-hsi-apply]").length, 0);
  assert.equal(f.children(".hsi-alt").length, 0);
});

test("canonicalises a query string off the bare /admin/hsi path", async (t) => {
  const f = fixture({ routes: defaultRoutes(), search: "?from=nav" }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.replaces.length, 1);
  assert.deepEqual(f.replaces[0], [{}, "", "/admin/hsi"]);
});

for (const missing of [
  "platform/admin-session.mjs",
  "platform/i18n.mjs",
  "platform/dates.mjs",
  "platform/format.mjs",
]) {
  test("missing " + missing + " fails closed: onError, no fetch, host untouched", async (t) => {
    const manifest = {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/dates.mjs": dataUrl(DATES_SRC),
      "platform/format.mjs": dataUrl(FORMAT_SRC),
    };
    delete manifest[missing];
    const f = fixture({ routes: defaultRoutes(), manifest }, t);
    let failed = null;
    const dispose = f.run({ onError: (err) => { failed = err; } });
    await dispose.ready;
    assert.ok(failed && /Missing HSI dependency/.test(failed.message), missing);
    assert.equal(f.requests.length, 0);
    assert.equal(f.host.innerHTML, "");
  });
}

test("a rejected dates import never falls back to a local date formatter", async (t) => {
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/dates.mjs": "data:text/javascript,throw new Error('dates exploded')",
      "platform/format.mjs": dataUrl(FORMAT_SRC),
    },
  }, t);
  let failed = null;
  const dispose = f.run({ onError: (err) => { failed = err; } });
  await dispose.ready;
  assert.ok(failed);
  assert.equal(f.requests.length, 0);
  assert.equal(f.host.innerHTML, "");
  assert.equal(globalThis.__hsiDateCalls.length, 0);
});

test("without onError a rejected session import paints one finite load-error line", async (t) => {
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/dates.mjs": dataUrl(DATES_SRC),
      "platform/format.mjs": dataUrl(FORMAT_SRC),
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.requests.length, 0);
  assert.match(f.host.innerHTML, /No se pudo cargar la sincronización con HSI\./);
  assert.equal(f.child("#hsi-retry-btn"), null, "dependency failure is not a retryable data load");
});

for (const [label, body] of [
  ["non-object body", []],
  ["null body", null],
  ["missing metrics", { panel: { conflictos: [], resueltos: [] } }],
  ["null percent for future appointments", { ...SYNC, panel: { ...PANEL, metricas: { ...PANEL.metricas, pct_futuras: null } } }],
  ["string future count", { ...SYNC, panel: { ...PANEL, metricas: { ...PANEL.metricas, futuras: "20" } } }],
  ["out-of-range percent", { ...SYNC, panel: { ...PANEL, metricas: { ...PANEL.metricas, pct_futuras: 101 } } }],
  ["panel string", { panel: "ok" }],
  ["panel array", { panel: [] }],
  ["conflictos object", { panel: { metricas: {}, conflictos: {}, resueltos: [] } }],
  ["resueltos string", { panel: { conflictos: [], resueltos: "none" } }],
  ["conflict without id", { panel: { conflictos: [{ paciente: "X", resoluciones: [] }], resueltos: [] } }],
  ["resoluciones not an array", { panel: { conflictos: [{ conflicto_id: 1, resoluciones: { accion: "mover" } }], resueltos: [] } }],
]) {
  test("malformed panel (" + label + ") is a load failure, never an invented empty success", async (t) => {
    const f = fixture({ routes: defaultRoutes({ "GET /api/admin/hsi-sync": ok(body) }) }, t);
    const dispose = f.run();
    await dispose.ready;
    assert.match(f.host.innerHTML, /No se pudo cargar la sincronización con HSI\./);
    assert.match(f.host.innerHTML, /id="hsi-retry-btn"/);
    assert.doesNotMatch(f.host.innerHTML, /No hay conflictos que necesiten a una persona/);
    assert.doesNotMatch(f.host.innerHTML, /hsi-hero/);
  });
}

test("HTTP 200 with panel:null is a retryable load failure, not a blank success", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/hsi-sync": ok({ ...SYNC, panel: null }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.match(f.host.innerHTML, /id="hsi-retry-btn"/);
  assert.doesNotMatch(f.host.innerHTML, /hsi-vacio/);
});

test("unparsable JSON and a rejected GET each show Retry; Retry recovers with one extra GET", async (t) => {
  let reads = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/hsi-sync") {
        reads += 1;
        if (reads === 1) return Promise.resolve(malformed(200));
        if (reads === 2) return Promise.reject(new Error("offline"));
        return ok(SYNC);
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.match(f.host.innerHTML, /id="hsi-retry-btn"/);
  f.fire(f.child("#hsi-retry-btn"), "click");
  await drain();
  assert.equal(f.gets().length, 2);
  assert.match(f.host.innerHTML, /id="hsi-retry-btn"/);
  f.fire(f.child("#hsi-retry-btn"), "click");
  await drain();
  assert.equal(f.gets().length, 3);
  assert.match(f.host.innerHTML, /Ana Pérez/);
  assert.equal(f.child("#hsi-retry-btn"), null);
});

test("ordinary 403 stays local with Retry; confirmar received the owner predicate", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/hsi-sync": fail(403, { error: "solo admin" }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(globalThis.__hsiConfirmCalls.length, 1);
  assert.equal(globalThis.__hsiConfirmCalls[0].hasPredicate, true);
  assert.equal(globalThis.__hsiConfirmCalls[0].status, 403);
  assert.match(f.host.innerHTML, /id="hsi-retry-btn"/);
  assert.equal(globalThis.location.href, "https://example.test/admin/hsi");
});

test("demo read-only never consults the session module", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "GET /api/admin/hsi-sync": ok(SYNC),
      "POST /api/admin/hsi-sync/conflicts": fail(403, { error: "demo_solo_lectura" }),
    }),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(applyButton(f), "click");
  await drain();
  assert.equal(globalThis.__hsiConfirmCalls.length, 0);
  assert.match(f.status.textContent, /Demo de solo lectura/);
  assert.equal(applyButton(f).disabled, false, "local demo refusal restores controls");
  assert.equal(globalThis.location.href, "https://example.test/admin/hsi");
});

test("confirmed expiry transitions through confirmar and leaves no Retry", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/hsi-sync": fail(401, { error: "no_autenticado" }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(globalThis.__hsiConfirmCalls.length, 1);
  assert.equal(globalThis.__hsiConfirmCalls[0].hasPredicate, true);
  assert.equal(f.host.innerHTML, "");
  assert.equal(f.child("#hsi-retry-btn"), null);
  assert.match(globalThis.location.href, /\/admin\/login\?next=/);
});

test("2FA repair transitions through confirmar and paints no local failure", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/hsi-sync": fail(403, { error: "2fa_requerido" }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(globalThis.__hsiConfirmCalls.length, 1);
  assert.equal(f.host.innerHTML, "");
  assert.equal(globalThis.location.href, "/admin/account?setup=2fa");
});

test("a stale GET after dispose does not paint or start a session repair", async (t) => {
  const gate = deferred();
  const f = fixture({
    routes: (url, method) => (method === "GET" && url === "/api/admin/hsi-sync" ? gate.promise : defaultRoutes()(url, method)),
  }, t);
  const dispose = f.run();
  await drain(6);
  assert.equal(f.requests.length, 1);
  dispose();
  gate.resolve(response(401, { error: "no_autenticado" }));
  await drain(8);
  assert.match(f.host.innerHTML, /Cargando/);
  assert.doesNotMatch(f.host.innerHTML, /hsi-retry-btn|Ana Pérez|85%/);
  assert.equal(globalThis.location.href, "https://example.test/admin/hsi");
  const calls = globalThis.__hsiConfirmCalls;
  if (calls.length) assert.equal(calls.every((c) => c.current === false || c.hasPredicate === true), true);
  assert.equal(calls.some((c) => c.current === true), false, "disposed owner is never current for session repair");
});

test("pending whoami after dispose cannot navigate; remount owns the next GET", async (t) => {
  const whoami = deferred();
  const first = deferred();
  let reads = 0;
  const f = fixture({
    whoamiGate: whoami.promise,
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/hsi-sync") {
        reads += 1;
        if (reads === 1) return first.promise;
        return ok(SYNC);
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const firstDispose = f.run();
  await drain(6);
  first.resolve(response(401, { error: "no_autenticado" }));
  await drain(6);
  assert.equal(globalThis.__hsiConfirmCalls.length, 1);
  assert.equal(globalThis.__hsiConfirmCalls[0].current, true);
  firstDispose();
  const second = f.run();
  await drain(4);
  whoami.resolve(true);
  await second.ready;
  assert.match(f.host.innerHTML, /Ana Pérez/);
  assert.equal(globalThis.location.href, "https://example.test/admin/hsi");
  assert.equal(f.gets().length, 2);
});

test("duplicate apply while pending posts once; controls restore after a failed acknowledgement", async (t) => {
  const gate = deferred();
  let posts = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "POST" && url === "/api/admin/hsi-sync/conflicts") {
        posts += 1;
        if (posts === 1) return gate.promise;
        return ok({
          ok: true,
          resultados: [{ conflicto_id: 41, ok: true, detalle: "Reprogramado." }],
          panel: { ...PANEL, conflictos: [], abiertos: 0 },
        });
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(applyButton(f), "click");
  f.fire(applyButton(f), "click");
  f.fire(applyButton(f), "click");
  await drain();
  assert.equal(f.posts("/api/admin/hsi-sync/conflicts").length, 1);
  assert.equal(applyButton(f).disabled, true);
  gate.resolve(response(200, { ok: true, resultados: [{ conflicto_id: 99, ok: true, detalle: "wrong id" }] }));
  await drain();
  assert.match(f.status.textContent, /No se pudo aplicar/);
  assert.equal(applyButton(f).disabled, false);
  f.fire(applyButton(f), "click");
  await drain();
  assert.equal(f.posts("/api/admin/hsi-sync/conflicts").length, 2);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/hsi-sync/conflicts")[1].options.body), {
    accion: "mover", conflictos: [41],
  });
});

test("duplicate rescan is single-flight; apply while rescan is pending is ignored", async (t) => {
  const gate = deferred();
  const f = fixture({
    routes: defaultRoutes({ "POST /api/admin/hsi-sync/resolver": gate.promise }),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#hsi-rescan-btn"), "click");
  f.fire(f.child("#hsi-rescan-btn"), "click");
  f.fire(applyButton(f), "click");
  await drain();
  assert.equal(f.posts("/api/admin/hsi-sync/resolver").length, 1);
  assert.equal(f.posts("/api/admin/hsi-sync/conflicts").length, 0);
  gate.resolve(response(200, { ok: true, resumen: { resueltos: 2 }, panel: PANEL }));
  await drain();
  assert.match(f.status.textContent, /Se resolvieron 2 conflictos/);
  assert.equal(f.child("#hsi-rescan-btn").disabled, false);
});

test("rejection confirmation cancel does not mutate; confirm-ok posts rechazar", async (t) => {
  let allow = false;
  const f = fixture({
    confirm: () => allow,
    routes: defaultRoutes(),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  const alt = altSelect(f);
  alt.value = "rechazar";
  f.fire(alt, "change");
  await drain();
  assert.equal(f.posts("/api/admin/hsi-sync/conflicts").length, 0);
  assert.equal(f.confirmCalls.length, 1);
  assert.match(f.confirmCalls[0], /Se le avisa al paciente por email/);
  assert.equal(alt.value, "", "cancelling keeps the same resolution selectable again");
  allow = true;
  alt.value = "rechazar";
  f.fire(alt, "change");
  await drain();
  assert.equal(f.posts("/api/admin/hsi-sync/conflicts").length, 1);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/hsi-sync/conflicts")[0].options.body), {
    accion: "rechazar", conflictos: [41],
  });
});

test("a failed alternative action resets the select so the same action can be retried", async (t) => {
  const f = fixture({ routes: defaultRoutes({
    "POST /api/admin/hsi-sync/conflicts": fail(500, { error: "unavailable" }),
  }) }, t);
  await f.run().ready;
  const alt = altSelect(f);
  alt.value = "sobreturno";
  f.fire(alt, "change");
  await drain();
  assert.equal(alt.value, "");
  assert.equal(alt.disabled, false);
  assert.equal(f.posts("/api/admin/hsi-sync/conflicts").length, 1);
});

test("HTTP 200 without a matching ack is not a successful resolution", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "POST /api/admin/hsi-sync/conflicts": ok({ ok: true, panel: { ...PANEL, conflictos: [] } }),
    }),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(applyButton(f), "click");
  await drain();
  assert.match(f.status.textContent, /No se pudo aplicar/);
  assert.match(f.host.innerHTML, /data-hsi-apply/);
  assert.equal(applyButton(f).disabled, false);
});

for (const marker of ["true", "false", 1, 0, null, undefined]) {
  test(`non-boolean apply acknowledgement ${String(marker)} cannot report success`, async (t) => {
    const f = fixture({ routes: defaultRoutes({
      "POST /api/admin/hsi-sync/conflicts": ok({ ok: true,
        resultados: [{ conflicto_id: 41, ok: marker, detalle: "Reprogramado." }], panel: PANEL }),
    }) }, t);
    await f.run().ready;
    f.fire(applyButton(f), "click");
    await drain();
    assert.match(f.status.textContent, /No se pudo aplicar/);
    assert.equal(applyButton(f).disabled, false);
    assert.equal(f.requests.filter(r => r.method === "GET").length, 1,
      "malformed acknowledgements cannot start a success refresh");
  });
}

test("HTTP 200 with a rejected top-level acknowledgement cannot report resolution", async (t) => {
  const f = fixture({ routes: defaultRoutes({
    "POST /api/admin/hsi-sync/conflicts": ok({ ok: false,
      resultados: [{ conflicto_id: 41, ok: true, detalle: "Reprogramado." }], panel: PANEL }),
  }) }, t);
  await f.run().ready;
  f.fire(applyButton(f), "click");
  await drain();
  assert.match(f.status.textContent, /No se pudo aplicar/);
});

test("HTTP 200 with ack ok:false is a per-conflict refusal, not invented success", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "POST /api/admin/hsi-sync/conflicts": ok({
        ok: true,
        resultados: [{ conflicto_id: 41, ok: false, detalle: "La solicitud ya no está aprobada." }],
        panel: PANEL,
      }),
    }),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(applyButton(f), "click");
  await drain();
  assert.match(f.status.textContent, /No se pudo: La solicitud ya no está aprobada/);
  assert.doesNotMatch(f.status.textContent, /^Resuelto/);
  assert.equal(applyButton(f).disabled, false);
});

test("malformed rescan resumen is a failure, not 'sin cambios'", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "POST /api/admin/hsi-sync/resolver": ok({ ok: true, resumen: "done", panel: PANEL }),
    }),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#hsi-rescan-btn"), "click");
  await drain();
  assert.match(f.status.textContent, /No se pudo revisar/);
  assert.doesNotMatch(f.status.textContent, /Sin cambios/);
  assert.equal(f.child("#hsi-rescan-btn").disabled, false);
});

for (const count of [undefined, null, "0", "garbage", -1, 1.5]) {
  test(`malformed resolved count ${String(count)} is not a no-change success`, async (t) => {
    const f = fixture({ routes: defaultRoutes({
      "POST /api/admin/hsi-sync/resolver": ok({ ok: true, resumen: { resueltos: count }, panel: PANEL }),
    }) }, t);
    await f.run().ready;
    f.fire(f.child("#hsi-rescan-btn"), "click");
    await drain();
    assert.match(f.status.textContent, /No se pudo revisar/);
    assert.equal(f.child("#hsi-rescan-btn").disabled, false);
  });
}

test("hsi_desactivado on apply keeps the clinical local sentence and restores controls", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "POST /api/admin/hsi-sync/conflicts": fail(409, { error: "hsi_desactivado", resultados: [] }),
    }),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(applyButton(f), "click");
  await drain();
  assert.equal(f.status.textContent, "Este entorno no envía turnos a HSI.");
  assert.equal(applyButton(f).disabled, false);
});

test("successful apply posts the exact payload, toasts the ack, then refreshes via GET", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(applyButton(f), "click");
  await drain();
  const posted = f.posts("/api/admin/hsi-sync/conflicts");
  assert.equal(posted.length, 1);
  assert.equal(posted[0].options.credentials, "same-origin");
  assert.equal(posted[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(posted[0].options.body), { accion: "mover", conflictos: [41] });
  assert.equal(f.status.textContent, "Reprogramado.");
  assert.ok(f.gets().length >= 2);
});

test("dispose removes handlers, aborts the in-flight GET, and ignores a late apply outcome", async (t) => {
  const loadGate = deferred();
  const applyGate = deferred();
  let phase = "load";
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/hsi-sync") {
        if (phase === "load") return loadGate.promise;
        return ok(SYNC);
      }
      if (method === "POST" && url === "/api/admin/hsi-sync/conflicts") return applyGate.promise;
      return defaultRoutes()(url, method);
    },
  }, t);
  const dispose = f.run();
  await drain(6);
  assert.equal(f.requests[0].options.signal.aborted, false);
  loadGate.resolve(response(200, SYNC));
  await dispose.ready;
  phase = "apply";
  f.fire(applyButton(f), "click");
  await drain();
  assert.equal(f.posts("/api/admin/hsi-sync/conflicts").length, 1);
  const applySignal = f.posts("/api/admin/hsi-sync/conflicts")[0].options.signal;
  dispose();
  assert.equal(applySignal.aborted, true);
  assert.equal(hostHasClick(f), false);
  applyGate.resolve(response(200, {
    ok: true,
    resultados: [{ conflicto_id: 41, ok: true, detalle: "late" }],
    panel: { ...PANEL, conflictos: [] },
  }));
  await drain(8);
  assert.doesNotMatch(f.status.textContent, /late/);
  assert.doesNotMatch(f.host.innerHTML, /late/);
});

test("a superseded refresh cannot overwrite the newest panel even if abort is ignored", async (t) => {
  const old = deferred();
  let reads = 0;
  const f = fixture({ routes: (url, method) => {
    if (method === "GET") {
      reads += 1;
      if (reads === 2) return old.promise;
      if (reads === 3) return ok({ ...SYNC, panel: { ...PANEL, conflictos: [], resueltos: [] } });
    }
    return defaultRoutes()(url, method);
  } }, t);
  await f.run().ready;
  f.fire(f.child("#hsi-rescan-btn"), "click");
  await drain();
  assert.equal(reads, 2);
  f.fire(f.child("#hsi-rescan-btn"), "click");
  await drain();
  assert.equal(reads, 3);
  const newest = f.host.innerHTML;
  assert.doesNotMatch(newest, /data-hsi-apply/);
  old.resolve(response(200, SYNC));
  await drain();
  assert.equal(f.host.innerHTML, newest, "late older refresh must be inert");
});

function hostHasClick(f) {
  return typeof f.host.listeners.get("click") === "function";
}

test("same-host remount aborts the old GET; stale success cannot replace the new panel", async (t) => {
  const stale = deferred();
  let reads = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/hsi-sync") {
        reads += 1;
        if (reads === 1) return stale.promise;
        return ok({
          ...SYNC,
          panel: { ...EMPTY_PANEL, metricas: { ...EMPTY_PANEL.metricas, futuras: 4, futuras_ok: 4, pct_futuras: 100 } },
        });
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  f.run();
  await drain(6);
  const latest = f.run();
  await latest.ready;
  assert.equal(f.requests[0].options.signal.aborted, true);
  stale.resolve(response(200, SYNC));
  await drain(8);
  assert.match(f.host.innerHTML, /100%/);
  assert.doesNotMatch(f.host.innerHTML, /Ana Pérez/);
  assert.doesNotMatch(f.host.innerHTML, /85%/);
});

// ---- slice 7: the installation's HSI state --------------------------------

test("without the HSI connector the state is the screen and the journal is never asked", async (t) => {
  const f = fixture({ routes: defaultRoutes(), state: () => ok(STATE_EXTENSION) }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  assert.deepEqual(f.order, [STATE_URL]);
  assert.equal(f.requests.length, 0, "the journal API is a 404 without the connector: never asked");
  assert.match(f.host.innerHTML, /<h2 class="hsi-modo" data-hsi-mode="extension">Carga con la extensión del navegador<\/h2>/);
  assert.match(f.host.innerHTML, /<p class="hsi-motivo">El conector HSI no está activo, así que el servidor no envía turnos\.<\/p>/);
  assert.match(f.host.innerHTML, /<h2>Instituciones en HSI<\/h2>/);
  assert.match(f.host.innerHTML, /<a href="\/admin\/centers\?item=hospital">Hospital<\/a>/);
  assert.match(f.host.innerHTML, />1086<\/td>/);
  // The center with no number is called out, with the warning mark: here the
  // extension loads the turnos, and it is the one that reads the number.
  assert.match(f.host.innerHTML, /<span class="hsi-falta" data-hsi-missing><span class="hsi-falta-dot"><\/span>Sin número<\/span>/);
  assert.equal(f.children("[data-hsi-missing]").length, 1);
  assert.equal(f.child("#hsi-rescan-btn"), null);
  assert.equal(f.child("#hsi-retry-btn"), null);
  assert.doesNotMatch(f.host.innerHTML, /hsi-hero|Conflictos/);
  assert.ok(f.status.hidden);
});

test("with HSI off a missing number is named without the warning mark", async (t) => {
  const off = { ...STATE_EXTENSION, modo: "apagado", modo_texto: "Apagado", motivo_texto: "Los turnos no llegan a HSI." };
  const f = fixture({ routes: defaultRoutes(), state: () => ok(off) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.match(f.host.innerHTML, /data-hsi-mode="apagado">Apagado</);
  assert.match(f.host.innerHTML, /<span class="hsi-falta" data-hsi-missing>Sin número<\/span>/);
  assert.doesNotMatch(f.host.innerHTML, /hsi-falta-dot/);
  assert.equal(f.requests.length, 0);
});

test("with the server sending, the journal follows the state and the centers close the page", async (t) => {
  const f = fixture({ routes: defaultRoutes(), state: () => ok({ ...STATE, centros: CENTERS }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.deepEqual(f.order, [STATE_URL, "/api/admin/hsi-sync"]);
  const html = f.host.innerHTML;
  assert.doesNotMatch(html, /hsi-modo/, "the synced share already says the server sends");
  assert.match(html, /85%/);
  assert.ok(html.indexOf("Instituciones en HSI") > html.indexOf("Conflictos"), "centers come after the journal");
  assert.match(html, /<span class="hsi-falta" data-hsi-missing>Sin número<\/span>/);
});

test("the connector selected but not sending: the mode and its reason head the journal", async (t) => {
  const skip = { ...STATE_EXTENSION, motivo: "skip", conector: "hsi", centros: [] };
  const f = fixture({ routes: defaultRoutes(), state: () => ok(skip) }, t);
  const dispose = f.run();
  await dispose.ready;
  const html = f.host.innerHTML;
  assert.deepEqual(f.gets(), ["/api/admin/hsi-sync"]);
  assert.ok(html.indexOf("hsi-modo") > -1 && html.indexOf("hsi-modo") < html.indexOf("hsi-hero"));
  assert.doesNotMatch(html, /Instituciones en HSI/, "no centers, no empty table");
});

for (const [name, answer] of [
  ["a 500", () => fail(500, { error: "boom" })],
  ["a rejected read", () => Promise.reject(new Error("offline"))],
  ["a body with no mode", () => ok({ centros: [] })],
  ["centers that are not a list", () => ok({ ...STATE, centros: "x" })],
  // A mode with no connector key would read as «no HSI connector» and replace
  // the journal with the state; a mode with no words would print an empty heading.
  ["a mode without the connector key", () => ok({ modo: "extension", modo_texto: "Carga con la extensión del navegador", centros: [] })],
  ["a mode without its words", () => ok({ modo: "extension", conector: null, centros: [] })],
]) {
  test(`${name} for the state never hides the journal`, async (t) => {
    const f = fixture({ routes: defaultRoutes(), state: answer }, t);
    const dispose = f.run();
    await dispose.ready;
    assert.deepEqual(f.gets(), ["/api/admin/hsi-sync"]);
    assert.match(f.host.innerHTML, /85%/);
    assert.doesNotMatch(f.host.innerHTML, /hsi-modo|Instituciones en HSI/);
  });
}

test("Retry reads the state again before the journal", async (t) => {
  let reads = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/hsi-sync") {
        reads += 1;
        return reads === 1 ? fail(500, {}) : ok(SYNC);
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#hsi-retry-btn"), "click");
  await drain();
  assert.deepEqual(f.order, [STATE_URL, "/api/admin/hsi-sync", STATE_URL, "/api/admin/hsi-sync"]);
  assert.match(f.host.innerHTML, /Ana Pérez/);
});

test("a state answer that lands after dispose paints nothing and asks nothing", async (t) => {
  const gate = deferred();
  const f = fixture({ routes: defaultRoutes(), state: () => gate.promise }, t);
  const dispose = f.run();
  await drain(8);
  assert.equal(f.stateRequests.length, 1);
  const before = f.host.innerHTML;
  dispose();
  assert.equal(f.stateRequests[0].options.signal.aborted, true);
  gate.resolve(response(200, STATE_EXTENSION));
  await drain(8);
  assert.equal(f.host.innerHTML, before);
  assert.equal(f.requests.length, 0);
});

test("a center's name and number are escaped", async (t) => {
  const hostile = { ...STATE_EXTENSION, centros: [{ slug: "a b", nombre: "<img src=x>", institucion: "<b>1</b>" }] };
  const f = fixture({ routes: defaultRoutes(), state: () => ok(hostile) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.doesNotMatch(f.host.innerHTML, /<img|<b>1/);
  assert.match(f.host.innerHTML, /href="\/admin\/centers\?item=a%20b"/);
});
