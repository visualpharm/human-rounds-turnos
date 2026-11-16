// Whole-module runtime tests for the native Appearance controller. The real
// static/platform/admin-appearance.mjs is imported and executed end to end;
// only the external boundaries are faked: the session and i18n dependencies
// arrive as data: URLs through window.SANA_STATIC (exactly how importStatic
// loads them), the DOM host and status element are Element stubs, fetch
// routes by "METHOD url" and FileReader/confirm/location are recorded fakes.
// No production logic is copied here, and an unrouted request fails the test
// loudly instead of passing silently.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { mount } from "../static/platform/admin-appearance.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function drain(turns = 16) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }
function malformed(status = 200) { return { status, ok: status >= 200 && status < 300, json: () => Promise.reject(new Error("body not JSON")) }; }
const ok = body => Promise.resolve(response(200, body));
const fail = (status, body = {}) => Promise.resolve(response(status, body));

// Minimal element stub: setting innerHTML registers every id="..." tag it
// contains as a queryable child with its attributes parsed (value, class,
// data-*), which covers everything the delegated handlers read. closest()
// supports the "#id" and "[data-x]" selectors the controller delegates on.
class Element {
  constructor(id = "") {
    this.id = id; this.isConnected = true; this._html = "";
    this.listeners = new Map(); this.children = new Map();
    this.attrs = Object.create(null);
    this.value = ""; this.disabled = false; this.hidden = false;
    this.className = ""; this.textContent = ""; this.style = {}; this.files = null;
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
      el.checked = /(?:^|\s)checked(?:\s|$)/.test(attrs);
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
  querySelectorAll() { return []; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  getAttribute(name) { const v = this.attrs[name]; return v === undefined ? null : v; }
  setAttribute(name, v) { this.attrs[name] = String(v); }
  appendChild(el) { el.parentNode = this; this.children.set(el.id, el); return el; }
  matches(sel) {
    if (sel[0] === "#") return this.id === sel.slice(1);
    if (sel[0] === "[") return Object.prototype.hasOwnProperty.call(this.attrs, sel.slice(1, -1));
    return false;
  }
  closest(sel) { let node = this; while (node) { if (node.matches && node.matches(sel)) return node; node = node.parentNode; } return null; }
}

function styleRecord() {
  const props = new Map();
  return { props, setProperty: (k, v) => props.set(k, v), removeProperty: (k) => props.delete(k) };
}

const SESSION_SRC = "export const confirmar=(response,parsedBody,isCurrent)=>Promise.resolve((!response||!isCurrent())?false:globalThis.__appearanceConfirm(response))";
const I18N_SRC = "export const t=(key,vars,fallback)=>String(fallback).replace(/\\{(\\w+)\\}/g,(m,k)=>vars&&vars[k]!==undefined?vars[k]:m)";

function fixture({ routes, confirmar = () => false, confirm = () => false, manifest = null, search = "" }, t) {
  const host = new Element("sec-configuracion");
  const status = new Element("configuracion-status");
  status.hidden = true;
  const head = new Element("head");
  let styleEl = null;
  const doc = {
    head,
    readyState: "complete",
    documentElement: { style: styleRecord() },
    getElementById(id) { return id === "cfg-style" && styleEl ? styleEl : null; },
    createElement(tag) { styleEl = new Element(tag); return styleEl; },
  };
  const readers = [];
  const requests = [];
  const reloads = [];
  const replaces = [];
  const confirmCalls = [];
  const keys = ["window", "fetch", "location", "history", "document", "confirm", "FileReader", "__appearanceConfirm"];
  const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.__appearanceConfirm = response => { confirmCalls.push(response); return confirmar(response); };
  globalThis.document = doc;
  globalThis.confirm = confirm;
  globalThis.FileReader = class {
    constructor() { readers.push(this); this.result = null; this.onload = null; this.onerror = null; }
    readAsDataURL(file) { this._file = file; this.result = "data:image/png;base64,QUJD"; }
  };
  globalThis.location = { href: "https://example.test/admin/appearance", pathname: "/admin/appearance", search, reload() { reloads.push(1); } };
  globalThis.history = { replaceState(...args) { replaces.push(args); } };
  const windowBase = { SANA_STATIC: manifest || {
    "platform/admin-session.mjs": "data:text/javascript," + encodeURIComponent(SESSION_SRC),
    "platform/i18n.mjs": "data:text/javascript," + encodeURIComponent(I18N_SRC),
  } };
  globalThis.window = windowBase;
  globalThis.fetch = (url, options = {}) => {
    const method = options.method || "GET";
    requests.push({ url, method, options });
    const next = routes(url, method, requests.length);
    assert.ok(next && typeof next.then === "function", "unrouted Appearance fetch: " + method + " " + url);
    return next;
  };
  t.after(() => {
    for (const [key, descriptor] of Object.entries(previous)) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key];
  });
  const disposes = [];
  const run = opts => { const dispose = mount(host, Object.assign({ status }, opts)); disposes.push(dispose); return dispose; };
  const fire = (el, type) => {
    const fn = host.listeners.get(type);
    assert.ok(typeof fn === "function", "no " + type + " listener on host");
    fn({ target: el, preventDefault() {} });
  };
  const card = (attr, value) => { const el = new Element(""); el.attrs[attr] = value; el.parentNode = host; return el; };
  return {
    host, status, doc, head, readers, requests, reloads, replaces, confirmCalls,
    windowRef: windowBase, run, fire, card,
    styleProps: doc.documentElement.style.props,
    child(id) { return host.children.get(id) || null; },
    posts(url) { return requests.filter(r => r.method !== "GET" && r.url === url); },
    gets() { return requests.filter(r => r.method === "GET").map(r => r.url); },
  };
}

// Payloads shaped exactly as the five owning routers serve them.
const THEME_GET = { tema: "pinamar", themes: [
  { id: "pinamar", label: "Pinamar", desc: "Verde" },
  { id: "humanrounds", label: "Human Rounds", desc: "Terracota" },
] };
const AUTH_GET = { auth_mode: "password", modes: [
  { id: "password", label: "Contraseña", desc: "Usuario y clave" },
  { id: "renaper", label: "RENAPER", desc: "Foto del DNI" },
], renaper_provider: "real", renaper_conectado: true };
const TS_GET = { on: true, options: [
  { id: "on", label: "Activada", desc: "Con verificación" },
  { id: "off", label: "Desactivada", desc: "Sin verificación" },
], configurado: true, host_dev: false, forzado_off_env: false };
const BRANDING_GET = { color: null, logo: false, color_efectivo: "#0d7d48", derivados: null };
const VOICE_GET = { model: "fal-ai/wizper", fal_configurado: true };
const DERIVADOS = { accent: "#a15030", accent_hover: "#b26441", accent_soft: "#f4e0d7", accent_bd: "#a15030" };

const GET_PATHS = [
  "/api/admin/theme", "/api/admin/auth-mode", "/api/admin/turnstile",
  "/api/admin/branding", "/api/admin/chat-voice",
];

test("a malformed successful theme collection cannot blank the surviving settings", async t => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/theme": ok({ tema: "pinamar", themes: {} }),
  }) }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  assert.ok(f.child("cfg-voice-model"), "the independently valid voice block remains usable");
  assert.ok(f.child("cfg-mk-hex"), "the independently valid branding block remains usable");
  assert.equal(f.status.hidden, false, "invalid theme data exposes load recovery");
  assert.equal(f.host.innerHTML.includes("data-tema="), false);
});

