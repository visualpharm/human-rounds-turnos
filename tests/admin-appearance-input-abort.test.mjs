// Whole-native-module runtime tests for the two Appearance contracts the
// lifecycle suite does not pin: read cancellation (the real
// AbortController/AbortSignal plumbing behind the five admin GETs) and the
// color/hex input mirror. static/platform/admin-appearance.mjs is imported
// and executed end to end; only the external boundaries are faked, exactly
// as in tests/admin-appearance-lifecycle.test.mjs: the session and i18n
// dependencies arrive as data: URLs through window.SANA_STATIC, the host and
// status element are Element stubs, and fetch routes by "METHOD url" while
// RECORDING the signal each read carries. The fake honors the browser abort
// contract (a read rejects with its signal's reason the moment it aborts), so
// an aborted read behaves like a real one; honorAbort:false delivers a late
// response anyway, proving the controller's own ownership guard — not fetch
// rejection — is what stops the publish. No production logic is copied, and
// an unrouted request fails the test loudly instead of passing silently.
import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../static/platform/admin-appearance.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function drain(turns = 16) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }
const ok = body => Promise.resolve(response(200, body));

// Minimal element stub (same shape as the lifecycle suite's): setting
// innerHTML registers every id="..." tag it contains as a queryable child
// with its value/class attributes parsed, which covers everything the
// delegated input handler reads and writes.
class Element {
  constructor(id = "") {
    this.id = id; this.isConnected = true; this._html = "";
    this.listeners = new Map(); this.children = new Map();
    this.attrs = Object.create(null);
    this.value = ""; this.disabled = false; this.hidden = false;
    this.className = ""; this.textContent = ""; this.style = {};
    this.parentNode = null;
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = String(html);
    this.children = new Map();
    const tagRe = /<([a-z]+)\b([^>]*)>/g;
    let m;
    while ((m = tagRe.exec(this._html))) {
      const attrs = m[2];
      const idMatch = /\bid="([^"]+)"/.exec(attrs);
      if (!idMatch) continue;
      const el = new Element(idMatch[1]);
      const pairRe = /([a-zA-Z-]+)="([^"]*)"/g;
      let p;
      while ((p = pairRe.exec(attrs))) {
        el.attrs[p[1]] = p[2];
        if (p[1] === "value") el.value = p[2];
        if (p[1] === "class") el.className = p[2];
      }
      el.parentNode = this;
      this.children.set(el.id, el);
    }
  }
  querySelector(selector) { return selector[0] === "#" ? (this.children.get(selector.slice(1)) || null) : null; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  appendChild(el) { el.parentNode = this; this.children.set(el.id, el); return el; }
}

function styleRecord() {
  const props = new Map();
  return { props, setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k) };
}

const SESSION_SRC = "export const confirmar=(response,parsedBody,isCurrent)=>Promise.resolve((!response||!isCurrent())?false:globalThis.__appearanceConfirm(response))";
const I18N_SRC = "export const t=(key,vars,fallback)=>String(fallback).replace(/\\{(\\w+)\\}/g,(m,k)=>vars&&vars[k]!==undefined?vars[k]:m)";

