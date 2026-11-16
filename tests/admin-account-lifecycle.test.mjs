// Runtime lifecycle tests for the native staff Account controller.
// Imports and executes static/platform/admin-account.mjs. DOM, fetch, history,
// clipboard, session/i18n/passkeys, and Response bodies are seams — no
// production functions are copied here. This is not the public patient
// platform/account.mjs surface.
import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../static/platform/admin-account.mjs";

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

const VOID_TAGS = new Set(["input", "br", "hr", "img", "meta", "link", "source", "col", "wbr"]);

function parseAttrs(raw) {
  const attrs = Object.create(null);
  const re = /([:@A-Za-z_:][\w:.-]*)(?:\s*=\s*"([^"]*)"|\s*=\s*'([^']*)')?/g;
  let m;
  while ((m = re.exec(String(raw || "")))) {
    attrs[m[1]] = m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : "");
  }
  return attrs;
}

class Elem {
  constructor(tag = "div", attrs = {}) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.attrs = Object.assign(Object.create(null), attrs);
    this.id = this.attrs.id || "";
    this.className = this.attrs["class"] || "";
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.isConnected = true;
    this.style = {};
    this.dataset = Object.create(null);
    for (const [key, value] of Object.entries(this.attrs)) {
      if (key.slice(0, 5) === "data-") {
        this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
      }
    }
    this.disabled = Object.prototype.hasOwnProperty.call(this.attrs, "disabled");
    this.hidden = Object.prototype.hasOwnProperty.call(this.attrs, "hidden");
    this._value = Object.prototype.hasOwnProperty.call(this.attrs, "value") ? this.attrs.value : "";
    this.focused = false;
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  get classList() {
    const self = this;
    const list = () => String(self.className || "").split(/\s+/).filter(Boolean);
    return {
      contains: (c) => list().includes(c),
      add(...cs) { const s = new Set(list()); cs.forEach((c) => s.add(c)); self.className = [...s].join(" "); },
      remove(...cs) { const s = new Set(list()); cs.forEach((c) => s.delete(c)); self.className = [...s].join(" "); },
      toggle(c, force) {
        const has = list().includes(c);
        const want = force === undefined ? !has : !!force;
        const s = new Set(list());
        if (want) s.add(c); else s.delete(c);
        self.className = [...s].join(" ");
        return want;
      },
    };
  }
  get value() { return this._value; }
  set value(v) { this._value = v == null ? "" : String(v); }
  get textContent() {
    return this.childNodes.map((n) => n.textContent || "").join("");
  }
  set textContent(v) {
    this.childNodes = [];
    if (v != null && String(v) !== "") {
      this.childNodes.push({ nodeType: 3, textContent: String(v), parentNode: this });
    }
  }
  get innerHTML() {
    return this.childNodes.map((node) => node.nodeType === 3 ? node.textContent : node.outerHTML).join("");
  }
  get outerHTML() {
    const attrs = Object.entries(this.attrs).map(([key, value]) =>
      " " + key + '="' + String(value).replace(/"/g, "&quot;") + '"').join("");
    const tag = this.tagName.toLowerCase();
    return "<" + tag + attrs + ">" + (VOID_TAGS.has(tag) ? "" : this.innerHTML + "</" + tag + ">");
  }
  set innerHTML(html) {
    this.childNodes = [];
    parseFragment(String(html), this);
  }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); }
  getAttribute(name) {
    const v = this.attrs[name];
    return v === undefined ? null : v;
  }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (name.slice(0, 5) === "data-") {
      this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
    }
  }
  removeAttribute(name) {
    delete this.attrs[name];
    if (name === "id") this.id = "";
    if (name === "class") this.className = "";
    if (name === "hidden") this.hidden = false;
    if (name === "disabled") this.disabled = false;
  }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  focus() { this.focused = true; }
  blur() { this.focused = false; }
  reset() {
    const walk = (node) => {
      if (node.tagName === "INPUT") node.value = "";
      (node.children || []).forEach(walk);
    };
    walk(this);
  }
  scrollIntoView() {}
  matches(sel) { return matchesSel(this, String(sel)); }
  closest(sel) {
    let node = this;
    while (node) {
      if (node.nodeType === 1 && node.matches(sel)) return node;
      node = node.parentNode;
    }
    return null;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const parts = String(sel).split(",").map((p) => p.trim()).filter(Boolean);
    const out = [];
    for (const part of parts) {
      const walk = (node) => {
        for (const child of node.children) {
          if (child.matches(part)) out.push(child);
          walk(child);
        }
      };
      walk(this);
    }
    return out;
  }
}

