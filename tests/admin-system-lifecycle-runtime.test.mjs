// Runtime ownership regression tests for the direct System controller.  These
// import the complete native controller: no controller function is
// copied or exported by the fixture.  A native replacement must keep the same
// externally observable lifecycle and session-boundary behavior.
import assert from "node:assert/strict";
import test from "node:test";
import { mount } from "../static/platform/admin-system.mjs";

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle(turns = 6) {
  for (let turn = 0; turn < turns; turn += 1) await tick();
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function response({ status = 200, body = {} } = {}) {
  return { status, ok: status >= 200 && status < 300, json: () => Promise.resolve(body) };
}

class Element {
  constructor(id = "") {
    this.id = id;
    this.isConnected = true;
    this.innerHTML = "";
    this.textContent = "";
    this.title = "";
    this.style = {};
    this.attributes = new Map();
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
}

function systemFixture({ fetches, confirmar = () => Promise.resolve(false) }, t) {
  let system = new Element("sec-sistema");
  const badge = new Element("nav-sistema-badge");
  const byId = new Map([["sec-sistema", system], ["nav-sistema-badge", badge]]);
  const document = {
    readyState: "complete",
    head: { appendChild(node) { if (node.id) byId.set(node.id, node); } },
    createElement() { return new Element(); },
    getElementById(id) { return byId.get(id) || null; },
    replaceSystem(next) { system.isConnected = false; system = next; byId.set("sec-sistema", next); },
  };
  let requestCount = 0;
  const previous = Object.fromEntries(["window", "document", "fetch", "__systemConfirm"].map(k => [k, Object.getOwnPropertyDescriptor(globalThis, k)]));
  const handles = [];
  globalThis.__systemConfirm = confirmar;
  globalThis.document = document;
  globalThis.window = { SANA_STATIC: {
    "platform/admin-session.mjs": "data:text/javascript,export const confirmar=(r)=>globalThis.__systemConfirm(r)",
    "platform/i18n.mjs": "data:text/javascript,export const t=(key,vars,fallback)=>fallback",
  } };
  const signals = [];
  globalThis.fetch = (url, options) => {
      assert.equal(url, "/api/admin/system");
      assert.equal(options.credentials, "same-origin");
      signals.push(options.signal);
      requestCount += 1;
      const next = fetches.shift();
      assert.ok(next, "the actual controller made no unexpected status request");
      return next;
  };
  t.after(() => {
    handles.forEach(dispose => dispose());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return {
    document, badge, get system() { return system; },
    requests: () => requestCount,
    signals,
    execute(options) { const dispose = mount(system, options); handles.push(dispose); return dispose; },
  };
}

const FRESH = {
  estado: "ok", ok: 1, total: 1,
  clusters: [{ estado: "warn", titulo: "Fresh status", resumen: "Current result", items: [{
    label: "Mail", valor: "Pending", estado: "warn", campo: "MAILGUN_DOMAIN",
  }] }],
};
const STALE = {
  estado: "error", ok: 0, total: 1,
  clusters: [{ estado: "error", titulo: "Stale status", items: [] }],
};

test("a delayed old System response cannot repaint a replacement #sec-sistema", async (t) => {
  const oldResponse = deferred();
  const page = systemFixture({ fetches: [oldResponse.promise, Promise.resolve(response({ body: FRESH }))] }, t);
  page.execute();
  await settle();
  assert.equal(page.system.innerHTML.includes("Cargando"), true, "the first actual controller owns the old loading shell");

  // Simulate the direct screen being replaced and booted again before its
  // original request settles.  Both runs execute the real controller source.
  const replacement = new Element("sec-sistema");
  page.document.replaceSystem(replacement);
  page.execute();
  await settle();
  assert.match(replacement.innerHTML, /Fresh status/);
  assert.match(replacement.innerHTML, /\/admin\/settings\?field=MAILGUN_DOMAIN/);

  oldResponse.resolve(response({ body: STALE }));
  await settle();
  assert.doesNotMatch(replacement.innerHTML, /Stale status/, "a settled obsolete request must not target the new System host");
  assert.match(replacement.innerHTML, /Fresh status/);
  assert.equal(page.badge.textContent, "100%", "obsolete responses cannot overwrite the current badge");
});

for (const outcome of ["success", "failure", "unauthorized"]) {
  test(`same-host remount aborts and ignores old ${outcome}`, async (t) => {
    const old = deferred();
    let confirmations = 0;
    const page = systemFixture({ fetches: [old.promise, Promise.resolve(response({ body: FRESH }))],
      confirmar: async () => { confirmations++; return true; } }, t);
    page.execute();
    await settle();
    const latest = page.execute();
    await latest.ready;
    assert.equal(page.signals[0].aborted, true);
    if (outcome === "failure") old.reject(new Error("offline"));
    else old.resolve(response(outcome === "unauthorized" ? { status: 401 } : { body: STALE }));
    await settle();
    assert.match(page.system.innerHTML, /Fresh status/);
    assert.equal(page.badge.textContent, "100%");
    assert.equal(confirmations, 0);
    assert.equal(page.requests(), 2);
  });
}

test("dispose before dependencies settle makes no request or DOM mutation", async (t) => {
  const page = systemFixture({ fetches: [] }, t);
  page.system.innerHTML = "preserved";
  const dispose = page.execute();
  dispose(); dispose();
  await dispose.ready;
  assert.equal(page.requests(), 0);
  assert.equal(page.system.innerHTML, "preserved");
});

for (const status of [403, 500, "offline", "invalid-json-shape"]) {
  test(`${status} remains a visible local failure and a fresh mount recovers`, async (t) => {
    const failed = deferred();
    let confirmations = 0;
    const page = systemFixture({ fetches: [failed.promise, Promise.resolve(response({ body: FRESH }))],
      confirmar: async () => { confirmations++; return false; } }, t);
    const first = page.execute();
    await settle();
    if (status === "offline") failed.reject(new Error("offline"));
    else failed.resolve(response(status === "invalid-json-shape" ? { body: {} } : { status }));
    await first.ready;
    assert.match(page.system.innerHTML, /role="alert"/);
    assert.equal(confirmations, status === 403 ? 1 : 0);
    await page.execute().ready;
    assert.match(page.system.innerHTML, /Fresh status/);
    assert.doesNotMatch(page.system.innerHTML, /role="alert"/);
  });
}

test("a confirmed admin-session transition does not paint the System generic error", async (t) => {
  let confirmations = 0;
  const page = systemFixture({
    fetches: [Promise.resolve(response({ status: 401, body: { error: "no_autenticado" } }))],
    confirmar(result) {
      confirmations += 1;
      assert.equal(result.status, 401);
      return Promise.resolve(true);
    },
  }, t);
  page.system.innerHTML = "<p>existing direct screen</p>";
  page.execute();
  await settle();
  assert.equal(confirmations, 1, "the shared session boundary receives the actual 401 response once");
  assert.doesNotMatch(page.system.innerHTML, /No se pudo cargar el estado del sistema/, "a redirecting session transition owns the screen; System must not overwrite it");
});

test("an authorized System response renders cards, badge, and the settings deep link", async (t) => {
  const page = systemFixture({ fetches: [Promise.resolve(response({ body: FRESH }))] }, t);
  page.execute();
  await settle();
  assert.equal(page.requests(), 1, "one direct mount makes one status request");
  assert.match(page.system.innerHTML, /Fresh status/);
  assert.match(page.system.innerHTML, /Pending/);
  assert.match(page.system.innerHTML, /href="\/admin\/settings\?field=MAILGUN_DOMAIN"/);
  assert.equal(page.badge.textContent, "100%");
});
