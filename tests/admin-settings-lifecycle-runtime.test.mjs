// Lifecycle ownership tests execute the native Settings module itself.
import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../static/platform/admin-settings.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 8) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }

class Element {
  constructor(id = "") { this.id = id; this.isConnected = true; this.innerHTML = ""; this.style = {}; this.listeners = new Map(); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
}

function fixture({ fetches, confirmar = () => Promise.resolve(false), controls = false }, t) {
  const host = new Element("sec-settings");
  const save = new Element();
  const card = new Element();
  card.getAttribute = name => name === "data-grupo" ? "location" : null;
  const saveMessage = new Element();
  save.getAttribute = name => ({ "data-grupo": "location", "data-card": null }[name] ?? null);
  save.closest = () => card;
  card.querySelectorAll = () => [];
  host.querySelector = selector => selector === '[data-msg="location"]' ? saveMessage : null;
  host.querySelectorAll = selector => controls && selector === ".set-guardar" ? [save] : [];
  const nodes = new Map([["sec-settings", host]]);
  const previous = Object.fromEntries(["window", "document", "fetch", "location", "history", "__settingsConfirm"].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const document = {
    readyState: "complete", head: { appendChild(node) { if (node.id) nodes.set(node.id, node); } },
    createElement: () => new Element(), getElementById: id => nodes.get(id) || null,
    querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, removeEventListener() {},
  };
  const listeners = new Map();
  globalThis.__settingsConfirm = confirmar;
  globalThis.document = document;
  globalThis.location = { href: "https://example.test/admin/settings", pathname: "/admin/settings", search: "", hash: "" };
  globalThis.history = { pushState() {}, replaceState() {} };
  globalThis.window = {
    SANA_STATIC: {
      "platform/admin-session.mjs": "data:text/javascript,export const confirmar=(r)=>globalThis.__settingsConfirm(r)",
      "platform/i18n.mjs": "data:text/javascript,export const t=(key,vars,fallback)=>fallback",
    },
    addEventListener(name, fn) { listeners.set(name, fn); }, removeEventListener(name) { listeners.delete(name); },
  };
  const signals = [], requests = [];
  globalThis.fetch = (url, options = {}) => { signals.push(options.signal); requests.push({ url, options }); const next = fetches.shift(); assert.ok(next, "unexpected Settings request"); return next; };
  const disposes = [];
  t.after(() => { disposes.forEach(dispose => dispose()); for (const [key, descriptor] of Object.entries(previous)) descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key]; });
  return { host, save, saveMessage, signals, requests, run(options) { const dispose = mount(host, options); disposes.push(dispose); return dispose; } };
}

const FRESH = { lock: false, location: {}, languages: {}, api_keys: [], chat: {}, turnos: {}, seguridad: {}, connectors: {} };

test("confirmed 401 leaves Settings to the shared session boundary", async t => {
  const page = fixture({ fetches: [Promise.resolve(response(401))], confirmar: () => Promise.resolve(true) }, t);
  page.host.innerHTML = "existing Settings screen";
  await page.run().ready;
  assert.equal(page.host.innerHTML, "existing Settings screen");
});

test("ordinary 403 remains visible local recovery", async t => {
  const page = fixture({ fetches: [Promise.resolve(response(403))], confirmar: () => Promise.resolve(false) }, t);
  await page.run().ready;
  assert.match(page.host.innerHTML, /role="alert"/);
});

test("a late disposed load cannot repaint a same-host remount", async t => {
  const old = deferred();
  const page = fixture({ fetches: [old.promise, Promise.resolve(response(200, FRESH))] }, t);
  page.run(); await settle();
  const latest = page.run(); await latest.ready;
  old.resolve(response(200, { ...FRESH, lock: true })); await settle();
  assert.equal(page.signals[0].aborted, true);
  assert.doesNotMatch(page.host.innerHTML, /La configuración está bloqueada/);
});

test("a late confirmed session result cannot paint over a replacement mount", async t => {
  const confirmation = deferred();
  const page = fixture({
    fetches: [Promise.resolve(response(401)), Promise.resolve(response(200, FRESH))],
    confirmar: () => confirmation.promise,
  }, t);
  page.run(); await settle();
  const latest = page.run(); await latest.ready;
  confirmation.resolve(true); await settle();
  assert.match(page.host.innerHTML, /Ubicación/);
  assert.doesNotMatch(page.host.innerHTML, /No se pudo cargar esta sección/);
});

test("a successful native load renders actual Settings content", async t => {
  const page = fixture({ fetches: [Promise.resolve(response(200, FRESH))] }, t);
  await page.run().ready;
  assert.match(page.host.innerHTML, /Ubicación/);
  assert.match(page.host.innerHTML, /Idiomas/);
});

// Review 03/11: a Spanish panel carries no client catalog, so the T() fallback
// IS what staff read — the AI chat card came out in English, and every card
// opened with a gray explanation (one naming TURNOS_WEBDATA_DIR).
test("Datos e idiomas reads in Spanish with no gray explanations or setting names", async t => {
  const page = fixture({ fetches: [Promise.resolve(response(200, {
    ...FRESH, lock: true, location: { modo_paquetes: false },
    chat: { greetings: [{ key: "CHAT_GREETING_ES", lang: "es", value: "" }] },
  }))] }, t);
  await page.run().ready;
  const html = page.host.innerHTML;
  assert.match(html, /<h2>Chat con IA<\/h2>/);
  assert.match(html, /Saludo en /);
  assert.match(html, /Borrá el contenido de un campo/);
  assert.match(html, /Escribí \{brand\} donde va el nombre de la instalación/);
  assert.match(html, /La configuración está bloqueada en el servidor/);
  assert.doesNotMatch(html, /AI chat|Greeting in|Clear a field|>Save</);
  assert.doesNotMatch(html, /class="set-lede">/);
  const visible = html.replace(/<[^>]*>/g, " ");
  assert.doesNotMatch(visible, /TURNOS_|modo legacy|modo paquetes/);
});

for (const [search, hidden] of [["", true], ["?view=connectors", true], ["?view=integrations", false]]) {
  test("the «Probar» AI notice shows only on Integraciones: " + (search || "general"), async t => {
    const page = fixture({ fetches: [Promise.resolve(response(200, FRESH))] }, t);
    const notice = { hidden: !hidden };
    globalThis.location.search = search;
    globalThis.document.querySelector = selector => selector === '[data-ai-notice="probe"]' ? notice : null;
    await page.run().ready;
    assert.equal(notice.hidden, hidden);
  });
}

test("a 200 Settings response without the required payload remains visible recovery", async t => {
  const page = fixture({ fetches: [Promise.resolve(response(200, { api_keys: [] }))] }, t);
  await page.run().ready;
  assert.match(page.host.innerHTML, /role="alert"/);
});

test("a successful save acknowledges its own POST once and re-enables its control", async t => {
  const page = fixture({
    controls: true,
    fetches: [Promise.resolve(response(200, FRESH)), Promise.resolve(response(200, { ok: true, location: {} }))],
  }, t);
  await page.run().ready;
  page.save.listeners.get("click")();
  page.save.listeners.get("click")();
  await settle();
  assert.equal(page.requests.filter(request => request.options.method === "POST").length, 1);
  assert.equal(JSON.parse(page.requests[1].options.body).grupo, "location");
  assert.equal(page.saveMessage.textContent, "Guardado.");
  assert.equal(page.save.disabled, false);
});

for (const malformed of [{ location: {} }, { ok: false, location: {} }, { ok: true, location: null }]) {
  test("a malformed 200 save never acknowledges success: " + JSON.stringify(malformed), async t => {
    const page = fixture({ controls: true, fetches: [Promise.resolve(response(200, FRESH)), Promise.resolve(response(200, malformed))] }, t);
    await page.run().ready;
    page.save.listeners.get("click")();
    await settle();
    assert.equal(page.saveMessage.textContent, "No se pudo guardar.");
    assert.equal(page.save.disabled, false);
    assert.equal(page.requests.length, 2);
  });
}
