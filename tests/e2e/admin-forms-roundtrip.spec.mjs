// Durable writes through the actual admin controls, ASGI and an isolated PostgreSQL:
// legal documents, editable prompts, the Wiki (initial-read lifecycle, malformed
// reads, focus trap, create/update/disable), Settings (general cards, integration
// cards, secrets), the account forms (email, password, TOTP), the intake toggle,
// patients and dependents, the appointment modal, appearance, doctor onboarding
// (profile, schedule, portrait) and the usability findings. Every write goes to the
// real server and is cold-reloaded; one acknowledgement is delayed for lifecycle
// coverage. External AI and background jobs stay off.
//
// Port of tests/e2e/admin-forms-roundtrip.e2e.mjs (21 tests, same titles, same order).
//
// Differences from the node:test original:
// - Session: the runner's own context + `adminPage` fixture (fresh per test, signed in
//   as the harness admin) replaces the hand-made `session(t)`. Tests that need a second
//   account use `browser.newContext()` (closed through the `cleanup` fixture); the
//   «malformed successful reads» test opens a fresh page in the admin context per
//   response instead of a fresh context + login per response (the Wiki keeps nothing
//   in local/session storage, so the page load is equally cold).
// - `t.after(...)` becomes a local `cleanup(fn)` fixture (runs LIFO even when the test
//   fails or times out): it releases held routes, closes extra contexts, tears down
//   extra apps and removes the generated doctor photo files.
// - The three tests that need their own app (hsi, emr_bridge, usability: other
//   connector/env options) start it inside the test with `startApp` and tear it down
//   in `cleanup`; they get a 120 s timeout because the app boot counts against it.
// - The hand-made failure diagnostics were dropped: the «Wiki stalled initial read»
//   `.catch` block (section state + URL) and the onboarding `try/catch` (invalid
//   inputs, disabled state, module resource). The runner's trace-on-first-retry
//   records the DOM, network and console of a failing retry, which covers the same
//   ground. The `t.diagnostic(...)` lines are kept as test annotations.
// - Screenshots that went to ~/.claude/artifacts are attached to the report with
//   `testInfo.attach` (Wiki recovery frames, doctor onboarding frames).
// - Waits and assertions are web-first (`expect(locator)...`, auto-retrying) where the
//   original polled a selector and then compared; the gating (a held route, released
//   after the intermediate inert/aria-busy assertions) is unchanged. «Must not be» checks the
//   original read once (absent editor, released inert/body lock, `data-pending` != 1) stay so.
// - The original's per-test `{ timeout: 30000/20000 }` become `test.setTimeout`, and
//   its `page.setDefaultTimeout(10000)` is the config's actionTimeout/expect timeout
//   (10 s) with a 20 s navigation timeout.
// - The assertion inside the held doctor-save route handler is checked from the test
//   body (the handler resolves with the status), not thrown inside the handler.
import fs from "node:fs/promises";
import {
  test, expect, loginAdmin, write, totpCode, holdGate, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({
  // RENAPER is a choice only with its credentials or the fake (2026-11-08 D8).
  appOptions: { dbName: "sana_turnos_test_forms_roundtrip", env: { TURNOS_FAKE_RENAPER: "1" } },
  viewport: { width: 1280, height: 900 },
});

const KNOWLEDGE_API = "/api/admin/content/knowledge";
const isKnowledgeApi = (url) => url.pathname === KNOWLEDGE_API;
const PHOTO_FIXTURE = new URL("../fixtures/center-photo-jfxx.jpeg", import.meta.url).pathname;

/** Hold the first Wiki list GET after the server has answered, until released. */
async function holdKnowledgeRead(page, g) {
  await page.route(isKnowledgeApi, async (route) => {
    const response = await route.fetch(); g.arrive(); await g.held;
    await route.fulfill({ response }).catch(() => {});
  }, { times: 1 });
}

/** A fresh browser context of the runner (extra account / extra app). */
async function openContext(browser, baseURL, cleanup) {
  const context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 900 } });
  cleanup(() => context.close());
  return context;
}

const note = (testInfo, description) => testInfo.annotations.push({ type: "diagnostic", description });

test("Legal: both documents save, cold reload and restore real stored bodies", async ({ adminPage: page }) => {
  const listed = await (await page.request.get("/api/admin/legal")).json();
  expect(listed.items.map((item) => item.id).sort()).toEqual(["privacidad", "terminos"]);
  for (const item of listed.items) {
    await page.goto("/admin/legal?doc=" + item.id);
    await expect(page.locator("#lg-cuerpo")).toBeVisible();
    const original = await page.inputValue("#lg-cuerpo");
    const text = original + "\n\nSynthetic round-trip " + item.id + ".";
    await page.fill("#lg-cuerpo", text);
    await write(page, "/api/admin/legal/" + item.id, () => page.click("#lg-guardar"));
    await expect(page.locator("#lg-guardar:not([disabled])")).toBeVisible();
    await page.reload();
    await expect(page.locator("#lg-cuerpo")).toBeVisible();
    await expect(page.locator("#lg-cuerpo")).toHaveValue(text);
    page.once("dialog", (dialog) => dialog.accept());
    await write(page, "/api/admin/legal/" + item.id, () => page.click("#lg-restaurar"));
    await expect(page.locator("#lg-restaurar")).toHaveCount(0);
    await page.reload();
    await expect(page.locator("#lg-cuerpo")).toBeVisible();
    await expect(page.locator("#lg-cuerpo")).toHaveValue(original);
  }
});

test("Prompts: English and Spanish save and cold reload without a substituted acknowledgement", async ({ adminPage: page }) => {
  const listed = await (await page.request.get("/api/admin/prompts")).json();
  const key = listed.items[0].clave;
  await page.goto("/admin/prompts?prompt=" + key);
  await expect(page.locator("#pr-en")).toBeAttached();
  const original = await page.inputValue("#pr-en");
  await page.locator('[data-tab="en"]').click();
  await page.fill("#pr-en", original + "\nSynthetic round trip.");
  await write(page, "/api/admin/prompts/" + key + "/guardar", () => page.click("#pr-guardar"));
  await expect(page.locator("#pr-guardar:not([disabled])")).toBeVisible();
  await page.reload();
  await expect(page.locator("#pr-en")).toBeAttached();
  await expect(page.locator("#pr-en")).toHaveValue(original + "\nSynthetic round trip.");
  await page.locator('[data-tab="trad"]').click();
  await expect(page.locator("#pr-es:not([readonly])")).toBeVisible();
  const spanish = await page.inputValue("#pr-es");
  await page.fill("#pr-es", spanish + "\nPrueba sintética persistente.");
  await write(page, "/api/admin/prompts/" + key + "/guardar", () => page.click("#pr-guardar"));
  await expect(page.locator("#pr-guardar:not([disabled])")).toBeVisible();
  await page.reload();
  await expect(page.locator("#pr-es:not([readonly])")).toBeAttached();
  await expect(page.locator("#pr-es")).toHaveValue(spanish + "\nPrueba sintética persistente.");
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/prompts/" + key, () => page.click("#pr-restaurar"));
  await expect(page.locator("#pr-restaurar")).toHaveCount(0);
  await page.reload();
  await expect(page.locator("#pr-en")).toBeAttached();
  await expect(page.locator("#pr-en")).toHaveValue(original);
});

