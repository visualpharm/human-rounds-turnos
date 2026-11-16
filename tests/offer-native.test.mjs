// The real offer page supplies this bootstrap and the real controller owns
// mutations. Only module timing and offer endpoint outcomes are synthetic.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import vm from "node:vm";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MODULE = pathToFileURL(join(ROOT, "app/modules/turnos/offer.mjs")).href;

function control(id) {
  const listeners = [];
  return {
    id, disabled: false, style: {},
    addEventListener(_name, listener) { listeners.push(listener); },
    click() {
      let prevented = false;
      listeners.forEach((listener) => listener({ preventDefault() { prevented = true; } }));
      return prevented;
    },
  };
}

function renderedBootstrap() {
  const code = [
    "from app.modules.turnos import appointment_offer as o",
    "print(o._offer_available_page('tok-abc', 'Consulta', 'CAPS', 'Mañana').decode())",
  ].join("; ");
  const page = execFileSync("python3", ["-c", code], { cwd: ROOT, encoding: "utf8" });
  const match = page.match(/<script data-token="tok-abc">([\s\S]*?)<\/script>/);
  assert.ok(match, "the real offer page emits one token-bearing bootstrap");
  return match[1].replace(/import\("[^"]+"\)/, "globalThis.__loadController()");
}

async function load() {
  return import(`${MODULE}?offer-test=${Date.now()}-${Math.random()}`);
}

function world({ response = { ok: true, status: 200, body: { ok: true } }, i18n = null } = {}) {
  const accept = control("oferta-aceptar");
  const reject = control("oferta-rechazar");
  const msg = { textContent: "", style: {}, querySelector: () => null, appendChild() {} };
  const posts = [];
  const oldWindow = globalThis.window;
  const oldFetch = globalThis.fetch;
  const oldLocation = globalThis.location;
  globalThis.window = i18n ? { SANA_STATIC: { "platform/i18n.mjs": i18n } } : {};
  globalThis.fetch = (url, options) => {
    posts.push({ url, options });
    return Promise.resolve({ ok: response.ok, status: response.status, json: () => Promise.resolve(response.body) });
  };
  globalThis.location = { href: "", reload() { this.reloads = (this.reloads || 0) + 1; } };
  return {
    accept, reject, msg, posts,
    document: { getElementById(id) { return id === "oferta-aceptar" ? accept : id === "oferta-rechazar" ? reject : msg; } },
    restore() { globalThis.window = oldWindow; globalThis.fetch = oldFetch; globalThis.location = oldLocation; },
  };
}

function tick() { return new Promise((resolve) => setTimeout(resolve, 0)); }
async function settle() { await tick(); await tick(); }

test("classic offer source is retired; the rendered owner imports the canonical Turnos asset", () => {
  const page = execFileSync("python3", ["-c", [
    "from app.modules.turnos import appointment_offer as o",
    "print(o._offer_available_page('tok-abc', 'Consulta', 'CAPS', 'Mañana').decode())",
  ].join("; ")], { cwd: ROOT, encoding: "utf8" });
  const manifest = readFileSync(join(ROOT, "app/modules/turnos/module.json"), "utf8");
  assert.equal(existsSync(join(ROOT, "offer.js")), false);
  assert.match(manifest, /modules\/turnos\/offer\.mjs/);
  assert.match(page, /data-token="tok-abc"/);
  assert.match(page, /modules\/turnos\/offer\.[a-f0-9]{8}\.mjs/);
  assert.doesNotMatch(page, /offer\.js/);
});

for (const [name, target, other] of [["accept", "accept", "reject"], ["reject", "reject", "accept"]]) {
  test(`the actual rendered bootstrap keeps the first early ${name} through the real controller`, async () => {
    const w = world();
    let release;
    const events = {};
    const doc = { ...w.document, currentScript: { getAttribute: (key) => key === "data-token" ? "tok-abc" : null } };
    const oldDocument = globalThis.document;
    globalThis.document = doc;
    try {
      const api = await load();
      const context = vm.createContext({
        document: doc,
        location: globalThis.location,
        window: { addEventListener(name, listener) { events[name] = listener; } },
        globalThis: null,
      });
      context.globalThis = context;
      context.__loadController = () => new Promise((resolve) => { release = resolve; });
      vm.runInContext(renderedBootstrap(), context);
      assert.equal(target === "accept" ? w.accept.click() : w.reject.click(), true);
      assert.equal(other === "accept" ? w.accept.click() : w.reject.click(), true);
      assert.deepEqual(w.posts, []);
      release(api);
      await settle();
      assert.deepEqual(w.posts.map((post) => post.url), [`/api/offer/tok-abc/${target}`]);
      events.pagehide();
    } finally { globalThis.document = oldDocument; w.restore(); }
  });
}

test("ordinary offer loads never mutate, and a rejected module exposes a working reload", async () => {
  const w = world();
  const children = [];
  const events = {};
  const doc = {
    ...w.document,
    currentScript: { getAttribute: () => "tok-abc" },
    createElement() { return { style: {}, setAttribute() {}, addEventListener(_name, listener) { this.click = listener; } }; },
  };
  w.msg.appendChild = (node) => children.push(node);
  try {
    const context = vm.createContext({
      document: doc, location: globalThis.location,
      window: { addEventListener(name, listener) { events[name] = listener; } }, globalThis: null,
    });
    context.globalThis = context;
    context.__loadController = () => Promise.reject(new Error("synthetic module failure"));
    vm.runInContext(renderedBootstrap(), context);
    await settle();
    assert.deepEqual(w.posts, []);
    assert.equal(w.accept.disabled, true);
    assert.equal(w.reject.style.pointerEvents, "none");
    assert.equal(children.length, 1);
    children[0].click();
    assert.equal(globalThis.location.reloads, 1);
    events.pagehide();
  } finally { w.restore(); }
});

test("a controller mount error in the actual rendered bootstrap exposes the same visible reload", async () => {
  const w = world();
  const children = [];
  const doc = {
    ...w.document,
    currentScript: { getAttribute: () => "tok-abc" },
    createElement() { return { style: {}, setAttribute() {}, addEventListener(_name, listener) { this.click = listener; } }; },
  };
  w.msg.appendChild = (node) => children.push(node);
  try {
    const context = vm.createContext({
      document: doc, location: globalThis.location,
      window: { addEventListener() {} }, globalThis: null,
    });
    context.globalThis = context;
    context.__loadController = () => Promise.resolve({ mount() { throw new Error("synthetic mount failure"); } });
    vm.runInContext(renderedBootstrap(), context);
    await settle();
    assert.equal(w.accept.disabled, true);
    assert.equal(w.reject.style.pointerEvents, "none");
    assert.equal(children.length, 1);
    children[0].click();
    assert.equal(globalThis.location.reloads, 1);
  } finally { w.restore(); }
});

test("the real loader invalidates a delayed controller on pagehide and reloads a persisted page", async () => {
  const w = world();
  let release;
  const events = {};
  const doc = { ...w.document, currentScript: { getAttribute: () => "tok-abc" } };
  const oldDocument = globalThis.document;
  globalThis.document = doc;
  try {
    const api = await load();
    const context = vm.createContext({
      document: doc, location: globalThis.location,
      window: { addEventListener(name, listener) { events[name] = listener; } }, globalThis: null,
    });
    context.globalThis = context;
    context.__loadController = () => new Promise((resolve) => { release = resolve; });
    vm.runInContext(renderedBootstrap(), context);
    w.accept.click();
    events.pagehide();
    release(api);
    await settle();
    assert.deepEqual(w.posts, []);
    events.pageshow({ persisted: true });
    assert.equal(globalThis.location.reloads, 1);
  } finally { globalThis.document = oldDocument; w.restore(); }
});

test("i18n rejection and timeout keep an explicit decline, locked before readiness", async () => {
  for (const i18n of ["data:text/javascript,throw new Error('i18n failed')", "data:text/javascript,await new Promise(()=>{});"]) {
    const w = world({ i18n });
    const oldTimer = globalThis.setTimeout;
    if (i18n.includes("new Promise")) globalThis.setTimeout = (fn) => { fn(); return 1; };
    try {
      const api = await load();
      const controller = api.mount({ token: "tok", document: w.document });
      w.reject.click();
      assert.equal(w.reject.style.pointerEvents, "none");
      await settle();
      assert.deepEqual(w.posts.map((post) => post.url), ["/api/offer/tok/reject"]);
      controller.destroy();
    } finally { globalThis.setTimeout = oldTimer; w.restore(); }
  }
});

test("malformed JSON and network failures unlock an offer for retry", async () => {
  const w = world();
  try {
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new Error("bad json")) });
    const api = await load();
    api.mount({ token: "tok", document: w.document });
    w.accept.click();
    await settle();
    assert.equal(w.accept.disabled, false);
    assert.match(w.msg.textContent, /No pudimos procesar/);
    globalThis.fetch = () => Promise.reject(new Error("offline"));
    w.accept.click();
    await settle();
    assert.equal(w.accept.disabled, false);
    assert.match(w.msg.textContent, /No pudimos procesar/);
  } finally { w.restore(); }
});

