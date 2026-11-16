import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import vm from "node:vm";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { until, stays } from "./helpers/wait.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MODULE = pathToFileURL(join(ROOT, "app/modules/turnos/confirm.mjs")).href;

function control(value) {
  const listeners = [];
  return { disabled: false, style: {}, getAttribute: (name) => name === "data-confirm-action" ? value : null,
    addEventListener: (_name, fn) => listeners.push(fn), click() { listeners.forEach((fn) => fn({ preventDefault() {} })); } };
}
async function load() { return import(`${MODULE}?confirm-test=${Date.now()}-${Math.random()}`); }
function world({ response = { ok: true, status: 200, body: { ok: true, mensaje: "Listo" } }, i18n = null } = {}) {
  const controls = [control("si"), control("no")], posts = [], msg = { textContent: "", style: {} };
  const oldWindow = globalThis.window, oldFetch = globalThis.fetch;
  globalThis.window = i18n ? { SANA_STATIC: { "platform/i18n.mjs": i18n } } : {};
  globalThis.fetch = (url, opts) => { posts.push({ url, opts }); return Promise.resolve({ ok: response.ok, status: response.status, json: () => Promise.resolve(response.body) }); };
  return { controls, posts, msg, document: { querySelectorAll: () => controls, getElementById: () => msg }, restore() { globalThis.window = oldWindow; globalThis.fetch = oldFetch; } };
}

function renderedBootstrap(mode = "si") {
  const code = [
    "from app.modules.turnos import appointment_confirm as c",
    `print(c._ask_page('tok-abc', 'Consulta', 'CAPS', 'Mañana', '${mode}').decode())`,
  ].join("; ");
  const page = execFileSync("python3", ["-c", code], { cwd: ROOT, encoding: "utf8" });
  return page.match(/<script data-token="tok-abc"(?: data-auto="si")?>([\s\S]*?)<\/script>/)[1]
    .replace(/import\("[^"]+"\)/, "globalThis.__loadController()");
}

test("the actual rendered bootstrap retains an early action until its controller arrives", async () => {
  let resolveModule, received;
  const yes = control("si"), no = control("no"), msg = { textContent: "", style: {}, querySelector: () => null, appendChild() {} };
  const script = { getAttribute: (name) => name === "data-token" ? "tok-abc" : null };
  const context = vm.createContext({
    document: { currentScript: script, querySelectorAll: () => [yes, no], getElementById: () => msg, createElement: () => ({ style: {}, setAttribute() {}, addEventListener() {} }) },
    location: { reload() {} }, window: { addEventListener() {} },
    globalThis: null,
  });
  context.globalThis = context;
  context.__loadController = () => new Promise((resolve) => { resolveModule = resolve; });
  vm.runInContext(renderedBootstrap(""), context);
  no.click();
  resolveModule({ mount: (opts) => { received = opts; return {}; } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(received.token, "tok-abc");
  assert.equal(received.initialAction, "no");
});

test("the actual rendered bootstrap rejects safely with visible retry and no POST", async () => {
  const yes = control("si"), no = control("no");
  const children = [], msg = { textContent: "", style: {}, querySelector: () => null, appendChild: (node) => children.push(node) };
  const script = { getAttribute: (name) => name === "data-token" ? "tok-abc" : name === "data-auto" ? "si" : null };
  const context = vm.createContext({
    document: { currentScript: script, querySelectorAll: () => [yes, no], getElementById: () => msg, createElement: () => ({ style: {}, setAttribute() {}, addEventListener() {} }) },
    location: { reload() {} }, window: { addEventListener() {} }, globalThis: null,
  });
  context.globalThis = context;
  context.__loadController = () => Promise.reject(new Error("synthetic rejected controller"));
  vm.runInContext(renderedBootstrap(), context);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(msg.textContent, /No pudimos cargar/);
  assert.equal(yes.disabled, true);
  assert.equal(no.disabled, true);
  assert.equal(children.length, 1);
});

test("classic confirmation source is physically retired and the Turnos asset is canonical", () => {
  const manifest = readFileSync(join(ROOT, "app/modules/turnos", "module.json"), "utf8");
  assert.equal(existsSync(join(ROOT, "confirm.js")), false);
  assert.match(manifest, /modules\/turnos\/confirm\.mjs/);
});

test("ordinary load and data-auto=no never POST", async () => {
  const w = world();
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document, autoAction: "no" });
    await Promise.resolve();
    assert.deepEqual(w.posts, []);
  } finally { w.restore(); }
});

