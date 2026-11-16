// Real-import behavioral tests for static/platform/dependents.mjs.
// A source scan cannot prove selection, cancel-during-ready, or the
// native Account DOB control. This file drives the actual named exports.
import test from "node:test";
import assert from "node:assert/strict";
import {
  renderAgregar, renderElegir, renderCuenta, renderSelector, plan,
} from "../static/platform/dependents.mjs";
import * as Account from "../static/platform/account-form.mjs";
import { until } from "./helpers/wait.mjs";

const HOY = new Date("2026-07-08T12:00:00");
const hijoA = {
  dni: "50000001", nombre: "Lila", apellido: "López", nacimiento: "2018-06-15", vinculo: "hijo",
};
const hijoB = {
  dni: "50000002", nombre: "Mauro", apellido: "López", nacimiento: "2014-03-02", vinculo: "hijo",
};
const titular = {
  dni: "27888999", nombre: "Ana", apellido: "López", nacimiento: "1979-06-01",
};

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
    else if (name.startsWith("data-")) attrs.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
  }
  return attrs;
}

function makeNode(tag, attrs, documentRef) {
  const node = {
    tagName: String(tag).toUpperCase(),
    id: attrs.id || "",
    className: attrs.className || "",
    name: attrs.name || "",
    type: attrs.type || "",
    href: attrs.href || "",
    value: attrs.value || "",
    disabled: false,
    checked: false,
    hidden: false,
    isConnected: true,
    parentNode: null,
    children: [],
    dataset: { ...(attrs.dataset || {}) },
    style: {},
    classList: {
      contains(cls) { return (" " + node.className + " ").includes(" " + cls + " "); },
      add(cls) { if (!node.classList.contains(cls)) node.className = (node.className + " " + cls).trim(); },
    },
    _listeners: {},
    _html: "",
    textContent: "",
    addEventListener(type, fn) { (node._listeners[type] ||= []).push(fn); },
    dispatchEvent(event) {
      const ev = event || {};
      if (typeof ev.preventDefault !== "function") ev.preventDefault = () => { ev.defaultPrevented = true; };
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
    getAttribute(name) {
      if (name === "id") return node.id;
      if (name === "href") return node.href;
      if (name === "class") return node.className;
      if (name.startsWith("data-")) return node.dataset[name.slice(5)] || "";
      return "";
    },
  };
  Object.defineProperty(node, "innerHTML", {
    get() { return node._html; },
    set(html) {
      for (const child of node.children) child.isConnected = false;
      node._html = String(html);
      node.children = parseFragment(String(html), node, documentRef);
      node.textContent = String(html).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
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
    if (child.id) documentRef._byId.set(child.id, child);
  }
  return kids;
}

function queryAll(root, sel) {
  const all = [root, ...descendants(root)];
  if (sel.includes(":checked")) {
    return queryAll(root, sel.replace(":checked", "")).filter((n) => n.checked);
  }
  if (sel.startsWith("#")) return all.filter((n) => n.id === sel.slice(1));
  const nameMatch = sel.match(/^(?:(\w+))?\[name="([^"]+)"\]$/);
  if (nameMatch) {
    const tag = nameMatch[1];
    const name = nameMatch[2];
    return all.filter((n) => (!tag || n.tagName === tag.toUpperCase()) && n.name === name);
  }
  if (sel.startsWith(".")) {
    const classes = sel.slice(1).split(".").filter(Boolean);
    return all.filter((n) => classes.every((cls) => (" " + n.className + " ").includes(" " + cls + " ")));
  }
  return all.filter((n) => n.tagName === sel.toUpperCase());
}

function installDom() {
  const previous = { window: global.window, document: global.document, fetch: global.fetch };
  const byId = new Map();
  const documentRef = { _byId: byId };
  const head = makeNode("head", {}, documentRef);
  const body = makeNode("body", {}, documentRef);
  const document = {
    _byId: byId,
    head,
    body,
    documentElement: { lang: "es" },
    readyState: "complete",
    getElementById(id) { return byId.get(id) || null; },
    createElement(tag) { return makeNode(tag, {}, documentRef); },
    querySelector(sel) { return queryAll({ children: [head, body] }, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll({ children: [head, body] }, sel); },
    addEventListener() {},
    removeEventListener() {},
  };
  documentRef._byId = byId;
  const host = makeNode("div", { id: "host" }, documentRef);
  host.isConnected = true;
  body.appendChild(host);
  global.document = document;
  globalThis.document = document;
  global.window = {
    document,
    location: { href: "http://127.0.0.1/account#deps" },
    confirm: () => true,
  };
  globalThis.window = global.window;
  const posts = [];
  const people = [];
  global.fetch = async (url, opts = {}) => {
    const path = String(url);
    const method = (opts.method || "GET").toUpperCase();
    if (path.includes("/api/account/dependents/delete") && method === "POST") {
      const bodyJson = JSON.parse(opts.body || "{}");
      const idx = people.findIndex((p) => p.dni === bodyJson.dni);
      if (idx >= 0) people.splice(idx, 1);
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    }
    if (path.includes("/api/account/dependents") && method === "POST") {
      posts.push(JSON.parse(opts.body || "{}"));
      const created = { ...posts[posts.length - 1], dni: posts[posts.length - 1].dni || "50111000" };
      people.push(created);
      return { ok: true, status: 200, json: async () => ({ dependiente: created }) };
    }
    if (path.includes("/api/account/dependents")) {
      return { ok: true, status: 200, json: async () => ({ dependientes: people.slice() }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  return {
    host,
    posts,
    people,
    restore() {
      global.window = previous.window;
      global.document = previous.document;
      global.fetch = previous.fetch;
    },
  };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function waitForDobReady(host) {
  await until(() => {
    const save = host.querySelector(".dep-save");
    const msg = host.querySelector("#dep-msg");
    if (save && save.disabled === false) return true;
    if (msg && /No pudimos cargar el control de fecha/.test(msg.textContent || "")) {
      throw new Error("native Account control failed: " + msg.textContent);
    }
    return false;
  }, "the native Account control unlocks submit");
}

test("renderElegir paints age-eligible people and selection returns that person", async () => {
  const world = installDom();
  try {
    let selected = null;
    renderElegir(world.host, {
      aptos: [hijoA, hijoB],
      lede: "Elegí quién va a recibir la atención.",
      onSelect: (p) => { selected = p; },
    });
    const html = world.host.innerHTML;
    assert.match(html, /Lila López/);
    assert.match(html, /Mauro López/);
    const choices = world.host.querySelectorAll(".dep-choice");
    assert.equal(choices.length, 2);
    choices[1].click();
    assert.equal(selected, hijoB);
  } finally {
    world.restore();
  }
});

test("renderAgregar becomes submittable after the native Account control, and cancel is final", async () => {
  const world = installDom();
  try {
    assert.equal(typeof Account.dobField, "function");
    assert.equal(typeof Account.parseFlexibleDate, "function");
    let added = null;
    let cancelled = 0;
    renderAgregar(world.host, {
      lede: "Agregalo una vez",
      onAdded: (p) => { added = p; },
      onCancel: () => { cancelled += 1; world.host.innerHTML = ""; },
    });
    assert.match(world.host.innerHTML, /id="dep-form"/);
    const save = world.host.querySelector(".dep-save");
    const cal = world.host.querySelector("#dep-calbtn");
    assert.equal(save.disabled, true, "submit stays closed until the native DOB control is ready");
    assert.equal(cal.disabled, true);
    assert.match(world.host.querySelector("#dep-msg").textContent, /Preparando el control de fecha/);

    await waitForDobReady(world.host);
    assert.equal(save.disabled, false, "the real Account dobField must unlock submit");
    assert.equal(cal.disabled, false);
    assert.equal(world.host.querySelector("#dep-msg").textContent, "");

    world.host.querySelector(".dep-cancel").click();
    assert.equal(cancelled, 1);
    assert.equal(world.host.querySelector("#dep-form"), null);
    assert.equal(added, null);
    assert.equal(world.posts.length, 0);
  } finally {
    world.restore();
  }
});

test("cancel after save begins never delivers a late dependent into the abandoned form", async () => {
  const world = installDom();
  try {
    let added = null;
    let cancelled = 0;
    let finishPost;
    const postStarted = new Promise((resolve) => { finishPost = resolve; });
    const normalFetch = global.fetch;
    global.fetch = (url, opts = {}) => {
      if (String(url).includes("/api/account/dependents") && (opts.method || "GET").toUpperCase() === "POST") {
        return postStarted;
      }
      return normalFetch(url, opts);
    };
    renderAgregar(world.host, {
      onAdded: (person) => { added = person; },
      onCancel: () => { cancelled += 1; world.host.innerHTML = ""; },
    });
    await waitForDobReady(world.host);
    world.host.querySelector("#dep-nac").value = "01/01/2015";
    world.host.querySelector("#dep-form").dispatchEvent({ type: "submit" });
    await flush();
    world.host.querySelector(".dep-cancel").click();
    assert.equal(cancelled, 1);
    assert.equal(world.host.querySelector("#dep-form"), null);

    finishPost({ ok: true, status: 200, json: async () => ({
      dependiente: { dni: "50000003", nombre: "Lola", apellido: "López", nacimiento: "2015-01-01" },
    }) });
    await flush();
    assert.equal(added, null, "a late POST response must not re-enter the cancelled form's callback");
    assert.equal(world.host.querySelector("#dep-form"), null);
  } finally {
    world.restore();
  }
});

test("renderCuenta lists fetched people and the add path uses the native form", async () => {
  const world = installDom();
  world.people.push(hijoA);
  try {
    renderCuenta(world.host);
    await flush();
    assert.match(world.host.innerHTML, /Lila López/);
    assert.match(world.host.innerHTML, /Personas a cargo/);
    world.host.querySelector(".dep-add").click();
    assert.ok(world.host.querySelector("#dep-form"), "Add must paint the real form, not a second markup path");
    await waitForDobReady(world.host);
    assert.equal(world.host.querySelector(".dep-save").disabled, false);
  } finally {
    world.restore();
  }
});

test("renderSelector shows the holder by name and selecting a dependent returns them", async () => {
  const world = installDom();
  world.people.push(hijoA);
  try {
    let selected = null;
    renderSelector(world.host, { usuario: titular, onSelect: (p) => { selected = p; } });
    await flush();
    assert.match(world.host.innerHTML, /Ana López/);
    assert.match(world.host.innerHTML, /Lila López/);
    assert.doesNotMatch(world.host.innerHTML, />Yo</);
    const choices = world.host.querySelectorAll(".dep-choice");
    assert.equal(choices.length, 2);
    choices[1].click();
    assert.equal(selected.dni, hijoA.dni);
  } finally {
    world.restore();
  }
});

test("plan still auto-selects a single eligible child on the actual module", () => {
  const svc = { edad: { min: null, max: 15 } };
  const result = plan([hijoA], svc, titular, HOY);
  assert.equal(result.paciente, hijoA);
  assert.equal(result.paso, null);
});