test("Wiki initial read keeps creation inert until real data is ready", async ({ adminPage: page, cleanup }) => {
  const g = holdGate(cleanup);
  await holdKnowledgeRead(page, g);
  await page.goto("/admin/knowledge", { waitUntil: "domcontentloaded" });
  await g.arrived;
  const root = page.locator("#sec-knowledge");
  await expect(root).toHaveAttribute("aria-busy", "true");
  await expect(root).toHaveAttribute("inert", "");
  await expect(page.locator('[data-kn-new="categoria"]').first().click({ timeout: 300 })).rejects.toThrow();
  expect(await page.locator("#kn-form").count(), "no editable draft before initial data").toBe(0);
  g.release();
  await expect(page.locator("#sec-knowledge:not([inert]):not([aria-busy])")).toBeVisible();
  await page.locator('[data-kn-new="categoria"]').first().click();
  await page.fill('#kn-form [name="title"]', "Synthetic category after load");
  const ack = await write(page, KNOWLEDGE_API, () => page.locator('#kn-form button[type="submit"]').click(), true);
  expect(ack.ok).toBe(true);
  expect(ack.item.id).toBeTruthy();
  await page.waitForURL((url) => url.searchParams.get("item") === ack.item.id);
  await page.reload();
  await expect(page.locator("#kn-form")).toBeVisible();
  await expect(page.locator('#kn-form [name="title"]')).toHaveValue("Synthetic category after load");
});

test("Wiki retired initial read cannot render or unlock a remounted owner", async ({ adminPage: page, cleanup }, testInfo) => {
  test.setTimeout(30_000);
  await page.addInitScript(() => {
    const original = window.fetch;
    window.wikiReadSettled = [];
    window.fetch = function (...args) {
      const result = original.apply(this, args);
      if (String(args[0]).endsWith("/api/admin/content/knowledge")) {
        const index = window.wikiReadSettled.length;
        window.wikiReadSettled.push(false);
        result.then(() => { window.wikiReadSettled[index] = true; }, () => { window.wikiReadSettled[index] = "failed"; });
      }
      return result;
    };
  });
  const pending = [];
  let notify;
  const firstRequested = new Promise((resolve) => { notify = resolve; });
  cleanup(() => pending.forEach((item) => item.release()));
  await page.route(isKnowledgeApi, async (route) => {
    const response = await route.fetch();
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    pending.push({ release }); notify(); await held;
    await route.fulfill({ response }).catch(() => {});
  });
  await page.goto("/admin/knowledge", { waitUntil: "domcontentloaded" });
  await firstRequested; note(testInfo, "First real GET is held");
  const secondRequested = new Promise((resolve) => { notify = resolve; });
  await page.evaluate(async () => {
    const controller = await import(window.SANA_STATIC["platform/admin-knowledge.mjs"]);
    controller.destroy();
    const root = document.getElementById("sec-knowledge");
    if (root.hasAttribute("inert") || root.hasAttribute("aria-busy")) throw new Error("destroy must release initial loading attributes synchronously");
    root.setAttribute("aria-busy", "false");
    window.wikiRemount = controller.mount();
  });
  note(testInfo, "Controller remounted");
  await secondRequested; note(testInfo, "Second real GET is held");
  pending[0].release();
  await page.waitForFunction(() => window.wikiReadSettled[0] === true);
  note(testInfo, "Old GET headers reached the retired consumer");
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const root = page.locator("#sec-knowledge");
  expect(await root.getAttribute("inert")).toBe("");
  expect(await root.getAttribute("aria-busy"), "retired finally cannot release the current read").toBe("true");
  expect(await page.locator("#kn-form").count(), "retired response cannot publish an editor").toBe(0);
  pending[1].release(); await page.evaluate(() => window.wikiRemount); note(testInfo, "Current GET released");
  expect(await root.getAttribute("inert")).toBeNull();
  await expect(root, "restore the previous attribute value exactly").toHaveAttribute("aria-busy", "false");
  await page.locator('[data-kn-new="categoria"]').first().click();
  await page.fill('#kn-form [name="title"]', "Current owner remains usable");
});

test("Wiki stalled initial read offers usable reload instead of permanent inert", async ({ adminPage: page, cleanup, app }, testInfo) => {
  test.setTimeout(20_000);
  const g = holdGate(cleanup);
  await holdKnowledgeRead(page, g);
  await page.goto("/admin/knowledge", { waitUntil: "domcontentloaded" });
  await g.arrived;
  await expect(page.locator("#sec-knowledge [data-kn-retry]")).toBeVisible();
  expect(await page.locator("#sec-knowledge").getAttribute("inert")).toBeNull();
  expect(await page.locator("#sec-knowledge").getAttribute("aria-busy")).toBeNull();
  const retry = page.locator("[data-kn-retry]");
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await testInfo.attach("wiki-recovery-error-" + width + ".png", {
      body: await page.screenshot({ fullPage: true }), contentType: "image/png",
    });
    const box = await stableBox(retry, `wiki retry button at ${width}`);
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(await retry.evaluate((node) => parseFloat(getComputedStyle(node).fontSize) >= 16)).toBe(true);
  }
  // The hold has done its job (its handler is still parked on the gate). Leaving the route
  // installed made every request of the reloaded document pass through the interception layer
  // with a handler of the old document still pending, and about 3 runs in 100 left one of them
  // (a module, or the Wiki list itself) unanswered although the server never saw it. Without
  // routes the reload goes straight to the network: 100 of 100 runs.
  await page.unroute(isKnowledgeApi);
  const reload = page.waitForNavigation({ waitUntil: "domcontentloaded" });
  const reloadStart = Date.now();
  // Requests of the reloaded document that never finished (the failing runs left one or two
  // unanswered with the page shell already drawn).
  const unfinished = new Map();
  const track = (request) => unfinished.set(request, new URL(request.url()).pathname);
  const done = (request) => unfinished.delete(request);
  page.on("request", track); page.on("requestfinished", done); page.on("requestfailed", done);
  await retry.click(); await reload;
  g.release();
  // If a request of the reloaded document is ever left unanswered again, the failure says
  // which one, what the section looked like, and what the server saw.
  try {
    await page.locator('#sec-knowledge:not([inert]) [data-kn-new="categoria"]').first().click({ timeout: 12_000 });
  } catch (error) {
    const section = await page.evaluate(() => {
      const node = document.querySelector("#sec-knowledge");
      return node && {
        inert: node.getAttribute("inert"), busy: node.getAttribute("aria-busy"),
        retry: node.querySelectorAll("[data-kn-retry]").length,
        newButtons: node.querySelectorAll('[data-kn-new="categoria"]').length,
        text: node.textContent.trim().slice(0, 120),
      };
    }).catch((reason) => "unreadable: " + reason.message);
    throw new Error(error.message + "\nreloaded Wiki: " + JSON.stringify({
      sinceReloadClickMs: Date.now() - reloadStart, section, url: page.url(),
      unfinishedRequests: [...unfinished.values()],
      serverLogTail: app.getServerLog().split("\n").slice(-25),
    }));
  }
  await page.fill('#kn-form [name="title"]', "Usable after reload");
});

