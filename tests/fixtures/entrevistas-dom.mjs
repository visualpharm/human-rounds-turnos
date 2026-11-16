// Compact DOM for executing modules/intake/admin-entrevistas.mjs in node.
export function seed(names = ["Ana Alvarez", "Beatriz Benitez", "Carla Campos"]) {
  return {
    enabled_system: true,
    medicos: names.map((nombre, i) => ({
      id: "doctor-" + i,
      nombre,
      especialidad: i === 0 ? "Pediatría" : "Cardiología",
      efectivo: i === 0,
      override: i === 0,
      cartel_url: "/api/admin/intake/door/doctor-" + i,
      pdf_url: i === 0 ? "/api/admin/intake/door/doctor-" + i + "?format=pdf" : null,
    })),
  };
}

function parseAttrs(raw) {
  const attrs = {};
  const re = /([:@\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?/g;
  let m;
  while ((m = re.exec(raw))) {
    attrs[m[1]] = m[2] != null ? m[2] : (m[3] != null ? m[3] : "");
  }
  return attrs;
}

function makeNode(tag, attrs, documentRef) {
  const node = {
    tagName: String(tag).toUpperCase(),
    id: attrs.id || "",
    className: attrs.class || "",
    href: attrs.href || "",
    type: attrs.type || "",
    name: attrs.name || "",
    disabled: false,
    checked: Object.prototype.hasOwnProperty.call(attrs, "checked"),
    indeterminate: false,
    isConnected: true,
    parentNode: null,
    children: [],
    attrs,
    style: {},
    _listeners: {},
    _html: "",
    _text: "",
    get textContent() { return node._text || node._html.replace(/<[^>]+>/g, ""); },
    set textContent(v) { node._text = String(v); node._html = ""; node.children = []; },
    get value() { return node.attrs.value || ""; },
    set value(v) { node.attrs.value = String(v); },
    getAttribute(name) {
      if (name === "id") return node.id || null;
      return Object.prototype.hasOwnProperty.call(node.attrs, name) ? node.attrs[name] : null;
    },
    setAttribute(name, value) { node.attrs[name] = String(value); if (name === "id") node.id = String(value); },
    addEventListener(type, fn) { (node._listeners[type] ||= []).push(fn); },
    dispatchEvent(event) {
      const ev = event || {};
      if (typeof ev.preventDefault !== "function") ev.preventDefault = () => {};
      ev.target = ev.target || node;
      ev.currentTarget = node;
      (node._listeners[ev.type] || []).forEach((fn) => fn(ev));
      if (node.parentNode && node.parentNode.dispatchEvent) node.parentNode.dispatchEvent(ev);
    },
    getContext() {
      return { font: "", measureText() { return { width: 0 }; } };
    },
    appendChild(child) {
      child.parentNode = node;
      node.children.push(child);
      if (child.id) documentRef._byId.set(child.id, child);
      return child;
    },
    closest(sel) {
      let cur = node;
      while (cur) {
        if (matches(cur, sel)) return cur;
        cur = cur.parentNode;
      }
      return null;
    },
    querySelector(sel) { return queryAll(node, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(node, sel); },
  };
  Object.defineProperty(node, "innerHTML", {
    get() { return node._html; },
    set(html) {
      node._html = String(html);
      node._text = "";
      node.children = parseFragment(String(html), node, documentRef);
    },
  });
  if (node.id) documentRef._byId.set(node.id, node);
  return node;
}

function matches(node, sel) {
  if (!sel) return false;
  if (sel.startsWith("#")) return node.id === sel.slice(1);
  if (sel.startsWith(".")) return (" " + node.className + " ").includes(" " + sel.slice(1) + " ");
  const attrEq = /^\[([^=\]]+)="([^"]*)"\]$/.exec(sel);
  if (attrEq) return node.getAttribute(attrEq[1]) === attrEq[2];
  const attr = /^\[([^=\]]+)\]$/.exec(sel);
  if (attr) return node.getAttribute(attr[1]) != null;
  if (/^[a-zA-Z]+$/.test(sel)) return node.tagName === sel.toUpperCase();
  if (sel.includes(",")) return sel.split(",").some((part) => matches(node, part.trim()));
  return false;
}

