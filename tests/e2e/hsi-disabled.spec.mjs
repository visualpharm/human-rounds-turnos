// HSI-off acceptance: the institutional connector is one explicit Settings
// switch. With it off no HSI data surface survives — navigation, journal
// APIs, request column, badge, roadmap copy — but the STATE is always named
// honestly (slice 7, 2026-11-10): Conectores, Sistema and /admin/hsi all say
// how a turno reaches HSI here (server / extension / off) and why. This runs
// the real ASGI app, a throwaway Postgres database, and Chromium against
// fictional data.
//
// Port of tests/e2e/hsi-disabled.e2e.mjs (1 test with 24 journeys).
//
// Differences from the node:test original:
// - The 24 `t.test()` subtests are 24 `test.step()`s of the one test, in the same order and
//   with the same "N. title" names. They are steps, not separate tests, because the journeys
//   are ONE operator session on ONE page: step 2 reads the page step 1 opened, steps 15-18
//   inherit the viewport and page of step 14, step 20 reads the page reloaded by step 19 and
//   steps 23-24 photograph the state step 22 left. Independent tests would have to repeat
//   that navigation, which changes the scenario. Consequence: a failing step stops the rest
//   (node:test went on to run the remaining subtests); the report shows exactly which step.
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `requests: true, queue: true`); the runner's page replaces `browser.newPage()` (1280x900
//   via test.use) and the sign-in is the shared loginAdmin(). `setTheme(..., "pinamar")` stays
//   in a beforeAll. The evidence directory is still allocated with allocateEvidenceRunDir()
//   (lib/hsi-evidence.mjs, unchanged) - at the start of the test instead of the before hook -
//   and its path is a test annotation (was t.diagnostic); the two screenshots go through
//   saveShot() into that directory and are attached to the report.
// - The original slept 3.2 s after setTheme for the server's theme cache (3.5 s TTL) to
//   expire. That sleep is replaced by polling /admin/plan, which only exists under the
//   pinamar theme, until it answers 200 (15 s cap) right after signing in.
// - Waits are web-first with the original timeouts (waitFor -> toBeVisible,
//   waitForFunction -> expect.poll / toHaveAttribute, the URL waits are waitForURL). The
//   connector-save readiness (control enabled AND checked as requested AND «Guardado.» shown,
//   evaluated as ONE condition) stays a single in-page predicate under expect.poll. Layout
//   overflow, HSI-mention counts, fetch statuses, element counts and the connector checkbox
//   state after a reload stay point-in-time reads, as in the original: a web-first expect
//   would retry for 15 s and let a late client-side redraw pass a server-rendered check.
// - Locators left as CSS: the Settings / nav hooks ([data-settings-view], [data-connector-hsi],
//   [data-msg="connectors"], #adm-nav [data-nav="hsi"], [data-cluster="hsi"], the request-list
//   hsi column / chip / action selectors) and `#sec-*` sections - they ARE the contract that
//   must be absent while HSI is off.
import fs from "node:fs";
import path from "node:path";
import {
  test, expect, loginAdmin, saveShot, note, setTheme,
} from "./lib/fixtures.mjs";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_hsi_disabled", requests: true, queue: true },
  viewport: { width: 1280, height: 900 },
});

test.beforeAll(async ({ app }) => {
  await setTheme(app.dbName, "pinamar");
});

async function interfaceHsiCount(page) {
  return page.evaluate(() => {
    let count = ((document.body.innerText.match(/\bHSI\b/gi)) || []).length;
    for (const el of document.querySelectorAll("[title], [aria-label], [placeholder], [alt]")) {
      for (const attr of ["title", "aria-label", "placeholder", "alt"]) {
        count += (((el.getAttribute(attr) || "").match(/\bHSI\b/gi)) || []).length;
      }
    }
    return count;
  });
}

// The one reading every surface names (GET /api/admin/system/hsi): its
// `modo_texto` is what Conectores, Sistema and /admin/hsi must all show.
async function hsiState(page) {
  const response = await page.evaluate(async () => {
    const r = await fetch("/api/admin/system/hsi", { cache: "no-store" });
    return { status: r.status, body: await r.json() };
  });
  expect(response.status, "the state API answers without the connector").toBe(200);
  expect(["servidor", "extension", "apagado"]).toContain(response.body.modo);
  expect(response.body.modo_texto.trim().length, "the mode comes in words").toBeGreaterThan(3);
  return response.body;
}

async function noHorizontalOverflow(page, width, height = 844) {
  await page.setViewportSize({ width, height });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, `${overflow}px horizontal overflow at ${width}px`).toBeLessThanOrEqual(1);
}