test("Wiki failed initial read releases loading state and its reload recovers", async ({ adminPage: page }) => {
  await page.route(isKnowledgeApi, async (route) => {
    await route.fetch();
    await route.fulfill({ status: 500, json: { error: "Synthetic read failure" } });
  }, { times: 1 });
  await page.goto("/admin/knowledge", { waitUntil: "domcontentloaded" });
  await expect(page.locator("#sec-knowledge [data-kn-retry]")).toBeVisible();
  expect(await page.locator("#sec-knowledge").getAttribute("inert")).toBeNull();
  const reload = page.waitForNavigation({ waitUntil: "domcontentloaded" });
  await page.locator("[data-kn-retry]").click(); await reload;
  await page.locator('#sec-knowledge:not([inert]) [data-kn-new="categoria"]').first().click();
  await page.fill('#kn-form [name="title"]', "Recovered from failed read");
});

test("Wiki malformed successful reads are errors rather than false empty editors", async ({ adminPage }) => {
  for (const response of [{ json: {} }, { json: null }, { json: { items: {} } }, { body: "not json", contentType: "application/json" }]) {
    const page = await adminPage.context().newPage();
    try {
      await page.route(isKnowledgeApi, async (route) => {
        await route.fetch();
        await route.fulfill({ status: 200, ...response });
      }, { times: 1 });
      await page.goto("/admin/knowledge", { waitUntil: "domcontentloaded" });
      await expect(page.locator("#sec-knowledge [data-kn-retry]")).toBeVisible();
      expect(await page.locator("#kn-form").count(), "malformed list cannot claim the Wiki is empty").toBe(0);
      expect(await page.locator("#sec-knowledge").getAttribute("inert")).toBeNull();
    } finally {
      await page.close();
    }
  }
});

test("Wiki malformed article rendering recovers and does not retain invalid list state", async ({ adminPage: page }) => {
  const listed = await (await page.request.get(KNOWLEDGE_API)).json();
  const article = listed.items.find((item) => item.type === "articulo");
  expect(article?.id).toBeTruthy();
  await page.route(isKnowledgeApi, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.items.find((item) => item.id === article.id).tags = {};
    await route.fulfill({ response, json: body });
  }, { times: 1 });
  await page.goto("/admin/knowledge?item=" + encodeURIComponent(article.id), { waitUntil: "domcontentloaded" });
  await expect(page.locator("#sec-knowledge [data-kn-retry]")).toBeVisible();
  expect(await page.locator("#sec-knowledge").getAttribute("inert")).toBeNull();
  await page.evaluate(async () => { const m = await import(window.SANA_STATIC["platform/admin-knowledge.mjs"]); m.destroy(); await m.mount(); });
  await expect(page.locator("#kn-form")).toBeVisible();
  await expect(page.locator('#kn-form [name="title"]'), "remount must read fresh data rather than cache malformed items").toHaveValue(article.title);
  await expect(page.locator("#kn-tags")).toHaveValue((article.tags || []).join(", "));
});

test("Wiki phone dialog releases covered elements and body lock on destroy", async ({ adminPage: page, cleanup }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const listed = await (await page.request.get(KNOWLEDGE_API)).json();
  expect(listed.items[0]?.id, "synthetic pack contains a real Wiki item").toBeTruthy();
  const id = listed.items[0].id;
  const g = holdGate(cleanup);
  await holdKnowledgeRead(page, g);
  await page.goto("/admin/knowledge?item=" + encodeURIComponent(id), { waitUntil: "domcontentloaded" });
  await g.arrived; g.release();
  await expect(page.locator("#sec-knowledge:not([inert]) #kn-form")).toBeVisible();
  const title = page.locator('#kn-form [name="title"]');
  await expect(title, "held read releases root before phone focus enters").toBeFocused();
  const editorHasFocus = () => page.locator("#kn-editor").evaluate((node) => node.contains(document.activeElement));
  await page.keyboard.press("Shift+Tab");
  expect(await editorHasFocus()).toBe(true);
  await page.keyboard.press("Tab");
  expect(await editorHasFocus()).toBe(true);
  const save = page.locator(".kn-save");
  for (let index = 0; index < 20; index++) {
    if (await save.evaluate((node) => document.activeElement === node)) break;
    await page.keyboard.press("Tab");
  }
  await expect(save).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator(".kn-more>summary"), "Tab from Save reaches More actions").toBeFocused();
  await page.keyboard.press("Enter"); await page.keyboard.press("Tab");
  const lastAction = page.locator("[data-kn-delete], [data-kn-restore]");
  await expect(lastAction, "keyboard reaches the actual menu action").toBeFocused();
  await page.keyboard.press("Tab");
  await expect(title, "last action wraps forward to first control").toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(lastAction, "first control wraps backward to actual last action").toBeFocused();
  await page.evaluate(async () => { const m = await import(window.SANA_STATIC["platform/admin-knowledge.mjs"]); m.destroy(); });
  expect(await page.locator("body").evaluate((node) => node.classList.contains("kn-modal-open")), "destroy releases body scroll").toBe(false);
  expect(await page.locator("[inert]").count(), "destroy releases covered chrome").toBe(0);
  await page.evaluate(async () => { const m = await import(window.SANA_STATIC["platform/admin-knowledge.mjs"]); await m.mount(); });
  await page.keyboard.press("Escape");
  expect(await page.locator("body").evaluate((node) => node.classList.contains("kn-modal-open"))).toBe(false);
  expect(await page.locator("#kn-tree a").evaluateAll(
    (nodes, wanted) => nodes.some((node) => new URL(node.href).searchParams.get("item") === wanted && document.activeElement === node), id)).toBe(true);
});

