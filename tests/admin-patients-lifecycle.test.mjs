// Runtime lifecycle tests for the native general-staff patients roster.
// Imports and executes static/platform/admin-patients.mjs. DOM, events, fetch,
// history/location, timers, confirm/toast and the eight shared modules are
// seams — no production function is copied here; the doubles only record and
// answer with synthetic fixtures (never a real fetch).
import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../static/platform/admin-patients.mjs";
import { API_ERROR_STUB } from "./fixtures/api-error-stub.mjs";

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function drain(turns = 16) { for (let i = 0; i < turns; i += 1) await tick(); }
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
    clone: () => response(status, body),
  };
}
const ok = (body) => Promise.resolve(response(200, body));
const fail = (status, body = {}) => Promise.resolve(response(status, body));

// ---------------------------------------------------------------------------
// Minimal DOM: a real nested parser (tags, attributes, text nodes), a
// compound+descendant selector engine, listeners, value/checked, outerHTML
// replacement, and bubbling events that reach the host, document and window.
// ---------------------------------------------------------------------------

const VOID_TAGS = new Set(["input", "br", "hr", "img", "meta", "link", "source", "col", "wbr"]);

function parseAttrs(raw) {
  const attrs = Object.create(null);
  const re = /([:@A-Za-z_:][\w:.-]*)(?:\s*=\s*"([^"]*)"|\s*=\s*'([^']*)')?/g;
  let m;
  while ((m = re.exec(String(raw || "")))) attrs[m[1]] = m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : "");
  return attrs;
}

let elemSeq = 0;

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
    this.disabled = Object.prototype.hasOwnProperty.call(this.attrs, "disabled");
    this.hidden = Object.prototype.hasOwnProperty.call(this.attrs, "hidden");
    this._value = Object.prototype.hasOwnProperty.call(this.attrs, "value") ? this.attrs.value : "";
    this.checked = this.disabled && false || Object.prototype.hasOwnProperty.call(this.attrs, "checked");
    this.focused = false;
    this.seq = ++elemSeq;
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  appendChild(node) { node.remove?.(); this.childNodes.push(node); node.parentNode = this; return node; }
  remove() {
    if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(node => node !== this);
    this.parentNode = null;
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
    return this.childNodes.map((n) => (n.nodeType === 3 ? n.textContent : n.textContent)).join("");
  }
  set textContent(v) {
    this.childNodes = [];
    if (v != null && String(v) !== "") this.childNodes.push({ nodeType: 3, textContent: String(v), parentNode: this });
  }
  get innerHTML() {
    return this.childNodes.map((node) => node.nodeType === 3 ? node.textContent : node.outerHTML).join("");
  }
  get outerHTML() {
    const attrs = Object.entries(this.attrs).map(([key, value]) =>
      ' ' + key + '="' + String(value).replace(/"/g, '&quot;') + '"').join('');
    const tag = this.tagName.toLowerCase();
    return '<' + tag + attrs + '>' + (VOID_TAGS.has(tag) ? '' : this.innerHTML + '</' + tag + '>');
  }
  set innerHTML(html) {
    this._html = String(html);
    this.childNodes = [];
    parseFragment(String(html), this);
    this.finalizeSelect();
  }
  set outerHTML(html) {
    const parent = this.parentNode;
    if (!parent) return;
    const at = parent.childNodes.indexOf(this);
    if (at < 0) return;
    const holder = new Elem("div");
    parseFragment(String(html), holder);
    parent.childNodes.splice(at, 1, ...holder.childNodes);
    holder.childNodes.forEach((n) => { n.parentNode = parent; });
  }
  finalizeSelect() {
    if (this.tagName !== "SELECT") return;
    const chosen = this.children.filter((o) => o.attrs && Object.prototype.hasOwnProperty.call(o.attrs, "selected"))[0];
    const pick = chosen || this.children[0];
    this._value = pick ? (pick.attrs && Object.prototype.hasOwnProperty.call(pick.attrs, "value") ? pick.attrs.value : pick.textContent) : "";
  }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); }
  getAttribute(name) { const v = this.attrs[name]; return v === undefined ? null : v; }
  setAttribute(name, value) {
    this.attrs[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
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
  focus() { this.focused = true; globalThis.__patFocusLog.push(this.id || this.className || this.tagName); }
  blur() { this.focused = false; }
  setSelectionRange() {}
  matches(sel) { return matchesCompoundChain(this, String(sel)); }
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
          stack[i].finalizeSelect();
          stack.splice(0, i + 1);
          break;
        }
      }
      continue;
    }
    const el = new Elem(tag, parseAttrs(raw));
    for (const [k, v] of Object.entries(el.attrs)) {
      if (k.slice(0, 5) === "data-") el.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
    }
    el.parentNode = stack[0];
    stack[0].childNodes.push(el);
    if (!VOID_TAGS.has(el.tagName.toLowerCase()) && !selfClose) stack.unshift(el);
  }
  pushText(html.slice(last));
}

// Compound selector: tag, .class, #id, [attr], [attr=v], [attr^=v], :checked
function matchesCompound(el, compound) {
  const tokens = [];
  const tre = /([a-zA-Z][\w-]*|\.[\w-]+|#[\w-]+|\[[^\]]+\]|:checked)/g;
  let m;
  while ((m = tre.exec(compound))) tokens.push(m[1]);
  if (!tokens.length) return false;
  for (const tok of tokens) {
    if (tok[0] === ".") { if (!el.classList.contains(tok.slice(1))) return false; }
    else if (tok[0] === "#") { if (el.id !== tok.slice(1)) return false; }
    else if (tok[0] === "[") {
      const am = /^\[([^\]~^$*|=]+)(?:([\^$*~|]?=)["']?([^"'\]]*)["']?)?\]$/.exec(tok);
      if (!am) return false;
      const [, name, op, want] = am;
      if (!el.hasAttribute(name)) return false;
      if (!op) continue;
      const got = el.getAttribute(name) || "";
      if (op === "=" && got !== want) return false;
      if (op === "^=" && !got.startsWith(want)) return false;
    } else if (tok === ":checked") { if (el.checked !== true) return false; }
    else if (tok.toUpperCase() !== el.tagName) return false;
  }
  return true;
}

function matchesCompoundChain(el, sel) {
  const compounds = sel.trim().split(/\s+>/).join(" > ").split(/\s+/);
  // Support only plain descendant chains (what this module queries).
  if (compounds.includes(">")) return false;
  const matchFrom = (node, parts) => {
    if (!matchesCompound(node, parts[parts.length - 1])) return false;
    if (parts.length === 1) return true;
    let anc = node.parentNode;
    while (anc && anc.nodeType === 1) {
      if (matchFrom(anc, parts.slice(0, -1))) return true;
      anc = anc.parentNode;
    }
    return false;
  };
  return matchFrom(el, compounds);
}

// ---------------------------------------------------------------------------
// Synthetic fixtures (no real patient data)
// ---------------------------------------------------------------------------

const NOW = new Date("2026-09-08T12:00:00Z");
const ALEJO = {
  dni: "30111222", apellido: "Varela", nombre: "Alejo", nacimiento: "1978-10-28", sexo: "M",
  contacto: "2254445566", email: "alejo@example.test", cobertura_nombre: "PAMI", servicio: "Cardiología",
  localidad: "Pinamar", created: "2025-02-01T10:00:00Z", activo: true,
  ranking: { puntaje: 82, etiqueta: "Viene siempre", nivel: "good", hechos: 6, ultimo: "2026-08-20", motivos: ["6 turnos, 0 ausencias"] },
  turnos: { otorgados: 7, proximo: "2026-09-15" },
};
const MARIA = {
  dni: "28777666", apellido: "López", nombre: "María", nacimiento: "1985-03-12", sexo: "F",
  contacto: "2254555444", email: "", cobertura_nombre: "OSDE", servicio: "Kinesiología", localidad: "Ostende",
  created: "2025-06-10T10:00:00Z", activo: true,
  ranking: { puntaje: 64, etiqueta: "Suele venir", nivel: "warn", hechos: 9, ultimo: "2026-07-01", motivos: [] },
};
const JUAN = {
  dni: "25333444", apellido: "Gómez", nombre: "Juan", nacimiento: "1962-11-05", sexo: "M",
  contacto: "", email: "juan@example.test", cobertura_nombre: "", localidad: "Valeria del Mar",
  created: "2026-01-15T10:00:00Z", activo: false,
  ranking: { sin_historial: true, puntaje: 55, etiqueta: "Sin historial", nivel: "none", hechos: 0, motivos: [] },
};
const BEATRIZ = {
  dni: "32555777", apellido: "Fernández", nombre: "Beatriz", nacimiento: "1992-06-14", sexo: "F",
  contacto: "2254666777", email: "bea@example.test", cobertura_nombre: "OSDE", localidad: "Pinamar",
  created: "2026-05-05T10:00:00Z", activo: true,
  ranking: { puntaje: 90, etiqueta: "Viene siempre", nivel: "good", hechos: 4, ultimo: "2026-09-01", motivos: [] },
};
const LIST_BODY = {
  modo: "recientes", total: 205,
  items: [ALEJO, MARIA, JUAN],
  medicos: [{ id: "7", nombre: "Dra Sosa" }, { id: "9", nombre: "Dr Ruiz" }],
};
const PAGE2_BODY = { modo: null, total: 205, items: [BEATRIZ] };
const SEARCH_BODY = { modo: null, total: 1, items: [MARIA] };
const DETAIL_ALEJO = {
  item: Object.assign({}, ALEJO, {
    cobertura: { obras_sociales: [{ nombre: "PAMI", origen: "puco", afiliado: "123" }], prepaga: {}, principal: "PAMI" },
  }),
  cobertura_principal: { clave: "PAMI" },
  ranking: ALEJO.ranking,
  turnos: [
    { id: 41, estado: "aprobada", servicio: "Cardiología", centro: "Hospital de Pinamar", medico: "Dra Sosa", dia: "2026-09-15", hora: "10:30", pedido: false, asistencia: "" },
    { id: 40, estado: "aprobada", servicio: "Clínica médica", centro: "CAPS Ostende", medico: "", dia: "2026-08-20", hora: "09:00", pedido: false, asistencia: "presente" },
    { id: 39, estado: "pendiente", servicio: "Kinesiología", centro: "Hospital de Pinamar", medico: "", dia: "2026-09-20", hora: "08:00", pedido: true, asistencia: "" },
  ],
  dependientes: [{
    dni: "25333444", nombre: "Tomás", apellido: "Varela", nacimiento: "2015-03-15", vinculo: "hijo",
    adultos: [{ dni: "30111222", nombre: "Alejo", apellido: "Varela" }, { dni: "28777666", nombre: "María", apellido: "López" }],
  }],
  a_cargo_de: [],
};
const DETAIL_ALEJO_SOLO = Object.assign({}, DETAIL_ALEJO, {
  dependientes: [Object.assign({}, DETAIL_ALEJO.dependientes[0], { adultos: [{ dni: "30111222", nombre: "Alejo", apellido: "Varela" }] })],
});
const DETAIL_BEATRIZ = {
  item: Object.assign({}, BEATRIZ, { cobertura: { obras_sociales: [], prepaga: { nombre: "Swiss", afiliado: "9", plan: "A" }, principal: "prepaga" } }),
  cobertura_principal: { clave: "prepaga" },
  ranking: BEATRIZ.ranking,
  dependientes: [],
  a_cargo_de: [{ dni: "35000111", nombre: "Helena", apellido: "Fernández" }],
};

// ---------------------------------------------------------------------------
// Shared-module doubles (data: URLs). Unique fragments keep every fixture's
// module state fresh regardless of the dynamic-import cache.
// ---------------------------------------------------------------------------

const SESSION_SRC = [
  "export function confirmar(response, parsedBody, isCurrent) {",
  "  var current = typeof isCurrent === 'function' ? isCurrent : null;",
  "  globalThis.__patConfirmCalls.push({ status: response && response.status, body: parsedBody, hasPredicate: typeof isCurrent === 'function', current: current ? current() : null });",
  "  if (!response || !current || !current()) return Promise.resolve(false);",
  "  if (response.status !== 401 || !parsedBody || parsedBody.error !== 'no_autenticado') return Promise.resolve(false);",
  "  var gate = globalThis.__patSessionGate || Promise.resolve(true);",
  "  return gate.then(function (confirmed) {",
  "    if (!current()) return false;",
  "    if (confirmed) globalThis.location.href = '/admin/login?next=%2Fadmin%2Fpacientes';",
  "    return !!confirmed;",
  "  });",
  "}",
].join("\n");
const I18N_SRC = "export function t(key, vars, fallback) { var out = String(fallback == null ? key : fallback); if (!vars) return out; return out.replace(/\\{(\\w+)\\}/g, function (m, k) { return vars[k] === undefined ? m : String(vars[k]); }); }" + API_ERROR_STUB;
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const DATES_SRC = "var MESES=" + JSON.stringify(MESES) + ";" +
  "export function nacimiento(iso) { if (!iso) return ''; var p = String(iso).slice(0, 10).split('-'); globalThis.__patDateCalls.push(iso); return (+p[2]) + ' de ' + MESES[+p[1] - 1] + ' de ' + p[0]; }" +
  "export function amigable(dia, hora) { return dia ? dia + (hora ? ', ' + hora : '') : ''; }" +
  "export function pasada(dia) { if (!dia) return ''; var p = String(dia).slice(0, 10).split('-'); return (+p[2]) + ' de ' + MESES[+p[1] - 1]; }" +
  "export function corta(dia, hora) { return dia ? dia + (hora ? ', ' + hora : '') : ''; }";