// The connector rerenders after its POST and a canonical fresh-settings GET.
// Visibility alone is not readiness: it stays true while the input is disabled
// and the save may still be in flight.
async function setHsiConnector(page, enabled) {
  const savedPost = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/admin/settings", { timeout: 10000 });
  const input = page.locator("[data-connector-hsi]");
  if (enabled) await input.check();
  else await input.uncheck();

  const post = await savedPost;
  expect(post.ok(), "connector save POST must succeed before reload").toBe(true);
  expect(post.request().postDataJSON().valores.HSI_ENABLED,
    "the actual checkbox must submit the requested connector state").toBe(enabled);
  await expect.poll(() => page.evaluate((expected) => {
    const control = document.querySelector("[data-connector-hsi]");
    const status = document.querySelector('[data-msg="connectors"]')?.textContent?.trim() || "";
    return !!control && !control.disabled && control.checked === expected && /Guardado\./.test(status);
  }, enabled), { timeout: 10000 }).toBe(true);
  // Do not match an arbitrary GET response: the page-load GET can still be
  // in flight when the operator clicks. Verify persistence with a new read
  // only after the controller has acknowledged its own POST -> GET cycle.
  const fresh = await page.evaluate(async () => {
    const response = await fetch("/api/admin/settings", { cache: "no-store" });
    return { ok: response.ok, data: await response.json() };
  });
  expect(fresh.ok, "independent settings read must succeed before reload").toBe(true);
  expect(!!fresh.data?.connectors?.emr_sync?.hsi_enabled,
    "fresh settings must acknowledge the requested connector state").toBe(enabled);
}

