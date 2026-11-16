// Lifecycle ownership tests execute the native alerts module itself
// (static/platform/admin-alerts.mjs). No controller markup builder is copied
// or re-implemented here: every assertion reads what the real module wrote
// into its own nodes, the real listener registries, the real interval
// registry, the real fetch calls, or the real AbortController signals.
import assert from "node:assert/strict";
import test from "node:test";
import { mountAlerts } from "../static/platform/admin-alerts.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 12) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const moduleUrl = code => "data:text/javascript," + encodeURIComponent(code);
// File-global: a per-fixture counter would mint the same gated-i18n URL
// twice and Node would hand the second fixture the first one's cached module.
let urlCounter = 0;

// Boundary doubles: i18n interpolates {n} like the real catalog, format and
// dates prefix their outputs so a row proves the canonical module was used.
const I18N_ES = moduleUrl(
  "export const t=(key,vars,fallback)=>{const s=String(fallback||key);"
  + "return (vars&&vars.n!==undefined)?s.split(\"{n}\").join(String(vars.n)):s}");
const I18N_EN = moduleUrl(
  "export const t=(key,vars,fallback)=>{const s=\"EN::\"+String(fallback||key);"
  + "return (vars&&vars.n!==undefined)?s.split(\"{n}\").join(String(vars.n)):s}");
const SESSION = moduleUrl(
  "export const confirmar=(res,body,isCurrent)=>globalThis.__alertsConfirm(res,isCurrent)");
const FORMAT = moduleUrl(
  "export const telWa=v=>v?\"WA:\"+v:\"\";"
  + "export const telE164=v=>v?\"E164:\"+v:\"\";"
  + "export const nombre=v=>v?\"NM:\"+v:\"\"");
const DATES = moduleUrl("export const relativo=()=>'hace un rato'");

class El {
  constructor(id = "") {
    this.id = id;
    this.tagName = "";
    this.className = "";
    this.isConnected = true;
    this.hidden = false;
    this.disabled = false;
    this.type = "";
    this.innerHTML = "";
    this.textContent = "";
    this.attrs = new Map();
    this.children = [];
    this.parent = null;
    this.listeners = new Map();
  }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  remove() {
    if (this.parent) {
      const at = this.parent.children.indexOf(this);
      if (at !== -1) this.parent.children.splice(at, 1);
      this.parent = null;
    }
  }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  contains(node) { for (let cur = node; cur; cur = cur.parent) if (cur === this) return true; return false; }
  closest(selector) {
    for (let cur = this; cur; cur = cur.parent) {
      if (selector === "[data-ack]" && cur.attrs.has("data-ack")) return cur;
      if (selector === ".alert-bell-wrap" && String(cur.className).split(/\s+/).includes("alert-bell-wrap")) return cur;
    }
    return null;
  }
  querySelectorAll(selector) {
    if (typeof selector !== "string" || selector.charCodeAt(0) !== 46 /* . */) return [];
    const cls = selector.slice(1);
    const out = [];
    const visit = node => {
      for (const child of node.children) {
        if (String(child.className).split(/\s+/).includes(cls)) out.push(child);
        visit(child);
      }
    };
    visit(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const response = (status = 200, body = {}) =>
  ({ status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) });

const ALERTA = {
  id: "i-1", triage: "rojo", paciente: "Alejo Varela", dni: "30111111",
  contacto: "+542235555555", servicio: "Clínica", medico: "Dra. Sosa",
  fecha: "2026-09-07T09:00:00-03:00", request_id: "r-9",
};

function fixture({ i18n = "es", dates = true, format = true, session = true } = {}, t) {
  const host = new El();
  host.className = "adm-h1row";
  const root = new El("root");
  root.appendChild(host);
  const docListeners = new Map();
  const nodes = new Map();
  const intervals = new Map();
  let timerSeq = 1;
  const requests = [];
  const strays = [];
  const toasts = [];
  const errors = [];
  let queue = [];
  let confirmarResult = () => Promise.resolve(false);
  const confirmarCalls = [];

  const staticMap = {};
  let gate = null;
  if (i18n === "es") staticMap["platform/i18n.mjs"] = I18N_ES;
  else if (i18n === "en") staticMap["platform/i18n.mjs"] = I18N_EN;
  else if (i18n === "gated") {
    gate = deferred();
    globalThis.__alertsI18nGate = gate.promise;
    staticMap["platform/i18n.mjs"] = moduleUrl(
      "export const t=(key,vars,fallback)=>String(fallback||key);"
      + "await globalThis.__alertsI18nGate;/*" + (++urlCounter) + "*/");
  }
  if (format) staticMap["platform/format.mjs"] = FORMAT;
  if (dates) staticMap["platform/dates.mjs"] = DATES;
  if (session) staticMap["platform/admin-session.mjs"] = SESSION;

  const previous = Object.fromEntries(
    ["window", "document", "fetch", "setInterval", "clearInterval", "console", "__alertsConfirm", "__alertsI18nGate"]
      .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const previousConsoleError = console.error;

  globalThis.document = {
    readyState: "complete",
    head: { appendChild(node) { if (node.id) nodes.set(node.id, node); } },
    createElement: () => new El(),
    getElementById: id => nodes.get(id) || null,
    querySelector: selector => (selector === ".adm-h1row" ? host : null),
    querySelectorAll: () => [],
    addEventListener(name, fn) {
      const list = docListeners.get(name) || [];
      list.push(fn); docListeners.set(name, list);
    },
    removeEventListener(name, fn) {
      const list = docListeners.get(name) || [];
      const at = list.indexOf(fn);
      if (at !== -1) list.splice(at, 1);
    },
  };
  globalThis.window = {
    SANA_STATIC: staticMap,
    toast: m => toasts.push(String(m)),
  };
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url, options });
    const next = queue.shift();
    if (!next) {
      strays.push(String(url));
      return Promise.reject(new Error("unexpected alerts request: " + url));
    }
    return typeof next === "function" ? next() : next;
  };
  globalThis.setInterval = (fn, delay) => {
    const id = timerSeq++;
    intervals.set(id, { fn, delay });
    return id;
  };
  globalThis.clearInterval = id => { intervals.delete(id); };
  globalThis.__alertsConfirm = (res, isCurrent) => {
    confirmarCalls.push({ status: res && res.status, isCurrent });
    return Promise.resolve(confirmarResult(res, isCurrent));
  };
  const realError = console.error;
  console.error = (...args) => { errors.push(args.map(String).join(" ")); realError(...args); };