test("malformed successful JSON shapes and every non-410 HTTP failure are retryable", async () => {
  for (const response of [
    { ok: true, status: 200, body: {} },
    { ok: true, status: 200, body: null },
    { ok: true, status: 200, body: { ok: "true" } },
    { ok: false, status: 500, body: { error: "temporary" } },
    { ok: false, status: 502, body: { error: "temporary" } },
    { ok: false, status: 400, body: { error: "token_invalido" } },
    { ok: false, status: 404, body: { error: "no_existe" } },
    { ok: false, status: 409, body: { error: "conflict" } },
  ]) {
    const w = world({ response });
    try {
      const api = await load();
      const controller = api.mount({ token: "tok", document: w.document });
      w.accept.click();
      await settle();
      assert.equal(w.accept.disabled, false, JSON.stringify(response));
      assert.match(w.msg.textContent, /No pudimos procesar/, JSON.stringify(response));
      controller.destroy();
    } finally { w.restore(); }
  }
});

test("a save of unknown outcome shows the sentence and the «Mis turnos» link and keeps the buttons off", async () => {
  const sentence = "No sabemos si quedó guardado. Revisá antes de repetir.";
  const w = world({ response: { ok: false, status: 503, body: {
    error: sentence, detalle: sentence, outcome: "unknown", accion: { texto: "Ver mis turnos", url: "/my-requests" } } } });
  const children = [];
  w.msg.appendChild = (node) => children.push(node);
  w.document.createElement = () => ({});
  try {
    const api = await load();
    const controller = api.mount({ token: "tok", document: w.document });
    w.accept.click();
    await settle();
    assert.equal(w.msg.textContent, sentence + " ");
    assert.deepEqual(children.map((a) => [a.href, a.textContent]), [["/my-requests", "Ver mis turnos"]]);
    assert.equal(w.accept.disabled, true, "no second accept: it would read «ya no está disponible»");
    assert.equal(w.reject.style.pointerEvents, "none");
    assert.doesNotMatch(w.msg.textContent, /Probá de nuevo|No pudimos procesar/);
    controller.destroy();
  } finally { w.restore(); }
});

