import test from "node:test";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MODULE_URL = pathToFileURL(join(ROOT, "static/platform/admin-preferences.mjs")).href;
let caseId = 0;

async function freshModule({ fetchImpl, timeoutImpl, clearImpl, toast } = {}) {
  const previous = {
    window: globalThis.window,
    fetch: globalThis.fetch,
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  };
  const listeners = {};
  globalThis.window = {
    addEventListener(name, handler) { listeners[name] = handler; },
  };
  if (toast) globalThis.window.toast = toast;
  if (fetchImpl) globalThis.fetch = fetchImpl;
  if (timeoutImpl) globalThis.setTimeout = timeoutImpl;
  if (clearImpl) globalThis.clearTimeout = clearImpl;
  const module = await import(`${MODULE_URL}?case=${++caseId}`);
  return {
    module,
    listeners,
    restore() {
      Object.assign(globalThis, previous);
    },
  };
}

test("load hydrates once, preserves falsey values, and force refreshes", async () => {
  const calls = [];
  const responses = [{ zero: 0, disabled: false, empty: "" }, { later: 2 }];
  const harness = await freshModule({
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, json: async () => ({ items: responses.shift() }) };
    },
  });
  try {
    await harness.module.load();
    await harness.module.load();
    assert.equal(calls.length, 1, "cached load must not issue a second request");
    assert.equal(harness.module.get("zero", 9), 0);
    assert.equal(harness.module.get("disabled", true), false);
    assert.equal(harness.module.get("empty", "fallback"), "");
    assert.equal(harness.module.get("missing", "fallback"), "fallback");
    await harness.module.load(true);
    assert.equal(calls.length, 2);
    assert.equal(harness.module.get("later", null), 2);
  } finally {
    harness.restore();
  }
});

test("parallel consumers share one in-flight hydration request", async () => {
  let resolveResponse;
  let calls = 0;
  const response = new Promise((resolve) => { resolveResponse = resolve; });
  const harness = await freshModule({
    fetchImpl: async () => {
      calls += 1;
      return response;
    },
  });
  try {
    const first = harness.module.load();
    const second = harness.module.load();
    assert.equal(calls, 1);
    resolveResponse({ ok: true, json: async () => ({ items: { shared: true } }) });
    assert.deepEqual(await first, { shared: true });
    assert.deepEqual(await second, { shared: true });
  } finally {
    harness.restore();
  }
});

test("set debounces writes and pagehide flushes pending values with keepalive", async () => {
  const writes = [];
  const pending = new Map();
  let timerId = 0;
  const harness = await freshModule({
    fetchImpl: async (url, options) => {
      writes.push({ url, options });
      return { ok: true, json: async () => ({ items: {} }) };
    },
    timeoutImpl: (callback) => {
      const id = ++timerId;
      pending.set(id, callback);
      return id;
    },
    clearImpl: (id) => pending.delete(id),
  });
  try {
    harness.module.set("patients.search", "one");
    harness.module.set("patients.search", "two");
    assert.equal(pending.size, 1, "the newer value must replace the pending write");
    [...pending.values()][0]();
    await Promise.resolve();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].options.keepalive, false);
    assert.deepEqual(JSON.parse(writes[0].options.body), {
      key: "patients.search", value: "two",
    });

    harness.module.set("day_queue.filters", { q: "Ana" });
    harness.listeners.pagehide();
    await Promise.resolve();
    assert.equal(writes.length, 2);
    assert.equal(writes[1].options.keepalive, true);
    assert.deepEqual(JSON.parse(writes[1].options.body), {
      key: "day_queue.filters", value: { q: "Ana" },
    });
  } finally {
    harness.restore();
  }
});

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function drain(turns = 8) { for (let i = 0; i < turns; i += 1) await tick(); }

