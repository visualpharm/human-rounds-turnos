// Public chrome acceptance: all data is the disposable example-town fixture.
// It covers actual public routes, then the real directory renderer with two
// deliberately intercepted catalog responses that make avatar cardinality
// unambiguous. Evidence never touches production or patient data.
//
// Port of tests/e2e/public-chrome.e2e.mjs (18 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app is the worker-scoped `app` fixture (same dbName, default options). Every route
//   of the multi-route tests still gets its own browser context with the test's viewport
//   (`newContext`, closed at the end of the route) instead of `browser.newPage`, so no
//   state leaks between routes. Evidence screenshots go through `saveShot` (same files
//   under the artifacts tree, also attached to the report).
// - THE MEASURING MOMENT OF THE FOOTER CHECKS IS THE ORIGINAL'S, NOT A LATER ONE. The width
//   and short-page tests probe the layout exactly once, right after the original's last
//   gate (`load`, the nav and the footer visible and, on `/account`, the first field of the
//   form in place), and the gates are `locator.waitFor`, as in the original, not
//   `expect(...).toBeVisible()` (which polls a little differently and would move the probe
//   later). Nothing here polls the measurement until it turns green. The probe itself
//   (`chromeProbe`) and every threshold are unchanged. See «What turns it red» below.
// - The fixed `waitForTimeout`s that let the nav settle (600/300 ms in the highlight and
//   compact-menu tests, 500 ms in the launcher test) are a wait until the geometry that
//   test then measures is the same on two consecutive samples 100 ms apart, after fonts
//   (`untilStable`). The 3.5 s sleep that outlasts core/theme.py's 3 s cache is a poll of
//   the served `<html data-theme>` until it is the wanted theme. The 50 ms inside the
//   Chat-launcher click counter is the measurement window of «exactly once» and stays.
// - Waits that only gate a later check (`waitFor` on the burger/menu/language, the
//   directory card) are web-first. Snapshots that ARE the claim stay point-in-time reads:
//   the footer probe above, «menu closed right after Escape», the click count, the hover
//   comparison taken right after hovering, the launcher-over-footer probe.
// - The `assert.match` that ran INSIDE the language-injecting route handler is checked in
//   the test body (the handler records whether the nav module tag was found).
// - The per-route context is closed in a `finally` (as the page was); the theme tests
//   restore the default theme through `cleanup`. Test timeouts: 120 s for the width sweeps, 240 s for the launcher sweeps
//   (the original had none).
// - Locators: ids/classes stay CSS (#stznav, .stznav-*, footer, .dir-card, #stzc-page ...)
//   because the checks are about geometry, computed style and markup contract.
//
// What turns it red: a page that really leaves room under the footer. With the chat widget
// of before fc2a27a3 (it reserved the room for the floating chat button twice) the width
// test fails at 360 and 390 px with «/account?mode=login at 360px leaves 72px under the
// footer», on the single probe taken once the form is in place; main has the fix, so the
// port is green there. The probe is deliberately NOT repeated or delayed: moving it later
// than the original's gate would change which state of the page is judged.
import path from "node:path";
import {
  test, expect, saveShot, artifactPath, setTheme, note, patientCookie,
} from "./lib/fixtures.mjs";

// `account`: one patient with an open session, for the signed-in header.
test.use({ appOptions: { dbName: "sana_turnos_test_e2e_public_chrome", account: true } });
const EVIDENCE = artifactPath("human-rounds", "public-chrome-2026-09-14", "candidate-browser");
const WIDTHS = [360, 390, 768, 1024, 1280, 1440];
const ROUTES = ["/", "/privacy.html", "/terms.html", "/about.html", "/centers", "/account?mode=login"];

function failuresFor(page) {
  const failures = [];
  page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
  page.on("requestfailed", (request) => failures.push({ requestfailed: new URL(request.url()).pathname, error: request.failure()?.errorText }));
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname;
    if (pathname === "/api/account/me" && response.status() === 401) return;
    if (response.status() >= 400) failures.push({ status: response.status(), pathname });
  });
  return failures;
}

