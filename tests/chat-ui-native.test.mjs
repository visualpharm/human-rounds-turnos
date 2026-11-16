// Behavioral contract for the native ChatUI module: call the actual named
// exports (not a VM of the retired classic file, not a source regex) for
// markdown, bubbles, tappable options after the list is gone, composer
// submit, and a closed/hidden form that must not send.
import test from "node:test";
import assert from "node:assert/strict";
import * as ChatUI from "../static/platform/chat-ui.mjs";

function el(tag) {
  const listeners = {};
  const node = {
    tagName: String(tag || "div").toUpperCase(),
    className: "",
    id: "",
    innerHTML: "",
    textContent: "",
    hidden: false,
    disabled: false,
    isConnected: true,
    value: "",
    children: [],
    style: {},
    scrollHeight: 26,
    attrs: {},
    parent: null,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type, ev) {
      const event = ev || { preventDefault() {} };
      (listeners[type] || []).forEach((fn) => fn(event));
    },
    appendChild(child) {
      child.parent = this;
      this.children.push(child);
      return child;
    },
    remove() {
      this.isConnected = false;
      if (this.parent) {
        this.parent.children = this.parent.children.filter((c) => c !== this);
      }
    },
    querySelector(sel) {
      const match = (n) => {
        if (sel.startsWith(".")) return (n.className || "").split(/\s+/).includes(sel.slice(1));
        if (sel.startsWith("#")) return n.id === sel.slice(1);
        return false;
      };
      const walk = (n) => {
        for (const c of n.children || []) {
          if (match(c)) return c;
          const hit = walk(c);
          if (hit) return hit;
        }
        return null;
      };
      return walk(this);
    },
    closest(sel) {
      if (sel === "[hidden]" && this.hidden) return this;
      let p = this.parent;
      while (p) {
        if (sel === "[hidden]" && p.hidden) return p;
        p = p.parent;
      }
      return null;
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    focus() {},
    click() { this.fire("click"); },
  };
  return node;
}

function parseComposer(form, html) {
  form.children = [];
  const add = (tag, cls, extra) => {
    const n = el(tag);
    n.className = cls;
    Object.assign(n, extra || {});
    form.appendChild(n);
    return n;
  };
  add("textarea", "stzc-input");
  add("button", "stzc-attach", { type: "button" });
  add("input", "stzc-file", { type: "file", hidden: true, files: [] });
  add("button", "stzc-mic", { type: "button", hidden: true });
  add("span", "stzc-compose-status");
  add("button", "stzc-send", { type: "submit" });
  form._raw = html;
}

function installDom() {
  const overrides = {
    window: globalThis,
    requestAnimationFrame: (fn) => fn(),
    navigator: {},
    document: { createElement: (tag) => el(tag) },
  };
  const prev = Object.fromEntries(Object.keys(overrides).map(
    (key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)],
  ));
  for (const [key, value] of Object.entries(overrides)) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  return () => {
    for (const [key, descriptor] of Object.entries(prev)) {
      if (descriptor === undefined) delete globalThis[key];
      else Object.defineProperty(globalThis, key, descriptor);
    }
  };
}

test("DOM fixture restores getter-only globals and animation-frame state", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const runtimeNavigator = {};
  Object.defineProperty(globalThis, "navigator", {
    configurable: true, enumerable: true, get: () => runtimeNavigator,
  });
  const keys = ["window", "requestAnimationFrame", "navigator", "document"];
  const before = keys.map((key) => Object.getOwnPropertyDescriptor(globalThis, key));
  try {
    const restore = installDom();
    try {
      assert.notEqual(globalThis.navigator, runtimeNavigator);
      let called = false;
      globalThis.requestAnimationFrame(() => { called = true; });
      assert.equal(called, true);
      assert.equal(globalThis.document.createElement("button").tagName, "BUTTON");
    } finally {
      restore();
    }
    assert.deepEqual(keys.map((key) => Object.getOwnPropertyDescriptor(globalThis, key)), before);
    assert.equal(globalThis.navigator, runtimeNavigator);
  } finally {
    if (original === undefined) delete globalThis.navigator;
    else Object.defineProperty(globalThis, "navigator", original);
  }
});

test("named exports exist and the module does not publish a browser global", () => {
  assert.equal(typeof ChatUI.renderMini, "function");
  assert.equal(typeof ChatUI.crearLista, "function");
  assert.equal(typeof ChatUI.crearComposer, "function");
  assert.equal(typeof globalThis.SanaChatUI, "undefined");
  assert.equal(typeof globalThis.window?.SanaChatUI, "undefined");
});

test("renderMini links names despite capitalisation and paints streaming bold", () => {
  const html = ChatUI.renderMini(
    "Te conviene **Medicina General**, o pediatria si es para un chico.",
    [
      { label: "Medicina general", href: "/d/clinica" },
      { label: "Pediatría", href: "/d/pediatria" },
    ],
  );
  assert.match(html, /<a href="\/d\/clinica">Medicina General<\/a>/);
  assert.match(html, /<a href="\/d\/pediatria">pediatria<\/a>/);
  assert.equal(ChatUI.renderMini("Hola **mundo**.", []), "Hola <b>mundo</b>.");
  assert.match(ChatUI.renderMini("Podés atenderte en **Hospital de Pinamar", []), /<b>Hospital de Pinamar<\/b>$/);
});