for (const [path, key, payload] of [
  ["theme", "themes", THEME_GET],
  ["auth-mode", "modes", AUTH_GET],
  ["turnstile", "options", TS_GET],
]) {
  for (const [shape, body] of [
    ["array payload", []],
    ["null option", { ...payload, [key]: [null] }],
    ["missing option label", { ...payload, [key]: [{ id: "unexpected", desc: "Invalid option" }] }],
  ]) {
    test(`${path}: ${shape} is a block-local load failure`, async t => {
      const f = fixture({ routes: defaultRoutes({ [`GET /api/admin/${path}`]: ok(body) }) }, t);
      const dispose = f.run();
      t.after(dispose);
      await dispose.ready;
      assert.ok(f.child("cfg-voice-model"));
      assert.ok(f.child("cfg-mk-hex"));
      assert.equal(f.status.hidden, false);
    });
  }
}

function defaultRoutes(over = {}) {
  const table = {
    "GET /api/admin/theme": ok(THEME_GET),
    "GET /api/admin/auth-mode": ok(AUTH_GET),
    "GET /api/admin/turnstile": ok(TS_GET),
    "GET /api/admin/branding": ok(BRANDING_GET),
    "GET /api/admin/chat-voice": ok(VOICE_GET),
    "POST /api/admin/theme": ok({ ok: true, tema: "humanrounds" }),
    "POST /api/admin/auth-mode": ok({ ok: true, auth_mode: "renaper" }),
    "POST /api/admin/turnstile": ok({ ok: true, on: true }),
    "POST /api/admin/branding": ok({ ok: true, color: "#a15030", derivados: DERIVADOS }),
    "POST /api/admin/branding/logo": ok({ ok: true, logo: true }),
    "DELETE /api/admin/branding/logo": ok({ ok: true, logo: false }),
  };
  return (url, method) => {
    const key = method + " " + url;
    if (Object.prototype.hasOwnProperty.call(over, key)) return over[key];
    const hit = table[key];
    assert.ok(hit, "unexpected Appearance fetch: " + key);
    return hit;
  };
}

