import test from "node:test";
import assert from "node:assert/strict";
import {
  configureAccountFixture, installAccountFixture, mountAccountStoryWhenAttached,
  setAccountStoryHash,
} from "../stories/account-fixture.mjs";
import * as LabStories from "../stories/AccountLabs.stories.js";
import * as RequestStories from "../stories/AccountRequests.stories.js";

function environment() {
  const values = new Map([["st_identity", "prior identity"]]);
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
  const forwarded = [];
  const originalFetch = async (input) => {
    forwarded.push(input);
    return new Response("outside fixture");
  };
  globalThis.localStorage = storage;
  const location = {
    href: "http://localhost:7500/iframe.html#prior",
    origin: "http://localhost:7500", pathname: "/iframe.html", search: "", hash: "#prior",
  };
  const initialState = { before: "storybook state" };
  const history = {
    state: initialState,
    replaceState(state, _title, target) {
      this.state = state;
      location.hash = new URL(target, location.href).hash;
      location.href = "http://localhost:7500/iframe.html" + location.hash;
    },
  };
  globalThis.window = {
    location, history, fetch: originalFetch,
  };
  const frames = new Map();
  let nextFrame = 1;
  globalThis.requestAnimationFrame = (callback) => { const id = nextFrame++; frames.set(id, callback); return id; };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  function flushFrames() {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback());
  }
  return { storage, originalFetch, forwarded, frames, flushFrames, initialState };
}

test("the account fixture owns only its story lifetime and rejects unexpected API traffic", async () => {
  const { storage, originalFetch, forwarded } = environment();
  const cleanup = installAccountFixture();
  assert.equal(storage.getItem("st_identity"), null);
  const config = await window.fetch("/api/account/config");
  assert.deepEqual(await config.json(), {
    auth_mode: "password", renaper_provider: "real", test_identity_provider: false,
    turnstile_sitekey: null, otp_registro_requerido: false,
  });
  const request = new Request("http://localhost:7500/api/account/dependents", {
    method: "POST", body: JSON.stringify({ nombre: "Ficción", dni: "50123456" }),
  });
  assert.equal((await (await window.fetch(request)).json()).dependiente.nombre, "Ficción");
  assert.equal(window.__HRAccountFixture.calls[1].body.nombre, "Ficción");
  await assert.rejects(window.fetch("/api/account/unknown"), /Unexpected account story API request/);
  await assert.rejects(window.fetch("https://outside.test/api/account/config"), /External account story request blocked/);
  await assert.rejects(window.fetch("/api/account/config?dni=123"), /Unexpected account story API query/);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(window.fetch("/api/account/dependents", { signal: controller.signal }), { name: "AbortError" });
  assert.equal((await (await window.fetch("/storybook-probe")).text()), "outside fixture");
  assert.deepEqual(forwarded, ["/storybook-probe"]);
  cleanup();
  assert.equal(window.fetch, originalFetch);
  assert.equal(window.__HRAccountFixture, undefined);
  assert.equal(storage.getItem("st_identity"), "prior identity");
});

test("lab upload keeps real FormData and the next GET sees the synthetic result", async () => {
  const { originalFetch } = environment();
  const cleanup = installAccountFixture();
  configureAccountFixture({
    labs: { informes: [], series: [], analisis_pendiente: 0 },
    uploadedLab: { fecha: "2026-09-01", tipo_estudio: "Uploaded sample", items: [] },
  });
  const form = new FormData();
  form.append("archivo", new File(["sample"], "sample.pdf", { type: "application/pdf" }));
  const response = await window.fetch("/api/account/lab-results/upload", { method: "POST", body: form });
  assert.equal(response.status, 200);
  assert.equal(window.__HRAccountFixture.calls[0].body, form);
  const labs = await (await window.fetch("/api/account/lab-results")).json();
  assert.equal(labs.informes[0].tipo_estudio, "Uploaded sample");
  const retiredFetch = window.fetch;
  cleanup();
  assert.equal(window.fetch, originalFetch);
  await assert.rejects(retiredFetch("/api/account/lab-results"), /Disposed account story fixture/);
});

test("a delayed Request body cannot mutate a fixture after disposal", async () => {
  environment();
  const cleanup = installAccountFixture();
  const trace = window.__HRAccountFixture;
  let stream;
  const request = new Request("http://localhost:7500/api/account/dependents", {
    method: "POST", duplex: "half", headers: { "Content-Type": "application/json" },
    body: new ReadableStream({ start(controller) { stream = controller; } }),
  });
  const response = window.fetch(request);
  // The real Request body's read is in flight when the story goes away.
  await new Promise((resolve) => setImmediate(resolve));
  cleanup();
  stream.enqueue(new TextEncoder().encode(JSON.stringify({ nombre: "Late synthetic person" })));
  stream.close();
  await assert.rejects(response, /Disposed account story fixture/);
  assert.deepEqual(trace.calls, [], "retired fixture cannot log or apply a late mutation");
});