  const disposes = [];
  t.after(() => {
    disposes.forEach(dispose => { try { dispose(); } catch (err) { /* already dead */ } });
    console.error = previousConsoleError;
    delete globalThis.__alertsI18nGate;
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });

  const findIn = (node, id) => {
    for (const child of node.children) {
      if (child.id === id) return child;
      const found = findIn(child, id);
      if (found) return found;
    }
    return null;
  };
  const wrapOf = rootEl => (rootEl || root).querySelector(".alert-bell-wrap");

  return {
    host, root, gate, nodes, intervals, requests, strays, toasts, errors,
    confirmarCalls,
    set confirmar(fn) { confirmarResult = fn; },
    enqueue(...next) { queue = queue.concat(next); },
    fireInterval() { for (const { fn } of [...intervals.values()]) fn(); },
    mount(opts = {}) {
      const dispose = mountAlerts(Object.assign({ root }, opts));
      disposes.push(dispose);
      return dispose;
    },
    bell(rootEl) { return findIn(wrapOf(rootEl) || wrapOf(root), "alert-bell") || findIn((rootEl || root).querySelector(".adm-h1row") || host, "alert-bell"); },
    badge(rootEl) { return findIn(wrapOf(rootEl) || wrapOf(root), "alert-bell-badge"); },
    panelBody(rootEl) { return findIn(wrapOf(rootEl) || wrapOf(root), "alert-bell-body"); },
    panel(rootEl) { return findIn(wrapOf(rootEl) || wrapOf(root), "alert-bell-panel"); },
    bellClick(rootEl) {
      const bellNode = this.bell(rootEl);
      assert.ok(bellNode, "the bell exists");
      bellNode.listeners.get("click")({ stopPropagation() {} });
    },
    docClick(target) {
      const event = { target, closest: target && target.closest ? target.closest.bind(target) : () => null };
      for (const fn of [...(docListeners.get("click") || [])]) fn(event);
    },
    escape() {
      for (const fn of [...(docListeners.get("keydown") || [])]) fn({ key: "Escape" });
    },
    ackTarget(id, rootEl) {
      const button = new El();
      button.attrs.set("data-ack", id);
      button.parent = wrapOf(rootEl);
      assert.ok(button.parent, "the wrap exists to own this ack button");
      return button;
    },
    docListenerCount(name) { return (docListeners.get(name) || []).length; },
    newRoot() {
      const otherHost = new El();
      otherHost.className = "adm-h1row";
      const otherRoot = new El("root-b");
      otherRoot.appendChild(otherHost);
      return { root: otherRoot, host: otherHost };
    },
    mountOn(rootEl, opts = {}) {
      const dispose = mountAlerts(Object.assign({ root: rootEl }, opts));
      disposes.push(dispose);
      return dispose;
    },
  };
}