test("import is inert and a mount reads exactly the five endpoints, in render order, only after both dependencies settle", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  assert.equal(f.requests.length, 0, "no fetch before the dependency promises settle");
  await dispose.ready;
  assert.deepEqual(f.gets(), GET_PATHS, "the five initial GETs, in render order");
  assert.equal(f.requests.filter(r => r.method !== "GET").length, 0, "no writes on load");
  // Every block painted, fixed order, active card marked, status stays clear.
  const order = ["Tema visual", "Ingreso de los pacientes", "Verificación anti-bot", "Dictado del chat", "Color y logo"]
    .map(title => f.host.innerHTML.indexOf(title));
  order.forEach((index, i) => assert.ok(index >= 0, "block " + i + " rendered"));
  assert.deepEqual(order, [...order].sort((a, b) => a - b), "blocks render in the fixed order");
  assert.match(f.host.innerHTML, /data-tema="pinamar"[^>]*aria-pressed="true"/);
  assert.match(f.host.innerHTML, /data-tema="humanrounds"[^>]*aria-pressed="false"/);
  assert.match(f.host.innerHTML, /data-auth="password"[^>]*aria-pressed="true"/);
  assert.ok(f.status.hidden, "no load-error line on full success");
  assert.equal(f.status.textContent, "");
  // Implementation globals: the module reads window, it never grows one.
  assert.deepEqual(Object.keys(f.windowRef), ["SANA_STATIC"]);
});

test("a mount without a status element paints the load line in-band; zero blocks is never a blank page", async t => {
  const f = fixture({ routes: defaultRoutes(Object.fromEntries(GET_PATHS.map(p => ["GET " + p, fail(404)]))) }, t);
  const dispose = f.run({ status: null });
  await dispose.ready;
  assert.equal(f.host.innerHTML.match(/class="cfg-h2"/g), null, "no block headers");
  assert.match(f.host.innerHTML, /class="cfg-msg err cfg-load-msg"/);
  assert.match(f.host.innerHTML, /Algunas opciones no se pudieron cargar\. Recargá la página\./);
});

test("a 404 removes only its own block, silently; the survivors render with no reload line", async t => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/chat-voice": fail(404) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.ok(!f.host.innerHTML.includes("Dictado del chat"), "voice block absent (module off)");
  for (const title of ["Tema visual", "Ingreso de los pacientes", "Verificación anti-bot", "Color y logo"]) {
    assert.ok(f.host.innerHTML.includes(title), title + " survives");
  }
  assert.ok(f.status.hidden, "a module-off 404 is not a failure");
});

test("malformed JSON and a rejected fetch each drop only their block and show one reload line", async t => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/turnstile": Promise.resolve(malformed(200)),
    "GET /api/admin/branding": Promise.reject(new Error("network down")),
  }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.ok(!f.host.innerHTML.includes("Verificación anti-bot"), "unparsable block dropped");
  assert.ok(!f.host.innerHTML.includes("Color y logo"), "network-failed block dropped");
  for (const title of ["Tema visual", "Ingreso de los pacientes", "Dictado del chat"]) {
    assert.ok(f.host.innerHTML.includes(title), title + " survives");
  }
  assert.equal(f.status.textContent, "Algunas opciones no se pudieron cargar. Recargá la página.");
  assert.ok(f.status.className.includes("cfg-status--error"));
});

test("a missing manifest dependency fails closed: onError runs, nothing is ever fetched", async t => {
  const f = fixture({
    routes: defaultRoutes(),
    manifest: { "platform/admin-session.mjs": "data:text/javascript," + encodeURIComponent(SESSION_SRC) },
  }, t);
  let failed = null;
  const dispose = f.run({ onError: err => { failed = err; } });
  await dispose.ready;
  assert.ok(failed && /Missing Appearance dependency/.test(failed.message), "onError carries the missing dependency");
  assert.equal(f.requests.length, 0, "no read without both dependencies");
  assert.equal(f.host.innerHTML, "");
});