async function chromeProbe(page) {
  return page.evaluate(() => {
    const footer = document.querySelector("footer");
    const nav = document.getElementById("stznav");
    const style = footer && getComputedStyle(footer);
    const viewport = document.documentElement.clientWidth;
    // The footer is separated from the content by ONE hairline divider (the product owner,
    // 2026-10-17, human-rounds.css «COMPACT FOOTER»). On the home page the
    // specialties strip right above it carries that divider and the footer
    // drops its own, so the divider is whichever of the two owns it.
    const prev = footer && footer.previousElementSibling;
    const dividerOwner = style && style.borderTopWidth === "0px" && prev && prev.classList.contains("browse") ? prev : footer;
    const divider = dividerOwner && getComputedStyle(dividerOwner);
    // Name what sticks out, so a failure says where to look.
    const culprits = [...document.body.querySelectorAll("*")]
      .filter((el) => el.getBoundingClientRect().right > viewport + 1)
      .slice(0, 4)
      .map((el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
        (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : "") +
        " in " + (el.parentElement ? el.parentElement.tagName.toLowerCase() + "." + String(el.parentElement.className || "").trim().split(/\s+/).join(".") : "") +
        " «" + (el.textContent || "").trim().slice(0, 40) + "»" +
        " right=" + Math.round(el.getBoundingClientRect().right));
    // Room left under the footer: the page is at least one viewport tall, and a
    // short page keeps its footer on the bottom edge (human-rounds.css, the
    // stretched `.wrap`), so this stays ~0 whether the content is short or long.
    const footerGap = footer
      ? Math.max(window.innerHeight, document.documentElement.scrollHeight)
        - (footer.getBoundingClientRect().bottom + window.scrollY)
      : null;
    return {
      culprits,
      footerGap: footerGap === null ? null : Math.round(footerGap),
      overflow: document.documentElement.scrollWidth - viewport,
      nav: !!nav,
      footer: !!footer,
      footerStyle: style && {
        divider: divider.borderTopWidth + " " + divider.borderTopStyle,
        borderRight: style.borderRightWidth,
        borderBottom: style.borderBottomWidth,
        borderLeft: style.borderLeftWidth,
        radius: style.borderRadius,
        shadow: style.boxShadow,
        background: style.backgroundColor,
      },
    };
  });
}

// A page in its own browser context with the test's viewport, as `browser.newPage` was.
async function openPage(newContext, viewport) {
  const context = await newContext({ viewport });
  return { page: await context.newPage(), close: () => context.close() };
}

// Wait until `measure()` returns the same value on two consecutive samples 100 ms apart
// (after the fonts are in): the nav has finished laying out. Replaces the original's
// fixed waits.
async function untilStable(page, measure, message) {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  let last = null;
  await expect.poll(async () => {
    const current = JSON.stringify(await measure());
    const same = current === last;
    last = current;
    return same;
  }, { message, intervals: [100] }).toBe(true);
}

async function injectLanguages(page, pathname = "/") {
  const injected = { found: false };
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== pathname || route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = await response.text();
    const bootstrap = '<script>window.SANA_I18N={active:"en",enabled:["es","en","pt","fr","it","de","ru","uk","ja","zh"],mode:"nav",default:"en"};</script>';
    // The shared nav is the fingerprinted native module (platform/nav.mjs),
    // not the retired classic deferred nav.js. The ten-language bootstrap
    // goes right before it — after the server's own SANA_I18N script — and
    // the injection must actually happen, or the test proves nothing.
    const navModule = /<script type="module" src="\/static\/platform\/nav\.[a-f0-9]+\.mjs"><\/script>/;
    injected.found = navModule.test(body);
    await route.fulfill({ response, body: body.replace(navModule, bootstrap + "$&") });
  });
  return injected;
}

for (const width of WIDTHS) {
  test(`public shared chrome fits ${width}px across public surfaces`, async ({ newContext }, testInfo) => {
    test.setTimeout(120_000);
    for (const route of ROUTES) {
      const { page, close } = await openPage(newContext, { width, height: 900 });
      const failures = failuresFor(page);
      try {
        await page.goto(route, { waitUntil: "load", timeout: 20_000 });
        await page.locator("#stznav .stznav-inner").waitFor({ state: "visible", timeout: 10_000 });
        await page.locator("footer").waitFor({ state: "visible", timeout: 10_000 });
        // /account renders its form client-side (account-form.mjs), usually
        // after `load`. Measure the page a person actually ends up seeing: the
        // form in place inside the chat widget's #stzc-page wrapper, whose
        // phone form-page room is what can stack under the footer.
        if (route.startsWith("/account")) {
          await page.locator("#stzc-page #cuenta-host form input").first().waitFor({ state: "visible", timeout: 10_000 });
        }
        const probe = await chromeProbe(page);
        expect(probe.nav, `${route} must retain the shared header`).toBe(true);
        expect(probe.footer, `${route} must retain its footer`).toBe(true);
        expect(Math.abs(probe.footerGap),
          `${route} at ${width}px leaves ${probe.footerGap}px under the footer, which belongs on the bottom edge`).toBeLessThanOrEqual(1);
        expect(probe.overflow, `${route} overflows ${width}px by ${probe.overflow}px: ${probe.culprits.join(", ")}`).toBeLessThanOrEqual(1);
        expect(probe.footerStyle, `${route} footer must remain page continuation behind one hairline divider, not a card`).toEqual({
          divider: "1px solid", borderRight: "0px", borderBottom: "0px", borderLeft: "0px",
          radius: "0px", shadow: "none", background: "rgba(0, 0, 0, 0)",
        });
        expect(failures, `${route} runtime failures`).toEqual([]);
        if (route === "/" || route === "/terms.html") {
          const name = route === "/" ? "home" : "terms";
          await saveShot(testInfo, page, path.join(EVIDENCE, `${name}-${width}.png`), { fullPage: true });
        }
      } finally { await close(); }
    }
  });
}

// A page shorter than the window keeps its footer on the bottom edge (body at
// full height, `.wrap` stretched: human-rounds.css, product owner 2026-09-15/10-17).
// 2400px is taller than every one of these pages, so the stretch is what puts
// it there. The home page is left out: its desktop layout has never stretched.
for (const width of [390, 1280]) {
  test(`public footer stays on the bottom edge of a short page at ${width}px`, async ({ newContext }) => {
    test.setTimeout(120_000);
    for (const route of ROUTES.filter((r) => r !== "/")) {
      const { page, close } = await openPage(newContext, { width, height: 2400 });
      try {
        await page.goto(route, { waitUntil: "load", timeout: 20_000 });
        await page.locator("footer").waitFor({ state: "visible", timeout: 10_000 });
        const probe = await chromeProbe(page);
        expect(Math.abs(probe.footerGap),
          `${route} at ${width}px leaves ${probe.footerGap}px under the footer, which belongs on the bottom edge`).toBeLessThanOrEqual(1);
      } finally { await close(); }
    }
  });
}