const COUNT = body => Promise.resolve(response(200, body));

// ---- boot surface ------------------------------------------------------------

test("importing the module alone touches nothing: no boot, no window exports", async t => {
  const page = fixture({}, t);
  await import("../static/platform/admin-alerts.mjs?probe=" + Date.now() + Math.random());
  await settle();
  assert.equal(page.host.children.length, 0);
  assert.equal(page.nodes.has("alert-bell-css"), false);
  assert.equal(page.docListenerCount("click"), 0);
  assert.equal(page.docListenerCount("keydown"), 0);
  assert.equal(page.intervals.size, 0);
  assert.equal(page.requests.length, 0);
  const keysBefore = Object.keys(globalThis.window);
  const dispose = page.mount({ role: "admin" });
  page.enqueue(COUNT({ count: 0 }));
  await dispose.ready;
  await settle();
  assert.deepEqual(Object.keys(globalThis.window).sort(), keysBefore.sort(), "mount adds nothing to window");
  assert.equal(globalThis.window.sanaInitAlerts, undefined);
});

test("mountAlerts without a root fails loudly", async t => {
  fixture({}, t);
  assert.throws(() => mountAlerts({ role: "admin", root: null }), /Alerts root is required/);
});

test("denied roles never mount: no markup, no fetch, no listeners, no timers", async t => {
  for (const role of ["admisionista", "reportes", "marca", "operador", undefined]) {
    const page = fixture({}, t);
    const dispose = page.mount({ role });
    await dispose.ready;
    await settle();
    assert.equal(page.host.children.length, 0, role + " leaves the header untouched");
    assert.equal(page.requests.length, 0, role + " fetches nothing");
    assert.equal(page.strays.length, 0);
    assert.equal(page.docListenerCount("click"), 0);
    assert.equal(page.docListenerCount("keydown"), 0);
    assert.equal(page.intervals.size, 0);
    assert.equal(page.nodes.has("alert-bell-css"), false);
  }
});

test("a hidden bell takes its wrap out of the title row (no stray gap at the right edge)", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 0 }));
  await page.mount({ role: "admin" }).ready;
  await settle();
  const styleTag = page.nodes.get("alert-bell-css");
  assert.ok(styleTag, "the bell injected its stylesheet");
  assert.match(styleTag.textContent, /\.alert-bell-wrap:has\(> \.alert-bell\[hidden\]\)\{display:none;\}/);
});

test("a denied-role mount also removes a live owner's markup on the same root", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 3 }));
  await page.mount({ role: "admin" }).ready;
  await settle();
  assert.ok(page.bell(), "the admin owner built the bell");
  page.mount({ role: "reportes" });
  await settle();
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 0, "the wrap is gone");
  assert.equal(page.intervals.size, 0, "the poll timer is cleared");
  assert.equal(page.docListenerCount("click"), 0);
  assert.equal(page.docListenerCount("keydown"), 0);
});

test("no .adm-h1row on the root owns nothing and fetches nothing", async t => {
  const page = fixture({}, t);
  const bare = new El("bare");
  const dispose = page.mountOn(bare, { role: "admin" });
  await dispose.ready;
  await settle();
  assert.equal(page.requests.length, 0);
  assert.equal(page.intervals.size, 0);
  assert.equal(page.docListenerCount("click"), 0);
  assert.equal(bare.children.length, 0);
});