test("without onError the same failure surfaces on the status element", async t => {
  const f = fixture({
    routes: defaultRoutes(),
    manifest: { "platform/admin-session.mjs": "data:text/javascript," + encodeURIComponent(SESSION_SRC) },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.requests.length, 0);
  assert.equal(f.status.textContent, "Algunas opciones no se pudieron cargar. Recargá la página.");
});

test("a confirmed session expiry on a read transitions only through the current owner and paints nothing", async t => {
  const f = fixture({
    routes: defaultRoutes({ "GET /api/admin/branding": fail(401, { error: "no_autenticado" }) }),
    confirmar: () => true,
  }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.confirmCalls.length, 1, "the boundary went through the canonical session module");
  assert.equal(f.confirmCalls[0].status, 401);
  assert.equal(f.host.innerHTML, "", "a transitioned boundary paints no blocks");
  assert.ok(f.status.hidden, "and no local failure line either — the page is leaving");
});

test("an ordinary 403 is a local permission failure: blocks dropped, reload line shown, no transition", async t => {
  const f = fixture({
    routes: defaultRoutes({ "GET /api/admin/theme": fail(403, { error: "solo admin" }) }),
    confirmar: () => false,
  }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.confirmCalls.length, 1);
  assert.doesNotMatch(f.host.innerHTML, /cfg-h2|data-tema=|data-auth=|data-ts=|cfg-voice-model|cfg-mk-hex/,
    "permission failure drops every settings block");
  assert.ok(f.child("cfg-retry"), "permission failure keeps only the deliberate reload action");
  assert.equal(f.status.textContent, "Algunas opciones no se pudieron cargar. Recargá la página.");
});

test("a boundary answer arriving after disposal has no authority: the session module is never consulted", async t => {
  const gate = deferred();
  const f = fixture({
    routes: (url, method) => (method === "GET" && url === "/api/admin/theme" ? gate.promise : defaultRoutes()(url, method)),
    confirmar: () => true,
  }, t);
  const dispose = f.run();
  await drain(4);
  assert.equal(f.requests.length, 5, "all five reads fired");
  dispose();
  gate.resolve(response(401, { error: "no_autenticado" }));
  await drain(6);
  assert.equal(f.confirmCalls.length, 0, "a disposed owner's 401 is inert, not a session event");
  assert.equal(f.host.innerHTML, "");
  assert.equal(f.status.textContent, "");
});

test("a stale 401 that resolves after dispose never reaches confirmar and never renders", async t => {
  const gate = deferred();
  const f = fixture({
    routes: (url, method) => (method === "GET" && url === "/api/admin/theme" ? gate.promise : defaultRoutes()(url, method)),
    confirmar: () => true,
  }, t);
  const dispose = f.run();
  await drain(4);
  assert.equal(f.requests.length, 5, "all five reads fired");
  dispose();
  gate.resolve(response(401, { error: "no_autenticado" }));
  await drain(6);
  assert.equal(f.confirmCalls.length, 0, "a disposed owner's 401 is inert, not a session event");
  assert.equal(f.host.innerHTML, "");
  assert.equal(f.status.textContent, "");
});

test("theme save: failure says the server reason and never reloads; the deliberate retry reloads exactly once", async t => {
  let themePosts = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "POST" && url === "/api/admin/theme") {
        themePosts += 1;
        return themePosts === 1 ? fail(400, { error: "tema inválido" }) : ok({ ok: true, tema: "humanrounds" });
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 1);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/theme")[0].options.body), { tema: "humanrounds" });
  assert.equal(f.reloads.length, 0, "a failed theme save never reloads");
  const msg = f.child("cfg-msg");
  assert.equal(msg.textContent, "tema inválido");
  assert.ok(msg.className.includes("err"));
  // The deliberate retry is answered with success: exactly one reload.
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 2, "retry after failure POSTs again");
  assert.equal(f.reloads.length, 1);
});

test("failed saves allow a deliberate retry on every operation", async t => {
  const base = defaultRoutes({ "POST /api/admin/theme": fail(503, { error: "base de datos no disponible" }) });
  // Fresh rejection per call: a pre-built rejected promise would sit unhandled
  // from fixture time until the click and fail the test as an unhandledRejection.
  const f = fixture({
    routes: (url, method) => (method === "POST" && url === "/api/admin/turnstile")
      ? Promise.reject(new Error("offline"))
      : base(url, method),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 1);
  assert.equal(f.child("cfg-msg").textContent, "base de datos no disponible");
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 2, "theme retry after failure POSTs again");
  // Turnstile network failure → retry line → retry POSTs again.
  f.child("cfg-ts-check").checked = false;
  f.fire(f.child("cfg-ts-check"), "change");
  await drain();
  assert.equal(f.child("cfg-ts-msg").textContent, "No se pudo cambiar la verificación. Reintentá.");
  assert.equal(f.child("cfg-ts-check").checked, true, "failure restores saved state");
  f.child("cfg-ts-check").checked = false;
  f.fire(f.child("cfg-ts-check"), "change");
  await drain();
  assert.equal(f.posts("/api/admin/turnstile").length, 2);
});