function fixture({ routes, honorAbort = true, confirmar = () => false }, t) {
  const host = new Element("sec-configuracion");
  const status = new Element("configuracion-status");
  status.hidden = true;
  const head = new Element("head");
  let styleEl = null;
  const doc = {
    head,
    documentElement: { style: styleRecord() },
    getElementById(id) { return id === "cfg-style" && styleEl ? styleEl : null; },
    createElement(tag) { styleEl = new Element(tag); return styleEl; },
  };
  const requests = [];
  // Positive control for the abort plumbing: each entry is a pending read
  // whose OWN fetch observed its OWN signal abort — proof that the signals
  // recorded below are the live ones wired into the requests, and that the
  // controller disposal aborts is the one the reads carry.
  const abortLog = [];
  const confirmCalls = [];
  const reloads = [];
  const keys = ["window", "fetch", "location", "history", "document", "confirm", "__appearanceConfirm"];
  const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.__appearanceConfirm = response => { confirmCalls.push(response); return confirmar(response); };
  globalThis.document = doc;
  globalThis.confirm = () => false;
  globalThis.location = { href: "https://example.test/admin/appearance", pathname: "/admin/appearance", search: "", reload() { reloads.push(1); } };
  globalThis.history = { replaceState() {} };
  const windowBase = { SANA_STATIC: {
    "platform/admin-session.mjs": "data:text/javascript," + encodeURIComponent(SESSION_SRC),
    "platform/i18n.mjs": "data:text/javascript," + encodeURIComponent(I18N_SRC),
  } };
  globalThis.window = windowBase;
  globalThis.fetch = (url, options = {}) => {
    const method = options.method || "GET";
    const signal = options.signal || null;
    requests.push({ url, method, options, signal });
    const next = routes(url, method, requests.length);
    assert.ok(next && typeof next.then === "function", "unrouted Appearance fetch: " + method + " " + url);
    if (!signal || !honorAbort) return next;
    if (signal.aborted) { abortLog.push(url); return Promise.reject(signal.reason); }
    return new Promise((resolve, reject) => {
      const onAbort = () => { abortLog.push(url); reject(signal.reason); };
      signal.addEventListener("abort", onAbort, { once: true });
      next.then(
        value => { signal.removeEventListener("abort", onAbort); resolve(value); },
        error => { signal.removeEventListener("abort", onAbort); reject(error); }
      );
    });
  };
  const disposes = [];
  t.after(() => {
    for (const dispose of disposes) dispose();
    for (const [key, descriptor] of Object.entries(previous)) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key];
  });
  const run = opts => { const dispose = mount(host, Object.assign({ status }, opts)); disposes.push(dispose); return dispose; };
  const fire = (el, type) => {
    const fn = host.listeners.get(type);
    assert.ok(typeof fn === "function", "no " + type + " listener on host");
    fn({ target: el, preventDefault() {} });
  };
  return {
    host, status, requests, abortLog, confirmCalls, reloads, run, fire,
    child(id) { return host.children.get(id) || null; },
    signals() { return requests.filter(r => r.method === "GET" && r.signal).map(r => r.signal); },
    gets() { return requests.filter(r => r.method === "GET").map(r => r.url); },
  };
}

// Payloads shaped exactly as the five owning admin routers serve them, in the
// controller's own read order.
const GET_PATHS = [
  "/api/admin/theme", "/api/admin/auth-mode", "/api/admin/turnstile",
  "/api/admin/branding", "/api/admin/chat-voice",
];
const GET_BODIES = {
  "/api/admin/theme": { tema: "pinamar", themes: [
    { id: "pinamar", label: "Pinamar", desc: "Verde" },
    { id: "humanrounds", label: "Human Rounds", desc: "Terracota" },
  ] },
  "/api/admin/auth-mode": { auth_mode: "password", modes: [
    { id: "password", label: "Contraseña", desc: "Usuario y clave" },
    { id: "renaper", label: "RENAPER", desc: "Foto del DNI" },
  ], renaper_provider: "real", renaper_conectado: true },
  "/api/admin/turnstile": { on: true, options: [
    { id: "on", label: "Activada", desc: "Con verificación" },
    { id: "off", label: "Desactivada", desc: "Sin verificación" },
  ], configurado: true, host_dev: false, forzado_off_env: false },
  "/api/admin/branding": { color: null, logo: false, color_efectivo: "#0d7d48", derivados: null },
  "/api/admin/chat-voice": { model: "fal-ai/wizper", fal_configurado: true },
};
// A fully valid collection whose label can only reach the DOM through an
// owner that should have no authority: if a guard is missing, this paints.
const STALE_THEME = { tema: "humanrounds", themes: [
  { id: "humanrounds", label: "STALE OWNER THEME", desc: "only a dead owner could paint this" },
] };

function defaultRoutes() {
  return (url, method) => {
    assert.equal(method, "GET", "unexpected Appearance write: " + method + " " + url);
    assert.ok(Object.prototype.hasOwnProperty.call(GET_BODIES, url), "unexpected Appearance fetch: " + method + " " + url);
    return ok(GET_BODIES[url]);
  };
}