function parseFragment(html, parent) {
  const stack = [parent];
  const re = /<\/?([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)(\/?)>/g;
  let last = 0;
  let m;
  const pushText = (text) => {
    if (text && !/^\s*$/.test(text)) {
      stack[0].childNodes.push({ nodeType: 3, textContent: text, parentNode: stack[0] });
    }
  };
  while ((m = re.exec(html))) {
    pushText(html.slice(last, m.index));
    last = re.lastIndex;
    const [, tag, raw, selfClose] = m;
    if (m[0][1] === "/") {
      for (let i = 0; i < stack.length - 1; i += 1) {
        if (stack[i].tagName === tag.toUpperCase()) {
          stack.splice(0, i + 1);
          break;
        }
      }
      continue;
    }
    const el = new Elem(tag, parseAttrs(raw));
    stack[0].childNodes.push(el);
    el.parentNode = stack[0];
    const voidish = VOID_TAGS.has(tag.toLowerCase()) || selfClose;
    if (!voidish) stack.unshift(el);
  }
  pushText(html.slice(last));
}

function matchesSel(el, sel) {
  const m = /^([a-z][\w-]*)?(#[\w-]+)?((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/i.exec(sel.trim());
  if (!m) return false;
  const [, tag, id, classes, attrs] = m;
  if (tag && el.tagName !== tag.toUpperCase()) return false;
  if (id && el.id !== id.slice(1)) return false;
  if (classes) {
    const have = String(el.className || "").split(/\s+/);
    for (const c of classes.slice(1).split(".").filter(Boolean)) {
      if (!have.includes(c)) return false;
    }
  }
  if (attrs) {
    const re = /\[([^\=\]]+)(?:=["']?([^"'\]]*)["']?)?\]/g;
    let a;
    while ((a = re.exec(attrs))) {
      const name = a[1];
      if (a[2] === undefined) {
        if (!el.hasAttribute(name)) return false;
      } else if (el.getAttribute(name) !== a[2]) return false;
    }
  }
  return true;
}

const I18N_SRC = "export const t=(key,vars,fallback)=>{var out=String(fallback==null?key:fallback);if(!vars)return out;return out.replace(/\\{(\\w+)\\}/g,(m,k)=>vars[k]===undefined?m:String(vars[k]))}";
const SESSION_SRC = [
  "export function confirmar(response, parsedBody, isCurrent) {",
  "  var current = typeof isCurrent === 'function' ? isCurrent : null;",
  "  globalThis.__acctConfirmCalls.push({ status: response && response.status, body: parsedBody, hasPredicate: typeof isCurrent === 'function', current: current ? current() : null });",
  "  if (!response || !current) return Promise.resolve(false);",
  "  if (!current()) return Promise.resolve(false);",
  "  if (response.status === 403 && parsedBody && parsedBody.error === '2fa_requerido') {",
  "    var two = globalThis.__acctWhoamiGate || Promise.resolve(true);",
  "    return two.then(function () { if (!current()) return false; globalThis.location.href = '/admin/account?setup=2fa'; return true; });",
  "  }",
  "  if (response.status !== 401) return Promise.resolve(false);",
  "  if (!parsedBody || parsedBody.error !== 'no_autenticado') return Promise.resolve(false);",
  "  var gate = globalThis.__acctWhoamiGate || Promise.resolve(true);",
  "  return gate.then(function (confirmed) {",
  "    if (!current()) return false;",
  "    if (confirmed) globalThis.location.href = '/admin/login?next=' + encodeURIComponent((globalThis.location.pathname || '/admin/account') + (globalThis.location.search || ''));",
  "    return !!confirmed;",
  "  });",
  "}",
].join("\n");
const PASSKEYS_SRC = [
  "export function soportado(){return globalThis.__pkSupported !== false}",
  "export function registrar(){globalThis.__pkRegisterCalls.push(1); var run = globalThis.__pkRegistrar; return run ? run() : Promise.resolve()}",
  "export function cancelado(err){return !!(err && (err.cancelled || err.name === 'NotAllowedError'))}",
  "export function mensaje(clave){return String(clave || '')}",
  "export function disponibleEnEsteEquipo(){return Promise.resolve(globalThis.__pkPlatform !== false)}",
].join("\n");
const USERS_SRC = "export function refresh(){globalThis.__usersRefreshCalls.push(Date.now())}";

function dataUrl(src) {
  return "data:text/javascript," + encodeURIComponent(src);
}

const ACCOUNT = {
  usuario: "ana",
  rol: "admin",
  email: "ana@hospital.gob.ar",
  tiene_2fa: false,
  "2fa_vencido": false,
  "2fa_deadline": "2026-09-15T00:00:00",
};
const ACCOUNT_2FA = { ...ACCOUNT, tiene_2fa: true, "2fa_vencido": false, "2fa_deadline": null };
const ACCOUNT_EXPIRED = { ...ACCOUNT, tiene_2fa: false, "2fa_vencido": true };
const GA_PLAY = "https://play.google.com/store/apps/details?id=com.google.android.apps.authenticator2";
const GA_APPSTORE = "https://apps.apple.com/app/google-authenticator/id388497605";
const KEYS = {
  passkeys: [
    { credential_id: "cred-1", nombre: "Mac", creado: "2026-01-10T00:00:00", ultimo_uso: "2026-08-01T00:00:00" },
  ],
};
const PROFILE = {
  ok: true,
  item: { nombre: "Alicia Méndez", nombre_pila: "Alicia", apellido: "Méndez", especialidad: "Pediatría", telefono: "" },
  especialidades: ["Medicina general", "Pediatría"],
};
const START_A = {
  ok: true,
  qr_svg: '<svg viewBox="0 0 8 8"><rect width="8" height="8"/></svg>',
  secret: "SECRETAAA",
  setup_token: "token-A",
};
const START_B = {
  ok: true,
  qr_svg: '<svg viewBox="0 0 8 8"><circle r="3"/></svg>',
  secret: "SECRETBBB",
  setup_token: "token-B",
};

function fixture({
  routes,
  manifest = null,
  search = "",
  whoamiGate = null,
  pkSupported = true,
  pkPlatform = true,
  userAgent = "",
  maxTouchPoints = 0,
  lang = "es",
} = {}, t) {
  const host = new Elem("div", { id: "sec-cuenta" });
  host.id = "sec-cuenta";
  const note = new Elem("p", { id: "adm-2fa-note", hidden: "" });
  note.id = "adm-2fa-note";
  note.hidden = true;
  const logout = new Elem("button", { id: "adm-logout", class: "adm-logout" });
  logout.id = "adm-logout";
  const h1 = new Elem("h1", { id: "adm-h1" });
  h1.id = "adm-h1";
  const sub = new Elem("p", { id: "adm-sub" });
  sub.id = "adm-sub";
  const body = new Elem("body");
  const htmlEl = new Elem("html");
  htmlEl.setAttribute("lang", lang);
  const head = new Elem("head");
  let styleEl = null;
  const byId = new Map([
    ["sec-cuenta", host],
    ["adm-2fa-note", note],
    ["adm-logout", logout],
    ["adm-h1", h1],
    ["adm-sub", sub],
  ]);
  const docListeners = new Map();
  const winListeners = new Map();
  const copied = [];
  const assigns = [];
  const requests = [];
  const replaces = [];
  const pushes = [];
  const logs = [];

  function applyUrl(url) {
    const path = String(url || "");
    const qAt = path.indexOf("?");
    globalThis.location.pathname = qAt < 0 ? path : path.slice(0, qAt);
    globalThis.location.search = qAt < 0 ? "" : path.slice(qAt);
    globalThis.location.href = "https://example.test" + globalThis.location.pathname + globalThis.location.search;
  }

  const doc = {
    readyState: "complete",
    head,
    body,
    documentElement: htmlEl,
    getElementById(id) {
      if (id === "acct-css") return styleEl && styleEl.id === "acct-css" ? styleEl : null;
      return byId.get(id) || host.querySelector("#" + id) || null;
    },
    createElement(tag) {
      const el = new Elem(tag);
      if (tag === "style") styleEl = el;
      return el;
    },
    querySelector(sel) {
      if (sel[0] === "#") return this.getElementById(sel.slice(1));
      return host.querySelector(sel);
    },
    querySelectorAll(sel) { return host.querySelectorAll(sel); },
    addEventListener(name, fn) { docListeners.set(name, fn); },
    removeEventListener(name) { docListeners.delete(name); },
  };
  const keys = ["window", "fetch", "location", "history", "document", "navigator", "console", "addEventListener", "removeEventListener",
    "__acctConfirmCalls", "__acctWhoamiGate", "__pkSupported", "__pkPlatform", "__pkRegistrar",
    "__pkRegisterCalls", "__usersRefreshCalls", "FileReader"];
  const previous = Object.fromEntries(keys.map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.__acctConfirmCalls = [];
  globalThis.__acctWhoamiGate = whoamiGate;
  globalThis.__pkSupported = pkSupported;
  globalThis.__pkPlatform = pkPlatform;
  globalThis.__pkRegisterCalls = [];
  globalThis.__usersRefreshCalls = [];
  globalThis.__pkRegistrar = () => Promise.resolve();
  globalThis.document = doc;
  globalThis.console = { log: (...a) => logs.push(a), error: (...a) => logs.push(["error"].concat(a.map(v => v instanceof Error ? v.stack : v))), warn: () => {} };
  globalThis.location = {
    href: "https://example.test/admin/account" + search,
    pathname: "/admin/account",
    search,
    assign(url) { assigns.push(url); this.href = url; },
  };
  globalThis.history = {
    replaceState(_s, _t, url) { replaces.push(url); applyUrl(url); },
    pushState(_s, _t, url) { pushes.push(url); applyUrl(url); },
  };
  Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true, value: {
    clipboard: { writeText(text) { copied.push(text); return Promise.resolve(); } },
    userAgent,
    maxTouchPoints,
  } });
  const windowBase = {
    SANA_STATIC: manifest || {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/passkeys.mjs": dataUrl(PASSKEYS_SRC),
      "platform/admin-users.mjs": dataUrl(USERS_SRC),
    },
  };
  globalThis.window = windowBase;
  windowBase.location = globalThis.location;
  windowBase.history = globalThis.history;
  windowBase.document = doc;
  windowBase.navigator = globalThis.navigator;
  windowBase.addEventListener = (name, fn) => winListeners.set(name, fn);
  windowBase.removeEventListener = (name) => winListeners.delete(name);
  globalThis.addEventListener = windowBase.addEventListener;
  globalThis.removeEventListener = windowBase.removeEventListener;
  globalThis.fetch = (url, options = {}) => {
    const method = String(options.method || "GET").toUpperCase();
    requests.push({ url, method, options });
    const next = routes(url, method, requests.length, options);
    assert.ok(next && typeof next.then === "function", "unrouted Account fetch: " + method + " " + url);
    return next;
  };
  t.after(() => {
    for (const dispose of disposes) dispose();
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  const disposes = [];
  const run = (opts) => {
    const dispose = mount(host, opts || {});
    disposes.push(dispose);
    return dispose;
  };
  const fire = (el, type, extra = {}) => {
    const event = Object.assign({
      target: el,
      button: 0,
      preventDefault() { event.defaultPrevented = true; },
      defaultPrevented: false,
    }, extra);
    const targetFn = el && el !== host && el.listeners.get(type);
    if (typeof targetFn === "function") targetFn(event);
    const hostFn = host.listeners.get(type);
    if (typeof hostFn === "function") hostFn(event);
    if (type === "click") {
      const docFn = docListeners.get("click");
      if (typeof docFn === "function") docFn(event);
    }
    return event;
  };
  const submit = (form) => fire(form, "submit");
  return {
    host, note, logout, h1, requests, replaces, pushes, copied, assigns, logs,
    docListeners, winListeners, windowRef: windowBase, run, fire, submit,
    child(sel) { return host.querySelector(sel); },
    children(sel) { return host.querySelectorAll(sel); },
    posts(url) { return requests.filter((r) => r.method === "POST" && r.url === url); },
    gets(url) { return requests.filter((r) => r.method === "GET" && (!url || r.url === url)); },
  };
}

function defaultRoutes(over = {}) {
  const table = {
    "GET /api/admin/account": ok(ACCOUNT),
    "GET /api/admin/passkeys": ok(KEYS),
    "GET /api/admin/doctor-profile": ok(PROFILE),
    "POST /api/admin/doctor-profile": ok({ ...PROFILE, item: { ...PROFILE.item, telefono: "2254 41-2233" } }),
    "POST /api/admin/account/profile": ok({ ok: true }),
    "POST /api/admin/account/password": ok({ ok: true }),
    "POST /api/admin/account/2fa/start": ok(START_A),
    "POST /api/admin/account/2fa/confirm": ok({ ok: true }),
    "POST /api/admin/account/2fa/remove": ok({ ok: true }),
    "POST /api/admin/passkeys/delete": ok({ ok: true }),
    "POST /api/admin/logout": ok({ ok: true }),
  };
  return (url, method) => {
    const key = method + " " + url;
    if (Object.prototype.hasOwnProperty.call(over, key)) return over[key];
    const hit = table[key];
    assert.ok(hit, "unexpected Account fetch: " + key);
    return hit;
  };
}

test("import is inert: mount waits for manifested dependencies before any fetch", async (t) => {
  const i18nGate = deferred();
  globalThis.__i18nGate = i18nGate.promise;
  t.after(() => { delete globalThis.__i18nGate; });
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl("await globalThis.__i18nGate; " + I18N_SRC),
      "platform/passkeys.mjs": dataUrl(PASSKEYS_SRC),
    },
  }, t);
  const dispose = f.run();
  await drain(8);
  assert.equal(f.requests.length, 0, "held i18n import must not start Account GETs");
  i18nGate.resolve();
  await dispose.ready;
  assert.deepEqual(f.gets().map((r) => r.url).sort(), ["/api/admin/account", "/api/admin/passkeys"]);
  assert.equal(f.windowRef.sanaRouteAccount, undefined);
  assert.equal(f.windowRef.sanaAbrir2FA, undefined);
  assert.equal(f.windowRef.sanaApplyAccountSecurity, undefined);
  assert.equal(f.windowRef.SETUP, undefined);
});