test("Wiki: category and article create, update, cold reload and disable retain database identity", async ({ adminPage: page }) => {
  const ids = {};
  for (const type of ["categoria", "articulo"]) {
    await page.goto("/admin/knowledge");
    await page.locator('[data-kn-new="' + type + '"]').first().click();
    await page.fill('#kn-form [name="title"]', "Synthetic " + type);
    if (type === "articulo") {
      await page.fill('#kn-form [name="content"]', "Synthetic article body.");
      await page.locator("#kn-form details").first().evaluate((node) => { node.open = true; });
      await page.locator("#kn-jurisdiction").selectOption("interno");
      await page.locator("#kn-parent_id").selectOption(ids.categoria);
      await page.fill("#kn-tags", "synthetic, roundtrip");
    }
    const ack = await write(page, KNOWLEDGE_API, () => page.locator('#kn-form button[type="submit"]').click(), true);
    expect(ack.ok === true && ack.item && typeof ack.item.id === "string"
      && (type === "categoria" || typeof ack.item.content === "string"), "real Wiki acknowledgement must match its item type").toBe(true);
    await page.waitForURL((url) => !!url.searchParams.get("item"));
    const id = new URL(page.url()).searchParams.get("item"); ids[type] = id;
    await page.reload();
    await expect(page.locator("#kn-form")).toBeVisible();
    await expect(page.locator('#kn-form [name="title"]')).toHaveValue("Synthetic " + type);
    if (type === "articulo") {
      await expect(page.locator('#kn-form [name="content"]')).toHaveValue("Synthetic article body.");
      await expect(page.locator("#kn-parent_id")).toHaveValue(ids.categoria);
      await expect(page.locator("#kn-tags")).toHaveValue("synthetic, roundtrip");
    }
    await page.fill('#kn-form [name="title"]', "Updated " + type);
    await write(page, KNOWLEDGE_API + "/" + id, () => page.locator('#kn-form button[type="submit"]').click());
    await expect(page.locator('#kn-form button[type="submit"]:not([disabled])')).toBeVisible();
    await page.reload();
    await expect(page.locator("#kn-form")).toBeVisible();
    await expect(page.locator('#kn-form [name="title"]')).toHaveValue("Updated " + type);
    await expect(page.locator('#kn-form [name="id"]')).toHaveValue(id);
  }
  await page.locator(".kn-more").evaluate((node) => { node.open = true; });
  await write(page, KNOWLEDGE_API + "/" + ids.articulo, () => page.click("[data-kn-delete]"));
  await page.reload();
  await expect(page.locator("#kn-tree")).toBeVisible();
  const stored = await (await page.request.get(KNOWLEDGE_API)).json();
  expect(stored.items.filter((item) => item.id === ids.articulo && !item._borrado)).toHaveLength(0);
});

test("Settings: each general card persists through its real independent save and reload", async ({ adminPage: page }) => {
  for (const [group, key, value] of [
    ["location", "SITE_ID_HOSPITAL", "Synthetic Roundtrip Hospital"],
    ["chat", "CHAT_GREETING_ES", "Hola. Prueba sintética persistente."],
    ["turnos", "MAX_ACTIVE_REQUESTS", "3"],
    ["seguridad", "IPS_EXENTAS", "127.0.0.1\n::1"],
  ]) {
    await page.goto("/admin/settings");
    const input = page.locator('[data-key="' + key + '"]'); await expect(input).toBeVisible();
    await input.fill(value);
    await write(page, "/api/admin/settings", () => page.locator('.set-card[data-grupo="' + group + '"] .set-guardar').click());
    await expect(page.locator('[data-msg="' + group + '"]').filter({ hasText: "Guardado" })).toBeVisible();
    await page.reload(); await expect(input).toBeVisible();
    await expect(input).toHaveValue(value);
  }
  const english = page.locator('[data-lang-check][value="en"]');
  const languageBefore = await english.isChecked();
  await english.setChecked(!languageBefore);
  await write(page, "/api/admin/settings", () => page.locator('.set-card[data-grupo="languages"] .set-guardar').click());
  await expect(page.locator('[data-msg="languages"]').filter({ hasText: "Guardado" })).toBeVisible();
  await page.reload();
  await expect(english).toBeChecked({ checked: !languageBefore });
  const photo = page.locator('[data-key="TURNOS_PHOTO_AUTO_ENHANCE"]');
  const before = await photo.isChecked(); await photo.setChecked(!before);
  await write(page, "/api/admin/settings", () => page.locator('.set-card[data-grupo="turnos"] .set-guardar').click());
  await expect(page.locator('[data-msg="turnos"]').filter({ hasText: "Guardado" })).toBeVisible();
  await page.reload();
  await expect(photo).toBeChecked({ checked: !before });
});

