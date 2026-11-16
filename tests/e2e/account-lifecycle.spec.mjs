// Real server, fingerprinted Account/Requests/Labs modules and Chromium DOM.
// Network failures are deliberately injected; identities and normal requests
// come from the existing synthetic waitlist fixture, never a live installation.
//
// Differences from the node:test original (tests/e2e/account-lifecycle.e2e.mjs):
//   - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }), same setupHarness
//     options: waitlist); the runner's `context` and `page` replace the per-test context: the patient
//     session cookie is added to it in patientPage() and the width is page.setViewportSize() before
//     the first navigation. The tests do not depend on each other (they only inject failures), so
//     the file is not serial.
//   - Waits are web-first (expect(locator).toBeVisible/toBeAttached/toContainText).
//     Assertions that an element is ABSENT (no nested login, retired profile, no badge) stay
//     point-in-time counts (expect(await locator.count()).toBe(0)): a retrying toHaveCount(0) would also
//     pass when the element only disappears late. Counts that must be positive are toHaveCount.
//   - Evidence stays where it was (artifactPath("human-rounds", "account-lifecycle-audit", "browser-<time>"),
//     i.e. ~/.claude/artifacts/... unless TURNOS_E2E_ARTIFACTS_DIR moves it; the original hard-wired the
//     home directory) and screenshots are also attached to the report (saveShot; fullPage and animations:"disabled" kept).
//   - Locators: ids/classes/data-* of the account shell (#ms-host, #lr-host, #cuenta-host, .rail-nav,
//     [data-tab], [data-cancel], #sc-*) stay CSS: they are the page's markup contract and several
//     checks are about what is present or absent in that structure.
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { test, expect, patientCookie, saveShot, artifactPath } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_account_lifecycle", waitlist: true } });

const evidence = artifactPath("human-rounds", "account-lifecycle-audit",
  `browser-${new Date().toISOString().replaceAll(":", "-")}`);

async function patientPage({ page, context, app }, width = 1280, hash = "#turnos") {
  await page.setViewportSize({ width, height: 900 });
  await context.addCookies([patientCookie(app.baseUrl, app.fixture.waitlist_session)]);
  await page.goto(app.baseUrl + "/account" + hash, { waitUntil: "load" });
  // The desktop rail is intentionally hidden below 900px; the patient menu
  // in shared navigation owns mobile navigation. Do not require desktop UI.
  await expect(page.locator(".rail-nav")).toBeAttached();
  return page;
}

async function freshMount(page) {
  await page.evaluate(() => {
    // Exercise the actual server-rendered page owner, including its canonical
    // static URL and fresh-session option. Synthetic lifecycle events do not
    // claim to prove the browser's BFCache admission policy.
    window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
  });
}

async function assertFits(page) {
  const size = await page.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
  expect(size.content <= size.viewport + 1, JSON.stringify(size)).toBe(true);
}

async function capturePage(testInfo, page, name) {
  // Full-page captures otherwise place a sticky header at the restored scroll
  // offset. Inspect the page from its top, after layout/paint have settled.
  await page.evaluate(async () => {
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  await saveShot(testInfo, page, path.join(evidence, name), { fullPage: true, animations: "disabled" });
}

async function writeEvidence(name, body) {
  await mkdir(evidence, { recursive: true });
  await writeFile(path.join(evidence, name), body);
}

test("Spanish native Account shows the actual login heading and action", async ({ page, context, app }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await context.addCookies([{ name: "lang", value: "es", url: app.baseUrl }]);
  await page.goto(app.baseUrl + "/account?mode=login");
  await expect(page.locator("#sc-submit")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  await expect(page.locator("#cuenta-title")).toHaveText("Ingresar");
  await expect(page.locator("#sc-submit")).toHaveText("Ingresar");
  await assertFits(page);
});

for (const width of [360, 390, 768, 1024, 1280]) {
  test(`Account ${width}px: real appointments remain reachable after a request-service outage`, async ({ page, context, app }, testInfo) => {
    await patientPage({ page, context, app }, width);
    let calls = 0;
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await expect(page.locator("[data-cancel]").first()).toBeVisible();
    await assertFits(page);
    await capturePage(testInfo, page, `appointments-${width}.png`);
    await page.route("**/api/requests", async (route) => {
      calls++;
      await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
    });
    await freshMount(page);
    const alert = page.locator('#ms-host [role="alert"]');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(/No pudimos cargar tus solicitudes/);
    await expect(page.locator('.rail-nav [data-tab="turnos"]')).toHaveCount(1);
    expect(await page.locator('.rail-nav [data-tab="turnos"] .rail-badge').count(),
      "unknown count must not be presented as zero").toBe(0);
    expect(await page.locator("#ms-auth").count()).toBe(0);
    await assertFits(page);
    const chrome = await page.evaluate(() => ["#stznav", "footer.site"].map((selector) => {
      const node = document.querySelector(selector);
      const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
      return { selector, text: node.innerText, x: rect.x, y: rect.y,
        width: rect.width, height: rect.height, display: style.display,
        visibility: style.visibility, opacity: style.opacity };
    }));
    await writeEvidence(`restore-chrome-${width}.json`, JSON.stringify(chrome, null, 2));
    for (const node of chrome) {
      expect(node.text.trim() && node.width > 0 && node.height > 0, JSON.stringify(node)).toBeTruthy();
      expect(node.display).not.toBe("none");
      expect(node.visibility).not.toBe("hidden");
      expect(node.opacity).toBe("1");
    }
    await capturePage(testInfo, page, `requests-unavailable-${width}.png`);
    // Preserve the initial capture and independently force a new layout.
    // This discriminates screenshot compositor reuse from missing DOM/UI.
    await page.setViewportSize({ width, height: 901 });
    await page.setViewportSize({ width, height: 900 });
    await capturePage(testInfo, page, `requests-repaint-${width}.png`);
    expect(calls, "bounded shell and child lookups").toBe(2);
    expect(errors).toEqual([]);
    // The recovery instruction must actually recover when the service returns.
    await page.unroute("**/api/requests");
    await page.reload({ waitUntil: "load" });
    await expect(page.locator("[data-cancel]").first()).toBeVisible();
  });
}

test("real browser back restores Account with shared navigation and appointments", async ({ page, context, app }, testInfo) => {
  await patientPage({ page, context, app }, 390);
  await expect(page.locator("[data-cancel]").first()).toBeVisible();
  await page.evaluate(() => {
    window.accountPageShows = [];
    window.addEventListener("pageshow", (event) => window.accountPageShows.push(event.persisted));
  });
  await page.goto(app.baseUrl + "/privacy.html", { waitUntil: "load" });
  await page.goBack({ waitUntil: "load" });
  await expect(page.locator("[data-cancel]").first()).toBeVisible();
  await expect(page.locator("#stznav")).toBeVisible();
  await expect(page.locator("#stznav")).toHaveText(/Human Rounds/, { useInnerText: true });
  await assertFits(page);
  await writeEvidence("real-back.json", JSON.stringify(await page.evaluate(() => ({
    observedPersistedEvents: window.accountPageShows || [],
    navigationType: performance.getEntriesByType("navigation")[0]?.type,
  })), null, 2));
  await capturePage(testInfo, page, "real-back-390.png");
});

test("Account lab deep link survives a failed background request count and exposes recoverable Requests", async ({ page, context, app }) => {
  await patientPage({ page, context, app }, 1280);
  await page.route("**/api/account/lab-results", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      informes: [{ fecha: "2026-09-01", tipo_estudio: "Synthetic hemogram", items: [] }],
      series: [], analisis_pendiente: 0,
    }),
  }));
  await page.route("**/api/requests", (route) => route.fulfill({ status: 503, contentType: "application/json", body: "{}" }));
  await page.evaluate(() => history.replaceState(null, "", "#labs&r=0"));
  await freshMount(page);
  await expect(page.locator('#lr-host [data-panel="detalle"]')).toBeVisible();
  expect(new URL(page.url()).hash).toBe("#labs&r=0");
  const requests = page.locator('.rail-nav [data-tab="turnos"]');
  await expect(requests).toBeVisible();
  expect(await requests.locator(".rail-badge").count()).toBe(0);
  await requests.click();
  await expect(page.locator('#ms-host [role="alert"]')).toBeVisible();
  expect(new URL(page.url()).hash).toBe("#turnos");
  await assertFits(page);
});