// First GET per path hangs on a controllable deferred; every later GET (the
// replacement owner's) goes straight through to the default table.
function gatedOnce() {
  const gates = new Map(GET_PATHS.map(path => [path, deferred()]));
  const taken = new Set();
  return {
    gates,
    routes(url, method) {
      if (method === "GET" && gates.has(url) && !taken.has(url)) {
        taken.add(url);
        return gates.get(url).promise;
      }
      return defaultRoutes()(url, method);
    },
  };
}

// Resolve every gate as if the server had finally answered, with the theme
// read carrying the stale-owner marker.
function releaseGates(g) {
  for (const [path, body] of Object.entries(GET_BODIES)) {
    g.gates.get(path).resolve(response(200, path === "/api/admin/theme" ? STALE_THEME : body));
  }
}

test("the five mounted reads fire on live AbortSignals; a completed load is never aborted", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await drain(4);
  assert.equal(f.gets().length, 5, "exactly the five admin reads");
  for (const r of f.requests) {
    assert.ok(r.signal instanceof AbortSignal, r.url + " carries a real AbortSignal");
    assert.equal(r.signal.aborted, false, r.url + " starts live");
    assert.equal(r.signal.reason, undefined, "no abort reason before any abort");
  }
  await dispose.ready;
  assert.equal(f.abortLog.length, 0, "a load that completes normally never aborts");
  assert.ok(f.signals().every(s => !s.aborted), "the signals stay live after their reads settle");
  assert.ok(f.child("cfg-mk-hex"), "the marca block painted — these reads drove a real render");
  assert.ok(f.status.hidden, "no load-error line");
});

test("disposal aborts every mounted read with a real AbortError; a late answer publishes nothing", async t => {
  const g = gatedOnce();
  const f = fixture({ routes: g.routes }, t);
  const dispose = f.run();
  await drain(4);
  assert.equal(f.gets().length, 5, "all five reads fired and are pending");
  assert.ok(f.signals().every(s => !s.aborted), "pending reads are live before disposal");
  dispose();
  const signals = f.signals();
  assert.equal(signals.length, 5);
  assert.ok(signals.every(s => s.aborted === true), "disposal aborts every mounted read");
  for (const s of signals) assert.equal(s.reason && s.reason.name, "AbortError", "the abort carries the browser's AbortError");
  assert.equal(f.abortLog.length, 5, "each pending fetch observed its own signal abort");
  // Late server answers are dead letters once the signal aborted.
  releaseGates(g);
  await drain(8);
  assert.equal(f.host.innerHTML, "", "a disposed owner never paints");
  assert.ok(!f.host.innerHTML.includes("STALE OWNER THEME"));
  assert.ok(f.status.hidden);
  assert.equal(f.status.textContent, "");
  assert.equal(f.confirmCalls.length, 0, "no session event from a dead owner");
  assert.equal(f.reloads.length, 0);
});

test("a read delivered after disposal has no authority: only the ownership guard stops the publish", async t => {
  const g = gatedOnce();
  const f = fixture({ routes: g.routes, honorAbort: false }, t);
  const dispose = f.run();
  await drain(4);
  assert.equal(f.gets().length, 5, "all five reads fired and are pending");
  dispose();
  assert.ok(f.signals().every(s => s.aborted === true), "the signals did abort — but this fake delivers the response anyway");
  releaseGates(g);
  await drain(10);
  assert.equal(f.host.innerHTML, "", "five valid payloads arrived after dispose and none painted");
  assert.ok(!f.host.innerHTML.includes("STALE OWNER THEME"));
  assert.ok(f.status.hidden);
  assert.equal(f.status.textContent, "");
  assert.equal(f.confirmCalls.length, 0);
});