test("Settings: every available integration card saves locally; synthetic secret state survives reload and deletion", async ({ adminPage: page }) => {
  // No Test/Probe buttons, live destinations, real credentials or enablement.
  const fields = {
    backup: ["TURNOS_BACKUP_S3_PREFIX", "synthetic/forms/"],
    openrouter: ["TURNOS_AI_MODEL", "synthetic/roundtrip-model"],
    email: ["MAILGUN_DOMAIN", "mail.example.invalid"],
    turnstile: ["TURNOS_TURNSTILE_SITEKEY", "synthetic-site-key"],
    whatsapp: ["TURNOS_WA_NUMERO", "+5490000000000"],
    puco: ["TURNOS_SISA_URL", "https://example.invalid/ws"],
    renaper: ["TURNOS_RENAPER_LABEL", "Synthetic identity provider"],
    b2: ["TURNOS_B2_PREFIX", "synthetic/forms/"],
    // The dictation card holds one field and it is a secret: what survives the reload is
    // «configured» (the delete control), never the value.
    fal: ["FAL_KEY", "synthetic-nonfunctional-key", "secret"],
  };
  await page.goto("/admin/settings?view=integrations");
  await expect(page.locator("[data-integracion-card]").first()).toBeVisible();
  const cards = await page.locator("[data-integracion-card]").evaluateAll((nodes) => nodes.map((node) => node.dataset.integracionCard));
  expect([...cards].sort(), "every available integration card is explicitly exercised").toEqual(Object.keys(fields).sort());
  for (const id of cards) {
    const [key, value, kind] = fields[id];
    const card = page.locator('[data-integracion-card="' + id + '"]');
    await card.locator('input[data-key="' + key + '"], select[data-key="' + key + '"], textarea[data-key="' + key + '"]').fill(value);
    await write(page, "/api/admin/settings", () => card.locator(".set-guardar").click());
    await expect(page.locator('[data-msg="api_keys-' + id + '"]').filter({ hasText: "Guardado" })).toBeVisible();
    await page.reload(); await expect(card).toBeVisible();
    if (kind === "secret") {
      const secretRow = card.locator('[data-campo="' + key + '"]');
      await expect(secretRow.locator(".set-borrar")).toBeVisible();
      await expect(secretRow.locator("input")).toHaveValue("");
      await write(page, "/api/admin/settings", () => secretRow.locator(".set-borrar").click());
      await expect(secretRow.locator(".set-borrar")).toHaveCount(0);
      continue;
    }
    await expect(card.locator('[data-key="' + key + '"]')).toHaveValue(value);
  }
  const key = "MAILGUN_API_KEY";
  await page.locator('[data-key="' + key + '"][data-tipo="secret"]').fill("synthetic-nonfunctional-key");
  await write(page, "/api/admin/settings", () => page.locator('[data-integracion-card="email"] .set-guardar').click());
  await expect(page.locator('[data-msg="api_keys-email"]').filter({ hasText: "Guardado" })).toBeVisible();
  await page.reload();
  const row = page.locator('[data-campo="' + key + '"]');
  await expect(row.locator(".set-borrar")).toBeVisible();
  expect(await row.locator("input").inputValue(), "a configured secret is never returned to the input").toBe("");
  const metadata = await (await page.request.get("/api/admin/settings")).json();
  expect(metadata.api_keys.find((card) => card.id === "email").campos.find((field) => field.clave === key).configurado).toBe(true);
  await write(page, "/api/admin/settings", () => row.locator(".set-borrar").click());
  await expect(row.locator(".set-borrar")).toHaveCount(0);
  await page.reload(); await expect(row).toBeVisible();
  expect(await row.locator(".set-borrar").count()).toBe(0);
});

test("Account: actual email, password and TOTP forms persist for a temporary staff account", async ({ adminPage, browser, app, cleanup }) => {
  const username = "synthetic.staff";
  const first = "SyntheticPassword93", changed = "SyntheticChanged94";
  expect((await adminPage.request.post("/api/admin/users", {
    data: { usuario: username, password: first, rol: "admin", email: "before@example.invalid" },
  })).status()).toBe(200);
  const staff = await openContext(browser, app.baseUrl, cleanup);
  const page = await staff.newPage();
  await page.goto("/admin/login?next=/admin/account");
  await page.fill("#gate-user", username); await page.fill("#gate-pass", first); await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname === "/admin/account");
  await page.click("#acct-email-edit");
  await page.fill("#acct-email", "after@example.invalid");
  await write(page, "/api/admin/account/profile", () => page.locator('#acct-email-form button[type="submit"]').click());
  await expect(page.locator("#acct-email-status").filter({ hasText: "guardado" })).toBeVisible();
  await page.reload(); await expect(page.locator("#acct-email-value")).toHaveText("after@example.invalid");
  await expect(page.locator("#acct-email")).toHaveValue("after@example.invalid");
  await page.click("#acct-password-edit");
  await page.fill("#acct-pass-current", first); await page.fill("#acct-pass-new", changed); await page.fill("#acct-pass-confirm", changed);
  await write(page, "/api/admin/account/password", () => page.locator('#acct-password-form button[type="submit"]').click());
  const fresh = await openContext(browser, app.baseUrl, cleanup);
  expect((await fresh.request.post("/api/admin/login", { data: { usuario: username, password: first } })).status()).toBe(401);
  expect((await fresh.request.post("/api/admin/login", { data: { usuario: username, password: changed } })).status()).toBe(200);
  const gate = await (await openContext(browser, app.baseUrl, cleanup)).newPage();
  await gate.goto("/admin/login?next=/admin/account");
  await gate.fill("#gate-user", username); await gate.fill("#gate-pass", changed); await gate.click("#gate-btn");
  await gate.waitForURL((url) => url.pathname === "/admin/account");
  await page.reload();
  const setup = await write(page, "/api/admin/account/2fa/start", () => page.click("#acct-2fa-start"), true);
  await page.fill("#acct-confirm-code", await totpCode(setup.secret));
  await write(page, "/api/admin/account/2fa/confirm", () => page.locator('#acct-confirm-form button[type="submit"]').click());
  await expect(page.locator("#acct-2fa-quitar")).toBeVisible();
  await page.reload(); await expect(page.locator("#acct-2fa-quitar")).toBeVisible();
  expect((await (await staff.request.get("/api/admin/account")).json()).tiene_2fa).toBe(true);
  await page.click("#acct-2fa-quitar"); await page.fill("#acct-remove-pass", changed);
  await write(page, "/api/admin/account/2fa/remove", () => page.locator('#acct-remove-form button[type="submit"]').click());
  await expect(page.locator("#acct-2fa-start")).toBeVisible();
  await page.reload(); await expect(page.locator("#acct-2fa-start")).toBeVisible();
  expect((await (await staff.request.get("/api/admin/account")).json()).tiene_2fa).toBe(false);
});

test("Intake: the rendered doctor enable checkbox saves and cold reloads both states", async ({ adminPage: page }) => {
  await page.goto("/admin/entrevistas");
  const first = page.locator("[data-effective-medico]").first(); await expect(first).toBeVisible();
  const id = await first.getAttribute("data-effective-medico");
  const checkbox = page.locator('[data-effective-medico="' + id + '"]');
  const original = await checkbox.isChecked();
  for (const enabled of [!original, original]) {
    await write(page, "/api/admin/intake/settings", () => checkbox.setChecked(enabled));
    await expect(checkbox).toBeVisible();
    await page.reload(); await expect(checkbox).toBeVisible();
    await expect(checkbox).toBeChecked({ checked: enabled });
  }
});