// ---- dependency readiness ------------------------------------------------------

test("nothing mounts or fetches until the i18n dependency actually settles", async t => {
  const page = fixture({ i18n: "gated" }, t);
  const dispose = page.mount({ role: "admin" });
  await settle();
  assert.equal(page.host.children.length, 0, "a pending dependency leaves the header untouched");
  assert.equal(page.requests.length, 0, "no poll before translation readiness");
  assert.equal(page.intervals.size, 0);
  page.gate.resolve();
  page.enqueue(COUNT({ count: 1 }));
  await dispose.ready;
  await settle();
  assert.ok(page.bell(), "the bell lands after i18n resolves");
  assert.equal(page.badge().textContent, "1");
});

test("a missing dependency fails through onError as a dead owner, never a silent widget", async t => {
  const page = fixture({ format: false }, t);
  const errors = [];
  const dispose = page.mount({ role: "admin", onError: error => errors.push(error) });
  await assert.rejects(dispose.ready, /platform\/format\.mjs/);
  await settle();
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /platform\/format\.mjs/);
  assert.equal(page.host.children.length, 0);
  assert.equal(page.nodes.has("alert-bell-css"), false);
  assert.equal(page.requests.length, 0);
  assert.equal(page.intervals.size, 0);
  assert.equal(page.docListenerCount("click"), 0);
  assert.match(page.errors.join(" "), /admin-alerts/);
});

test("a missing dates module degrades: rows render without the relative-time segment", async t => {
  const page = fixture({ dates: false }, t);
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(200, { items: [ALERTA] })));
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  assert.match(page.panelBody().innerHTML, /Clínica · Dra\. Sosa/);
  assert.doesNotMatch(page.panelBody().innerHTML, /hace un rato/);
});

test("translations resolved at mount time reach the markup, not frozen fallbacks", async t => {
  const page = fixture({ i18n: "en" }, t);
  page.enqueue(COUNT({ count: 2 }), Promise.resolve(response(200, { items: [ALERTA] })));
  await page.mount({ role: "admin" }).ready;
  await settle();
  assert.equal(page.bell().getAttribute("aria-label"), "EN::2 alertas de triage rojo sin ver");
  page.bellClick();
  await settle();
  assert.match(page.panelBody().innerHTML, /EN::Rojo<\/span>/);
  assert.match(page.panelBody().innerHTML, /EN::Ver entrevista/);
  assert.match(page.panelBody().innerHTML, /EN::Marcar como visto/);
});

// ---- count, list and ack business semantics ------------------------------------

test("a successful count shows the bell with the badge and the counted aria label", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 3 }));
  await page.mount({ role: "admin" }).ready;
  await settle();
  const bell = page.bell();
  const badge = page.badge();
  assert.equal(badge.textContent, "3");
  assert.equal(badge.hidden, false);
  assert.equal(bell.hidden, false);
  assert.equal(bell.getAttribute("aria-label"), "3 alertas de triage rojo sin ver");
  assert.equal(bell.getAttribute("aria-expanded"), "false");
  assert.equal(page.requests[0].url, "/api/admin/intake/alerts/count");
});

test("count 0 keeps the bell hidden with the plain aria label; 120 renders 99+", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 0 }));
  await page.mount({ role: "medico" }).ready;
  await settle();
  assert.equal(page.bell().hidden, true);
  assert.equal(page.badge().hidden, true);
  assert.equal(page.bell().getAttribute("aria-label"), "Alertas de triage rojo");
  page.enqueue(COUNT({ count: 120 }));
  page.fireInterval();
  await settle();
  assert.equal(page.badge().textContent, "99+");
  assert.equal(page.bell().hidden, false);
});

test("opening the panel loads the full list and renders the canonical row", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(200, { items: [ALERTA] })));
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  assert.equal(page.requests[1].url, "/api/admin/intake/alerts", "the full list is fetched only on open");
  const html = page.panelBody().innerHTML;
  assert.match(html, /class="queue-triage queue-triage--rojo">Rojo<\/span>/);
  assert.match(html, /NM:Alejo Varela/);
  assert.match(html, /Clínica · Dra\. Sosa · hace un rato/);
  assert.match(html, /href="https:\/\/wa\.me\/WA:\+542235555555"/);
  assert.match(html, /href="tel:E164:\+542235555555">Llamar</);
  assert.match(html, /href="\/admin\/consult\?interview=i-1">Ver entrevista/);
  assert.match(html, /data-ack="i-1">Marcar como visto/);
  assert.equal(page.panel().hidden, false);
  assert.equal(page.bell().getAttribute("aria-expanded"), "true");
});