const FORMAT_SRC = [
  "function edad(iso) { if (!iso) return null; var p = String(iso).slice(0, 10).split('-'); var n = new Date(globalThis.__patNow); var a = n.getFullYear() - +p[0]; var m = n.getMonth() + 1 - +p[1]; if (m < 0 || (m === 0 && n.getDate() < +p[2])) a -= 1; return a; }",
  "export function dni(x) { return String(x == null ? '' : x).replace(/\\B(?=(\\d{3})+(?!\\d))/g, '.'); }",
  "export function digits(x) { return String(x == null ? '' : x).replace(/\\D/g, ''); }",
  "export { edad };",
  "export function edadTexto(iso) { var e = edad(iso); return e == null ? '' : String(e); }",
  "export function personaEdad(pila, ap, iso) { var n = [pila, ap].filter(Boolean).join(' '); var e = edadTexto(iso); return n && e ? n + ', ' + e : n; }",
  "export function telNacional(x) { return x ? String(x) : ''; }",
  "export function contactoIconoHtml(x) { return x ? '<a class=\"cnt-ico-link\" href=\"' + (String(x).indexOf('@') > 0 ? 'mailto:' : 'tel:') + x + '\" data-copy=\"' + x + '\"></a>' : ''; }",
  "export function medicosTexto(l) { l = l || []; return l.length ? l[0].nombre + (l.length > 1 ? ' +' + (l.length - 1) : '') : ''; }",
  "export function medicosTitulo(l) { return (l || []).map(function (m) { return m.nombre; }).join(', '); }",
  "export function medicosLista(l) { return (l || []).map(function (m) { return m.nombre; }); }",
  "export function dniHtml(v, pre) { var d = digits(v); return d ? '<span class=\"dni-cp\" data-copy-dni=\"' + d + '\">' + (pre || '') + dni(d) + '</span>' : ''; }",
].join("\n");
const COMBO_SRC = [
  "export function html(cls, items, value, display, placeholder, title) {",
  "  return '<span class=\"cnt-combo\"><input type=\"hidden\" class=\"' + cls + '\" value=\"' + (value || '') + '\" data-combo=\"1\">'",
  "    + '<span class=\"cnt-combo-txt\" title=\"' + (title || '') + '\">' + (display || placeholder || '') + '</span></span>';",
  "}",
].join("\n");
const PREFS_SRC = [
  "var store = new Map();",
  "globalThis.__patPrefs = { store: store, calls: [] };",
  "export function load() { return Promise.resolve(); }",
  "export function get(k, d) { return store.has(k) ? store.get(k) : d; }",
  "export function set(k, v) { store.set(k, v); globalThis.__patPrefs.calls.push({ k: k, v: v }); }",
].join("\n");
const TABLE_SRC = [
  "export function crear(cfg) {",
  "  globalThis.__patCrearCfg = cfg;",
  "  return { programarFit: function () { globalThis.__patFitCount += 1; }, dispose: function () { globalThis.__patCrearDisposed += 1; } };",
  "}",
  "export function columnas(cfg) {",
  "  globalThis.__patColsCfg = cfg;",
  "  var selected = cfg.cols.filter(function (c) { return c.always || c['default']; });",
  "  cfg.select = function (keys) { selected = cfg.cols.filter(function (c) { return c.always || keys.includes(c.key); }); cfg.onChange(selected); };",
  "  return {",
  "    activas: function () { return selected; },",
  "    gearHtml: function () { return '<span class=\"tbl-config\" data-tbl-res=\"1\"><button type=\"button\" id=\"pat-gear\">Columnas</button></span>'; },",
  "    dispose: function () { globalThis.__patColsDisposed += 1; }",
  "  };",
  "}",
].join("\n");
const ACCOUNT_SRC = [
  "export function isoToArg(iso) { if (!iso) return ''; var p = String(iso).slice(0, 10).split('-'); return p.length === 3 ? p[2] + '/' + p[1] + '/' + p[0] : ''; }",
  "export function parseFlexibleDate(txt) {",
  "  globalThis.__patDateParses.push(String(txt));",
  "  var s = String(txt || '').trim();",
  "  var iso = /^(\\d{4})-(\\d{2})-(\\d{2})$/.exec(s);",
  "  if (iso) return s;",
  "  var m = /^(\\d{1,2})[\\/\\-](\\d{1,2})[\\/\\-](\\d{2,4})$/.exec(s);",
  "  if (!m) return null;",
  "  var d = +m[1], mo = +m[2], y = +m[3];",
  "  if (y < 100) y += y > 30 ? 1900 : 2000;",
  "  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;",
  "  var out = y + '-' + String(mo).padStart(2, '0') + '-' + String(d).padStart(2, '0');",
  "  return out <= '2026-09-08' ? out : null;",
  "}",
].join("\n");

function dataUrl(src, salt) {
  return "data:text/javascript," + encodeURIComponent(src) + "#" + salt;
}
// The shared disclosure rule is stateless: the real module, not a double.
const FILTERS_URL = new URL("../static/platform/admin-filter-controls.mjs", import.meta.url).href;

let fixtureSeq = 0;

