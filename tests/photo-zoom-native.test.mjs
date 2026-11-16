import test from "node:test";
import assert from "node:assert/strict";
import { close, groupedItems, mount, normalizeItems } from "../static/platform/photo-zoom.mjs";

class Classes {
  constructor() { this.values = new Set(); }
  add(name) { this.values.add(name); }
  remove(name) { this.values.delete(name); }
  contains(name) { return this.values.has(name); }
}

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attrs = new Map();
    this.listeners = new Map();
    this.classList = new Classes();
    this.style = {};
    this.parentNode = null;
    this.focused = false;
  }
  appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
  append(...nodes) { nodes.forEach((node) => this.appendChild(node)); }
  removeChild(node) { this.children.splice(this.children.indexOf(node), 1); node.parentNode = null; }
  setAttribute(key, value) { this.attrs.set(key, String(value)); }
  getAttribute(key) { return this.attrs.get(key) || ""; }
  removeAttribute(key) { this.attrs.delete(key); }
  addEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) || []).concat(listener)); }
  focus() { this.focused = true; }
  closest(selector) { return selector === "[data-photozoom]" && this.attrs.has("data-photozoom") ? this : null; }
}

class Document {
  constructor() {
    this.head = new Node("head");
    this.body = new Node("body");
    this.documentElement = new Node("html");
    this.listeners = new Map();
    this.triggers = [];
    this.activeElement = new Node("button");
  }
  createElement(tag) { return new Node(tag); }
  getElementById(id) {
    const find = (node) => node.attrs.get("id") === id ? node : node.children.map(find).find(Boolean);
    return find(this.head) || find(this.body);
  }
  querySelectorAll(selector) { return selector === "[data-photozoom]" ? this.triggers : []; }
  addEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) || []).concat(listener)); }
  removeEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener)); }
}

function trigger(src, group = "") {
  const node = new Node("a");
  node.setAttribute("data-photozoom", "");
  node.setAttribute("href", src);
  if (group) node.setAttribute("data-photozoom-group", group);
  return node;
}

test("gallery arrows are the Icons8 arrow of the project pack as a mask, never a typed glyph", async () => {
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(new URL("../static/platform/photo-zoom.mjs", import.meta.url), "utf8");
  assert.match(source, /\.pz-prev,\.pz-next\{[^}]*width:52px;height:52px;font-size:0\}/);
  assert.match(source, /\.pz-prev::before,\.pz-next::before\{[^}]*mask:url\(\/static\/icons\/dir-card-arrow-sf-regular\.svg\)/);
  assert.match(source, /\.pz-prev::before\{transform:rotate\(180deg\)\}/);
  assert.match(source, /button\(doc, "pz-prev", "", "Foto anterior"\)/);
  assert.match(source, /button\(doc, "pz-next", "", "Foto siguiente"\)/);
  assert.doesNotMatch(source, /[‹›]/);
});

test("normalization and grouped galleries keep only usable same-group items", () => {
  assert.deepEqual(normalizeItems(["/one.jpg", { src: "/two.jpg", alt: "Two" }, {}]), [
    { src: "/one.jpg", alt: "" }, { src: "/two.jpg", alt: "Two" },
  ]);
  const doc = new Document();
  const bad = trigger("", "gallery");
  const selected = trigger("/two.jpg", "gallery");
  const other = trigger("/outside.jpg", "other");
  doc.triggers = [bad, selected, other];
  assert.deepEqual(groupedItems(doc, selected), { items: [{ src: "/two.jpg", alt: "" }], index: 0 });
});

test("mounted viewer is idempotent and preserves gallery navigation, focus, and scroll", () => {
  const doc = new Document();
  const one = trigger("/one.jpg", "gallery");
  const two = trigger("/two.jpg", "gallery");
  doc.triggers = [one, two];
  doc.documentElement.style.overflow = "scroll";
  const viewer = mount(doc);
  assert.equal(mount(doc), viewer);
  assert.equal(doc.listeners.get("click").length, 1);
  let prevented = false;
  viewer.handleClick({ target: two, button: 0, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(viewer.state.index, 1);
  assert.equal(doc.documentElement.style.overflow, "hidden");
  viewer.onKey({ key: "ArrowRight" });
  assert.equal(viewer.state.index, 0, "next wraps within its own gallery");
  viewer.touchStart({ touches: [{ clientX: 100 }] });
  viewer.touchEnd({ changedTouches: [{ clientX: 20 }] });
  assert.equal(viewer.state.index, 1, "swipe advances");
  viewer.onKey({ key: "Escape" });
  assert.equal(viewer.state.open, false);
  assert.equal(doc.documentElement.style.overflow, "scroll");
  assert.equal(two.focused, true, "focus returns to the original trigger");
  viewer.destroy();
  assert.equal(doc.listeners.get("click").length, 0);
  assert.notEqual(mount(doc), viewer, "destroy permits a clean remount");
});

test("destroying an open viewer restores the page and makes its controller inert", () => {
  const doc = new Document();
  const link = trigger("/one.jpg");
  doc.triggers = [link];
  doc.documentElement.style.overflow = "auto";
  const viewer = mount(doc);
  viewer.open("/one.jpg", { trigger: link });
  viewer.destroy();
  assert.equal(doc.documentElement.style.overflow, "auto");
  assert.equal(link.focused, true);
  assert.equal(viewer.open("/two.jpg"), false);
});

test("modified or already-handled anchor clicks retain their native fallback", () => {
  const doc = new Document();
  const link = trigger("/original.jpg");
  doc.triggers = [link];
  const viewer = mount(doc);
  let prevented = false;
  viewer.handleClick({ target: link, button: 0, ctrlKey: true, preventDefault() { prevented = true; } });
  viewer.handleClick({ target: link, button: 0, defaultPrevented: true, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(viewer.state.open, false);
  close();
});