test("duplicate saves are serialized per operation: a second click while pending never POSTs", async t => {
  const gate = deferred();
  const f = fixture({ routes: defaultRoutes({ "POST /api/admin/theme": gate.promise }) }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.card("data-tema", "humanrounds"), "click");
  f.fire(f.card("data-tema", "humanrounds"), "click");
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 1, "one in-flight save per operation");
  gate.resolve(response(200, { ok: true, tema: "humanrounds" }));
  await drain();
  assert.equal(f.reloads.length, 1, "the settled save reloads once");
  // Clicking the already-active card is a no-op even when idle.
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 1);
});

test("a late save answer after disposal never reloads and never touches styles", async t => {
  const themeGate = deferred();
  const colorGate = deferred();
  const f = fixture({
    routes: (url, method) => {
      if (method === "POST" && url === "/api/admin/theme") return themeGate.promise;
      if (method === "POST" && url === "/api/admin/branding") return colorGate.promise;
      return defaultRoutes()(url, method);
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.card("data-tema", "humanrounds"), "click");
  f.fire(f.child("cfg-mk-save"), "click");
  await drain(2);
  dispose();
  themeGate.resolve(response(200, { ok: true, tema: "humanrounds" }));
  colorGate.resolve(response(200, { ok: true, color: "#a15030", derivados: DERIVADOS }));
  await drain(6);
  assert.equal(f.reloads.length, 0, "an old owner's success never reloads");
  assert.equal(f.styleProps.size, 0, "an old owner's derived CSS never lands");
});

test("auth save repaints its own block and preserves the unsaved color draft", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  f.child("cfg-mk-hex").value = "#a15030";
  f.fire(f.card("data-auth", "renaper"), "click");
  await drain();
  const authPost = f.posts("/api/admin/auth-mode");
  assert.equal(authPost.length, 1);
  assert.deepEqual(JSON.parse(authPost[0].options.body), { auth_mode: "renaper" });
  assert.match(f.host.innerHTML, /data-auth="renaper"[^>]*aria-pressed="true"/);
  const authMsg = f.child("cfg-auth-msg");
  assert.equal(authMsg.textContent, "Listo — el ingreso de los pacientes ahora es “Identidad digital (RENAPER)”.");
  assert.ok(!authMsg.className.includes("err"));
  assert.equal(f.child("cfg-mk-hex").value, "#a15030", "unsaved hex draft survives another block's save");
});

test("dictation shows its state, not the service key: no editable field, no save, no POST", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  // 2026-11-08 review: a service key is not a word for the desk — the block
  // says the feature is on, the server still pins the model.
  assert.match(f.host.innerHTML, /id="cfg-voice-model"[^>]*>Activo</,
    "dictation renders its state");
  assert.doesNotMatch(f.host.innerHTML, /fal-ai|cfg-model--locked/, "no service key on screen");
  assert.doesNotMatch(f.host.innerHTML, /TURNOS_TURNSTILE_/, "no variable names on screen");
  assert.doesNotMatch(f.host.innerHTML, /cfg-voice-save/, "nothing to save: the server pins the model");
  assert.doesNotMatch(f.host.innerHTML, /<input[^>]*cfg-voice-model/);
  assert.equal(f.posts("/api/admin/chat-voice").length, 0);
});

// 2026-11-08 review D8: a feature with no key or no agreement is one line —
// its name, «Sin configurar» and a link to Ajustes › Integraciones — never
// a checkbox, a selectable card or a note explaining why it does nothing.
test("a feature without its key or agreement is one «Sin configurar» line linking to Integraciones", async t => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/auth-mode": ok({ ...AUTH_GET, renaper_conectado: false }),
    "GET /api/admin/turnstile": ok({ ...TS_GET, configurado: false }),
    "GET /api/admin/chat-voice": ok({ ...VOICE_GET, fal_configurado: false }),
  }) }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  const html = f.host.innerHTML;
  for (const block of ["auth", "ts", "voice"]) {
    const line = html.match(new RegExp('<h2 class="cfg-h2 cfg-off" data-off="' + block + '">.*?</h2>'));
    assert.ok(line, block + " collapses to one line");
    assert.match(line[0], /Sin configurar · <a href="\/admin\/settings\?view=integrations">Integraciones<\/a>/);
  }
  assert.doesNotMatch(html, /data-auth=/, "no sign-in card to pick: RENAPER cannot run");
  assert.doesNotMatch(html, /cfg-ts-check/, "no anti-bot checkbox without a key");
  assert.doesNotMatch(html, /cfg-voice-model/, "no dictation model without a key");
  assert.doesNotMatch(html, /cfg-note[^>]*>[^<]*(convenio|clave de Cloudflare|clave de Fal)/, "no explaining note");
  assert.match(html, /cfg-mk-hex/, "the rest of the screen stays");
});

