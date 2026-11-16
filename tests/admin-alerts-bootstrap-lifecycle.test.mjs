// Bootstrap-lifecycle tests execute the ACTUAL inline module bootstrap that
// templates/admin/base.html emits for the shared triage-bell shell resource
// (static/platform/admin-alerts.mjs) — the same convention as the Account
// (tests/account-bootstrap-lifecycle.test.mjs) and Plan
// (tests/admin-roadmap-lifecycle-runtime.test.mjs) bootstrap suites: the script
// is read out of the real template, the ONLY substitutions are the two server
// expressions (screen_rol and the fingerprinted static URL), and it runs as a
// real module through a unique data: URL. Mocks exist ONLY at platform
// boundaries — DOM, timers, fetch, and the dynamic-import target — no
// production logic is copied or re-implemented here.
//
// Fixture honesty notes:
//  - The <template id="admin-alerts-recovery"> DOM is an explicit fixture with
//    localized strings; it is NOT evidence that Jinja translated anything. The
//    source pins at the top only prove the production template ships the real
//    hooks (the template id, role="alert", data-alerts-reload, the t() call)
//    this fixture stands in for.
//  - The mount-recording stub's dispose is deliberately NOT internally
//    idempotent: these tests pin the SHELL's own hygiene (stop() must call the
//    owner at most once per generation), which the real module's internal
//    guard would otherwise mask.
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

