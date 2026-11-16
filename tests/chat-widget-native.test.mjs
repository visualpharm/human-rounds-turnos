// Delayed/rejected native chat widget, separately from delayed/rejected Chat
// UI. Source scans cannot prove an early launcher click queues, a close
// while held stays closed after release, rejected imports recover, or
// sessionStorage survives internal navigation. Uses the real runtime files
// and rendered controls — not a hand-rolled widget.
import { test, within, hookOptions, openPage } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file) => fs.readFileSync(path.join(ROOT, file), "utf8");
const ORIGIN = "https://chat-widget-native.test";
const WIDGET_PATH = "/static/platform/chat-widget.aaaaaaaa.mjs";
const CHAT_UI_PATH = "/static/platform/chat-ui.aaaaaaaa.mjs";
const FILES = {
  [WIDGET_PATH]: "static/platform/chat-widget.mjs",
  [CHAT_UI_PATH]: "static/platform/chat-ui.mjs",
  "/static/platform/i18n.mjs": "static/platform/i18n.mjs",
  "/static/platform/dates.mjs": "static/platform/dates.mjs",
  "/static/platform/account-form.mjs": "static/platform/account-form.mjs",
};
const STATIC = {
  "platform/chat-widget.mjs": WIDGET_PATH,
  "platform/chat-ui.mjs": CHAT_UI_PATH,
  "platform/i18n.mjs": "/static/platform/i18n.mjs",
  "platform/dates.mjs": "/static/platform/dates.mjs",
  "platform/account-form.mjs": "/static/platform/account-form.mjs",
};
const COPY = {
  es: {
    "chat.open": "Abrir Chat con IA",
    "nav.chat": "Chateá con IA",
    "chat.connect_error": "No pudimos conectar. Probá de nuevo en un rato.",
  },
  en: {
    "chat.open": "Open Chat with AI",
    "nav.chat": "Chat with AI",
    "chat.connect_error": "We couldn't connect. Try again shortly.",
  },
};

function deferred() {
  let release;
  let done = false;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release() { if (!done) { done = true; release(); } } };
}