test("affirmative auto waits for readiness then POSTs only affirmative", async () => {
  const w = world();
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document, autoAction: "si" });
    await Promise.resolve();
    assert.equal(w.posts.length, 1);
    assert.equal(w.posts[0].url, "/api/confirm/tok/si");
  } finally { w.restore(); }
});

test("a click posts its intended response and a failed request unlocks it for retry", async () => {
  const w = world({ response: { ok: false, status: 500, body: {} } });
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document });
    w.controls[1].click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(w.posts[0].url, "/api/confirm/tok/no");
    assert.equal(w.controls[1].disabled, false);
    assert.match(w.msg.textContent, /No pudimos guardar/);
  } finally { w.restore(); }
});

test("a save of unknown outcome shows the sentence and the «Mis turnos» link and keeps the controls off", async () => {
  const sentence = "No sabemos si quedó guardado. Revisá antes de repetir.";
  const w = world({ response: { ok: false, status: 503, body: {
    error: sentence, detalle: sentence, outcome: "unknown", accion: { texto: "Ver mis turnos", url: "/my-requests" } } } });
  const children = [];
  w.msg.appendChild = (node) => children.push(node);
  w.document.createElement = () => ({});
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document });
    w.controls[0].click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(w.msg.textContent, sentence + " ");
    assert.deepEqual(children.map((a) => [a.href, a.textContent]), [["/my-requests", "Ver mis turnos"]]);
    assert.deepEqual(w.controls.map((c) => c.disabled), [true, true], "no second answer: it may already be saved");
    assert.doesNotMatch(w.msg.textContent, /Probá de nuevo/);
  } finally { w.restore(); }
});

test("a rejected i18n import retains fallback copy and does not strand an action", async () => {
  const w = world({ i18n: "data:text/javascript,throw new Error('i18n failed')" });
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document, initialAction: "si" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(w.posts.length, 1);
    assert.equal(w.msg.textContent, "Listo");
  } finally { w.restore(); }
});

test("a destroyed controller ignores stale response callbacks", async () => {
  let resolveFetch;
  const w = world();
  const oldFetch = globalThis.fetch;
  globalThis.fetch = (url, opts) => { w.posts.push({ url, opts }); return new Promise((resolve) => { resolveFetch = resolve; }); };
  try {
    const api = await load();
    const controller = api.mount({ token: "tok", document: w.document, initialAction: "si" });
    await Promise.resolve();
    controller.destroy();
    resolveFetch({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, mensaje: "stale" }) });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.notEqual(w.msg.textContent, "stale");
  } finally { globalThis.fetch = oldFetch; w.restore(); }
});

// Exercise the actual Python-rendered loader AND canonical controller in one
// chain. Only the import transport is held, never module.mount or the POST.
for (const action of ["si", "no"]) {
  test(`held rendered loader preserves the first ${action} click through the real controller`, async () => {
    const w = world();
    try {
      const api = await load();
      let release;
      const doc = { ...w.document, currentScript: { getAttribute: (key) => key === "data-token" ? "tok-abc" : null } };
      const oldDocument = globalThis.document;
      globalThis.document = doc;
      try {
        const context = vm.createContext({ document: doc, window: { addEventListener() {} }, location: { reload() {} } });
        context.__loadController = () => new Promise((resolve) => { release = resolve; });
        vm.runInContext(renderedBootstrap(""), context);
        const index = action === "si" ? 0 : 1;
        w.controls[index].click();
        w.controls[1 - index].click();
        assert.equal(w.posts.length, 0);
        release(api);
        await new Promise((resolve) => setTimeout(resolve, 0));
        assert.equal(w.posts.length, 1);
        assert.equal(w.posts[0].url, `/api/confirm/tok-abc/${action}`);
        assert.equal(w.posts[0].opts.method, "POST");
      } finally { globalThis.document = oldDocument; }
    } finally { w.restore(); }
  });
}