// ui review 08.11: on a phone the state wraps whole under the name, the name
// does not break at its hyphen; an empty status line leaves no band of space.
test("off line and empty status: the CSS that lays them out", () => {
  const src = readFileSync(new URL("../static/platform/admin-appearance.mjs", import.meta.url), "utf8");
  assert.match(src, /"\.cfg-off\{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:12px;row-gap:4px;\}"/);
  assert.match(src, /"\.cfg-off-state\{font-weight:400;letter-spacing:0;white-space:nowrap;\}"/);
  assert.match(src, /"\.cfg-msg:empty\{min-height:0;margin-top:0;\}"/);
});

test("an installation already on RENAPER keeps both cards to switch back, even without the agreement", async t => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/auth-mode": ok({ ...AUTH_GET, auth_mode: "renaper", renaper_conectado: false }),
  }) }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  assert.match(f.host.innerHTML, /data-auth="password"[^>]*aria-pressed="false"/);
  assert.match(f.host.innerHTML, /data-auth="renaper"[^>]*aria-pressed="true"/);
  assert.doesNotMatch(f.host.innerHTML, /data-off="auth"/);
});

test("color save applies the server's derived CSS live and repaints the reset action", async t => {
  let brandingPosts = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "POST" && url === "/api/admin/branding") {
        brandingPosts += 1;
        // First POST saves a custom color; the reset POST answers with the
        // theme color and no derived tokens, exactly as branding.py does.
        return brandingPosts === 1
          ? ok({ ok: true, color: "#a15030", derivados: DERIVADOS })
          : ok({ ok: true, color: null, derivados: null });
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.child("cfg-mk-hex").value = "#a15030";
  f.fire(f.child("cfg-mk-save"), "click");
  await drain();
  const colorPost = f.posts("/api/admin/branding");
  assert.equal(colorPost.length, 1);
  assert.deepEqual(JSON.parse(colorPost[0].options.body), { color: "#a15030" });
  assert.equal(f.styleProps.get("--accent"), "#a15030");
  assert.equal(f.styleProps.get("--accent-hover"), "#b26441");
  assert.equal(f.styleProps.get("--accent-soft"), "#f4e0d7");
  assert.equal(f.styleProps.get("--accent-bd"), "#a15030");
  assert.match(f.host.innerHTML, /id="cfg-mk-reset"/, "a saved custom color offers the reset link");
  assert.equal(f.child("cfg-mk-msg").textContent, "Listo — el sitio entero ya usa este color.");
  // Reset back to the theme color removes the derived overrides.
  f.fire(f.child("cfg-mk-reset"), "click");
  await drain();
  assert.equal(f.styleProps.size, 0, "reset clears every derived token");
  assert.equal(f.child("cfg-mk-msg").textContent, "Listo — volvió el color del tema.");
  assert.equal(f.child("cfg-mk-hex").value, "#0d7d48", "reset must not restore the removed custom draft");
  assert.equal(f.child("cfg-mk-color").value, "#0d7d48");
  dispose();
  await f.run().ready;
  assert.equal(f.child("cfg-mk-hex").value, "#0d7d48", "reset draft stays corrected across remount");
});

