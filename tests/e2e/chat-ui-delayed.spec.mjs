// Delayed native ChatUI module on the public widget. A source scan cannot
// prove the widget waits for platform/chat-ui.mjs before mounting a composer,
// queues an early launcher open, or shows localized recovery on rejection.
//
// Differences from the node:test original (tests/e2e/chat-ui-delayed.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), same setupHarness
//     options: requests + bookingFirstPaint). The pages the original opened with state.browser.newPage({
//     viewport }) each get their own context from newContext({ viewport }) (a fresh cache and storage per
//     page, closed at the end of its block), or the runner's `page` when the viewport is the default one
//     (1280x900 / 390x844 are set with page.setViewportSize before navigating). The tests are independent
//     and the file is not serial.
//   - The held module / widget are promise gates (holdGate) released by the test, as in the original. The
//     "chat-ui.mjs was requested" Promise.race against a setTimeout(20 s) is expect.poll over the URL
//     the route saw (20 s); the route handler no longer asserts the pathname itself (an exception in a
//     route handler would not fail the test): the same toMatch runs on the captured URL after the poll.
//   - openChat() waited a fixed 800 ms after the nav element attached, for the nav to measure itself "after
//     fonts settle". That is now the event it was waiting for: document.fonts.ready (the nav re-runs its
//     layout() on it, with a handler registered before ours) and two animation frames for the class
//     changes to be applied; then the launcher / nav button is chosen exactly as before.
//   - waitFor({state}) calls are expect(locator).toBeVisible/toBeAttached with the original timeouts.
//     Snapshots taken while the module is held (no composer yet, no global) and every "this is
//     absent" read stay point-in-time page.evaluate / count reads.
//   - The recovery evidence (screenshot + reachability JSON per width) keeps its directory under
//     artifactPath("human-rounds", "native-chat-widget", "booking-recovery", <time>) (~/.claude/artifacts/...
//     unless TURNOS_E2E_ARTIFACTS_DIR moves it; the original hard-wired the home directory); the screenshot is also
//     attached to the report (saveShot, same viewport-only shot as the original).
//   - The unconfigured-WhatsApp route is still checked with a separate bounded Node fetch (kept as is: the
//     original documents why the browser body read was not used).
//   - Locators: #stz-chat-launcher, #stz-chat-panel, #stzc-*, #chat-boot-error, .wz-seg-opt and the nav
//     selectors stay CSS: they are the widget's markup contract, and the reachability probe is
//     geometry (elementFromPoint, computed position).
import fs from "node:fs";
import path from "node:path";
import { test, expect, holdGate, saveShot, artifactPath } from "./lib/fixtures.mjs";

const MODULE_PATH = /\/static\/platform\/chat-ui\.[a-f0-9]{8}\.mjs$/;
const WIDGET_PATH = /\/static\/platform\/chat-widget\.[a-f0-9]{8}\.mjs$/;
const CLASSIC = /(?:^|\/)chat-ui\.js$/;
const RECOVERY_ARTIFACT_DIR = artifactPath("human-rounds",
  "native-chat-widget", "booking-recovery", new Date().toISOString().replaceAll(":", "-"));

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_chat_ui_delayed", requests: true, bookingFirstPaint: true } });

// The floating launcher yields to the nav's own Chat item whenever the nav
// bar is fully laid out (wide windows); on phones the launcher is the entry.
async function openChat(page) {
  // The nav measures itself after fonts settle; only then does the launcher
  // know whether it yields.
  await expect(page.locator("#stznav .stznav-inner")).toBeAttached({ timeout: 10_000 });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  const launcher = page.locator("#stz-chat-launcher");
  if (await launcher.isVisible()) return launcher.click();
  return page.locator('[data-nav-accion="abrir-chat"]:visible').first().click();
}

function observe(page, { expectedFailed = [] } = {}) {
  const failures = [];
  const requested = [];
  page.on("request", (request) => {
    requested.push(new URL(request.url()).pathname);
  });
  page.on("requestfailed", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/account/me" && request.failure()?.errorText === "net::ERR_ABORTED") return;
    if (expectedFailed.some((pattern) => pattern.test(path))) return;
    failures.push({ path, error: request.failure()?.errorText || "request failed" });
  });
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ path, status: response.status() });
  });
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  return { failures, requested };
}