function fixture({ routes, confirm = () => true, search = "", sessionGate = null, manifest = null, realSession = false, now = NOW } = {}, t) {
  const salt = "f" + (++fixtureSeq);
  const host = new Elem("div", { id: "sec-pacientes" });
  const nav = new Elem("nav", { id: "adm-nav" });
  for (const [id, href] of [["inicio", "/admin"], ["pacientes", "/admin/pacientes"], ["cola", "/admin/cola"]]) {
    const a = new Elem("a", { "data-nav": id, href });
    nav.childNodes.push(a);
    a.parentNode = nav;
  }
  const h1 = new Elem("h1", { id: "adm-h1" });
  h1.textContent = "Panel";
  const sub = new Elem("p", { id: "adm-sub" });
  const docRoot = new Elem("div");
  docRoot.childNodes.push(nav, host, h1, sub);
  [nav, host, h1, sub].forEach((n) => { n.parentNode = docRoot; });
  const docListeners = new Map();
  const doc = {
    head: new Elem('head'),
    createElement(tag) { return new Elem(tag); },
    readyState: "complete",
    getElementById(id) { return docRoot.querySelector("#" + id); },
    querySelector(sel) { return docRoot.querySelector(sel); },
    querySelectorAll(sel) { return docRoot.querySelectorAll(sel); },
    addEventListener(name, fn) { docListeners.set(name, fn); },
    removeEventListener(name) { docListeners.delete(name); },
    listeners: docListeners,
  };

  const requests = [];
  const assigns = [];
  const pushes = [];
  const replaces = [];
  const toasts = [];
  const confirms = [];
  const timers = new Map();
  let timerSeq = 0;

  const location = {
    href: "https://example.test/admin/pacientes" + search,
    pathname: "/admin/pacientes",
    search,
    assign(url) { assigns.push(String(url)); setUrl(String(url)); },
  };
  function setUrl(url) {
    location.href = "https://example.test" + (url[0] === "/" ? url : "/" + url);
    const u = new URL(location.href);
    location.pathname = u.pathname;
    location.search = u.search;
  }
  const history = {
    pushState(state, title, url) { pushes.push(String(url)); setUrl(String(url)); },
    replaceState(state, title, url) { replaces.push(String(url)); setUrl(String(url)); },
  };

  const windowBase = {
    SANA_ADMIN_SCREEN: "pacientes",
    SANA_STATIC: manifest || {
      "platform/admin-session.mjs": realSession
        ? new URL('../static/platform/admin-session.mjs?' + salt, import.meta.url).href
        : dataUrl(SESSION_SRC, salt + "s"),
      "platform/i18n.mjs": dataUrl(I18N_SRC, salt + "i"),
      "platform/dates.mjs": dataUrl(DATES_SRC, salt + "d"),
      "platform/format.mjs": dataUrl(FORMAT_SRC, salt + "f"),
      "platform/admin-combo.mjs": dataUrl(COMBO_SRC, salt + "c"),
      "platform/admin-preferences.mjs": dataUrl(PREFS_SRC, salt + "p"),
      "platform/admin-table.mjs": dataUrl(TABLE_SRC, salt + "t"),
      "platform/account-form.mjs": dataUrl(ACCOUNT_SRC, salt + "a"),
      "platform/admin-filter-controls.mjs": FILTERS_URL,
    },
    location,
    history,
    matchMedia: () => ({ matches: false }),
    confirm: (msg) => { confirms.push(msg); return confirm(msg); },
    toast: (msg) => { toasts.push(String(msg)); },
    listeners: new Map(),
    addEventListener(name, fn) { this.listeners.set(name, fn); },
    removeEventListener(name) { this.listeners.delete(name); },
  };
  windowBase.document = doc;

  const keys = ["window", "document", "fetch", "location", "history", "confirm", "toast",
    "setTimeout", "clearTimeout", "__patConfirmCalls", "__patDateCalls", "__patDateParses",
    "__patFocusLog", "__patSessionGate", "__patPrefs", "__patCrearCfg", "__patColsCfg",
    "__patFitCount", "__patCrearDisposed", "__patColsDisposed", "__patNow"];
  const previous = Object.fromEntries(keys.map((k) => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  globalThis.__patNow = now.getTime();
  globalThis.__patConfirmCalls = [];
  globalThis.__patDateCalls = [];
  globalThis.__patDateParses = [];
  globalThis.__patFocusLog = [];
  globalThis.__patSessionGate = sessionGate;
  globalThis.__patPrefs = { store: new Map(), calls: [] };
  globalThis.__patCrearCfg = null;
  globalThis.__patColsCfg = null;
  globalThis.__patFitCount = 0;
  globalThis.__patCrearDisposed = 0;
  globalThis.__patColsDisposed = 0;
  globalThis.document = doc;
  globalThis.location = location;
  globalThis.history = history;
  globalThis.confirm = windowBase.confirm;
  globalThis.toast = windowBase.toast;
  globalThis.window = windowBase;
  globalThis.setTimeout = (fn, ms) => { timerSeq += 1; timers.set(timerSeq, { fn, ms }); return timerSeq; };
  globalThis.clearTimeout = (id) => { timers.delete(id); };
  globalThis.fetch = (url, options = {}) => {
    const method = String(options.method || "GET").toUpperCase();
    requests.push({ url: String(url), method, options });
    const next = routes(String(url), method, requests.length, options);
    assert.ok(next && typeof next.then === "function", "unrouted patients fetch: " + method + " " + url);
    return next;
  };
  t.after(() => {
    for (const [k, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, k, descriptor);
      else delete globalThis[k];
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
      type,
      target: el,
      button: 0,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.propagationStopped = true; },
      metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
    }, extra);
    let node = el;
    while (node) {
      const fn = node.listeners && node.listeners.get(type);
      if (fn) fn(event);
      if (event.propagationStopped) return event;
      node = node.parentNode;
    }
    const docFn = docListeners.get(type);
    if (docFn) docFn(event);
    const winFn = windowBase.listeners.get(type);
    if (winFn) winFn(event);
    return event;
  };
  const flushTimers = () => {
    const pending = [...timers.values()];
    timers.clear();
    pending.forEach(({ fn }) => fn());
  };
  const check = (radio) => {
    for (const peer of host.querySelectorAll('input[name="' + radio.attrs.name + '"]')) peer.checked = false;
    radio.checked = true;
  };
  return {
    host, doc, windowRef: windowBase, requests, assigns, pushes, replaces, toasts, confirms,
    timers, run, fire, flushTimers, check,
    child(sel) { return host.querySelector(sel); },
    children(sel) { return host.querySelectorAll(sel); },
    // The claims strip (TASK-68) is its own read; the counts below are about the roster.
    gets() { return requests.filter((r) => r.method === "GET" && r.url !== "/api/admin/dni-claims").map((r) => r.url); },
    posts(url) { return requests.filter((r) => r.method === "POST" && r.url === url); },
    navAnchor: () => nav.querySelectorAll("a[data-nav=pacientes]")[0],
    admH1: h1,
  };
}

function defaultRoutes(over = {}) {
  const table = {
    "GET /api/admin/patients": ok(LIST_BODY),
    "GET /api/admin/dni-claims": ok({ items: [] }),
    "GET /api/admin/patients?q=MAR": ok(SEARCH_BODY),
    "GET /api/admin/patients/30111222": ok(DETAIL_ALEJO),
    "GET /api/admin/patients/32555777": ok(DETAIL_BEATRIZ),
  };
  return (url, method) => {
    const key = method + " " + url;
    if (Object.prototype.hasOwnProperty.call(over, key)) return over[key];
    const hit = table[key];
    if (hit) return hit;
    if (method === "GET" && url.startsWith("/api/admin/patients?")) return ok(LIST_BODY);
    assert.fail("unexpected patients fetch: " + key);
  };
}

// ---------------------------------------------------------------------------
// Readiness: inert import, held/rejected dependencies
// ---------------------------------------------------------------------------

test("import is inert: a held dependency starts no fetch until it resolves", async (t) => {
  const gate = deferred();
  globalThis.__patDepGate = gate.promise;
  t.after(() => { delete globalThis.__patDepGate; });
  const manifest = {
    "platform/admin-session.mjs": dataUrl(SESSION_SRC, "hs"),
    "platform/i18n.mjs": dataUrl(I18N_SRC, "hi"),
    "platform/dates.mjs": dataUrl(DATES_SRC, "hd"),
    "platform/format.mjs": dataUrl(FORMAT_SRC, "hf"),
    "platform/admin-combo.mjs": dataUrl(COMBO_SRC, "hc"),
    "platform/admin-preferences.mjs": dataUrl(PREFS_SRC, "hp"),
    "platform/admin-table.mjs": dataUrl(TABLE_SRC, "ht"),
    "platform/account-form.mjs": dataUrl("await globalThis.__patDepGate; " + ACCOUNT_SRC, "ha"),
    "platform/admin-filter-controls.mjs": FILTERS_URL,
  };
  const f = fixture({ routes: defaultRoutes(), manifest }, t);
  const dispose = f.run();
  await drain(8);
  assert.equal(f.requests.length, 0, "held account-form import must block the list GET");
  gate.resolve();
  await dispose.ready;
  assert.deepEqual(f.gets(), ["/api/admin/patients"]);
});

for (const [label, manifestPatch] of [
  ["missing session manifest key", (m) => { delete m["platform/admin-session.mjs"]; }],
  ["missing account-form manifest key", (m) => { delete m["platform/account-form.mjs"]; }],
  ["rejected table import", (m) => { m["platform/admin-table.mjs"] = "data:text/javascript,throw new Error('table exploded')#x"; }],
  ["invalid session surface (no confirmar)", (m) => { m["platform/admin-session.mjs"] = dataUrl("export const version = 1", "bad"); }],
]) {
  test("dependency failure (" + label + ") paints one load-error line, fetches nothing, rejects ready", async (t) => {
    const manifest = {
      "platform/admin-session.mjs": dataUrl(SESSION_SRC, "es"),
      "platform/i18n.mjs": dataUrl(I18N_SRC, "ei"),
      "platform/dates.mjs": dataUrl(DATES_SRC, "ed"),
      "platform/format.mjs": dataUrl(FORMAT_SRC, "ef"),
      "platform/admin-combo.mjs": dataUrl(COMBO_SRC, "ec"),
      "platform/admin-preferences.mjs": dataUrl(PREFS_SRC, "ep"),
      "platform/admin-table.mjs": dataUrl(TABLE_SRC, "et"),
      "platform/account-form.mjs": dataUrl(ACCOUNT_SRC, "ea"),
      "platform/admin-filter-controls.mjs": FILTERS_URL,
    };
    manifestPatch(manifest);
    const f = fixture({ routes: defaultRoutes(), manifest }, t);
    const dispose = f.run();
    await assert.rejects(dispose.ready, /patients dependency|table exploded/);
    assert.equal(f.requests.length, 0);
    assert.match(f.host.innerHTML, /No se pudo cargar\. Reintentá\./);
    assert.equal(f.windowRef.SanaPacientes, undefined);
    assert.equal(f.windowRef.mountPatients, undefined);
    assert.equal(f.windowRef.renderPacientes, undefined);
  });
}

// ---------------------------------------------------------------------------
// Positive list
// ---------------------------------------------------------------------------

test('switching to another patient withdraws the old editable form before the new response', async (t) => {
  const gate = deferred();
  const f = fixture({ search: '?patient=30111222', routes: defaultRoutes({
    'GET /api/admin/patients/32555777': gate.promise,
  }) }, t);
  await f.run().ready;
  assert.equal(f.child('#pat-name').value, 'Alejo');
  f.windowRef.history.pushState({}, '', '/admin/pacientes?patient=32555777');
  f.windowRef.listeners.get('popstate')();
  assert.equal(f.child('#pac-save'), null, 'old patient data cannot be saved under the new route identity');
  gate.resolve(response(200, DETAIL_BEATRIZ));
  await drain();
  assert.equal(f.child('#pat-name').value, 'Beatriz');
});

for (const confirmed of [false, true]) {
  test('actual shared session confirms whoami before login: ' + confirmed, async (t) => {
    const f = fixture({ realSession: true, routes: defaultRoutes({
      'GET /api/admin/patients': fail(401, { error: 'no_autenticado' }),
      'GET /api/admin/whoami': confirmed ? fail(401, { error: 'no_autenticado' }) : ok({ role: 'admin' }),
    }) }, t);
    await f.run().ready;
    assert.equal(f.gets().filter(x => x === '/api/admin/whoami').length, 1);
    assert.equal(f.windowRef.location.href.startsWith('/admin/login?next='), confirmed);
    if (confirmed) assert.equal(f.host.innerHTML, '');
    else assert.match(f.host.textContent, /No se pudo cargar/, 'live identity keeps a visible local failure');
  });
}

test('actual shared session repairs 2FA without a whoami probe', async (t) => {
  const f = fixture({ realSession: true, routes: defaultRoutes({
    'GET /api/admin/patients': fail(403, { error: '2fa_requerido' }),
  }) }, t);
  await f.run().ready;
  assert.equal(f.windowRef.location.href, '/admin/account?setup=2fa');
  assert.equal(f.gets().includes('/api/admin/whoami'), false);
});

test('actual shared session delayed whoami cannot navigate after disposal', async (t) => {
  const gate = deferred(), entered = deferred();
  const f = fixture({ realSession: true, routes: (url, method) => {
    if (url === '/api/admin/whoami') { entered.resolve(); return gate.promise; }
    return defaultRoutes({ 'GET /api/admin/patients': fail(401, { error: 'no_autenticado' }) })(url, method);
  } }, t);
  const dispose = f.run();
  await entered.promise;
  dispose();
  gate.resolve(response(401, { error: 'no_autenticado' }));
  await dispose.ready;
  assert.equal(f.windowRef.location.href, 'https://example.test/admin/pacientes');
});

test('a post-save dependent refresh cannot erase a newly opened draft and is abort-owned', async (t) => {
  const gate = deferred(), entered = deferred();
  let detailReads = 0;
  const f = fixture({ search: '?patient=30111222', routes: (url, method) => {
    if (method === 'POST') return ok({ ok: true });
    if (url === '/api/admin/patients/30111222' && ++detailReads > 1) { entered.resolve(); return gate.promise; }
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.children('[data-dep-edit]')[0], 'click');
  f.fire(f.child('#dep-save'), 'click');
  await entered.promise;
  const refresh = f.requests.at(-1);
  f.fire(f.child('#pac-dep-add'), 'click');
  f.child('#dep-nombre').value = 'New draft';
  gate.resolve(response(200, DETAIL_ALEJO));
  await drain();
  assert.ok(f.child('#dep-nombre'), 'draft remains mounted');
  assert.equal(f.child('#dep-nombre').value, 'New draft');
  assert.ok(refresh.options.signal?.aborted, 'opening a new draft retires the prior refresh');
});

for (const invalid of [{}, {dependientes: []}, {item: [], dependientes: []}]) test('a malformed post-save dependent refresh preserves rows: ' + JSON.stringify(invalid), async (t) => {
  let detailReads = 0;
  const f = fixture({ search: '?patient=30111222', routes: (url, method) => {
    if (method === 'POST') return ok({ ok: true });
    if (url === '/api/admin/patients/30111222' && ++detailReads > 1) return ok(invalid);
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  const before = f.child('#pat-deps').innerHTML;
  f.fire(f.children('[data-dep-edit]')[0], 'click');
  f.fire(f.child('#dep-save'), 'click');
  await drain();
  assert.equal(f.child('#pat-deps').innerHTML, before, 'an invalid refresh never invents an empty dependent list');
  assert.deepEqual(f.toasts, ['Guardado.', 'No se pudo actualizar las personas a cargo.']);
});

test('K10: «Quién abrió sus datos» only when the server offers it, fetched on click', async (t) => {
  const log = { items: [
    { dia: '2026-10-22', hora: '09:15', usuario: 'dra', rol: 'medico', accion: 'resumen_ia', objeto: 'med-2', permitido: false },
    { dia: '2026-10-21', hora: '11:40', usuario: 'mesa', rol: 'admisionista', accion: 'ficha', objeto: null, permitido: true },
  ], retencion_dias: 3650 };
  for (const offered of [false, true]) {
    const f = fixture({ search: '?patient=30111222', routes: (url, method) => {
      if (url === '/api/admin/patients/30111222') return ok(Object.assign({}, DETAIL_ALEJO, { ver_accesos: offered }));
      if (url === '/api/admin/patients/30111222/access-log') return ok(log);
      return defaultRoutes()(url, method);
    } }, t);
    const dispose = f.run();
    await dispose.ready;
    const logReads = () => f.requests.filter((r) => r.url.endsWith('/access-log')).length;
    if (!offered) {
      assert.equal(f.child('#pat-accesos'), null, 'no section without ver_accesos');
      assert.equal(logReads(), 0);
      dispose();
      continue;
    }
    assert.ok(f.child('#pat-accesos'), 'section offered');
    // Ley 25.326 art. 14: the copy of all the data travels with the same
    // `ver_accesos` (same permission), the same button as its neighbour —
    // nothing is fetched until the admin presses it.
    assert.match(f.child('#pat-accesos').innerHTML,
      /<button type="button" class="btn btn-quiet pat-act" id="pac-datos-exportar">Descargar sus datos<\/button>/);
    assert.equal(f.requests.filter((r) => r.url.endsWith('/data-export')).length, 0);
    assert.equal(logReads(), 0, 'opening the ficha does not fetch the log');
    f.fire(f.child('#pac-accesos-ver'), 'click');
    await drain();
    assert.equal(logReads(), 1);
    const out = f.child('#pat-accesos-out').innerHTML;
    assert.match(out, /Resumen del paciente/);
    assert.match(out, /Negado: no es su paciente/);
    assert.match(out, /mesa/);
    assert.match(out, /se guarda 10 años/);
    assert.equal((out.match(/<th /g) || []).length, 3, 'three columns: the refusal sits next to what was opened');
    dispose();
  }
});

test("a superseded search cannot redirect after its delayed session confirmation", async (t) => {
  const gate = deferred();
  const f = fixture({ sessionGate: gate.promise, routes: defaultRoutes({
    "GET /api/admin/patients?q=MAR": fail(401, { error: "no_autenticado" }),
  }) }, t);
  await f.run().ready;
  f.child('#pac-q').value = 'MAR';
  f.fire(f.child('#pac-q'), 'input'); f.flushTimers();
  await drain();
  assert.equal(globalThis.__patConfirmCalls.length, 1, 'old search reached the real ownership boundary');
  f.child('#pac-q').value = 'fresh';
  f.fire(f.child('#pac-q'), 'input'); f.flushTimers();
  await drain();
  assert.equal(f.child('#pac-q').value, 'fresh');
  gate.resolve(true);
  await drain();
  assert.equal(f.windowRef.location.pathname, '/admin/pacientes');
  assert.ok(f.child('#pat-table'), 'new search survives old session confirmation');
  assert.equal(f.windowRef.location.href, 'https://example.test/admin/pacientes');
});

test('surface styles have one mount owner and are released on disposal', async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const first = f.run(); await first.ready;
  assert.equal(f.doc.head.querySelectorAll('style').length, 1);
  const second = f.run(); await second.ready;
  assert.equal(f.doc.head.querySelectorAll('style').length, 1);
  first();
  assert.equal(f.doc.head.querySelectorAll('style').length, 1, 'old dispose cannot remove fresh style');
  second(); assert.equal(f.doc.head.querySelectorAll('style').length, 0);
});

test('new typed search intent retires old whoami before its debounce fires', async (t) => {
  const gate = deferred(), entered = deferred();
  const f = fixture({ realSession: true, routes: (url, method) => {
    if (url === '/api/admin/whoami') { entered.resolve(); return gate.promise; }
    return defaultRoutes({ 'GET /api/admin/patients?q=old': fail(401, { error: 'no_autenticado' }) })(url, method);
  } }, t);
  await f.run().ready;
  f.child('#pac-q').value = 'old';
  f.fire(f.child('#pac-q'), 'input'); f.flushTimers();
  await entered.promise;
  f.child('#pac-q').value = 'fresh';
  f.fire(f.child('#pac-q'), 'input');
  assert.equal(f.timers.size, 1, 'new read has not started yet');
  gate.resolve(response(401, { error: 'no_autenticado' }));
  await drain();
  assert.equal(f.windowRef.location.href, 'https://example.test/admin/pacientes');
  assert.equal(f.child('#pac-q').value, 'fresh');
  f.flushTimers(); await drain();
  assert.ok(f.gets().includes('/api/admin/patients?q=fresh'));
});

test("adding multiple coverage drafts retains blank rows and registry-owned names", async (t) => {
  const f = fixture({ search: '?patient=30111222', routes: defaultRoutes() }, t);
  await f.run().ready;
  f.fire(f.child('#pat-coverage-add'), 'click');
  f.fire(f.child('#pat-coverage-add'), 'click');
  assert.equal(f.children('.pat-coverage-row[data-cob-idx]').length, 3);
  assert.equal(f.children('[data-cob-nombre]').length, 2);
  assert.equal(f.child('.pat-coverage-fixed').firstChild.textContent, 'PAMI');
  f.fire(f.children('[data-cob-quitar]')[2], 'click');
  assert.equal(f.children('[data-cob-nombre]').length, 1, 'other unfinished row survives removal');
  assert.equal(f.child('.pat-coverage-fixed').firstChild.textContent, 'PAMI');
});

test("the roster paints through the real module: 14-column engine, name links, counts", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.host.getAttribute("data-list-view"), "1");
  assert.ok(f.child("#pac-q"), "search input present");
  assert.doesNotMatch(f.host.innerHTML, /Pacientes con contacto reciente/, "the persistent helper sentence is gone");
  assert.equal(f.children("#pat-table thead th").length, 6, "default column set: Paciente, DNI, Contacto, Servicio más usado, Médico, Turnos (the state only when it differs; the attendance bars sit after the name)");
  const firstServicio = f.child('a[href="/admin/pacientes?patient=30111222"]').closest("tr").querySelector('td[data-col="servicio"]');
  assert.equal(firstServicio.textContent, "Cardiología", "the main service replaces the coverage column");
  assert.equal(f.children('#pat-table thead th').filter((th) => th.getAttribute("data-sort") === "cobertura").length, 0, "no coverage column in the roster");
  assert.ok(f.child('#pat-table a[href^="/admin/pacientes?patient="]'), "patient links present");
  const firstRow = f.child('a[href="/admin/pacientes?patient=30111222"]').closest('tr');
  assert.match(firstRow.innerHTML, /<a class="pat-person" href="\/admin\/pacientes\?patient=30111222">Alejo Varela, \d+<\/a>/, "«Nombre Apellido, 48» as people write it");
  assert.match(firstRow.querySelector('td[data-col="dni"]').innerHTML, /30\.111\.222/, "the DNI is its own column, apart from the one-line name");
  assert.doesNotMatch(firstRow.innerHTML, /pat-dni-sub/, "no second line under the name");
  assert.doesNotMatch(firstRow.querySelector('td[data-col="contacto"]').textContent, /@|\d{6}/, "phone and mail are icons, never printed numbers");
  assert.match(firstRow.querySelector('td[data-col="contacto"]').innerHTML, /href="(tel|mailto):/, "the icons call or write");
  assert.match(firstRow.querySelector('td[data-col="nombre"]').innerHTML, /<\/a><span class="pat-bars[^"]*"/, "attendance is three bars, right after the name on the same line (the product owner, 2026-11-04)");
  const sinHistoria = f.child('a[href="/admin/pacientes?patient=25333444"]').closest("tr");
  assert.doesNotMatch(sinHistoria.innerHTML, /pat-bars/, "no attendance yet draws nothing, not three empty bars (2026-11-05)");
  assert.equal(firstRow.querySelector('td[data-col="ranking"]'), null, "no separate attendance column by default");
  assert.doesNotMatch(firstRow.innerHTML, /años/, "the age is a bare number inside the name, never repeated as a word per row");
  assert.doesNotMatch(firstRow.innerHTML, /chip-activo/, "«Activo» on every row is repeated, so it is not written");
  assert.match(f.host.innerHTML, /id="pat-gear"/, "column gear present");
  assert.equal(f.child("#pat-account"), null, "no counters (responsable del producto, 2026-11-02)");
  assert.equal(f.child("#pat-mas").hidden, false);
  assert.equal(f.child("#pat-mas").textContent, "Cargar más");
  assert.equal(f.child("input.pat-medico-sel"), null, "no doctor dropdown: the doctor is found by typing in the search");
  assert.equal(f.navAnchor().classList.contains("on"), true, "nav highlights pacientes");
  assert.equal(f.admH1.textContent, "Pacientes");
});

// Salud, 30.10: «cuesta mucho encontrar qué turnos tiene cada paciente. En esta
// sección no informa qué turnos se le otorgaron».
test("«Turnos» counts the appointments granted and links to the ficha where they are listed", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const celda = f.child('a[href="/admin/pacientes?patient=30111222"]').closest("tr").querySelector('td[data-col="turnos"]');
  assert.match(celda.innerHTML, /<span class="pat-turnos-n" title="Turnos otorgados: 7 · Próximo: 2026-09-15" aria-label="Turnos otorgados: 7 · Próximo: 2026-09-15">7<\/span>/,
    "the granted count (not the attendance facts) is a plain number, and the hint on hover says what it counts and names the next one");
  assert.doesNotMatch(celda.innerHTML, /<a /, "the count is not a link: the name already opens the ficha");
  // Sin turnos otorgados la celda queda vacía: un guion en cada fila es ruido (ui-expert, 2026-11-02).
  const vacia = f.child('a[href="/admin/pacientes?patient=25333444"]').closest("tr").querySelector('td[data-col="turnos"]');
  assert.equal(vacia.innerHTML, "", "a patient with no granted appointment has an empty cell");
  dispose();
});

test("the ficha lists every appointment: when, service, where, and the state in words", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const sec = f.child("#pat-turnos");
  assert.ok(sec, "Turnos section in the ficha");
  const filas = sec.querySelectorAll("tbody tr");
  assert.equal(filas.length, 3);
  assert.match(filas[0].textContent, /Confirmado.*15 de septiembre.*Dra Sosa.*Cardiología · Hospital de Pinamar/, "status first, then the date and the doctor on one line, then service and place");
  assert.match(filas[1].textContent, /Vino.*Clínica médica.*CAPS Ostende/, "an attended one says Vino, not the approval");
  assert.match(filas[2].textContent, /Pendiente.*Pidió 2026-09-20.*Kinesiología/, "a request without a slot shows the date asked for; the status is one word at every width");
  assert.doesNotMatch(filas[2].textContent, /Esperando respuesta/);
  assert.ok(f.host.innerHTML.indexOf('id="pat-turnos"') < f.host.innerHTML.indexOf('id="pac-form"'), "above the form");
  dispose();
});

test("a patient with no appointments gets a sentence in the ficha, not an empty table", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: (url, method) => {
    if (url === "/api/admin/patients/30111222") return ok(Object.assign({}, DETAIL_ALEJO, { turnos: [] }));
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.match(f.child("#pat-turnos").innerHTML, /Todavía no pidió ni tiene turnos\./);
  assert.equal(f.child("#pat-turnos table"), null);
  dispose();
});

test("the ficha undoes a desk mistake: «No vino» goes back to «Atendió», a cancelled turno is restored or rescheduled", async (t) => {
  // Product owner, 2026-11-05: a «No vino» pressed by accident gets a button to change
  // it; a cancelled turno can be brought back or rescheduled. «Restaurar»
  // only where the server says the time is still free and ahead.
  const turnos = [
    { id: 52, estado: "aprobada", servicio: "Cardiología", centro: "Hospital de Pinamar", medico: "Dra Sosa", dia: "2026-08-10", hora: "10:00", pedido: false, asistencia: "ausente" },
    { id: 51, estado: "cancelada", servicio: "Cardiología", servicio_id: "cardio", centro: "Hospital de Pinamar", medico: "Dra Sosa", dia: "2026-11-20", hora: "09:00", pedido: false, asistencia: "", futuro: true, restaurable: true },
    { id: 50, estado: "cancelada", servicio: "Clínica médica", servicio_id: "clinica", centro: "CAPS Ostende", medico: "", dia: "2026-08-01", hora: "09:00", pedido: false, asistencia: "", restaurable: false },
  ];
  let restore = ok({ ok: true });
  const f = fixture({ search: "?patient=30111222", routes: (url, method) => {
    if (method === "GET" && url === "/api/admin/patients/30111222") return ok(Object.assign({}, DETAIL_ALEJO, { turnos }));
    if (method === "POST" && url === "/api/admin/requests/52/attendance") return ok({ ok: true });
    if (method === "POST" && url === "/api/admin/requests/51/restore") return restore;
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  const fila = (id) => f.child(`[data-pt-fila="${id}"]`);
  const fichaGets = () => f.gets().filter((u) => u === "/api/admin/patients/30111222").length;

  assert.ok(fila(52).querySelector('[data-pt-asis][data-valor="presente"]'), "a «No vino» offers «Atendió»");
  assert.equal(fila(52).querySelector('[data-pt-asis][data-valor="ausente"]'), null, "not the mark it already has");
  assert.equal(fila(51).querySelector("[data-pt-restore]").textContent, "Restaurar");
  assert.equal(fila(51).querySelector("[data-pt-reprog]").textContent, "Reprogramar");
  assert.equal(fila(50).querySelector("[data-pt-restore]"), null, "a time that is taken or gone is not offered back");
  assert.ok(fila(50).querySelector("[data-pt-reprog]"), "but any cancelled turno can be rescheduled");
  assert.equal(fila(50).querySelector("[data-pt-cancel]"), null);

  f.fire(fila(52).querySelector('[data-pt-asis][data-valor="presente"]'), "click");
  await drain(8);
  assert.deepEqual(JSON.parse(f.posts("/api/admin/requests/52/attendance")[0].options.body), { asistencia: "presente" },
    "the same attendance write as the day queue");
  assert.equal(f.toasts.at(-1), "Asistencia guardada.");

  let before = fichaGets();
  f.fire(fila(51).querySelector("[data-pt-restore]"), "click");
  await drain(8);
  assert.equal(f.posts("/api/admin/requests/51/restore").length, 1);
  assert.equal(f.posts("/api/admin/requests/51/restore")[0].options.body, "{}");
  assert.equal(f.toasts.at(-1), "Turno restaurado.");
  assert.ok(fichaGets() > before, "the ficha is read again");

  // Taken between opening the ficha and the click: the remedy, and the ficha
  // is read again so the button goes away.
  restore = fail(409, { error: "turno_tomado" });
  before = fichaGets();
  f.fire(fila(51).querySelector("[data-pt-restore]"), "click");
  await drain(8);
  assert.equal(f.toasts.at(-1), "Ese turno ya no se puede restaurar. Reprogramalo.");
  assert.ok(fichaGets() > before, "a 409 refreshes the ficha");
  dispose();
});

test("an account without a name says «Sin nombre» instead of an unexplained dash", async (t) => {
  const SIN = { dni: "41877103", apellido: "", nombre: "", nacimiento: null, sexo: "", contacto: "", email: "a@example.test",
    cobertura_nombre: "", localidad: "", created: "2026-10-30T10:00:00Z", activo: true,
    ranking: { sin_historial: true, puntaje: 55, etiqueta: "Sin historial", nivel: "none", hechos: 0, motivos: [] },
    turnos: { otorgados: 0, proximo: null } };
  const f = fixture({ routes: (url, method) => {
    if (method === "GET" && url.startsWith("/api/admin/patients") && !url.startsWith("/api/admin/patients/")) return ok(Object.assign({}, LIST_BODY, { items: [SIN] }));
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  const fila = f.child('a[href="/admin/pacientes?patient=41877103"]').closest("tr");
  assert.match(fila.innerHTML, /class="pat-person"[^>]*>Sin nombre<\/a>/);
  assert.match(fila.querySelector('td[data-col="dni"]').innerHTML, /41\.877\.103/);
  dispose();
});

test("clicking a column header sorts, persists the preference, and keyboard sorts too", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  // The person column is the default order (surname, then name, ascending):
  // a click reverses it, Enter and Space keep toggling.
  assert.match(f.children("#pat-table tbody tr")[0].innerHTML, /patient=25333444/, "surname ascending puts Gómez (Juan) first");
  const nombreTh = f.children("#pat-table thead th").filter((th) => th.getAttribute("data-sort") === "nombre")[0];
  f.fire(nombreTh, "click");
  assert.equal(f.child('th[data-sort="nombre"]').getAttribute("aria-sort"), "descending");
  assert.deepEqual(globalThis.__patPrefs.calls.find((c) => c.k === "patients.sort"), { k: "patients.sort", v: { key: "nombre", dir: -1 } });
  assert.match(f.children("#pat-table tbody tr")[0].innerHTML, /patient=30111222/, "surname descending puts Varela (Alejo) first");
  f.fire(f.child('th[data-sort="nombre"]'), "keydown", { key: "Enter" });
  assert.equal(f.child('th[data-sort="nombre"]').getAttribute("aria-sort"), "ascending");
  assert.match(f.children("#pat-table tbody tr")[0].innerHTML, /patient=25333444/);
  f.fire(f.child('th[data-sort="nombre"]'), "keydown", { key: " " });
  assert.equal(f.child('th[data-sort="nombre"]').getAttribute("aria-sort"), "descending");
});

// Responsable del producto, 2026-11-02: «no hace falta un filtro aparte por médicos, habrá muchos;
// que lo escriban en la búsqueda». The doctor is found through the one search.
test("the doctor is found by typing in the search (no doctor dropdown)", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/patients?q=Sosa": ok({ modo: "busqueda", total: 1, items: [ALEJO], medicos: [] }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.child("input.pat-medico-sel"), null, "no doctor combo");
  assert.match(f.child("#pac-q").getAttribute("placeholder"), /médico/i, "the search says it finds by doctor too");
  const q = f.child("#pac-q");
  q.value = "Sosa";
  f.fire(q, "input");
  f.flushTimers();
  await drain();
  assert.equal(f.gets().pop(), "/api/admin/patients?q=Sosa", "the doctor's name goes through q=, never a separate medico=");
  assert.equal(f.children("#pat-table tbody tr").length, 1);
});

test("column filters repaint client-side only", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const getsBefore = f.gets().length;
  const estado = f.child("#pac-f-estado");
  estado.value = "activo";
  f.fire(estado, "change");
  assert.equal(f.gets().length, getsBefore, "column filters never fetch");
  assert.equal(estado.classList.contains("on"), true);
  assert.equal(f.children("#pat-table tbody tr").length, 2, "archived Juan filtered out");
  assert.equal(f.child("#pat-account"), null, "no counters: a client-side column filter changes nothing but the rows");
  assert.deepEqual(globalThis.__patPrefs.calls.find((c) => c.k === "patients.filters"), { k: "patients.filters", v: { estado: "activo", banda: "", cobertura: "" } });
});

test("load-more paginates with real offsets, single-flights duplicate clicks, and hides at exhaustion", async (t) => {
  const page2 = deferred();
  const f = fixture({ routes: (url, method) => {
    if (method === "GET" && url === "/api/admin/patients?offset=3&limit=200") return page2.promise;
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  const mas = f.child("#pat-mas");
  f.fire(mas, "click");
  f.fire(mas, "click");
  f.fire(mas, "click");
  await drain(2);
  assert.equal(f.requests.filter((r) => r.url.includes("offset=3")).length, 1, "duplicate clicks post one page request");
  assert.equal(f.child("#pat-mas").disabled, true);
  assert.equal(f.child("#pat-mas").textContent, "Cargando…");
  page2.resolve(response(200, PAGE2_BODY));
  await drain();
  assert.equal(f.child("#pat-account"), null, "no counter after load-more either");
  assert.ok(f.child('#pat-table a[href="/admin/pacientes?patient=32555777"]'), "second page included in sorted rows");
  assert.equal(f.child("#pat-mas").disabled, false);

  const tiny = fixture({ routes: defaultRoutes({ "GET /api/admin/patients": ok({ modo: null, total: 2, items: [MARIA, BEATRIZ], medicos: [] }) }) }, t);
  await tiny.run().ready;
  assert.equal(tiny.child("#pat-mas").hidden, true, "no load-more when the server count is already shown");
});

test("debounced search refetches with q=, persists, and load-more follows the new query", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const q = f.child("#pac-q");
  q.value = "MAR";
  f.fire(q, "input");
  f.flushTimers();
  await drain();
  assert.equal(f.gets().pop(), "/api/admin/patients?q=MAR");
  assert.equal(f.child("#pat-account"), null, "no counter after a search");
  assert.equal(f.child("#pat-mas").hidden, true);
  assert.deepEqual(globalThis.__patPrefs.calls.find((c) => c.k === "patients.search"), { k: "patients.search", v: "MAR" });
  assert.ok(globalThis.__patFocusLog.includes("pac-q"), "search focus is restored after the repaint");
  assert.equal(q.isConnected && f.child("#pac-q").value, "MAR");
});

// ---------------------------------------------------------------------------
// Progressive disclosure (the product owner, 2026-10-26: «if there's no content, why to
// show all the ui?»): empty → no data tools; filtered to nothing → the filters
// stay and «Limpiar filtros» is the way back; a one-value filter is hidden.
// ---------------------------------------------------------------------------

const TWO_DOCTORS = [{ id: "7", nombre: "Dra Sosa" }, { id: "9", nombre: "Dr Ruiz" }];

test("DISCLOSURE: no patients at all → title, «Nuevo paciente» and one empty state, no data tools", async (t) => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/patients": ok({ modo: "recientes", total: 0, items: [], medicos: TWO_DOCTORS }),
  }) }, t);
  await f.run().ready;
  assert.equal(f.host.getAttribute("data-disclosure"), "empty");
  for (const sel of ["#pac-q", "select", "#pat-gear", "table", "th", "#pat-mas", "[data-clear-filters]"]) {
    assert.equal(f.children(sel).length, 0, sel + " is a data tool: not rendered without data");
  }
  assert.equal(f.child("a.cnt-add"), null, "the primary action lives on the title row (template), not in the toolbar");
  const empty = f.child('[data-empty-state="empty"]');
  assert.ok(empty, "one empty state");
  assert.match(empty.textContent, /Todavía no hay pacientes\./);
  assert.match(empty.textContent, /Nuevo paciente/, "it says what will appear here and from where");
  assert.doesNotMatch(f.host.textContent, /Nadie tuvo contacto reciente/);
});

test("DISCLOSURE: a search that finds nobody keeps the search, shows no empty header, and «Limpiar filtros» brings the list back", async (t) => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/patients?q=ZZZ": ok({ modo: "busqueda", total: 0, items: [], medicos: TWO_DOCTORS }),
  }) }, t);
  await f.run().ready;
  const q = f.child("#pac-q");
  q.value = "ZZZ";
  f.fire(q, "input");
  f.flushTimers();
  await drain();
  assert.equal(f.host.getAttribute("data-disclosure"), "no-match");
  assert.equal(f.child("#pac-q").value, "ZZZ", "the search stays: it is what emptied the list");
  assert.equal(f.children("th").length, 0, "no table header over nothing");
  assert.equal(f.children("#pat-gear").length, 0, "no columns to configure without rows");
  assert.equal(f.child("#pac-f-edad"), null, "no age filter (responsable del producto, 2026-11-03)");
  for (const id of ["#pac-f-estado", "#pac-f-banda", "#pac-f-cobertura"]) {
    assert.equal(f.child(id).hidden, true, id + " has no values to choose between");
  }
  const state = f.child('[data-empty-state="no-match"]');
  assert.match(state.textContent, /Sin resultados para “ZZZ”\./);
  const clear = f.child("[data-clear-filters]");
  assert.equal(clear.textContent, "Limpiar filtros");
  f.fire(clear, "click");
  await drain();
  assert.equal(f.gets().at(-1), "/api/admin/patients");
  assert.deepEqual(globalThis.__patPrefs.calls.filter((c) => c.k === "patients.search").at(-1), { k: "patients.search", v: "" });
  assert.equal(f.host.getAttribute("data-disclosure"), "data");
  assert.equal(f.children("#pat-table tbody tr").length, 3);
  assert.equal(f.child("#pac-q").value, "");
});