test("replacement aborts only the previous owner's reads; the new owner reads, paints and is aborted on its own dispose", async t => {
  const g = gatedOnce();
  const f = fixture({ routes: g.routes }, t);
  const first = f.run();
  await drain(4);
  assert.equal(f.gets().length, 5, "the first owner's reads are pending");
  const firstSignals = f.signals();

  const second = f.run();
  await second.ready;
  assert.equal(f.gets().length, 10, "the replacement reads its own five endpoints");
  assert.deepEqual(f.gets(), [...GET_PATHS, ...GET_PATHS], "each owner reads the same five paths, in order");
  const secondSignals = f.signals().slice(5);
  assert.equal(secondSignals.length, 5);
  assert.ok(firstSignals.every(s => s.aborted === true), "replacement aborted the old owner's reads");
  assert.ok(secondSignals.every(s => !s.aborted), "the new owner's reads were never aborted");
  assert.equal(new Set(firstSignals).size, 1, "one owner shares one controller across its five reads");
  assert.equal(new Set(secondSignals).size, 1, "the replacement also reads on one shared controller");
  assert.notEqual(firstSignals[0], secondSignals[0], "the new owner's reads never ride the old owner's controller");
  assert.equal(f.abortLog.length, 5, "only the old owner's fetches observed an abort");
  assert.ok(f.child("cfg-mk-hex"), "the new owner painted its marca block");
  assert.ok(f.status.hidden, "the new owner loaded clean");

  // A late answer for the old owner cannot repaint the new owner's host.
  const painted = f.host.innerHTML;
  releaseGates(g);
  await drain(8);
  assert.equal(f.host.innerHTML, painted, "the new owner's render is untouched");
  assert.ok(!f.host.innerHTML.includes("STALE OWNER THEME"));
  assert.ok(secondSignals.every(s => !s.aborted), "settling the old owner's gates never aborts the new owner");

  second();
  assert.ok(secondSignals.every(s => s.aborted === true), "the new owner's own dispose aborts its reads");
  // Only reads still pending at abort time reject (the first owner's five);
  // the new owner's reads had already settled, so aborting their shared
  // signal afterwards is the browser's harmless no-op.
  assert.equal(f.abortLog.length, 5, "a settled read never observes a later abort");
  assert.equal(f.host.listeners.size, 0, "the host holds no delegated handlers after the live owner's dispose");
});

test("picker input mirrors the chosen color into the hex code field", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const color = f.child("cfg-mk-color");
  const hex = f.child("cfg-mk-hex");
  assert.ok(color && hex, "the marca block painted both controls");
  assert.equal(color.value, "#0d7d48");
  color.value = "#123456";
  f.fire(color, "input");
  assert.equal(hex.value, "#123456", "the picker drives the hex field");
  color.value = "#abcdef";
  f.fire(color, "input");
  assert.equal(hex.value, "#abcdef", "every picker input event mirrors");
});

test("a valid bare or mixed-case hex normalizes into the picker", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const color = f.child("cfg-mk-color");
  const hex = f.child("cfg-mk-hex");
  assert.equal(color.value, "#0d7d48");
  for (const [typed, expected] of [
    ["a15030", "#a15030"],
    ["A15030", "#a15030"],
    ["1B2B3d", "#1b2b3d"],
    ["#9C9C9C", "#9c9c9c"],
    [" a15030 ", "#a15030"],
  ]) {
    hex.value = typed;
    f.fire(hex, "input");
    assert.equal(color.value, expected, JSON.stringify(typed) + " normalizes into the picker");
  }
});

test("an invalid hex never clobbers the picker's color", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const color = f.child("cfg-mk-color");
  const hex = f.child("cfg-mk-hex");
  assert.equal(color.value, "#0d7d48");
  for (const typed of ["zzzzzz", "12345", "1234567", "", "#1b2b"]) {
    hex.value = typed;
    f.fire(hex, "input");
    assert.equal(color.value, "#0d7d48", JSON.stringify(typed) + " leaves the picker untouched");
  }
});

test("a retained input handler from a disposed owner is inert", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const color = f.child("cfg-mk-color");
  const hex = f.child("cfg-mk-hex");
  const retained = f.host.listeners.get("input");
  assert.equal(typeof retained, "function", "the host holds the delegated input handler");
  // Positive control: while mounted, the same handler mirrors the picker.
  color.value = "#123456";
  f.fire(color, "input");
  assert.equal(hex.value, "#123456", "the handler works while its owner is live");

  dispose();
  assert.equal(f.host.listeners.has("input"), false, "the host no longer holds the handler");
  color.value = "#654321";
  retained({ target: color, preventDefault() {} });
  assert.equal(hex.value, "#123456", "the old picker handler is inert after disposal");
  hex.value = "#987654";
  retained({ target: hex, preventDefault() {} });
  assert.equal(color.value, "#654321", "the old hex handler is inert after disposal");
});
