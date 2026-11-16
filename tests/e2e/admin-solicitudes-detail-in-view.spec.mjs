// TASK-53 (26.10, persona Karla, 1280 px): on a desktop the page scrolls, not
// the queue. Picking a request far down the list painted its detail at the
// top of the page — the «Rechazar» button sat 1275 px above the window — and
// the right column looked empty. The detail of the picked row is in view
// without scrolling back up, its «Aprobar» included.
import { mkdirSync } from "node:fs";
import { test, expect, loginAdmin, artifactPath, ADMIN_USER, ADMIN_PASS } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_solicitudes_detail_in_view", requests: 24, queue: true } });

test("a request picked far down the list opens its detail in view at 1280", async ({ page, app }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/cola");
  await page.goto("/admin/solicitudes", { waitUntil: "load", timeout: 20000 });
  const rows = page.locator("#list .sol-row");
  await expect(rows.first()).toBeVisible({ timeout: 15000 });
  // A long group shows its first five; the rest is one click away inside the group.
  // The desk opens the groups until the list is long enough to scroll the page.
  const more = page.locator("#list [data-sol-grp-mas]");
  for (let i = 0; i < 12 && (await rows.count()) < 20 && (await more.count()) > 0; i += 1) {
    const before = await rows.count();
    await more.first().click();
    await expect.poll(() => rows.count()).toBeGreaterThan(before);
  }
  await expect(rows.nth(19)).toBeVisible({ timeout: 15000 });
  const row = rows.nth(19);
  await row.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => window.scrollY), "the list is long enough to scroll the page").toBeGreaterThan(200);
  await row.locator(".sol-open").click();
  const detail = page.locator("aside.sol-det[data-id]");
  await expect(detail).toBeVisible({ timeout: 8000 });
  const aprobar = detail.locator('[data-act="aprobar"]');
  await expect(aprobar).toBeInViewport();
  await expect(detail.locator(".sol-det-n")).toBeInViewport();
});

// TASK-45 (25.10): «Solicitudes on the phone: the time of the turno is not visible… date and
// time on one line». A pending request shows when it arrived, an approved
// one the appointment it got: date and time, whole, on one line, inside the
// list (not under the detail panel), at every width — and the page never
// scrolls sideways for it.
const SHOTS = artifactPath("pinamar-turnos", "progressive-disclosure", "round2");
const WIDTHS = [["phone", 390, 844], ["tablet", 820, 1000], ["laptop", 1280, 800], ["desktop", 1440, 900]];

async function showTab(page, estado) {
  await page.evaluate((value) => {
    const select = document.querySelector("#f-estado");
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }, estado);
}

test("date and time of every request read on one line at phone, laptop and desktop", async ({ page, app }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginAdmin(page, app.baseUrl, ADMIN_USER, ADMIN_PASS, "/admin/cola");
  await page.goto("/admin/solicitudes", { waitUntil: "load", timeout: 20000 });
  await expect(page.locator("#list .sol-row").first()).toBeVisible({ timeout: 15000 });
  mkdirSync(SHOTS, { recursive: true });
  for (const [estado, cellClass] of [["pendiente", "sol-cell-cuando"], ["aprobada", "sol-cell-turno"]]) {
    await showTab(page, estado);
    const cell = page.locator(`#list .sol-row .${cellClass}`).first();
    for (const [label, width, height] of WIDTHS) {
      await page.setViewportSize({ width, height });
      await expect(cell, `${estado} at ${width}px: the date/time cell is shown`).toBeVisible({ timeout: 8000 });
      const m = await cell.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const list = el.closest(".sol-queue").getBoundingClientRect();
        const style = getComputedStyle(el);
        return {
          text: el.textContent.trim(),
          clipped: el.scrollWidth > el.clientWidth + 1,
          lines: Math.round(box.height / (parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4)),
          insideList: box.left >= list.left - 1 && box.right <= list.right + 1,
          pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        };
      });
      expect(m.text, `${estado} at ${width}px`).toMatch(/\d{1,2}:\d{2}/);
      expect(m.clipped, `${estado} at ${width}px: «${m.text}» is cut`).toBe(false);
      expect(m.lines, `${estado} at ${width}px: «${m.text}» on one line`).toBeLessThanOrEqual(1);
      expect(m.insideList, `${estado} at ${width}px: «${m.text}» sits inside the list`).toBe(true);
      expect(m.pageOverflow, `${estado} at ${width}px: the page scrolls sideways`).toBeLessThanOrEqual(1);
      if (label === "phone" || label === "desktop") await page.screenshot({ path: `${SHOTS}/solicitudes-time-${estado}-${label}.png` });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await showTab(page, "pendiente");
});