test("a valid account paints email, password, 2FA, passkeys, QR instructions and expiry notice", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  t.after(dispose);
  assert.equal(f.host.innerHTML, "");
  await dispose.ready;
  assert.equal(JSON.stringify(f.logs), "[]");
  assert.match(f.h1.textContent, /Cuenta/);
  assert.match(f.host.innerHTML, /id="acct-username"[^>]*value="ana"/);
  assert.match(f.host.innerHTML, /Administrador/);
  assert.equal(f.child("#acct-email").value, "ana@hospital.gob.ar");
  assert.equal(f.child("#acct-email").getAttribute("type"), "email");
  assert.equal(f.child("#acct-pass-current").getAttribute("type"), "password");
  assert.equal(f.child("#acct-pass-new").getAttribute("autocomplete"), "new-password");
  assert.match(f.host.innerHTML, /Activá la verificación en 2 pasos/);
  assert.match(f.host.innerHTML, /data-2fa-start/);
  assert.match(f.host.innerHTML, /id="acct-keys-add"/);
  assert.match(f.host.innerHTML, /Mac/);
  assert.match(f.host.innerHTML, /data-key-del="cred-1"/);
  assert.match(f.note.innerHTML, /adm-2fa-go/);
  assert.match(f.note.innerHTML, /href="\/admin\/account\?setup=2fa"/);
  assert.equal(f.child("#mifoto-foto-av"), null);
  assert.match(globalThis.document.getElementById("acct-css").textContent, /#sec-cuenta \.btn\{min-width:44px/);
});

test("canonical ?setup=2fa is the only accepted query; extras replaceState to bare", async (t) => {
  const f = fixture({ routes: defaultRoutes(), search: "?setup=2fa&extra=1" }, t);
  await f.run().ready;
  assert.ok(f.replaces.includes("/admin/account"));
  assert.equal(globalThis.location.search, "");
});

test("canonical setup query opens TOTP with QR, Spanish steps and manual-key fallback", async (t) => {
  const f = fixture({ routes: defaultRoutes(), search: "?setup=2fa" }, t);
  await f.run().ready;
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/start").length, 1);
  assert.match(f.host.innerHTML, /class="acct-qr"/);
  assert.match(f.host.innerHTML, /role="img"/);
  assert.match(f.host.innerHTML, /Código QR para configurar la verificación en 2 pasos/);
  assert.match(f.host.innerHTML, /Instalá Google Authenticator en tu teléfono:/);
  assert.doesNotMatch(f.host.innerHTML, /Microsoft/);
  assert.equal(f.host.innerHTML.includes(GA_PLAY), true, "setup step 1 links the Play Store");
  assert.equal(f.host.innerHTML.includes(GA_APPSTORE), true, "an unknown/desktop device links both stores");
  assert.match(f.host.innerHTML, /No puedo escanear/);
  assert.match(f.host.innerHTML, /<code>SECRETAAA<\/code>/);
  assert.match(f.host.innerHTML, /data-copy-account="SECRETAAA"/);
  assert.match(f.host.innerHTML, /Ingresar una clave/);
  assert.equal(f.child("#acct-confirm-code").getAttribute("inputmode"), "numeric");
  assert.equal(f.child("#acct-confirm-code").getAttribute("autocomplete"), "one-time-code");
  assert.equal(f.child("#acct-confirm-code").getAttribute("maxlength"), "6");
  assert.equal(f.logs.some((a) => a.join(" ").includes("SECRETAAA") || a.join(" ").includes("token-A")), false);
});

for (const missing of [
  "platform/admin-session.mjs",
  "platform/i18n.mjs",
  "platform/passkeys.mjs",
]) {
  test("missing " + missing + " fails closed: onError, no fetch, host empty", async (t) => {
    const manifest = {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/passkeys.mjs": dataUrl(PASSKEYS_SRC),
    };
    delete manifest[missing];
    const f = fixture({ routes: defaultRoutes(), manifest }, t);
    let failed = null;
    const dispose = f.run({ onError: (err) => { failed = err; } });
    await dispose.ready;
    assert.ok(failed && /Missing Account dependency/.test(failed.message), missing);
    assert.equal(f.requests.length, 0);
    assert.equal(f.host.innerHTML, "");
  });
}

test("a rejected passkeys import never falls back to a local WebAuthn engine", async (t) => {
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/passkeys.mjs": "data:text/javascript,throw new Error('passkeys exploded')",
    },
  }, t);
  let failed = null;
  const dispose = f.run({ onError: (err) => { failed = err; } });
  await dispose.ready;
  assert.ok(failed);
  assert.equal(f.requests.length, 0);
  assert.equal(f.host.innerHTML, "");
  assert.equal(globalThis.__pkRegisterCalls.length, 0);
});

test("without onError a rejected session import paints one finite load-error line", async (t) => {
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/passkeys.mjs": dataUrl(PASSKEYS_SRC),
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.requests.length, 0);
  assert.match(f.host.innerHTML, /No se pudo cargar la cuenta/);
});

test("a hanging i18n import keeps ready pending and fetches nothing", async (t) => {
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl("await new Promise(()=>{}); export const t=()=>''"),
      "platform/passkeys.mjs": dataUrl(PASSKEYS_SRC),
    },
  }, t);
  const dispose = f.run();
  let done = false;
  dispose.ready.then(() => { done = true; });
  await drain(12);
  assert.equal(done, false);
  assert.equal(f.requests.length, 0);
  dispose();
});

test("same-host remount disposes the prior owner; stale account A cannot paint over B", async (t) => {
  const stale = deferred();
  const requested = deferred();
  let reads = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/account") {
        reads += 1;
        if (reads === 1) { requested.resolve(); return stale.promise; }
        return ok({ ...ACCOUNT, usuario: "bruno", email: "bruno@hospital.gob.ar" });
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  f.run();
  await requested.promise;
  const latest = f.run();
  await latest.ready;
  assert.equal(f.requests.find((r) => r.url === "/api/admin/account").options.signal.aborted, true);
  stale.resolve(response(200, ACCOUNT));
  await drain(8);
  assert.match(f.host.innerHTML, /bruno/);
  assert.doesNotMatch(f.host.innerHTML, />ana</);
  assert.equal(f.child("#acct-email").value, "bruno@hospital.gob.ar");
  f.child("#acct-email").value = "nuevo@hospital.gob.ar";
  f.submit(f.child("#acct-email-form"));
  await drain();
  assert.equal(f.posts("/api/admin/account/profile").length, 1);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/account/profile")[0].options.body), {
    email: "nuevo@hospital.gob.ar",
  });
  assert.match(f.child("#acct-email-status").textContent, /Email guardado/);
});

test("a stale GET 401 after dispose never redirects; remount still loads", async (t) => {
  const gate = deferred();
  let reads = 0;
  const f = fixture({
    whoamiGate: Promise.resolve(true),
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/account") {
        reads += 1;
        if (reads === 1) return gate.promise;
        return ok(ACCOUNT);
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const first = f.run();
  await drain(6);
  first();
  gate.resolve(response(401, { error: "no_autenticado" }));
  await drain(8);
  assert.doesNotMatch(String(globalThis.location.href), /\/admin\/login/);
  const second = f.run();
  await second.ready;
  assert.match(f.host.innerHTML, /ana/);
  assert.equal(f.child("#acct-email").getAttribute("type"), "email");
});

test("pending whoami after dispose cannot navigate; fresh owner still confirms 401", async (t) => {
  const whoami = deferred();
  const firstRead = deferred();
  let reads = 0;
  const f = fixture({
    whoamiGate: whoami.promise,
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/account") {
        reads += 1;
        if (reads === 1) return firstRead.promise;
        return fail(401, { error: "no_autenticado" });
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  const first = f.run();
  await drain(6);
  firstRead.resolve(response(401, { error: "no_autenticado" }));
  await drain(6);
  assert.equal(globalThis.__acctConfirmCalls[0].hasPredicate, true);
  assert.equal(globalThis.__acctConfirmCalls[0].current, true);
  first();
  const second = f.run();
  await drain(4);
  whoami.resolve(true);
  await second.ready;
  assert.match(String(globalThis.location.href), /\/admin\/login\?next=/);
  assert.ok(globalThis.__acctConfirmCalls.some((c) => c.current === true && c.status === 401));
});

test("ordinary account 403 stays local; confirmar received the owner predicate", async (t) => {
  const f = fixture({
    routes: defaultRoutes({ "GET /api/admin/account": fail(403, { error: "solo admin" }) }),
  }, t);
  await f.run().ready;
  assert.equal(globalThis.__acctConfirmCalls[0].hasPredicate, true);
  assert.equal(globalThis.__acctConfirmCalls[0].status, 403);
  assert.match(f.host.innerHTML, /No se pudo cargar la cuenta/);
  assert.doesNotMatch(String(globalThis.location.href), /\/admin\/login/);
});

test("expired-2FA passkeys 403 stays restricted and is not an unauthenticated 401", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "GET /api/admin/passkeys": fail(403, { error: "2fa_requerido" }),
    }),
  }, t);
  await f.run().ready;
  assert.match(f.host.innerHTML, /ana/);
  assert.equal(f.child("#acct-email").value, "ana@hospital.gob.ar");
  assert.doesNotMatch(String(globalThis.location.href), /\/admin\/login/);
  assert.equal(globalThis.location.href.includes("/admin/login"), false);
  const pkConfirms = globalThis.__acctConfirmCalls.filter((c) => c.status === 403);
  assert.equal(pkConfirms.some((c) => c.body && c.body.error === "2fa_requerido" && c.current === true), false,
    "passkeys 2fa_requerido must not take the session-repair navigation");
  assert.equal(f.child("#acct-sec-pk-status").textContent, "Primero activá la verificación en 2 pasos con la app.");
  assert.equal(f.child("#acct-sec-pk-status").classList.contains("is-error"), false, "a closed-until-repair path is not an error");
  assert.equal(f.child("#acct-keys-add").disabled, true);
  assert.equal(f.child("#acct-keys-list").innerHTML, "");
});