test("phone keyboard menu, language selector, and Chat bridge use the shared controls", async ({ newContext }, testInfo) => {
  const { page, close } = await openPage(newContext, { width: 390, height: 844 });
  const failures = failuresFor(page);
  try {
    const injected = await injectLanguages(page);
    await page.goto("/?source=public-chrome#section", { waitUntil: "load", timeout: 20_000 });
    expect(injected.found, "home must load the fingerprinted native nav module").toBe(true);
    await page.locator(".stznav-burger").focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#stznav.open")).toBeAttached({ timeout: 3_000 });
    const language = page.locator(".stznav-language");
    await expect(language).toHaveCount(1);
    await expect(language.locator("option")).toHaveCount(10);
    await expect(language).toHaveValue("en");
    await expect(page.locator('[data-nav-accion="abrir-chat"]')).toHaveCount(1);
    await page.keyboard.press("Escape");
    expect(await page.locator("#stznav").evaluate((node) => node.classList.contains("open"))).toBe(false);
    const clicks = await page.evaluate(() => new Promise((resolve) => {
      let count = 0;
      const launcher = document.getElementById("stz-chat-launcher");
      launcher.addEventListener("click", () => { count += 1; }, { capture: true });
      document.querySelector('[data-nav-accion="abrir-chat"]').click();
      setTimeout(() => resolve(count), 50);
    }));
    expect(clicks, "menu Chat must activate the one existing floating launcher exactly once").toBe(1);
    await page.locator(".stznav-burger").focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#stznav.open")).toBeAttached({ timeout: 3_000 });
    await language.selectOption("es");
    await page.waitForURL(/\?source=public-chrome&lang=es#section/, { timeout: 10_000 });
    // The hermetic example-town fixture deliberately has no WhatsApp number;
    // its established chat contract is a 404 for this optional affordance.
    expect(failures.filter((failure) => failure.pathname !== "/api/chat/whatsapp")).toEqual([]);
    await saveShot(testInfo, page, path.join(EVIDENCE, "phone-menu-chat-language.png"), { fullPage: true });
  } finally { await close(); }
});

// The hover/active highlight IS the link's own box, so the label must sit in
// the middle of it. A `display:block` on the compact dropdown links once left
// the text ~10px above center, which read as a misaligned highlight.
for (const width of [390, 768, 1100, 1440]) {
  test(`menu highlights center their label at ${width}px, open and hovered`, async ({ newContext }) => {
    const { page, close } = await openPage(newContext, { width, height: 900 });
    try {
      await page.goto("/upload-order", { waitUntil: "load", timeout: 20_000 });
      await page.locator("#stznav .stznav-link").first().waitFor({ state: "attached", timeout: 10_000 });
      const measure = () => page.evaluate(() => [...document.querySelectorAll("#stznav .stznav-link, #stznav .stznav-btn")]
        .filter((node) => node.getBoundingClientRect().width > 0)
        .map((node) => {
          const box = node.getBoundingClientRect();
          const range = document.createRange();
          range.selectNodeContents(node);
          const text = range.getBoundingClientRect();
          return {
            label: node.textContent.trim(),
            top: box.top, height: box.height, left: box.left, width: box.width,
            offset: (text.top + text.bottom) / 2 - (box.top + box.bottom) / 2,
          };
        }));
      await untilStable(page, measure, `the menu items at ${width}px never stopped moving`);
      const burger = page.locator(".stznav-burger");
      if (await burger.isVisible()) {
        await burger.click();
        await expect(page.locator("#stznav.open")).toBeAttached({ timeout: 3_000 });
        // The dropdown slides in from translateY(-4px) (nav.mjs); under load the
        // transition can start late, after three identical reads, so wait for the
        // panel to reach its resting place before measuring.
        await expect.poll(() => page.evaluate(() => {
          const panel = document.querySelector(document.getElementById("stznav").classList.contains("stznav--compact")
            ? "#stznav .stznav-content" : "#stznav .stznav-menu");
          const style = getComputedStyle(panel);
          return style.opacity === "1" && ["none", "matrix(1, 0, 0, 1, 0, 0)"].includes(style.transform);
        }), { message: `the open menu at ${width}px never reached its resting place` }).toBe(true);
        await untilStable(page, measure, `the open menu at ${width}px never stopped moving`);
      }
      const before = await measure();
      expect(before.length, `expected the menu items to render at ${width}px, got ${before.length}`).toBeGreaterThanOrEqual(4);
      for (const item of before) {
        expect(Math.abs(item.offset),
          `"${item.label}" label sits ${item.offset.toFixed(1)}px off the center of its highlight at ${width}px`).toBeLessThanOrEqual(1.5);
      }
      // Inside the open dropdown every row (links and the chat button) shares
      // one left content edge; the button used to sit 6px right of the links.
      if (await burger.isVisible()) {
        const lefts = await page.evaluate(() => {
          // Compact: the dropdown is .stznav-content (the order button stays in the bar).
          // Collapsed: the whole .stznav-menu is the dropdown.
          const panel = document.getElementById("stznav").classList.contains("stznav--compact") ? ".stznav-content" : ".stznav-menu";
          return [...document.querySelectorAll(`#stznav ${panel} .stznav-link, #stznav ${panel} .stznav-btn`)]
            .filter((node) => node.getBoundingClientRect().width > 0)
            .map((node) => ({ label: node.textContent.trim(), left: Math.round(node.getBoundingClientRect().left), top: Math.round(node.getBoundingClientRect().top) }));
        });
        // Ingresar and Registrarse share one row: only each row's first item sits on the edge.
        const rowStarts = lefts.filter((item) => !lefts.some((other) => other.top === item.top && other.left < item.left));
        const edge = Math.min(...rowStarts.map((item) => item.left));
        for (const item of rowStarts) {
          expect(Math.abs(item.left - edge),
            `"${item.label}" starts ${item.left - edge}px off the dropdown's left edge at ${width}px`).toBeLessThanOrEqual(1);
        }
      }
      const links = page.locator("#stznav .stznav-link:visible");
      for (let index = 0; index < await links.count(); index += 1) await links.nth(index).hover();
      const after = await measure();
      expect(after.map(({ label, top, height, left, width: w }) => ({ label, top, height, left, w })),
        `hovering menu items must not move or resize them at ${width}px`)
        .toEqual(before.map(({ label, top, height, left, width: w }) => ({ label, top, height, left, w })));
    } finally { await close(); }
  });
}

// Product owner 2026-10-03: the visible logo glyph shares the page's left edge (the
// logo.png carries internal air, so the box itself sits left of the text);
// the menu is compact: no Talleres item (it is a tab inside Centros), Chat is
// sparkles + one word, and the language switcher is name + chevron, unboxed.
for (const width of [390, 1440]) {
  test(`compact public menu at ${width}px: logo aligned, no Talleres, Chat, unboxed language`, async ({ newContext }) => {
    const { page, close } = await openPage(newContext, { width, height: 900 });
    try {
      // The language switcher only renders where the page carries SANA_I18N
      // (the harness injects it on the home page, like the test above).
      const injected = await injectLanguages(page);
      await page.goto("/", { waitUntil: "load", timeout: 20_000 });
      expect(injected.found, "home must load the fingerprinted native nav module").toBe(true);
      await page.locator("#stznav .stznav-link").first().waitFor({ state: "attached", timeout: 10_000 });
      await untilStable(page, () => page.evaluate(() => [".stznav-mark", "h1", ".stznav-language"]
        .map((selector) => JSON.stringify(document.querySelector(selector)?.getBoundingClientRect() ?? null))),
      `the logo, the heading and the language switcher at ${width}px never stopped moving`);
      const probe = await page.evaluate(() => {
        const box = (selector) => document.querySelector(selector)?.getBoundingClientRect();
        const lang = document.querySelector(".stznav-language");
        const ls = getComputedStyle(lang);
        const chat = document.querySelector('[data-nav-accion="abrir-chat"]');
        return {
          markLeft: box(".stznav-mark").left, markW: box(".stznav-mark").width,
          h1Left: box("h1").left,
          talleres: document.querySelectorAll('#stznav [data-nav-key="talleres"]').length,
          chatText: chat.textContent.trim(), chatAria: chat.getAttribute("aria-label"),
          chatIcon: !!chat.querySelector("img"),
          langBorder: ls.borderTopWidth, langHeight: lang.getBoundingClientRect().height,
          langRight: lang.getBoundingClientRect().right,
          chevronW: getComputedStyle(lang.parentElement, "::after").width,
        };
      });
      // The glyph starts ~20% of the mark width inside its box.
      const glyphLeft = probe.markLeft + probe.markW * 0.203;
      expect(Math.abs(glyphLeft - probe.h1Left),
        `logo glyph starts ${glyphLeft.toFixed(1)}px but the page text starts at ${probe.h1Left}px`).toBeLessThanOrEqual(2.5);
      expect(probe.talleres, "Talleres must not be a menu item").toBe(0);
      expect(probe.langBorder, "the language switcher has no visible border").toBe("0px");
      expect(probe.langHeight, "the language switcher keeps a 44px hit target").toBeGreaterThanOrEqual(44);
      expect(probe.chevronW, "the language switcher carries its chevron").toBe("12px"); // the Icons8 expand-arrow, not a drawn border
      expect(probe.chatText.length < probe.chatAria.length, "Chat is one short word; the full phrase stays the accessible name").toBe(true);
      expect(probe.chatIcon, "Chat keeps its robot icon").toBe(true);
      // Demo review 2026-11-15 (P35): no «Centros | Talleres» tab bar above the title;
      // the workshops are a heading link after the centers when the pack has any.
      await page.goto("/centers", { waitUntil: "load", timeout: 20_000 });
      const tabs = await page.evaluate(() => document.querySelectorAll(".subtabs a").length);
      expect(tabs, "no tab bar above the centers title").toBe(0);
    } finally { await close(); }
  });
}

async function directoryCard(page, { total, portraits }) {
  await page.route("**/api/directory", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ clusters: [{
    id: "avatar-proof", nombre: "Avatar proof", top: 1, n_medicos: total,
    avatares: Array.from({ length: portraits }, (_, index) => ({ ph: `background-position:-${index * 44}px 0` })),
  }] }) }));
  await page.goto("/", { waitUntil: "load", timeout: 20_000 });
  // The server-rendered skeletons share .dir-card (dir-card--sk) until the
  // directory response replaces them: wait for the one real rendered card,
  // and for the skeletons to be gone before counting portraits.
  await expect(page.locator(".dir-card:not(.dir-card--sk)")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator(".dir-card--sk")).toHaveCount(0, { timeout: 10_000 });
}

