// Lifecycle ownership tests execute the native Roadmap controller itself
// (static/platform/admin-roadmap.mjs) and the lifecycle script emitted by
// templates/admin/plan.html itself — the only substitution in the executed
// bootstrap is the server-produced static URL. No controller markup builder
// is copied or re-implemented here: every assertion reads what the real
// module wrote into the host, the real listener registries, or the real
// history calls.
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { mount } from "../static/platform/admin-roadmap.mjs";
import { placeHost, withScreenBoot, withSiblings } from "./screen-boot-fixture.mjs";

const planTemplate = await readFile(new URL("../templates/admin/plan.html", import.meta.url), "utf8");
const bootstrapScript = /<script type="module">\n([\s\S]*?)<\/script>/.exec(planTemplate)?.[1];
assert.ok(bootstrapScript, "plan template contains its native module lifecycle script");
const realModuleUrl = new URL("../static/platform/admin-roadmap.mjs", import.meta.url).href;

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 10) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const moduleUrl = code => "data:text/javascript," + encodeURIComponent(code);
const I18N_ES = moduleUrl("export const t=(key,vars,fallback)=>fallback");
const I18N_EN = moduleUrl("export const t=(key,vars,fallback)=>'EN::'+fallback");
let urlCounter = 0;

class Element {
  constructor(id = "") {
    this.id = id;
    this.tagName = "";
    this.isConnected = true;
    this.innerHTML = "";
    this.textContent = "";
    this.style = {};
    this.attrs = new Map();
    this.classes = new Set();
    this.parent = null;
    this.children = [];
  }
  replaceChildren(...children) { this.children = children.flatMap(child => (child && child.fragment) ? child.children : [child]); }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  contains(node) { for (let cur = node; cur; cur = cur.parent) if (cur === this) return true; return false; }
  ancestors() { const out = []; for (let cur = this.parent; cur; cur = cur.parent) out.push(cur); return out; }
  // The browser supplies closest(); the fixture covers the one selector the
  // controller delegates to it (".rm-tabs a" — an anchor inside the tabs).
  closest(selector) {
    for (let cur = this; cur; cur = cur.parent) {
      if (selector === ".rm-tabs a" && cur.tagName === "A" && cur.ancestors().some(a => a.classes.has("rm-tabs"))) return cur;
    }
    return null;
  }
}

