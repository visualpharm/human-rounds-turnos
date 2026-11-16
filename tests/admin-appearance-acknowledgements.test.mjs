// Whole-native-module runtime tests for the successful-acknowledgement
// contract on the five Appearance mutations the voice and colour suites do
// not pin: theme, sign-in mode, anti-bot toggle, logo upload, logo delete.
// static/platform/admin-appearance.mjs is imported and executed end to end
// under the same external-boundary fakes as tests/admin-appearance-lifecycle
// .test.mjs (session and i18n arrive as data: URLs through window.
// SANA_STATIC, the host and status element are Element stubs, fetch routes
// by "METHOD url", FileReader/confirm/location are recorded fakes). No
// production logic is copied here, and an unrouted request fails the test
// loudly instead of passing silently.
//
// The contract mirrors what the voice and colour saves already enforce: a
// 2xx answer counts as a success only when its `ok` field is the boolean
// true. A truthy stand-in (string "yes", number 1) or a missing `ok` must
// be refused — no reload, no selected/saved state painted, no success line
// shown — and the unsaved voice-model and hex drafts must survive the
// refusal untouched.
import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../static/platform/admin-appearance.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function drain(turns = 16) { for (let i = 0; i < turns; i += 1) await tick(); }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }
const ok = body => Promise.resolve(response(200, body));

// Minimal element stub (same shape as the lifecycle suite's): setting
// innerHTML registers every id="..." tag it contains as a queryable child
// with its value/class attributes parsed; closest() supports the "#id" and
// "[data-x]" selectors the delegated click handler dispatches on.
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
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  getAttribute(name) { const v = this.attrs[name]; return v === undefined ? null : v; }
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

