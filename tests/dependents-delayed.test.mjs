// Delayed native Account dependency inside platform/dependents.mjs.
// Separate process from dependents-runtime.test.mjs so the gated import is
// the first (and only) evaluation of the canonical module.
import test from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { accountFormGate } from "./fixtures/dependents-account-form-gate.mjs";
import { stays } from "./helpers/wait.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

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

function parseAttrs(raw) {
  const attrs = { dataset: {} };
  const re = /([:@\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?/g;
  let m;
  while ((m = re.exec(raw))) {
    const name = m[1];
    const value = m[2] != null ? m[2] : (m[3] != null ? m[3] : "");
    if (name === "id") attrs.id = value;
    else if (name === "class") attrs.className = value;
    else if (name === "type") attrs.type = value;
    else if (name === "name") attrs.name = value;
    else if (name === "href") attrs.href = value;
    else if (name === "value") attrs.value = value;
    else if (name.startsWith("data-")) attrs.dataset[name.slice(5)] = value;
  }
  return attrs;
}

function queryAll(root, sel) {
  const all = [root, ...descendants(root)];
  if (sel.startsWith("#")) return all.filter((n) => n.id === sel.slice(1));
  if (sel.startsWith(".")) {
    const cls = sel.slice(1).split(".")[0];
    return all.filter((n) => (" " + n.className + " ").includes(" " + cls + " "));
  }
  return all.filter((n) => n.tagName === sel.toUpperCase());
}

function makeNode(tag, attrs, documentRef) {
  const node = {
    tagName: String(tag).toUpperCase(),
    id: attrs.id || "",
    className: attrs.className || "",
    href: attrs.href || "",
    value: attrs.value || "",
    disabled: false,
    isConnected: true,
    parentNode: null,
    children: [],
    dataset: { ...(attrs.dataset || {}) },
    style: {},
    _listeners: {},
    _html: "",
    textContent: "",
    addEventListener(type, fn) { (node._listeners[type] ||= []).push(fn); },
    dispatchEvent(event) {
      const ev = event || {};
      if (typeof ev.preventDefault !== "function") ev.preventDefault = () => {};
      ev.target = ev.target || node;
      ev.type = ev.type || "click";
      (node._listeners[ev.type] || []).forEach((fn) => fn(ev));
    },
    click() { node.dispatchEvent({ type: "click" }); },
    focus() {},
    appendChild(child) {
      child.parentNode = node;
      child.isConnected = node.isConnected;
      node.children.push(child);
      if (child.id) documentRef._byId.set(child.id, child);
      return child;
    },
    contains(other) {
      if (other === node) return true;
      return descendants(node).includes(other);
    },
    querySelector(sel) { return queryAll(node, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(node, sel); },
  };
  Object.defineProperty(node, "innerHTML", {
    get() { return node._html; },
    set(html) {
      for (const child of node.children) child.isConnected = false;
      node._html = String(html);
      node.children = parseFragment(String(html), node, documentRef);
    },
  });
  if (node.id) documentRef._byId.set(node.id, node);
  return node;
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

function installDom() {
  const previous = { window: global.window, document: global.document, fetch: global.fetch };
  const byId = new Map();
  const documentRef = { _byId: byId };
  const head = makeNode("head", {}, documentRef);
  const body = makeNode("body", {}, documentRef);
  const document = {
    head,
    body,
    documentElement: { lang: "es" },
    getElementById(id) { return byId.get(id) || null; },
    createElement(tag) { return makeNode(tag, {}, documentRef); },
    addEventListener() {},
    removeEventListener() {},
  };
  const host = makeNode("div", { id: "host" }, documentRef);
  body.appendChild(host);
  const posts = [];
  global.document = document;
  global.window = {
    document,
    location: { href: "http://127.0.0.1/svc/pediatria/book" },
    confirm: () => true,
    SANA_STATIC: {
      "platform/i18n.mjs": pathToFileURL(join(ROOT, "static/platform/i18n.mjs")).href,
      "platform/account-form.mjs": pathToFileURL(join(ROOT, "tests/fixtures/gated-account-form.mjs")).href,
    },
  };
  global.fetch = async (url, opts = {}) => {
    if (String(url).includes("/api/account/dependents") && (opts.method || "GET").toUpperCase() === "POST") {
      posts.push(JSON.parse(opts.body || "{}"));
    }
    return { ok: true, status: 200, json: async () => ({ dependientes: [] }) };
  };
  return {
    host,
    posts,
    restore() {
      global.window = previous.window;
      global.document = previous.document;
      global.fetch = previous.fetch;
    },
  };
}

test("cancel while Account is delayed is final; a late import cannot submit or revive the form", async () => {
  const world = installDom();
  try {
    const Dep = await import("../static/platform/dependents.mjs");
    let cancelled = 0;
    Dep.renderAgregar(world.host, {
      onAdded: () => { throw new Error("a cancelled form must not add anyone"); },
      onCancel: () => { cancelled += 1; world.host.innerHTML = ""; },
    });
    const save = world.host.querySelector(".dep-save");
    assert.equal(save.disabled, true);
    assert.match(world.host.querySelector("#dep-msg").textContent, /Preparando el control de fecha/);
    world.host.querySelector("#dep-form").dispatchEvent({ type: "submit" });
    await stays(() => world.posts.length === 0, "submit while the native control is held does not POST", 20);
    assert.equal(world.posts.length, 0, "submit while the native control is held must not POST");
    world.host.querySelector(".dep-cancel").click();
    assert.equal(cancelled, 1);
    assert.equal(world.host.querySelector("#dep-form"), null);
    accountFormGate.release();
    await stays(
      () => world.host.querySelector("#dep-form") === null && world.posts.length === 0,
      "a late Account import neither paints into the cancelled host nor submits",
      20,
    );
    assert.equal(world.host.querySelector("#dep-form"), null, "late Account must not paint into a cancelled host");
    assert.equal(world.posts.length, 0);
    assert.equal(typeof global.window.SanaDependents, "undefined");
  } finally {
    accountFormGate.release();
    world.restore();
  }
});
