import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { mountMyAppointments } from "../static/platform/admin-my-appointments.mjs";
import { placeHost, withScreenBoot, withSiblings } from "./screen-boot-fixture.mjs";
const template = await readFile(new URL("../templates/admin/mis-turnos.html", import.meta.url), "utf8");
const bootstrap = [...template.matchAll(/<script type="module">\n([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(bootstrap, "the actual appointments template has a native bootstrap");

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(n = 8) { while (n--) await tick(); }
function deferred() { let resolve; let reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
function response(status = 200, body = {}) { return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) }; }
class Node { constructor(id = "") { this.id = id; this.innerHTML = ""; this.textContent = ""; this.style = {}; this.listeners = new Map(); this.children = []; } addEventListener(n, f) { this.listeners.set(n, f); } removeEventListener(n) { this.listeners.delete(n); } querySelector() { return null; } querySelectorAll() { return []; } appendChild(n) { this.children.push(n); return n; } }
const PlacedNode = withSiblings(Node);
async function executeBootstrap(source, t) {
  const host = new PlacedNode("sec-mis-turnos"), button = new Node(), events = new Map(), timers = new Map(); let nextTimer = 0, reloads = 0;
  const placed = placeHost(PlacedNode, host);
  const saved = Object.fromEntries(["document", "window", "location", "setTimeout", "clearTimeout", "addEventListener"].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  const recovery = { content: { cloneNode() { return { fragment: true, children: ["alert", button], querySelector() { return button; } }; } } };
  host.replaceChildren = (...nodes) => { host.children = nodes; };
  globalThis.document = { getElementById(id) { return id === "sec-mis-turnos" ? host : recovery; }, createElement: () => new PlacedNode() };
  globalThis.window = { addEventListener(name, fn) { events.set(name, fn); } };
  globalThis.addEventListener = (name, fn) => events.set(name, fn);
  globalThis.location = { reload() { reloads++; } };
  globalThis.setTimeout = fn => { const id = ++nextTimer; timers.set(id, fn); return id; };
  globalThis.clearTimeout = id => timers.delete(id);
  t.after(() => { for (const [k, v] of Object.entries(saved)) v ? Object.defineProperty(globalThis, k, v) : delete globalThis[k]; });
  const url = "data:text/javascript," + encodeURIComponent(source);
  const code = withScreenBoot(bootstrap).replace(/"\{\{ static\('platform\/admin-my-appointments\.mjs'\) \}\}"/, JSON.stringify(url));
  await import("data:text/javascript," + encodeURIComponent(code) + "#" + Math.random());
  return { host, button, events, timers, slowCopy: placed.slowCopy, fireDeadline() { [...timers.values()].forEach(fn => fn()); }, reloads: () => reloads };
}
function fixture(fetches, t, session = "data:text/javascript,export const confirmar=()=>Promise.resolve(false)", calNodes = false) {
  const host = new Node("sec-mis-turnos"), nodes = new Map([[host.id, host]]), listeners = new Map();
  if (calNodes) {
    // «Agregar a mi calendario»: the title button and its panel, both server-
    // painted by the template and filled on demand by the controller.
    for (const id of ["mt-cal-open", "mt-cal"]) {
      const n = new Node(id);
      n.setAttribute = (k, v) => { n[k] = v; };
      n.hidden = id === "mt-cal";  // the template paints the panel hidden
      nodes.set(id, n);
    }
  }
  const saved = Object.fromEntries(["window", "document", "fetch", "navigator", "__tableDispose", "__pickerDispose"].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  const disposed = []; let tableDisposals = 0, pickerDisposals = 0, calls = 0;
  let cleaned = false;
  function cleanup() {
    if (cleaned) return;
    cleaned = true;
    try { disposed.forEach(d => d()); }
    finally { for (const [k, v] of Object.entries(saved)) v ? Object.defineProperty(globalThis, k, v) : delete globalThis[k]; }
  }
  t.after(cleanup);
  globalThis.document = { getElementById: id => nodes.get(id) || null, querySelector: s => s === "#sec-mis-turnos" ? host : null, querySelectorAll: () => [], createElement: () => new Node(), addEventListener(n, f) { listeners.set(n, f); }, removeEventListener(n) { listeners.delete(n); }, body: { classList: { remove() {} } } };
  globalThis.window = { SANA_STATIC: {
    "platform/dates.mjs": "data:text/javascript,export const dia=x=>x;export const larga=x=>x",
    "platform/format.mjs": "data:text/javascript,export const edadTexto=()=>'';export const contactoHtml=()=>'';export const nombre=x=>x;export const dni=x=>x",
    "platform/admin-table.mjs": "data:text/javascript,export const crear=()=>({programarFit(){},dispose(){globalThis.__tableDispose++}});export const columnas=()=>({activas(){return[{key:'paciente',label:'Paciente',always:true}]},gearHtml(){return''},dispose(){globalThis.__pickerDispose++}})",
    "platform/admin-session.mjs": session,
    "platform/i18n.mjs": "data:text/javascript,export const t=(k,v,f)=>f",
  }, requestAnimationFrame: f => { f(); return 1; }, cancelAnimationFrame() {} };
  globalThis.__tableDispose = 0; globalThis.__pickerDispose = 0;
  Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true,
    value: { clipboard: { writeText: () => Promise.resolve() } } });
  globalThis.fetch = (url) => { calls++; const next = fetches.shift(); assert.ok(next, "unexpected request " + url); return next; };
  return { host, cleanup, cal: id => nodes.get(id) || null, requests: () => calls, run(root = globalThis.document) { const d = mountMyAppointments({ root }); disposed.push(d); return d; }, counts: () => [globalThis.__tableDispose, globalThis.__pickerDispose] };
}
const POPULATED = { turnos: [{ turno_dt: "2030-01-01T10:00:00", dia_iso: "2030-01-01", hora: "10:00", paciente: "Ana", dni: "1" }], entrevista_activa: false };
test("fixture supports and exactly restores a getter-only navigator", async t => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const getter = () => ({ userAgent: "synthetic getter-only runtime" });
  const instrumentation = Object.fromEntries(["__tableDispose", "__pickerDispose"].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  Object.defineProperty(globalThis, "navigator", { configurable: true, get: getter });
  let page;
  try {
    page = fixture([Promise.resolve(response(200, { turnos: [] }))], t);
    await page.run().ready;
    page.cleanup();
    assert.equal(Object.getOwnPropertyDescriptor(globalThis, "navigator").get, getter);
    for (const [key, descriptor] of Object.entries(instrumentation)) {
      assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, key), descriptor);
    }
  } finally {
    page?.cleanup();
    if (original) Object.defineProperty(globalThis, "navigator", original);
    else delete globalThis.navigator;
  }
});
test("retry works while mounted but an obsolete retry cannot request after disposal", async t => {
  const page = fixture([Promise.resolve(response(500)), Promise.resolve(response(500))], t);
  const retry = new Node();
  page.host.querySelector = selector => selector === "[data-retry]" ? retry : null;
  const dispose = page.run();
  await dispose.ready;
  await retry.listeners.get("click")();
  assert.equal(page.requests(), 2, "live retry must make a request");
  const oldRetry = retry.listeners.get("click");
  const rendered = page.host.innerHTML;
  dispose();
  await oldRetry();
  assert.equal(page.requests(), 2, "disposed retry must not make a request");
  assert.equal(page.host.innerHTML, rendered);
});
test("actual controller renders its populated appointments screen", async t => {
  const page = fixture([Promise.resolve(response(200, POPULATED))], t);
  await page.run().ready; await settle(); assert.match(page.host.innerHTML, /Ana/);
});
test("actual controller preserves the successful empty appointment state", async t => {
  const page = fixture([Promise.resolve(response(200, { turnos: [], entrevista_activa: false }))], t);
  await page.run().ready; await settle();
  assert.match(page.host.innerHTML, /No tenés turnos/);
});
test("ready remains pending until the initial appointment request has rendered", async t => {
  const load = deferred();
  const page = fixture([load.promise], t);
  const dispose = page.run(); let done = false; dispose.ready.then(() => { done = true; });
  await settle(); assert.equal(done, false);
  load.resolve(response(200, POPULATED)); await dispose.ready;
  assert.match(page.host.innerHTML, /Ana/);
});
test("a rejected required session dependency is observed through ready", async t => {
  const page = fixture([], t, "data:text/javascript,throw new Error('session import rejected')");
  await assert.rejects(page.run().ready, /session import rejected/);
  assert.equal(page.requests(), 0);
});
test("the actual template bootstrap recovers a rejected import by reloading, not retrying a cached module", async t => {
  const page = await executeBootstrap("throw new Error('rejected')", t); await settle();
  assert.equal(page.host.children.length, 1); page.button.onclick(); assert.equal(page.reloads(), 1);
});
test("the actual template bootstrap deadline shows the localized copy beside the screen; pagehide retires it", async t => {
  const source = "await new Promise(()=>{});export function mountMyAppointments(){throw new Error('must not mount')}";
  const page = await executeBootstrap(source, t); await settle(); page.fireDeadline();
  assert.ok(page.slowCopy(), "the late copy is up"); assert.equal(page.host.children.length, 0, "the screen is left alone");
  page.events.get("pagehide")(); assert.equal(page.slowCopy(), null);
});
test("late old appointment response cannot repaint a remount and owned table instances dispose", async t => {
  const old = deferred();
  const page = fixture([old.promise, Promise.resolve(response(200, POPULATED))], t);
  page.run(); await settle(); const current = page.run(); await current.ready; old.resolve(response(200, { turnos: [] })); await settle();
  assert.match(page.host.innerHTML, /Ana/); current(); assert.deepEqual(page.counts(), [1, 1]);
});
test("two mounted roots remain isolated", async t => {
  // Since 2026-10-27 the calendar address is fetched only when its panel
  // opens: each mount makes exactly one request now.
  const page = fixture([Promise.resolve(response(200, { turnos: [] })), Promise.resolve(response(200, POPULATED))], t);
  const left = new Node("sec-mis-turnos"), right = new Node("sec-mis-turnos");
  await page.run(left).ready; await settle(); await page.run(right).ready; await settle();
  assert.match(left.innerHTML, /No tenés turnos/); assert.match(right.innerHTML, /Ana/);
});
test("a malformed 200 (absent or wrong-type turnos) never renders the empty state", async t => {
  const bodies = [null, {}, { turnos: "9:00" }, { turnos: { length: 0 } }, [{ hora: "9:00" }]];
  const page = fixture(bodies.map(b => Promise.resolve(response(200, b))), t);
  for (const body of bodies) {
    await page.run().ready; await settle();
    assert.match(page.host.innerHTML, /Estamos trayendo tus turnos/, "malformed body " + JSON.stringify(body) + " must keep fetching quietly");
    assert.ok(page.host.innerHTML.includes("data-retry"), "malformed body " + JSON.stringify(body) + " must offer retry");
    assert.doesNotMatch(page.host.innerHTML, /No tenés turnos/, "malformed body " + JSON.stringify(body) + " must not pose as the successful empty state");
  }
});
test("invalid JSON on a 200 reaches the load-error retry path", async t => {
  const page = fixture([Promise.resolve({ status: 200, ok: true, json: () => Promise.reject(new SyntaxError("Unexpected token")) })], t);
  await page.run().ready; await settle();
  assert.match(page.host.innerHTML, /Estamos trayendo tus turnos/);
  assert.doesNotMatch(page.host.innerHTML, /No tenés turnos/);
});
test("a 401 the session check does not confirm keeps fetching quietly instead of freezing on the loading text", async t => {
  // admin-session.mjs keeps an unconfirmed 401 (site Basic Auth, a transient
  // upstream answer) local: the screen must not sit on «Cargando tus turnos…».
  const page = fixture([Promise.resolve(response(401, { error: "no_autenticado" }))], t);
  await page.run().ready; await settle();
  assert.match(page.host.innerHTML, /Estamos trayendo tus turnos/);
  assert.ok(page.host.innerHTML.includes("data-retry"));
});
test("a confirmed 401 leaves the screen to the redirect to login", async t => {
  const page = fixture([Promise.resolve(response(401, { error: "no_autenticado" }))], t, "data:text/javascript,export const confirmar=()=>Promise.resolve(true)");
  page.host.innerHTML = "<p>Cargando tus turnos…</p>";
  await page.run().ready; await settle();
  assert.equal(page.host.innerHTML, "<p>Cargando tus turnos…</p>");
  assert.equal(page.requests(), 1);
});
test("retry after a malformed 200 refetches instead of replaying the broken body", async t => {
  const page = fixture([Promise.resolve(response(200, {})), Promise.resolve(response(200, POPULATED))], t);
  const retry = new Node();
  page.host.querySelector = selector => selector === "[data-retry]" ? retry : null;
  await page.run().ready; await settle();
  assert.match(page.host.innerHTML, /Estamos trayendo tus turnos/);
  await retry.listeners.get("click")();
  await settle();
  assert.match(page.host.innerHTML, /Ana/);
  assert.equal(page.requests(), 2, "retry must hit the network again, not a cached broken body");
});

// ---- «Agregar a mi calendario» -------------------------------------------
// The doctor's subscription address: fetched only when the panel opens, with
// the Google / Apple steps and «Cambiar la dirección» (responsable del producto, 2026-10-27).
const click = (node, selector) => node.listeners.get("click")({ target: { closest: s => s === selector ? {} : null } });

test("the calendar panel fetches its address only when opened and paints the steps", async t => {
  const url = "https://pinamar.humanrounds.org/feed/doctor/tok123.ics";
  const page = fixture([Promise.resolve(response(200, POPULATED)), Promise.resolve(response(200, { url }))], t, undefined, true);
  await page.run().ready; await settle();
  const btn = page.cal("mt-cal-open"), panel = page.cal("mt-cal");
  assert.equal(panel.hidden, true, "closed by default: nothing about the calendar is on screen");
  await btn.listeners.get("click")();            // open -> lazy address fetch
  assert.match(panel.innerHTML, /Preparando tu dirección/);
  await settle();
  assert.ok(!panel.hidden);
  assert.match(panel.innerHTML, new RegExp(url));
  assert.match(panel.innerHTML, /Google Calendar/);
  assert.match(panel.innerHTML, /iPhone, iPad o Mac/);
  assert.match(panel.innerHTML, /webcal:\/\/pinamar\.humanrounds\.org/);
  assert.match(panel.innerHTML, /Cambiar la dirección/);
  assert.equal(page.requests(), 2);
  await btn.listeners.get("click")();            // close
  assert.equal(panel.hidden, true); assert.equal(panel.innerHTML, "");
  await btn.listeners.get("click")();            // reopen: cached, no refetch
  await settle();
  assert.match(panel.innerHTML, new RegExp(url));
  assert.equal(page.requests(), 2, "a cached address must not refetch");
});

test("«Cambiar la dirección» asks once, then swaps in the new address", async t => {
  const oldUrl = "https://x.example/feed/doctor/viejo.ics", newUrl = "https://x.example/feed/doctor/nuevo.ics";
  const page = fixture([Promise.resolve(response(200, POPULATED)), Promise.resolve(response(200, { url: oldUrl })), Promise.resolve(response(200, { url: newUrl }))], t, undefined, true);
  const toasts = [];
  globalThis.window.toast = m => toasts.push(m);
  await page.run().ready; await settle();
  const btn = page.cal("mt-cal-open"), panel = page.cal("mt-cal");
  await btn.listeners.get("click")(); await settle();
  click(panel, "[data-cal-rotate]");             // first tap: confirmation, no request yet
  assert.match(panel.innerHTML, /deja de funcionar/);
  assert.equal(page.requests(), 2);
  click(panel, "[data-cal-rotate-no]");          // backing out changes nothing
  assert.doesNotMatch(panel.innerHTML, /deja de funcionar/);
  assert.match(panel.innerHTML, new RegExp(oldUrl));
  click(panel, "[data-cal-rotate]"); click(panel, "[data-cal-rotate-yes]");
  await settle();
  assert.equal(page.requests(), 3, "confirming issues the POST to rotate");
  assert.match(panel.innerHTML, new RegExp(newUrl));
  assert.doesNotMatch(panel.innerHTML, new RegExp(oldUrl));
  assert.ok(toasts.some(m => /ya no funciona/.test(m)));
});

test("an address that already exists shows its last 4 characters, and «Crear una dirección nueva» shows the new one once", async t => {
  // The server keeps only the hash (alembic 0039): after the first visit the
  // GET answers {url: null, hint}, so the panel never has the old address.
  const newUrl = "https://x.example/feed/doctor/nuevoAbCd.ics";
  const page = fixture([Promise.resolve(response(200, POPULATED)), Promise.resolve(response(200, { url: null, hint: "k3Zq" })),
    Promise.resolve(response(200, { url: newUrl, hint: "AbCd" }))], t, undefined, true);
  globalThis.window.toast = () => {};
  await page.run().ready; await settle();
  const btn = page.cal("mt-cal-open"), panel = page.cal("mt-cal");
  await btn.listeners.get("click")(); await settle();
  assert.match(panel.innerHTML, /la que termina en k3Zq/);
  assert.match(panel.innerHTML, /Crear una dirección nueva/);
  assert.doesNotMatch(panel.innerHTML, /\/feed\/doctor\//, "no address is on screen in the hint state");
  click(panel, "[data-cal-rotate-yes]");         // one tap: the old one stops working
  await settle();
  assert.equal(page.requests(), 3);
  assert.match(panel.innerHTML, new RegExp(newUrl));
  assert.match(panel.innerHTML, /no se vuelve a mostrar/);
});

test("an address that cannot be fetched says so, and reopening the panel asks again", async t => {
  const url = "https://x.example/feed/doctor/retry.ics";
  const page = fixture([Promise.resolve(response(200, POPULATED)), Promise.resolve(response(500)), Promise.resolve(response(200, { url, hint: "etry" }))], t, undefined, true);
  await page.run().ready; await settle();
  const btn = page.cal("mt-cal-open"), panel = page.cal("mt-cal");
  await btn.listeners.get("click")(); await settle();
  assert.match(panel.innerHTML, /No pudimos traer tu dirección/);
  await btn.listeners.get("click")();            // close
  await btn.listeners.get("click")();            // reopen: the loading line, not the old error
  assert.match(panel.innerHTML, /Preparando tu dirección/);
  await settle();
  assert.match(panel.innerHTML, new RegExp(url));
  assert.equal(page.requests(), 3);
});

test("copying the address marks the button without leaving the panel", async t => {
  const url = "https://x.example/feed/doctor/copy.ics";
  let copied = null;
  const page = fixture([Promise.resolve(response(200, POPULATED)), Promise.resolve(response(200, { url }))], t, undefined, true);
  const savedClipboard = Object.getOwnPropertyDescriptor(globalThis.navigator, "clipboard");
  Object.defineProperty(globalThis.navigator, "clipboard", { configurable: true, value: { writeText: t2 => { copied = t2; return Promise.resolve(); } } });
  t.after(() => { if (savedClipboard) Object.defineProperty(globalThis.navigator, "clipboard", savedClipboard); });
  await page.run().ready; await settle();
  const btn = page.cal("mt-cal-open"), panel = page.cal("mt-cal");
  await btn.listeners.get("click")(); await settle();
  click(panel, "[data-cal-copy]"); await settle();
  assert.equal(copied, url);
  assert.match(panel.innerHTML, /Copiada/);
});
