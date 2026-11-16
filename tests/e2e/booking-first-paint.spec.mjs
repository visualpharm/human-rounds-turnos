// The scheduled, no-order wizard must have a stable real first screen before
// service.js's deferred canonical modules resolve.  This is intentionally a
// browser gate: raw HTML or a source scan cannot measure a footer jump.
//
// Port of tests/e2e/booking-first-paint.e2e.mjs (18 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app is the worker-scoped `app` fixture (same dbName, `bookingFirstPaint: true`); the
//   runner's `page` replaces `browser.newPage({ viewport })` and gets the viewport through
//   `page.setViewportSize` before anything is loaded.
// - The held `format.mjs` request uses `holdGate` (released on cleanup too) instead of the
//   file-local `deferred()`, and the «format.mjs was not requested» 10 s guard (a hand-made
//   timer raced against the route) is `expect.poll` on the URL the route saw, same message.
//   The `assert.match(pathname, FORMAT_MODULE)` that ran INSIDE the route handler runs in
//   the test body right after that poll (it is also still asserted at the end).
// - `page.waitForTimeout(150)` («let the late layout shifts land» after the slots/fallback
//   are visible) is a 150 ms `holdsFor` window whose invariant is the layout-shift budget
//   itself (`totalCls < 0.05`, sampled every 50 ms): the cumulative score only grows, so
//   a window that stays under the budget ends where the original's single read would. The
//   original end-of-test budget assertions are kept unchanged.
// - Evidence (`recordProof`): the screenshot goes through `saveShot` (same file names in the
//   same directory, also attached to the report); the JSON is still written beside it.
//   The directory honours TURNOS_BOOKING_FIRST_PAINT_ARTIFACT_DIR, else `artifactPath`.
// - The 7.5 s bound on reading the slots response body (`bounded`) is kept as is; the
//   35 s per-test timeout is `test.setTimeout(35_000)`.
// - Navigation and slots-response promises started before the module is released get a
//   no-op `.catch` so that a failure earlier in the test does not surface as an unhandled
//   rejection; the test still awaits the original promises.
// - Waits are web-first (`toBeVisible`, `toBeHidden`, `toHaveAttribute`) where the original
//   called `waitFor` and compared; everything measured in a snapshot (`stableShell`, the
//   reachability probe, the shift list) is unchanged.
// - Locators: ids/classes/data-* stay CSS (#svc-form-host, .wz-seg-opt[data-slot], #wz-caso,
//   #wz-auth #sc-dni ...): geometry and the wizard's markup contract are what is checked.
import fs from "node:fs";
import path from "node:path";
import {
  test, expect, saveShot, artifactPath, holdGate, holdsFor,
} from "./lib/fixtures.mjs";

// Every case here reaches the empty step and each one opens «Contanos tu caso»
// as a chat: more new conversations than the per-IP window (20/h) the file shares.
test.use({ appOptions: { dbName: "sana_turnos_test_e2e_booking_first_paint", bookingFirstPaint: true,
  env: { INTAKE_CASE_PER_HOUR: "1000" } } });

const FORMAT_MODULE = /\/static\/platform\/format\.[a-f0-9]{8}\.mjs$/;
const POPULATED_SCHEDULES = [
  // Public example-town schedules, rendered through the app's actual local
  // agenda path. They deliberately differ in density rather than stubbing a
  // response: 2 half-days x 6 slots vs weekday 20-minute availability.
  { id: "nutricion-infantil-maternity", label: "small", minSlots: 24, minDays: 4 },
  { id: "medicina-general-riverside-clinic", label: "large", minSlots: 60, minDays: 5 },
];
const VIEWPORTS = [
  { width: 360, height: 640, label: "phone-short" },
  { width: 390, height: 844, label: "phone" },
  { width: 768, height: 1024, label: "tablet-portrait" },
  { width: 1024, height: 900, label: "tablet-landscape" },
  { width: 1280, height: 900, label: "desktop" },
];
const ARTIFACT_DIR = process.env.TURNOS_BOOKING_FIRST_PAINT_ARTIFACT_DIR
  || artifactPath("human-rounds", "booking-first-paint", "managed-e2e");
const TEST_TIMEOUT_MS = 35000;
const RESPONSE_JSON_TIMEOUT_MS = 7500;
const SHIFT_BUDGET = 0.05;