async function bookingControlReachability(page, controlSelector) {
  return page.evaluate((selector) => {
    const control = document.querySelector(selector);
    const slots = [...document.querySelectorAll(".wz-seg-opt[data-slot][data-dia]")];
    const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    const covered = [];
    for (const slot of slots) {
      slot.scrollIntoView({ block: "end", inline: "nearest" });
      const slotRect = slot.getBoundingClientRect();
      const controlRect = control?.getBoundingClientRect();
      if (!controlRect || !overlaps(slotRect, controlRect)) continue;
      const x = (Math.max(slotRect.left, controlRect.left) + Math.min(slotRect.right, controlRect.right)) / 2;
      const y = (Math.max(slotRect.top, controlRect.top) + Math.min(slotRect.bottom, controlRect.bottom)) / 2;
      const target = document.elementFromPoint(x, y);
      covered.push({ text: slot.textContent.trim(), target: target?.id || target?.className || "unknown" });
    }
    const anchor = document.getElementById("stzc-booking-launcher-anchor");
    const help = document.querySelector(".wizard-help");
    return {
      controlPosition: control ? getComputedStyle(control).position : "missing",
      controlParent: control?.parentElement?.id || "",
      anchorHeight: anchor ? Math.round(anchor.getBoundingClientRect().height) : 0,
      anchorBeforeHelp: !!anchor && !!help && anchor.compareDocumentPosition(help) & Node.DOCUMENT_POSITION_FOLLOWING,
      covered,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
    };
  }, controlSelector);
}

/** Hold platform/chat-ui.mjs behind `gate`; getURL() is the URL the route saw (empty until requested). */
async function holdChatUi(page, gate) {
  let moduleURL = "";
  await page.route("**/static/platform/chat-ui.*.mjs", async (route) => {
    moduleURL = route.request().url();
    gate.arrive();
    await gate.held;
    await route.continue();
  });
  return { getURL: () => moduleURL };
}

test("booking bootstrap keeps delayed and rejected native chat controls out of synthetic schedule actions", async ({ newContext, app, cleanup }, testInfo) => {
  test.setTimeout(45_000);
  fs.mkdirSync(RECOVERY_ARTIFACT_DIR, { recursive: true });
  const bookingPath = "/service/medicina-general-riverside-clinic/book";
  for (const viewport of [
    { width: 360, height: 640 }, { width: 390, height: 844 },
    { width: 768, height: 1024 }, { width: 1024, height: 900 }, { width: 1280, height: 900 },
  ]) {
    const delayedContext = await newContext({ viewport });
    const delayed = await delayedContext.newPage();
    const gate = holdGate(cleanup);
    try {
      let widgetURL = "";
      await delayed.route("**/static/platform/chat-widget.*.mjs", async (route) => {
        widgetURL = route.request().url();
        await gate.held;
        await route.continue();
      });
      await delayed.goto(app.baseUrl + bookingPath, { waitUntil: "domcontentloaded", timeout: 20_000 });
      await expect(delayed.locator(".wz-seg-opt[data-slot][data-dia]").first()).toBeVisible({ timeout: 15_000 });
      expect(new URL(widgetURL).pathname).toMatch(WIDGET_PATH);
      const before = await bookingControlReachability(delayed, "#stz-chat-launcher");
      expect(before.covered, `delayed launcher covered a slot at ${viewport.width}px`).toEqual([]);
      expect(before.horizontalOverflow).toBe(false);
      expect(before.anchorBeforeHelp).toBeTruthy();
      if (viewport.width <= 1100) {
        expect(before.controlPosition).toBe("static");
        expect(before.controlParent).toBe("stzc-booking-launcher-anchor");
        expect(before.anchorHeight).toBe(76); // the anchor's reserved height (patient_web/site_*.py, 8/16/24/32 scale)
      }
    } finally {
      gate.release();
      await delayedContext.close();
    }

    const rejectedContext = await newContext({ viewport });
    const rejected = await rejectedContext.newPage();
    const seen = observe(rejected, { expectedFailed: [WIDGET_PATH] });
    try {
      await rejected.route("**/static/platform/chat-widget.*.mjs", (route) => route.abort("failed"));
      await rejected.goto(app.baseUrl + bookingPath, { waitUntil: "domcontentloaded", timeout: 20_000 });
      await expect(rejected.locator(".wz-seg-opt[data-slot][data-dia]").first()).toBeVisible({ timeout: 15_000 });
      await expect(rejected.locator("#chat-boot-error")).toBeVisible({ timeout: 10_000 });
      const failed = await bookingControlReachability(rejected, "#chat-boot-error");
      expect(failed.covered, `rejected-widget recovery covered a slot at ${viewport.width}px`).toEqual([]);
      expect(failed.horizontalOverflow).toBe(false);
      expect(failed.controlPosition).toBe("static");
      expect(failed.controlParent).toBe("stzc-booking-launcher-anchor");
      expect(failed.anchorBeforeHelp).toBeTruthy();
      expect(seen.failures).toEqual([]);
      await rejected.locator("#chat-boot-error").scrollIntoViewIfNeeded();
      await saveShot(testInfo, rejected, path.join(RECOVERY_ARTIFACT_DIR, `recovery-${viewport.width}.png`));
      fs.writeFileSync(path.join(RECOVERY_ARTIFACT_DIR, `reachability-${viewport.width}.json`),
        JSON.stringify({ viewport, rejected: failed }, null, 2));
    } finally {
      await rejectedContext.close();
    }
  }
});