function renderBootstrap(lang) {
  const catalog = COPY[lang];
  return read("templates/_chat_bootstrap.html")
    .replace(/\{#[\s\S]*?#\}/g, "")
    .replace(/\{\{\s*t\('([^']+)',\s*'([^']*)'\)\s*\}\}/g, (_, key, fallback) => catalog[key] || fallback);
}

function pageHtml({ lang = "es", title = "Home" } = {}) {
  const catalog = { ...COPY[lang] };
  return `<!doctype html><html lang="${lang}"><head><meta charset="utf-8">
<title>${title}</title>
<script>window.SANA_STATIC=${JSON.stringify(STATIC)};
window.SANA_I18N={active:${JSON.stringify(lang)},catalog:${JSON.stringify(catalog)}};</script>
</head><body>
<main><h1>${title}</h1><p>Public page.</p></main>
${renderBootstrap(lang)}
</body></html>`;
}

async function fulfillFiles(route, {
  holdWidget, abortWidget, holdChatUi, abortChatUi, whatsapp,
} = {}) {
  const url = new URL(route.request().url());
  const pathname = url.pathname;
  if (abortWidget && pathname === WIDGET_PATH) return route.abort("failed");
  if (abortChatUi && pathname === CHAT_UI_PATH) return route.abort("failed");
  if (holdWidget && pathname === WIDGET_PATH) {
    holdWidget.requested.release();
    await holdWidget.release.promise;
  }
  if (holdChatUi && pathname === CHAT_UI_PATH) {
    holdChatUi.requested.release();
    await holdChatUi.release.promise;
  }
  if (pathname === "/api/chat/whatsapp") {
    if (whatsapp && whatsapp.link) {
      return route.fulfill({ json: { link: whatsapp.link } });
    }
    return route.fulfill({ status: 404, json: { error: "not found" } });
  }
  if (pathname === "/api/chat/history") {
    return route.fulfill({ json: { mensajes: [] } });
  }
  if (pathname === "/api/chat/voice") {
    return route.fulfill({ json: { disponible: false } });
  }
  if (pathname === "/api/account/me") {
    return route.fulfill({ status: 401, json: { error: "unauthorized" } });
  }
  if (FILES[pathname]) {
    return route.fulfill({
      contentType: "text/javascript; charset=utf-8",
      body: read(FILES[pathname]),
    });
  }
  if (pathname === "/" || pathname === "/about.html" || pathname === "/en") {
    const lang = pathname === "/en" ? "en" : "es";
    const title = pathname === "/about.html" ? "About" : "Home";
    return route.fulfill({
      contentType: "text/html; charset=utf-8",
      body: pageHtml({ lang, title }),
    });
  }
  if (pathname.startsWith("/static/icons/")) {
    return route.fulfill({ status: 200, body: "", contentType: "image/png" });
  }
  return route.fulfill({ status: 404, body: "" });
}

// One Chromium per file, a fresh context per page (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

async function withPage(run, viewport = { width: 1280, height: 900 }) {
  const page = await openPage(browser, { viewport });
  try {
    const failures = [];
    page.on("pageerror", (error) => failures.push(String(error)));
    await run(page, failures);
    assert.deepEqual(failures.filter((msg) => !/failed to fetch|Failed to fetch|Load failed|error loading dynamically imported module/i.test(msg)), []);
  } finally {
    await page.context().close();
  }
}

test("early open then close while the widget is held cannot resurrect; a later open works once", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdWidget: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the widget module");
    const delayed = await page.evaluate(() => ({
      launcher: !!document.getElementById("stz-chat-launcher"),
      panel: !!document.getElementById("stz-chat-panel"),
      sanaChat: typeof window.SanaChat,
      chatWidget: typeof window.ChatWidget,
      composer: !!document.querySelector("#stzc-form textarea"),
    }));
    assert.equal(delayed.launcher, true, "bootstrap launcher must be clickable while the widget is held");
    assert.equal(delayed.panel, false);
    assert.equal(delayed.composer, false);
    assert.equal(delayed.sanaChat, "undefined");
    assert.equal(delayed.chatWidget, "undefined");

    await page.locator("#stz-chat-launcher").click();
    assert.equal(await page.locator("#stz-chat-launcher").getAttribute("data-chat-intent"), "open");
    await page.locator("#stz-chat-launcher").evaluate((el) => el.dispatchEvent(new Event("stzc-close")));
    assert.equal(await page.locator("#stz-chat-launcher").getAttribute("data-chat-intent"), "closed");

    release.release();
    await navigation;
    await page.locator("#stz-chat-panel").waitFor({ state: "attached" });
    assert.deepEqual(await page.evaluate(() => ({
      expanded: document.getElementById("stz-chat-launcher").getAttribute("aria-expanded"),
      panelOpen: !document.getElementById("stz-chat-panel").hidden,
      bodyOpen: document.body.classList.contains("stzc-chat-open"),
      sanaChat: typeof window.SanaChat,
    })), { expanded: "false", panelOpen: false, bodyOpen: false, sanaChat: "undefined" });

    await page.locator("#stz-chat-launcher").click();
    await page.locator("#stz-chat-panel").waitFor({ state: "visible" });
    await page.locator("#stzc-form textarea").waitFor({ state: "visible" });
    assert.equal(await page.locator("#stz-chat-panel").count(), 1);
  });
});