test("crearLista paints bubbles, options and https-only sources through the real export", () => {
  const restore = installDom();
  try {
    const host = el("div");
    const lista = ChatUI.crearLista(host, { prefix: "stzc" });
    lista.addBubble("user", "<script>x</script>");
    assert.equal(host.children[0].textContent, "<script>x</script>");
    assert.doesNotMatch(host.children[0].innerHTML, /<script>/);

    const asst = lista.addBubble("assistant", "Hay lugar en **Cardiología**.", {
      enlaces: [{ label: "Cardiología", href: "/d/cardiologia" }],
    });
    assert.match(asst.innerHTML, /<a href="\/d\/cardiologia">Cardiología<\/a>/);
    assert.match(asst.innerHTML, /<b>/);

    let picked = "";
    const row = lista.addOpciones(["Mañana", "Tarde"], (etq) => { picked = etq; });
    assert.equal(row.children.length, 2);
    row.children[0].click();
    assert.equal(picked, "Mañana");
    assert.equal(row.isConnected, false);

    lista.addFuentes([
      { titulo: "Hospital", url: "https://pinamar.humanrounds.org/center/hospital-pinamar" },
      { titulo: "Descartada", url: "http://ejemplo.test" },
    ]);
    const sources = host.children.find((c) => c.className === "stzc-sources");
    assert.ok(sources);
    assert.equal(sources.children.filter((c) => c.tagName === "A").length, 1);
  } finally {
    restore();
  }
});

test("tapping an option after the list is disconnected does not send", () => {
  const restore = installDom();
  try {
    const host = el("div");
    const lista = ChatUI.crearLista(host, { prefix: "iv" });
    let picked = 0;
    const row = lista.addOpciones(["Sí"], () => { picked += 1; });
    host.isConnected = false;
    row.children[0].isConnected = false;
    // The list host is what addOpciones consults.
    host.isConnected = false;
    row.children[0].click();
    assert.equal(picked, 0);
  } finally {
    restore();
  }
});

test("crearComposer submits text and refuses to send once the form is closed or hidden", () => {
  const restore = installDom();
  try {
    const sent = [];
    const form = el("form");
    Object.defineProperty(form, "innerHTML", {
      configurable: true,
      set() { parseComposer(form, ""); },
      get() { return form._raw || ""; },
    });

    const composer = ChatUI.crearComposer(form, {
      prefix: "stzc",
      placeholder: "Escribí tu consulta…",
      onEnviar(texto, adjunto) { sent.push({ texto, adjunto }); },
    });
    const input = form.querySelector(".stzc-input");
    input.value = "Quiero un turno";
    form.fire("submit", { preventDefault() {} });
    assert.equal(sent.length, 1);
    assert.equal(sent[0].texto, "Quiero un turno");
    assert.equal(input.value, "");

    composer.setValue("texto que no debe irse");
    form.hidden = true;
    form.fire("submit", { preventDefault() {} });
    assert.equal(sent.length, 1);
    assert.equal(composer.getValue(), "texto que no debe irse");

    form.hidden = false;
    form.isConnected = false;
    form.fire("submit", { preventDefault() {} });
    assert.equal(sent.length, 1);
  } finally {
    restore();
  }
});

// The dictation notice (ai.notice.voice) is a prior disclosure, but it no
// longer sits in the answer field for everyone: the first press of the
// microphone shows it and does not record; the next press records; sending
// the message puts it away (the product owner's hub, 2026-11-07, case-chat-polish).
test("crearComposer: the first microphone press shows the dictation notice and does not record", () => {
  const restore = installDom();
  const prevRecorder = globalThis.MediaRecorder;
  try {
    const form = el("form");
    let notice = null;
    Object.defineProperty(form, "innerHTML", {
      configurable: true,
      set() {
        parseComposer(form, "");
        notice = el("p");
        notice.className = "stzc-voice-disclosure";
        notice.hidden = true;
        form.appendChild(notice);
      },
      get() { return form._raw || ""; },
    });
    ChatUI.crearComposer(form, { prefix: "stzc", onEnviar() {} });
    assert.equal(notice.hidden, true, "nothing about dictation before the microphone is pressed");

    let opened = 0;
    globalThis.navigator.mediaDevices = { getUserMedia() { opened += 1; return new Promise(() => {}); } };
    globalThis.MediaRecorder = function MediaRecorder() {};
    const mic = form.querySelector(".stzc-mic");

    mic.click();
    assert.equal(notice.hidden, false);
    assert.equal(opened, 0, "the first press only shows the notice");

    mic.click();
    assert.equal(opened, 1, "the second press opens the microphone");

    const input = form.querySelector(".stzc-input");
    input.value = "Desde hace dos semanas";
    form.fire("submit", { preventDefault() {} });
    assert.equal(notice.hidden, true, "sending puts the notice away");
  } finally {
    if (prevRecorder === undefined) delete globalThis.MediaRecorder;
    else globalThis.MediaRecorder = prevRecorder;
    restore();
  }
});