test("cleanup does not overwrite a later fetch owner", () => {
  environment();
  const cleanup = installAccountFixture();
  const replacement = async () => new Response("replacement");
  window.fetch = replacement;
  cleanup();
  assert.equal(window.fetch, replacement);
});

test("nested story cleanup preserves the active fixture's identity scope", async () => {
  const { storage, originalFetch } = environment();
  const outerCleanup = installAccountFixture();
  storage.setItem("st_identity", "outer state");
  const innerCleanup = installAccountFixture();
  const innerFetch = window.fetch;
  outerCleanup();
  assert.equal(window.fetch, innerFetch);
  assert.equal(storage.getItem("st_identity"), null);
  assert.equal(await (await window.fetch("/storybook-probe")).text(), "outside fixture",
    "a surviving fixture must skip its disposed predecessor for ordinary requests");
  innerCleanup();
  assert.equal(window.fetch, originalFetch);
  assert.equal(storage.getItem("st_identity"), "prior identity");
  assert.equal(window.__HRAccountFixture, undefined);
  // Repeated disposal does not restore the retired wrapper.
  outerCleanup();
  innerCleanup();
  assert.equal(window.fetch, originalFetch);
});

test("overlap disposal runs mounts before global restoration and restores the first hash and history state", () => {
  const { storage, originalFetch, flushFrames, initialState } = environment();
  const outerCleanup = installAccountFixture();
  setAccountStoryHash("#labs");
  const order = [];
  const outerNode = { isConnected: true };
  mountAccountStoryWhenAttached(outerNode, () => () => {
    order.push("outer disposed");
    assert.notEqual(window.fetch, originalFetch);
  });
  flushFrames();
  const innerCleanup = installAccountFixture();
  setAccountStoryHash("#labs&r=0");
  window.history.state = { detail: "pushed report" };
  const innerNode = { isConnected: true };
  mountAccountStoryWhenAttached(innerNode, () => () => {
    order.push("inner disposed");
    assert.notEqual(window.fetch, originalFetch);
  });
  flushFrames();
  outerCleanup();
  assert.deepEqual(order, ["outer disposed"]);
  assert.equal(window.location.hash, "#labs&r=0");
  assert.equal(storage.getItem("st_identity"), null);
  innerCleanup();
  assert.deepEqual(order, ["outer disposed", "inner disposed"]);
  assert.equal(window.location.hash, "#prior");
  assert.equal(window.history.state, initialState);
  assert.equal(storage.getItem("st_identity"), "prior identity");
  assert.equal(window.fetch, originalFetch);
});

test("a deferred lab reply cannot resolve after its fixture is disposed", async () => {
  environment();
  const cleanup = installAccountFixture();
  let resolveReply;
  configureAccountFixture({ labsReply: () => new Promise((resolve) => { resolveReply = resolve; }) });
  const pending = window.fetch("/api/account/lab-results");
  await Promise.resolve();
  cleanup();
  resolveReply(new Response(JSON.stringify({ informes: [] })));
  await assert.rejects(pending, /Disposed account story fixture cannot resolve/);
});

test("a story removed before attachment cannot late-mount", () => {
  const { frames, flushFrames } = environment();
  const cleanup = installAccountFixture();
  let mounted = false;
  mountAccountStoryWhenAttached({ isConnected: false }, () => { mounted = true; return () => {}; });
  flushFrames();
  assert.equal(frames.size, 1, "detached story remains scheduled while active");
  cleanup();
  flushFrames();
  assert.equal(frames.size, 0);
  assert.equal(mounted, false);
});

test("Labs and Requests exports return host elements synchronously", () => {
  environment();
  class Element {
    constructor(tagName) {
      this.tagName = tagName.toUpperCase();
      this.children = []; this.style = {}; this.dataset = {}; this.isConnected = false;
    }
    append(...children) { this.children.push(...children); }
    setAttribute(name, value) { this[name] = String(value); }
  }
  globalThis.HTMLElement = Element;
  globalThis.document = { createElement: (tag) => new Element(tag) };
  const cases = [
    [LabStories, ["Results", "Empty", "Unauthorized"]],
    [RequestStories, ["Appointments", "Empty", "Unauthorized"]],
  ];
  for (const [stories, names] of cases) {
    for (const name of names) {
      const cleanup = stories.default.beforeEach();
      const node = stories[name]();
      assert.ok(node instanceof HTMLElement, name + " must return an HTMLElement, not a Promise");
      if (stories === LabStories) {
        assert.equal(node.className, "dcard");
        assert.deepEqual(node.children.map((child) => child.id), ["lr-host", "lr-note"]);
      } else {
        assert.equal(node.id, "ms-host");
        assert.equal(node["data-embedded"], "1");
      }
      cleanup();
    }
  }
});