const template = await readFile(new URL("../templates/admin/base.html", import.meta.url), "utf8");
const bootstrapScript = /<script type="module">\n([\s\S]*?)<\/script>/.exec(template)?.[1];
assert.ok(bootstrapScript, "the shared admin base template carries its native alerts bootstrap script");
assert.match(template, /<template id="admin-alerts-recovery">/, "production ships the recovery template the bootstrap clones");
assert.match(template, /<span role="alert">/, "the recovery root is a real alert region");
assert.match(template, /data-alerts-reload/, "the recovery carries the reload hook the script wires");
assert.match(template, /t\('admin\.system\.alerts\.load_error'/, "the recovery copy is server-localized, not frozen in the script");

const realModuleUrl = new URL("../static/platform/admin-alerts.mjs", import.meta.url).href;

const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle(turns = 12) { for (let i = 0; i < turns; i += 1) await tick(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const moduleUrl = code => "data:text/javascript," + encodeURIComponent(code);
// Node caches modules by URL: every generated module mints a unique one.
let urlCounter = 0;
const cleanupStacks = new WeakMap();

// Dependency doubles for the REAL module (same boundary doubles as
// tests/admin-alerts-native-lifecycle.test.mjs): i18n interpolates {n} like the
// real catalog; format/dates prefix outputs so rendered text proves the
// canonical module was used, never a reimplementation.
const I18N_ES = moduleUrl(
  "export const t=(key,vars,fallback)=>{const s=String(fallback||key);"
  + "return (vars&&vars.n!==undefined)?s.split(\"{n}\").join(String(vars.n)):s}");
const FORMAT = moduleUrl(
  "export const telWa=v=>v?\"WA:\"+v:\"\";"
  + "export const telE164=v=>v?\"E164:\"+v:\"\";"
  + "export const nombre=v=>v?\"NM:\"+v:\"\"");
const DATES = moduleUrl("export const relativo=()=>'hace un rato'");
const SESSION = moduleUrl("export const confirmar=() => Promise.resolve(false)");

class El {
  constructor(id = "") {
    this.id = id;
    this.tagName = "";
    this.className = "";
    this.isConnected = true;
    this.hidden = false;
    this.disabled = false;
    this.type = "";
    this.title = "";
    this.innerHTML = "";
    this.textContent = "";
    this.attrs = new Map();
    this.children = [];
    this.parent = null;
    this.listeners = new Map();
  }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  appendChild(child) {
    if (child && child.fragment) {
      for (const node of child.children) { node.parent = this; this.children.push(node); }
      return child.children[0] || null;
    }
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (this.parent) {
      const at = this.parent.children.indexOf(this);
      if (at !== -1) this.parent.children.splice(at, 1);
      this.parent = null;
    }
  }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  querySelectorAll(selector) {
    // The browser supplies selector matching; the fixture covers the two
    // selector shapes the real code delegates: ".class" and "[attr]".
    const out = [];
    if (typeof selector !== "string") return out;
    const byClass = selector.charCodeAt(0) === 46 /* . */ ? selector.slice(1) : null;
    const byAttr = selector.charCodeAt(0) === 91 /* [ */ && selector.endsWith("]") ? selector.slice(1, -1) : null;
    if (!byClass && !byAttr) return out;
    const visit = node => {
      for (const child of node.children) {
        if (byClass && String(child.className).split(/\s+/).includes(byClass)) out.push(child);
        else if (byAttr && child.attrs.has(byAttr)) out.push(child);
        visit(child);
      }
    };
    visit(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const response = (status = 200, body = {}) =>
  ({ status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) });

// A mount-recording stand-in for platform/admin-alerts.mjs AT THE IMPORT
// BOUNDARY. It paints a real node into the host through the very root the
// bootstrap hands it, records the role it received, and returns a dispose
// WITHOUT an internal idempotency guard (see the fixture-honesty note above).
// The gated variant holds its own import open until the fixture resolves
// __alertsStub.gate — but records and paints exactly like the plain one, so
// an illicit stale mount stays observable instead of vanishing into a throw.
function stubUrl({ gated = false } = {}) {
  const hold = gated ? "await globalThis.__alertsStub.gate;\n" : "";
  return moduleUrl(
    "globalThis.__alertsStub.imports += 1;/*" + (++urlCounter) + "*/\n"
    + hold
    + "export function mountAlerts(options) {\n"
    + "  globalThis.__alertsStub.mounts.push({ role: options.role });\n"
    + "  var host = options.root.querySelector('.adm-h1row');\n"
    + "  var node = options.root.createElement('div');\n"
    + "  node.className = 'alert-bell-wrap';\n"
    + "  node.textContent = 'stub alert bell';\n"
    + "  host.appendChild(node);\n"
    + "  function dispose() {\n"
    + "    globalThis.__alertsStub.disposals += 1;\n"
    + "    if (node.parent) node.remove();\n"
    + "  }\n"
    + "  dispose.ready = globalThis.__alertsStub.ready;\n"
    + "  return dispose;\n"
    + "}");
}

// The server-localized recovery DOM the <template> stands for. cloneNode()
// mints FRESH nodes per clone, exactly like the real DOM: each failure gets
// its own span and its own reload button.
function recoveryTemplate(message, reloadLabel) {
  const clones = [];
  return {
    clones,
    content: {
      cloneNode() {
        const text = new El("span");
        text.textContent = message;
        const button = new El("button");
        button.setAttribute("type", "button");
        button.setAttribute("data-alerts-reload", "");
        button.className = "btn btn-quiet";
        button.textContent = reloadLabel;
        const span = new El("span");
        span.setAttribute("role", "alert");
        span.appendChild(text);
        span.appendChild(button);
        clones.push({ span, button });
        return { fragment: true, firstElementChild: span, children: [span] };
      },
    },
  };
}

function bootstrapPage(t, { role = "admin", controllerUrl, message = "No se pudieron cargar las alertas.",
  reloadLabel = "Recargar", withHost = true } = {}) {
  const host = new El("adm-h1row");
  host.className = "adm-h1row";
  const recovery = recoveryTemplate(message, reloadLabel);
  const nodes = new Map([["admin-alerts-recovery", recovery]]);
  const docListeners = new Map();
  const winListeners = new Map();
  const timeouts = new Map();
  const intervals = new Map();
  const requests = [];
  const strays = [];
  const toasts = [];
  let timerSeq = 1;
  let reloads = 0;
  let fetchQueue = [];

  const staticMap = {
    "platform/i18n.mjs": I18N_ES,
    "platform/format.mjs": FORMAT,
    "platform/dates.mjs": DATES,
    "platform/admin-session.mjs": SESSION,
  };

  const previous = Object.fromEntries(
    ["window", "document", "location", "setTimeout", "clearTimeout", "setInterval", "clearInterval",
      "fetch", "addEventListener", "removeEventListener", "__alertsModuleUrl__", "__alertsStub"]
      .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));

  globalThis.document = {
    head: { appendChild(node) { if (node.id) nodes.set(node.id, node); } },
    createElement: tag => new El(tag),
    getElementById: id => nodes.get(id) || null,
    querySelector: selector => (withHost && selector === ".adm-h1row" ? host : null),
    querySelectorAll: () => [],
    addEventListener(name, fn) {
      const list = docListeners.get(name) || [];
      list.push(fn); docListeners.set(name, list);
    },
    removeEventListener(name, fn) {
      const list = docListeners.get(name) || [];
      const at = list.indexOf(fn);
      if (at !== -1) list.splice(at, 1);
    },
  };
  globalThis.window = {
    SANA_STATIC: staticMap,
    toast: m => toasts.push(String(m)),
  };
  globalThis.location = { href: "https://example.test/admin/solicitudes", reload() { reloads += 1; } };
  globalThis.setTimeout = (callback, delay) => { const id = timerSeq++; timeouts.set(id, { callback, delay }); return id; };
  globalThis.clearTimeout = id => { timeouts.delete(id); };
  globalThis.setInterval = (fn, delay) => { const id = timerSeq++; intervals.set(id, { fn, delay }); return id; };
  globalThis.clearInterval = id => { intervals.delete(id); };
  globalThis.fetch = (url, options = {}) => {
    requests.push({ url: String(url), options });
    const next = fetchQueue.shift();
    if (!next) {
      strays.push(String(url));
      return Promise.reject(new Error("unexpected alerts request: " + url));
    }
    return Promise.resolve(typeof next === "function" ? next() : next);
  };
  // The bootstrap's bare addEventListener IS window.addEventListener in the
  // browser; Node's globalThis has none, so the fixture supplies the binding.
  globalThis.addEventListener = (name, fn) => {
    const list = winListeners.get(name) || [];
    list.push(fn); winListeners.set(name, list);
  };
  globalThis.removeEventListener = (name, fn) => {
    const list = winListeners.get(name) || [];
    const at = list.indexOf(fn);
    if (at !== -1) list.splice(at, 1);
  };
  globalThis.__alertsStub = { imports: 0, mounts: [], disposals: 0, gate: null, ready: Promise.resolve() };

  if (!cleanupStacks.has(t)) {
    cleanupStacks.set(t, []);
    t.after(() => {
      for (const cleanup of cleanupStacks.get(t).reverse()) cleanup();
      cleanupStacks.delete(t);
    });
  }
  cleanupStacks.get(t).push(() => {
    // Nested fixtures restore browser globals in reverse construction order.
    // Dispose first while this fixture still owns the ambient document.
    for (const fn of [...(winListeners.get('pagehide') || [])]) fn({});
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });

  const findIn = (node, id) => {
    for (const child of node.children) {
      if (child.id === id) return child;
      const found = findIn(child, id);
      if (found) return found;
    }
    return null;
  };

  return {
    host, nodes, timeouts, intervals, docListeners, winListeners, requests, strays, toasts,
    recovery, stub: globalThis.__alertsStub,
    reloads: () => reloads,
    enqueue(...next) { fetchQueue = fetchQueue.concat(next); },
    bell: () => findIn(host, "alert-bell"),
    badge: () => findIn(host, "alert-bell-badge"),
    async execute(turns = 24) {
      globalThis.__alertsModuleUrl__ = controllerUrl;
      const script = bootstrapScript
        .replace("{{ screen_rol()|tojson }}", JSON.stringify(role))
        .replace('"{{ static(\'platform/admin-alerts.mjs\') }}"', "globalThis.__alertsModuleUrl__");
      assert.doesNotMatch(script, /\{\{|\}\}/, "every server expression in the executed bootstrap was substituted");
      await import(moduleUrl(script + "\n// alerts bootstrap run: " + (++urlCounter)));
      await settle(turns);
    },
    fireTimeout(delay = 8000) {
      const entry = [...timeouts.entries()].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, "an active " + delay + "ms lifecycle timeout exists");
      timeouts.delete(entry[0]);
      entry[1].callback();
    },
    fire(type, event = {}) { for (const fn of [...(winListeners.get(type) || [])]) fn(event); },
  };
}

// ---- server role eligibility ------------------------------------------------------

test("server roles other than admin/medico never import, schedule or paint", async t => {
  for (const role of ["admisionista", "reportes", "marca", "operador", ""]) {
    const page = bootstrapPage(t, { role, controllerUrl: stubUrl() });
    await page.execute();
    assert.equal(page.stub.imports, 0, JSON.stringify(role) + " never imports the alerts module");
    assert.equal(page.timeouts.size, 0, JSON.stringify(role) + " arms no watchdog");
    assert.equal(page.host.children.length, 0, JSON.stringify(role) + " paints nothing");
    assert.equal(page.intervals.size, 0, JSON.stringify(role) + " arms no interval");
    assert.equal(page.requests.length, 0);
  }
});

test("a document without .adm-h1row starts nothing at all", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl(), withHost: false });
  await page.execute();
  assert.equal(page.stub.imports, 0, "without the header row there is no import");
  assert.equal(page.timeouts.size, 0);
});