test("real directory renderer draws up to six portraits and never a +N badge", async ({ newContext }, testInfo) => {
  const { page, close } = await openPage(newContext, { width: 390, height: 844 });
  try {
    await directoryCard(page, { total: 6, portraits: 6 });
    await expect(page.locator(".dir-card .dc-av--ph")).toHaveCount(6);
    await expect(page.locator(".dir-card .dc-av--more")).toHaveCount(0);
    await saveShot(testInfo, page, path.join(EVIDENCE, "directory-six-390.png"), { fullPage: true });
    await page.unroute("**/api/directory");
    await directoryCard(page, { total: 7, portraits: 7 });
    await expect(page.locator(".dir-card .dc-av--ph")).toHaveCount(6);
    await expect(page.locator(".dir-card .dc-av--more")).toHaveCount(0);
    await expect(page.locator(".dir-card .dc-sub")).toHaveText("7 médicos");
    await saveShot(testInfo, page, path.join(EVIDENCE, "directory-seven-390.png"), { fullPage: true });
  } finally { await close(); }
});

// The floating «Chateá con IA» launcher never sits on the footer's text or
// links: scrolled to the very bottom of any public page, at any width where the
// launcher is drawn, in both themes. One shared room under the footer (chat-
// widget.mjs), not a per-page margin.
const FAB_ROUTES = ["/", "/about.html", "/privacy.html", "/terms.html", "/centers", "/doctors", "/datos",
  "/account?mode=login", "/service/medicina-general-riverside-clinic", "/service/medicina-general-riverside-clinic/book"];
