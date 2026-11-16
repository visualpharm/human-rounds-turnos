// A page left open past the municipal portal session: the gateway answers
// every /static request with 401 {"error":"unauthenticated","login":...} and
// the next lazy import() fails. core/static_recover.js must send the person
// once to the portal sign-in, back to the same page — and do nothing else.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../app/core/static_recover.js", import.meta.url), "utf8");
const LOGIN = "https://portal.pinamar.gob.ar/sso/login";
const PAGE = "https://salud.pinamar.gob.ar/admin/my-patients?tab=today";

function boot({ status = 401, body = { error: "unauthenticated", login: LOGIN }, storage = {}, fetchFails = false, map } = {}) {
  const listeners = { unhandledrejection: [], error: [] };
  const calls = { fetch: [], assign: [] };
  const win = {
    SANA_STATIC: map || { "platform/i18n.mjs": "/static/platform/i18n.abcd1234.mjs", "platform/dates.mjs": "/static/platform/dates.mjs" },
    location: { href: PAGE, assign: (u) => calls.assign.push(u) },
    sessionStorage: { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = v; } },
    fetch: async (url, init) => {
      calls.fetch.push([url, init]);
      if (fetchFails) throw new TypeError("offline");
      return { status, json: async () => { if (body === null) throw new SyntaxError("not json"); return body; } };
    },
    addEventListener: (type, fn) => listeners[type].push(fn),
  };
  win.window = win;
  vm.runInNewContext(SOURCE, { window: win, URL, Promise, Date, Number, String, Object, RegExp, Error });
  return { win, listeners, calls, storage };
}

test("401 from the gateway: one trip to the portal sign-in that returns to this page", async () => {
  const { win, calls, storage } = boot();
  assert.equal(await win.sanaRecoverStatic(), true);
  assert.equal(calls.fetch.length, 1);
  assert.equal(calls.fetch[0][0], "/static/platform/i18n.abcd1234.mjs");
  assert.equal(calls.fetch[0][1].credentials, "same-origin");
  assert.equal(calls.assign.length, 1);
  const target = new URL(calls.assign[0]);
  assert.equal(target.origin + target.pathname, LOGIN);
  assert.equal(target.searchParams.get("return"), PAGE);
  assert.ok(Number(storage["sana:static-relogin"]) > 0, "the trip is stamped");
});

test("no loop: a second failure within five minutes does not leave again", async () => {
  const { win, calls } = boot({ storage: { "sana:static-relogin": String(Date.now() - 60000) } });
  assert.equal(await win.sanaRecoverStatic(), false);
  assert.equal(calls.fetch.length, 0);
  assert.equal(calls.assign.length, 0);
});

test("a trip older than five minutes is allowed again", async () => {
  const { win, calls } = boot({ storage: { "sana:static-relogin": String(Date.now() - 400000) } });
  assert.equal(await win.sanaRecoverStatic(), true);
  assert.equal(calls.assign.length, 1);
});

test("anything that is not the gateway's 401 shape stays the page's own problem", async () => {
  for (const opts of [
    { status: 404, body: null },
    { status: 200, body: null },
    { status: 401, body: null },                                  // Basic Auth text/plain
    { status: 401, body: { error: "unauthenticated" } },          // no sign-in URL
    { status: 401, body: { login: "http://portal.pinamar.gob.ar/sso/login" } },
    { status: 401, body: { login: "javascript:alert(1)" } },
    { fetchFails: true },
    { map: {} },
  ]) {
    const { win, calls } = boot(opts);
    assert.equal(await win.sanaRecoverStatic(), false, JSON.stringify(opts));
    assert.equal(calls.assign.length, 0, JSON.stringify(opts));
  }
});

test("concurrent failures share one probe", async () => {
  const { win, calls } = boot();
  const [a, b, c] = await Promise.all([win.sanaRecoverStatic(), win.sanaRecoverStatic(), win.sanaRecoverStatic()]);
  assert.deepEqual([a, b, c], [true, true, true]);
  assert.equal(calls.fetch.length, 1);
  assert.equal(calls.assign.length, 1);
});

test("an unhandled import failure, and a module script that failed to load, trigger it; other errors do not", async () => {
  const chrome = boot();
  chrome.listeners.unhandledrejection[0]({ reason: new TypeError("Failed to fetch dynamically imported module: https://x/static/platform/i18n.abcd1234.mjs") });
  await new Promise((r) => setImmediate(r));
  assert.equal(chrome.calls.assign.length, 1);

  const safari = boot();
  safari.listeners.unhandledrejection[0]({ reason: new TypeError("Importing a module script failed.") });
  await new Promise((r) => setImmediate(r));
  assert.equal(safari.calls.assign.length, 1);

  const tag = boot();
  tag.listeners.error[0]({ target: { tagName: "SCRIPT", type: "module" } });
  await new Promise((r) => setImmediate(r));
  assert.equal(tag.calls.assign.length, 1);

  const other = boot();
  other.listeners.unhandledrejection[0]({ reason: new TypeError("x is not a function") });
  other.listeners.error[0]({ target: { tagName: "IMG" } });
  other.listeners.error[0]({ message: "ReferenceError: y is not defined", target: other.win });
  await new Promise((r) => setImmediate(r));
  assert.equal(other.calls.fetch.length, 0);
});

test("the admin loader hands a recovering page a promise that never settles, and a normal failure still shows the error", async () => {
  const shell = readFileSync(new URL("../static/root/admin-shell.js", import.meta.url), "utf8");
  for (const leaving of [true, false]) {
    const added = [];
    const doc = { querySelector: () => ({ appendChild: (n) => added.push(n) }), getElementById: () => null, createElement: () => ({ setAttribute() {} }), body: {} };
    const win = { SANA_STATIC: { "platform/x.mjs": "data:text/javascript,throw new Error('boom')" }, sanaRecoverStatic: async () => leaving };
    win.window = win;
    vm.runInNewContext(shell, { window: win, document: doc, console: { error() {} }, Promise, setTimeout, clearTimeout });
    const outcome = await Promise.race([
      win.sanaImportStatic("platform/x.mjs").then(() => "resolved", () => "rejected"),
      new Promise((r) => setTimeout(() => r("pending"), 50)),
    ]);
    assert.equal(outcome, leaving ? "pending" : "rejected");
    assert.equal(added.length, leaving ? 0 : 1);
  }
});
