// tests/medcal-lifecycle.test.mjs — the MedCal island's OWN lifecycle, driven
// in Node against the REAL modules: static/islands/MedCal.mjs, the SAME
// vendored preact.module.js/htm-preact.module.js bytes the admin import map
// serves in production, and the canonical platform/calendar-grid.mjs. In the
// browser those load through <script type="importmap"> bare specifiers
// ("preact", "htm/preact", "sana/calendar-grid"); Node has no import maps,
// so this file registers a resolve-only module hook mapping each specifier
// to the same file on disk — no copy, no stub, the actual shipped code.
//
// The ONE stand-in is a minimal DOM (document.createElement/TextNode, the
// node child-list operations the vendored Preact build actually calls —
// same stance as tests/islands.test.mjs's hand-built elements). What that
// proves natively: mount() paints through the real renderer, update()
// re-renders in place, dispose() drives the real renderer's unmount path
// (render(null, el) — this vendor build exports no `unmount`) and empties
// the element, dispose is idempotent, a post-dispose update cannot repaint,
// and the same element remounts. What it does NOT prove: real-browser DOM,
// layout, or click behavior — that stays with the Playwright pass
// (tests/e2e/calendar-island.spec.mjs), which is why no click is simulated
// here and none of the assertions below is claimed as browser proof.
import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";

// --- loader: bare specifiers -> the real vendored/canonical files --------
const ROOT = new URL("../", import.meta.url);
const BARE = {
  preact: "static/vendor/preact.module.js",
  "htm/preact": "static/vendor/htm-preact.module.js",
  htm: "static/vendor/htm.module.js",
  "sana/calendar-grid": "static/platform/calendar-grid.mjs",
};
const mapLiteral = JSON.stringify(
  Object.fromEntries(Object.entries(BARE).map(([k, p]) => [k, new URL(p, ROOT).href])),
);
register(
  "data:text/javascript;charset=utf-8," +
    encodeURIComponent(
      `const MAP = new Map(Object.entries(${mapLiteral}));
       export function resolve(specifier, context, next) {
         if (MAP.has(specifier)) return { url: MAP.get(specifier), shortCircuit: true };
         return next(specifier, context);
       }`,
    ),
);

// --- minimal DOM: exactly the surface the vendored Preact build touches --
function makeNode(nodeType, name) {
  const node = {
    nodeType,
    nodeName: name,
    tagName: name,
    namespaceURI: "http://www.w3.org/1999/xhtml",
    childNodes: [],
    parentNode: null,
    attributes: {},
    listeners: {},
    _innerHTML: "",
    data: "",
  };
  Object.defineProperties(node, {
    firstChild: { get() { return node.childNodes[0] || null; } },
    innerHTML: {
      get() { return node._innerHTML; },
      // dangerouslySetInnerHTML lands here; clearing childNodes mirrors a
      // real element replacing its content.
      set(value) { node._innerHTML = String(value); node.childNodes.length = 0; },
    },
  });
  node.appendChild = (child) => node.insertBefore(child, null);
  node.insertBefore = (child, ref) => {
    if (child.parentNode) child.parentNode.removeChild(child);
    const at = ref ? node.childNodes.indexOf(ref) : -1;
    if (at === -1) node.childNodes.push(child);
    else node.childNodes.splice(at, 0, child);
    child.parentNode = node;
    return child;
  };
  node.removeChild = (child) => {
    const at = node.childNodes.indexOf(child);
    if (at !== -1) node.childNodes.splice(at, 1);
    child.parentNode = null;
    return child;
  };
  node.setAttribute = (key, value) => { node.attributes[key] = String(value); };
  node.removeAttribute = (key) => { delete node.attributes[key]; };
  node.getAttribute = (key) => (Object.hasOwn(node.attributes, key) ? node.attributes[key] : null);
  node.addEventListener = (type, fn) => { (node.listeners[type] = node.listeners[type] || []).push(fn); };
  node.removeEventListener = () => {};
  return node;
}

const head = makeNode(1, "HEAD");
globalThis.document = {
  createElement: (tag) => makeNode(1, String(tag).toUpperCase()),
  createElementNS: (_ns, tag) => makeNode(1, String(tag).toUpperCase()),
  createTextNode: (text) => {
    const node = makeNode(3, "#text");
    node.data = String(text);
    return node;
  },
  head,
  getElementById: (id) => head.childNodes.find((n) => n.id === id) || null,
};