test("HSI-off browser acceptance covers 24 operator journeys", async ({ page, app }, testInfo) => {
  // One fresh evidence directory per run; earlier runs are never touched.
  const artifactDir = allocateEvidenceRunDir({ label: "e2e" });
  note(testInfo, `evidence directory: ${artifactDir}`);
  await loginAdmin(page, app.baseUrl);
  // The pinamar theme (set in beforeAll) reaches the server once its 3.5 s cache expires;
  // /admin/plan exists only under that theme.
  await expect.poll(async () => (await page.request.get("/admin/plan")).status(),
    { timeout: 15000, message: "the pinamar theme must reach the server" }).toBe(200);

  await test.step("1. Connectors is an ordinary Settings tab", async () => {
    await page.goto("/admin/settings?view=connectors", { waitUntil: "load" });
    await expect(page.locator('[data-settings-view="connectors"][aria-current="page"]')).toBeVisible();
    expect(new URL(page.url()).searchParams.get("view")).toBe("connectors");
  });
  await test.step("2. the HSI connector starts disabled", async () => {
    expect(await page.locator("[data-connector-hsi]").isChecked()).toBe(false);
  });
  await test.step("3. Conectores names the honest state next to its switch", async () => {
    const estado = await hsiState(page);
    await expect(page.locator(".set-card h2")).toHaveText("Sistema clínico", { useInnerText: true });
    // The mode in words, bold, above the switch — the same sentence Sistema
    // and /admin/hsi show, with the reason that follows it.
    await expect(page.locator(".set-hsi b").first()).toHaveText(estado.modo_texto, { useInnerText: true });
    await expect(page.locator(".set-hsi").first()).toContainText(estado.motivo_texto, { useInnerText: true });
    await expect(page.locator(".set-toggle")).toHaveText("Sincronización de turnos con HSI", { useInnerText: true });
    await expect(page.locator("[data-connector-hsi]")).toHaveCount(1);
  });
  await test.step("4. the connector control has a programmatic label", async () => {
    const label = page.locator(".set-toggle");
    await expect(label.locator('input[type="checkbox"]')).toHaveCount(1);
    expect((await label.innerText()).trim().length).toBeGreaterThan(3);
  });
  await test.step("5. direct HSI screen shows the honest state, never a redirect", async () => {
    const estado = await hsiState(page);
    const response = await page.goto("/admin/hsi", { waitUntil: "load" });
    expect(response.status()).toBe(200);
    expect(new URL(page.url()).pathname).toBe("/admin/hsi");
    await expect(page.locator("#sec-hsi")).toBeVisible();
    // the controller asks the state API and draws its reading; the closed
    // journal API is never part of a connector-less screen
    await expect(page.locator("#sec-hsi")).toContainText(estado.modo_texto);
    expect(await page.locator("#hsi-rescan-btn").count()).toBe(0);
  });
  await test.step("6. HSI status API is closed", async () => {
    expect(await page.evaluate(async () => (await fetch("/api/admin/hsi-sync")).status)).toBe(404);
  });
  await test.step("7. HSI resolver API is closed", async () => {
    expect(await page.evaluate(async () => (await fetch("/api/admin/hsi-sync/resolver", { method: "POST" })).status)).toBe(404);
  });
  await test.step("8. HSI conflict API is closed", async () => {
    expect(await page.evaluate(async () => (await fetch("/api/admin/hsi-sync/conflicts", { method: "POST" })).status)).toBe(404);
  });
  await test.step("9. HSI retry API is closed", async () => {
    expect(await page.evaluate(async () => (await fetch("/api/admin/requests/1/retry-hsi", { method: "POST" })).status)).toBe(404);
  });
  await test.step("10. Sistema names the HSI mode without probing anything", async () => {
    const estado = await hsiState(page);
    await page.goto("/admin/system", { waitUntil: "load" });
    await expect(page.locator("#sec-sistema")).toBeVisible();
    const card = page.locator('[data-cluster="hsi"]');
    await expect(card).toHaveCount(1);
    await expect(card).toContainText(estado.modo_texto);
    await expect(card).toContainText(estado.motivo_texto);
    // Without the server-side push nothing is probed and no journal is read:
    // the card holds the mode and the centers' numbers, nothing else.
    const rows = card.locator(".sis-tabla tr");
    if (estado.total > 0) await expect(rows).toHaveCount(2);
    else await expect(rows).toHaveCount(1);
  });
  await test.step("11. the Pinamar roadmap has no HSI copy while off", async () => {
    await page.goto("/admin/plan", { waitUntil: "load" });
    await expect(page.locator("#sec-plan")).toBeVisible();
    expect(await interfaceHsiCount(page)).toBe(0);
  });
  await test.step("12. Requests has no HSI column, chip, or visible mention", async () => {
    await page.goto("/admin/solicitudes", { waitUntil: "load" });
    await expect(page.locator("#sec-solicitudes")).toBeVisible();
    expect(await interfaceHsiCount(page)).toBe(0);
    expect(await page.locator('[data-col="hsi"], .hsi-chip, [data-act="retry-hsi"]').count()).toBe(0);
  });
  await test.step("13. Settings preserves browser back navigation", async () => {
    await page.goto("/admin/settings?view=general", { waitUntil: "load" });
    // The async settings controller canonicalizes the general route after
    // its data loads. Browser load alone is not that readiness condition.
    await page.waitForURL((url) => url.pathname === "/admin/settings" && !url.search, { timeout: 10000 });
    await page.click('[data-settings-view="connectors"]');
    await page.waitForURL((url) => url.searchParams.get("view") === "connectors");
    await page.goBack({ waitUntil: "load" });
    await expect(page.locator('[data-settings-view="general"]')).toHaveAttribute("aria-current", "page");
    expect(new URL(page.url()).searchParams.get("view")).toBeNull();
    expect(await page.locator('[data-settings-view="general"]').getAttribute("aria-current")).toBe("page");
  });
  await test.step("14. 360px Settings has no horizontal overflow", async () => {
    await page.goto("/admin/settings?view=connectors", { waitUntil: "load" });
    await noHorizontalOverflow(page, 360);
  });
  await test.step("15. 390px Settings has no horizontal overflow", async () => {
    await noHorizontalOverflow(page, 390);
  });
  await test.step("16. 768px Settings has no horizontal overflow", async () => {
    await noHorizontalOverflow(page, 768, 1024);
  });
  await test.step("17. 1024px Settings has no horizontal overflow", async () => {
    await noHorizontalOverflow(page, 1024, 768);
  });
  await test.step("18. 1280px Settings has no horizontal overflow", async () => {
    await noHorizontalOverflow(page, 1280, 900);
  });
  await test.step("19. enabling HSI persists through a reload", async () => {
    await setHsiConnector(page, true);
    await page.reload({ waitUntil: "load" });
    expect(await page.locator("[data-connector-hsi]").isChecked()).toBe(true);
  });
  await test.step("20. enabling HSI restores its server-rendered navigation", async () => {
    // Server-rendered: counted once on the page step 19 reloaded, never
    // waited for, so a client-side redraw cannot satisfy it.
    expect(await page.locator('#adm-nav [data-nav="hsi"]').count()).toBe(1);
  });
  await test.step("21. enabling HSI restores its direct screen", async () => {
    const response = await page.goto("/admin/hsi", { waitUntil: "load" });
    expect(response.status()).toBe(200);
  });
  await test.step("22. disabling HSI again removes it after reload", async () => {
    await page.goto("/admin/settings?view=connectors", { waitUntil: "load" });
    await setHsiConnector(page, false);
    await page.reload({ waitUntil: "load" });
    expect(await page.locator("[data-connector-hsi]").isChecked()).toBe(false);
    expect(await page.locator('#adm-nav [data-nav="hsi"]').count()).toBe(0);
  });
  await test.step("23. phone screenshot is captured into this run's evidence directory", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    const shot = path.join(artifactDir, "connectors-off-phone-390.png");
    await saveShot(testInfo, page, shot, { fullPage: true });
    // Byte size proves the capture landed, not that the screen looks
    // right: visual review belongs to the operator gate that inspects
    // the PNG itself.
    expect(fs.statSync(shot).size).toBeGreaterThan(1000);
    note(testInfo, `evidence file: ${shot}`);
  });
  await test.step("24. desktop screenshot is captured into this run's evidence directory", async () => {
    await page.setViewportSize({ width: 1280, height: 900 });
    const shot = path.join(artifactDir, "connectors-off-desktop-1280.png");
    await saveShot(testInfo, page, shot, { fullPage: true });
    // Same honesty rule as the phone capture above.
    expect(fs.statSync(shot).size).toBeGreaterThan(1000);
    note(testInfo, `evidence file: ${shot}`);
  });
});