function fixture({ routes, confirm = () => false }, t) {
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
  const readers = [];
  const requests = [];
  const reloads = [];
  const confirmCalls = [];
  const keys = ["window", "fetch", "location", "history", "document", "confirm", "FileReader", "__appearanceConfirm"];
  const previous = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.__appearanceConfirm = () => false;
  globalThis.document = doc;
  globalThis.confirm = confirm;
  globalThis.FileReader = class {
    constructor() { readers.push(this); this.result = null; this.onload = null; this.onerror = null; }
    readAsDataURL(file) { this._file = file; this.result = "data:image/png;base64,QUJD"; }
  };
  globalThis.location = { href: "https://example.test/admin/appearance", pathname: "/admin/appearance", search: "", reload() { reloads.push(1); } };
  globalThis.history = { replaceState() {} };
  const windowBase = { SANA_STATIC: {
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
  const card = (attr, value) => { const el = new Element(""); el.attrs[attr] = value; el.parentNode = host; return el; };
  return {
    host, status, doc, head, readers, requests, reloads, confirmCalls,
    run, fire, card,
    child(id) { return host.children.get(id) || null; },
    posts(url) { return requests.filter(r => r.method !== "GET" && r.url === url); },
  };
}

// Payloads shaped exactly as the five owning admin routers serve them.
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

// Reads only: every write is routed explicitly by the test that performs it,
// so a stray mutation anywhere fails the test loudly.
function defaultRoutes(over = {}) {
  const table = {
    "GET /api/admin/theme": ok(THEME_GET),
    "GET /api/admin/auth-mode": ok(AUTH_GET),
    "GET /api/admin/turnstile": ok(TS_GET),
    "GET /api/admin/branding": ok(BRANDING_GET),
    "GET /api/admin/chat-voice": ok(VOICE_GET),
  };
  return (url, method) => {
    const key = method + " " + url;
    if (Object.prototype.hasOwnProperty.call(over, key)) return over[key];
    const hit = table[key];
    assert.ok(hit, "unexpected Appearance fetch: " + key);
    return hit;
  };
}

// The rendered option card is the one place "selected" is painted:
// '<button ... data-auth="renaper" aria-pressed="true">'. Option cards carry
// no id, so the assertion reads the host's painted markup.
function assertCard(f, attr, id, pressed) {
  const marker = attr + '="' + id + '" aria-pressed="' + (pressed ? "true" : "false") + '"';
  assert.equal(f.host.innerHTML.includes(marker), true,
    attr + '="' + id + '" must render aria-pressed="' + (pressed ? "true" : "false"));
}

function assertDraftsKept(f) {
  assert.equal(f.child("cfg-mk-hex").value, "#123456", "the unsaved hex draft survives");
}

const OPS = [
  {
    name: "theme",
    write: "POST /api/admin/theme",
    ackFields: { tema: "humanrounds" },
    msgId: "cfg-msg",
    act: f => f.fire(f.card("data-tema", "humanrounds"), "click"),
    control(f) {
      assert.equal(f.reloads.length, 1, "a boolean ok:true acknowledgement reloads exactly once");
      assert.deepEqual(JSON.parse(f.posts("/api/admin/theme")[0].options.body), { tema: "humanrounds" });
    },
    refused(f) {
      assert.equal(f.reloads.length, 0, "no reload without a boolean ok:true acknowledgement");
    },
  },
  {
    name: "auth-mode",
    write: "POST /api/admin/auth-mode",
    ackFields: { auth_mode: "renaper" },
    msgId: "cfg-auth-msg",
    act: f => f.fire(f.card("data-auth", "renaper"), "click"),
    control(f) {
      assertCard(f, "data-auth", "renaper", true);
      assertCard(f, "data-auth", "password", false);
      assertAccepted(f, "cfg-auth-msg");
    },
    refused(f) {
      assertCard(f, "data-auth", "renaper", false);
      assertCard(f, "data-auth", "password", true);
    },
  },
  {
    name: "turnstile",
    write: "POST /api/admin/turnstile",
    ackFields: { on: false },
    msgId: "cfg-ts-msg",
    act: f => { const box = f.child("cfg-ts-check"); box.checked = false; f.fire(box, "change"); },
    control(f) {
      assert.equal(f.child("cfg-ts-check").checked, false);
      assertAccepted(f, "cfg-ts-msg");
    },
    refused(f) {
      assert.equal(f.child("cfg-ts-check").checked, true);
    },
  },
  {
    name: "logo upload",
    write: "POST /api/admin/branding/logo",
    ackFields: { logo: true },
    msgId: "cfg-mk-logo-msg",
    act: async f => {
      const file = f.child("cfg-mk-file");
      file.files = [{ name: "logo.png" }];
      f.fire(file, "change");
      await drain(2);
      assert.equal(f.readers.length, 1, "one reader per picked file");
      f.readers[0].onload();
    },
    control(f) {
      assert.match(f.host.innerHTML, /id="cfg-mk-quitar"/, "an acknowledged upload repaints with the remove action");
      assertAccepted(f, "cfg-mk-logo-msg");
    },
    refused(f) {
      assert.doesNotMatch(f.host.innerHTML, /id="cfg-mk-quitar"/, "an unacknowledged upload paints no saved logo state");
    },
  },
  {
    name: "logo delete",
    write: "DELETE /api/admin/branding/logo",
    ackFields: { logo: false },
    msgId: "cfg-mk-logo-msg",
    routes: { "GET /api/admin/branding": ok({ ...BRANDING_GET, logo: true }) },
    confirm: () => true,
    act: f => f.fire(f.child("cfg-mk-quitar"), "click"),
    control(f) {
      assert.doesNotMatch(f.host.innerHTML, /id="cfg-mk-quitar"/, "an acknowledged delete repaints without the remove action");
      assertAccepted(f, "cfg-mk-logo-msg");
    },
    refused(f) {
      assert.match(f.host.innerHTML, /id="cfg-mk-quitar"/, "an unacknowledged delete keeps the saved logo state");
    },
  },
];

function assertAccepted(f, msgId) {
  const msg = f.child(msgId);
  assert.ok(msg, "the block's message line is painted");
  assert.equal(msg.className.includes("err"), false, "a boolean ok:true acknowledgement is not an error");
  assert.ok(msg.textContent.includes("Listo"), "the success line is shown");
}

async function setup(op, t, writeResponse) {
  const f = fixture({
    routes: defaultRoutes(Object.assign({}, op.routes, { [op.write]: writeResponse })),
    confirm: op.confirm,
  }, t);
  const dispose = f.run();
  await dispose.ready;
  // An unsaved edit in an unrelated block: no acknowledgement outcome may
  // cost it.
  f.child("cfg-mk-hex").value = "#123456";
  await op.act(f);
  await drain();
  assert.equal(f.posts(op.write.slice(op.write.indexOf(" ") + 1)).length, 1,
    "the mutation itself was sent exactly once");
  return f;
}

for (const op of OPS) {
  test(`${op.name}: a boolean ok:true acknowledgement is a success`, async t => {
    const f = await setup(op, t, ok({ ok: true, ...op.ackFields }));
    op.control(f);
    assertDraftsKept(f);
  });

  for (const [shape, ack] of [
    ["a truthy string ok", { ok: "yes" }],
    ["a truthy number ok", { ok: 1 }],
    ["a missing ok", {}],
  ]) {
    test(`${op.name} refuses ${shape} acknowledgement`, async t => {
      const f = await setup(op, t, ok({ ...ack, ...op.ackFields }));
      op.refused(f);
      const msg = f.child(op.msgId);
      assert.ok(msg, "the block's message line is painted");
      assert.ok(msg.className.includes("err"), "the refusal is reported as a failure");
      assert.notEqual(msg.textContent, "", "the refusal says something");
      assert.equal(msg.textContent.includes("Listo"), false, "the refusal never claims success");
      assertDraftsKept(f);
    });
  }
}

test("a refused theme acknowledgement leaves the current theme selected after a later repaint", async t => {
  const f = fixture({ routes: defaultRoutes({
    "POST /api/admin/theme": ok({ tema: "humanrounds" }),
    "POST /api/admin/auth-mode": ok({ ok: true, auth_mode: "renaper" }),
  }) }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.card("data-tema", "humanrounds"), "click");
  await drain();
  assert.equal(f.reloads.length, 0);
  assert.ok(f.child("cfg-msg").className.includes("err"));
  // A different, validly acknowledged save forces a full render: the
  // unacknowledged theme answer must not have been adopted as selected.
  f.fire(f.card("data-auth", "renaper"), "click");
  await drain();
  assertCard(f, "data-tema", "pinamar", true);
  assertCard(f, "data-tema", "humanrounds", false);
});