test("early open then close while Chat UI is held cannot resurrect; a later open works once", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdChatUi: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the Chat UI module");
    assert.equal(await page.locator("#stz-chat-launcher").count(), 1);
    assert.equal(await page.locator("#stz-chat-panel").count(), 0);
    await page.locator("#stz-chat-launcher").click();
    await page.locator("#stz-chat-launcher").evaluate((el) => el.dispatchEvent(new Event("stzc-close")));
    release.release();
    await navigation;
    await page.locator("#stz-chat-panel").waitFor({ state: "attached" });
    assert.deepEqual(await page.evaluate(() => ({
      expanded: document.getElementById("stz-chat-launcher").getAttribute("aria-expanded"),
      panelOpen: !document.getElementById("stz-chat-panel").hidden,
      composer: !!document.querySelector("#stzc-form textarea"),
      sanaChat: typeof window.SanaChat,
    })), { expanded: "false", panelOpen: false, composer: true, sanaChat: "undefined" });
    await page.locator("#stz-chat-launcher").click();
    await page.locator("#stz-chat-panel").waitFor({ state: "visible" });
    assert.equal(await page.locator("#stzc-form textarea").isVisible(), true);
  });
});

test("rejected widget import shows localized recovery without duplicate listeners or a hidden fatal state", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { abortWidget: true }));
    await page.goto(ORIGIN + "/", { waitUntil: "load" });
    const box = page.locator("#chat-boot-error");
    await box.waitFor({ state: "visible" });
    assert.match(await box.locator("p").textContent(), /No pudimos conectar\. Probá de nuevo/);
    assert.equal(await page.locator("#chat-boot-retry").count(), 1);
    assert.equal(await page.locator("#stz-chat-launcher").isVisible(), false);
    assert.equal(await page.locator("#stz-chat-panel").count(), 0);
    assert.equal(await page.evaluate(() => typeof window.SanaChat), "undefined");
    await page.locator("#stz-chat-launcher").evaluate((el) => el.click());
    assert.equal(await page.locator("#chat-boot-retry").count(), 1);
    assert.equal(await page.locator("#chat-boot-retry").getAttribute("data-wired"), "1");
  });
});

test("rejected widget import recovers in English without a second listener", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { abortWidget: true }));
    await page.goto(ORIGIN + "/en", { waitUntil: "load" });
    const box = page.locator("#chat-boot-error");
    await box.waitFor({ state: "visible" });
    assert.match(await box.locator("p").textContent(), /We couldn't connect\. Try again shortly/);
    assert.match(await page.locator("#chat-boot-retry").textContent(), /Open Chat with AI/);
    assert.equal(await page.locator("#stz-chat-launcher").isVisible(), false);
    await page.locator("#stz-chat-launcher").evaluate((el) => el.click());
    assert.equal(await page.locator("#chat-boot-retry").count(), 1);
    assert.equal(await page.locator("#chat-boot-retry").getAttribute("data-wired"), "1");
  });
});

test("rejected Chat UI import shows localized recovery and truthful open/close without a composer", async () => {
  await withPage(async (page, failures) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { abortChatUi: true }));
    await page.goto(ORIGIN + "/", { waitUntil: "load" });
    await page.locator("#stz-chat-launcher").click();
    const alert = page.locator("#stz-chat-panel [role='alert']");
    await alert.waitFor({ state: "visible" });
    assert.match(await alert.textContent(), /No pudimos conectar\. Probá de nuevo/);
    assert.equal(await page.locator("#stz-chat-panel .stzc-login-btn").count(), 1);
    assert.equal(await page.locator("#stzc-form textarea").count(), 0);
    assert.deepEqual(await page.evaluate(() => ({
      expanded: document.getElementById("stz-chat-launcher").getAttribute("aria-expanded"),
      panelOpen: !document.getElementById("stz-chat-panel").hidden,
      bodyOpen: document.body.classList.contains("stzc-chat-open"),
      sanaChat: typeof window.SanaChat,
    })), { expanded: "true", panelOpen: true, bodyOpen: true, sanaChat: "undefined" });
    await page.locator(".stzc-close").click();
    assert.deepEqual(await page.evaluate(() => ({
      expanded: document.getElementById("stz-chat-launcher").getAttribute("aria-expanded"),
      panelOpen: !document.getElementById("stz-chat-panel").hidden,
      alerts: document.querySelectorAll("#stz-chat-panel [role='alert']").length,
    })), { expanded: "false", panelOpen: false, alerts: 1 });
    await page.locator("#stz-chat-launcher").click();
    assert.equal(await page.locator("#stz-chat-panel [role='alert']").count(), 1);
    assert.equal(await page.locator("#stzc-form textarea").count(), 0);
  }, { width: 390, height: 844 });
});