test("home holds the composer until native ChatUI arrives, never the retired global or classic script", async ({ page, app, cleanup }) => {
  test.setTimeout(45_000);
  await page.setViewportSize({ width: 1280, height: 900 });
  const seen = observe(page);
  const gate = holdGate(cleanup);
  const held = await holdChatUi(page, gate);
  try {
    await page.goto(app.baseUrl + "/", { waitUntil: "domcontentloaded", timeout: 20_000 });
    await expect.poll(() => held.getURL(), { message: "platform/chat-ui.mjs was not requested from the manifest", timeout: 20_000 }).not.toBe("");
    expect(new URL(held.getURL()).pathname).toMatch(MODULE_PATH);

    const whileDelayed = await page.evaluate(() => ({
      launcher: !!document.getElementById("stz-chat-launcher"),
      composer: !!document.getElementById("stzc-form")?.querySelector("textarea"),
      legacyGlobal: typeof window.SanaChatUI,
      replacementGlobal: typeof window.ChatUI,
      widgetGlobal: typeof window.SanaChat,
      intent: document.getElementById("stz-chat-launcher")?.getAttribute("data-chat-intent"),
      manifestURL: window.SANA_STATIC?.["platform/chat-ui.mjs"] || "",
    }));
    expect(whileDelayed.launcher, "bootstrap launcher must be clickable while ChatUI is held").toBe(true);
    expect(whileDelayed.composer).toBe(false);
    expect(whileDelayed.legacyGlobal).toBe("undefined");
    expect(whileDelayed.replacementGlobal).toBe("undefined");
    expect(whileDelayed.widgetGlobal, "there is no SanaChat compatibility global").toBe("undefined");
    expect(whileDelayed.manifestURL).toMatch(MODULE_PATH);

    // This managed fixture has no WhatsApp integration configured. Opening
    // the real widget probes it; the native route deliberately returns 404.
    const whatsappResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/chat/whatsapp");
    await openChat(page);

    gate.release();

    await expect(page.locator("#stz-chat-launcher")).toBeAttached({ timeout: 15_000 });
    await expect(page.locator("#stz-chat-panel")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("#stzc-form textarea")).toBeVisible({ timeout: 8_000 });

    const after = await page.evaluate(() => ({
      legacyGlobal: typeof window.SanaChatUI,
      replacementGlobal: typeof window.ChatUI,
      widgetGlobal: typeof window.SanaChat,
      composer: !!document.querySelector("#stzc-form textarea"),
      rawKeys: /(?:^|\s)chat\.[a-z0-9_.]+(?:\s|$)/.test(document.getElementById("stz-chat-panel")?.innerText || ""),
    }));
    expect(after.legacyGlobal).toBe("undefined");
    expect(after.replacementGlobal).toBe("undefined");
    expect(after.widgetGlobal).toBe("undefined");
    expect(after.composer).toBe(true);
    expect(after.rawKeys).toBe(false);
    const whatsapp = await whatsappResponse;
    expect(whatsapp.status()).toBe(404);
    // Chromium's body retrieval for this intercepted browser context hung
    // after the completed 404 response. Validate the native response bytes
    // with a separately bounded local request, retaining the browser status
    // and missing-action assertions rather than ignoring optional failures.
    const optionalRoute = await fetch(app.baseUrl + "/api/chat/whatsapp", {
      signal: AbortSignal.timeout(5000),
    });
    expect(optionalRoute.status).toBe(404);
    expect(await optionalRoute.json()).toEqual({ error: "not found" });
    expect(await page.locator("#stzc-wa").isVisible(),
      "an unconfigured optional integration must not offer a broken action").toBe(false);
    expect(seen.requested.some((pathname) => CLASSIC.test(pathname))).toBe(false);
    expect(seen.failures).toEqual([{ path: "/api/chat/whatsapp", status: 404 }]);
  } finally {
    gate.release();
  }
});

