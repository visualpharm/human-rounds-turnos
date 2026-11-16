import { before, after } from "node:test";
import { test, ceil, hookOptions } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { stays } from "./helpers/wait.mjs";
import { stableBox } from "./helpers/box.mjs";

// Real chat modules/DOM with synthetic visualViewport events and mocked
// network. This verifies layout/lifecycle logic, not a hardware keyboard.
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
after(async () => { await browser?.close(); });

async function fixture(t, { mode = "helper", width = 390, embedded = false, noViewport = false, noMedia = false, initialStyle = "", messages = [] } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 844 } });
  ceil(page);
  t.after(() => page.close());
  const errors = [], posts = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.addInitScript(({ noViewport, noMedia }) => {
    const vv = new EventTarget();
    Object.assign(vv, { height: 844, offsetTop: 0, scale: 1 });
    const tracked = new Map();
    for (const [name, target] of [["viewport", vv], ["window", window]]) {
      const add = target.addEventListener.bind(target), remove = target.removeEventListener.bind(target);
      target.addEventListener = (type, listener, options) => {
        const key = `${name}:${type}`;
        if (!tracked.has(key)) tracked.set(key, new Set());
        tracked.get(key).add(listener);
        return add(type, listener, options);
      };
      target.removeEventListener = (type, listener, options) => {
        tracked.get(`${name}:${type}`)?.delete(listener);
        return remove(type, listener, options);
      };
    }
    Object.defineProperty(window, "visualViewport", { configurable: true, value: noViewport ? undefined : vv });
    if (noMedia) window.matchMedia = undefined;
    window.viewportListeners = () => Object.fromEntries([...tracked].map(([k, v]) => [k, v.size]));
    window.resizeVisual = (height, offsetTop = 0, scale = 1, type = "resize") => {
      Object.assign(vv, { height, offsetTop, scale });
      vv.dispatchEvent(new Event(type));
    };
    window.syntheticViewport = vv;
  }, { noViewport, noMedia });
  const staticMap = Object.fromEntries(["chat-ui", "chat-widget", "i18n", "dates", "account-form"].map(name => [`platform/${name}.mjs`, `/static/platform/${name}.mjs`]));
  await page.route("**/*", async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (req.method() === "POST") posts.push(path);
    if (path.startsWith("/static/platform/") || ["/modules/intake/entrevista.js", "/modules/intake/entrevista.css"].includes(path)) {
      // a module asset's URL (/modules/...) names it inside the code package
      const disk = path.startsWith("/modules/") ? `../app${path}` : `..${path}`;
      return route.fulfill({ contentType: path.endsWith("css") ? "text/css" : "text/javascript", body: readFileSync(new URL(disk, import.meta.url), "utf8") });
    }
    if (path === "/api/chat/voice") return route.fulfill({ json: { disponible: false } });
    if (path === "/api/chat/history") return route.fulfill({ json: { mensajes: [] } });
    if (path === "/api/chat/whatsapp") return route.fulfill({ status: 404, json: {} });
    if (path === "/api/intake/t/synthetic-only") return route.fulfill({ json: { status: "active", mensajes: messages } });
    if (path === "/") {
      const body = mode === "helper" ? `<div id="surface" style="${initialStyle}"><textarea></textarea></div>` : mode === "intake"
        ? '<main class="wrap--chat"><div id="iv-host" data-token="synthetic-only"></div></main>' : '<main><h1>Demo</h1></main>';
      const script = mode === "helper"
        ? `import {seguirViewport} from '/static/platform/chat-ui.mjs';window.opened=true;window.updates=0;window.viewport=seguirViewport(document.getElementById('surface'),{media:'(max-width:700px)',enabled:()=>window.opened,onResize:()=>window.updates++});`
        : mode === "intake" ? "import '/modules/intake/entrevista.js';"
          : "import * as widget from '/static/platform/chat-widget.mjs';window.widget=widget;widget.abrir();";
      return route.fulfill({ contentType: "text/html", body: `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
        <style>body{margin:0;font-family:Arial,sans-serif;--font:Arial,sans-serif;--bg:#fff;--text:#222;--accent:#176b43}#surface{position:fixed;left:0;right:0}</style>
        ${mode === "intake" ? '<link rel="stylesheet" href="/modules/intake/entrevista.css">' : ""}
        <script>window.SANA_STATIC=${JSON.stringify(staticMap)};window.SANA_I18N={active:'es',catalog:{}};</script>
        </head><body class="${mode === "intake" ? embedded ? "iv-embed-body" : "iv-chat-body" : ""}">${body}<script type="module">${script}</script></body></html>` });
    }
    return route.fulfill({ status: 404, body: "Outside synthetic scope" });
  });
  await page.goto("https://viewport.test/");
  const selector = mode === "helper" ? "#surface" : mode === "intake" ? "#iv-host" : "#stz-chat-panel";
  if (mode === "helper") await page.waitForFunction(() => window.viewport);
  else await page.locator(mode === "intake" ? "#iv-form textarea" : "#stz-chat-panel textarea").waitFor();
  t.after(() => { assert.deepEqual(errors, []); assert.deepEqual(posts, []); });
  return { page, selector };
}
async function styles(page, selector) {
  return page.locator(selector).evaluate(el => ({ top: el.style.top, height: el.style.height }));
}
async function resize(page, height, offset = 0, scale = 1, type = "resize") {
  await page.evaluate(([height, offset, scale, type]) => window.resizeVisual(height, offset, scale, type), [height, offset, scale, type]);
}