test("DISCLOSURE: a column filter that hides every row keeps the filters, and clearing it never fetches", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  await f.run().ready;
  const estado = f.child("#pac-f-estado");
  const cobertura = f.child("#pac-f-cobertura");
  assert.equal(cobertura.hidden, false, "PAMI and OSDE: two coverages to choose between");
  estado.value = "archivado";
  f.fire(estado, "change");
  cobertura.value = "PAMI";
  f.fire(cobertura, "change");
  assert.equal(f.host.getAttribute("data-disclosure"), "no-match");
  assert.equal(f.children("th").length, 0);
  assert.match(f.child('[data-empty-state="no-match"]').textContent, /Ningún paciente coincide con los filtros\./);
  assert.ok(f.child("#pac-f-cobertura"), "the filter that emptied the list stays");
  assert.ok(f.child("#pat-gear"), "rows exist: the column picker stays");
  const gets = f.gets().length;
  f.fire(f.child("[data-clear-filters]"), "click");
  await drain();
  assert.equal(f.gets().length, gets, "a client-side filter clears without a request");
  assert.equal(f.children("#pat-table tbody tr").length, 3);
  assert.equal(f.child("#pac-f-cobertura").value, "");
  assert.deepEqual(globalThis.__patPrefs.calls.filter((c) => c.k === "patients.filters").at(-1).v, { estado: "", banda: "", cobertura: "" });
});