test("an empty list renders the empty copy, and a patient without name falls back", async t => {
  const page = fixture({}, t);
  const sinNombre = Object.assign({}, ALERTA, { id: "i-2", paciente: "" });
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(200, { items: [sinNombre] })));
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  assert.match(page.panelBody().innerHTML, /Sin nombre/);
  page.bellClick(); // close
  page.enqueue(Promise.resolve(response(200, { items: [] })));
  page.bellClick(); // reopen refetches
  await settle();
  assert.equal(page.requests[2].url, "/api/admin/intake/alerts");
  assert.match(page.panelBody().innerHTML, /No hay alertas de triage rojo pendientes\./);
});

test("outside click and Escape close the open panel", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(200, { items: [ALERTA] })));
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  page.docClick(new El("afuera"));
  assert.equal(page.panel().hidden, true);
  assert.equal(page.bell().getAttribute("aria-expanded"), "false");
  page.enqueue(Promise.resolve(response(200, { items: [ALERTA] })));
  page.bellClick();
  await settle();
  page.escape();
  assert.equal(page.panel().hidden, true);
});

test("a successful ack removes its row, toasts once, and refreshes the count", async t => {
  const page = fixture({}, t);
  const otra = Object.assign({}, ALERTA, { id: "i-2", paciente: "Rosa Vázquez" });
  page.enqueue(
    COUNT({ count: 2 }),
    Promise.resolve(response(200, { items: [ALERTA, otra] })),
    Promise.resolve(response(200, { ok: true })),
    COUNT({ count: 1 }),
  );
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  const target = page.ackTarget("i-1");
  page.docClick(target);
  await settle();
  assert.equal(page.requests[2].url, "/api/admin/intake/alerts/i-1/ack");
  assert.equal(page.requests[2].options.method, "POST");
  assert.equal(page.requests[2].options.credentials, "same-origin");
  const html = page.panelBody().innerHTML;
  assert.doesNotMatch(html, /data-alert-row="i-1"/);
  assert.match(html, /data-alert-row="i-2"/, "the untouched row stays");
  assert.deepEqual(page.toasts, ["Marcada como vista."]);
  assert.equal(page.badge().textContent, "1");
});

test("a duplicate ack for the same alert in flight posts exactly once", async t => {
  const page = fixture({}, t);
  const ack = deferred();
  page.enqueue(
    COUNT({ count: 2 }),
    Promise.resolve(response(200, { items: [ALERTA] })),
    ack.promise,
    COUNT({ count: 1 }),
  );
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  const target = page.ackTarget("i-1");
  page.docClick(target);
  page.docClick(target);
  page.docClick(target);
  await settle();
  assert.equal(page.requests.filter(r => r.url.endsWith("/ack")).length, 1);
  ack.resolve(response(200, { ok: true }));
  await settle();
  assert.deepEqual(page.toasts, ["Marcada como vista."]);
  assert.equal(page.requests.filter(r => r.url.endsWith("/ack")).length, 1);
  assert.equal(page.strays.length, 0);
});

test("a failed ack re-enables the button, explains, and a retry succeeds", async t => {
  const page = fixture({}, t);
  page.enqueue(
    COUNT({ count: 1 }),
    Promise.resolve(response(200, { items: [ALERTA] })),
    Promise.resolve(response(500, { error: "boom" })),
    Promise.resolve(response(200, { ok: true })),
    COUNT({ count: 0 }),
  );
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  const target = page.ackTarget("i-1");
  page.docClick(target);
  await settle();
  assert.equal(target.disabled, false, "the failed ack button is usable again");
  assert.deepEqual(page.toasts, ["No se pudo marcar. Reintentá."]);
  assert.match(page.panelBody().innerHTML, /data-alert-row="i-1"/, "the row is still there");
  page.docClick(target);
  await settle();
  assert.equal(page.requests.filter(r => r.url.endsWith("/ack")).length, 2);
  assert.deepEqual(page.toasts, ["No se pudo marcar. Reintentá.", "Marcada como vista."]);
  assert.equal(page.badge().hidden, true, "count 0 hides the badge again");
});