test("shared helper follows synthetic keyboard height/offset, then restores the viewport", async t => {
  const { page, selector } = await fixture(t);
  for (const [height, offset] of [[360, 0], [393, 0], [430, 0], [390, 40]]) {
    await resize(page, height, offset);
    assert.deepEqual(await styles(page, selector), { height: height + "px", top: offset + "px" });
  }
  await resize(page, 420, 38);
  assert.deepEqual(await styles(page, selector), { height: "420px", top: "38px" });
  await resize(page, 420, 62, 1, "scroll");
  assert.deepEqual(await styles(page, selector), { height: "420px", top: "62px" });
  await resize(page, 844);
  assert.deepEqual(await styles(page, selector), { height: "844px", top: "0px" });
});

test("pinch zoom/pan does not shrink or reposition the layout; scale 1 resumes", async t => {
  const { page, selector } = await fixture(t);
  await resize(page, 420, 20);
  await resize(page, 210, 70, 2);
  await resize(page, 210, 120, 2, "scroll");
  assert.deepEqual(await styles(page, selector), { height: "420px", top: "20px" });
  await resize(page, 440, 0);
  assert.deepEqual(await styles(page, selector), { height: "440px", top: "0px" });
});

test("desktop/closed/disposal restore exact pre-existing styles and priority", async t => {
  const { page, selector } = await fixture(t, { initialStyle: "top:9px;height:77px !important" });
  await page.setViewportSize({ width: 701, height: 844 });
  await page.waitForFunction(() => document.querySelector('#surface').style.height === '77px');
  assert.deepEqual(await styles(page, selector), { height: "77px", top: "9px" });
  assert.equal(await page.locator(selector).evaluate(el => el.style.getPropertyPriority("height")), "important");
  await page.setViewportSize({ width: 700, height: 844 });
  await page.waitForFunction(() => document.querySelector('#surface').style.height === '844px');
  await page.evaluate(() => { window.opened = false; window.viewport.update(); });
  await resize(page, 300);
  assert.deepEqual(await styles(page, selector), { height: "77px", top: "9px" });
  await page.evaluate(() => { window.opened = true; window.viewport.update(); window.viewport.dispose(); window.viewport.dispose(); });
  await resize(page, 280, 50);
  assert.deepEqual(await styles(page, selector), { height: "77px", top: "9px" });
  const listeners = await page.evaluate(() => window.viewportListeners());
  for (const type of ["viewport:resize", "viewport:scroll", "window:resize", "window:orientationchange", "window:pagehide", "window:pageshow"]) assert.equal(listeners[type], 0, type);
});

for (const missing of ["noViewport", "noMedia"]) {
  test(`${missing}: CSS geometry remains authoritative`, async t => {
    const { page, selector } = await fixture(t, { [missing]: true, initialStyle: "height:77px;top:9px" });
    await page.evaluate(() => window.dispatchEvent(new Event("resize")));
    assert.deepEqual(await styles(page, selector), { height: "77px", top: "9px" });
  });
}

test("orientation updates immediately and again after the browser settles its dimensions", async t => {
  const { page, selector } = await fixture(t);
  // Read the immediate update and let the browser "settle" in the same task:
  // a separate round trip could arrive after the 60ms settle timer under load.
  const immediate = await page.evaluate(() => {
    Object.assign(window.syntheticViewport, { height: 500, offsetTop: 20 });
    window.dispatchEvent(new Event("orientationchange"));
    const el = document.getElementById("surface");
    const seen = { height: el.style.height, top: el.style.top };
    window.syntheticViewport.height = 400;
    return seen;
  });
  assert.deepEqual(immediate, { height: "500px", top: "20px" });
  await page.waitForFunction(() => document.getElementById('surface').style.height === '400px');
});