test("the server-verified role reaches mountAlerts for admin and medico", async t => {
  for (const role of ["admin", "medico"]) {
    const page = bootstrapPage(t, { role, controllerUrl: stubUrl() });
    await page.execute();
    assert.equal(page.stub.imports, 1, role + " imports the module exactly once");
    assert.deepEqual(page.stub.mounts.map(mounted => mounted.role), [role],
      "mountAlerts receives the role the server rendered into the script");
    assert.equal(page.host.children.length, 1, role + " actually mounted into the header");
    assert.equal(page.host.children[0].className, "alert-bell-wrap");
    assert.equal(page.host.children[0].textContent, "stub alert bell");
  }
});

// ---- successful mount.ready ---------------------------------------------------------

test("the real module mounts through the real bootstrap: bell, badge, poll, watchdog cleared", async t => {
  const page = bootstrapPage(t, { role: "admin", controllerUrl: realModuleUrl });
  page.enqueue(response(200, { count: 3 }));
  await page.execute(30); // Increased from default 24 ticks for catalog expansion (2026-09-10 calendar mobile layout fix adds date.day_two_letter.* keys)
  const bell = page.bell();
  const badge = page.badge();
  assert.ok(bell, "the real bell landed in .adm-h1row");
  assert.ok(badge, "the real badge landed with it");
  assert.equal(badge.textContent, "3");
  assert.equal(bell.getAttribute("aria-label"), "3 alertas de triage rojo sin ver");
  assert.equal(page.requests.length, 1);
  assert.equal(page.requests[0].url, "/api/admin/intake/alerts/count");
  assert.equal(page.requests[0].options.credentials, "same-origin");
  assert.equal(page.nodes.has("alert-bell-css"), true, "the module injected its stylesheet once");
  assert.equal((page.docListeners.get("click") || []).length, 2, "outside-close + ack delegation are wired");
  assert.deepEqual([...page.intervals.values()].map(timer => timer.delay), [60000], "the 60s poll cadence is armed");
  assert.equal(page.timeouts.size, 0, "a successful mount.ready clears the eight-second watchdog");
});