test("ordinary passkeys 403 does not invent a two-factor requirement or an empty key list", async (t) => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/passkeys": fail(403, { error: "forbidden" }),
  }) }, t);
  await f.run().ready;
  assert.equal(f.child("#acct-sec-pk-status").textContent, "No se pudo consultar.");
  assert.equal(f.child("#acct-keys-list").innerHTML, "");
  assert.equal(f.child("#acct-keys-add").disabled, true);
});

test("confirmed expiry on the account GET transitions through confirmar", async (t) => {
  const f = fixture({
    routes: defaultRoutes({ "GET /api/admin/account": fail(401, { error: "no_autenticado" }) }),
  }, t);
  await f.run().ready;
  assert.equal(globalThis.__acctConfirmCalls[0].hasPredicate, true);
  assert.match(globalThis.location.href, /\/admin\/login\?next=/);
});

test("optional Users refresh never blocks email save or control recovery", async (t) => {
  const usersGate = deferred();
  globalThis.__usersGate = usersGate.promise;
  t.after(() => { delete globalThis.__usersGate; });
  const f = fixture({
    routes: defaultRoutes(),
    manifest: {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC),
      "platform/i18n.mjs": dataUrl(I18N_SRC),
      "platform/passkeys.mjs": dataUrl(PASSKEYS_SRC),
      "platform/admin-users.mjs": dataUrl("await globalThis.__usersGate; export function refresh(){globalThis.__usersRefreshCalls.push(1)}"),
    },
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.child("#acct-email").value = "  nueva@hospital.gob.ar  ";
  f.submit(f.child("#acct-email-form"));
  f.submit(f.child("#acct-email-form"));
  f.submit(f.child("#acct-email-form"));
  await drain();
  assert.equal(f.posts("/api/admin/account/profile").length, 1);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/account/profile")[0].options.body), {
    email: "nueva@hospital.gob.ar",
  });
  const btn = f.child("#acct-email-form").querySelector("button[type=submit]")
    || f.host.querySelector("#acct-email-form").querySelector
    || f.children("button[type=submit]")[0];
  const submitBtn = f.child("#acct-email-form").querySelector("button[type=submit]");
  assert.equal(submitBtn.disabled, false, "email save must recover before Users refresh settles");
  assert.match(f.child("#acct-email-status").textContent, /Email guardado/);
  assert.equal(globalThis.__usersRefreshCalls.length, 0);
  usersGate.resolve();
  await drain(8);
  assert.equal(globalThis.__usersRefreshCalls.length, 1);
});

test("password mismatch stays local; matching duplicate submits post once with actual/nueva", async (t) => {
  const gate = deferred();
  const f = fixture({
    routes: defaultRoutes({ "POST /api/admin/account/password": gate.promise }),
  }, t);
  await f.run().ready;
  f.child("#acct-pass-current").value = "old";
  f.child("#acct-pass-new").value = "next-1";
  f.child("#acct-pass-confirm").value = "next-2";
  f.submit(f.child("#acct-password-form"));
  await drain();
  assert.equal(f.posts("/api/admin/account/password").length, 0);
  assert.match(f.child("#acct-pass-status").textContent, /no coinciden/);
  f.child("#acct-pass-confirm").value = "next-1";
  f.submit(f.child("#acct-password-form"));
  f.submit(f.child("#acct-password-form"));
  await drain();
  assert.equal(f.posts("/api/admin/account/password").length, 1);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/account/password")[0].options.body), {
    actual: "old",
    nueva: "next-1",
    usuario: "ana",
  });
  gate.resolve(response(200, { ok: true }));
  await drain();
  assert.match(f.child("#acct-pass-status").textContent, /Contraseña actualizada/);
  assert.equal(f.child("#acct-pass-current").value, "");
});

for (const [label, body] of [
  ["missing ok", {}],
  ["ok false", { ok: false }],
  ["ok string", { ok: "true" }],
]) {
  test("password acknowledgement " + label + " recovers the control and is not success", async (t) => {
    const f = fixture({
      routes: defaultRoutes({ "POST /api/admin/account/password": ok(body) }),
    }, t);
    await f.run().ready;
    f.child("#acct-pass-current").value = "old";
    f.child("#acct-pass-new").value = "n";
    f.child("#acct-pass-confirm").value = "n";
    f.submit(f.child("#acct-password-form"));
    await drain();
    assert.doesNotMatch(f.child("#acct-pass-status").textContent, /Contraseña actualizada/);
    assert.match(f.child("#acct-pass-status").textContent, /No se pudo/);
    assert.equal(f.child("#acct-password-form").querySelector("button[type=submit]").disabled, false);
  });
}

test("malformed JSON on email save is a current error, not a saved state", async (t) => {
  const f = fixture({
    routes: defaultRoutes({ "POST /api/admin/account/profile": Promise.resolve(malformed(200)) }),
  }, t);
  await f.run().ready;
  f.submit(f.child("#acct-email-form"));
  await drain();
  assert.doesNotMatch(f.child("#acct-email-status").textContent, /Email guardado/);
  assert.equal(f.child("#acct-email-form").querySelector("button[type=submit]").disabled, false);
});