test("Patients: create, edit and dependent forms cold reload their actual stored values", async ({ adminPage: page }) => {
  const dni = "30111888";
  await page.goto("/admin/pacientes?new=1");
  await page.fill("#pat-dni", dni); await page.fill("#pat-name", "Synthetic");
  await page.fill("#pac-apellido", "Patient"); await page.fill("#pat-birth", "01/02/1990");
  await page.locator("#pat-sex").selectOption("F");
  await page.fill("#pac-contacto", "+5492254000000");
  await write(page, "/api/admin/patients", () => page.click("#pac-save"));
  await expect(page.locator("#pac-out code")).toBeVisible();
  await page.goto("/admin/pacientes?patient=" + dni);
  await page.click("#pac-edit");
  await page.fill("#pat-email", "synthetic.patient@example.invalid");
  await write(page, "/api/admin/patients/" + dni, () => page.click("#pac-save"));
  await page.waitForURL((url) => url.pathname === "/admin/pacientes" && !url.search);
  await page.goto("/admin/pacientes?patient=" + dni);
  await page.reload(); await page.click("#pac-edit"); await expect(page.locator("#pat-email")).toBeVisible();
  await expect(page.locator("#pat-email")).toHaveValue("synthetic.patient@example.invalid");
  await expect(page.locator("#pat-name")).toHaveValue("Synthetic");
  await page.click("#pac-dep-add");
  await page.fill("#dep-dni", "60111888"); await page.fill("#dep-nombre", "Synthetic");
  await page.fill("#dep-apellido", "Child"); await page.fill("#dep-nac", "01/02/2020");
  await page.locator("#dep-sexo").selectOption("F");
  await write(page, "/api/admin/patients/" + dni + "/dependents", () => page.click("#dep-save"));
  await page.reload(); await expect(page.locator('[data-dep-quitar="60111888"]')).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/admin/patients/" + dni + "/dependents/60111888", () => page.click('[data-dep-quitar="60111888"]'));
  await page.reload(); await expect(page.locator("#pac-dep-add")).toBeVisible();
  expect(await page.locator('[data-dep-quitar="60111888"]').count()).toBe(0);
});

test("Appointment modal: embedded new-patient form persists before a real scheduled booking", async ({ adminPage: page }) => {
  const dni = "30111777";
  await page.goto("/admin/cola");
  // «Crear turno» is in the server's first document, but its click is heard only once the queue screen has
  // mounted (a dynamic import after «load»); a click before that is lost and no modal ever opens.
  await expect(page.locator("#sec-cola:not([aria-busy])")).toBeVisible();
  await page.click("#adm-crear-turno");
  await page.fill("#ct-pac", dni);
  await page.click("#ct-pac-sug li[data-alta]");
  await page.fill("#ct-alta-apellido", "Modal"); await page.fill("#ct-alta-nombre", "Synthetic");
  await page.fill("#ct-alta-nac", "01/02/1990"); await page.locator("#ct-alta-sexo").selectOption("F");
  await write(page, "/api/admin/patients", () => page.click("#ct-alta-ok"));
  await page.fill("#ct-svc", "medicina");
  await page.click('#ct-svc-sug [data-svc="medicina-general-riverside-clinic"]');
  await expect(page.locator(".ct-prop-cuando").first()).toBeVisible();
  // The service owns its schedule and has several doctors: the desk names one
  // (launch plan P1) — the first of the service's list.
  if (await page.locator("#ct-med-turno").count()) await page.selectOption("#ct-med-turno", { index: 1 });
  const bookingRequest = page.waitForRequest((request) => new URL(request.url()).pathname === "/api/admin/appointments" && request.method() === "POST");
  const booked = await write(page, "/api/admin/appointments", () => page.click("#ct-ok"), true);
  expect(booked.ok).toBe(true);
  const selected = (await bookingRequest).postDataJSON();
  const stored = (await (await page.request.get("/api/admin/requests?status=aprobada")).json()).solicitudes.find((row) => row.id === booked.id);
  expect(stored, "independent read finds the created appointment").toBeTruthy();
  expect(stored.dni).toBe(dni); expect(stored.servicio_id).toBe(selected.servicio_id);
  expect(stored.medico_asignado).toBe(selected.medico || null);
  expect(new Date(stored.turno_dt).getTime()).toBe(new Date(selected.dia + "T" + selected.hora + ":00-03:00").getTime());
  await page.goto("/admin/cola"); await page.reload();
  await page.locator("[data-cola-buscar]").fill(selected.dia);
  const savedRow = page.locator('[data-cola-row="' + booked.id + '"]');
  const group = page.locator('button[data-cola-doctor="' + (stored.medico_asignado_id || "_sin_medico") + '"]');
  if (await group.count()) await group.click();
  await expect(savedRow).toBeVisible(); await expect(savedRow).toContainText(/Synthetic/, { useInnerText: true });
  await page.goto("/admin/pacientes?patient=" + dni);
  await page.reload(); await page.click("#pac-edit"); await expect(page.locator("#pat-name")).toBeVisible();
  await expect(page.locator("#pat-name")).toHaveValue("Synthetic");
  await expect(page.locator("#pac-apellido")).toHaveValue("Modal");
});

test("Appearance: actual theme and patient sign-in mode selections survive a cold reload", async ({ adminPage: page }) => {
  await page.goto("/admin/appearance");
  const nextTheme = page.locator('[data-tema][aria-pressed="false"]').first();
  const theme = await nextTheme.getAttribute("data-tema");
  await write(page, "/api/admin/theme", () => Promise.all([
    page.waitForNavigation({ waitUntil: "load" }), nextTheme.click(),
  ]));
  await expect(page.locator('[data-tema="' + theme + '"][aria-pressed="true"]')).toBeVisible();
  await page.reload();
  await expect(page.locator('[data-tema="' + theme + '"][aria-pressed="true"]')).toBeVisible();
  const nextMode = page.locator('[data-auth][aria-pressed="false"]').first();
  const mode = await nextMode.getAttribute("data-auth");
  await write(page, "/api/admin/auth-mode", () => nextMode.click());
  await expect(page.locator('[data-auth="' + mode + '"][aria-pressed="true"]')).toBeVisible();
  await page.reload();
  await expect(page.locator('[data-auth="' + mode + '"][aria-pressed="true"]')).toBeVisible();
});