test("DISCLOSURE: a filter shows only with two values in the loaded rows; a set filter stays", async (t) => {
  const f = fixture({ routes: defaultRoutes({
    "GET /api/admin/patients": ok({ modo: "recientes", total: 2, items: [MARIA, BEATRIZ], medicos: [{ id: "7", nombre: "Dra Sosa" }] }),
    "GET /api/admin/patients?q=MAR": ok({ modo: "busqueda", total: 1, items: [MARIA], medicos: [{ id: "7", nombre: "Dra Sosa" }] }),
  }) }, t);
  await f.run().ready;
  assert.equal(f.host.getAttribute("data-disclosure"), "data");
  // Both active, both OSDE, no attendance band: one value each.
  for (const id of ["#pac-f-estado", "#pac-f-banda", "#pac-f-cobertura"]) {
    assert.equal(f.child(id).hidden, true, id + " offers a single value");
  }
  assert.ok(f.child("#pac-q"), "the search is not a one-value filter");
  // A filter already set stays visible so it can be cleared, even with one value.
  const estado = f.child("#pac-f-estado");
  estado.value = "archivado";
  f.fire(estado, "change");
  const q = f.child("#pac-q");
  q.value = "MAR";
  f.fire(q, "input");
  f.flushTimers();
  await drain();
  assert.equal(f.child("#pac-f-estado").hidden, false);
  assert.equal(f.child("#pac-f-estado").value, "archivado");
  assert.equal(f.host.getAttribute("data-disclosure"), "no-match");
  f.fire(f.child("[data-clear-filters]"), "click");
  await drain();
  assert.equal(f.gets().at(-1), "/api/admin/patients", "the search was part of what emptied it");
  assert.equal(f.child("#pac-f-estado").hidden, true);
  assert.equal(f.children("#pat-table tbody tr").length, 2);
});

// Product owner 05.09 C1, 21.10, 26.10: «fewer than ~10 records — do not draw filters
// at all». The registry's own size decides (Filters.FILTER_MIN_ROWS), not
// what a search found: typing never makes the filters jump away.
test("THRESHOLD: a registry under ten patients draws no filter; ten or more does", async (t) => {
  const short = fixture({ routes: defaultRoutes({
    "GET /api/admin/patients": ok({ modo: "recientes", total: 3, items: [MARIA, JUAN, BEATRIZ], medicos: TWO_DOCTORS }),
  }) }, t);
  await short.run().ready;
  assert.equal(short.host.getAttribute("data-disclosure"), "data");
  assert.ok(short.child("#pac-q"), "the search is not a filter: it stays");
  for (const id of ["#pac-f-estado", "#pac-f-cobertura"]) {
    assert.equal(short.child(id).hidden, true, id + " on a three-row registry");
  }

  const long = fixture({ routes: defaultRoutes({
    "GET /api/admin/patients": ok({ modo: "recientes", total: 12, items: [MARIA, JUAN, BEATRIZ], medicos: TWO_DOCTORS }),
    "GET /api/admin/patients?q=MAR": ok({ modo: "busqueda", total: 1, items: [MARIA], medicos: TWO_DOCTORS }),
  }) }, t);
  await long.run().ready;
  for (const id of ["#pac-f-estado", "#pac-f-cobertura"]) {
    assert.equal(long.child(id).hidden, false, id + " on a twelve-row registry");
  }
  const q = long.child("#pac-q");
  q.value = "MAR";
  long.fire(q, "input");
  long.flushTimers();
  await drain();
  assert.equal(long.child("#pac-q").value, "MAR", "the search keeps what was typed");
});

// ---------------------------------------------------------------------------
// The three reproduced races of the classic controller
// ---------------------------------------------------------------------------

function nameLink(f, dni) {
  const link = f.child('#pat-table a[href="/admin/pacientes?patient=' + dni + '"]');
  assert.ok(link, "roster row link for " + dni);
  return link;
}