for (const status of [401, 403, "offline"]) {
  test(`Account request ${status} has the correct authentication or recovery boundary`, async ({ page, context, app }) => {
    await patientPage({ page, context, app });
    await page.route("**/api/requests", (route) => status === "offline" ? route.abort("failed") :
      route.fulfill({ status, contentType: "application/json", body: "{}" }));
    await freshMount(page);
    if (status === "offline") {
      await expect(page.locator('#ms-host [role="alert"]')).toBeVisible();
      expect(await page.locator("#ms-auth").count()).toBe(0);
    } else {
      await expect(page.locator("#cuenta-host #sc-dni")).toBeVisible();
      expect(await page.locator("#ms-auth").count(), "no nested login inside an old account").toBe(0);
      expect(await page.locator("#ms-host").count()).toBe(0);
      expect(await page.locator("#cuenta-host .rail-nav").count(), "previous profile is retired").toBe(0);
    }
  });
}

for (const status of [401, 403, 503, "offline"]) {
  test(`Account Labs ${status} has the correct whole-shell authentication or outage boundary`, async ({ page, context, app }) => {
    await patientPage({ page, context, app }, 1280, "#datos");
    await page.route("**/api/account/lab-results", (route) => status === "offline" ? route.abort("failed") :
      route.fulfill({ status, contentType: "application/json", body: "{}" }));
    await page.locator('.rail-nav [data-tab="labs"]').click();
    if (status === 401 || status === 403) {
      await expect(page.locator("#cuenta-host .sc-authform #sc-dni")).toBeVisible();
      expect(await page.locator("#lr-host").count()).toBe(0);
      expect(await page.locator("#cuenta-host .rail-nav").count(), "expired profile is retired").toBe(0);
    } else {
      await expect(page.locator('#lr-host [role="alert"]')).toBeVisible();
      // Profile editing also has #sc-dni; absence of that field would
      // incorrectly require destroying a valid profile during an outage.
      expect(await page.locator("#cuenta-host .sc-authform").count()).toBe(0);
      await expect(page.locator("#cuenta-host .rail-nav")).toHaveCount(1);
    }
  });
}

for (const status of [500, 503, "offline"]) {
  test(`Account session-check ${status} shows recovery instead of login`, async ({ page, context, app }) => {
    await patientPage({ page, context, app });
    await page.route("**/api/account/me", (route) => status === "offline" ? route.abort("failed") :
      route.fulfill({ status, contentType: "application/json", body: "{}" }));
    await freshMount(page);
    await expect(page.locator("#cuenta-host .account-load-error")).toBeVisible();
    expect(await page.locator("#cuenta-host #sc-dni").count()).toBe(0);
    expect(await page.locator("#cuenta-host .rail-nav").count()).toBe(0);
    const reload = page.locator("#cuenta-host a");
    expect(await reload.getAttribute("href")).toBe(page.url());
    await page.unroute("**/api/account/me");
    await reload.click();
    await expect(page.locator("[data-cancel]").first()).toBeVisible();
  });
}