test("Doctor onboarding: profile, own schedule and local portrait save and cold reload", async ({ adminPage, browser, app, cleanup }, testInfo) => {
  const doctors = await (await adminPage.request.get("/api/admin/content/doctors")).json();
  expect(doctors.items.length).toBeGreaterThan(0);
  const id = "forms-synthetic-" + Date.now();
  const centerId = id + "-center";
  expect((await adminPage.request.post("/api/admin/content/centers", {
    data: { slug: centerId, name: "Synthetic Onboarding Center" },
  })).status()).toBe(200);
  // A new center has no service-owned schedule. The doctor edits only this
  // run's synthetic agenda, without resetting any existing service or doctor.
  const doctor = { ...doctors.items[0], id, nombre: "Synthetic Doctor", nombre_pila: "Synthetic", apellido: "Doctor",
    centros: [{ centro: centerId, franjas: [{ dias: [1], desde: "08:00", hasta: "12:00", cada_min: 20 }], excepciones: [] }] };
  for (const key of Object.keys(doctor)) if (key.startsWith("_")) delete doctor[key];
  const created = await adminPage.request.post("/api/admin/content/doctors", { data: doctor });
  expect(created.status()).toBe(200);
  // Médico = usuario (2026-10-19): the create already made the doctor's one
  // account (inactive, «sin invitar»); a second account for it is a 409.
  // Hand that account a known password and switch it on.
  const { cuenta } = await created.json();
  expect(cuenta && cuenta.usuario, "the doctor create returns its account").toBeTruthy();
  expect((await adminPage.request.post("/api/admin/users", {
    data: { usuario: id, password: ADMIN_PASS, rol: "medico", medico_id: id },
  })).status(), "one account per doctor").toBe(409);
  const login = cuenta.usuario;
  expect((await adminPage.request.put("/api/admin/users/" + encodeURIComponent(login), {
    data: { password: ADMIN_PASS, activo: true },
  })).status()).toBe(200);
  const staff = await openContext(browser, app.baseUrl, cleanup);
  const page = await staff.newPage();
  const moduleGate = holdGate(cleanup);
  const modulePattern = /\/static\/platform\/doctor-workspace\.[a-f0-9]+\.mjs$/;
  await page.route(modulePattern, async (route) => { await moduleGate.held; await route.continue().catch(() => {}); });
  await page.goto("/admin/login");
  await page.fill("#gate-user", login); await page.fill("#gate-pass", ADMIN_PASS); await page.click("#gate-btn");
  await page.waitForURL((url) => url.pathname === "/admin/onboarding");
  await expect(page.locator("#doctor-workspace"), "SSR form cannot accept input before its handlers mount").toHaveJSProperty("inert", true);
  await expect(page.locator("[data-finish]").click({ timeout: 200 })).rejects.toThrow(/Timeout/);
  moduleGate.release();
  await expect(page.locator('#doctor-workspace[aria-busy="false"]')).toBeVisible();
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.evaluate(() => scrollTo(0, 0));
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await testInfo.attach("doctor-onboarding-" + width + ".png", {
      body: await page.screenshot({ fullPage: true }), contentType: "image/png",
    });
  }
  await page.fill('[data-profile] [name="telefono"]', "+5492254000011");
  // The onboarding cannot finish without an email (Médico = usuario).
  if (!(await page.inputValue('[data-profile] [name="email"]'))) await page.fill('[data-profile] [name="email"]', id + "@hospital.example");
  const interval = page.locator('[data-schedule] [data-time="cada_min"]').first();
  await interval.fill("25");
  let releaseSave, wroteSave;
  const heldSave = new Promise((resolve) => { releaseSave = resolve; });
  const savedOnServer = new Promise((resolve) => { wroteSave = resolve; });
  cleanup(() => releaseSave());
  const savePath = "/api/admin/content/doctors/" + id;
  await page.route((url) => url.pathname === savePath, async (route) => {
    const response = await route.fetch();
    wroteSave(response.status());
    await heldSave;
    await route.fulfill({ response }).catch(() => {});
  }, { times: 1 });
  await page.click("[data-finish]");
  expect(await savedOnServer, "the held doctor save was accepted by the server").toBe(200);
  await expect(page.locator("[data-finish]")).toBeDisabled();
  await page.evaluate(() => {
    dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
  await expect(page.locator('#doctor-workspace[aria-busy="false"]')).toBeVisible();
  expect(await page.locator("[data-finish]").isEnabled(), "remount releases the retired controller save lock").toBe(true);
  expect(await page.locator("[data-finish-section]").getAttribute("data-pending")).not.toBe("1");
  releaseSave();
  await write(page, "/api/admin/onboarding/complete", () => page.click("[data-finish]"));
  // Without a second factor the finished onboarding lands on the account's
  // security step (bb0df62e); the doctor's own queue is one click away.
  await page.waitForURL((url) => url.pathname === "/admin/account");
  await page.goto("/admin/cola");
  await expect(page.locator("[data-entrevista-toggle]")).toBeVisible();
  await page.evaluate(() => {
    window.__formToasts = [];
    const original = window.toast;
    window.toast = (message) => { window.__formToasts.push(message); if (original) original(message); };
  });
  for (const [status, body] of [[500, "{}"], [200, "{}"], [200, "not-json"], [500, '{"ok":true}']]) {
    const count = await page.evaluate(() => window.__formToasts.length);
    await page.route("**/api/admin/intake/my-settings", (route) => route.fulfill({ status, body, contentType: "application/json" }), { times: 1 });
    await page.click("[data-entrevista-toggle]");
    await page.waitForFunction((n) => window.__formToasts.length > n, count);
    expect(await page.evaluate(() => window.__formToasts.at(-1)), "failed/malformed acknowledgement never claims success").toMatch(/No se pudo guardar/);
  }
  const beforeIntake = await (await staff.request.get("/api/admin/intake/my-settings")).json();
  for (const enabled of [!beforeIntake.efectivo, beforeIntake.efectivo]) {
    await expect(page.locator('[data-entrevista-toggle="' + Number(enabled) + '"]')).toBeVisible();
    await write(page, "/api/admin/intake/my-settings", () => page.click("[data-entrevista-toggle]"));
    await page.reload();
    await expect(page.locator('[data-entrevista-toggle="' + Number(!enabled) + '"]')).toBeVisible();
    const persisted = await (await staff.request.get("/api/admin/intake/my-settings")).json();
    expect(persisted.override).toBe(enabled); expect(persisted.efectivo).toBe(enabled);
  }
  await page.goto("/admin/onboarding");
  await expect(page.locator('#doctor-workspace[aria-busy="false"]')).toBeVisible();
  await expect(page.locator('[data-profile] [name="telefono"]')).toBeVisible();
  await expect(page.locator('[data-profile] [name="telefono"]')).toHaveValue("+5492254000011");
  await expect(interval).toHaveValue("25");
  // Storage is shared by the local server, so use a unique synthetic doctor
  // namespace and remove only this run's generated files after the browser.
  cleanup(async () => {
    await fs.rm(new URL("../../data/medico-fotos/" + id + ".webp", import.meta.url), { force: true });
    await fs.rm(new URL("../../data/medico-fotos/.originals/" + id, import.meta.url), { force: true, recursive: true });
  });
  const photoPath = "/api/admin/doctors/" + id + "/photo";
  await write(page, photoPath, () => page.setInputFiles("[data-upload]", PHOTO_FIXTURE));
  await expect(page.locator('[data-photo-preview][style*="background-image"]')).toBeVisible();
  await page.reload(); await expect(page.locator('#doctor-workspace[aria-busy="false"]')).toBeVisible();
  await expect(page.locator('[data-photo-preview][style*="background-image"]')).toBeVisible();
  expect((await (await staff.request.get(photoPath)).json()).has_upload).toBe(true);
  await page.goto("/admin/account");
  await expect(page.locator("#mifoto-foto-quitar")).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/mi/foto", () => page.click("#mifoto-foto-quitar"));
  await page.reload(); await expect(page.locator("#mifoto-foto-input")).toBeAttached();
  await expect(page.locator("#mifoto-foto-quitar")).toBeHidden();
  expect((await (await staff.request.get(photoPath)).json()).has_upload).toBe(false);
  await write(page, "/api/mi/foto", () => page.setInputFiles("#mifoto-foto-input", PHOTO_FIXTURE));
  await expect(page.locator("#mifoto-foto-quitar")).toBeVisible();
  await page.reload(); await expect(page.locator("#mifoto-foto-quitar")).toBeVisible();
  expect((await (await staff.request.get(photoPath)).json()).has_upload).toBe(true);
  page.once("dialog", (dialog) => dialog.accept());
  await write(page, "/api/mi/foto", () => page.click("#mifoto-foto-quitar"));
  await page.reload(); await expect(page.locator("#mifoto-foto-input")).toBeAttached();
  await expect(page.locator("#mifoto-foto-quitar")).toBeHidden();
  expect((await (await staff.request.get(photoPath)).json()).has_upload, "Catalog begins without an uploaded portrait").toBe(false);
  await adminPage.goto("/admin/doctors?item=" + id);
  await adminPage.locator('[data-tab="info"]').click();
  await write(adminPage, photoPath, () => adminPage.setInputFiles("#med-foto-input", PHOTO_FIXTURE));
  await adminPage.reload();
  await adminPage.locator('[data-tab="info"]').click();
  await expect(adminPage.locator("#med-foto-quitar")).toBeVisible();
  expect((await (await adminPage.request.get(photoPath)).json()).has_upload).toBe(true);
  await page.unroute(modulePattern);
  await page.route(modulePattern, (route) => route.abort(), { times: 1 });
  await page.goto("/admin/onboarding", { waitUntil: "domcontentloaded" });
  await expect(page.locator("#doctor-recovery button")).toBeVisible();
  await expect(page.locator("#doctor-workspace")).toHaveJSProperty("inert", true);
  await page.locator("#doctor-recovery button").click();
  await expect(page.locator('#doctor-workspace[aria-busy="false"]')).toBeVisible();
  await expect(page.locator('[data-profile] [name="telefono"]')).toHaveValue("+5492254000011");
});

for (const [connector, key] of [["hsi", "HSI_URL"], ["emr_bridge", "EMR_BRIDGE_URL"]]) {
  test(`Settings: ${connector} card saves in an isolated connector-selected installation without probes`, async ({ browser, cleanup, launchApp }) => {
    test.setTimeout(120_000);
    const fixture = await launchApp({ dbName: "sana_turnos_test_forms_" + connector, env: { CONNECTOR_EMR_SYNC: connector } });
    const context = await openContext(browser, fixture.baseUrl, cleanup);
    const page = await context.newPage();
    await loginAdmin(page, fixture.baseUrl);
    await page.goto("/admin/settings?view=integrations");
    const input = page.locator('[data-integracion-card="' + connector + '"] [data-key="' + key + '"]');
    await input.fill("https://example.invalid/" + connector);
    await write(page, "/api/admin/settings", () => page.locator('[data-integracion-card="' + connector + '"] .set-guardar').click());
    await expect(page.locator('[data-msg="api_keys-' + connector + '"]').filter({ hasText: "Guardado" })).toBeVisible();
    await page.reload(); await expect(input).toBeVisible();
    await expect(input).toHaveValue("https://example.invalid/" + connector);
  });
}

test("Usability: actual finding create, edit and bulk status persist after hard reload", async ({ browser, cleanup, launchApp }) => {
  test.setTimeout(120_000);
  const fixture = await launchApp({ dbName: "sana_turnos_test_forms_usability", env: { TURNOS_STUDY: "1", TURNOS_DEMO_MODE: "1" } });
  const context = await openContext(browser, fixture.baseUrl, cleanup);
  const page = await context.newPage();
  await loginAdmin(page, fixture.baseUrl);
  await page.goto("/admin/usability?tab=findings");
  await page.locator("#usability .ux-actions").first().locator("button").last().click();
  await page.fill("#ux-title", "Synthetic persistence finding");
  await page.fill("#ux-detail", "A synthetic local finding for form acceptance.");
  const created = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/admin/usability/findings" && r.request().method() === "POST");
  await page.locator('#ux-editor button[type="submit"]').click();
  expect((await created).status()).toBe(201);
  const id = (await (await created).json()).id;
  await expect(page.locator("#ux-editor")).toHaveCount(0);
  await page.reload();
  const row = page.locator("tr").filter({ hasText: "Synthetic persistence finding" });
  await row.locator(".ux-status").click();
  await page.locator("#ux-status").selectOption("fixed");
  await page.fill("#ux-fix_note", "Verified local fix");
  await page.fill("#ux-live_url", "https://example.invalid/fix");
  await write(page, "/api/admin/usability/findings/" + id, () => page.locator('#ux-editor button[type="submit"]').click());
  await expect(page.locator("#ux-editor")).toHaveCount(0);
  await page.reload(); await expect(row).toBeVisible();
  await expect(row).toContainText(/Verified local fix/, { useInnerText: true });
  await row.locator('input[type="checkbox"]').check();
  const bulk = page.locator("#usability form.ux-actions");
  await bulk.locator("select").selectOption("in_progress");
  await write(page, "/api/admin/usability/findings/" + id, () => bulk.locator('button[type="submit"]').click());
  await page.reload();
  await expect(row.locator(".ux-status")).toContainText("En curso");
  const report = await (await page.request.get("/api/admin/usability")).json();
  expect(report.findings.find((item) => item.id === id).status).toBe("in_progress");
});