const { mount } = await import("../static/islands/MedCal.mjs");

// The real admin shape: dias entries as admin-content.js's
// medCalGridDias() builds them (gridHtml reads dia/className/title).
const DIAS = [
  { dia: "2026-09-10", className: "medcal-libre", title: "3 libres" },
  { dia: "2026-09-15", className: "medcal-justo", title: "1 libre" },
  { dia: "2026-09-21", className: "medcal-lleno", title: "completo" },
];

function gridMarkup(el) {
  return el.childNodes[0] ? el.childNodes[0].innerHTML : "";
}

test("old controller cannot clear or update a replacement tree on the same element", () => {
  const el = makeNode(1, "DIV");
  const old = mount(el, { dias: DIAS, selected: "2026-09-10" });
  const fresh = mount(el, { dias: DIAS, selected: "2026-09-21" });
  old.update({ selected: "2026-09-15" });
  assert.match(gridMarkup(el), /is-selected[^>]*data-med-date="2026-09-21"/);
  old.dispose();
  assert.equal(el.childNodes.length, 1);
  assert.match(gridMarkup(el), /is-selected[^>]*data-med-date="2026-09-21"/);
  fresh.dispose();
  assert.equal(el.childNodes.length, 0);
});

test("mount() pinta la grilla con el renderer real y devuelve controller con update y dispose", () => {
  const el = makeNode(1, "DIV");
  const controller = mount(el, { dias: DIAS, selected: "" });
  assert.ok(controller, "mount devuelve el controller");
  assert.equal(typeof controller.update, "function");
  assert.equal(typeof controller.dispose, "function");
  assert.equal(el.childNodes.length, 1, "el árbol Preact vive dentro del elemento");
  assert.match(gridMarkup(el), /data-med-date="2026-09-15"/,
    "la grilla se generó con el calendar-grid real");
});

test("update() re-renderiza en el lugar (selección) sin perder el árbol", () => {
  const el = makeNode(1, "DIV");
  const controller = mount(el, { dias: DIAS, selected: "" });
  const firstPaint = el.childNodes[0];
  controller.update({ selected: "2026-09-15" });
  assert.equal(el.childNodes[0], firstPaint, "Preact reusa el mismo nodo raíz");
  assert.match(gridMarkup(el), /is-selected/);
  // gridHtml() emite class= antes de data-med-date= en el mismo <button>.
  assert.match(gridMarkup(el), /is-selected[^>]*data-med-date="2026-09-15"/);
});

test("dispose() libera de verdad el árbol Preact y vacía el elemento", () => {
  const el = makeNode(1, "DIV");
  const controller = mount(el, { dias: DIAS, selected: "" });
  assert.equal(el.childNodes.length, 1);
  controller.dispose();
  assert.equal(el.childNodes.length, 0,
    "render(null, el) — el unmount real del vendor build — removió el nodo");
});

test("dispose() es idempotente y un update posterior no puede resucitar el árbol", () => {
  const el = makeNode(1, "DIV");
  const controller = mount(el, { dias: DIAS, selected: "" });
  controller.dispose();
  assert.doesNotThrow(() => controller.dispose());
  controller.update({ selected: "2026-09-15" });
  assert.equal(el.childNodes.length, 0, "el update post-dispose no vuelve a pintar");
});

test("el mismo elemento se puede montar de nuevo tras dispose (dueño nuevo)", () => {
  const el = makeNode(1, "DIV");
  const first = mount(el, { dias: DIAS, selected: "" });
  first.dispose();
  const second = mount(el, { dias: DIAS, selected: "2026-09-21" });
  assert.ok(second && second !== first);
  assert.equal(el.childNodes.length, 1, "un mount nuevo pinta un árbol fresco");
  assert.match(gridMarkup(el), /is-selected[^>]*data-med-date="2026-09-21"/);
  second.dispose();
  assert.equal(el.childNodes.length, 0);
});

test("el estilo de la isla se inyecta una sola vez en head a través de montajes", () => {
  const elA = makeNode(1, "DIV");
  const elB = makeNode(1, "DIV");
  const a = mount(elA, { dias: DIAS, selected: "" });
  const b = mount(elB, { dias: DIAS, selected: "" });
  const tags = head.childNodes.filter((n) => n.id === "sana-medcal-css");
  assert.equal(tags.length, 1, "ensureStyle no duplica el <style> compartido");
  assert.match(tags[0].textContent || "", /medcal-grids/);
  a.dispose();
  b.dispose();
});
