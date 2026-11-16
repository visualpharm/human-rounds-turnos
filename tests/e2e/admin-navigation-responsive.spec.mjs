// Direct admin shell regression: a native <details> menu closed for a phone
// must become open again when the same document returns to desktop width.
//
// Port of tests/e2e/admin-navigation-responsive.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions })); the runner's
//   page replaces the hand-made context (phone viewport 360x844 via test.use) and the sign-in
//   is the shared loginAdmin() helper landing on /admin/solicitudes.
// - The two `waitForFunction(menu.open === ..., { timeout: 3000 })` waits are retrying
//   `toHaveJSProperty("open", ...)` expects with the same 3 s timeout. The state snapshots
//   (navState) stay point-in-time reads taken after that wait, as in the original.
// - Locators left as CSS: `.adm5-menu` / `#adm-nav` (the native <details> menu and its nav
//   are the markup contract under test).
import { test, expect, loginAdmin, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_admin_navigation_responsive" },
  viewport: { width: 360, height: 844 },
});

async function navState(page) {
  return page.evaluate(() => {
    const menu = document.querySelector(".adm5-menu");
    const nav = document.getElementById("adm-nav");
    return {
      open: !!(menu && menu.open),
      visible: !!(nav && nav.getClientRects().length),
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
}

test("the direct navigation reopens on desktop after a compact viewport", async ({ page, app }) => {
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/solicitudes");
  await expect(page.locator(".adm5-menu")).toBeVisible();
  expect((await navState(page)).open, "phone starts with its menu closed").toBe(false);

  await page.setViewportSize({ width: 1440, height: 960 });
  await expect(page.locator(".adm5-menu")).toHaveJSProperty("open", true, { timeout: 3000 });
  const desktop = await navState(page);
  expect(desktop.open, "desktop restores the native disclosure").toBe(true);
  expect(desktop.visible, "desktop shows every navigation destination").toBe(true);
  expect(desktop.overflow, `desktop overflows by ${desktop.overflow}px`).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 360, height: 844 });
  await expect(page.locator(".adm5-menu")).toHaveJSProperty("open", false, { timeout: 3000 });
  expect((await navState(page)).open, "returning to phone closes the menu again").toBe(false);
});