const FAB_SHOTS = new Set(["/", "/about.html", "/account?mode=login"]);

function fabProbe(page) {
  return page.evaluate(async () => {
    window.scrollTo(0, document.documentElement.scrollHeight);
    // Up to 600px the launcher steps aside on the frame after the scroll
    // (chat-widget.mjs launcherAside): measure what the reader sees then.
    await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
    const fab = document.getElementById("stz-chat-launcher");
    const style = fab && getComputedStyle(fab);
    if (!fab || style.display === "none" || style.position !== "fixed" || style.visibility === "hidden" || Number(style.opacity) === 0) {
      return { drawn: false, hits: [] };
    }
    const box = fab.getBoundingClientRect();
    const hits = [];
    for (const footer of document.querySelectorAll("footer")) {
      const walker = document.createTreeWalker(footer, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!node.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const rect of range.getClientRects()) {
          if (rect.width > 0 && rect.left < box.right - 1 && rect.right > box.left + 1
            && rect.top < box.bottom - 1 && rect.bottom > box.top + 1) {
            hits.push(node.textContent.trim().slice(0, 40));
            break;
          }
        }
      }
    }
    return { drawn: true, hits, box: { left: Math.round(box.left), top: Math.round(box.top), width: Math.round(box.width), height: Math.round(box.height) } };
  });
}

for (const theme of ["pinamar", "humanrounds"]) {
  test(`chat launcher never covers footer text, ${theme} theme`, async ({ app, newContext, request, cleanup }, testInfo) => {
    test.setTimeout(240_000);
    cleanup(() => setTheme(app.dbName, "humanrounds").catch(() => {}));
    await setTheme(app.dbName, theme);
    // core/theme.py caches theme.current() for ~3s: wait until the server serves the new one.
    await expect.poll(async () => {
      const html = await (await request.get(app.baseUrl + "/")).text();
      return /<html[^>]*\sdata-theme="([^"]*)"/.exec(html)?.[1];
    }, { message: `the server never started serving the ${theme} theme`, timeout: 10_000 }).toBe(theme);
    const failures = [];
    for (const width of [390, 641, 768, 1024, 1440]) {
      for (const route of FAB_ROUTES) {
        const { page, close } = await openPage(newContext, { width, height: 800 });
        try {
          await page.goto(route, { waitUntil: "load", timeout: 20_000 });
          await page.locator("footer").first().waitFor({ state: "visible", timeout: 10_000 });
          if (route.startsWith("/account")) {
            await page.locator("#stzc-page #cuenta-host form input").first().waitFor({ state: "visible", timeout: 10_000 });
          }
          // A page without the chat widget has no launcher and nothing to cover.
          const hasLauncher = await page.locator("#stz-chat-launcher").waitFor({ state: "attached", timeout: 4_000 }).then(() => true, () => false);
          if (!hasLauncher) { note(testInfo, `no launcher: ${route} at ${width}px`); continue; }
          // nav layout() settles the compact/collapsed state
          await untilStable(page, () => page.evaluate(() => [
            document.getElementById("stznav")?.className,
            JSON.stringify(document.querySelector("footer")?.getBoundingClientRect()),
            JSON.stringify(document.getElementById("stz-chat-launcher")?.getBoundingClientRect()),
            document.documentElement.scrollHeight,
          ]), `${route} at ${width}px never stopped moving`);
          const probe = await fabProbe(page);
          if (FAB_SHOTS.has(route)) {
            await saveShot(testInfo, page, path.join(EVIDENCE, `fab-${theme}-${route.replace(/[^a-z]+/g, "_")}-${width}.png`));
          }
          if (probe.hits.length) failures.push(`${route} at ${width}px: launcher ${JSON.stringify(probe.box)} covers «${probe.hits.join("», «")}»`);
        } finally { await close(); }
      }
    }
    expect(failures, `launcher on footer text:\n${failures.join("\n")}`).toEqual([]);
  });
}