test("synthetic conversation state survives internal navigation and cannot display an unavailable WhatsApp action", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route));
    await page.goto(ORIGIN + "/", { waitUntil: "load" });
    await page.locator("#stz-chat-launcher").waitFor({ state: "visible" });
    await page.evaluate(() => {
      const now = Date.now();
      sessionStorage.setItem("stz_chat_sidebar_v2", JSON.stringify({
        open: true,
        activeId: "chat-test",
        conversations: [{
          id: "chat-test",
          title: "Pedido de turno",
          messages: '<div class="stzc-msg stzc-msg--user">Quiero un turno en cardiología</div>',
          hasUserMessage: true,
          welcomed: true,
          whatsapp: '<a href="https://example.test">Seguí por WhatsApp</a>',
          createdAt: now,
          updatedAt: now,
        }],
      }));
    });
    await page.goto(ORIGIN + "/about.html", { waitUntil: "load" });
    await page.locator("#stz-chat-panel").waitFor({ state: "visible" });
    assert.match(await page.locator("#stzc-msgs").innerHTML(), /Quiero un turno en cardiología/);
    const wa = page.locator("#stzc-wa");
    await wa.waitFor({ state: "attached" });
    assert.deepEqual(await wa.evaluate((node) => ({
      hidden: node.hidden,
      display: getComputedStyle(node).display,
      width: node.getBoundingClientRect().width,
      href: node.getAttribute("href"),
    })), { hidden: true, display: "none", width: 0, href: null });
    assert.equal(await page.evaluate(() => typeof window.SanaChat), "undefined");
  });
});

test("«Nuevo chat» opens a fresh server session: it POSTs /api/chat/new, never fetches history, and stays empty", async () => {
  await withPage(async (page) => {
    const historyFetches = [];
    await page.route(`${ORIGIN}/**`, (route) => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      if (pathname === "/api/chat/history") {
        // Whatever session asks: the fused thread the BUG poured into a
        // brand-new conversation. Post-fix, a button-created conversation
        // must never even make this request.
        historyFetches.push(request.url());
        return route.fulfill({ json: { mensajes: [
          { rol: "user", texto: "turno viejo que no debe volver", canal: "web" },
        ] } });
      }
      if (pathname === "/api/chat/new" && request.method() === "POST") {
        return route.fulfill({ json: { ok: true } });
      }
      return fulfillFiles(route);
    });
    await page.goto(ORIGIN + "/", { waitUntil: "load" });
    await page.locator("#stz-chat-launcher").waitFor({ state: "visible" });
    await page.evaluate(() => {
      const now = Date.now();
      sessionStorage.setItem("stz_chat_sidebar_v2", JSON.stringify({
        open: true,
        activeId: "chat-vieja",
        conversations: [{
          id: "chat-vieja",
          title: "Pedido de turno",
          messages: '<div class="stzc-msg stzc-msg--user">Quiero un turno en cardiología</div>',
          hasUserMessage: true,
          welcomed: true,
          hidratado: true,
          whatsapp: "",
          createdAt: now,
          updatedAt: now,
        }],
      }));
    });
    await page.goto(ORIGIN + "/about.html", { waitUntil: "load" });
    await page.locator("#stz-chat-panel").waitFor({ state: "visible" });
    assert.match(await page.locator("#stzc-msgs").innerHTML(), /Quiero un turno en cardiología/);
    assert.equal(historyFetches.length, 0, "the restored conversation is already hydrated");

    const newPost = page.waitForRequest((request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/chat/new")
      .catch(() => null);
    await page.locator("#stzc-new").click();
    assert.ok(await newPost, "«Nuevo chat» must POST /api/chat/new (fire-and-forget)");

    const lateHistory = await page.waitForRequest((request) =>
      new URL(request.url()).pathname === "/api/chat/history", { timeout: 1500 })
      .then(() => "fetched", () => null);
    assert.equal(lateHistory, null,
      "a button-created conversation must never fetch /api/chat/history");
    assert.deepEqual(historyFetches, []);

    const html = await page.locator("#stzc-msgs").innerHTML();
    assert.doesNotMatch(html, /turno viejo que no debe volver/);
    assert.doesNotMatch(html, /Quiero un turno en cardiología/);
    assert.match(html, /Hola, soy el chat con IA/, "only the fresh greeting remains");
  });
});

// bootWidget() wraps every existing <body> child into #stzc-page the moment
// the delayed module evaluates (chat-widget.mjs). A person already typing
// into a page field when that happens must not lose the field: appendChild-
// moving the subtree detaches the focused input, so focus, the selected
// range and every later keystroke die with it — the intermittent home query
// loss. Each test below holds the widget module, types for real, then
// releases it and asserts the person's typing state survived the mount.
// Nothing may re-focus or re-fill the field after the mount: that would hide
// the defect, not prove it fixed.
async function heldWidgetPage(page) {
  const requested = deferred();
  const release = deferred();
  await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
    holdWidget: { requested, release },
  }));
  const navigation = page.goto(ORIGIN + "/", { waitUntil: "domcontentloaded" });
  await within(requested.promise, "request of the widget module");
  return { release, navigation };
}