test("queued explicit decline wins over affirmative auto while i18n arrives", async () => {
  let releaseI18n;
  globalThis.__confirmI18nGate = new Promise((resolve) => { releaseI18n = resolve; });
  const w = world({ i18n: "data:text/javascript,await globalThis.__confirmI18nGate;export const t=(k,v,f)=>f;" });
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document, autoAction: "si", initialAction: "no" });
    w.controls[0].click();
    assert.equal(w.posts.length, 0);
    releaseI18n();
    await until(() => w.posts.length >= 1, "the queued decline is sent once i18n arrives");
    await stays(() => w.posts.length === 1, "the affirmative auto action is not sent as well", 20);
    assert.equal(w.posts.length, 1);
    assert.equal(w.posts[0].url, "/api/confirm/tok/no");
  } finally { releaseI18n(); delete globalThis.__confirmI18nGate; w.restore(); }
});

test("real loader pagehide invalidates a delayed controller before any mutation", async () => {
  const w = world();
  try {
    const api = await load();
    let release;
    const events = {};
    const doc = { ...w.document, currentScript: { getAttribute: (key) => key === "data-token" ? "tok-abc" : key === "data-auto" ? "si" : null } };
    const context = vm.createContext({ document: doc, window: { addEventListener(name, fn) { events[name] = fn; } }, location: { reload() {} } });
    context.__loadController = () => new Promise((resolve) => { release = resolve; });
    vm.runInContext(renderedBootstrap(), context);
    events.pagehide();
    release(api);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(w.posts.length, 0);
  } finally { w.restore(); }
});

test("i18n readiness times out to fallback copy instead of leaving the confirmation locked", async () => {
  const oldTimer = globalThis.setTimeout;
  const w = world({ i18n: "data:text/javascript,await new Promise(()=>{});" });
  globalThis.setTimeout = (fn) => { fn(); return 1; };
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document, initialAction: "no" });
    await Promise.resolve();
    assert.equal(w.posts.length, 1);
    assert.equal(w.posts[0].url, "/api/confirm/tok/no");
  } finally { globalThis.setTimeout = oldTimer; w.restore(); }
});

test("malformed JSON and a network error both unlock the controls for a retry", async () => {
  const w = world();
  try {
    globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new Error("bad json")) });
    const api = await load();
    api.mount({ token: "tok", document: w.document });
    w.controls[0].click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(w.controls[0].disabled, false);
    assert.match(w.msg.textContent, /No pudimos guardar/);
    globalThis.fetch = () => Promise.reject(new Error("offline"));
    w.controls[0].click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(w.controls[0].disabled, false);
    assert.match(w.msg.textContent, /No pudimos guardar/);
  } finally { w.restore(); }
});

test("410 schedules a reload and never re-enables the expired confirmation", async () => {
  const oldTimer = globalThis.setTimeout, oldLocation = globalThis.location;
  let reloads = 0;
  const w = world({ response: { ok: false, status: 410, body: {} } });
  globalThis.location = { reload() { reloads += 1; } };
  globalThis.setTimeout = (fn) => { fn(); return 1; };
  try {
    const api = await load();
    api.mount({ token: "tok", document: w.document, initialAction: "si" });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(reloads, 1);
    assert.equal(w.controls[0].disabled, true);
    assert.match(w.msg.textContent, /ya no se puede confirmar/);
  } finally { globalThis.setTimeout = oldTimer; globalThis.location = oldLocation; w.restore(); }
});