test("duplicate 2FA starts while in flight post once; a later start owns the shown token", async (t) => {
  const first = deferred();
  const second = deferred();
  let starts = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "POST" && url === "/api/admin/account/2fa/start") {
        starts += 1;
        if (starts === 1) return first.promise;
        return second.promise;
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  f.fire(f.child("#acct-2fa-start"), "click");
  f.fire(f.child("[data-2fa-start]"), "click");
  f.fire(f.child("#acct-2fa-start"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/start").length, 1);
  first.resolve(response(200, START_A));
  await drain();
  assert.match(f.host.innerHTML, /SECRETAAA/);
  globalThis.history.pushState({}, "", "/admin/account");
  f.winListeners.get("popstate")({});
  await drain(8);
  assert.doesNotMatch(f.host.innerHTML, /SECRETAAA/);
  globalThis.history.pushState({}, "", "/admin/account?setup=2fa");
  f.winListeners.get("popstate")({});
  await drain(4);
  assert.equal(f.posts("/api/admin/account/2fa/start").length, 2);
  second.resolve(response(200, START_B));
  await drain();
  first.resolve(response(200, START_A));
  await drain();
  assert.match(f.host.innerHTML, /SECRETBBB/);
  assert.doesNotMatch(f.host.innerHTML, /SECRETAAA/);
  f.child("#acct-confirm-code").value = "123456";
  f.submit(f.child("#acct-confirm-form"));
  await drain();
  assert.deepEqual(JSON.parse(f.posts("/api/admin/account/2fa/confirm")[0].options.body), {
    setup_token: "token-B",
    codigo: "123456",
  });
});

test("a detached confirm form cannot post a stale or newer setup token", async (t) => {
  const second = deferred();
  let starts = 0;
  const f = fixture({
    search: "?setup=2fa",
    routes: (url, method) => {
      if (method === "POST" && url === "/api/admin/account/2fa/start") {
        starts += 1;
        if (starts === 1) return ok(START_A);
        return second.promise;
      }
      if (method === "POST" && url === "/api/admin/account/2fa/confirm") return ok({ ok: true });
      if (method === "GET" && url === "/api/admin/account") {
        return starts >= 2 ? ok(ACCOUNT) : ok(ACCOUNT);
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  await drain();
  const oldForm = f.child("#acct-confirm-form");
  assert.ok(oldForm);
  oldForm.querySelector("#acct-confirm-code") || (f.child("#acct-confirm-code").value = "111111");
  f.child("#acct-confirm-code").value = "111111";
  globalThis.history.pushState({}, "", "/admin/account");
  f.winListeners.get("popstate")({});
  await drain(8);
  globalThis.history.pushState({}, "", "/admin/account?setup=2fa");
  f.winListeners.get("popstate")({});
  await drain(4);
  f.submit(oldForm);
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/confirm").length, 0, "detached form must not confirm");
  second.resolve(response(200, START_B));
  await drain();
  f.submit(oldForm);
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/confirm").length, 0);
  f.child("#acct-confirm-code").value = "222222";
  f.submit(f.child("#acct-confirm-form"));
  await drain();
  assert.equal(JSON.parse(f.posts("/api/admin/account/2fa/confirm")[0].options.body).setup_token, "token-B");
});

test("dispose clears the setup secret and ignores a late start body", async (t) => {
  const start = deferred();
  const f = fixture({
    search: "?setup=2fa",
    routes: defaultRoutes({ "POST /api/admin/account/2fa/start": start.promise }),
  }, t);
  const dispose = f.run();
  await drain(8);
  dispose();
  start.resolve(response(200, START_A));
  await drain(8);
  assert.doesNotMatch(f.host.innerHTML, /SECRETAAA/);
  assert.equal(f.logs.some((a) => String(a).includes("SECRETAAA")), false);
});

test("rejected 2FA start recovers the activate controls and the choice panel", async (t) => {
  const f = fixture({
    routes: defaultRoutes({ "POST /api/admin/account/2fa/start": fail(400, { error: "No se pudo guardar." }) }),
  }, t);
  await f.run().ready;
  f.fire(f.child("#acct-2fa-start"), "click");
  await drain();
  assert.match(f.child("#acct-2fa-setup").innerHTML, /No se pudo/);
  assert.equal(f.child("#acct-2fa-start").style.display !== "none", true);
  assert.equal(f.child("#acct-2fa-start").disabled, false);
  assert.equal(f.child("#acct-security").classList.contains("is-setup"), false);
  assert.equal(f.child("#acct-sec-back-row").hidden, true);
});

test("2FA confirm with a rejected acknowledgement keeps the shown setup", async (t) => {
  const f = fixture({
    search: "?setup=2fa",
    routes: defaultRoutes({ "POST /api/admin/account/2fa/confirm": ok({ ok: false, error: "codigo_invalido" }) }),
  }, t);
  await f.run().ready;
  await drain();
  f.child("#acct-confirm-code").value = "000000";
  f.submit(f.child("#acct-confirm-form"));
  f.submit(f.child("#acct-confirm-form"));
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/confirm").length, 1);
  assert.match(f.host.innerHTML, /SECRETAAA/);
  assert.equal(f.child("#acct-confirm-form").querySelector("button[type=submit]").disabled, false);
});

test("removing 2FA asks for the password only and duplicate submits post once", async (t) => {
  let removed = false;
  const gate = deferred();
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/account") {
        return ok(removed ? ACCOUNT : ACCOUNT_2FA);
      }
      if (method === "POST" && url === "/api/admin/account/2fa/remove") return gate.promise;
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  assert.equal(f.child("#acct-2fa-start"), null);
  f.fire(f.child("#acct-2fa-quitar"), "click");
  f.child("#acct-remove-pass").value = "secret-pass";
  f.submit(f.child("#acct-remove-form"));
  f.submit(f.child("#acct-remove-form"));
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/remove").length, 1);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/account/2fa/remove")[0].options.body), { actual: "secret-pass" });
  removed = true;
  gate.resolve(response(200, { ok: true }));
  await drain(8);
  assert.ok(f.child("#acct-2fa-start"));
});

test("several account renders cannot multiply passkey-delete requests", async (t) => {
  const gate = deferred();
  let profiles = 0;
  const f = fixture({
    routes: (url, method) => {
      if (method === "POST" && url === "/api/admin/account/profile") {
        profiles += 1;
        return ok({ ok: true });
      }
      if (method === "POST" && url === "/api/admin/passkeys/delete") return gate.promise;
      if (method === "GET" && url === "/api/admin/account") {
        return ok(profiles ? { ...ACCOUNT, email: "x@y.z" } : ACCOUNT);
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  f.submit(f.child("#acct-email-form"));
  await drain();
  f.fire(f.child("[data-2fa-start]"), "click");
  await drain();
  const del = f.child("[data-key-del]");
  f.fire(del, "click");
  f.fire(del, "click");
  f.fire(del, "click");
  await drain();
  assert.equal(f.posts("/api/admin/passkeys/delete").length, 1);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/passkeys/delete")[0].options.body), {
    credential_id: "cred-1",
  });
  gate.resolve(response(200, { ok: false }));
  await drain();
  assert.equal(del.disabled, false);
  f.fire(del, "click");
  await drain();
  assert.equal(f.posts("/api/admin/passkeys/delete").length, 2);
});

test("duplicate passkey register issues one registrar call; a cancel stays recoverable in the panel", async (t) => {
  const gate = deferred();
  const f = fixture({ routes: defaultRoutes() }, t);
  globalThis.__pkRegistrar = () => gate.promise;
  await f.run().ready;
  f.fire(f.child("#acct-keys-add"), "click");
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  assert.equal(globalThis.__pkRegisterCalls.length, 1);
  gate.reject(Object.assign(new Error("nope"), { cancelled: true }));
  await drain();
  assert.match(f.child("#acct-sec-pk-status").textContent, /Cancelaste la creación de la passkey/);
  assert.ok(f.child("[data-pk-retry]"), "a cancelled attempt offers a retry");
  assert.ok(f.child("[data-pk-help]"), "a cancelled attempt offers the app fallback");
  assert.equal(f.child("#acct-keys-add").disabled, false);
  assert.equal(f.child("#acct-sec-app-help").hidden, true, "cancelling must not auto-start the app flow");
  f.fire(f.child("[data-pk-retry]"), "click");
  await drain();
  assert.equal(globalThis.__pkRegisterCalls.length, 2, "retry runs the registration again");
});

test("a failed passkey attempt keeps the message and both recovery actions", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  globalThis.__pkRegistrar = () => Promise.reject(new Error("firma_invalida"));
  await f.run().ready;
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  assert.equal(f.child("#acct-sec-pk-status").textContent, "firma_invalida");
  assert.equal(f.child("#acct-sec-pk-status").classList.contains("is-error"), true);
  assert.ok(f.child("[data-pk-retry]"));
  assert.ok(f.child("[data-pk-help]"));
  assert.equal(f.child("#acct-keys-add").disabled, false);
});

test("«No me funciona» reveals the Google Authenticator instructions without starting the QR", async (t) => {
  const f = fixture({ routes: defaultRoutes(), userAgent: "Mozilla/5.0 (Linux; Android 14) Mobile" }, t);
  globalThis.__pkRegistrar = () => Promise.reject(Object.assign(new Error("nope"), { cancelled: true }));
  await f.run().ready;
  assert.equal(f.child("#acct-sec-app-help").hidden, true);
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  f.fire(f.child("[data-pk-help]"), "click");
  assert.equal(f.child("#acct-sec-app-help").hidden, false);
  assert.match(f.child("#acct-sec-app-help").textContent, /Instalá Google Authenticator en tu teléfono:/);
  assert.equal(f.child("#acct-sec-app-help").innerHTML.includes(GA_PLAY), true);
  assert.equal(f.child("#acct-sec-app-help").innerHTML.includes(GA_APPSTORE), false,
    "a recognised Android phone shows only its own store");
  assert.equal(f.posts("/api/admin/account/2fa/start").length, 0,
    "revealing the instructions must not start the QR flow on its own");
});

test("unsupported passkeys disable add without inventing a fallback registrar", async (t) => {
  const f = fixture({ routes: defaultRoutes(), pkSupported: false }, t);
  await f.run().ready;
  assert.equal(f.child("#acct-keys-add").disabled, true);
  assert.match(f.child("#acct-sec-pk-status").textContent, /Este navegador no maneja passkeys/);
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  assert.equal(globalThis.__pkRegisterCalls.length, 0);
});

test("an insecure context disables the passkey option and points to the app", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  f.windowRef.isSecureContext = false;
  await f.run().ready;
  assert.equal(f.child("#acct-keys-add").disabled, true);
  assert.match(f.child("#acct-sec-pk-status").textContent, /Necesitás una conexión segura/);
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  assert.equal(globalThis.__pkRegisterCalls.length, 0);
  assert.equal(f.child("#acct-2fa-start").disabled, false, "the app option stays available");
});

test("a device without a platform authenticator keeps the passkey enabled with a conditional phone hint", async (t) => {
  const f = fixture({ routes: defaultRoutes(), pkPlatform: false }, t);
  await f.run().ready;
  await drain();
  assert.equal(f.child("#acct-keys-add").disabled, false,
    "no platform authenticator must not exclude the phone/cross-device key");
  assert.match(f.child("#acct-sec-pk-cap").textContent, /usar otro dispositivo o tu teléfono, si el navegador ofrece esa opción/,
    "the hint stays conditional: a failed capability probe proves neither an absent PIN nor what the browser will show");
  assert.doesNotMatch(f.child("#acct-sec-pk-cap").textContent, /el navegador muestra un código QR/);
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  assert.equal(globalThis.__pkRegisterCalls.length, 1);
});

test("logout posts once then assigns the direct login; dispose removes the listener", async (t) => {
  const gate = deferred();
  const f = fixture({
    routes: defaultRoutes({ "POST /api/admin/logout": gate.promise }),
  }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.logout, "click");
  f.fire(f.logout, "click");
  await drain();
  assert.equal(f.posts("/api/admin/logout").length, 1);
  gate.resolve(response(200, { ok: true }));
  await drain();
  assert.deepEqual(f.assigns, ["/admin/login"]);
  dispose();
  assert.equal(typeof f.logout.listeners.get("click"), "undefined");
  assert.equal(typeof f.winListeners.get("popstate"), "undefined");
  assert.equal(typeof f.docListeners.get("click"), "undefined");
  assert.equal(typeof f.host.listeners.get("click"), "undefined");
  assert.equal(typeof f.host.listeners.get("submit"), "undefined");
});

test("on a portal installation logout leaves through the portal's own address, not /admin/login", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  f.logout.setAttribute("data-portal-logout", "https://portal.example.invalid/logout");
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.logout, "click");
  await drain();
  assert.equal(f.posts("/api/admin/logout").length, 1);
  assert.deepEqual(f.assigns, ["https://portal.example.invalid/logout"]);
});

