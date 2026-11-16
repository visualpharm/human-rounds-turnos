import assert from "node:assert/strict";

class TestNode {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.className = "";
    this.id = "";
    this.textContent = "";
    this.innerHTML = "";
    this.style = {};
    this.children = [];
  }
  appendChild(child) { this.children.push(child); return child; }
  querySelector(selector) {
    const className = selector.startsWith(".") ? selector.slice(1) : "";
    return this.children.find((child) =>
      (child.className || "").split(/\s+/).includes(className)) || null;
  }
  addEventListener() {}
  setAttribute() {}
}

globalThis.Node = TestNode;
globalThis.HTMLElement = TestNode;
globalThis.document = { createElement: (tag) => new TestNode(tag) };

const Story = await import("../../stories/ChatUI.stories.js");
globalThis.window = { requestAnimationFrame: (callback) => callback() };
for (const name of [
  "ConversacionDeTurno", "Escribiendo", "ConOpcionesParaTocar",
  "ListaYNegrita", "ConFuentes", "RevelacionProgresiva",
]) {
  const rendered = Story[name]();
  assert.ok(rendered instanceof Node, `${name} must return a DOM Node synchronously`);
  assert.equal(typeof rendered.then, "undefined", `${name} must not return a Promise`);
}
