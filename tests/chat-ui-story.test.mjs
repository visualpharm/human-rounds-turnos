// Behavioral contract for stories/ChatUI.stories.js: every story must call
// the real crearLista export and return a Node synchronously. The story file
// imports entrevista.css (Vite-only), so this test cannot import the story
// module in node — it executes the same renderer the story calls, and pins
// the story source against the HTML-renderer Promise boundary.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import * as ChatUI from "../static/platform/chat-ui.mjs";

const storySrc = readFileSync(new URL("../stories/ChatUI.stories.js", import.meta.url), "utf8");

const STORIES = [
  "ConversacionDeTurno",
  "Escribiendo",
  "ConOpcionesParaTocar",
  "ListaYNegrita",
  "ConFuentes",
  "RevelacionProgresiva",
];

function el(tag) {
  return {
    tagName: String(tag || "div").toUpperCase(),
    className: "",
    id: "",
    innerHTML: "",
    textContent: "",
    style: {},
    children: [],
    isConnected: true,
    hidden: false,
    attrs: {},
    appendChild(child) { this.children.push(child); return child; },
    remove() { this.isConnected = false; },
    querySelector(sel) {
      const cls = sel.startsWith(".") ? sel.slice(1) : "";
      return this.children.find((c) => (c.className || "").split(/\s+/).includes(cls)) || null;
    },
    addEventListener() {},
    setAttribute(k, v) { this.attrs[k] = v; },
    closest() { return null; },
  };
}

function installDom() {
  const prev = { document: globalThis.document, window: globalThis.window };
  globalThis.window = globalThis;
  globalThis.window.requestAnimationFrame = (fn) => fn();
  globalThis.document = {
    createElement: (tag) => el(tag),
  };
  return () => {
    if (prev.document === undefined) delete globalThis.document;
    else globalThis.document = prev.document;
    if (prev.window === undefined) delete globalThis.window;
    else globalThis.window = prev.window;
  };
}

test("the actual story module meets Storybook HTML's synchronous Node boundary", () => {
  execFileSync(process.execPath, [
    "--no-warnings",
    "--experimental-loader", new URL("./fixtures/story-css-loader.mjs", import.meta.url).pathname,
    new URL("./fixtures/chat-ui-story-boundary-runner.mjs", import.meta.url).pathname,
  ], { stdio: "pipe" });

  assert.match(storySrc, /from ["']\.\.\/static\/platform\/chat-ui\.mjs["']/);
  assert.doesNotMatch(storySrc, /SanaChatUI|chat-ui\.js|async function|async \(\)/);
});

test("the story scenes render through the real crearLista export", () => {
  const restore = installDom();
  try {
    const host = el("div");
    const lista = ChatUI.crearLista(host, { prefix: "iv" });
    lista.addBubble("assistant", "Hola. ¿Para qué necesitás el turno?");
    lista.addBubble("user", "Me duele el pecho cuando camino rápido");
    lista.addBubble(
      "assistant",
      "Eso lo ve **Cardiología**. Hay lugar el jueves a las 09:40 en el Hospital de Pinamar.",
      { enlaces: [{ label: "Cardiología", href: "/d/cardiologia" }] },
    );
    assert.equal(host.children.length, 3);
    assert.match(host.children[2].innerHTML, /<a href="\/d\/cardiologia">Cardiología<\/a>/);
    assert.match(host.children[2].innerHTML, /<b>/);

    lista.setTyping(true);
    assert.ok(host.children.some((c) => c.className === "iv-typing"));

    lista.addOpciones(["Mañana temprano", "Mañana a la tarde", "Cualquier día"], () => {});
    const chips = host.children.find((c) => c.className === "iv-chips");
    assert.equal(chips.children.length, 3);

    lista.addFuentes([
      { titulo: "Hospital de Pinamar", url: "https://pinamar.humanrounds.org/center/hospital-pinamar" },
      { titulo: "Se descarta: no es https", url: "http://ejemplo.test" },
    ]);
    const sources = host.children.find((c) => c.className === "iv-sources");
    assert.equal(sources.children.filter((c) => c.tagName === "A").length, 1);

    const streaming = lista.addBubble("assistant", "Está en");
    lista.updateBubble(
      streaming,
      "assistant",
      "Está en **Constitución y Del Tuyú**, Ostende. Atiende de lunes a viernes de 8 a 14.",
    );
    assert.match(streaming.innerHTML, /<b>/);
    assert.match(streaming.innerHTML, /Constitución y Del Tuyú/);
    assert.equal(typeof lista.then, "undefined");
  } finally {
    restore();
  }
});