function timerHarness({ results, toast }) {
  // results: array of behaviors per PUT attempt — "fail" (network reject),
  // "503" (non-ok response) or a value the server accepted.
  const writes = [];
  const timers = new Map();
  let timerId = 0;
  let attempt = 0;
  const args = {
    toast,
    fetchImpl: async (url, options) => {
      writes.push({ url, options });
      const outcome = results[Math.min(attempt, results.length - 1)];
      attempt += 1;
      if (outcome === "fail") throw new Error("offline");
      if (outcome === "503") return { ok: false, status: 503 };
      return { ok: true, json: async () => ({ ok: true }) };
    },
    timeoutImpl: (callback) => {
      const id = ++timerId;
      timers.set(id, callback);
      return id;
    },
    clearImpl: (id) => timers.delete(id),
  };
  const fire = () => { const cb = [...timers.values()][0]; timers.delete([...timers.keys()][0]); return cb && cb(); };
  return { args, writes, timers, fire };
}

test("a failed PUT retries once; a second failure restores the previous value and says so", async () => {
  const toasts = [];
  const previousWarn = console.warn;
  const warnings = [];
  console.warn = (...args) => warnings.push(args.join(" "));
  const h = timerHarness({ results: ["fail", "503"], toast: (m) => toasts.push(m) });
  const harness = await freshModule(h.args);
  try {
    harness.module.set("patients.search", "new");
    h.fire(); // debounce → first PUT fails
    await drain();
    assert.equal(h.writes.length, 1);
    assert.equal(h.timers.size, 1, "the transient failure schedules one retry");
    h.fire(); // retry → 503
    await drain();
    assert.equal(h.writes.length, 2, "exactly one retry, no loop");
    assert.equal(harness.module.get("patients.search", "old"), "old",
      "the cache returns to the last value the server accepted");
    assert.equal(toasts.length, 1, "the failure is surfaced, not swallowed");
    assert.match(toasts[0], /No se pudo guardar tu preferencia/);
    assert.equal(warnings.length, 1);
  } finally {
    console.warn = previousWarn;
    harness.restore();
  }
});

test("a transient failure that the retry covers keeps the value and stays silent", async () => {
  const toasts = [];
  const h = timerHarness({ results: ["503", "ok"], toast: (m) => toasts.push(m) });
  const harness = await freshModule(h.args);
  try {
    harness.module.set("patients.search", "new");
    h.fire();
    await drain();
    h.fire(); // retry succeeds
    await drain();
    assert.equal(h.writes.length, 2);
    assert.equal(harness.module.get("patients.search", "old"), "new");
    assert.equal(toasts.length, 0);
  } finally {
    harness.restore();
  }
});

test("a set without a prior value deletes the key back when both attempts fail", async () => {
  const h = timerHarness({ results: ["fail", "fail"], toast: () => {} });
  const harness = await freshModule(h.args);
  try {
    harness.module.set("day_queue.widths", { name: 120 });
    h.fire();
    await drain();
    h.fire();
    await drain();
    assert.equal(harness.module.get("day_queue.widths", "fallback"), "fallback",
      "no phantom value the server never accepted");
  } finally {
    harness.restore();
  }
});

test("a change flushed on pagehide that the server has not stored yet is what the next page shows, and it is sent again", async () => {
  // Form audit 2026-10-25: «the last chosen role» came back as the old one 2 runs of 3. The
  // pagehide keepalive PUT and the next page's GET race; when the GET answers first the screen
  // showed the old value. The tab's sessionStorage is the outbox both pages share.
  const store = new Map();
  const previousStorage = globalThis.sessionStorage;
  globalThis.sessionStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  };
  const writes = [];
  const leaving = await freshModule({
    fetchImpl: (url, options) => {
      writes.push({ url, options });
      return new Promise(() => {}); // the keepalive PUT is still on its way
    },
  });
  try {
    leaving.module.set("users.last_role", "reportes");
    leaving.listeners.pagehide();
  } finally {
    leaving.restore();
  }
  assert.equal(writes.length, 1);
  assert.equal(writes[0].options.keepalive, true);

  const next = await freshModule({
    fetchImpl: async (url, options) => {
      writes.push({ url, options });
      if ((options && options.method) === "PUT") return { ok: true, json: async () => ({ ok: true }) };
      return { ok: true, json: async () => ({ items: { "users.last_role": "admisionista" } }) };
    },
  });
  try {
    await next.module.load();
    assert.equal(next.module.get("users.last_role", null), "reportes",
      "the unacknowledged change wins over the older stored value");
    await drain();
    const resent = writes.slice(1).filter((w) => w.options && w.options.method === "PUT");
    assert.equal(resent.length, 1, "the next page sends the change again");
    assert.deepEqual(JSON.parse(resent[0].options.body), { key: "users.last_role", value: "reportes" });
    assert.equal(store.size, 0, "an acknowledged change leaves the outbox");
  } finally {
    next.restore();
    globalThis.sessionStorage = previousStorage;
  }
});