test("a confirmed color save preserves text typed while that save was pending", async t => {
  const pending = deferred();
  const f = fixture({ routes: defaultRoutes({ "POST /api/admin/branding": pending.promise }) }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  f.child("cfg-mk-hex").value = "#a15030";
  f.child("cfg-mk-color").value = "#a15030";
  f.fire(f.child("cfg-mk-save"), "click");
  f.child("cfg-mk-hex").value = "#abcdef";
  f.fire(f.child("cfg-mk-hex"), "input");
  pending.resolve(response(200, { ok: true, color: "#a15030", derivados: DERIVADOS }));
  await drain();
  assert.equal(f.child("cfg-mk-hex").value, "#abcdef");
  assert.equal(f.child("cfg-mk-color").value, "#abcdef");
  assert.equal(f.styleProps.get("--accent"), "#a15030", "only acknowledged color changes the live site");
});

test("reset reads the actual theme color even when the page opened with a custom override", async t => {
  let reads = 0;
  const f = fixture({ routes: (url, method) => {
    if (url === "/api/admin/branding" && method === "GET") {
      reads += 1;
      return ok(reads === 1
        ? { ...BRANDING_GET, color: "#123456", color_efectivo: "#123456", derivados: DERIVADOS }
        : { ...BRANDING_GET, color_efectivo: "#654321" });
    }
    if (url === "/api/admin/branding" && method === "POST") return ok({ ok: true, color: null, derivados: null });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  assert.equal(f.child("cfg-mk-hex").value, "#123456");
  f.fire(f.child("cfg-mk-reset"), "click");
  await drain();
  assert.equal(reads, 2, "reset refreshes authoritative branding instead of guessing a palette");
  assert.equal(f.child("cfg-mk-hex").value, "#654321");
  assert.equal(f.child("cfg-mk-color").value, "#654321");
});

test("a stale reset refresh cannot overwrite replacement drafts or publish CSS", async t => {
  let reads = 0;
  const late = deferred();
  const f = fixture({ routes: (url, method) => {
    if (url === "/api/admin/branding" && method === "GET" && ++reads === 2) return late.promise;
    if (url === "/api/admin/branding" && method === "POST") return ok({ ok: true, color: null, derivados: null });
    if (url === "/api/admin/branding" && method === "GET") return ok({ ...BRANDING_GET, color: "#123456" });
    return defaultRoutes()(url, method);
  } }, t);
  const first = f.run();
  await first.ready;
  f.fire(f.child("cfg-mk-reset"), "click");
  await drain(3);
  const second = f.run();
  t.after(second);
  await second.ready;
  f.child("cfg-mk-hex").value = "#abcdef";
  const html = f.host.innerHTML;
  late.resolve(response(200, { ...BRANDING_GET, color_efectivo: "#654321" }));
  await drain();
  assert.equal(f.host.innerHTML, html);
  assert.equal(f.child("cfg-mk-hex").value, "#abcdef");
  assert.equal(f.styleProps.size, 0);
});

test("a saved reset with failed refresh exposes recovery, never falsely reports an unsaved POST", async t => {
  let reads = 0;
  const f = fixture({ routes: (url, method) => {
    if (url === "/api/admin/branding" && method === "GET") return ++reads === 1
      ? ok({ ...BRANDING_GET, color: "#123456" }) : fail(503);
    if (url === "/api/admin/branding" && method === "POST") return ok({ ok: true, color: null, derivados: null });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  f.fire(f.child("cfg-mk-reset"), "click");
  await drain();
  assert.equal(f.child("cfg-mk-msg").textContent, "Algunas opciones no se pudieron cargar. Recargá la página.");
  assert.ok(f.child("cfg-mk-msg").className.includes("err"));
  assert.equal(f.posts("/api/admin/branding").length, 1);
});

test("partial read failure offers a live reload action and retires its retained callback", async t => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/theme": fail(503) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  const retry = f.child("cfg-retry");
  const callback = f.host.listeners.get("click");
  assert.ok(retry, "partial failure includes a usable action, not just instructions");
  f.fire(retry, "click");
  assert.equal(f.reloads.length, 1);
  dispose();
  callback({ target: retry, preventDefault() {} });
  assert.equal(f.reloads.length, 1, "old owner's retained reload is inert");
});

test("logo deletion does not ask for confirmation while an upload owns the operation", async t => {
  let confirmations = 0;
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/branding": ok({ ...BRANDING_GET, logo: true }),
  }), confirm: () => { confirmations += 1; return true; } }, t);
  const dispose = f.run();
  t.after(dispose);
  await dispose.ready;
  const file = f.child("cfg-mk-file");
  file.files = [{ name: "new.png" }];
  f.fire(file, "change");
  f.fire(f.child("cfg-mk-quitar"), "click");
  assert.equal(confirmations, 0, "do not solicit a confirmation that will be silently ignored");
  f.readers[0].onerror();
  f.fire(f.child("cfg-mk-quitar"), "click");
  await drain();
  assert.equal(confirmations, 1, "deliberate deletion is available again after the upload settles");
  assert.equal(f.posts("/api/admin/branding/logo").length, 1);
});

test("color save failure leaves the styles untouched and says the server's reason", async t => {
  const f = fixture({ routes: defaultRoutes({ "POST /api/admin/branding": fail(400, { error: "color inválido" }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("cfg-mk-save"), "click");
  await drain();
  assert.equal(f.styleProps.size, 0);
  assert.equal(f.child("cfg-mk-msg").textContent, "color inválido");
  assert.ok(f.child("cfg-mk-msg").className.includes("err"));
});

test("logo upload: FileReader completion POSTs the payload, success repaints with the remove action", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const file = f.child("cfg-mk-file");
  file.files = [{ name: "logo.png" }];
  f.fire(file, "change");
  await drain(2);
  assert.equal(f.readers.length, 1, "one reader per picked file");
  f.readers[0].onload();
  await drain();
  const logoPost = f.posts("/api/admin/branding/logo");
  assert.equal(logoPost.length, 1);
  assert.deepEqual(JSON.parse(logoPost[0].options.body), { data_b64: "QUJD" });
  assert.match(f.host.innerHTML, /id="cfg-mk-quitar"/, "an uploaded logo offers the remove link");
  assert.equal(f.child("cfg-mk-logo-msg").textContent, "Listo — el logo nuevo ya está en todo el sitio.");
});

test("a retained FileReader completing after disposal never POSTs", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const file = f.child("cfg-mk-file");
  file.files = [{ name: "logo.png" }];
  f.fire(file, "change");
  await drain(2);
  dispose();
  f.readers[0].onload();
  await drain();
  assert.equal(f.posts("/api/admin/branding/logo").length, 0, "the old owner's reader is inert");
  assert.equal(f.requests.filter(r => r.method !== "GET").length, 0);
});

test("a failed read shows the file error and a re-picked file uploads (retry, not a stuck guard)", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const file = f.child("cfg-mk-file");
  file.files = [{ name: "logo.png" }];
  f.fire(file, "change");
  await drain(2);
  f.readers[0].onerror();
  await drain();
  const msg = f.child("cfg-mk-logo-msg");
  assert.equal(msg.textContent, "No se pudo leer el archivo.");
  assert.ok(msg.className.includes("err"));
  file.files = [{ name: "logo2.png" }];
  f.fire(file, "change");
  await drain(2);
  assert.equal(f.readers.length, 2);
  f.readers[1].onload();
  await drain();
  assert.equal(f.posts("/api/admin/branding/logo").length, 1, "deliberate retry after a read failure");
});

test("logo delete needs the explicit confirmation; success removes it, failure keeps the retry open", async t => {
  let confirmed = false;
  const f = fixture({
    routes: defaultRoutes({ "DELETE /api/admin/branding/logo": fail(503, { error: "base de datos no disponible" }) }),
    confirm: () => confirmed,
  }, t);
  const dispose = f.run();
  await dispose.ready;
  // Paint a custom logo first so the remove action exists.
  const file = f.child("cfg-mk-file");
  file.files = [{ name: "logo.png" }];
  f.fire(file, "change");
  await drain(2);
  f.readers[0].onload();
  await drain();
  assert.match(f.host.innerHTML, /id="cfg-mk-quitar"/);
  // Cancelled confirmation: nothing is sent.
  f.fire(f.child("cfg-mk-quitar"), "click");
  await drain();
  assert.equal(f.requests.filter(r => r.method === "DELETE").length, 0);
  // Confirmed but failing delete: error message, then a confirmed retry.
  confirmed = true;
  f.fire(f.child("cfg-mk-quitar"), "click");
  await drain();
  assert.equal(f.requests.filter(r => r.method === "DELETE").length, 1);
  assert.equal(f.child("cfg-mk-logo-msg").textContent, "base de datos no disponible");
});

test("dispose drops the delegated handlers and a remount owns the host alone", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const first = f.run();
  await first.ready;
  const retainedClick = f.host.listeners.get("click");
  first();
  assert.equal(f.host.listeners.size, 0, "dispose removes the delegated handlers");
  const retainedCard = f.card("data-tema", "humanrounds");
  retainedCard.parentNode = null;
  retainedClick({ target: retainedCard, preventDefault() {} });
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 0, "a retained old handler never writes");

  const second = f.run();
  await second.ready;
  assert.equal(f.gets().length, 10, "the remount reads its own five endpoints");
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/theme").length, 1, "one listener set: one click, one POST");
  assert.equal(f.reloads.length, 1);
  assert.equal(f.head.children.size, 1, "the shared stylesheet is injected once, not per mount");
});

test("unsaved drafts survive a full remount", async t => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const first = f.run();
  await first.ready;
  f.child("cfg-mk-hex").value = "#1b2b3d";
  first();
  const second = f.run();
  await second.ready;
  assert.equal(f.child("cfg-mk-hex").value, "#1b2b3d");
});

test("a stray query string is canonicalized to the bare screen path once, at mount", async t => {
  const f = fixture({ routes: defaultRoutes(), search: "?next=/admin/appearance" }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.replaces.length, 1);
  assert.equal(f.replaces[0][2], "/admin/appearance");
});

test("mount throws without a host", () => {
  assert.throws(() => mount(null), /Appearance host is required/);
});
