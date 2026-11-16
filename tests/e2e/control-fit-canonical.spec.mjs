// Browser acceptance for the physical admin-control-fit.mjs cutover. The
// direct Solicitudes screen must load the fingerprinted module, measure real
// selects and remain usable at the narrow admin breakpoint.
//
// Port of tests/e2e/control-fit-canonical.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName and requests option); there is no requireOk()/skip path. The page is the
//   runner's, at the original 390x844 viewport (test.use({ viewport })).
// - The admin login is the shared loginAdmin() helper (original: form login, then wait for
//   any /admin/ page except the login); /admin/solicitudes is opened right after, as before.
// - Waits are web-first: the filter toggle and `#f-estado` are `toBeVisible` (10 s), the wait
//   for the measured `--control-fit-width` custom property is `expect.poll` (5 s) on the same
//   value test. The «filters start folded» guard, the no-global/module-requested checks, the
//   overflow check and the failures list stay point-in-time reads.
// - Locators: `#adm-filter-toggle` and `#f-estado` stay CSS - the ids and the
//   `--control-fit-width` property are what the module under test is measured on.
import { test, expect, loginAdmin } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_control_fit_canonical",
    requests: true,
    // Approved history next to the pending requests: the status filter has
    // two values to choose between, so the folded filter band exists (a
    // filter with one value is not shown — progressive disclosure, 2026-10-26).
    queue: true,
  },
  viewport: { width: 390, height: 844 },
});

test("Solicitudes measures filters through canonical admin-control-fit.mjs", async ({ page, app }) => {
  const requested = [];
  const failures = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  page.on("requestfailed", (request) => {
    const pathname = new URL(request.url()).pathname;
    // Best-effort login telemetry is deliberately absent from the hermetic
    // fixture; it is unrelated to the screen/module under test.
    if (pathname !== "/api/login-panel/event") failures.push(pathname);
  });
  page.on("pageerror", (error) => failures.push(String(error)));
  await loginAdmin(page, app.baseUrl);
  await page.goto("/admin/solicitudes", { waitUntil: "load", timeout: 15_000 });
  // On the phone (<=640px) the selects fold behind the one filter button
  // with a counter (110fbc13, 2026-09-15): they must stay hidden until it
  // is pressed, and then be measured like any other select.
  const toggle = page.locator("#adm-filter-toggle");
  await expect(toggle).toBeVisible({ timeout: 10_000 });
  expect(await page.locator("#f-estado").isVisible(), "phone filters start folded").toBe(false);
  await toggle.click();
  await expect(page.locator("#f-estado")).toBeVisible({ timeout: 10_000 });
  await expect.poll(
    () => page.evaluate(() => document.getElementById("f-estado")?.style.getPropertyValue("--control-fit-width")),
    { timeout: 5_000 },
  ).toMatch(/px$/);

  expect(await page.evaluate(() => window.SanaControlFit)).toBeUndefined();
  expect(requested.some((pathname) => /\/static\/platform\/admin-control-fit\.[a-f0-9]+\.mjs$/.test(pathname))).toBe(true);
  expect(requested.some((pathname) => pathname.endsWith("/admin-control-fit.js"))).toBe(false);
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, `Solicitudes overflows 390px by ${overflow}px`).toBeLessThanOrEqual(1);
  expect(failures).toEqual([]);
});