test("a disposed logout cannot redirect a replacement account owner", async (t) => {
  const gate = deferred();
  const f = fixture({ routes: defaultRoutes({ "POST /api/admin/logout": gate.promise }) }, t);
  await f.run().ready;
  f.fire(f.logout, "click");
  assert.equal(f.posts("/api/admin/logout").length, 1);
  await f.run().ready;
  assert.equal(f.logout.disabled, false, "the replacement owner has a usable sign-out control");
  gate.resolve(response(200, { ok: true }));
  await drain();
  assert.deepEqual(f.assigns, []);
});

for (const disposed of [false, true]) test(`logout network rejection settles without stale redirect (disposed=${disposed})`, async (t) => {
  const gate = deferred();
  const f = fixture({ routes: defaultRoutes({ "POST /api/admin/logout": gate.promise }) }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.logout, "click");
  assert.equal(f.posts("/api/admin/logout").length, 1);
  if (disposed) dispose();
  gate.reject(new Error("synthetic transport failure"));
  await drain();
  assert.deepEqual(f.assigns, disposed ? [] : ["/admin/login"]);
});

test("a failed photo read releases its flight so the user can retry", async (t) => {
  const readers = [];
  const f = fixture({ routes: (url, method) => {
    if (url === "/api/mi/foto") return ok({ ok: true, has_upload: false });
    if (url === "/api/admin/account") return ok({ ...ACCOUNT, rol: "medico" });
    return defaultRoutes()(url, method);
  } }, t);
  globalThis.FileReader = class {
    constructor() { readers.push(this); }
    readAsDataURL() {}
  };
  await f.run().ready;
  const input = f.child("#mifoto-foto-input");
  input.files = [{ name: "synthetic.png", type: "image/png", size: 10 }];
  f.fire(input, "change");
  assert.equal(readers.length, 1);
  readers[0].onerror?.();
  f.fire(input, "change");
  assert.equal(readers.length, 2, "a fresh user action starts a second read after the failed one");
  assert.equal(f.posts("/api/mi/foto").length, 0);
});

test("in-page 2FA notice click pushStates setup without a full reload", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  await f.run().ready;
  const go = f.note.querySelector("a.adm-2fa-go");
  assert.ok(go);
  f.fire(go, "click");
  await drain();
  assert.ok(f.pushes.includes("/admin/account?setup=2fa"));
  assert.match(f.host.innerHTML, /SECRETAAA/);
});

test("copying the manual key uses the visible secret and never logs it", async (t) => {
  const f = fixture({ routes: defaultRoutes(), search: "?setup=2fa" }, t);
  await f.run().ready;
  await drain();
  f.fire(f.child("[data-copy-account]"), "click");
  await drain();
  assert.deepEqual(f.copied, ["SECRETAAA"]);
  assert.equal(f.logs.length, 0);
});

test("photo reader completion after disposal cannot dispatch an upload", async (t) => {
  let reader;
  const f = fixture({ routes: (url, method) => {
    if (url === "/api/mi/foto") return ok({ ok: true, has_upload: false });
    if (url === "/api/admin/account") return ok({ ...ACCOUNT, rol: "medico" });
    return defaultRoutes()(url, method);
  } }, t);
  globalThis.FileReader = class {
    constructor() { reader = this; }
    readAsDataURL() {}
  };
  const dispose = f.run();
  await dispose.ready;
  const input = f.child("#mifoto-foto-input");
  input.files = [{ name: "synthetic.png", type: "image/png", size: 10 }];
  f.fire(input, "change");
  assert.ok(reader, "the live owner starts reading the selected file");
  dispose();
  reader.result = "data:image/png;base64,c3ludGhldGlj";
  reader.onload();
  await drain();
  assert.equal(f.posts("/api/mi/foto").length, 0);
});

test("TOTP start without a setup token cannot expose a confirmation form", async (t) => {
  const f = fixture({ search: "?setup=2fa", routes: defaultRoutes({
    "POST /api/admin/account/2fa/start": ok({ ...START_A, setup_token: undefined }),
  }) }, t);
  await f.run().ready;
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/start").length, 1);
  assert.equal(f.child("#acct-confirm-code"), null);
  assert.match(f.child("#acct-2fa-setup").textContent, /No se pudo guardar/);
  assert.equal(f.posts("/api/admin/account/2fa/confirm").length, 0);
});

test("rejected passkey acknowledgement cannot announce registration or reload", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  globalThis.__pkRegistrar = () => ok({ ok: false }).then(r => r.json());
  await f.run().ready;
  const reads = f.gets("/api/admin/passkeys").length;
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  assert.equal(globalThis.__pkRegisterCalls.length, 1);
  assert.equal(f.gets("/api/admin/passkeys").length, reads);
  assert.doesNotMatch(f.child("#acct-sec-pk-status").textContent, /Passkey creada|Passkey registrada/);
  assert.ok(f.child("#acct-sec-pk-status").textContent);
});

test("confirmed passkey acknowledgement refreshes the list and keeps the TOTP requirement visible", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  globalThis.__pkRegistrar = () => Promise.resolve({ ok: true });
  await f.run().ready;
  const reads = f.gets("/api/admin/passkeys").length;
  const accountReads = f.gets("/api/admin/account").length;
  f.fire(f.child("#acct-keys-add"), "click");
  await drain();
  assert.equal(globalThis.__pkRegisterCalls.length, 1);
  assert.equal(f.gets("/api/admin/passkeys").length, reads + 1);
  assert.match(f.child("#acct-sec-pk-status").textContent, /Passkey creada/);
  assert.match(f.child("#acct-sec-pk-status").textContent, /Falta activar la verificación en 2 pasos/,
    "a registered passkey must not read as the 2FA requirement being met");
  assert.ok(f.child("#acct-security"), "the security panel stays after a passkey");
  assert.match(f.child("#acct-security").textContent, /Activá la verificación en 2 pasos/);
  assert.equal(f.child("#acct-2fa-start").hidden === true, false, "the app option stays available");
  assert.equal(f.gets("/api/admin/account").length, accountReads,
    "a passkey registration must not refetch or re-evaluate the account state");
});

test("medico still gets the self photo section after a fresh remount", async (t) => {
  const f = fixture({
    routes: (url, method) => {
      if (url === "/api/mi/foto" && method === "GET") return ok({ has_upload: false, ini: "AM", color: 1 });
      if (method === "GET" && url === "/api/admin/account") {
        return ok({ ...ACCOUNT, rol: "medico", usuario: "alicia" });
      }
      return defaultRoutes()(url, method);
    },
  }, t);
  f.run();
  const second = f.run();
  await second.ready;
  await drain();
  assert.ok(f.child("#mifoto-foto-av"));
  assert.ok(f.child("#mifoto-foto-pick"), "the photo is changed from a labelled button, not a bare file input");
  assert.equal(f.child("#acct-email").getAttribute("type"), "email");
});


test("login name is editable beside passwords and rename reads autofilled DOM values", async (t) => {
  const f = fixture({routes:defaultRoutes({"POST /api/admin/account/password":ok({ok:true,usuario:"renamed"})})},t);
  await f.run().ready;
  assert.equal(f.child("#acct-username").value, "ana");
  f.child("#acct-username").value = "renamed";
  f.child("#acct-pass-current").value = "autofilled-current";
  f.submit(f.child("#acct-password-form"));
  await drain();
  assert.deepEqual(JSON.parse(f.posts("/api/admin/account/password")[0].options.body),
    {usuario:"renamed",actual:"autofilled-current",nueva:""});
});