test("a failed list load shows the load error; reopening retries and succeeds", async t => {
  const page = fixture({}, t);
  page.enqueue(
    COUNT({ count: 1 }),
    Promise.resolve(response(500, { error: "boom" })),
  );
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  await settle();
  assert.match(page.panelBody().innerHTML, /No se pudo cargar\./);
  assert.match(page.errors.join(" "), /\[admin-alerts\] list:/);
  page.bellClick(); // close
  page.enqueue(Promise.resolve(response(200, { items: [ALERTA] })));
  page.bellClick(); // reopen refetches
  await settle();
  assert.match(page.panelBody().innerHTML, /NM:Alejo Varela/);
});

test("overlapping polls coalesce: one count in flight at a time", async t => {
  const page = fixture({}, t);
  const first = deferred();
  page.enqueue(first.promise, COUNT({ count: 2 }));
  await page.mount({ role: "admin" }).ready;
  await settle();
  assert.equal(page.requests.filter(r => r.url.endsWith("/count")).length, 1);
  page.fireInterval();
  page.fireInterval();
  page.fireInterval();
  await settle();
  assert.equal(page.requests.filter(r => r.url.endsWith("/count")).length, 1, "ticks while pending do not stack requests");
  first.resolve(response(200, { count: 1 }));
  await settle();
  page.fireInterval();
  await settle();
  assert.equal(page.requests.filter(r => r.url.endsWith("/count")).length, 2, "the next tick polls again once free");
  assert.equal(page.badge().textContent, "2");
});

test("reopening the panel while a list load is pending does not double-fetch", async t => {
  const page = fixture({}, t);
  const list = deferred();
  page.enqueue(COUNT({ count: 1 }), list.promise);
  await page.mount({ role: "admin" }).ready;
  await settle();
  page.bellClick();
  page.bellClick();
  page.bellClick();
  await settle();
  assert.equal(page.requests.filter(r => r.url === "/api/admin/intake/alerts").length, 1);
  list.resolve(response(200, { items: [ALERTA] }));
  await settle();
  assert.match(page.panelBody().innerHTML, /NM:Alejo Varela/);
});

// ---- disposal, replacement and late callbacks -----------------------------------

test("malformed 200 acknowledgement never removes a real alert or claims success", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(200, { items: [ALERTA] })),
    Promise.resolve(response(200, {})));
  await page.mount({ role: "admin" }).ready; await settle();
  page.bellClick(); await settle();
  const button = page.ackTarget("i-1"); page.docClick(button); await settle();
  assert.match(page.panelBody().innerHTML, /data-alert-row="i-1"/);
  assert.deepEqual(page.toasts, ["No se pudo marcar. Reintentá."]);
  assert.equal(button.disabled, false);
  assert.equal(page.requests.length, 3, "invalid ack must not trigger a success refresh");
});

test("session dependency rejection fails readiness and leaves no live polling owner", async t => {
  const page = fixture({ session: false }, t);
  const dispose = page.mount({ role: "admin" });
  await assert.rejects(dispose.ready, /platform\/admin-session\.mjs/);
  await settle();
  assert.equal(page.requests.length, 0);
  assert.equal(page.intervals.size, 0);
  assert.equal(page.host.children.length, 0);
});

test("malformed count keeps an already visible alert count rather than claiming zero", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 2 }), Promise.resolve(response(200, {})));
  await page.mount({ role: "admin" }).ready; await settle();
  page.fireInterval(); await settle();
  assert.equal(page.bell().hidden, false);
  assert.equal(page.badge().textContent, "2");
});

test("malformed list is a load failure, not an empty clinical queue", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(200, {})));
  await page.mount({ role: "admin" }).ready; await settle();
  page.bellClick(); await settle();
  assert.match(page.panelBody().innerHTML, /No se pudo cargar/);
});

