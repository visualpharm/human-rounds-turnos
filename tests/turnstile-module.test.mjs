import test from "node:test";
import assert from "node:assert/strict";

async function fresh(name) {
  return import(`../static/platform/turnstile.mjs?test=${name}-${Date.now()}-${Math.random()}`);
}

test("turnstile.mjs exports the real client without a compatibility global", async () => {
  globalThis.window = {};
  const mod = await fresh("exports");
  for (const name of ["mount", "token", "reset", "activo", "config"]) {
    assert.equal(typeof mod[name], "function", name);
  }
  assert.equal(window.SanaTurnstile, undefined);
});

test("disabled gate hides its box, caches config and returns an empty token", async () => {
  let fetches = 0;
  globalThis.fetch = async (url, options) => {
    fetches += 1;
    assert.equal(url, "/api/account/config");
    assert.equal(options.credentials, "same-origin");
    return { ok: true, async json() { return { turnstile_sitekey: null }; } };
  };
  globalThis.window = {};
  const mod = await fresh("disabled");
  const box = { style: {}, isConnected: true };
  assert.equal(await mod.mount(box), false);
  assert.equal(box.style.display, "none");
  assert.equal(await mod.token(1), "");
  assert.equal(mod.activo(), false);
  await mod.config();
  assert.equal(fetches, 1, "configuration is fetched once per page");
});

test("enabled gate mounts, returns the provider token and resets it", async () => {
  const renders = [];
  const resets = [];
  globalThis.fetch = async () => ({
    ok: true,
    async json() { return { turnstile_sitekey: "site-key" }; },
  });
  globalThis.window = {
    turnstile: {
      render(box, options) { renders.push({ box, options }); return "widget-1"; },
      getResponse(widget) { assert.equal(widget, "widget-1"); return "challenge-token"; },
      reset(widget) { resets.push(widget); },
    },
  };
  globalThis.document = {
    getElementById() { return null; },
    createElement() { throw new Error("provider script must not be duplicated"); },
    head: { appendChild() { throw new Error("provider script must not be duplicated"); } },
  };
  const mod = await fresh("enabled");
  const box = { style: { display: "none" }, isConnected: true };
  assert.equal(await mod.mount(box), true);
  assert.equal(box.style.display, "");
  assert.equal(mod.activo(), true);
  assert.equal(renders.length, 1);
  assert.deepEqual(renders[0].options, {
    sitekey: "site-key", theme: "auto", appearance: "interaction-only",
  });
  assert.equal(await mod.token(20), "challenge-token");
  mod.reset();
  assert.deepEqual(resets, ["widget-1"]);
});

test("enabled gate times out a silent provider, clears timers, and retries with the actual module", async () => {
  const nativeSetTimeout = globalThis.setTimeout;
  const nativeClearTimeout = globalThis.clearTimeout;
  let clock = 0;
  let nextTimer = 1;
  const timers = new Map();
  globalThis.setTimeout = (callback, delay = 0) => {
    const id = nextTimer++;
    timers.set(id, { at: clock + Number(delay), callback });
    return id;
  };
  globalThis.clearTimeout = (id) => { timers.delete(id); };
  async function flush() {
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  }
  async function advance(ms) {
    const target = clock + ms;
    while (true) {
      const due = [...timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) break;
      const [id, timer] = due;
      timers.delete(id);
      clock = timer.at;
      timer.callback();
      await flush();
    }
    clock = target;
    await flush();
  }

  try {
    globalThis.fetch = async () => ({
      ok: true,
      async json() { return { turnstile_sitekey: "site-key" }; },
    });
    globalThis.window = {};
    let currentScript = null;
    const created = [];
    const removed = [];
    function scriptElement() {
      const attrs = new Map();
      const listeners = new Map();
      return {
        parentNode: null,
        addEventListener(type, listener) { listeners.set(type, listener); },
        removeEventListener(type, listener) {
          if (listeners.get(type) === listener) listeners.delete(type);
        },
        getAttribute(name) { return attrs.get(name) || null; },
        setAttribute(name, value) { attrs.set(name, String(value)); },
      };
    }
    const head = {
      appendChild(script) {
        script.parentNode = head;
        currentScript = script;
        created.push(script);
      },
      removeChild(script) {
        assert.equal(script, currentScript);
        script.parentNode = null;
        currentScript = null;
        removed.push(script);
      },
    };
    globalThis.document = {
      getElementById(id) { return id === "cf-turnstile-script" ? currentScript : null; },
      createElement(name) {
        assert.equal(name, "script");
        return scriptElement();
      },
      head,
    };

    const mod = await fresh("silent-provider-timeout");
    const firstBox = { style: { display: "none" }, isConnected: true };
    const firstMount = mod.mount(firstBox);
    await flush();
    assert.equal(created.length, 1);
    assert.equal(timers.size, 2, "one provider deadline and one poll must be pending");
    await advance(8000);
    assert.equal(await firstMount, false, "the unchanged production 8s deadline must resolve mount(false)");
    assert.equal(currentScript.getAttribute("data-load-failed"), "1");
    assert.equal(timers.size, 0, "deadline and recursive poll must both be cleared");

    const renders = [];
    const secondBox = { style: { display: "none" }, isConnected: true };
    const retryMount = mod.mount(secondBox);
    await flush();
    assert.equal(removed.length, 1, "retry must remove the failed provider script");
    assert.equal(created.length, 2, "retry must create a fresh provider request");
    window.turnstile = {
      render(box, options) { renders.push({ box, options }); return "widget-retry"; },
      getResponse() { return "retry-token"; },
      reset() {},
    };
    await advance(200);
    assert.equal(await retryMount, true);
    assert.equal(renders.length, 1);
    assert.equal(renders[0].box, secondBox);
    assert.equal(timers.size, 0, "successful retry must clear its deadline and poll");
  } finally {
    globalThis.setTimeout = nativeSetTimeout;
    globalThis.clearTimeout = nativeClearTimeout;
  }
});


test("all login widgets use the shared renderer", async () => {
  const {readFile} = await import('node:fs/promises');
  for (const name of ['admin-login', 'account-form']) {
    const source = await readFile(new URL(`../static/platform/${name}.mjs`, import.meta.url), 'utf8');
    assert.ok(source.includes('platform/turnstile.mjs'));
    assert.ok(!source.includes('window.turnstile.render'));
  }
});

test("required token rejects a silent challenge and accepts delayed success", async () => {
  globalThis.fetch = async () => ({ok:true, json:async()=>({turnstile_sitekey:'key'})});
  let response = '';
  globalThis.window = {turnstile:{render:()=> 'w', getResponse:()=>response}};
  const mod = await fresh('required');
  await mod.mount({style:{},isConnected:true});
  await assert.rejects(mod.requireToken({timeout:1}), /turnstile/);
  response = 'ready';
  assert.equal(await mod.requireToken(), 'ready');
});