test("medico account shows the profile as a view with its values, edited in place", async (t) => {
  const f = fixture({
    routes: (url, method) => {
      if (url === "/api/mi/foto" && method === "GET") return ok({ has_upload: false, ini: "AM", color: 1 });
      if (method === "GET" && url === "/api/admin/account") return ok({ ...ACCOUNT, rol: "medico" });
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  await drain();
  const section = f.child("#acct-profile");
  assert.ok(section, "the medico's Cuenta carries the profile itself");
  assert.doesNotMatch(f.host.innerHTML, /\/admin\/onboarding/, "no detour to the onboarding to edit the profile");
  assert.doesNotMatch(f.host.innerHTML, /Profesional/, "no bare role line for the medico");
  assert.equal(f.child("#acct-profile-name").textContent, "Alicia Méndez");
  assert.equal(f.child("#acct-profile-specialty").textContent, "Pediatría");
  assert.equal(f.child("#acct-profile-phone").textContent, "", "no «Sin teléfono»: the «Agregar» button says it");
  assert.equal(f.child("#acct-profile-phone-add").hidden, false, "an empty phone offers its action beside it");
  assert.equal(f.child("#acct-profile-form").hidden, true, "no field is open until Editar");
  assert.equal(f.child("#acct-password-form").hidden, true, "no password field is open until Cambiar");
  assert.equal(f.child("#acct-email-form").hidden, true, "no email field is open until Editar");
  f.fire(f.child("#acct-profile-edit"), "click");
  assert.equal(f.child("#acct-profile-form").hidden, false);
  assert.equal(f.child("#acct-profile-first").value, "Alicia");
  assert.equal(f.child("#acct-profile-last").value, "Méndez");
  assert.match(f.child("#acct-profile-spec").innerHTML, /value="Pediatría" selected/);
  f.child("#acct-profile-spec").value = "Pediatría";
  f.child("#acct-profile-tel").value = "2254 41-2233";
  f.submit(f.child("#acct-profile-form"));
  await drain();
  const posts = f.posts("/api/admin/doctor-profile");
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].options.body),
    { nombre_pila: "Alicia", apellido: "Méndez", especialidad: "Pediatría", telefono: "2254 41-2233" },
    "only the four self-service fields travel, never an id");
  assert.equal(f.child("#acct-profile-form").hidden, true, "a saved edit closes back to the view");
  assert.equal(f.child("#acct-profile-phone").textContent, "2254 41-2233");
  assert.equal(f.child("#acct-profile-phone-add").hidden, true);
});

test("a rejected profile save keeps the form open with the server's remedy; Cancelar restores the view", async (t) => {
  const f = fixture({
    routes: (url, method) => {
      if (url === "/api/mi/foto" && method === "GET") return ok({ has_upload: false, ini: "AM", color: 1 });
      if (method === "GET" && url === "/api/admin/account") return ok({ ...ACCOUNT, rol: "medico" });
      if (method === "POST" && url === "/api/admin/doctor-profile") return fail(400, { error: "Elegí tu especialidad de la lista." });
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  await drain();
  f.fire(f.child("#acct-profile-edit"), "click");
  f.child("#acct-profile-first").value = "";
  f.submit(f.child("#acct-profile-form"));
  await drain();
  assert.equal(f.posts("/api/admin/doctor-profile").length, 0, "an empty name stops before the request");
  assert.match(f.child("#acct-profile-status").textContent, /Completá tu nombre/);
  f.child("#acct-profile-first").value = "Alicia";
  f.child("#acct-profile-spec").value = "Pediatría";
  f.submit(f.child("#acct-profile-form"));
  await drain();
  assert.equal(f.posts("/api/admin/doctor-profile").length, 1);
  assert.equal(f.child("#acct-profile-form").hidden, false);
  assert.equal(f.child("#acct-profile-status").classList.contains("is-error"), true, "the rejection is told where the form is");
  f.fire(f.child("[data-cancel=profile]"), "click");
  assert.equal(f.child("#acct-profile-form").hidden, true);
  assert.equal(f.child("#acct-profile-edit").hidden, false);
  assert.equal(f.child("#acct-profile-name").textContent, "Alicia Méndez");
});

test("a profile that cannot load offers a retry instead of an empty block", async (t) => {
  let calls = 0;
  const f = fixture({
    routes: (url, method) => {
      if (url === "/api/mi/foto" && method === "GET") return ok({ has_upload: false, ini: "AM", color: 1 });
      if (method === "GET" && url === "/api/admin/account") return ok({ ...ACCOUNT, rol: "medico" });
      if (method === "GET" && url === "/api/admin/doctor-profile") return ++calls === 1 ? fail(503, { error: "base de datos no disponible" }) : ok(PROFILE);
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  await drain();
  assert.match(f.child("#acct-profile-specialty").innerHTML, /data-profile-retry/);
  assert.equal(f.child("#acct-profile-edit").hidden, true, "nothing to edit before the values arrive");
  f.fire(f.child("[data-profile-retry]"), "click");
  await drain();
  assert.equal(calls, 2);
  assert.equal(f.child("#acct-profile-name").textContent, "Alicia Méndez");
  assert.equal(f.child("#acct-profile-edit").hidden, false);
});

test("email is a value with Editar; a save closes the form and shows the new value", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  await f.run().ready;
  assert.equal(f.child("#acct-email-value").textContent, "ana@hospital.gob.ar");
  f.fire(f.child("#acct-email-edit"), "click");
  assert.equal(f.child("#acct-email-form").hidden, false);
  f.child("#acct-email").value = "ana.nueva@hospital.gob.ar";
  f.submit(f.child("#acct-email-form"));
  await drain();
  assert.equal(f.child("#acct-email-form").hidden, true);
  assert.equal(f.child("#acct-email-value").textContent, "ana.nueva@hospital.gob.ar");
  assert.equal(f.child("#acct-email-status").textContent, "Email guardado.");
});

test("non-medico accounts get no profile block and read their role as a row", async (t) => {
  for (const rol of ["admin", "admisionista", "reportes"]) {
    const f = fixture({
      routes: defaultRoutes({ "GET /api/admin/account": ok({ ...ACCOUNT, rol }) }),
    }, t);
    await f.run().ready;
    assert.equal(f.child("#acct-profile"), null, rol + " has no doctor profile");
    assert.equal(f.gets("/api/admin/doctor-profile").length, 0, rol + " never asks for one");
    assert.doesNotMatch(f.host.innerHTML, /\/admin\/onboarding/);
    assert.match(f.child("#acct-access").innerHTML, /Rol/);
  }
});

test("an expired-2FA medico sees only the repair: no profile, no photo, no reads the gate refuses", async (t) => {
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/account") {
        return ok({ ...ACCOUNT, rol: "medico", "2fa_vencido": true });
      }
      if (method === "GET" && url === "/api/admin/passkeys") return fail(403, { error: "2fa_requerido" });
      return defaultRoutes()(url, method);
    },
  }, t);
  await f.run().ready;
  await drain();
  assert.equal(f.child("#acct-profile"), null, "an expired-2FA session can only reach /admin/account");
  assert.equal(f.child("#mifoto-foto-av"), null, "the photo endpoints refuse the session until the repair");
  assert.equal(f.gets("/api/mi/foto").length, 0);
  assert.equal(f.gets("/api/admin/doctor-profile").length, 0);
  assert.ok(f.child("#acct-security"), "the repair leads the page");
});

test("an account without 2FA opens on the security step: passkey first, app second, defer in grace", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  await f.run().ready;
  const panel = f.child("#acct-security");
  assert.ok(panel, "the security panel replaces the bare warning band");
  const opts = f.child("#acct-sec-opts").children;
  assert.equal(opts.length, 2);
  assert.match(opts[0].innerHTML, /Passkey/);
  assert.match(opts[0].innerHTML, /id="acct-keys-add"/);
  assert.match(opts[1].innerHTML, /App de autenticación/);
  assert.match(opts[1].innerHTML, /data-2fa-start/);
  assert.ok(panel.innerHTML.indexOf("acct-keys-add") < panel.innerHTML.indexOf("data-2fa-start"),
    "the passkey option is offered first");
  assert.match(panel.innerHTML, /Computadora compartida del hospital/,
    "the shared-computer guidance steers the passkey to the personal phone");
  assert.match(panel.textContent, /Aunque registres una passkey, esta instalación todavía necesita Google Authenticator/,
    "the panel states up front that the app is still required even after a passkey");
  assert.ok(f.child("#acct-sec-skip"), "inside the grace period the panel can be deferred");
  assert.equal(f.child("#acct-keys-status"), null, "no second status line while the panel owns the passkey");
  assert.equal(f.child("#acct-2fa-start").disabled, false);
  assert.match(globalThis.document.getElementById("acct-css").textContent, /\.acct-code\{width:11ch/,
    "the 6-digit field is 11ch so digits and placeholder survive .cnt-in padding");
  assert.doesNotMatch(f.host.innerHTML, /Microsoft/);
});

test("a portal installation without our TOTP shows no security step or enrollment prompt", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "GET /api/admin/account": ok({ ...ACCOUNT, acceso_portal: true, "2fa_no_aplica": true, "2fa_deadline": null }),
    }),
  }, t);
  await f.run().ready;
  await drain();
  assert.equal(f.child("#acct-security"), null,
    "the portal is the second factor: the server's 2fa_no_aplica keeps the enrollment panel out");
  assert.equal(f.child("#acct-2fa"), null, "no 2FA section while the installation keeps our TOTP off");
  assert.doesNotMatch(f.host.innerHTML, /Activá la verificación en 2 pasos/, "no misleading enrollment prompt");
  assert.equal(f.child("#acct-password-form"), null, "the portal closes the password form");
  assert.equal(f.child("#acct-passkeys"), null, "the portal closes passkey management");
});