test("mount.ready pending keeps the watchdog armed; resolving it clears the deadline", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl() });
  const ready = deferred();
  page.stub.ready = ready.promise;
  await page.execute();
  assert.equal(page.stub.mounts.length, 1, "the mount already happened");
  assert.equal(page.host.children.length, 1, "the mounted owner painted its node");
  assert.deepEqual([...page.timeouts.values()].map(timer => timer.delay), [8000],
    "the eight-second deadline stays armed while readiness is pending");
  ready.resolve();
  await settle();
  assert.equal(page.timeouts.size, 0, "a resolved owner.ready clears the deadline");
  assert.equal(page.stub.disposals, 0, "readiness never disposes the live owner");
});

// ---- rejected import and the localized recovery -------------------------------------

test("a rejected import shows the server-localized recovery, fresh-cloned from the template", async t => {
  const page = bootstrapPage(t, {
    controllerUrl: moduleUrl("throw new Error('alerts module unavailable')/*" + (++urlCounter) + "*/"),
    message: "No se pudieron cargar las alertas.",
    reloadLabel: "Recargar",
  });
  await page.execute();
  assert.equal(page.host.children.length, 1, "the recovery replaces the widget area in the header");
  const [span] = page.host.children;
  assert.equal(span.getAttribute("role"), "alert", "the recovery root is a real alert region");
  assert.equal(span.children[0].textContent, "No se pudieron cargar las alertas.", "the localized message is the cloned text");
  const button = span.children[1];
  assert.equal(button.getAttribute("data-alerts-reload"), "", "the cloned button carries the reload hook");
  assert.equal(button.getAttribute("type"), "button");
  assert.equal(button.textContent, "Recargar", "the localized reload label is the cloned text");
  assert.equal(page.recovery.clones.length, 1, "exactly one fresh clone was taken from the template");
  assert.equal(page.recovery.clones[0].span, span, "what landed in the header IS the cloned node");
  assert.equal(page.timeouts.size, 0, "the failure path clears the watchdog");
  assert.equal(page.stub.mounts.length, 0, "nothing mounted for a failed import");
  assert.equal(page.strays.length, 0);
});