test("BFCache suspends listeners/timer and resumes once with fresh geometry; normal pagehide disposes", async t => {
  const { page, selector } = await fixture(t);
  await resize(page, 420, 24);
  await page.evaluate(() => {
    window.dispatchEvent(new Event("orientationchange"));
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
  });
  await resize(page, 300, 30);
  assert.deepEqual(await styles(page, selector), { height: "", top: "" });
  assert.equal((await page.evaluate(() => window.viewportListeners()))["viewport:resize"], 0);
  // prove the old orientation timer (60ms) cannot reapply: the geometry stays cleared for the whole window
  await stays(async () => { const s = await styles(page, selector); return s.height === "" && s.top === ""; }, "the old orientation timer does not reapply geometry", 80);
  assert.deepEqual(await styles(page, selector), { height: "", top: "" });
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
  assert.deepEqual(await styles(page, selector), { height: "300px", top: "30px" });
  assert.equal((await page.evaluate(() => window.viewportListeners()))["viewport:resize"], 1);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: false })));
  await resize(page, 200, 40);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));
  assert.deepEqual(await styles(page, selector), { height: "", top: "" });
  assert.equal((await page.evaluate(() => window.viewportListeners()))["viewport:resize"], 0);
});

test("detached surface disposes on its next viewport event", async t => {
  const { page } = await fixture(t);
  await page.locator("#surface").evaluate(el => el.remove());
  await resize(page, 400);
  assert.equal((await page.evaluate(() => window.viewportListeners()))["viewport:resize"], 0);
});

for (const mode of ["widget", "intake"]) {
  test(`${mode}: real composer fits synthetic keyboard viewport and offset; desktop keeps CSS height`, async t => {
    const { page, selector } = await fixture(t, { mode });
    await resize(page, 420, 30);
    assert.deepEqual(await styles(page, selector), { height: "420px", top: "30px" });
    const composer = page.locator(mode === "widget" ? ".stzc-form" : "#iv-form");
    const box = await stableBox(composer, `${mode} composer`);
    assert.ok(box.y >= 30 && box.y + box.height <= 450, JSON.stringify(box));
    await page.setViewportSize({ width: mode === "widget" ? 701 : 768, height: 844 });
    await page.waitForFunction(selector => document.querySelector(selector).style.height === "", selector);
    assert.deepEqual(await styles(page, selector), { height: "", top: "" });
    await page.setViewportSize({ width: mode === "widget" ? 700 : 767, height: 844 });
    await page.waitForFunction(selector => document.querySelector(selector).style.height === "420px", selector);
    if (mode === "widget") {
      await page.evaluate(() => window.widget.cerrar());
      await resize(page, 320, 40);
      assert.deepEqual(await styles(page, selector), { height: "", top: "" });
      await page.evaluate(() => window.widget.abrir());
      assert.deepEqual(await styles(page, selector), { height: "320px", top: "40px" });
    }
  });
}

test("embedded intake follows its own viewport at wide widths without changing outer page policy", async t => {
  const { page, selector } = await fixture(t, { mode: "intake", width: 900, embedded: true });
  await resize(page, 400, 16);
  assert.deepEqual(await styles(page, selector), { height: "400px", top: "16px" });
  const box = await stableBox(page.locator("#iv-form"), "embedded intake composer");
  assert.ok(box.y + box.height <= 416, JSON.stringify(box));
});

test("intake keeps the last message visible on viewport resize without pulling a reader down", async t => {
  const messages = Array.from({ length: 30 }, (_, i) => ({ role: "assistant", texto: `Mensaje sintético ${i}. Texto de prueba.` }));
  const { page } = await fixture(t, { mode: "intake", messages });
  const gap = () => page.locator("#iv-msgs").evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
  await page.waitForFunction(() => { const el = document.getElementById('iv-msgs'); return el.scrollHeight - el.scrollTop - el.clientHeight < 2; });
  await resize(page, 420, 20);
  await page.waitForFunction(() => { const el = document.getElementById('iv-msgs'); return el.scrollHeight - el.scrollTop - el.clientHeight < 2; });
  assert.ok(await gap() < 2);
  await page.locator("#iv-msgs").evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event("scroll")); });
  await resize(page, 360, 30);
  // let both existing scroll-animation frames run
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(() => done()))));
  assert.equal(await page.locator("#iv-msgs").evaluate(el => el.scrollTop), 0);
});