for (const atomicMove of [true, false]) {
test(`delayed widget mount preserves field identity, focus and both selections: atomic ${atomicMove}`, async () => {
  await withPage(async (page) => {
    await page.addInitScript((enabled) => {
      if (!enabled) Object.defineProperty(Element.prototype, "moveBefore", { value: undefined, configurable: true });
    }, atomicMove);
    const { release, navigation } = await heldWidgetPage(page);
    assert.equal(await page.evaluate(() => typeof Element.prototype.moveBefore), atomicMove ? "function" : "undefined");
    await page.evaluate(() => {
      const main = document.querySelector("main");
      const q = document.createElement("input");
      q.id = "q";
      q.type = "text";
      const note = document.createElement("textarea");
      note.id = "note";
      main.append(q, note);
    });
    const qHandle = await page.evaluateHandle(() => document.getElementById("q"));
    await page.locator("#note").click();
    await page.keyboard.type("dolor de cabeza");
    await page.keyboard.press("Home");
    for (let i = 0; i < 5; i += 1) await page.keyboard.press("Shift+ArrowRight");
    await page.locator("#q").click();
    await page.keyboard.type("cardio");
    await page.keyboard.press("Shift+ArrowLeft");
    await page.keyboard.press("Shift+ArrowLeft");
    assert.deepEqual(await page.evaluate(() => ({
      active: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null,
      qValue: document.getElementById("q").value,
      qSelection: [document.getElementById("q").selectionStart, document.getElementById("q").selectionEnd],
      noteValue: document.getElementById("note").value,
      noteSelection: [document.getElementById("note").selectionStart, document.getElementById("note").selectionEnd],
    })), {
      active: "q",
      qValue: "cardio",
      qSelection: [4, 6],
      noteValue: "dolor de cabeza",
      noteSelection: [0, 5],
    }, "the person's typing state must be exactly this before the widget loads");

    await page.locator("#q").evaluate((q) => {
      q.mountFocusEvents = [];
      for (const kind of ["blur", "focus"]) q.addEventListener(kind, () => q.mountFocusEvents.push(kind));
    });
    release.release();
    await navigation;
    await page.locator("#stz-chat-panel").waitFor({ state: "attached" });
    assert.equal(
      await page.evaluate((held) => document.getElementById("q") === held, qHandle),
      true,
      "the wrap must MOVE the page column, never rebuild it — the input node's identity must survive",
    );
    const mounted = await page.evaluate(() => {
      const q = document.getElementById("q");
      const note = document.getElementById("note");
      return {
        active: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null,
        qFocused: document.activeElement === q,
        qInWrapper: !!document.querySelector("#stzc-page #q"),
        mainInWrapper: !!document.querySelector("#stzc-page main"),
        qValue: q.value,
        qSelection: [q.selectionStart, q.selectionEnd],
        noteValue: note.value,
        noteSelection: [note.selectionStart, note.selectionEnd],
        events: q.mountFocusEvents,
      };
    });
    assert.equal(mounted.qFocused, true,
      "the field the person is typing in must still be the focused element after the delayed mount (active: " + mounted.active + ")");
    assert.equal(mounted.qInWrapper, true, "the page column with the field must live inside #stzc-page");
    assert.equal(mounted.mainInWrapper, true, "<main> must live inside #stzc-page");
    assert.deepEqual(mounted.qSelection, [4, 6], "the input's selected range must survive the delayed mount");
    assert.deepEqual(mounted.noteSelection, [0, 5], "the textarea's selected range must survive the delayed mount");
    assert.equal(mounted.qValue, "cardio", "the input's value must survive the delayed mount");
    assert.equal(mounted.noteValue, "dolor de cabeza", "the textarea's value must survive the delayed mount");
    assert.deepEqual(mounted.events, atomicMove ? [] : ["blur", "focus"],
      "atomic moves must not blur/refocus; the fallback must exercise actual restoration");
  });
});
}

