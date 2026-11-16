// Real synthetic installation: server-owned center markup and fingerprinted
// bytes, not a fixture that reproduces the application's loader.
//
// Port of tests/e2e/photo-zoom-canonical.e2e.mjs (6 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the
//   original dbName); there is no requireOk()/skip path. Pages come from the runner (a fresh
//   page per test, viewport set with page.setViewportSize() before the first goto); the
//   tests are independent, so no serial mode.
// - Evidence: the unique `center-run-*` directory under
//   ~/.claude/artifacts/human-rounds/photo-viewer-native is created in `test.beforeAll`
//   (the original's `test.before`; creating it at import time would also create it in the
//   runner process that only lists tests) and its path is recorded as a `diagnostic`
//   annotation (the original's `t.diagnostic`). The per-width screenshot goes through
//   `saveShot` to the same file name, and is attached to the report too. The original's
//   `center-<w>-failure.png` catch-block screenshot is replaced by the runner's
//   screenshot-on-failure and trace-on-retry (same as the other ports).
// - Waits are web-first where the original waited on one selector (`.lst-more`, the link's
//   accessible name, the `src` of the zoomed image); the compound «image complete, decoded
//   and opaque» wait stays a `waitForFunction`. Negative/guard checks (dead gallery
//   controls hidden, focus back on the trigger, no `.pz-open`, geometry, fit) stay
//   point-in-time reads.
// - Locators: `a[data-photozoom]`, `.lst-more`, `.pz-*` stay CSS - they are the viewer's own
//   markup contract; the accessible-name check already uses getByRole.
import { mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import os from "node:os";
import { test, expect, ROOT, saveShot, note } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_photo_viewer" } });

const source = readFileSync(path.join(ROOT, "static/platform/photo-zoom.mjs"));
const asset = "/static/platform/photo-zoom." + createHash("sha256").update(source).digest("hex").slice(0, 8) + ".mjs";
const evidenceRoot = path.join(os.homedir(), ".claude/artifacts/human-rounds/photo-viewer-native");
let evidence;

test.beforeAll(() => {
  mkdirSync(evidenceRoot, { recursive: true });
  evidence = mkdtempSync(path.join(evidenceRoot, "center-run-"));
});

for (const width of [360, 390, 768, 1024, 1280]) {
  test(`actual center photo opens and restores the page at ${width}px`, async ({ page }, testInfo) => {
    note(testInfo, "Unique photo evidence: " + evidence);
    await page.setViewportSize({ width, height: 900 });
    const errors = [], requests = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("request", (r) => requests.push(new URL(r.url()).pathname));
    await page.goto("/center/sampleton-hospital", { waitUntil: "load" });
    expect(requests.includes(asset), "real server must load exact candidate viewer").toBe(true);
    await expect(page.locator(".lst-more").first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      "the actual underlying center listing must fit before the viewer opens").toBe(true);
    const trigger = page.locator("a[data-photozoom]").first();
    const centerName = (await page.locator("h1").textContent()).trim();
    await expect(page.locator(".hero-photo").getByRole("link", { name: centerName, exact: true }),
      "photo link must have a real accessible name, not only a data attribute").toHaveCount(1);
    const imageUrl = await trigger.getAttribute("href");
    expect(imageUrl).toBeTruthy();
    await trigger.click();
    await page.waitForFunction(() => {
      const i = document.querySelector(".pz-open .pz-img");
      return i?.complete && i.naturalWidth > 0 && getComputedStyle(i).opacity === "1";
    });
    await expect(page.locator(".pz-img")).toHaveAttribute("src", imageUrl);
    expect(await page.locator(".pz-prev").isVisible(), "single-photo center has no dead gallery controls").toBe(false);
    expect(await page.locator(".pz-counter").isVisible()).toBe(false);
    expect(await page.evaluate(() => typeof window.PhotoZoom)).toBe("undefined");
    const geometry = await page.evaluate(() => {
      const image = document.querySelector(".pz-img").getBoundingClientRect();
      return { viewport: { width: innerWidth, height: innerHeight }, scrollWidth: document.documentElement.scrollWidth,
        image: image.toJSON(), overflowing: [...document.querySelectorAll("body *")].filter((el) => {
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && (rect.left < 0 || rect.right > innerWidth);
        }).map((el) => ({ tag: el.tagName, class: el.className, rect: el.getBoundingClientRect().toJSON() })) };
    });
    await saveShot(testInfo, page, path.join(evidence, `center-${width}.png`));
    const { image, viewport, scrollWidth } = geometry;
    expect(scrollWidth <= viewport.width && image.left >= 0 && image.right <= viewport.width && image.top >= 0 && image.bottom <= viewport.height,
      JSON.stringify(geometry)).toBe(true);
    await page.keyboard.press("Escape");
    expect(await trigger.evaluate((el) => document.activeElement === el)).toBe(true);
    expect(await page.locator(".pz-open").count()).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      "closing the viewer must leave a fitting page").toBe(true);
    expect(errors).toEqual([]);
  });
}

test("actual center retains its image link when the viewer fails", async ({ page, app }) => {
  let blocked = 0;
  await page.route(app.baseUrl + asset, (r) => { blocked++; return r.abort(); });
  await page.goto("/center/sampleton-hospital", { waitUntil: "load" });
  expect(blocked).toBe(1);
  const trigger = page.locator("a[data-photozoom]").first();
  const centerName = (await page.locator("h1").textContent()).trim();
  await expect(page.locator(".hero-photo").getByRole("link", { name: centerName, exact: true }),
    "accessible name must survive enhancement failure").toHaveCount(1);
  const expected = new URL(await trigger.getAttribute("href"), app.baseUrl).href;
  const popupPromise = page.waitForEvent("popup");
  await trigger.click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  expect(popup.url()).toBe(expected);
  expect(await popup.locator("img").evaluate((i) => i.complete && i.naturalWidth > 0)).toBeTruthy();
  await popup.close();
});