test("dispose clears every timer, listener and markup node; late ticks and clicks do nothing", async t => {
  const page = fixture({}, t);
  page.enqueue(COUNT({ count: 3 }));
  const dispose = page.mount({ role: "admin" });
  await dispose.ready;
  await settle();
  assert.ok(page.bell());
  dispose();
  dispose(); // idempotent
  assert.equal(page.intervals.size, 0, "the 60s poll interval is cleared");
  assert.equal(page.docListenerCount("click"), 0);
  assert.equal(page.docListenerCount("keydown"), 0);
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 0, "the wrap markup is removed");
  page.fireInterval();
  page.docClick(new El("afuera"));
  page.escape();
  await settle();
  assert.equal(page.strays.length, 0);
  assert.deepEqual(page.toasts, []);
});

test("dispose before the dependency settles makes no DOM mutation at all", async t => {
  const page = fixture({ i18n: "gated" }, t);
  const dispose = page.mount({ role: "admin" });
  await settle();
  dispose();
  page.gate.resolve();
  await dispose.ready;
  await settle();
  assert.equal(page.host.children.length, 0);
  assert.equal(page.nodes.has("alert-bell-css"), false);
  assert.equal(page.docListenerCount("click"), 0);
  assert.equal(page.intervals.size, 0);
  assert.equal(page.requests.length, 0);
});

test("dispose mid-count aborts the request and a late answer paints nothing", async t => {
  const page = fixture({}, t);
  const count = deferred();
  page.enqueue(count.promise);
  const dispose = page.mount({ role: "admin" });
  await dispose.ready;
  await settle();
  assert.equal(page.requests.length, 1);
  dispose();
  assert.equal(page.requests[0].options.signal.aborted, true, "the in-flight count is aborted");
  count.resolve(response(200, { count: 9 }));
  await settle();
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 0);
  assert.deepEqual(page.toasts, []);
  assert.equal(page.strays.length, 0);
});

test("dispose mid-list leaves no panel to paint and no live ack path", async t => {
  const page = fixture({}, t);
  const list = deferred();
  page.enqueue(COUNT({ count: 1 }), list.promise);
  const dispose = page.mount({ role: "admin" });
  await dispose.ready;
  await settle();
  page.bellClick();
  await settle();
  assert.match(page.panelBody().innerHTML, /Cargando…/);
  dispose();
  list.resolve(response(200, { items: [ALERTA] }));
  await settle();
  page.docClick((() => { const b = new El(); b.attrs.set("data-ack", "i-1"); return b; })());
  page.escape();
  await settle();
  assert.equal(page.requests.filter(r => r.url.endsWith("/ack")).length, 0, "a disposed owner acknowledges nothing");
  assert.deepEqual(page.toasts, []);
});

test("dispose mid-ack neither toasts nor resurrects the poll", async t => {
  const page = fixture({}, t);
  const ack = deferred();
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(200, { items: [ALERTA] })), ack.promise);
  const dispose = page.mount({ role: "admin" });
  await dispose.ready;
  await settle();
  page.bellClick();
  await settle();
  const target = page.ackTarget("i-1");
  page.docClick(target);
  await settle();
  const requestsBefore = page.requests.length;
  dispose();
  ack.resolve(response(200, { ok: true }));
  await settle();
  assert.deepEqual(page.toasts, [], "a late ack success from a dead owner stays silent");
  assert.equal(page.requests.length, requestsBefore, "no poll refresh fires for the dead owner");
});

test("a same-root remount disposes the old owner: one listener set, one wrap, no zombie paint", async t => {
  const page = fixture({}, t);
  const oldCount = deferred();
  page.enqueue(oldCount.promise, COUNT({ count: 2 }));
  const first = page.mount({ role: "admin" });
  await first.ready;
  await settle();
  assert.equal(page.docListenerCount("click"), 2, "outside-close + ack delegation");
  const second = page.mount({ role: "admin" });
  await second.ready;
  await settle();
  assert.equal(page.docListenerCount("click"), 2, "the old owner's document listeners are gone");
  assert.equal(page.docListenerCount("keydown"), 1);
  assert.equal(page.intervals.size, 1, "exactly one poll interval survives");
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 1);
  oldCount.resolve(response(200, { count: 5 }));
  await settle();
  assert.equal(page.badge().textContent, "2", "the old owner's late count cannot paint the new badge");
  // Active-owner positive control: the replacement works end to end.
  page.enqueue(Promise.resolve(response(200, { items: [ALERTA] })), Promise.resolve(response(200, { ok: true })), COUNT({ count: 0 }));
  page.bellClick();
  await settle();
  assert.match(page.panelBody().innerHTML, /NM:Alejo Varela/);
  page.docClick(page.ackTarget("i-1"));
  await settle();
  assert.deepEqual(page.toasts, ["Marcada como vista."]);
  assert.equal(page.badge().hidden, true);
  assert.equal(first === second, false);
});