test("410 stays unavailable instead of becoming a retry, while stale callbacks after pagehide are inert", async () => {
  let resolveFetch;
  const w = world({ response: { ok: false, status: 410, body: {} } });
  const oldTimer = globalThis.setTimeout;
  const scheduled = [];
  try {
    const api = await load();
    const controller = api.mount({ token: "tok", document: w.document, initialAction: "accept" });
    await Promise.resolve();
    globalThis.setTimeout = (fn) => { scheduled.push(fn); return scheduled.length; };
    for (let index = 0; index < 5; index += 1) await Promise.resolve();
    assert.equal(scheduled.length, 1);
    scheduled[0]();
    assert.equal(globalThis.location.reloads, 1);
    assert.equal(w.accept.disabled, true);
    assert.match(w.msg.textContent, /ya no está disponible/);
    globalThis.setTimeout = oldTimer;
    globalThis.fetch = () => new Promise((resolve) => { resolveFetch = resolve; });
    const late = api.mount({ token: "late", document: w.document, initialAction: "accept" });
    await settle();
    late.destroy();
    resolveFetch({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) });
    await tick();
    assert.notEqual(w.msg.textContent, "¡Listo! Tu turno quedó confirmado. Te llevamos a Mis turnos…");
    controller.destroy();
  } finally { globalThis.setTimeout = oldTimer; w.restore(); }
});
