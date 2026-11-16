import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { linkEmergencyNumbers } from "../static/platform/nav.mjs";

const source = fs.readFileSync(new URL("../static/platform/nav.mjs", import.meta.url), "utf8");

test("the shared navigation installs a standing 107 tap-to-call hook", () => {
  const document = {
    addEventListener() {},
    createTreeWalker(root) {
      let used = false;
      return { nextNode() { if (used) return null; used = true; return root.text; } };
    },
    createDocumentFragment() { return { children: [], appendChild(n) { this.children.push(n); } }; },
    createElement(tagName) { return { tagName: tagName.toUpperCase(), className: "", href: "", textContent: "" }; },
    createTextNode(text) { return { nodeType: 3, nodeValue: text }; },
  };
  const parent = {
    tagName: "P",
    replacement: null,
    replaceChild(next) { this.replacement = next; },
  };
  const text = { nodeType: 3, nodeValue: "Urgencias: 107.", parentElement: parent, parentNode: parent };
  const root = { text };
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  try {
    globalThis.document = document;
    linkEmergencyNumbers(root);
  } finally {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else delete globalThis.document;
  }

  const link = parent.replacement.children.find((n) => n.tagName === "A");
  assert.equal(link.href, "tel:107");
  assert.equal(link.textContent, "107");
  assert.match(source, /new MutationObserver/);
});

// One installation, one emergency number (demo review 2026-11-15): on the
// demo the footer says 112, so the hook links 112 and leaves a stray 107 alone.
test("the hook links the footer's own emergency number, not a hard-coded 107", () => {
  const made = [];
  const document = {
    querySelector(sel) { return sel.includes(".furg") ? { textContent: "112" } : null; },
    createTreeWalker(root) {
      const queue = [...root.texts];
      return { nextNode() { return queue.shift() || null; } };
    },
    createDocumentFragment() { return { children: [], appendChild(n) { this.children.push(n); } }; },
    createElement(tagName) { const n = { tagName: tagName.toUpperCase(), className: "", href: "", textContent: "" }; made.push(n); return n; },
    createTextNode(text) { return { nodeType: 3, nodeValue: text }; },
  };
  const mk = (value) => {
    const parent = { tagName: "P", replacement: null, replaceChild(next) { this.replacement = next; } };
    return { nodeType: 3, nodeValue: value, parentElement: parent, parentNode: parent };
  };
  const a = mk("Llamá al 112."), b = mk("Línea 107.");
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  try {
    globalThis.document = document;
    linkEmergencyNumbers({ texts: [a, b] });
  } finally {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else delete globalThis.document;
  }
  assert.deepEqual(made.map((n) => n.href), ["tel:112"]);
  assert.equal(b.parentNode.replacement, null);
});