for (const atomicMove of [true, false]) for (const fieldTag of ["input", "textarea"]) {
test(`keystrokes survive delayed widget mount: ${fieldTag}, atomic move ${atomicMove}`, async () => {
  await withPage(async (page) => {
    await page.addInitScript((enabled) => {
      if (!enabled) Object.defineProperty(Element.prototype, "moveBefore", { value: undefined, configurable: true });
    }, atomicMove);
    const { release, navigation } = await heldWidgetPage(page);
    assert.equal(await page.evaluate(() => typeof Element.prototype.moveBefore), atomicMove ? "function" : "undefined");
    await page.evaluate((tag) => {
      const q = document.createElement(tag);
      q.id = "q";
      if (tag === "input") q.type = "text";
      document.body.appendChild(document.createComment("body comment must survive wrapping"));
      document.querySelector("main").appendChild(q);
    }, fieldTag);
    await page.locator("#q").click();
    await page.keyboard.type("cardio");
    await page.keyboard.press("Shift+ArrowLeft");
    await page.keyboard.press("Shift+ArrowLeft");

    release.release();
    await navigation;
    await page.locator("#stz-chat-panel").waitFor({ state: "attached" });
    assert.equal(
      await page.evaluate(() => !!document.querySelector("#stzc-page #q")),
      true,
      "the delayed widget must have wrapped the page column for this proof to mean anything",
    );
    // Real keys only. No fill(), no click, no focus() — refocusing here would
    // hide exactly the defect this test exists to pin.
    await page.keyboard.type("!");
    assert.equal(
      await page.evaluate(() => document.getElementById("q").value),
      "card!",
      'keystrokes after the delayed mount must still land in the person\'s field (selection "io" replaced by "!")',
    );
  });
});
}

test("delayed widget mount without a focused field still wraps and mounts normally", async () => {
  await withPage(async (page) => {
    const { release, navigation } = await heldWidgetPage(page);
    await page.evaluate(() => {
      const q = document.createElement("input");
      q.id = "q";
      q.type = "text";
      document.querySelector("main").appendChild(q);
    });
    release.release();
    await navigation;
    await page.locator("#stz-chat-panel").waitFor({ state: "attached" });
    assert.deepEqual(await page.evaluate(() => ({
      wrapped: !!document.querySelector("#stzc-page #q"),
      panelAttached: !!document.getElementById("stz-chat-panel"),
      expanded: document.getElementById("stz-chat-launcher").getAttribute("aria-expanded"),
      bodyOpen: document.body.classList.contains("stzc-chat-open"),
    })), { wrapped: true, panelAttached: true, expanded: "false", bodyOpen: false });
  });
});