// The same promise while the chat module has NOT arrived yet. The server paints
// the launcher in the first frame (templates/_chat_bootstrap.html) but the module
// (chat-widget.mjs) comes later — seconds on a slow phone, with no CDN — so the
// footer's room for the button has to be in the bootstrap too. The module is held
// back on purpose, so the window that used to be a race under load is the whole test.
const FAB_HELD_ROUTES = ["/", "/about.html", "/account?mode=login"];
const FAB_HELD_SHOT = { route: "/", width: 641 };

for (const theme of ["pinamar", "humanrounds"]) {
  test(`chat launcher never covers footer text while the chat module is still loading, ${theme} theme`, async ({ app, newContext, request, cleanup }, testInfo) => {
    test.setTimeout(240_000);
    cleanup(() => setTheme(app.dbName, "humanrounds").catch(() => {}));
    await setTheme(app.dbName, theme);
    await expect.poll(async () => {
      const html = await (await request.get(app.baseUrl + "/")).text();
      return /<html[^>]*\sdata-theme="([^"]*)"/.exec(html)?.[1];
    }, { message: `the server never started serving the ${theme} theme`, timeout: 10_000 }).toBe(theme);
    const failures = [];
    let drawnWhileHeld = 0;
    for (const width of [601, 641, 700, 768]) {
      for (const route of FAB_HELD_ROUTES) {
        const { page, close } = await openPage(newContext, { width, height: 800 });
        try {
          let release;
          const held = new Promise((resolve) => { release = resolve; });
          let heldRequests = 0;
          await page.route(/\/platform\/chat-widget\.[0-9a-f]+\.mjs$/, async (r) => { heldRequests += 1; await held; await r.continue().catch(() => {}); });
          await page.goto(route, { waitUntil: "load", timeout: 20_000 });
          await page.locator("footer").first().waitFor({ state: "visible", timeout: 10_000 });
          await page.locator("#stz-chat-launcher").waitFor({ state: "attached", timeout: 4_000 });
          // The hold is real: the module was asked for and has not answered.
          await expect.poll(() => heldRequests, { message: `chat-widget.mjs was never requested on ${route}`, timeout: 10_000 }).toBeGreaterThan(0);
          await untilStable(page, () => page.evaluate(() => [
            document.getElementById("stznav")?.className,
            JSON.stringify(document.querySelector("footer")?.getBoundingClientRect()),
            JSON.stringify(document.getElementById("stz-chat-launcher")?.getBoundingClientRect()),
            document.documentElement.scrollHeight,
          ]), `${route} at ${width}px never stopped moving while the chat module was held`);
          const probe = await fabProbe(page);
          if (probe.drawn) drawnWhileHeld += 1;
          if (route === FAB_HELD_SHOT.route && width === FAB_HELD_SHOT.width) {
            await saveShot(testInfo, page, path.join(EVIDENCE, `fab-held-${theme}-${width}.png`));
          }
          if (probe.hits.length) failures.push(`${route} at ${width}px, module held: launcher ${JSON.stringify(probe.box)} covers «${probe.hits.join("», «")}»`);
          release();
        } finally { await close(); }
      }
    }
    expect(drawnWhileHeld, "the launcher was never drawn while the module was held: the test would prove nothing").toBeGreaterThan(0);
    expect(failures, `launcher on footer text before the chat module loaded:\n${failures.join("\n")}`).toEqual([]);
  });
}

// The boxes of the header's right side, left to right, in Chat's row, with the
// air between neighbours. The signed-in patient's «Mis turnos» highlight is a
// ::before that overflows the link, so its visible edge is the pill's, not the
// link's (the product owner, 2026-10-25, m908541: the pill almost touched «Chat» while
// «Chat» and «Subí tu orden» had normal air between them).
function headerRow() {
  const nav = document.getElementById("stznav");
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  // The bar's own row (not the open burger panel): Chat's, or the account's
  // when Chat went into the burger (compact).
  const inBar = (el) => shown(el) && !el.closest(".stznav--compact .stznav-content, .stznav--collapsed .stznav-menu");
  const anchor = [...nav.querySelectorAll(".stznav-btn--chat, .stznav-cuenta")].find(inBar);
  if (!anchor) return { navClass: nav.className, oneRow: false };
  const row = anchor.getBoundingClientRect();
  const boxes = [...nav.querySelectorAll(".stznav-cuenta, .stznav-langs, .stznav-btn--chat, .stznav-btn--ghost, .stznav-burger")]
    .filter(inBar)
    .map((el) => {
      const r = el.getBoundingClientRect();
      let left = r.left, right = r.right;
      if (el.matches(".stznav-cuenta.on")) {
        const pill = getComputedStyle(el, "::before");
        left -= parseFloat(pill.left) || 0;
        right -= parseFloat(pill.right) || 0;
      }
      // The language switcher has no box: what shows is its name and chevron.
      if (el.matches(".stznav-langs")) {
        const select = el.querySelector("select");
        left += parseFloat(getComputedStyle(select).paddingLeft) || 0;
        right -= 10; // the chevron sits 10px inside the right edge
      }
      if (el.matches(".stznav-burger")) { left += 11; right -= 11; } // its three bars
      const name = el.matches(".stznav-cuenta") ? (el.matches(".on") ? "account pill" : "account")
        : el.matches(".stznav-langs") ? "language" : el.matches(".stznav-btn--chat") ? "Chat"
          : el.matches(".stznav-burger") ? "burger" : "order";
      return { name, left, right, top: r.top, bottom: r.bottom };
    })
    .filter((b) => b.bottom > row.top && b.top < row.bottom)
    .sort((a, b) => a.left - b.left);
  const gaps = boxes.slice(1).map((b, i) => ({ pair: `${boxes[i].name} → ${b.name}`, air: Math.round(b.left - boxes[i].right) }));
  return { navClass: nav.className, oneRow: true, gaps };
}