function descendants(node) {
  const out = [];
  function walk(n) {
    for (const child of n.children || []) {
      out.push(child);
      walk(child);
    }
  }
  walk(node);
  return out;
}

function queryAll(root, sel) {
  return [root, ...descendants(root)].filter((n) => n !== root && matches(n, sel));
}

function parseFragment(html, parent, documentRef) {
  const kids = [];
  const re = /<(\w+)([^>]*)>/g;
  let m;
  while ((m = re.exec(html))) {
    const child = makeNode(m[1], parseAttrs(m[2] || ""), documentRef);
    child.parentNode = parent;
    kids.push(child);
  }
  return kids;
}

export function installDom({ pathname = "/admin/entrevistas", search = "", fetchImpl } = {}) {
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    fetch: globalThis.fetch,
    location: globalThis.location,
    CSS: globalThis.CSS,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    addEventListener: globalThis.addEventListener,
    removeEventListener: globalThis.removeEventListener,
    MutationObserver: globalThis.MutationObserver,
    getComputedStyle: globalThis.getComputedStyle,
    SANA_ADMIN_SCREEN: globalThis.SANA_ADMIN_SCREEN,
    SANA_COUNTRY_PHONE: globalThis.SANA_COUNTRY_PHONE,
  };
  const byId = new Map();
  const documentRef = { _byId: byId, active: null };
  const head = makeNode("head", {}, documentRef);
  const body = makeNode("body", {}, documentRef);
  const host = makeNode("section", { id: "sec-entrevistas" }, documentRef);
  body.appendChild(host);
  const document = {
    head,
    body,
    documentElement: { lang: "es" },
    readyState: "complete",
    getElementById(id) { return byId.get(id) || null; },
    createElement(tag) { return makeNode(tag, {}, documentRef); },
    querySelector(sel) { return host.querySelector(sel) || (matches(host, sel) ? host : null); },
    querySelectorAll(sel) { return host.querySelectorAll(sel); },
    addEventListener() {},
    removeEventListener() {},
  };
  const location = { href: "http://127.0.0.1" + pathname + search, pathname, search, hash: "" };
  const fetches = [];
  globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/"/g, '\\"') };
  globalThis.requestAnimationFrame = (fn) => fn();
  globalThis.document = document;
  globalThis.SANA_ADMIN_SCREEN = "entrevistas";
  globalThis.location = location;
  globalThis.window = globalThis;
  globalThis.window.SANA_ADMIN_SCREEN = "entrevistas";
  globalThis.window.location = location;
  globalThis.window.CSS = globalThis.CSS;
  globalThis.window.requestAnimationFrame = globalThis.requestAnimationFrame;
  if (typeof globalThis.addEventListener !== "function") {
    globalThis.addEventListener = () => {};
  }
  if (typeof globalThis.removeEventListener !== "function") {
    globalThis.removeEventListener = () => {};
  }
  if (typeof globalThis.MutationObserver !== "function") {
    globalThis.MutationObserver = function () {
      return { observe() {}, disconnect() {}, takeRecords() { return []; } };
    };
  }
  if (typeof globalThis.getComputedStyle !== "function") {
    globalThis.getComputedStyle = () => new Proxy({}, { get: () => "" });
  }
  globalThis.fetch = fetchImpl || (async (url, opts = {}) => {
    fetches.push({ url, opts });
    return { ok: true, status: 200, json: async () => seed() };
  });
  return {
    host,
    location,
    fetches,
    restore() {
      globalThis.window = previous.window;
      globalThis.document = previous.document;
      globalThis.fetch = previous.fetch;
      globalThis.location = previous.location;
      globalThis.CSS = previous.CSS;
      globalThis.requestAnimationFrame = previous.requestAnimationFrame;
      globalThis.addEventListener = previous.addEventListener;
      globalThis.removeEventListener = previous.removeEventListener;
      globalThis.MutationObserver = previous.MutationObserver;
      globalThis.getComputedStyle = previous.getComputedStyle;
      globalThis.SANA_ADMIN_SCREEN = previous.SANA_ADMIN_SCREEN;
      globalThis.SANA_COUNTRY_PHONE = previous.SANA_COUNTRY_PHONE;
    },
  };
}