test("a portal installation with our TOTP keeps the security step app-only and deadline-honest", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "GET /api/admin/account": ok({ ...ACCOUNT, acceso_portal: true }),
      "GET /api/admin/passkeys": fail(403, { error: "2fa_requerido" }),
    }),
  }, t);
  await f.run().ready;
  await drain();
  const panel = f.child("#acct-security");
  assert.ok(panel, "the step follows the server's own grace contract, never the role alone");
  const opts = f.child("#acct-sec-opts").children;
  assert.equal(opts.length, 1, "passkey login is closed on a portal installation: app option only");
  assert.match(opts[0].innerHTML, /App de autenticación/);
  assert.equal(f.child("#acct-keys-add"), null, "no passkey path where the portal disallows passkey login");
  assert.doesNotMatch(panel.textContent, /Aunque registres una passkey/, "portal instructions never suggest an unavailable credential");
  assert.doesNotMatch(panel.innerHTML, /Computadora compartida del hospital/,
    "no phone-passkey guidance without the passkey option");
  assert.ok(f.child("#acct-sec-skip"), "the deferral honors the live grace period");
  assert.equal(f.child("#acct-passkeys"), null, "the standalone passkeys section stays hidden");
  assert.equal(f.child("#acct-password-form"), null, "the password form stays closed");
});

test("an expired deadline on a portal installation keeps the app-only step without deferral", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "GET /api/admin/account": ok({ ...ACCOUNT, acceso_portal: true, "2fa_vencido": true }),
      "GET /api/admin/passkeys": fail(403, { error: "2fa_requerido" }),
    }),
  }, t);
  await f.run().ready;
  await drain();
  assert.match(f.host.innerHTML, /El plazo terminó/, "the server's expired verdict is told as-is");
  assert.equal(f.child("#acct-sec-skip"), null, "no deferral once the server closed the grace");
  assert.equal(f.child("#acct-keys-add"), null, "no passkey path appears with the deadline closed");
  assert.equal(f.child("#acct-2fa-start").disabled, false, "the app flow still repairs the account");
  assert.equal(f.child("#acct-profile"), null, "an expired-2FA session keeps the profile link hidden");
});

test("the GA store link follows the recognised device: iPhone, desktop-class iPad, unknown desktop", async (t) => {
  for (const [label, ua, touch, expectPlay, expectStore] of [
    ["iphone", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile", 5, false, true],
    ["ipad desktop ua", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", 5, false, true],
    ["unknown desktop", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", 0, true, true],
  ]) {
    await t.test(label, async () => {
      const f = fixture({ routes: defaultRoutes(), search: "?setup=2fa", userAgent: ua, maxTouchPoints: touch }, t);
      await f.run().ready;
      await drain();
      const links = f.host.innerHTML;
      assert.equal(links.includes(GA_PLAY), expectPlay);
      assert.equal(links.includes(GA_APPSTORE), expectStore);
    });
  }
});

test("deferring is a per-visit collapse; the restore control brings the choices back", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  await f.run().ready;
  f.fire(f.child("#acct-sec-skip"), "click");
  assert.equal(f.child("#acct-security").classList.contains("is-deferred"), true);
  assert.equal(f.child("#acct-sec-restore-row").hidden, false);
  assert.match(f.host.innerHTML, /Activá la verificación en 2 pasos/, "the deadline reminder stays visible after deferring");
  f.fire(f.child("#acct-sec-restore"), "click");
  assert.equal(f.child("#acct-security").classList.contains("is-deferred"), false);
  assert.equal(f.child("#acct-sec-restore-row").hidden, true);
  assert.ok(f.child("#acct-sec-opts"), "the choices come back without a refetch");
  assert.equal(f.gets("/api/admin/account").length, 1);
});

test("an expired deadline keeps the panel but removes the deferral", async (t) => {
  const f = fixture({
    routes: defaultRoutes({
      "GET /api/admin/account": ok(ACCOUNT_EXPIRED),
      "GET /api/admin/passkeys": fail(403, { error: "2fa_requerido" }),
    }),
  }, t);
  await f.run().ready;
  assert.match(f.host.innerHTML, /El plazo terminó/);
  assert.equal(f.child("#acct-sec-skip"), null, "no deferral once the deadline gate closed");
  assert.equal(f.child("#acct-keys-add").disabled, true, "the passkey path is closed while 2FA stays required");
  assert.equal(f.child("#acct-2fa-start").disabled, false, "the account route still repairs through the app");
});

test("opening the setup collapses the panel to a return control; return closes the QR without a reload", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  await f.run().ready;
  f.fire(f.child("#acct-2fa-start"), "click");
  await drain();
  assert.equal(f.child("#acct-security").classList.contains("is-setup"), true);
  assert.equal(f.child("#acct-sec-back-row").hidden, false);
  assert.match(f.host.innerHTML, /SECRETAAA/);
  f.fire(f.child("#acct-sec-back"), "click");
  await drain();
  assert.equal(f.child("#acct-security").classList.contains("is-setup"), false);
  assert.equal(f.child("#acct-sec-back-row").hidden, true);
  assert.doesNotMatch(f.host.innerHTML, /SECRETAAA/);
  assert.ok(f.pushes.includes("/admin/account"), "returning pushes the bare account URL");
  assert.equal(f.gets("/api/admin/account").length, 1, "the return is a local state change, not a refetch");
  assert.notEqual(f.child("#acct-2fa-start").style.display, "none", "the return restores the visible action");
  assert.equal(f.child("#acct-2fa-start").disabled, false);
  f.fire(f.child("#acct-2fa-start"), "click");
  await drain();
  assert.match(f.host.innerHTML, /SECRETAAA/, "the setup can be reopened after returning");
});

test("a cold ?setup=2fa load opens the QR with the panel collapsed to the return control", async (t) => {
  const f = fixture({ routes: defaultRoutes(), search: "?setup=2fa" }, t);
  await f.run().ready;
  await drain();
  assert.equal(f.posts("/api/admin/account/2fa/start").length, 1);
  assert.equal(f.child("#acct-security").classList.contains("is-setup"), true);
  assert.equal(f.child("#acct-sec-back-row").hidden, false);
});

test("disposal during a pending registrar cannot paint recovery or re-enable a dead button", async (t) => {
  const gate = deferred();
  const f = fixture({ routes: defaultRoutes() }, t);
  globalThis.__pkRegistrar = () => gate.promise;
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#acct-keys-add"), "click");
  dispose();
  gate.reject(Object.assign(new Error("late"), { cancelled: true }));
  await drain();
  assert.doesNotMatch(f.child("#acct-sec-pk-status").textContent, /Cancelaste/,
    "a late cancellation after dispose must not paint recovery into the dead owner");
  assert.equal(f.child("#acct-sec-pk-extra").innerHTML, "");
});

test("a verified TOTP confirmation clears the panel and restores the standalone sections", async (t) => {
  let activated = false;
  const f = fixture({
    routes: (url, method) => {
      if (method === "GET" && url === "/api/admin/account") {
        return ok(activated ? ACCOUNT_2FA : ACCOUNT);
      }
      if (method === "POST" && url === "/api/admin/account/2fa/confirm") {
        activated = true;
        return ok({ ok: true });
      }
      return defaultRoutes()(url, method);
    },
    search: "?setup=2fa",
  }, t);
  await f.run().ready;
  await drain();
  assert.ok(f.child("#acct-security"));
  f.child("#acct-confirm-code").value = "123456";
  f.submit(f.child("#acct-confirm-form"));
  await drain(12);
  assert.equal(f.child("#acct-security"), null, "the security step is gone once 2FA is verified");
  assert.match(f.host.innerHTML, /App de autenticación/);
  assert.ok(f.child("#acct-2fa-quitar"), "an active 2FA shows its app and «Quitar», with no green «Activa»");
  assert.doesNotMatch(f.host.innerHTML, /acct-dot/);
  assert.ok(f.child("#acct-keys-add"), "the standalone Passkeys section owns adding keys again");
  assert.match(f.host.innerHTML, /Agregar una passkey/);
});


test("returning during setup retires its late response and lets a new attempt own the panel", async (t) => {
  const first=deferred(), second=deferred();let starts=0;
  const f=fixture({routes:(url,method)=>{
    if(method==="POST" && url==="/api/admin/account/2fa/start") return ++starts===1?first.promise:second.promise;
    return defaultRoutes()(url,method);
  }},t);
  await f.run().ready;
  f.fire(f.child("#acct-2fa-start"),"click");
  await drain();
  f.fire(f.child("#acct-sec-back"),"click");
  assert.equal(f.child("#acct-2fa-start").disabled,false);
  assert.notEqual(f.child("#acct-2fa-start").style.display,"none");
  f.fire(f.child("#acct-2fa-start"),"click");
  await drain();
  assert.equal(starts,2,"a cancelled in-flight attempt must not block retry");
  first.resolve(response(200,START_A));await drain();
  assert.doesNotMatch(f.host.innerHTML,/SECRETAAA/);
  assert.equal(f.child("#acct-2fa-start").disabled,true,"old response cannot unlock a newer attempt");
  second.resolve(response(200,START_B));await drain();
  assert.match(f.host.innerHTML,/SECRETBBB/);
  assert.doesNotMatch(f.host.innerHTML,/SECRETAAA/);
  f.child("#acct-confirm-code").value="123456";
  f.submit(f.child("#acct-confirm-form"));await drain();
  assert.equal(JSON.parse(f.posts("/api/admin/account/2fa/confirm")[0].options.body).setup_token,"token-B");
});