test("RACE-1: a late list success never repaints over a rendered patient detail", async (t) => {
  const searchList = deferred();
  const f = fixture({ routes: (url, method) => {
    if (method === "GET" && url === "/api/admin/patients?q=MAR") return searchList.promise;
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  const q = f.child("#pac-q");
  q.value = "MAR";
  f.fire(q, "input");
  f.flushTimers();
  await drain(2);
  assert.equal(f.gets().length, 2, "debounced search request is out");
  f.fire(nameLink(f, "30111222"), "click");
  await drain();
  assert.ok(f.child("#pac-form"), "detail rendered");
  assert.equal(f.child("#pat-table"), null, "roster table replaced by the detail");
  assert.equal(globalThis.location.search, "?patient=30111222");
  searchList.resolve(response(200, SEARCH_BODY));
  await drain(8);
  assert.ok(f.child("#pac-form"), "patient detail (#pac-form) survives the late list response");
  assert.equal(f.child("#pat-table"), null, "no roster table over the detail");
  assert.equal(globalThis.location.search, "?patient=30111222", "URL is still the detail route");
  assert.equal(f.child("#pat-account"), null);
});

test("RACE-2: a late detail failure never paints over the new-patient form", async (t) => {
  const detailA = deferred();
  const f = fixture({
    search: "?patient=30111222",
    routes: (url, method) => (method === "GET" && url === "/api/admin/patients/30111222" ? detailA.promise : defaultRoutes()(url, method)),
  }, t);
  const dispose = f.run();
  await drain(4);
  assert.equal(f.requests.length, 1, "patient A's detail fetch is pending");
  globalThis.history.pushState({}, "", "/admin/pacientes?new=1");
  f.windowRef.listeners.get("popstate")();
  await drain();
  assert.ok(f.child("#pat-dni"), "new-patient form rendered");
  assert.equal(globalThis.location.search, "?new=1");
  detailA.reject(new Error("network down"));
  await drain(8);
  assert.ok(f.child("#pat-dni"), "new-patient form (#pat-dni) survives patient A's late failure");
  assert.doesNotMatch(f.host.innerHTML, /No se pudo cargar/, "route catch's load-error line does NOT replace the form");
  assert.equal(globalThis.location.search, "?new=1", "URL is still the new-patient route");
});

test("A11Y: every field of the new-patient form has a label bound to it (usability study 2026-10-16)", async (t) => {
  const f = fixture({ search: "?new=1" }, t);
  const dispose = f.run();
  await drain(8);
  const html = f.host.innerHTML;
  for (const [id, text] of [["pat-dni", "DNI"], ["pat-name", "Nombre"], ["pac-apellido", "Apellido"],
    ["pat-birth", "Nacimiento"], ["pat-sex", "Sexo"], ["pac-contacto", "Teléfono"], ["pat-email", "Email"]]) {
    assert.ok(f.child("#" + id), id + " is in the form");
    assert.match(html, new RegExp('<label for="' + id + '">' + text + "</label>"), id + " is named by its visible label");
  }
  for (const id of ["pac-prepaga", "pac-prepaga-af", "pac-prepaga-plan"]) {
    assert.match(html, new RegExp('id="' + id + '"[^>]*placeholder="([^"]+)" aria-label="\\1"'), id + " keeps its placeholder and is named for assistive tech");
  }
  if (dispose) dispose();
});

test("RACE-3: a late detail failure never paints over another patient's rendered detail", async (t) => {
  const detailA = deferred();
  const f = fixture({
    search: "?patient=30111222",
    routes: (url, method) => (method === "GET" && url === "/api/admin/patients/30111222" ? detailA.promise : defaultRoutes()(url, method)),
  }, t);
  const dispose = f.run();
  await drain(4);
  globalThis.history.pushState({}, "", "/admin/pacientes?patient=32555777");
  f.windowRef.listeners.get("popstate")();
  await drain();
  assert.equal(f.child("#pat-name").value, "Beatriz");
  detailA.reject(new Error("network down"));
  await drain(8);
  assert.ok(f.child("#pac-form"), "patient B's detail survives patient A's late failure");
  assert.equal(f.child("#pat-name").value, "Beatriz");
  assert.doesNotMatch(f.host.innerHTML, /No se pudo cargar/);
  assert.equal(globalThis.location.search, "?patient=32555777");
});

test("a pending search debounce is cancelled by a route switch: no request ever leaves", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const q = f.child("#pac-q");
  q.value = "MAR";
  f.fire(q, "input");
  assert.equal(f.timers.size, 1, "debounce timer pending");
  f.fire(nameLink(f, "30111222"), "click");
  await drain();
  assert.ok(f.child("#pac-form"));
  f.flushTimers();
  await drain(4);
  assert.equal(f.gets().length, 2, "only the initial list and the detail — the search never fired");
  assert.equal(globalThis.location.search, "?patient=30111222");
});

test("an old load-more page resolving after a new search never concatenates", async (t) => {
  const page = deferred();
  const f = fixture({ routes: (url, method) => {
    if (method === "GET" && url === "/api/admin/patients?offset=3&limit=200") return page.promise;
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#pat-mas"), "click");
  await drain(2);
  const q = f.child("#pac-q");
  q.value = "MAR";
  f.fire(q, "input");
  f.flushTimers();
  await drain();
  assert.equal(f.children("#pat-table tbody tr").length, 1);
  page.resolve(response(200, PAGE2_BODY));
  await drain(8);
  assert.equal(f.children("#pat-table tbody tr").length, 1, "stale page response did not append Beatriz");
});

// ---------------------------------------------------------------------------
// Intercepted navigation and URL semantics
// ---------------------------------------------------------------------------

test("internal links are intercepted (pushState + route) instead of reloading", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const event = f.fire(nameLink(f, "30111222"), "click");
  assert.equal(event.defaultPrevented, true, "the interceptor consumes the click");
  assert.deepEqual(f.pushes, ["/admin/pacientes?patient=30111222"]);
  await drain();
  assert.ok(f.child("#pac-form"));
  const crumb = f.child('.cnt-crumb a[href="/admin/pacientes"]');
  f.fire(crumb, "click");
  await drain();
  assert.ok(f.child("#pac-q"), "crumb returns to the list without a reload");
  assert.equal(f.assigns.length, 0);
});

test("?patient=DNI&new=1 canonicalises to the detail route, not the form", async (t) => {
  const f = fixture({ search: "?patient=30111222&new=1", routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.deepEqual(f.replaces, ["/admin/pacientes?patient=30111222"]);
  assert.ok(f.child("#pac-form"), "detail rendered");
  assert.equal(f.child("#pat-dni"), null, "no free DNI field on an edit form");
});

// ---------------------------------------------------------------------------
// Session boundary
// ---------------------------------------------------------------------------

test("ordinary 403 stays local with the no-permission line; confirmar got the predicate", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/patients": fail(403, { error: "solo admin" }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(globalThis.__patConfirmCalls.length, 1);
  assert.equal(globalThis.__patConfirmCalls[0].hasPredicate, true);
  assert.equal(globalThis.__patConfirmCalls[0].status, 403);
  assert.match(f.host.innerHTML, /No tenés permiso para ver esta sección\./);
  assert.equal(globalThis.location.href, "https://example.test/admin/pacientes");
});

test("confirmed 401 expiry transitions out: host cleared, module disposed, no error paint", async (t) => {
  const f = fixture({ routes: defaultRoutes({ "GET /api/admin/patients": fail(401, { error: "no_autenticado" }) }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.match(globalThis.location.href, /\/admin\/login\?next=/);
  assert.equal(f.host.innerHTML, "");
  assert.equal(f.child("#pac-q"), null);
  assert.equal(f.windowRef.listeners.has("popstate"), false, "module disposed its window listener");
  assert.equal(f.doc.listeners.has("click"), false, "module disposed its document listener");
  assert.equal(globalThis.__patColsDisposed >= 1, true, "column owner released");
});

test("a session confirmation held past dispose cannot navigate", async (t) => {
  const gate = deferred();
  const f = fixture({
    sessionGate: gate.promise,
    routes: defaultRoutes({ "GET /api/admin/patients": fail(401, { error: "no_autenticado" }) }),
  }, t);
  const dispose = f.run();
  await drain(6);
  assert.equal(globalThis.__patConfirmCalls.length, 1);
  assert.equal(globalThis.__patConfirmCalls[0].current, true, "predicate was live when confirmar started");
  dispose();
  gate.resolve(true);
  await drain(8);
  assert.equal(globalThis.location.href, "https://example.test/admin/pacientes", "disposed owner never navigates");
});

test("a session confirmation held across a route switch belongs to the dead route", async (t) => {
  const gate = deferred();
  const f = fixture({
    search: "?patient=30111222",
    sessionGate: gate.promise,
    routes: (url, method) => (method === "GET" && url === "/api/admin/patients/30111222" ? fail(401, { error: "no_autenticado" }) : defaultRoutes()(url, method)),
  }, t);
  const dispose = f.run();
  await drain(6);
  assert.equal(globalThis.__patConfirmCalls.length, 1);
  globalThis.history.pushState({}, "", "/admin/pacientes");
  f.windowRef.listeners.get("popstate")();
  await drain();
  assert.ok(f.child("#pac-q"), "the list route took over");
  gate.resolve(true);
  await drain(8);
  assert.equal(globalThis.location.href, "https://example.test/admin/pacientes", "stale 401 cannot navigate the new route away");
  assert.ok(f.child("#pat-table"), "stale 401 cannot clear the newer route's paint");
});

// ---------------------------------------------------------------------------
// Mutations: single-flight, acknowledgements, stale continuations
// ---------------------------------------------------------------------------

function fillNewPatient(f, { dni = "30111999", nombre = "Nueva", apellido = "Persona", nac = "28/10/1978", sexo = "F" } = {}) {
  f.child("#pat-sex").value = sexo;
  f.child("#pat-dni").value = dni;
  f.child("#pat-name").value = nombre;
  f.child("#pac-apellido").value = apellido;
  f.child("#pat-birth").value = nac;
}

test("creating a patient: one POST for three clicks, temp password shown once, no redirect", async (t) => {
  const gate = deferred();
  const f = fixture({ search: "?new=1", routes: (url, method) => {
    if (method === "POST" && url === "/api/admin/patients") return gate.promise;
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  await drain();
  fillNewPatient(f);
  f.fire(f.child("#pac-save"), "click");
  f.fire(f.child("#pac-save"), "click");
  f.fire(f.child("#pac-save"), "click");
  await drain(2);
  assert.equal(f.posts("/api/admin/patients").length, 1, "duplicate save events are single-flight");
  assert.equal(f.child('#pac-save').disabled, true, 'pending save is visibly busy');
  const body = JSON.parse(f.posts("/api/admin/patients")[0].options.body);
  assert.equal(body.dni, "30111999");
  assert.equal(body.nacimiento, "1978-10-28", "the typed 28/10/1978 is sent as the ISO the system understood");
  assert.deepEqual(body.cobertura, { obras_sociales: [], prepaga: null, principal: "" }, "coverage is always sent, even empty");
  assert.equal("password" in body, false, "empty password field is omitted, the server generates one");
  gate.resolve(response(200, { ok: true, item: { dni: "30111999" }, password_temporal: "temp-9" }));
  await drain();
  assert.match(f.child("#pac-out").innerHTML, /temp-9/);
  assert.match(f.host.innerHTML, /no se vuelve a mostrar/);
  assert.deepEqual(f.toasts, ["Paciente creado."]);
  assert.equal(f.assigns.length, 0, "the temporary password stays on screen — no redirect");
});

test("editing a patient sends the full normalised body and redirects to the list on ack", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes({
    "PUT /api/admin/patients/30111222": ok({ ok: true, item: ALEJO }),
  }) }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.child("#pat-birth").value, "28/10/1978", "DOB field shows dd/mm/aaaa");
  assert.equal(f.child("#pat-dni"), null, "DNI is fixed on edit");
  assert.equal(f.child("#pac-pass"), null, "no password field on edit");
  f.child("#pat-name").value = "Alejandro";
  f.child("#pac-contacto").value = "2254111222";
  f.fire(f.child("#pac-save"), "click");
  await drain();
  const put = f.requests.filter((r) => r.method === "PUT" && r.url === "/api/admin/patients/30111222")[0];
  assert.ok(put, "edit saves via PUT");
  const body = JSON.parse(put.options.body);
  assert.equal(body.nombre, "Alejandro");
  assert.equal(body.email, ALEJO.email, "saving another field must preserve the rendered email");
  assert.equal(body.nacimiento, "1978-10-28");
  assert.equal(body.cobertura.principal, "PAMI");
  assert.deepEqual(body.cobertura.obras_sociales, [{ nombre: "PAMI", afiliado: "123" }]);
  assert.deepEqual(f.toasts, ["Guardado."]);
  assert.deepEqual(f.assigns, ["/admin/pacientes"]);
});

for (const scenario of [
  { name: "unchanged", create: false, value: "alejo@example.test", expected: "alejo@example.test" },
  { name: "edited", create: false, value: "  updated@example.test  ", expected: "updated@example.test" },
  { name: "cleared", create: false, value: "", expected: null },
  { name: "new patient", create: true, value: "new@example.test", expected: "new@example.test" },
]) test(`patient email survives form serialization: ${scenario.name}`, async (t) => {
  const method = scenario.create ? "POST" : "PUT";
  const url = "/api/admin/patients" + (scenario.create ? "" : "/30111222");
  const f = fixture({ search: scenario.create ? "?new=1" : "?patient=30111222", routes: defaultRoutes({
    [method + " " + url]: ok({ ok: true, item: ALEJO }),
  }) }, t);
  await f.run().ready;
  if (scenario.create) fillNewPatient(f);
  const email = f.child('input[type="email"]');
  assert.ok(email, "use the actual rendered email control, not a fabricated selector fixture");
  email.value = scenario.value;
  f.fire(email, "input");
  f.fire(f.child("#pac-save"), "click");
  await drain();
  const writes = f.requests.filter(r => r.method === method && r.url === url);
  assert.equal(writes.length, 1);
  assert.equal(JSON.parse(writes[0].options.body).email, scenario.expected);
});

test("HTTP 200 without {ok,item} is not a save acknowledgement", async (t) => {
  const f = fixture({ search: "?new=1", routes: defaultRoutes({
    "POST /api/admin/patients": ok({ item: { dni: "30111999" } }),
  }) }, t);
  const dispose = f.run();
  await dispose.ready;
  await drain();
  fillNewPatient(f);
  f.fire(f.child("#pac-save"), "click");
  await drain();
  assert.deepEqual(f.toasts, ["No se pudo guardar."]);
  assert.equal(f.assigns.length, 0);
  assert.equal(f.child("#pac-out").innerHTML, "", "no temp-password block for a non-acknowledgement");
});

for (const mutation of ['save', 'archive']) {
  test('a disproved 401 ' + mutation + ' failure stays visible on its current owner', async (t) => {
    const f = fixture({ realSession: true, search: '?patient=30111222', confirm: () => true, routes: (url, method) => {
      if (url === '/api/admin/whoami') return ok({ role: 'admin' });
      if (url === '/api/admin/patients/30111222' && method === (mutation === 'save' ? 'PUT' : 'DELETE')) {
        return fail(401, { error: 'no_autenticado' });
      }
      return defaultRoutes()(url, method);
    } }, t);
    const dispose = f.run();
    await dispose.ready;
    f.fire(f.child(mutation === 'save' ? '#pac-save' : '#pac-arch'), 'click');
    await drain();
    assert.equal(f.windowRef.location.href, 'https://example.test/admin/pacientes?patient=30111222');
    assert.ok(f.child('#pac-save'), 'the current form remains usable after a local failure');
    // The failure stays on screen in words, never as the server's code (TASK-48).
    assert.deepEqual(f.toasts, [mutation === 'save' ? 'No se pudo guardar.' : 'No se pudo.'], 'the current owner exposes the server failure without redirecting');
  });
}

test("a stale save resolving after a route switch toasts nothing and frees the flight", async (t) => {
  const gate = deferred();
  const f = fixture({ search: "?patient=30111222", routes: (url, method) => {
    if (method === "PUT" && url === "/api/admin/patients/30111222") return gate.promise;
    if (method === "GET" && url === "/api/admin/patients") return ok({ ...LIST_BODY, items: [ALEJO, MARIA, BEATRIZ] });
    if (method === "PUT" && url === "/api/admin/patients/32555777") return ok({ ok: true, item: BEATRIZ });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#pac-save"), "click");
  await drain(2);
  const putSignal = f.requests.filter((r) => r.method === "PUT")[0].options.signal;
  f.fire(f.child('.cnt-crumb a[href="/admin/pacientes"]'), "click");
  await drain();
  assert.equal(putSignal.aborted, true, "the route switch aborts the in-flight save");
  gate.resolve(response(200, { ok: true, item: {} }));
  await drain(8);
  assert.deepEqual(f.toasts, [], "stale success never toasts");
  assert.deepEqual(f.assigns, [], "stale success never redirects");
  assert.ok(f.child("#pat-table"), "the list route owns the screen");
  // The dead flight truly released the busy state: a new route can save again.
  f.fire(nameLink(f, "32555777"), "click");
  await drain();
  f.fire(f.child("#pac-save"), "click");
  await drain(2);
  assert.equal(f.requests.filter((r) => r.method === "PUT" && r.url === "/api/admin/patients/32555777").length, 1);
});

test("archiving asks first; restoring does not; both acknowledge before redirecting", async (t) => {
  let confirmado = true;
  const f = fixture({ search: "?patient=30111222", confirm: () => confirmado, routes: (url, method) => {
    if (method === "DELETE" && url === "/api/admin/patients/30111222") return ok({ ok: true, item: {} });
    if (method === "PUT" && url === "/api/admin/patients/30111222") return ok({ ok: true, item: {} });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.child("#pac-arch").textContent, "Archivar");
  confirmado = false;
  f.fire(f.child("#pac-arch"), "click");
  await drain();
  assert.equal(f.requests.filter((r) => r.method === "DELETE").length, 0, "cancelled confirmation never deletes");
  assert.match(f.confirms[0], /¿Archivar este paciente\?/);
  confirmado = true;
  f.fire(f.child("#pac-arch"), "click");
  await drain();
  assert.equal(f.requests.filter((r) => r.method === "DELETE").length, 1);
  assert.deepEqual(f.toasts, ["Paciente archivado."]);
  assert.deepEqual(f.assigns, ["/admin/pacientes"]);

  const quiet = fixture({ search: "?patient=30111222", confirm: () => { throw new Error("restore must not ask"); }, routes: (url, method) => {
    if (method === "PUT" && url === "/api/admin/patients/30111222") return ok({ ok: true, item: {} });
    return defaultRoutes({ "GET /api/admin/patients/30111222": ok(Object.assign({}, DETAIL_ALEJO, { item: Object.assign({}, DETAIL_ALEJO.item, { activo: false }) })) })(url, method);
  } }, t);
  await quiet.run().ready;
  assert.equal(quiet.child("#pac-arch").textContent, "Restaurar");
  quiet.fire(quiet.child("#pac-arch"), "click");
  await drain();
  assert.deepEqual(quiet.confirms, []);
  const restore = quiet.requests.filter((r) => r.method === "PUT")[0];
  assert.deepEqual(JSON.parse(restore.options.body), { activo: true });
  assert.deepEqual(quiet.toasts, ["Paciente restaurado."]);
});

// TASK-68 step c: a document-number claim waits above the roster; confirming
// asks first and posts the decision once.
test("a document claim shows above the roster and «Confirmar» asks, then posts the decision", async (t) => {
  const claim = { id: 7, dni: "30111222", estado: "pending", nombre: "Ana", apellido: "Gil", titular_nombre: "Beatriz", titular_apellido: "Soto" };
  let claims = [claim];
  const f = fixture({ confirm: () => true, routes: (url, method) => {
    if (method === "GET" && url === "/api/admin/dni-claims") return ok({ items: claims });
    if (method === "POST" && url === "/api/admin/dni-claims/7/resolve") { claims = []; return ok({ ok: true }); }
    return defaultRoutes()(url, method);
  } }, t);
  await f.run().ready;
  await drain(8);
  assert.match(f.host.innerHTML, /1 reclamo de documento/);
  assert.match(f.host.innerHTML, /Ana Gil pide el DNI 30111222\. Hoy es de Beatriz Soto\./);
  assert.ok(f.host.innerHTML.indexOf("pat-claims") < f.host.innerHTML.indexOf("cnt-toolbar"), "the claims sit above the toolbar");
  const btn = f.children("[data-claim-resolve]").filter((b) => b.getAttribute("data-claim-resolve") === "1")[0];
  f.fire(btn, "click");
  await drain(8);
  assert.match(f.confirms[0], /¿Confirmar\?/);
  const posts = f.requests.filter((r) => r.method === "POST");
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].options.body), { aprobar: true });
});

// TASK-50 (26.10): a patient without email who forgot the password gets a
// new temporary one at the desk — asked first, printed once, like the one a
// new patient gets.
test("«Nueva contraseña temporal» asks first, posts once and prints the password once", async (t) => {
  let confirmado = false;
  const nueva = ["Tmp", "abc123xyz"].join("-");
  const f = fixture({ search: "?patient=30111222", confirm: () => confirmado, routes: (url, method) => {
    if (method === "POST" && url === "/api/admin/patients/30111222/temp-password") return ok({ ok: true, password_temporal: nueva });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  assert.equal(f.child("#pac-temp-pass").textContent, "Nueva contraseña temporal");
  f.fire(f.child("#pac-temp-pass"), "click");
  await drain();
  assert.equal(f.requests.filter((r) => r.method === "POST").length, 0, "a cancelled confirmation changes nothing");
  assert.match(f.confirms[0], /contraseña temporal nueva/);
  confirmado = true;
  f.fire(f.child("#pac-temp-pass"), "click");
  await drain();
  const posts = f.requests.filter((r) => r.method === "POST");
  assert.deepEqual(posts.map((r) => r.url), ["/api/admin/patients/30111222/temp-password"]);
  // Printed right under the button, not at the foot of the form below
  // «Personas a cargo», where the desk would not see it (frame 390, 26.10).
  assert.match(f.child("#pac-temp-out").innerHTML, /<code>Tmp-abc123xyz<\/code>/);
  assert.equal(f.child("#pac-out").innerHTML, "");
  assert.deepEqual(f.assigns, [], "the password stays on screen; no redirect");
});

test("archiving a patient with live appointments lists them and cancels only after a second yes", async (t) => {
  // 2026-10-23 (problems.md №6): the server refuses with 409 and the list;
  // the desk sees it and confirms; the same DELETE returns with
  // ?cancelar_turnos=1.
  const live = { error: "turnos_activos", turnos: [
    { id: 7, estado: "aprobada", servicio: "Clínica médica", dia: "2099-03-02", hora: "09:30" },
    { id: 8, estado: "pendiente", servicio: "Cardiología", dia: null, hora: null },
  ] };
  let segunda = true;
  const f = fixture({ search: "?patient=30111222", confirm: (msg) => (/turnos por venir/.test(msg) ? segunda : true), routes: (url, method) => {
    if (method === "DELETE" && url === "/api/admin/patients/30111222") return fail(409, live);
    if (method === "DELETE" && url === "/api/admin/patients/30111222?cancelar_turnos=1") return ok({ ok: true, item: {}, turnos_cancelados: 2 });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  segunda = false;
  f.fire(f.child("#pac-arch"), "click");
  await drain(6);
  assert.match(f.confirms[1], /Tiene 2 turnos por venir/);
  assert.match(f.confirms[1], /Clínica médica — /);
  assert.match(f.confirms[1], /Cardiología — pendiente, sin horario/);
  assert.equal(f.requests.filter((r) => r.url.includes("cancelar_turnos")).length, 0, "a no keeps every appointment");
  assert.deepEqual(f.assigns, []);
  segunda = true;
  f.fire(f.child("#pac-arch"), "click");
  await drain(6);
  assert.equal(f.requests.filter((r) => r.url === "/api/admin/patients/30111222?cancelar_turnos=1").length, 1);
  assert.deepEqual(f.toasts, ["Paciente archivado."]);
  assert.deepEqual(f.assigns, ["/admin/pacientes"]);
});

test("archiving: another center's appointments are only counted, and alone they ask no second yes", async (t) => {
  // The server describes only what the account can see (404 convention).
  const body = { error: "turnos_activos", turnos: [], turnos_ajenos: 2 };
  const f = fixture({ search: "?patient=30111222", routes: (url, method) => {
    if (method === "DELETE" && url === "/api/admin/patients/30111222") return fail(409, body);
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#pac-arch"), "click");
  await drain(6);
  assert.equal(f.requests.filter((r) => r.url.includes("cancelar_turnos")).length, 0);
  assert.equal(f.toasts.length, 1);
  assert.match(f.toasts[0], /no podés cancelar desde tu cuenta/);
  assert.deepEqual(f.assigns, []);
});

// ---------------------------------------------------------------------------
// Birth-date echo (shared parser) and save-blocking
// ---------------------------------------------------------------------------

test("the birth echo speaks in words, flags garbage on blur, and blocks the save", async (t) => {
  // Pinned clock: the age in the echo must not change on the real 28 October.
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes(), now: new Date("2026-10-27T12:00:00Z") }, t);
  const dispose = f.run();
  await dispose.ready;
  const birth = f.child("#pat-birth");
  const eco = f.child("#pat-birth-eco");
  birth.value = "28/10/1978";
  f.fire(birth, "input");
  assert.equal(eco.textContent, "28 de octubre de 1978 · 47 años");
  assert.equal(globalThis.__patDateParses.includes("28/10/1978"), true, "the shared account-form parser did the work");
  birth.value = "pronto";
  f.fire(birth, "input");
  assert.equal(eco.textContent, "", "no error while typing");
  f.fire(birth, "blur");
  assert.match(eco.textContent, /No entiendo esa fecha\. Probá 28\/10\/1978\./);
  f.fire(f.child("#pac-save"), "click");
  await drain();
  assert.equal(f.requests.filter((r) => r.method !== "GET").length, 0, "an unreadable date blocks the save");
  assert.ok(globalThis.__patFocusLog.includes("pat-birth"));
});

test("the birth echo counts the age from the pinned clock, and the birthday itself adds the year", async (t) => {
  // The format stub reads the fixture clock (__patNow), never the machine's: the echo must not change on the real birthday.
  for (const [day, years] of [["2026-10-27", 47], ["2026-10-28", 48], ["2026-12-31", 48], ["2027-01-01", 48], ["2027-10-27", 48]]) {
    await t.test(day, async (st) => {
      const f = fixture({ search: "?patient=30111222", routes: defaultRoutes(), now: new Date(day + "T12:00:00Z") }, st);
      const dispose = f.run();
      await dispose.ready;
      const birth = f.child("#pat-birth");
      birth.value = "28/10/1978";
      f.fire(birth, "input");
      assert.equal(f.child("#pat-birth-eco").textContent, "28 de octubre de 1978 · " + years + " años");
      dispose();
    });
  }
});

// ---------------------------------------------------------------------------
// Dependents and custody
// ---------------------------------------------------------------------------

test("the dependents table names links, shows shared custody, and adds a dependent end-to-end", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: (url, method) => {
    if (method === "POST" && url === "/api/admin/patients/30111222/dependents") return ok({ ok: true });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  const row = f.children("#pat-deps tbody tr")[0];
  assert.match(row.innerHTML, /<a class="pat-person" href="\/admin\/pacientes\?patient=25333444">Tomás Varela, 11<\/a>/);
  assert.match(row.innerHTML, /Hijo/);
  assert.match(f.child('#pat-deps thead').textContent, /También a cargo de/);
  assert.match(row.innerHTML, /href="\/admin\/pacientes\?patient=28777666"/);

  f.fire(f.child("#pac-dep-add"), "click");
  assert.ok(f.child("#dep-dni"), "dependent form opened");
  f.child("#dep-dni").value = "27000111";
  f.child("#dep-nombre").value = "Sofía";
  f.child("#dep-apellido").value = "Varela";
  f.child("#dep-nac").value = "15/03/2015";
  f.fire(f.child("#dep-save"), "click");
  await drain(2);
  const post = f.posts("/api/admin/patients/30111222/dependents")[0];
  const body = JSON.parse(post.options.body);
  assert.equal(body.dni, "27000111");
  assert.equal(body.nacimiento, "2015-03-15");
  assert.equal(body.vinculo, "hijo");
  assert.deepEqual(f.toasts, ["Guardado."]);
  assert.equal(f.child("#pat-dep-form").innerHTML, "", "form cleared after the ack");
  await drain();
  assert.equal(f.gets().length, 2, "the dependent section refetched the detail");
});

test("an unreadable dependent DOB blocks the POST inside its own field", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#pac-dep-add"), "click");
  f.child("#dep-nac").value = "mañana";
  f.fire(f.child("#dep-save"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/patients/30111222/dependents").length, 0);
  assert.match(f.child("#dep-nac-eco").textContent, /No entiendo esa fecha/);
  assert.ok(globalThis.__patFocusLog.includes("dep-nac"));
});

test("editing a dependent locks the DNI and posts it as the identity", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: (url, method) => {
    if (method === "POST" && url === "/api/admin/patients/30111222/dependents") return ok({ ok: true });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.children("[data-dep-edit]")[0], "click");
  assert.equal(f.child("#dep-dni"), null, "no editable DNI while editing");
  assert.match(f.host.innerHTML, /25\.333\.444/);
  f.child("#dep-nombre").value = "Tomás A.";
  f.fire(f.child("#dep-save"), "click");
  await drain(2);
  const body = JSON.parse(f.posts("/api/admin/patients/30111222/dependents")[0].options.body);
  assert.equal(body.dni, "25333444", "the edited dependent keeps its own DNI");
  assert.equal(body.nombre, "Tomás A.");
});

test("removing shared custody says who keeps it; the last adult gets the history warning", async (t) => {
  let confirmado = true;
  const f = fixture({ search: "?patient=30111222", confirm: () => confirmado, routes: (url, method) => {
    if (method === "DELETE" && url === "/api/admin/patients/30111222/dependents/25333444") return ok({ ok: true });
    return defaultRoutes()(url, method);
  } }, t);
  const dispose = f.run();
  await dispose.ready;
  confirmado = false;
  f.fire(f.children("[data-dep-quitar]")[0], "click");
  await drain();
  assert.match(f.confirms[0], /Sigue a cargo de López, María/);
  assert.equal(f.requests.filter((r) => r.method === "DELETE").length, 0);
  confirmado = true;
  f.fire(f.children("[data-dep-quitar]")[0], "click");
  await drain();
  assert.equal(f.requests.filter((r) => r.method === "DELETE").length, 1);
  assert.deepEqual(f.toasts, ["Listo."]);

  const solo = fixture({ search: "?patient=30111222", confirm: () => true, routes: defaultRoutes({
    "GET /api/admin/patients/30111222": ok(DETAIL_ALEJO_SOLO),
    "DELETE /api/admin/patients/30111222/dependents/25333444": ok({ ok: true }),
  }) }, t);
  await solo.run().ready;
  solo.fire(solo.children("[data-dep-quitar]")[0], "click");
  assert.match(solo.confirms[0], /Los turnos que ya sacó quedan/);
});

// ---------------------------------------------------------------------------
// Coverage: PUCO fixed names, explicit clearing, principal key
// ---------------------------------------------------------------------------

test("PUCO rows show the registry-fixed name with an editable member number", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const fixed = f.child(".pat-coverage-fixed");
  assert.ok(fixed, "fixed-name span present");
  assert.equal(fixed.textContent, "PAMIsegún el padrón");
  assert.equal(f.child("[data-cob-nombre]"), null, "no editable insurer name for a registry row");
  assert.equal(f.child("[data-cob-afiliado]").value, "123", "member number stays editable");
  assert.equal(f.child('input[value="PAMI"]').checked, true, "server-suggested principal is visibly checked");
});

test("coverage clearing is explicit: a removed row saves as an empty list", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes({
    "PUT /api/admin/patients/30111222": ok({ ok: true, item: ALEJO }),
  }) }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.children("[data-cob-quitar]")[0], "click");
  assert.equal(f.children(".pat-coverage-row[data-cob-idx]").length, 0, "row left the form");
  f.fire(f.child("#pac-save"), "click");
  await drain();
  const body = JSON.parse(f.requests.filter((r) => r.method === "PUT")[0].options.body);
  assert.deepEqual(body.cobertura, { obras_sociales: [], prepaga: null, principal: "" },
    "an empty cobertura is sent, not the last saved one");
});

test("adding a row and choosing it as principal sends the chosen key", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes({
    "PUT /api/admin/patients/30111222": ok({ ok: true, item: ALEJO }),
  }) }, t);
  const dispose = f.run();
  await dispose.ready;
  f.fire(f.child("#pat-coverage-add"), "click");
  const rows = f.children(".pat-coverage-row[data-cob-idx]");
  assert.equal(rows.length, 2, "a fresh row appeared");
  const nuevo = f.children("[data-cob-nombre]")[0];
  nuevo.value = "OSDE";
  f.fire(nuevo, "input");
  rows[1].querySelector("[data-cob-afiliado]").value = "77";
  f.check(rows[1].querySelector("input[name=pac-principal]"));
  f.fire(f.child("#pac-save"), "click");
  await drain();
  const body = JSON.parse(f.requests.filter((r) => r.method === "PUT")[0].options.body);
  assert.deepEqual(body.cobertura.obras_sociales, [
    { nombre: "PAMI", afiliado: "123" },
    { nombre: "OSDE", afiliado: "77" },
  ]);
  assert.equal(body.cobertura.principal, "OSDE");
});

// ---------------------------------------------------------------------------
// Load failures and recovery
// ---------------------------------------------------------------------------

test("a 404 detail says the patient is missing and offers the list", async (t) => {
  const f = fixture({ search: "?patient=99999999", routes: defaultRoutes({
    "GET /api/admin/patients/99999999": ok({ item: null }),
  }) }, t);
  await f.run().ready;
  assert.match(f.host.innerHTML, /No encontramos ese paciente\./);
  assert.ok(f.child('a[href="/admin/pacientes"]'));
});

for (const [label, body] of [["list items string", { modo: "recientes", total: 3, items: "x" }], ["list body null", null]]) {
  test("malformed 200 (" + label + ") is a load error, not an invented empty roster", async (t) => {
    const f = fixture({ routes: defaultRoutes({ "GET /api/admin/patients": ok(body) }) }, t);
    await f.run().ready;
    assert.match(f.host.innerHTML, /No se pudo cargar\. Reintentá\./);
    assert.equal(f.child("#pat-table"), null);
  });
}

test("a malformed 200 detail is a load error; the not-found line never appears", async (t) => {
  const f = fixture({ search: "?patient=30111222", routes: defaultRoutes({
    "GET /api/admin/patients/30111222": ok({ item: "Alejo" }),
  }) }, t);
  await f.run().ready;
  assert.match(f.host.innerHTML, /No se pudo cargar\. Reintentá\./);
  assert.doesNotMatch(f.host.innerHTML, /No encontramos/);
});

test("a late 500 list error cannot paint over the detail that replaced it", async (t) => {
  const boom = deferred();
  const f = fixture({ routes: (url, method) => (method === "GET" && url === "/api/admin/patients" ? boom.promise : defaultRoutes()(url, method)) }, t);
  const dispose = f.run();
  await drain(4);
  globalThis.history.pushState({}, "", "/admin/pacientes?patient=32555777");
  f.windowRef.listeners.get("popstate")();
  await drain();
  assert.equal(f.child("#pat-name").value, "Beatriz");
  boom.resolve(response(500, {}));
  await drain(8);
  assert.equal(f.child("#pat-name").value, "Beatriz");
  assert.doesNotMatch(f.host.innerHTML, /No se pudo cargar/);
});

// ---------------------------------------------------------------------------
// Dispose, remount, and retained-callback ownership
// ---------------------------------------------------------------------------

test("dispose aborts the pending list, strips every listener, and a late 200 stays inert", async (t) => {
  const gate = deferred();
  const f = fixture({ routes: (url, method) => (method === "GET" && url === "/api/admin/patients" ? gate.promise : defaultRoutes()(url, method)) }, t);
  const dispose = f.run();
  await drain(6);
  const listReq = () => f.requests.filter((r) => r.url === "/api/admin/patients");
  assert.equal(listReq().length, 1);
  assert.equal(listReq()[0].options.signal.aborted, false);
  dispose();
  assert.equal(listReq()[0].options.signal.aborted, true);
  assert.equal(f.host.listeners.size, 0, "host listeners removed");
  assert.equal(f.doc.listeners.has("click"), false);
  assert.equal(f.windowRef.listeners.has("popstate"), false);
  assert.equal(globalThis.__patColsDisposed >= 1, true, "column owner released");
  assert.equal(f.timers.size, 0, "debounce timer cleared");
  f.host.innerHTML = "replacement view";
  gate.resolve(response(200, LIST_BODY));
  await drain(8);
  assert.equal(f.host.innerHTML, "replacement view", "late success never paints over replacement content");
  assert.deepEqual(f.toasts, []);
  assert.equal(f.gets().length, 1, "nothing refetches after disposal");
});

test("remounting over the same host retires the first owner's pending work", async (t) => {
  const stale = deferred();
  const f = fixture({ routes: (url, method) => {
    if (method === "GET" && url === "/api/admin/patients") {
      return f.requests.filter((r) => r.url === "/api/admin/patients").length === 1 ? stale.promise : ok({ modo: null, total: 0, items: [], medicos: [] });
    }
    return defaultRoutes()(url, method);
  } }, t);
  f.run();
  await drain(6);
  const second = f.run();
  await second.ready;
  assert.equal(f.requests.filter((r) => r.url === "/api/admin/patients")[0].options.signal.aborted, true);
  assert.match(f.host.innerHTML, /Todavía no hay pacientes\./);
  stale.resolve(response(200, LIST_BODY));
  await drain(8);
  assert.doesNotMatch(f.host.innerHTML, /Varela, Alejo/, "the first owner's late list never repaints");
  assert.match(f.host.innerHTML, /Todavía no hay pacientes\./);
});

test("a retained column-picker callback repaints while live and is inert after dispose", async (t) => {
  const f = fixture({ routes: defaultRoutes() }, t);
  const dispose = f.run();
  await dispose.ready;
  const cfg = globalThis.__patColsCfg;
  assert.equal(typeof cfg.onChange, "function");
  const emailTh = f.children("#pat-table thead th").filter((th) => th.getAttribute("data-sort") === "servicio")[0];
  f.fire(emailTh, "click");
  assert.equal(f.child('th[data-sort="servicio"]').getAttribute("aria-sort"), "ascending");
  cfg.select(["nombre", "estado"]);
  assert.equal(f.children("#pat-table thead th").filter((th) => th.getAttribute("data-sort") === "servicio").length, 0,
    "a live column change repaints without the hidden column");
  dispose();
  f.host.innerHTML = "fresh screen";
  cfg.onChange([{ key: "nombre" }]);
  assert.equal(f.host.innerHTML, "fresh screen", "retired callback cannot repaint a disposed screen");
});

// HSI necesita nacimiento y sexo para empadronar: el alta de la mesa los pide.
test("a new patient without birth date or sex is not posted", async (t) => {
  const f = fixture({ search: "?new=1", routes: defaultRoutes({
    "POST /api/admin/patients": ok({ ok: true, item: ALEJO }),
  }) }, t);
  await f.run().ready;
  fillNewPatient(f, { sexo: "" });
  f.fire(f.child("#pac-save"), "click");
  fillNewPatient(f, { nac: "" });
  f.fire(f.child("#pac-save"), "click");
  await drain();
  assert.equal(f.posts("/api/admin/patients").length, 0);
});