function fixture({ i18n = "es", search = "" } = {}, t) {
  const host = new Element("sec-plan");
  const nodes = new Map([["sec-plan", host]]);
  const docListeners = new Map();
  const winListeners = new Map();
  const historyCalls = [];
  const location = { href: "https://example.test/admin/plan", pathname: "/admin/plan", search, hash: "" };
  function applyUrl(url) {
    const s = String(url);
    const q = s.indexOf("?");
    location.pathname = q === -1 ? s : s.slice(0, q);
    location.search = q === -1 ? "" : s.slice(q);
    location.href = "https://example.test" + s;
  }
  if (search) applyUrl("/admin/plan" + search);
  const previous = Object.fromEntries(["window", "document", "location", "history", "__roadmapI18nGate"]
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const staticMap = {};
  let gate = null;
  if (i18n === "es") staticMap["platform/i18n.mjs"] = I18N_ES;
  else if (i18n === "en") staticMap["platform/i18n.mjs"] = I18N_EN;
  else if (i18n === "gated") {
    gate = deferred();
    globalThis.__roadmapI18nGate = gate.promise;
    staticMap["platform/i18n.mjs"] = moduleUrl(
      "export const t=(key,vars,fallback)=>fallback;await globalThis.__roadmapI18nGate;/*" + (++urlCounter) + "*/");
  }
  globalThis.document = {
    readyState: "complete",
    head: { appendChild(node) { if (node.id) nodes.set(node.id, node); } },
    createElement: () => new Element(),
    getElementById: id => nodes.get(id) || null,
    addEventListener(name, fn) { (docListeners.get(name) || docListeners.set(name, []).get(name)).push(fn); },
    removeEventListener(name, fn) {
      const list = docListeners.get(name) || [];
      const at = list.indexOf(fn);
      if (at !== -1) list.splice(at, 1);
    },
  };
  globalThis.window = {
    SANA_STATIC: staticMap,
    addEventListener(name, fn) { (winListeners.get(name) || winListeners.set(name, []).get(name)).push(fn); },
    removeEventListener(name, fn) {
      const list = winListeners.get(name) || [];
      const at = list.indexOf(fn);
      if (at !== -1) list.splice(at, 1);
    },
  };
  globalThis.location = location;
  globalThis.history = {
    pushState(state, title, url) { historyCalls.push({ method: "pushState", url }); applyUrl(url); },
    replaceState(state, title, url) { historyCalls.push({ method: "replaceState", url }); applyUrl(url); },
  };
  const disposes = [];
  t.after(() => {
    disposes.forEach(dispose => dispose());
    delete globalThis.__roadmapI18nGate;
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return {
    host, nodes, location, historyCalls, gate, docListeners, winListeners,
    mount(options) { const dispose = mount(host, options); disposes.push(dispose); return dispose; },
    tab(href) {
      const nav = new Element();
      nav.tagName = "NAV";
      nav.classes.add("rm-tabs");
      nav.parent = host;
      const anchor = new Element();
      anchor.tagName = "A";
      anchor.parent = nav;
      anchor.attrs.set("href", href);
      return anchor;
    },
    foreignTab(href) {
      const other = new Element("sec-otra");
      const anchor = this.tab(href);
      anchor.parent.parent = other;
      return anchor;
    },
    click(target, mods = {}) {
      let prevented = false;
      const event = {
        target,
        button: mods.button || 0,
        metaKey: !!mods.metaKey, ctrlKey: !!mods.ctrlKey, shiftKey: !!mods.shiftKey, altKey: !!mods.altKey,
        preventDefault() { prevented = true; },
      };
      for (const fn of [...(docListeners.get("click") || [])]) fn(event);
      return prevented;
    },
    popstate() { for (const fn of [...(winListeners.get("popstate") || [])]) fn({}); },
  };
}

test("nothing renders until the i18n dependency actually settles", async t => {
  const page = fixture({ i18n: "gated" }, t);
  const dispose = page.mount();
  await settle();
  assert.equal(page.host.innerHTML, "", "a pending dependency leaves the host untouched");
  assert.equal(page.nodes.has("roadmap-css"), false, "the css lands with the first render, not at import");
  page.gate.resolve();
  await dispose.ready;
  assert.match(page.host.innerHTML, /El sistema ya está listo/);
});

test("popstate cannot render or canonicalize while i18n is pending", async t => {
  const page = fixture({ i18n: "gated", search: "?view=technical&extra=1" }, t);
  const dispose = page.mount();
  await settle();
  page.popstate();
  const pendingHtml = page.host.innerHTML;
  const pendingHistory = [...page.historyCalls];
  page.gate.resolve();
  await dispose.ready;
  assert.equal(pendingHtml, "", "history events must not bypass dependency readiness");
  assert.deepEqual(pendingHistory, [], "pending mounts do not own URL mutation");
  assert.match(page.host.innerHTML, /Ya construido/);
});

test("retained tab links cannot mutate history before mount readiness", async t => {
  const page = fixture({ i18n: "gated" }, t);
  page.host.innerHTML = "previous document content";
  const dispose = page.mount();
  const prevented = page.click(page.tab("/admin/plan?view=technical"));
  const pendingHtml = page.host.innerHTML;
  const pendingHistory = [...page.historyCalls];
  page.gate.resolve();
  await dispose.ready;
  assert.equal(prevented, false);
  assert.equal(pendingHtml, "previous document content");
  assert.deepEqual(pendingHistory, []);
});

test("a rejected dependency stays failed after later history events", async t => {
  const page = fixture({ i18n: "gated" }, t);
  const dispose = page.mount();
  page.gate.reject(new Error("unavailable"));
  await dispose.ready;
  const failure = page.host.innerHTML;
  page.location.search = "?view=technical";
  page.popstate();
  assert.equal(page.host.innerHTML, failure);
  assert.equal(page.click(page.tab("/admin/plan")), false);
  assert.deepEqual(page.historyCalls, []);
});

test("content is built after i18n readiness, so translations reach the screen", async t => {
  const page = fixture({ i18n: "en" }, t);
  await page.mount().ready;
  assert.match(page.host.innerHTML, /EN::El sistema ya está listo/);
  assert.match(page.host.innerHTML, /EN::En pocas palabras/);
  assert.doesNotMatch(page.host.innerHTML, />El sistema ya está listo/, "the untranslated fallback never ships when a translation exists");
  const technical = page.tab("/admin/plan?view=technical");
  assert.equal(page.click(technical), true);
  assert.match(page.host.innerHTML, /EN::Ya construido/);
});

test("a rejected i18n import surfaces the failure, never untranslated content", async t => {
  const page = fixture({ i18n: "gated" }, t);
  const dispose = page.mount();
  page.gate.reject(new Error("i18n unavailable"));
  await dispose.ready;
  assert.match(page.host.innerHTML, /role="alert"/);
  assert.match(page.host.innerHTML, /No se pudo cargar esta sección/);
  assert.doesNotMatch(page.host.innerHTML, /rm-flow/, "a failed dependency must not render the roadmap as if it had loaded");
  assert.doesNotMatch(page.host.innerHTML, /El sistema ya está listo/);
});

test("a missing manifest entry rejects through onError instead of translating from a stale global", async t => {
  const page = fixture({ i18n: "missing" }, t);
  globalThis.window.SanaI18n = { t: (key, vars, fallback) => "STALE::" + fallback };
  const errors = [];
  const dispose = page.mount({ onError: error => errors.push(error) });
  await dispose.ready;
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /platform\/i18n\.mjs/);
  assert.equal(page.host.innerHTML, "", "no fallback translation path and no silent success");
  assert.equal(page.nodes.has("roadmap-css"), false);
});

test("dispose before the import settles makes no DOM mutation at all", async t => {
  const page = fixture({ i18n: "gated" }, t);
  page.host.innerHTML = "preserved";
  const dispose = page.mount();
  dispose();
  dispose(); // idempotent
  assert.equal((page.docListeners.get("click") || []).length, 0);
  assert.equal((page.winListeners.get("popstate") || []).length, 0);
  page.gate.resolve();
  await dispose.ready;
  await settle();
  assert.equal(page.host.innerHTML, "preserved", "a late import cannot render a disposed owner");
  assert.equal(page.nodes.has("roadmap-css"), false);
});

test("a same-host remount disposes the previous owner before wiring its own listeners", async t => {
  const page = fixture({ i18n: "gated" }, t);
  const first = page.mount();
  await settle();
  assert.equal((page.docListeners.get("click") || []).length, 1);
  const second = page.mount();
  assert.equal((page.docListeners.get("click") || []).length, 1, "the old owner's document click listener is gone");
  assert.equal((page.winListeners.get("popstate") || []).length, 1);
  page.gate.resolve();
  await first.ready;
  await second.ready;
  assert.match(page.host.innerHTML, /El sistema ya está listo/);
  // One real click must produce exactly one history write: a zombie handler
  // from the first mount would double it.
  assert.equal(page.click(page.tab("/admin/plan?view=technical")), true);
  assert.equal(page.historyCalls.filter(call => call.method === "pushState").length, 1);
});

test("a detached host cannot render: a late import owns nothing", async t => {
  const page = fixture({ i18n: "gated" }, t);
  const replaceHost = new Element("sec-plan");
  page.mount();
  page.host.isConnected = false;
  page.nodes.set("sec-plan", replaceHost);
  page.gate.resolve();
  await settle();
  assert.equal(page.host.innerHTML, "", "the detached old host is never painted");
  assert.equal(replaceHost.innerHTML, "", "the replacement host is not this mount's to paint");
});

test("the canonical technical URL renders the technical tab without rewriting history", async t => {
  const page = fixture({ search: "?view=technical" }, t);
  await page.mount().ready;
  assert.match(page.host.innerHTML, /Ya construido/);
  assert.match(page.host.innerHTML, /Detalle técnico/);
  assert.equal(page.host.innerHTML.includes('aria-selected="true"'), true);
  assert.equal(page.historyCalls.length, 0);
});

test("a non-canonical query is rewritten to the canonical direct URL", async t => {
  const page = fixture({ search: "?view=TECHNICAL&x=1" }, t);
  await page.mount().ready;
  assert.match(page.host.innerHTML, /Qué ya funciona/);
  assert.deepEqual(page.historyCalls, [{ method: "replaceState", url: "/admin/plan" }]);
  assert.equal(page.location.search, "");
});

test("extra parameters around a valid view still canonicalize to the technical URL", async t => {
  const page = fixture({ search: "?view=technical&x=1" }, t);
  await page.mount().ready;
  assert.match(page.host.innerHTML, /Ya construido/);
  assert.deepEqual(page.historyCalls, [{ method: "replaceState", url: "/admin/plan?view=technical" }]);
  assert.equal(page.location.search, "?view=technical");
});

test("tab links push history and re-render; popstate re-routes without new writes", async t => {
  const page = fixture({}, t);
  await page.mount().ready;
  assert.match(page.host.innerHTML, /Qué ya funciona/);
  assert.equal(page.click(page.tab("/admin/plan?view=technical")), true, "the controller prevents default on a plain left click");
  assert.deepEqual(page.historyCalls, [{ method: "pushState", url: "/admin/plan?view=technical" }]);
  assert.equal(page.location.search, "?view=technical");
  assert.match(page.host.innerHTML, /Ya construido/);
  // Browser Back restores the simple URL, then fires popstate.
  page.location.search = "";
  page.location.pathname = "/admin/plan";
  page.popstate();
  assert.match(page.host.innerHTML, /Qué ya funciona/);
  assert.equal(page.historyCalls.length, 1, "popstate routing never writes history");
});

for (const mods of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
  test("modifier and non-left clicks keep native navigation: " + JSON.stringify(mods), async t => {
    const page = fixture({}, t);
    await page.mount().ready;
    assert.equal(page.click(page.tab("/admin/plan?view=technical"), mods), false);
    assert.equal(page.historyCalls.length, 0);
    assert.match(page.host.innerHTML, /Qué ya funciona/, "the view is unchanged");
  });
}

test("a roadmap link outside this host keeps its default navigation", async t => {
  const page = fixture({}, t);
  await page.mount().ready;
  const foreign = page.foreignTab("/admin/plan?view=technical");
  assert.equal(page.host.contains(foreign), false);
  assert.equal(page.click(foreign), false);
  assert.equal(page.historyCalls.length, 0);
  assert.match(page.host.innerHTML, /Qué ya funciona/);
});

test("teardown removes every global listener; late popstate and clicks render nothing", async t => {
  const page = fixture({}, t);
  const dispose = page.mount();
  await dispose.ready;
  const painted = page.host.innerHTML;
  dispose();
  assert.equal((page.docListeners.get("click") || []).length, 0);
  assert.equal((page.winListeners.get("popstate") || []).length, 0);
  page.location.search = "?view=technical";
  page.popstate();
  assert.equal(page.click(page.tab("/admin/plan?view=technical")), false);
  await settle();
  assert.equal(page.host.innerHTML, painted, "the disposed owner no longer repaints");
});

test("opt-in HSI visibility: off leaves no HSI mention on either tab", async t => {
  const page = fixture({}, t);
  await page.mount({ emrSync: false }).ready;
  assert.doesNotMatch(page.host.innerHTML, /HSI/i);
  assert.match(page.host.innerHTML, /7 de 14 condiciones de lanzamiento ya están completas/);
  assert.match(page.host.innerHTML, /La dirección oficial y la fecha de lanzamiento/);
  assert.equal(page.click(page.tab("/admin/plan?view=technical")), true);
  assert.doesNotMatch(page.host.innerHTML, /HSI/i);
  assert.match(page.host.innerHTML, /Las dos cosas son suyas/, "the no-EMR week list is the one rendered");
});

test("opt-in HSI visibility: on surfaces the HSI conditions on both tabs", async t => {
  const page = fixture({}, t);
  await page.mount({ emrSync: true }).ready;
  assert.match(page.host.innerHTML, /Conexión con el HSI del hospital/);
  assert.match(page.host.innerHTML, /8 de 15 condiciones de lanzamiento ya están completas/);
  assert.match(page.host.innerHTML, /está preparada y probada localmente/);
  assert.equal(page.click(page.tab("/admin/plan?view=technical")), true);
  assert.match(page.host.innerHTML, /Acceso al HSI productivo/);
  assert.match(page.host.innerHTML, /Push a HSI \+ resolutor de conflictos/);
  assert.match(page.host.innerHTML, /Las tres cosas son suyas/);
});

test("importing the module alone touches no DOM: bootstrap lives in the template", async t => {
  const page = fixture({}, t);
  await import("../static/platform/admin-roadmap.mjs?bootstrap-probe=" + (++urlCounter));
  await settle();
  assert.equal(page.host.innerHTML, "");
  assert.equal(page.nodes.has("roadmap-css"), false);
  assert.equal((page.docListeners.get("click") || []).length, 0);
  assert.equal((page.winListeners.get("popstate") || []).length, 0);
});

test("mount without a host fails loudly", () => {
  assert.throws(() => mount(null), /Roadmap host is required/);
});

// ---- the real template bootstrap (templates/admin/plan.html) ----------------
//
// The recovery template DOM is an explicit fixture; everything else — the
// generation guard, the watchdog timer, the mount call and the pagehide /
// pageshow wiring — is the script the server actually emits.

// The shared loader puts its late copy right before the host.
const PlacedElement = withSiblings(Element);

function bootstrapPage(t, { controllerUrl = realModuleUrl, i18n = "es", emrFlag = false } = {}) {
  const host = new PlacedElement("sec-plan");
  const placed = placeHost(PlacedElement, host);
  const nodes = new Map([["sec-plan", host]]);
  const docListeners = new Map();
  const winListeners = new Map();
  const historyCalls = [];
  const timers = new Map();
  let nextTimer = 1;
  let reloads = 0;
  const location = { href: "https://example.test/admin/plan", pathname: "/admin/plan", search: "", hash: "", reload() { reloads += 1; } };
  function applyUrl(url) {
    const s = String(url);
    const q = s.indexOf("?");
    location.pathname = q === -1 ? s : s.slice(0, q);
    location.search = q === -1 ? "" : s.slice(q);
  }
  const retry = new Element();
  retry.setAttribute("data-retry", "");
  const recoveryClone = {
    fragment: true,
    children: [Object.assign(new Element(), { className: "adm-empty", role: "alert" }), retry],
    querySelector(selector) { return selector === "[data-retry]" ? retry : null; },
  };
  nodes.set("plan-load-recovery", { content: { cloneNode() { return recoveryClone; } } });
  const staticMap = {};
  let gate = null;
  if (i18n === "es") staticMap["platform/i18n.mjs"] = I18N_ES;
  else if (i18n === "gated") {
    gate = deferred();
    globalThis.__roadmapI18nGate = gate.promise;
    staticMap["platform/i18n.mjs"] = moduleUrl(
      "export const t=(key,vars,fallback)=>fallback;await globalThis.__roadmapI18nGate;/*" + (++urlCounter) + "*/");
  }
  const previous = Object.fromEntries(["window", "document", "location", "history", "setTimeout", "clearTimeout", "__roadmapI18nGate", "__roadmapModuleUrl__"]
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  globalThis.document = {
    readyState: "complete",
    head: { appendChild(node) { if (node.id) nodes.set(node.id, node); } },
    createElement: () => new PlacedElement(),
    getElementById: id => nodes.get(id) || null,
    addEventListener(name, fn) { (docListeners.get(name) || docListeners.set(name, []).get(name)).push(fn); },
    removeEventListener(name, fn) {
      const list = docListeners.get(name) || [];
      const at = list.indexOf(fn);
      if (at !== -1) list.splice(at, 1);
    },
  };
  globalThis.window = {
    SANA_STATIC: staticMap,
    SANA_EMR_SYNC_ENABLED: emrFlag,
    addEventListener(name, fn) { (winListeners.get(name) || winListeners.set(name, []).get(name)).push(fn); },
    removeEventListener(name, fn) {
      const list = winListeners.get(name) || [];
      const at = list.indexOf(fn);
      if (at !== -1) list.splice(at, 1);
    },
  };
  globalThis.location = location;
  globalThis.history = {
    pushState(state, title, url) { historyCalls.push({ method: "pushState", url }); applyUrl(url); },
    replaceState(state, title, url) { historyCalls.push({ method: "replaceState", url }); applyUrl(url); },
  };
  globalThis.setTimeout = (callback, delay) => { const id = nextTimer++; timers.set(id, { callback, delay }); return id; };
  globalThis.clearTimeout = id => { timers.delete(id); };
  globalThis.__roadmapModuleUrl__ = controllerUrl;
  t.after(() => {
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return {
    host, nodes, timers, location, docListeners, winListeners, gate,
    reloads: () => reloads,
    retry,
    slowCopy: placed.slowCopy,
    async execute() {
      const script = withScreenBoot(bootstrapScript)
        .replace('"{{ static(\'platform/admin-roadmap.mjs\') }}"', "globalThis.__roadmapModuleUrl__");
      assert.doesNotMatch(script, /\{\{|\}\}/, "all server expressions in the executed bootstrap were substituted");
      await import(moduleUrl(script + "\n// bootstrap run: " + (++urlCounter)));
      await settle();
    },
    fireTimeout(delay = 8000) {
      const entry = [...timers.entries()].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, "an active " + delay + "ms lifecycle timeout exists");
      timers.delete(entry[0]);
      entry[1].callback();
    },
    fire(type, event = {}) { for (const fn of [...(winListeners.get(type) || [])]) fn(event); },
  };
}

test("the real bootstrap mounts the real module, flag included, then clears its watchdog", async t => {
  const page = bootstrapPage(t, { emrFlag: true });
  await page.execute();
  assert.match(page.host.innerHTML, /El sistema ya está listo/);
  assert.match(page.host.innerHTML, /Conexión con el HSI del hospital/, "the EMR flag set by the server reaches the mount options");
  assert.equal(page.host.innerHTML.includes("8 de 15 condiciones"), true);
  assert.equal((page.docListeners.get("click") || []).length, 1);
  assert.equal(page.timers.size, 0, "a successful ready clears the eight-second watchdog");
});

test("a failed module import shows the recovery line and a working reload", async t => {
  const page = bootstrapPage(t, { controllerUrl: moduleUrl("throw new Error('roadmap module unavailable')") });
  await page.execute();
  assert.equal(page.host.children.length, 2, "the recovery template replaces the screen");
  assert.equal(page.host.children[0].role, "alert");
  assert.equal(typeof page.retry.onclick, "function");
  page.retry.onclick();
  assert.equal(page.reloads(), 1, "the retry action reloads the document");
  assert.equal(page.timers.size, 0);
  assert.equal((page.docListeners.get("click") || []).length, 0);
});

test("at eight seconds the copy shows beside the screen and the late load still mounts", async t => {
  const page = bootstrapPage(t, { i18n: "gated" });
  await page.execute();
  assert.equal(page.timers.size, 1, "the deadline stays armed while readiness is pending");
  page.fireTimeout();
  assert.ok(page.slowCopy(), "the late copy is on screen");
  assert.equal(page.slowCopy().children[0].role, "alert");
  assert.equal(page.host.children.includes(page.retry), false, "the copy never replaces the screen");
  page.gate.resolve();
  await settle();
  assert.equal((page.docListeners.get("click") || []).length, 1, "the late answer mounts the screen");
  assert.match(page.host.innerHTML, /El sistema ya está listo/);
  assert.equal(page.slowCopy(), null, "and removes the copy");
});

test("pagehide disposes the mount and a stale import completion cannot remount", async t => {
  const page = bootstrapPage(t, { i18n: "gated" });
  await page.execute();
  page.fire("pagehide");
  assert.equal(page.timers.size, 0, "pagehide clears the watchdog");
  page.gate.resolve();
  await settle();
  assert.equal((page.docListeners.get("click") || []).length, 0, "the obsolete import cannot mount after pagehide");
  assert.equal(page.host.innerHTML, "");
  assert.equal(page.host.children.length, 0);
});

test("a persisted pageshow remounts the controller on the same host", async t => {
  const page = bootstrapPage(t, {});
  await page.execute();
  assert.match(page.host.innerHTML, /El sistema ya está listo/);
  assert.equal((page.docListeners.get("click") || []).length, 1);
  page.fire("pagehide");
  assert.equal((page.docListeners.get("click") || []).length, 0);
  page.fire("pageshow", { persisted: true });
  await settle();
  assert.equal((page.docListeners.get("click") || []).length, 1, "the remount owns exactly one click listener again");
  assert.match(page.host.innerHTML, /El sistema ya está listo/);
  assert.equal(page.timers.size, 0);
});
