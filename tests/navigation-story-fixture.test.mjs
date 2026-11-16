import test from "node:test";
import assert from "node:assert/strict";
import { installNavigationFixture } from "../stories/navigation-fixture.mjs";

function browser(t) {
  const originals = ["window", "document", "localStorage"].map((key) => [key, globalThis[key]]);
  t.after(() => {
    for (const [key, value] of originals) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  });
  const stored = new Map([["st_nav", "original"], ["st_identity", "untouched"], ["st_photo_v", "untouched"]]);
  const styles = new Map([["--stznav-h", ["42px", "important"]]]);
  const calls = [];
  const baselineFetch = (input) => {
    calls.push(input);
    return Promise.resolve(new Response("ok"));
  };
  globalThis.localStorage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, String(value)),
    removeItem: (key) => stored.delete(key),
  };
  globalThis.document = { documentElement: { style: {
    getPropertyValue: (key) => styles.get(key)?.[0] || "",
    getPropertyPriority: (key) => styles.get(key)?.[1] || "",
    setProperty: (key, value, priority = "") => styles.set(key, [value, priority]),
    removeProperty: (key) => styles.delete(key),
  } } };
  const priorTrace = { prior: true };
  globalThis.window = { fetch: baselineFetch, location: { href: "https://story.test/iframe.html", origin: "https://story.test" }, __HRNavFixture: priorTrace };
  return { calls, styles, stored, baselineFetch, priorTrace };
}

test("out-of-order story cleanup restores the original fetch, cache, height, and trace", (t) => {
  const { styles, stored, baselineFetch, priorTrace } = browser(t);
  const cleanupA = installNavigationFixture();
  const fetchA = window.fetch;
  const traceA = window.__HRNavFixture;
  stored.set("st_nav", "anonymous");
  styles.set("--stznav-h", ["10px", ""]);
  const cleanupB = installNavigationFixture();
  const fetchB = window.fetch;
  const traceB = window.__HRNavFixture;
  stored.set("st_nav", "authenticated");
  styles.set("--stznav-h", ["20px", ""]);

  cleanupA();
  assert.equal(window.fetch, fetchB);
  assert.equal(window.__HRNavFixture, traceB);
  cleanupB();
  cleanupA();
  assert.notEqual(fetchA, baselineFetch);
  assert.notEqual(traceA, priorTrace);
  assert.equal(window.fetch, baselineFetch);
  assert.equal(window.__HRNavFixture, priorTrace);
  assert.equal(stored.get("st_nav"), "original");
  assert.deepEqual(styles.get("--stznav-h"), ["42px", "important"]);
  assert.equal(stored.get("st_identity"), "untouched");
  assert.equal(stored.get("st_photo_v"), "untouched");
});

test("Request signal aborts and unknown or external APIs never reach network", async (t) => {
  const { calls } = browser(t);
  const cleanup = installNavigationFixture();
  const aborter = new AbortController();
  const request = new Request("https://story.test/api/account/me", { signal: aborter.signal });
  aborter.abort();
  await assert.rejects(window.fetch(request), { name: "AbortError" });
  assert.deepEqual(window.__HRNavFixture.calls, []);
  assert.throws(() => window.fetch("/api/unexpected"), /Unexpected navigation story API request/);
  assert.throws(() => window.fetch("https://outside.test/api/account/me"), /External navigation story request blocked/);
  assert.deepEqual(calls, []);
  await window.fetch("/static/logo.png");
  assert.deepEqual(calls, ["/static/logo.png"]);
  cleanup();
});