for (const theme of ["pinamar", "humanrounds"]) {
  test(`signed-in header: the «Mis turnos» pill keeps the same air before Chat as Chat before «Subí tu orden», ${theme} theme`, async ({ app, newContext, request, cleanup }, testInfo) => {
    test.setTimeout(240_000);
    cleanup(() => setTheme(app.dbName, "humanrounds").catch(() => {}));
    await setTheme(app.dbName, theme);
    await expect.poll(async () => {
      const html = await (await request.get(app.baseUrl + "/")).text();
      return /<html[^>]*\sdata-theme="([^"]*)"/.exec(html)?.[1];
    }, { message: `the server never started serving the ${theme} theme`, timeout: 10_000 }).toBe(theme);
    const evidence = artifactPath("pinamar-turnos", "reset-flow", "header");
    const failures = [];
    const measured = [];
    for (const width of [390, 768, 1024, 1280, 1440]) {
      for (const state of ["signed-out", "signed-in", "signed-in-account", "signed-in-ten-languages", "signed-in-account-ten-languages"]) {
        const { page, close } = await openPage(newContext, { width, height: 800 });
        const route = state.startsWith("signed-in-account") ? "/account" : "/";
        try {
          if (state !== "signed-out") await page.context().addCookies([patientCookie(app.baseUrl, app.fixture.account_session)]);
          const injected = state.endsWith("ten-languages") ? await injectLanguages(page, route) : null;
          await page.goto(route, { waitUntil: "load", timeout: 20_000 });
          if (injected) expect(injected.found, `${route} must load the native nav module`).toBe(true);
          if (state !== "signed-out") await page.locator(".stznav-cuenta").first().waitFor({ state: "attached", timeout: 10_000 });
          await untilStable(page, () => page.evaluate(headerRow), `${state} at ${width}px: the header never settled`);
          const probe = await page.evaluate(headerRow);
          measured.push({ width, state, ...probe });
          if ([390, 1280].includes(width) && !state.endsWith("ten-languages")) {
            await saveShot(testInfo, page, path.join(evidence, `${theme}-${state}-${width}.png`), { clip: { x: 0, y: 0, width, height: 120 } });
          }
          if (!probe.oneRow) continue;
          // Chat → «Subí tu orden» is the reference; with Chat in the burger, its 8px.
          const chatToOrder = probe.gaps.find((g) => g.pair === "Chat → order")?.air ?? 8;
          for (const g of probe.gaps) {
            if (g.pair.startsWith("account pill") && Math.abs(g.air - chatToOrder) > 1) {
              failures.push(`${state} at ${width}px: ${g.pair} has ${g.air}px of air, Chat → order has ${chatToOrder}px`);
            }
            if (g.air < chatToOrder - 1) failures.push(`${state} at ${width}px: ${g.pair} is ${g.air}px apart, tighter than Chat → order (${chatToOrder}px)`);
          }
        } finally { await close(); }
      }
    }
    note(testInfo, JSON.stringify(measured));
    await testInfo.attach("header-row.json", { body: JSON.stringify(measured, null, 2), contentType: "application/json" });
    expect(failures, `header air:\n${failures.join("\n")}`).toEqual([]);
  });
}

// The home page's sticky search strip: the strip around the search box is
// TRANSPARENT (the product owner, 2026-10-25: "the input must cast a shadow, not a
// shadow on a white band"): no fill, no padding, only the box is painted; it
// sticks 10px below the top edge. The folded «Todas las especialidades» block
// that used to sit above the footer is gone (demo review 2026-11-15: it
// repeated the specialties grid above it).
const HOME_EVIDENCE = artifactPath("human-rounds", "home-searchbar-browse-2026-10-25", "candidate-browser");

async function openHome(newContext, theme, request, app, width, height) {
  await setTheme(app.dbName, theme);
  await expect.poll(async () => {
    const html = await (await request.get(app.baseUrl + "/")).text();
    return /<html[^>]*\sdata-theme="([^"]*)"/.exec(html)?.[1];
  }, { message: `the server never started serving the ${theme} theme`, timeout: 10_000 }).toBe(theme);
  const { page, close } = await openPage(newContext, { width, height });
  await page.goto(app.baseUrl + "/", { waitUntil: "load", timeout: 20_000 });
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.locator(".dir-card").first().waitFor({ state: "attached" });
  return { page, close };
}

