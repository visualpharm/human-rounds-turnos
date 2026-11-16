import test from "node:test";
import assert from "node:assert/strict";
import {
  installEditorialFixture, mountEditorialStoryWhenAttached,
  reportEditorialMountError, setEditorialStoryRoute, setEditorialSystemStatus,
  holdEditorialSystemRead,
} from "../stories/admin-editorial-fixture.mjs";

function environment(t) {
  const prior = Object.fromEntries(["window", "requestAnimationFrame", "cancelAnimationFrame"].map((key) =>
    [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  t.after(() => Object.entries(prior).forEach(([key, descriptor]) =>
    descriptor ? Object.defineProperty(globalThis, key, descriptor) : delete globalThis[key]));
  const location = new URL("http://localhost:7500/iframe.html#before");
  const priorState = { story: "before" };
  const history = {
    state: priorState,
    replaceState(state, _title, path) {
      this.state = state;
      const next = new URL(path, location.href);
      location.href = next.href;
    },
  };
  const forwards = [];
  const nativeFetch = async (input) => { forwards.push(input); return new Response("outside fixture"); };
  globalThis.window = { location, history, fetch: nativeFetch };
  const frames = new Map();
  let id = 0;
  globalThis.requestAnimationFrame = (callback) => { const next = ++id; frames.set(next, callback); return next; };
  globalThis.cancelAnimationFrame = (frame) => frames.delete(frame);
  return {
    location, priorState, nativeFetch, forwards,
    flush() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach((callback) => callback()); },
  };
}

test("editorial fixture mutates only exact synthetic routes and captures native bodies", async (t) => {
  const { nativeFetch, forwards } = environment(t);
  const cleanup = installEditorialFixture();
  const trace = window.__HREditorialFixture;
  const original = (await (await window.fetch("/api/admin/content/preparations")).json()).items;
  assert.equal(original.length, 1);
  const created = { nombre: "New fictional prep", resumen: "One line", match: ["study"],
    ayuno: { required: true, horas: "8", agua_permitida: false, nota: null },
    antes: [], traer: [], avisar: [], no_hace_falta: [], despues: [] };
  const post = await (await window.fetch("/api/admin/content/preparations", {
    method: "POST", body: JSON.stringify(created),
  })).json();
  assert.equal(post.item.id, "story-created-preparation");
  assert.deepEqual(trace.calls.at(-1).body.match, ["study"]);
  const put = await (await window.fetch("/api/admin/content/preparations/story-created-preparation", {
    method: "PUT", body: JSON.stringify({ ...created, nombre: "Updated fictional prep" }),
  })).json();
  assert.equal(put.item.nombre, "Updated fictional prep");
  assert.equal((await (await window.fetch("/api/admin/content/preparations")).json()).items.at(-1).nombre,
    "Updated fictional prep");
  assert.deepEqual(await (await window.fetch("/api/admin/content/preparations/story-created-preparation", {
    method: "DELETE",
  })).json(), { ok: true, turnos: 0, conservado: true });
  assert.equal((await (await window.fetch("/api/admin/content/preparations/story-created-preparation/restaurar", {
    method: "POST",
  })).json()).item._borrado, false);

  const legal = await (await window.fetch("/api/admin/legal/terminos", {
    method: "PUT", body: JSON.stringify({ cuerpo: "Updated fictional terms" }),
  })).json();
  assert.equal(legal.item.editado, true);
  assert.equal((await (await window.fetch("/api/admin/legal")).json()).items[0].cuerpo, "Updated fictional terms");
  assert.equal((await (await window.fetch("/api/admin/legal/terminos", { method: "DELETE" })).json()).item.editado, false);

  const prompts = await (await window.fetch("/api/admin/prompts/story-appointment/guardar", {
    method: "POST", body: JSON.stringify({ texto_en: "Updated English instructions" }),
  })).json();
  assert.equal(prompts.item.texto_en, "Updated English instructions");
  assert.deepEqual(await (await window.fetch("/api/admin/prompts/story-appointment/historial")).json(), { items: [] });
  assert.equal((await (await window.fetch("/api/admin/prompts/story-appointment/traduccion")).json()).alineado, true);
  assert.equal((await (await window.fetch("/api/admin/prompts/story-appointment", { method: "DELETE" })).json()).item.editado, false);

  await assert.rejects(window.fetch("/api/admin/legal/unknown"), /Unexpected admin editorial story API request/);
  await assert.rejects(window.fetch("/api/admin/prompts/unknown/historial"), /Unknown synthetic prompt/);
  await assert.rejects(window.fetch("/api/admin/legal?other=1"), /query or fragment/);
  await assert.rejects(window.fetch("/api/admin/legal#fragment"), /query or fragment/);
  await assert.rejects(window.fetch("https://outside.test/api/admin/legal"), /External admin editorial request blocked/);
  await assert.rejects(window.fetch("/api/admin/legal", { method: "GET", body: "{}" }), /GET cannot carry a body/);
  await assert.rejects(window.fetch("/api/admin/legal/terminos", {
    method: "PUT", body: JSON.stringify({ cuerpo: "Text", extra: true }),
  }), /cuerpo only/);
  assert.equal(await (await window.fetch("/storybook-probe")).text(), "outside fixture");
  assert.deepEqual(forwards, ["/storybook-probe"]);
  const retiredFetch = window.fetch;
  cleanup();
  assert.equal(window.fetch, nativeFetch);
  assert.equal(window.__HREditorialFixture, undefined);
  await assert.rejects(retiredFetch("/api/admin/legal"), /Disposed admin editorial fixture/);
});

test("a successor retires singleton mounts and restores the surviving route and trace", (t) => {
  const { location, nativeFetch, priorState, flush } = environment(t);
  const cleanupA = installEditorialFixture();
  const traceA = window.__HREditorialFixture;
  setEditorialStoryRoute("/admin/legal?doc=terminos");
  assert.equal(location.search, "?doc=terminos");
  let retired = 0;
  mountEditorialStoryWhenAttached({ isConnected: true }, () => () => { retired += 1; });
  flush();
  const cleanupB = installEditorialFixture();
  const traceB = window.__HREditorialFixture;
  assert.equal(retired, 1, "A releases its singleton controller before B begins");
  assert.notEqual(traceA, traceB);
  assert.throws(() => traceA.setSystemStatus(500), /Retired System trace/);
  assert.throws(() => traceA.holdSystemRead(), /Retired System trace/);
  setEditorialStoryRoute("/admin/prompts");
  let current = 0;
  mountEditorialStoryWhenAttached({ isConnected: true }, () => () => { current += 1; });
  flush();
  cleanupA();
  assert.equal(current, 0, "late A cleanup cannot destroy B");
  assert.equal(window.__HREditorialFixture, traceB);
  const status = { hidden: true, setAttribute() {}, textContent: "" };
  reportEditorialMountError(new Error("late A error"), status, traceA);
  assert.deepEqual(traceB.errors, [], "late A rejection cannot pollute B's trace");
  assert.equal(status.hidden, true);
  reportEditorialMountError(new Error("B mount error"), status, traceB);
  assert.deepEqual(traceB.errors, ["B mount error"]);
  cleanupB();
  assert.equal(current, 1);
  assert.equal(window.fetch, nativeFetch);
  assert.equal(window.__HREditorialFixture, undefined);
  assert.equal(location.pathname + location.search + location.hash, "/iframe.html#before");
  assert.equal(window.history.state, priorState);
});

test("story route keys stay paired with their owning admin screens", (t) => {
  environment(t);
  const cleanup = installEditorialFixture();
  for (const path of ["/admin/legal?prompt=x", "/admin/prompts?doc=terminos",
    "/admin/preparations?item=x&other=y", "/admin/legal?doc=other", "/admin/content"]) {
    assert.throws(() => setEditorialStoryRoute(path), /Invalid admin editorial story route/);
  }
  cleanup();
});

test("cleanup preserves a successor Storybook URL and history state", (t) => {
  const { location } = environment(t);
  const cleanup = installEditorialFixture();
  setEditorialStoryRoute("/admin/legal?doc=terminos");
  const nextState = { story: "next" };
  window.history.replaceState(nextState, "", "/iframe.html?id=next-story&viewMode=story");
  cleanup();
  assert.equal(location.pathname + location.search + location.hash,
    "/iframe.html?id=next-story&viewMode=story#before");
  assert.equal(window.history.state, nextState);
});

test("cleanup follows manager selection before the preview URL updates", (t) => {
  const { location, priorState } = environment(t);
  window.history.replaceState(priorState, "", "/iframe.html?id=admin-content-legal--documents&viewMode=story#before");
  window.parent = { location: new URL("http://localhost:7500/?path=/story/admin-content-legal--documents") };
  const cleanup = installEditorialFixture();
  setEditorialStoryRoute("/admin/legal?doc=terminos");
  window.parent.location.search = "?path=/story/neutral--chart";
  cleanup();
  assert.equal(location.pathname + location.search + location.hash,
    "/iframe.html?id=neutral--chart&viewMode=story#before");
  assert.equal(window.history.state, priorState);
});

test("System uses exact synthetic statuses, all four cluster states, and abortable held reads", async (t) => {
  environment(t);
  const cleanup = installEditorialFixture();
  const trace = window.__HREditorialFixture;
  setEditorialStoryRoute("/admin/system");
  const data = await (await window.fetch("/api/admin/system")).json();
  assert.deepEqual(data.clusters.map((cluster) => cluster.estado), ["ok", "warn", "error", "na"]);
  assert.equal(data.clusters[1].items[0].campo, "TURNOS_DOMINIO");
  assert.equal(data.clusters[2].items[0].campo, "MAILGUN_DOMAIN");
  setEditorialSystemStatus(401);
  const expired = await window.fetch("/api/admin/system");
  assert.equal(expired.status, 401);
  assert.deepEqual(await expired.json(), { error: "no_autenticado" });
  assert.equal((await window.fetch("/api/admin/whoami")).status, 200);
  setEditorialSystemStatus(403);
  assert.equal((await window.fetch("/api/admin/system")).status, 403);
  const hold = holdEditorialSystemRead();
  const controller = new AbortController();
  const pending = window.fetch("/api/admin/system", { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(hold.release(), false, "aborted work cannot resolve later");
  assert.equal(trace.calls.filter(({ path }) => path === "/api/admin/system").length, 4);
  cleanup();
});

test("a cached real session module checks the successor's synthetic whoami", async (t) => {
  const priorFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  t.after(() => priorFetch ? Object.defineProperty(globalThis, "fetch", priorFetch) : delete globalThis.fetch);
  environment(t);
  const cleanupA = installEditorialFixture();
  globalThis.fetch = window.fetch;
  const session = await import("../static/platform/admin-session.mjs?story-fixture-session-boundary");
  const cleanupB = installEditorialFixture();
  globalThis.fetch = window.fetch;
  cleanupA();
  const expired = new Response(JSON.stringify({ error: "no_autenticado" }), {
    status: 401, headers: { "Content-Type": "application/json" },
  });
  assert.equal(await session.confirmar(expired, undefined, () => true), false);
  assert.deepEqual(window.__HREditorialFixture.calls.at(-1), {
    method: "GET", path: "/api/admin/whoami", body: null,
  });
  assert.equal(window.location.pathname, "/iframe.html", "synthetic session must not navigate");
  cleanupB();
});
