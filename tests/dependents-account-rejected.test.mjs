// Rejected native Account dependency inside platform/dependents.mjs.
// Own process so the gated import can fail before the canonical module loads.
import test from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { accountFormGate } from "./fixtures/dependents-account-form-gate.mjs";
import { until, stays } from "./helpers/wait.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function descendants(node) {
  const out = [];
  (function walk(n) {
    for (const child of n.children || []) { out.push(child); walk(child); }
  })(node);
  return out;
}

function queryAll(root, sel) {
  const all = [root, ...descendants(root)];
  if (sel.startsWith("#")) return all.filter((n) => n.id === sel.slice(1));
  if (sel.startsWith(".")) return all.filter((n) => (" " + n.className + " ").includes(" " + sel.slice(1).split(".")[0] + " "));
  return all.filter((n) => n.tagName === sel.toUpperCase());
}

function makeNode(tag, attrs, documentRef) {
  const node = {
    tagName: String(tag).toUpperCase(),
    id: attrs.id || "",
    className: attrs.className || "",
    href: attrs.href || "",
    disabled: false,
    isConnected: true,
    parentNode: null,
    children: [],
    dataset: {},
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
      node.children.push(child);
      if (child.id) documentRef._byId.set(child.id, child);
      return child;
    },
    contains(other) { return other === node || descendants(node).includes(other); },
    querySelector(sel) { return queryAll(node, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(node, sel); },
  };
  Object.defineProperty(node, "innerHTML", {
    get() { return node._html; },
    set(html) {
      node._html = String(html);
      node.children = [];
      const re = /<(\w+)([^>]*)>/g;
      let m;
      while ((m = re.exec(String(html)))) {
        const attrs = {};
        const id = /id="([^"]+)"/.exec(m[2] || "");
        const cls = /class="([^"]+)"/.exec(m[2] || "");
        if (id) attrs.id = id[1];
        if (cls) attrs.className = cls[1];
        const child = makeNode(m[1], attrs, documentRef);
        child.parentNode = node;
        node.children.push(child);
      }
    },
  });
  if (node.id) documentRef._byId.set(node.id, node);
  return node;
}

test("a rejected Account import leaves the form unsubmittable with a real reload link", async () => {
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
  globalThis.document = document;
  global.window = {
    document,
    location: { href: "http://127.0.0.1/account#deps" },
    SANA_STATIC: {
      "platform/i18n.mjs": pathToFileURL(join(ROOT, "static/platform/i18n.mjs")).href,
      "platform/account-form.mjs": pathToFileURL(join(ROOT, "tests/fixtures/gated-account-form.mjs")).href,
    },
  };
  globalThis.window = global.window;
  global.fetch = async (url, opts = {}) => {
    if (String(url).includes("/api/account/dependents") && (opts.method || "GET").toUpperCase() === "POST") {
      posts.push(true);
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  const Dep = await import("../static/platform/dependents.mjs");
  Dep.renderAgregar(host, { onAdded() {}, onCancel() {} });
  assert.equal(host.querySelector(".dep-save").disabled, true);
  assert.match(host.querySelector("#dep-msg").textContent, /Preparando el control de fecha/);

  accountFormGate.fail(new Error("synthetic account-form load failure"));
  await until(() => {
    const m = host.querySelector("#dep-msg");
    return m && /No pudimos cargar el control de fecha/.test(m.textContent || "");
  }, "the failed Account control paints its recovery message");

  const msg = host.querySelector("#dep-msg");
  const reload = msg.querySelector("a");
  assert.equal(host.querySelector(".dep-save").disabled, true);
  assert.equal(host.querySelector("#dep-calbtn").disabled, true);
  assert.match(msg.textContent, /No pudimos cargar el control de fecha/);
  assert.ok(reload, "recovery must be a real reload link");
  assert.equal(reload.href, "http://127.0.0.1/account#deps");
  assert.match(reload.textContent, /Recargar la página/);
  host.querySelector("#dep-form").dispatchEvent({ type: "submit" });
  await stays(() => posts.length === 0 && msg.querySelector("a"), "Enter after rejection neither POSTs nor drops the reload link", 20);
  assert.equal(posts.length, 0);
  assert.ok(msg.querySelector("a"), "Enter after rejection must keep the reload link");
  assert.equal(typeof global.window.SanaDependents, "undefined");
});