for (const theme of ["pinamar", "humanrounds"]) {
  test(`the sticky home search strip is transparent, ${theme} theme`, async ({ app, newContext, request, cleanup }, testInfo) => {
    test.setTimeout(120_000);
    cleanup(() => setTheme(app.dbName, "humanrounds").catch(() => {}));
    for (const width of [390, 1440]) {
      const { page, close } = await openHome(newContext, theme, request, app, width, 800);
      try {
        const strip = await page.evaluate(() => {
          const bar = document.querySelector(".searchbar");
          const cs = getComputedStyle(bar);
          return {
            position: cs.position, top: cs.top, background: cs.backgroundColor, padding: cs.padding,
            stripHeight: bar.getBoundingClientRect().height,
            boxHeight: document.querySelector(".searchbox").getBoundingClientRect().height,
          };
        });
        expect(strip.position).toBe("sticky");
        expect(strip.top).toBe("10px");
        expect(strip.background).toBe("rgba(0, 0, 0, 0)");
        expect(strip.padding).toBe("0px");
        expect(strip.stripHeight).toBe(strip.boxHeight);
        // At rest nothing is painted behind the box.
        const fade = () => page.evaluate(() => {
          const bar = document.querySelector(".searchbar");
          return { stuck: bar.classList.contains("searchbar--stuck"), opacity: getComputedStyle(bar, "::before").opacity };
        });
        expect(await fade()).toEqual({ stuck: false, opacity: "0" });
        await page.evaluate(() => window.scrollTo(0, 700));
        await expect.poll(() => page.evaluate(() => Math.round(document.querySelector(".searchbar").getBoundingClientRect().top))).toBe(10);
        // Stuck, the page fades out behind it: a line of text passing under the
        // box is no longer cut in half by its edge (the product owner's hub, 2026-11-05, 1440 px).
        await expect.poll(fade, { message: "the fade behind the stuck search" }).toEqual({ stuck: true, opacity: "1" });
        await saveShot(testInfo, page, path.join(HOME_EVIDENCE, `home-${theme}-${width}-scrolled.png`));
        // A centre's photo passing under the strip: the evidence frame the owner judged.
        await page.locator("#centros").scrollIntoViewIfNeeded();
        await page.evaluate(() => window.scrollBy(0, document.querySelector("#centros").getBoundingClientRect().top + 100 - 40));
        await saveShot(testInfo, page, path.join(HOME_EVIDENCE, `home-${theme}-${width}-photo-under.png`));
        await page.evaluate(() => window.scrollTo(0, 0));
        await saveShot(testInfo, page, path.join(HOME_EVIDENCE, `home-${theme}-${width}-rest.png`));
      } finally { await close(); }
    }
  });

  test(`the chat button never sits on content while the home scrolls at 390px, ${theme} theme`, async ({ app, newContext, request, cleanup }, testInfo) => {
    // Demo review 2026-11-15: at 390 px «Chateá con IA» covered «+4» on the home,
    // «Más horarios», a list row, a heading. Any text line, image or control
    // under it makes it step aside; it comes back once the spot is clear.
    test.setTimeout(180_000);
    cleanup(() => setTheme(app.dbName, "humanrounds").catch(() => {}));
    const { page, close } = await openHome(newContext, theme, request, app, 390, 800);
    try {
      await page.locator("#stz-chat-launcher").waitFor({ state: "attached" });
      await page.mouse.move(0, 0);
      const seen = { shown: 0, covering: [] };
      const end = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
      for (let y = 0; y <= end + 40; y += 40) {
        await page.evaluate((v) => window.scrollTo(0, v), Math.min(y, end));
        let at;
        // Re-measure (retrying expect, no sleep) until the class agrees with the geometry.
        await expect.poll(async () => {
          at = await page.evaluate(() => new Promise((done) => {
            requestAnimationFrame(() => requestAnimationFrame(() => {
              const l = document.getElementById("stz-chat-launcher");
              const r = l.getBoundingClientRect();
              const shown = getComputedStyle(l).display !== "none" && !l.classList.contains("stzc-launcher--aside");
              const under = [];
              const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
              for (let n = walker.nextNode(); n; n = walker.nextNode()) {
                if (!n.textContent.trim() || l.contains(n)) continue;
                const range = document.createRange();
                range.selectNodeContents(n);
                for (const q of range.getClientRects()) {
                  if (q.width > 0 && q.left < r.right - 1 && q.right > r.left + 1 && q.top < r.bottom - 1 && q.bottom > r.top + 1) {
                    if (getComputedStyle(n.parentElement).visibility !== "hidden") { under.push(n.textContent.trim().slice(0, 30)); break; }
                  }
                }
              }
              done({ y: Math.round(window.scrollY), shown, under });
            }));
          }));
          return !at.shown || at.under.length === 0;
        }, { message: `the chat button sits on content (scrollY ${y})`, timeout: 5000 }).toBe(true);
        if (at.shown) seen.shown++;
      }
      expect(seen.shown, "the button is visible where nothing is under it").toBeGreaterThan(0);
      await saveShot(testInfo, page, path.join(HOME_EVIDENCE, `fab-clear-${theme}-390.png`));
    } finally { await close(); }
  });

  test(`the open phone menu hides the floating chat button, ${theme} theme`, async ({ app, newContext, request, cleanup }) => {
    test.setTimeout(120_000);
    cleanup(() => setTheme(app.dbName, "humanrounds").catch(() => {}));
    const { page, close } = await openHome(newContext, theme, request, app, 390, 800);
    try {
      await page.locator(".stznav-burger").click();
      await expect(page.locator("#stznav")).toHaveClass(/\bopen\b/);
      await expect(page.locator("#stz-chat-launcher")).toBeHidden();
      // and the home has no folded duplicate of the specialties above the footer
      await expect(page.locator("nav.browse")).toHaveCount(0);
    } finally { await close(); }
  });
}