test("a rejected mount.ready disposes the mounted owner and exposes usable recovery", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl() });
  const ready = deferred();
  page.stub.ready = ready.promise;
  await page.execute();
  assert.equal(page.stub.mounts.length, 1);
  assert.equal(page.host.children[0].className, 'alert-bell-wrap');
  ready.reject(new Error('required dependency unavailable'));
  await settle();
  assert.equal(page.stub.disposals, 1);
  assert.equal(page.host.children.length, 1);
  assert.equal(page.host.children[0].getAttribute('role'), 'alert');
  assert.equal(page.timeouts.size, 0);
  page.recovery.clones[0].button.listeners.get('click')();
  assert.equal(page.reloads(), 1);
});

test("the recovery reload button reloads the document", async t => {
  const page = bootstrapPage(t, { controllerUrl: moduleUrl("throw new Error('gone')/*" + (++urlCounter) + "*/") });
  await page.execute();
  const button = page.recovery.clones[0].button;
  const click = button.listeners.get("click");
  assert.equal(typeof click, "function", "the reload action is wired on the cloned button");
  click();
  assert.equal(page.reloads(), 1, "the reload action reloads the document exactly once per click");
});

// ---- the eight-second deadline ------------------------------------------------------

test("an import held past the eight-second deadline recovers and blocks the late mount", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl({ gated: true }) });
  const gate = deferred();
  page.stub.gate = gate.promise;
  await page.execute();
  assert.equal(page.stub.imports, 1, "the import was issued");
  assert.deepEqual([...page.timeouts.values()].map(timer => timer.delay), [8000],
    "the deadline is armed at eight seconds while the import is pending");
  page.fireTimeout(8000);
  assert.equal(page.host.children.length, 1, "the deadline shows the recovery");
  assert.equal(page.host.children[0].getAttribute("role"), "alert");
  gate.resolve();
  await settle();
  assert.equal(page.stub.mounts.length, 0, "a late import completion cannot mount after the deadline fired");
  assert.equal(page.host.children.length, 1, "the late success does not remove the recovery");
  assert.equal(page.timeouts.size, 0);
  page.fire("pagehide");
  assert.equal(page.host.children.length, 0, "pagehide removes the recovery clone from the header");
});

// ---- pagehide before the late import / readiness ------------------------------------

test("pagehide before a held import settles cancels the watchdog and forbids the mount", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl({ gated: true }) });
  const gate = deferred();
  page.stub.gate = gate.promise;
  await page.execute();
  assert.equal(page.timeouts.size, 1, "the watchdog was armed");
  page.fire("pagehide");
  assert.equal(page.timeouts.size, 0, "pagehide clears the eight-second watchdog");
  gate.resolve();
  await settle();
  assert.equal(page.stub.mounts.length, 0, "the stale import cannot mount after pagehide");
  assert.equal(page.host.children.length, 0, "the stale import paints nothing");
});