test("on the read-only demo a preference stays in the tab: no toast, no retry, no second request", async () => {
  // Demo 2026-10-26: opening a patient's card showed «No se pudo guardar tu preferencia» — the
  // demo session refuses every write (403 demo_solo_lectura) and this module took it for a failure.
  // A preference is how the screen remembers a column or a filter, not something the visitor
  // asked to save: on the demo it lives in the tab, and the server is not asked again.
  const store = new Map();
  const previousStorage = globalThis.sessionStorage;
  globalThis.sessionStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  };
  const toasts = [];
  const writes = [];
  const timers = new Map();
  let timerId = 0;
  const fetchImpl = async (url, options) => {
    if ((options && options.method) === "PUT") {
      writes.push(JSON.parse(options.body));
      return { ok: false, status: 403, json: async () => ({ error: "demo_solo_lectura" }) };
    }
    return { ok: true, json: async () => ({ items: { "patients.columns": ["dni"] } }) };
  };
  const fire = () => { const id = [...timers.keys()][0]; const cb = timers.get(id); timers.delete(id); return cb && cb(); };
  const first = await freshModule({
    fetchImpl, toast: (m) => toasts.push(m),
    timeoutImpl: (cb) => { const id = ++timerId; timers.set(id, cb); return id; },
    clearImpl: (id) => timers.delete(id),
  });
  try {
    await first.module.load();
    first.module.set("patients.columns", ["dni", "nombre"]);
    fire();
    await drain();
    assert.equal(writes.length, 1);
    assert.equal(timers.size, 0, "a read-only refusal is not retried");
    assert.deepEqual(toasts, [], "nothing failed: nothing to say");
    assert.deepEqual(first.module.get("patients.columns", null), ["dni", "nombre"],
      "the visitor's choice holds");
    first.module.set("patients.page_size", 50);
    assert.equal(timers.size, 0, "once refused, later choices never go to the server");
    assert.equal(first.module.get("patients.page_size", 10), 50);
  } finally {
    first.restore();
  }
  const next = await freshModule({ fetchImpl, toast: (m) => toasts.push(m) });
  try {
    await next.module.load();
    await drain();
    assert.deepEqual(next.module.get("patients.columns", null), ["dni", "nombre"],
      "the next screen of the visit still shows the choice");
    assert.equal(next.module.get("patients.page_size", 10), 50);
    assert.equal(writes.length, 1, "and does not send it again");
    assert.deepEqual(toasts, []);
  } finally {
    next.restore();
    globalThis.sessionStorage = previousStorage;
  }
});

test("the pagehide flush never retries and never toasts: the tab is already leaving", async () => {
  const toasts = [];
  const h = timerHarness({ results: ["fail"], toast: (m) => toasts.push(m) });
  const harness = await freshModule(h.args);
  try {
    harness.module.set("patients.search", "late");
    harness.listeners.pagehide();
    await drain();
    assert.equal(h.writes.length, 1, "keepalive flush: one attempt, no retry timer");
    assert.equal(h.timers.size, 0);
    assert.equal(toasts.length, 0);
    assert.equal(harness.module.get("patients.search", "old"), "old");
  } finally {
    harness.restore();
  }
});