test("a leftover wrap from a previous owner is replaced, never adopted", async t => {
  const page = fixture({}, t);
  const huérfano = new El();
  huérfano.className = "alert-bell-wrap";
  page.host.appendChild(huérfano);
  page.enqueue(COUNT({ count: 1 }));
  await page.mount({ role: "admin" }).ready;
  await settle();
  const wraps = page.host.querySelectorAll(".alert-bell-wrap");
  assert.equal(wraps.length, 1);
  assert.notEqual(wraps[0], huérfano);
  assert.ok(page.bell(), "the new owner built its own bell");
});

// ---- session boundary -------------------------------------------------------------

test("a confirmed 401 transitions to the session boundary and the owner shuts down quietly", async t => {
  const page = fixture({}, t);
  let isCurrentFn = null;
  page.confirmar = (res, isCurrent) => { isCurrentFn = isCurrent; return true; };
  page.enqueue(Promise.resolve(response(401)));
  const dispose = page.mount({ role: "admin" });
  await dispose.ready;
  await settle();
  assert.equal(page.confirmarCalls.length, 1);
  assert.equal(typeof page.confirmarCalls[0].isCurrent, "function");
  assert.equal(page.confirmarCalls[0].status, 401);
  assert.equal(page.intervals.size, 0, "the session transition cleared the poll");
  assert.equal(page.docListenerCount("click"), 0);
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 0);
  assert.deepEqual(page.toasts, [], "no toast from a session-expired widget");
  assert.equal(isCurrentFn(), false, "after disposal the session callback no longer claims currency");
});

test("an ordinary 403 stays silent on the poll and the next tick still loads alerts", async t => {
  const page = fixture({}, t);
  page.confirmar = () => false;
  page.enqueue(Promise.resolve(response(403)), COUNT({ count: 4 }));
  await page.mount({ role: "admin" }).ready;
  await settle();
  assert.equal(page.bell().hidden, true, "a silent poll failure shows no gate and no error surface");
  assert.deepEqual(page.toasts, []);
  assert.equal(page.intervals.size, 1, "the owner keeps its poll cadence");
  page.fireInterval();
  await settle();
  assert.equal(page.badge().textContent, "4", "an authorized success after a 403 still shows alerts");
});

test("a 401 on the list mid-panel also hands the page to the session boundary", async t => {
  const page = fixture({}, t);
  page.confirmar = () => true;
  page.enqueue(COUNT({ count: 1 }), Promise.resolve(response(401)));
  const dispose = page.mount({ role: "admin" });
  await dispose.ready;
  await settle();
  page.bellClick();
  await settle();
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 0);
  assert.equal(page.intervals.size, 0);
  assert.deepEqual(page.toasts, []);
});

// ---- independent roots --------------------------------------------------------------

test("two roots own independent widgets; disposing one leaves the other active", async t => {
  const page = fixture({}, t);
  const other = page.newRoot();
  page.enqueue(COUNT({ count: 1 }));
  const disposeA = page.mount({ role: "admin" });
  await disposeA.ready;
  await settle();
  page.enqueue(COUNT({ count: 5 }));
  const disposeB = page.mountOn(other.root, { role: "medico" });
  await disposeB.ready;
  await settle();
  assert.equal(page.badge().textContent, "1");
  assert.equal(page.badge(other.root).textContent, "5");
  disposeA();
  await settle();
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 0);
  assert.ok(other.root.querySelector(".alert-bell-wrap"), "root B's widget is untouched");
  page.enqueue(COUNT({ count: 6 }));
  page.fireInterval();
  await settle();
  assert.equal(page.badge(other.root).textContent, "6", "root B still polls after A is gone");
});