test("optional composer actions honor hidden and visible availability at phone and desktop widths", async ({ newContext, app }) => {
  test.setTimeout(45_000);

  for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 900 }]) {
    const unavailableContext = await newContext({ viewport });
    const unavailable = await unavailableContext.newPage();
    try {
      const unavailableResponse = unavailable.waitForResponse((response) =>
        new URL(response.url()).pathname === "/api/chat/whatsapp");
      await unavailable.goto(app.baseUrl + "/", { waitUntil: "load", timeout: 20_000 });
      await openChat(unavailable);
      expect((await unavailableResponse).status()).toBe(404);
      const hiddenAction = unavailable.locator("#stzc-wa");
      await expect(hiddenAction).toBeAttached({ timeout: 8_000 });
      expect(await hiddenAction.evaluate((node) => ({
        hidden: node.hidden,
        display: getComputedStyle(node).display,
        width: node.getBoundingClientRect().width,
        height: node.getBoundingClientRect().height,
      })), `unconfigured action must occupy no composer space at ${viewport.width}px`)
        .toEqual({ hidden: true, display: "none", width: 0, height: 0 });
    } finally {
      await unavailableContext.close();
    }

    const availableContext = await newContext({ viewport });
    const available = await availableContext.newPage();
    try {
      await available.route("**/api/chat/whatsapp", (route) => route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ link: "https://wa.me/5492254000000" }),
      }));
      await available.goto(app.baseUrl + "/", { waitUntil: "load", timeout: 20_000 });
      await openChat(available);
      const visibleAction = available.locator("#stzc-wa:not([hidden])");
      await expect(visibleAction).toBeVisible({ timeout: 8_000 });
      const bounds = await visibleAction.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        return { display: getComputedStyle(node).display, width: rect.width, height: rect.height };
      });
      expect(bounds.display).not.toBe("none");
      expect(bounds.width > 0 && bounds.height > 0,
        `configured action must render with a tappable box at ${viewport.width}px`).toBe(true);
    } finally {
      await availableContext.close();
    }
  }
});

test("native ChatUI rejection keeps the mobile launcher open/close state truthful", async ({ page, app }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const seen = observe(page, { expectedFailed: [MODULE_PATH] });
  await page.route("**/static/platform/chat-ui.*.mjs", (route) => route.abort("failed"));
  await page.goto(app.baseUrl + "/", { waitUntil: "load", timeout: 20_000 });
  await expect(page.locator("#stz-chat-launcher")).toBeVisible({ timeout: 15_000 });
  await page.locator("#stz-chat-launcher").click();
  const alert = page.locator("#stz-chat-panel [role='alert']");
  await expect(alert).toBeVisible({ timeout: 8_000 });
  await expect(alert).toHaveText(/No pudimos conectar\. Probá de nuevo/);
  expect(await page.locator("#stzc-form textarea").count()).toBe(0);
  expect(await page.evaluate(() => typeof window.SanaChatUI)).toBe("undefined");
  expect(await page.evaluate(() => typeof window.SanaChat)).toBe("undefined");
  expect(await page.evaluate(() => ({
    expanded: document.getElementById("stz-chat-launcher").getAttribute("aria-expanded"),
    panelOpen: !document.getElementById("stz-chat-panel").hidden,
    bodyOpen: document.body.classList.contains("stzc-chat-open"),
    rootOpen: document.documentElement.classList.contains("stzc-chat-open-root"),
  }))).toEqual({ expanded: "true", panelOpen: true, bodyOpen: true, rootOpen: true });
  await page.locator(".stzc-close").click();
  expect(await page.evaluate(() => ({
    expanded: document.getElementById("stz-chat-launcher").getAttribute("aria-expanded"),
    panelOpen: !document.getElementById("stz-chat-panel").hidden,
    bodyOpen: document.body.classList.contains("stzc-chat-open"),
    rootOpen: document.documentElement.classList.contains("stzc-chat-open-root"),
  }))).toEqual({ expanded: "false", panelOpen: false, bodyOpen: false, rootOpen: false });
  expect(seen.requested.some((pathname) => CLASSIC.test(pathname))).toBe(false);
  expect(seen.failures).toEqual([]);
});