async function holdFormat(page, cleanup) {
  const gate = holdGate(cleanup);
  let moduleURL = "";
  await page.route("**/static/platform/format.*.mjs", async (route) => {
    moduleURL = route.request().url();
    await gate.held;
    await route.continue();
  });
  return { releaseModule: gate.release, getURL: () => moduleURL };
}

async function installLayoutShiftProof(page) {
  await page.addInitScript(() => {
    const describe = (node) => {
      if (!node) return "unknown";
      if (node.id) return `#${node.id}`;
      if (node.matches?.("footer.site")) return "footer.site";
      if (node.matches?.(".wizard-help")) return ".wizard-help";
      return node.tagName?.toLowerCase() || "unknown";
    };
    window.__bookingFirstPaintShifts = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (!entry.hadRecentInput) {
          window.__bookingFirstPaintShifts.push({
            value: entry.value,
            sources: Array.from(entry.sources || [], (source) => describe(source.node)),
          });
        }
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

async function stableShell(page) {
  return page.evaluate(() => {
    const geometry = (element) => {
      const box = element?.getBoundingClientRect();
      return box ? {
        top: Math.round(box.top), bottom: Math.round(box.bottom), height: Math.round(box.height),
        width: Math.round(box.width),
      } : null;
    };
    const host = document.getElementById("svc-form-host");
    const footer = document.querySelector("footer.site");
    const help = document.querySelector(".wizard-help");
    const root = document.documentElement;
    const helpStyle = help ? getComputedStyle(help) : null;
    const launcher = document.getElementById("stz-chat-launcher")?.getBoundingClientRect();
    const helpTextRects = [];
    if (help) {
      const walker = document.createTreeWalker(help, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const range = document.createRange();
        range.selectNodeContents(walker.currentNode);
        helpTextRects.push(...range.getClientRects());
      }
    }
    return {
      boot: host?.classList.contains("svc-form-host--schedule-boot") || false,
      fallback: host?.classList.contains("svc-form-host--schedule-fallback") || false,
      recovery: host?.classList.contains("svc-form-host--schedule-recovery") || false,
      spinner: document.querySelector(".svc-first-schedule")?.textContent?.trim() || "",
      host: geometry(host),
      hostMinHeight: host ? getComputedStyle(host).minHeight : null,
      footer: geometry(footer),
      help: geometry(help),
      supportInFlow: !!help && helpStyle?.display !== "none" && helpStyle?.visibility !== "hidden"
        && helpStyle?.position !== "fixed" && helpStyle?.position !== "absolute",
      supportCoveredByLauncher: !!launcher && helpTextRects.some((r) =>
        r.width > 0 && r.height > 0 && r.left < launcher.right && r.right > launcher.left
        && r.top < launcher.bottom && r.bottom > launcher.top),
      viewportHeight: window.innerHeight,
      horizontalOverflow: root.scrollWidth > window.innerWidth,
      stepper: document.querySelector("#svc-steps")?.textContent?.trim() || "",
    };
  });
}

function slotCount(payload) {
  return (payload?.medicos || []).reduce((total, medico) => total + (medico.turnos || []).length, 0);
}

// The first screen lists the first VISIBLE_DAYS agenda days of every block;
// «Otras fechas» opens the calendar for the rest (hr-patient review 15.11, #20).
const VISIBLE_DAYS = 2;
function firstDays(payload) {
  return (payload?.medicos || []).map((medico) => {
    const days = [...new Set((medico.turnos || []).map((t) => t.dia))].sort().slice(0, VISIBLE_DAYS);
    return { days: days.length, slots: (medico.turnos || []).filter((t) => days.includes(t.dia)).length };
  }).reduce((acc, m) => ({ days: acc.days + m.days, slots: acc.slots + m.slots }), { days: 0, slots: 0 });
}

async function bookingLauncherReachability(page) {
  return page.evaluate(() => {
    const launcher = document.getElementById("stz-chat-launcher");
    const slots = [...document.querySelectorAll(".wz-seg-opt[data-slot][data-dia]")];
    const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    const covered = [];
    for (const slot of slots) {
      // A person can reasonably scroll a chosen time to the bottom edge to
      // read its day/context. That is exactly where the old fixed launcher
      // lived, so this exercises the real overlap rather than only centered
      // screenshots.
      slot.scrollIntoView({ block: "end", inline: "nearest" });
      const slotRect = slot.getBoundingClientRect();
      const launcherRect = launcher?.getBoundingClientRect();
      if (!launcherRect || !overlaps(slotRect, launcherRect)) continue;
      const x = (Math.max(slotRect.left, launcherRect.left) + Math.min(slotRect.right, launcherRect.right)) / 2;
      const y = (Math.max(slotRect.top, launcherRect.top) + Math.min(slotRect.bottom, launcherRect.bottom)) / 2;
      const target = document.elementFromPoint(x, y);
      covered.push({
        text: slot.textContent.trim(),
        target: target?.id || target?.className || target?.tagName || "unknown",
        launcherOwnsPoint: !!target && (target === launcher || launcher.contains(target)),
      });
    }
    return {
      position: launcher ? getComputedStyle(launcher).position : "missing",
      inline: !!launcher?.classList.contains("stzc-booking-inline"),
      covered,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
    };
  });
}

async function recordProof(testInfo, page, stem, proof) {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
  await saveShot(testInfo, page, path.join(ARTIFACT_DIR, `${stem}.png`));
  fs.writeFileSync(path.join(ARTIFACT_DIR, `${stem}.json`), `${JSON.stringify(proof, null, 2)}\n`);
}

async function bounded(promise, timeoutMs, label) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} exceeded ${timeoutMs}ms`)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function responseJson(response, label) {
  return bounded(response.json(), RESPONSE_JSON_TIMEOUT_MS, `${label} response JSON`);
}

const layoutShifts = (page) => page.evaluate(() => window.__bookingFirstPaintShifts || []);
const sumShifts = (shifts) => shifts.reduce((sum, shift) => sum + shift.value, 0);

// «Let the late layout shifts land»: for 150 ms after the surface is drawn the cumulative
// shift score must stay inside the budget.
async function lateShiftsStayInBudget(page, message) {
  await holdsFor(async () => {
    const shifts = await layoutShifts(page);
    expect(sumShifts(shifts), `${message}: ${JSON.stringify(shifts)}`).toBeLessThan(SHIFT_BUDGET);
  }, { during: 150, every: 50 });
}

for (const schedule of POPULATED_SCHEDULES) {
  for (const viewport of VIEWPORTS) {
    test(`${schedule.label} real schedule holds a stable first screen on ${viewport.label}`, async ({ page, cleanup }, testInfo) => {
      test.setTimeout(TEST_TIMEOUT_MS);
      await page.setViewportSize(viewport);
      const failures = [];
      await installLayoutShiftProof(page);
      const held = await holdFormat(page, cleanup);
      page.on("pageerror", (error) => failures.push({ pageerror: String(error) }));
      page.on("response", (response) => {
        const pathname = new URL(response.url()).pathname;
        if (response.status() >= 400 && !(pathname === "/api/account/me" && response.status() === 401)) {
          failures.push({ pathname, status: response.status() });
        }
      });

      const slotsResponse = page.waitForResponse((response) => {
        const request = response.request();
        return request.method() === "GET"
          && new URL(response.url()).pathname === `/api/service-slots/${schedule.id}`;
      }, { timeout: 15000 });
      slotsResponse.catch(() => {});
      const navigation = page.goto(`/service/${schedule.id}/book`, {
        waitUntil: "domcontentloaded", timeout: 20000,
      });
      navigation.catch(() => {});
      await expect.poll(held.getURL, {
        message: "format.mjs was not requested by the booking wizard", timeout: 10000,
      }).not.toBe("");
      expect(new URL(held.getURL()).pathname).toMatch(FORMAT_MODULE);

      const before = await stableShell(page);
      await recordProof(testInfo, page, `${schedule.label}-${viewport.width}x${viewport.height}-held`, { before });
      expect(before.boot, "the server-rendered schedule shell must remain until slots replace it").toBe(true);
      expect(before.spinner).toMatch(/Buscando turnos disponibles/i);
      expect(before.stepper).toMatch(/1/);
      expect(before.horizontalOverflow, "the first screen must fit without horizontal scrolling").toBe(false);
      expect(before.supportInFlow,
        "support must remain a normal, reachable document-flow region while slots load").toBe(true);
      expect(before.host && before.help && before.footer, "booking chrome must have measurable geometry").toBeTruthy();
      expect(before.help.top,
        "support must follow the initial booking surface instead of being hidden or overlaid").toBeGreaterThanOrEqual(before.host.bottom);
      expect(before.footer.top,
        "footer must follow the reachable support region in normal flow").toBeGreaterThanOrEqual(before.help.bottom);
      held.releaseModule();
      await navigation;
      const apiPayload = await responseJson(await slotsResponse, `${schedule.label} slots`);
      const apiSlots = slotCount(apiPayload);
      await recordProof(testInfo, page, `${schedule.label}-${viewport.width}x${viewport.height}-response`, {
        before, afterResponse: await stableShell(page), apiSlots,
        shifts: await layoutShifts(page),
      });
      expect(apiSlots,
        `synthetic ${schedule.label} schedule unexpectedly sparse: ${apiSlots} slots`).toBeGreaterThanOrEqual(schedule.minSlots);
      const slotButtons = page.locator(".wz-seg-opt[data-slot][data-dia]");
      await expect(slotButtons.first()).toBeVisible({ timeout: 15000 });
      await lateShiftsStayInBudget(page, "unexpected initial layout shift");

      const after = await stableShell(page);
      const shifts = await layoutShifts(page);
      const renderedSlots = await slotButtons.count();
      const renderedDays = await page.locator(".wz-day").count();
      const totalCls = sumShifts(shifts);
      await recordProof(testInfo, page, `${schedule.label}-${viewport.width}x${viewport.height}-slots`, {
        before, after, apiSlots, renderedSlots, renderedDays, shifts, totalCls,
      });
      expect(after.boot, "the reservation class must disappear when API-backed slots render").toBe(false);
      expect(after.horizontalOverflow, "the real slot grid must fit at this width").toBe(false);
      const shown = firstDays(apiPayload);
      expect(renderedSlots, "the browser must render every genuine API slot of the first days, not a fallback control").toBe(shown.slots);
      expect(renderedDays, `the first screen lists the first ${VISIBLE_DAYS} days of each block`).toBe(shown.days);
      expect(shown.days, `expected a multi-day ${schedule.label} schedule`).toBeGreaterThanOrEqual(VISIBLE_DAYS);
      await expect(page.locator(".wz-otras").first(), "the rest of the days stay one tap away").toBeVisible();
      const reachability = await bookingLauncherReachability(page);
      await recordProof(testInfo, page, `${schedule.label}-${viewport.width}x${viewport.height}-reachability`, { reachability });
      expect(reachability.covered, "the chat launcher must not intercept a real appointment action").toEqual([]);
      expect(reachability.horizontalOverflow).toBe(false);
      // This is an all-source budget. Small, measured shifts can be caused by
      // browser font/raster work; requiring zero named sources was stricter
      // than the product criterion and incorrectly rejected 0.0026/0.0113.
      expect(totalCls, `unexpected initial layout shift ${totalCls}: ${JSON.stringify(shifts)}`).toBeLessThan(SHIFT_BUDGET);
      expect(new URL(held.getURL()).pathname).toMatch(FORMAT_MODULE);
      expect(failures).toEqual([]);
    });
  }
}

test("booking launcher regression catches a deliberately restored fixed phone control", async ({ page }, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/service/medicina-general-riverside-clinic/book", {
    waitUntil: "domcontentloaded", timeout: 20000,
  });
  const slots = page.locator(".wz-seg-opt[data-slot][data-dia]");
  await expect(slots.first()).toBeVisible({ timeout: 15000 });
  const fixed = await bookingLauncherReachability(page);
  expect(fixed.inline, "the real phone booking launcher must be in normal flow").toBe(true);
  expect(fixed.covered).toEqual([]);
  await page.locator("#stz-chat-launcher").click();
  await expect(page.locator("#stz-chat-panel")).toBeVisible({ timeout: 10000 });
  await expect(page.locator("#stz-chat-launcher")).toHaveAttribute("aria-expanded", "true");
  await page.locator(".stzc-close").click();
  await expect(page.locator("#stz-chat-panel")).toBeHidden({ timeout: 10000 });
  await expect(page.locator("#stz-chat-launcher")).toHaveAttribute("aria-expanded", "false");
  await page.evaluate(() => {
    const launcher = document.getElementById("stz-chat-launcher");
    launcher.style.setProperty("position", "fixed", "important");
    launcher.style.setProperty("right", "16px", "important");
    launcher.style.setProperty("bottom", "16px", "important");
    launcher.style.setProperty("width", "48px", "important");
    launcher.style.setProperty("height", "48px", "important");
    launcher.style.setProperty("padding", "0", "important");
  });
  const regressed = await bookingLauncherReachability(page);
  await recordProof(testInfo, page, "launcher-390-regressed-fixed", { fixed, regressed });
  expect(regressed.covered.some((slot) => slot.launcherOwnsPoint),
    `the deliberate fixed regression must reproduce launcher interception: ${JSON.stringify(regressed)}`).toBeTruthy();
  await page.evaluate(() => document.getElementById("stz-chat-launcher").removeAttribute("style"));
  const restored = await bookingLauncherReachability(page);
  await recordProof(testInfo, page, "launcher-390-restored-inline", { restored });
  expect(restored.covered).toEqual([]);
  await slots.first().click();
  await expect(page.locator("#wz-auth #sc-dni")).toBeVisible({ timeout: 10000 });
});

// Confirmá hides the shell's help line (service.mjs `ayudaEnShell(false)`), so
// the inline «Chateá con IA» is the last visible block and only <script> tags
// follow it. The footer gap used to hang on :nth-last-child(2) — the hidden
// line — and the button sat on the footer rule (the product owner 27.10).
test("inline phone launcher keeps the 32px gap to the footer when the help line is hidden", async ({ page }) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/service/medicina-general-riverside-clinic/book", {
    waitUntil: "domcontentloaded", timeout: 20000,
  });
  await expect(page.locator(".wz-seg-opt[data-slot][data-dia]").first()).toBeVisible({ timeout: 15000 });
  await expect(page.locator("#stz-chat-launcher.stzc-booking-inline")).toBeVisible();
  const gaps = await page.evaluate(() => {
    document.querySelector(".wizard-help").hidden = true;
    const launcher = document.getElementById("stz-chat-launcher").getBoundingClientRect();
    const anchor = document.getElementById("stzc-booking-launcher-anchor").getBoundingClientRect();
    const footer = document.querySelector("main.wrap>footer").getBoundingClientRect();
    return { aboveFooter: Math.round(footer.top - launcher.bottom), anchorToFooter: Math.round(footer.top - anchor.bottom) };
  });
  expect(gaps, "the launcher keeps the shared 32px above the footer rule").toEqual({ aboveFooter: 32, anchorToFooter: 32 });
});

for (const viewport of VIEWPORTS) {
  test(`fully occupied synthetic schedule reaches the real empty fallback on ${viewport.label}`, async ({ page, app, cleanup }, testInfo) => {
    test.setTimeout(TEST_TIMEOUT_MS);
    expect(app.fixture.booking_empty_slots, "fixture did not fill the empty-schedule service").toBeGreaterThan(0);

    await page.setViewportSize(viewport);
    await installLayoutShiftProof(page);
    const held = await holdFormat(page, cleanup);
    const serviceId = app.fixture.booking_empty_service;
    const slotsResponse = page.waitForResponse((response) =>
      response.request().method() === "GET"
        && new URL(response.url()).pathname === `/api/service-slots/${serviceId}`, { timeout: 15000 });
    slotsResponse.catch(() => {});
    const navigation = page.goto(`/service/${serviceId}/book`, {
      waitUntil: "domcontentloaded", timeout: 20000,
    });
    navigation.catch(() => {});
    await expect.poll(held.getURL, { message: "format.mjs was not requested", timeout: 10000 }).not.toBe("");
    expect(new URL(held.getURL()).pathname).toMatch(FORMAT_MODULE);
    const before = await stableShell(page);
    await recordProof(testInfo, page, `empty-${viewport.width}x${viewport.height}-held`, { before });
    expect(before.boot).toBe(true);
    expect(before.supportInFlow).toBe(true);
    expect(before.host && before.help && before.footer).toBeTruthy();
    expect(before.help.top).toBeGreaterThanOrEqual(before.host.bottom);
    expect(before.footer.top).toBeGreaterThanOrEqual(before.help.bottom);
    held.releaseModule();
    await navigation;
    const apiPayload = await responseJson(await slotsResponse, "empty fallback slots");
    await recordProof(testInfo, page, `empty-${viewport.width}x${viewport.height}-response`, {
      before, afterResponse: await stableShell(page), apiSlots: slotCount(apiPayload),
      shifts: await layoutShifts(page),
    });
    expect(slotCount(apiPayload), "the fixture must drive the actual empty API response").toBe(0);
    // «Contanos tu caso» opens as a conversation (the product owner, 2026-11-07); its first question lands in reserved space.
    await expect(page.locator("#wz-casochat .wzc-msg--asst").first()).toBeVisible({ timeout: 15000 });
    await lateShiftsStayInBudget(page, "empty fallback shifted");
    const after = await stableShell(page);
    const shifts = await layoutShifts(page);
    const totalCls = sumShifts(shifts);
    await recordProof(testInfo, page, `empty-${viewport.width}x${viewport.height}-fallback`, {
      before, after, apiSlots: 0, shifts, totalCls,
    });
    expect(after.boot, "the fallback must end the loading marker").toBe(false);
    expect(after.fallback, "the actual fallback form must retain the measured first-screen geometry").toBe(true);
    expect(await page.locator(".wz-seg-opt[data-slot]").count(),
      "a fully occupied schedule must not invent a selectable time").toBe(0);
    expect(after.horizontalOverflow).toBe(false);
    expect(totalCls, `empty fallback shifted ${totalCls}: ${JSON.stringify(shifts)}`).toBeLessThan(SHIFT_BUDGET);
    // Continue through the actual wizard, not a mocked next-step renderer.
    // First-paint space belongs to the schedule, not the account form.
    // «Contanos tu caso» is the request when no slot is free (the reference-deployment counterpart, 2026-10-17).
    await page.locator("#wz-casochat .wzc-input").fill("Necesito un control antes de fin de mes.");
    await page.locator("#wz-casochat .wzc-send").click();
    await expect(page.locator("#wz-casochat .wzc-msg--asst")).toHaveCount(2);
    await page.locator("#wz-horario-next").click();
    await expect(page.locator("#wz-auth #sc-dni")).toBeVisible({ timeout: 10000 });
    const next = await stableShell(page);
    await recordProof(testInfo, page, `empty-${viewport.width}x${viewport.height}-account`, { after, next });
    expect(next.fallback, "leaving the schedule must retire its reserved space").toBe(false);
    expect(next.hostMinHeight, "account must use its own natural height").toBe("0px");
    expect(next.horizontalOverflow).toBe(false);
    expect(next.supportInFlow).toBe(true);
    await page.locator("#wz-auth #sc-swap").click();
    await expect(page.locator("#wz-auth #sc-pass")).toBeVisible({ timeout: 10000 });
    const login = await stableShell(page);
    await recordProof(testInfo, page, `empty-${viewport.width}x${viewport.height}-login`, { next, login });
    expect(login.hostMinHeight, "the shorter sign-in form must not inherit schedule height").toBe("0px");
    expect(login.fallback).toBe(false);
    expect(login.horizontalOverflow).toBe(false);
    expect(login.supportInFlow).toBe(true);
    expect(login.supportCoveredByLauncher, "chat must not cover the visible help text or phone").toBe(false);
  });
}

for (const viewport of [
  { width: 390, height: 844, label: "phone" },
  { width: 1280, height: 900, label: "desktop" },
]) {
  test(`rejected booking dependency recovers without hiding support on ${viewport.label}`, async ({ page }, testInfo) => {
    test.setTimeout(TEST_TIMEOUT_MS);
    await page.setViewportSize(viewport);
    let failFirst = true;
    await installLayoutShiftProof(page);
    await page.route("**/static/platform/format.*.mjs", async (route) => {
      if (failFirst) {
        failFirst = false;
        await route.abort("failed");
        return;
      }
      await route.continue();
    });
    await page.goto("/service/medicina-general-riverside-clinic/book", {
      waitUntil: "domcontentloaded", timeout: 20000,
    });
    await recordProof(testInfo, page, `rejected-${viewport.width}x${viewport.height}-start`, {
      beforeRecovery: await stableShell(page),
      shifts: await layoutShifts(page),
    });
    const recovery = page.locator("#svc-form-host .svc-sub");
    await expect(recovery).toBeVisible({ timeout: 15000 });
    const recoveryText = (await recovery.textContent()) || "";
    const failed = await page.evaluate(() => ({
      boot: document.getElementById("svc-form-host")?.classList.contains("svc-form-host--schedule-boot"),
      supportVisible: !!Array.from(document.querySelectorAll(".wizard-help a")).find((link) =>
        link.offsetWidth || link.offsetHeight || link.getClientRects().length),
      overflow: document.documentElement.scrollWidth > window.innerWidth,
    }));
    const failedGeometry = await stableShell(page);
    const failedShifts = await layoutShifts(page);
    const failedCls = sumShifts(failedShifts);
    await recordProof(testInfo, page, `rejected-${viewport.width}x${viewport.height}`, {
      recoveryText, failed, failedGeometry, shifts: failedShifts, totalCls: failedCls,
    });
    expect(recoveryText).toMatch(/No pudimos cargar esta pantalla|Recargá/i);
    expect(failed.boot, "a rejected import must end the loading marker before showing recovery").toBe(false);
    expect(failedGeometry.recovery,
      "the visible recovery message must retain the measured first-screen geometry").toBe(true);
    expect(failed.supportVisible, "recovery must not hide useful booking support").toBe(true);
    expect(failed.overflow).toBe(false);
    expect(failedCls,
      `rejected dependency shifted ${failedCls}: ${JSON.stringify(failedShifts)}`).toBeLessThan(SHIFT_BUDGET);

    await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
    await expect(page.locator(".wz-seg-opt[data-slot][data-dia]").first()).toBeVisible({ timeout: 15000 });
    const afterReload = await stableShell(page);
    await recordProof(testInfo, page, `rejected-${viewport.width}x${viewport.height}-reloaded`, { afterReload });
    expect(
      await page.evaluate(() =>
        document.getElementById("svc-form-host")?.classList.contains("svc-form-host--schedule-boot")),
    ).toBe(false);
  });
}

// TASK-29: the server paints the order-upload step (zone + the «no order» link) and the
// client repaints the same step once its modules resolve.  The link must not move when the
// client takes over, and the whole hand-over stays inside the shift budget.
for (const viewport of VIEWPORTS.filter((v) => v.width <= 390)) {
  test(`order upload step keeps the «no order» link in place when the client repaints on ${viewport.label}`, async ({ page, cleanup }, testInfo) => {
    test.setTimeout(TEST_TIMEOUT_MS);
    await page.setViewportSize(viewport);
    await installLayoutShiftProof(page);
    const held = await holdFormat(page, cleanup);
    const navigation = page.goto("/service/electrocardiograma-hospital/book", {
      waitUntil: "domcontentloaded", timeout: 20000,
    });
    navigation.catch(() => {});
    await expect.poll(held.getURL, { message: "format.mjs was not requested", timeout: 10000 }).not.toBe("");
    const linkBox = () => page.evaluate(() => {
      const box = document.getElementById("wz-noorden")?.getBoundingClientRect();
      const footer = document.querySelector("footer.site")?.getBoundingClientRect();
      return box ? { top: Math.round(box.top), height: Math.round(box.height), footerTop: Math.round(footer?.top ?? 0) } : null;
    });
    const painted = await linkBox();
    expect(painted, "the server-painted order step must already hold the link").toBeTruthy();
    // The client repaint replaces the node: wait until the painted one is gone.
    await page.evaluate(() => { window.__serverPaintedLink = document.getElementById("wz-noorden"); });
    held.releaseModule();
    await navigation;
    await expect.poll(() => page.evaluate(() => {
      const now = document.getElementById("wz-noorden");
      return !!now && now !== window.__serverPaintedLink;
    }), { message: "the client never repainted the order step", timeout: 15000 }).toBe(true);
    await lateShiftsStayInBudget(page, "order step shifted when the client repainted");
    const repainted = await linkBox();
    const shifts = await layoutShifts(page);
    await recordProof(testInfo, page, `orden-${viewport.width}x${viewport.height}`, {
      painted, repainted, shifts, totalCls: sumShifts(shifts),
    });
    expect(repainted.top, `the link moved from ${painted.top} to ${repainted.top}`).toBe(painted.top);
    expect(repainted.footerTop, "the footer moved when the client repainted").toBe(painted.footerTop);
    expect(sumShifts(shifts), `layout shift ${JSON.stringify(shifts)}`).toBeLessThan(SHIFT_BUDGET);
  });
}