test("pagehide between mount and readiness disposes once; the late ready adds nothing", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl() });
  const ready = deferred();
  page.stub.ready = ready.promise;
  await page.execute();
  assert.equal(page.stub.mounts.length, 1, "the mount happened");
  assert.equal(page.host.children.length, 1, "the owner had painted its node");
  page.fire("pagehide");
  assert.equal(page.stub.disposals, 1, "pagehide disposes the owner");
  assert.equal(page.host.children.length, 0, "the disposed owner's markup is gone");
  assert.equal(page.timeouts.size, 0);
  ready.resolve();
  await settle();
  assert.equal(page.stub.disposals, 1, "the late ready resolution never disposes again");
  assert.equal(page.host.children.length, 0, "nothing repaints for a dead generation");
});

// ---- persisted pageshow: fresh ownership ---------------------------------------------

test("a persisted pageshow remounts fresh: old owner gone, new owner owns the header", async t => {
  const page = bootstrapPage(t, { role: "medico", controllerUrl: realModuleUrl });
  page.enqueue(response(200, { count: 2 }));
  await page.execute();
  assert.ok(page.bell(), "the first mount painted the bell");
  assert.equal(page.badge().textContent, "2");
  page.fire("pagehide");
  assert.equal(page.host.querySelector(".alert-bell-wrap"), null, "pagehide removed the widget markup");
  assert.equal(page.intervals.size, 0, "pagehide cleared the poll interval");
  assert.equal((page.docListeners.get("click") || []).length, 0, "pagehide dropped the document listeners");
  page.fire("pageshow", { persisted: false });
  await settle();
  assert.equal(page.host.querySelector(".alert-bell-wrap"), null, "a non-persisted pageshow never remounts");
  page.enqueue(response(200, { count: 5 }));
  page.fire("pageshow", { persisted: true });
  await settle();
  assert.equal(page.host.querySelectorAll(".alert-bell-wrap").length, 1, "exactly one wrap after the remount");
  assert.equal(page.badge().textContent, "5", "the fresh owner completed its own poll end to end");
  assert.equal(page.timeouts.size, 0, "the fresh owner's ready cleared its watchdog again");
  assert.deepEqual([...page.intervals.values()].map(timer => timer.delay), [60000], "exactly one poll cadence survives");
  assert.equal((page.docListeners.get("click") || []).length, 2, "the fresh owner owns its listeners");
  assert.equal(page.recovery.clones.length, 0, "no recovery was ever needed");
  assert.equal(page.strays.length, 0);
});

test("a persisted pageshow mounts a fresh owner exactly once per generation", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl() });
  await page.execute();
  assert.deepEqual(page.stub.mounts.map(mounted => mounted.role), ["admin"]);
  page.fire("pagehide");
  page.fire("pageshow", { persisted: true });
  await settle();
  assert.deepEqual(page.stub.mounts.map(mounted => mounted.role), ["admin", "admin"],
    "the remount is a fresh mountAlerts call on the live generation");
  assert.equal(page.stub.disposals, 1, "only the pagehide disposed; the remount's own stop() adds none");
  assert.equal(page.host.children.length, 1, "one painted node again");
  assert.equal(page.timeouts.size, 0, "the second ready resolved and cleared the new watchdog");
});

// ---- idempotent disposal -------------------------------------------------------------

test("disposal is idempotent: repeated pagehide disposes the owner exactly once", async t => {
  const page = bootstrapPage(t, { controllerUrl: stubUrl() });
  await page.execute();
  assert.equal(page.stub.mounts.length, 1);
  page.fire("pagehide");
  page.fire("pagehide");
  page.fire("pagehide");
  assert.equal(page.stub.disposals, 1,
    "the shell's stop() calls the owner at most once (the stub's dispose carries no internal guard)");
  assert.equal(page.host.children.length, 0);
  assert.equal(page.timeouts.size, 0);
});
