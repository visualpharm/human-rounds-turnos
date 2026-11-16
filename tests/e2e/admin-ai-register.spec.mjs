// Synthetic isolated ASGI/DB/browser acceptance. No provider probes or submissions.
//
// Port of tests/e2e/admin-ai-register.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - App and browser come from the runner (`test.use({ appOptions })`, `page`, 1280x900
//   viewport as the original's context); the hand-made login is `loginAdmin(...)` with
//   `next=/admin/ai-register` and the `finally { context.close() }` is gone.
// - Screenshots go through `saveShot` to the same files as before
//   (artifactPath("human-rounds", "ai-register-browser", ...), i.e. the original's
//   ~/.claude/artifacts/human-rounds/ai-register-browser by default) and are attached to the report.
// - `page.waitForNavigation` (deprecated) is `page.waitForEvent("load")`, armed before the click
//   together with the notify response, exactly as the original's Promise.all.
// - Waits are web-first (`expect(locator).toBeVisible()`); geometry, the 44px target and the
//   absence counts are point-in-time reads, as in the original.
// - Locators that stay CSS: ids/`data-change-id`/`[name]` of the register's own markup (the
//   register table, the download action and the per-change notification form are the contract).
import path from "node:path";
import {
  test, expect, loginAdmin, saveShot, artifactPath, ADMIN_USER, ADMIN_PASS,
} from "./lib/fixtures.mjs";
import { stableBox } from "../helpers/box.mjs";

test.use({
  appOptions: { dbName: "sana_turnos_test_ai_register_e2e" },
  viewport: { width: 1280, height: 900 },
});

const evidence = artifactPath("human-rounds", "ai-register-browser");

test("AI register: seven widths, live config, pending ledger, notification and exports", async ({ page, app }, testInfo) => {
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/ai-register");
  await expect(page.locator("#air-table")).toBeVisible();
  for (const width of [360, 390, 768, 834, 1024, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.reload();
    await expect(page.locator("#air-table")).toBeVisible();
    const geometry = await page.evaluate(() => ({
      width: innerWidth, content: document.documentElement.scrollWidth,
    }));
    expect(geometry.content, JSON.stringify(geometry)).toBeLessThanOrEqual(geometry.width + 1);
    const action = await stableBox(page.locator("#air-download"), `download button at ${width}`);
    expect(action.height).toBeGreaterThanOrEqual(44);
    await saveShot(testInfo, page, path.join(evidence, "register-" + width + ".png"), { fullPage: true });
  }
  const changed = await page.request.post("/api/admin/settings", {
    data: { grupo: "api_keys", valores: { TURNOS_AI_MODEL: "synthetic/register-model" } },
  });
  expect(changed.status()).toBe(200);
  await page.reload();
  const downloaded = await page.request.get("/api/admin/ai-register/export");
  expect(downloaded.status()).toBe(200);
  const dossier = await downloaded.json();
  expect(Object.keys(dossier).sort()).toEqual(["características", "estado_actual", "modificaciones"].sort());
  expect(dossier.características[0].model).toBe("synthetic/register-model");
  const pending = dossier.modificaciones.find((change) => change.status === "pending");
  expect(pending).toBeTruthy();
  const form = page.locator('form[data-change-id="' + pending.id + '"]');
  await form.locator('[name="reference"]').fill("EX-2026-1234");
  const localToday = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Argentina/Buenos_Aires", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
  await form.locator('[name="date"]').fill(localToday);
  await Promise.all([
    page.waitForEvent("load"),
    page.waitForResponse((response) => response.url().endsWith("/api/admin/ai-register/notify")),
    form.locator('button[type="submit"]').click(),
  ]);
  const after = await (await page.request.get("/api/admin/ai-register/export")).json();
  const notified = after.modificaciones.find((change) => change.id === pending.id);
  expect(notified.status).toBe("notified");
  expect(notified.notification_reference).toBe("EX-2026-1234");
  expect(notified.notified_on).toBe(localToday);
  await page.reload();
  await expect(page.locator("#air-table")).toBeVisible();
  expect(await page.locator('form[data-change-id="' + pending.id + '"]').count()).toBe(0);
  await expect(page.locator("#ai-register")).toContainText(/EX-2026-1234/);
  await page.goto("/admin/ai-register/print");
  expect(await page.locator("#air-download").count()).toBe(0);
  expect(await page.locator("#ai-register details:not([open])").count()).toBe(0);
  await saveShot(testInfo, page, path.join(evidence, "printable.png"), { fullPage: true });
});
