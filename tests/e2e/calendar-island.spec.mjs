// Literal maintainer-audit proof for the doctor calendar: a real authenticated
// browser opens the direct doctor screen, loads the Preact+HTM island from
// PostgreSQL-backed data, and exercises its day selection without navigation.
//
// Port of tests/e2e/calendar-island.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. The page is the runner's, at the
//   original 1280x900 viewport (test.use({ viewport })), and the admin login is the shared
//   loginAdmin() helper (original: form login, then wait for any /admin/ page except the
//   login); /admin/doctors is opened right after, as before.
// - Waits are web-first: the doctor link, the form, the calendar tab and the first day cell
//   are `toBeVisible`/`not.toHaveCount(0)` (10 s), the selected-day wait is `toHaveClass`
//   (5 s). The no-global/module-requested checks, «no navigation» (url), the panel's
//   visibility and the per-width overflow loop stay point-in-time reads.
// - The `catch` that recorded {failures, screen text} through `t.diagnostic` before
//   rethrowing is kept: it adds the same JSON as a `diagnostic` annotation.
// - Locators: `#sec-medicos ...`, `[data-tab]`, `[data-tabpanel]`, `#med-cal-grid [data-med-date]`
//   stay CSS - the island's data attributes are the contract it is tested through.
import { test, expect, loginAdmin, note } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_calendar_island" },
  viewport: { width: 1280, height: 900 },
});

test("doctor calendar island loads and selects a working day", async ({ page, app }, testInfo) => {
  const requested = [];
  const failures = [];
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("requestfailed", (request) => failures.push(`${new URL(request.url()).pathname}: ${request.failure()?.errorText}`));
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  try {
    await loginAdmin(page, app.baseUrl);
    await page.goto("/admin/doctors", { waitUntil: "load", timeout: 15_000 });
    const doctor = page.locator(
      '#sec-medicos table.cnt-table tbody a[href^="/admin/doctors?item="]',
    ).first();
    await expect(doctor).toBeVisible({ timeout: 10_000 });
    await doctor.click();
    await expect(page.locator("#sec-medicos #cnt-form")).toBeVisible({ timeout: 10_000 });
    const calendarTab = page.locator('#sec-medicos [data-tab="calendario"]');
    await expect(calendarTab).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#med-cal-grid [data-med-date]")).not.toHaveCount(0, { timeout: 10_000 });
    expect(await page.evaluate(() => window.SanaIslands)).toBeUndefined();
    const moduleUrl = await page.evaluate(() => window.SANA_STATIC?.["platform/islands.mjs"] || "");
    expect(moduleUrl).toMatch(/\/static\/platform\/islands\.[a-f0-9]{8}\.mjs$/);
    expect(requested.some((pathname) => /\/static\/platform\/islands\.[a-f0-9]{8}\.mjs$/.test(pathname)),
      "the doctor screen must request the canonical fingerprinted islands module").toBe(true);
    expect(requested.some((pathname) => pathname.endsWith("/islands.js")),
      "the retired classic islands script must never be requested").toBe(false);

    const beforeUrl = page.url();
    await page.locator('#sec-medicos [data-tab="calendario"]').click();
    const firstDay = page.locator("#med-cal-grid [data-med-date]").first();
    const selectedDay = await firstDay.getAttribute("data-med-date");
    await firstDay.click();
    await expect(page.locator(`#med-cal-grid [data-med-date="${selectedDay}"]`)).toHaveClass(/is-selected/, { timeout: 5_000 });

    expect(page.url(), "calendar interaction must not navigate").toBe(beforeUrl);
    expect(await page.locator('#sec-medicos [data-tabpanel="calendario"]').isVisible()).toBe(true);
    for (const width of [360, 390, 768, 1024, 1280]) {
      await page.setViewportSize({ width, height: 844 });
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `doctor calendar overflows by ${overflow}px at ${width}px`).toBeLessThanOrEqual(1);
    }
  } catch (error) {
    note(testInfo, JSON.stringify({ failures, screen: await page.locator("#sec-medicos").innerText() }));
    throw error;
  }
});
