// «Pacientes de hoy» with the desk's HSI extension installed: the REAL content
// script and stylesheet of extensions/hsi-humanizador (content-turnos.js/.css)
// decorate the REAL /admin/cola rows, against the real ASGI app and its
// fingerprinted assets. Only the Chrome extension API is stubbed (storage,
// runtime messaging) — the row markup, the panel's CSS and the extension's own
// CSS are the production ones.
//
// Launch plan P2 (hospital map, desk d23): the «Cargar en HSI» button sat on top
// of the patient's name — the name column is narrow and the extension reserved
// 132px of padding in it for an absolutely positioned button, so the name was
// clipped to nothing. Whatever the width, the desk must read who the row is.
//
// Port of tests/e2e/hsi-extension-row.e2e.mjs (4 tests, one per width, same titles and order).
//
// Differences from the node:test original:
// - The app comes from the worker-scoped `app` fixture (test.use({ appOptions }) with the same
//   `queue: true`); the runner's page replaces the hand-made context per width (the width is
//   set with page.setViewportSize() before signing in with loginAdmin()). The extension is
//   injected exactly as before: addStyleTag for the stylesheet, a stub `chrome.*` through
//   evaluate, then addScriptTag for lib/shared.js and content-turnos.js.
// - The per-run evidence directory (allocateEvidenceRunDir, lib/hsi-evidence.mjs, unchanged)
//   is allocated in a beforeAll instead of at module import, so listing the tests does not
//   create empty directories; the screenshots go through saveShot() into the same directory
//   tree (viewport screenshots, as before) and are attached to the report.
// - settle(): the original waited 300 ms and two animation frames. It now waits until the
//   measured layout (overflow, name fit, row geometry) is identical in three consecutive reads
//   100 ms apart, then two frames; it gives up after 5 s and measures anyway, like the sleep
//   always did. All geometry is still computed in the page by the same functions.
// - Waits are web-first with the original timeouts (12 s for the queue controls, group and
//   first row, 5 s for the extension button). The geometry assertions are plain expects on
//   the in-page measurements, with the same messages.
// - Locators left as CSS: the queue's own classes / data attributes (#sec-cola, tr[data-cola-row],
//   td[data-col="paciente"], .queue-pat-link, .queue-controls, button.queue-doctor-link) and the
//   extension's [data-hr-hsi] / .hr-hsi button - geometry and overlap are the subject.
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { test, expect, loginAdmin, saveShot, ROOT } from "./lib/fixtures.mjs";
import { allocateEvidenceRunDir } from "./lib/hsi-evidence.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_hsi_ext_row", queue: true } });

const EXT = join(ROOT, "extensions", "hsi-humanizador");
const SHARED_JS = readFileSync(join(EXT, "lib", "shared.js"), "utf8");
const CONTENT_JS = readFileSync(join(EXT, "content-turnos.js"), "utf8");
const CONTENT_CSS = readFileSync(join(EXT, "content-turnos.css"), "utf8");
const WIDTHS = [390, 1024, 1280, 1440];

let evidence = "";
test.beforeAll(() => {
  evidence = allocateEvidenceRunDir({
    root: join(homedir(), ".claude/artifacts/human-rounds/hsi-extension-row"), label: "run",
  });
});

// What the extension's manifest does on /admin/*: its stylesheet and its
// two content scripts (lib/shared.js, content-turnos.js), with the Chrome API
// they call answered in memory.
async function installExtension(page) {
  await page.addStyleTag({ content: CONTENT_CSS });
  await page.evaluate(() => {
    const noop = () => {};
    window.chrome = {
      runtime: { id: "stub", sendMessage: () => Promise.resolve({ ok: true }), onMessage: { addListener: noop } },
      storage: { local: { get: () => Promise.resolve({ turnosCargados: {}, modo: "boton" }), set: noop },
                 onChanged: { addListener: noop } },
    };
  });
  await page.addScriptTag({ content: SHARED_JS });
  await page.addScriptTag({ content: CONTENT_JS });
}

// Per row: the patient's name (the panel's `.queue-pat-link`, the text the
// desk reads) against the cell's visible area and the extension's button.
function rowsGeometry(page) {
  return page.evaluate(() => [...document.querySelectorAll("#sec-cola tr[data-cola-row]")].map((row) => {
    const cell = row.querySelector('td[data-col="paciente"]');
    const box = cell && cell.querySelector(".hr-hsi");
    const button = box && box.querySelector("[data-hr-hsi]");
    const link = cell && cell.querySelector(".queue-pat-link");
    if (!cell || !button || !link) return { id: row.getAttribute("data-cola-row"), decorated: !!button, link: !!link };
    const range = document.createRange();
    range.selectNodeContents(link);
    const text = range.getBoundingClientRect();
    const c = cell.getBoundingClientRect();
    const b = button.getBoundingClientRect();
    const style = getComputedStyle(cell);
    const contentRight = c.right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth);
    const clipRight = style.overflow === "visible" ? Infinity : c.right - parseFloat(style.borderRightWidth);
    const overlaps = text.right > b.left + 1 && text.left < b.right - 1 && text.bottom > b.top + 1 && text.top < b.bottom - 1;
    return { id: row.getAttribute("data-cola-row"), decorated: true, link: true, name: link.textContent.trim(),
      textWidth: text.width, overlaps,
      // the whole name inside what the cell shows (its content box, or at
      // least its clip edge when it lets content run into the padding)
      cut: text.right > Math.min(contentRight, clipRight) + 1 || link.scrollWidth > link.clientWidth + 1,
      cell: { left: c.left, right: c.right, contentRight }, text: { left: text.left, right: text.right },
      button: { left: b.left, right: b.right, top: b.top, bottom: b.bottom } };
  }));
}

// How far the queue's table and the page run past their right edge (0 when
// they fit) — the extension must not push the row off the screen either.
function overflow(page) {
  return page.evaluate(() => {
    const table = document.querySelector("#sec-cola table.queue-table");
    const wrap = table && table.closest(".cnt-table-wrap");
    const doc = document.documentElement;
    return {
      table: table && wrap ? Math.max(0, Math.round(table.getBoundingClientRect().right - wrap.getBoundingClientRect().right)) : null,
      page: Math.max(0, doc.scrollWidth - doc.clientWidth),
      button: Math.max(0, ...[...document.querySelectorAll("#sec-cola [data-hr-hsi]")].map((b) => {
        const cell = b.closest("td");
        return cell ? Math.round(b.getBoundingClientRect().right - cell.getBoundingClientRect().right) : 0;
      })),
    };
  });
}
// The panel alone (no extension): which names it already shows whole. A
// narrow screen may clip a long name by itself; the extension must not clip
// one more.
function namesWhole(page) {
  return page.evaluate(() => Object.fromEntries([...document.querySelectorAll("#sec-cola tr[data-cola-row]")].map((row) => {
    const cell = row.querySelector('td[data-col="paciente"]');
    const link = cell && cell.querySelector(".queue-pat-link");
    if (!link) return [row.getAttribute("data-cola-row"), false];
    const range = document.createRange();
    range.selectNodeContents(link);
    const style = getComputedStyle(cell);
    const right = cell.getBoundingClientRect().right - parseFloat(style.paddingRight);
    return [row.getAttribute("data-cola-row"), range.getBoundingClientRect().right <= right + 1];
  })));
}

// Wait until the measured layout stops moving (three identical reads in a row), then two
// frames. Best effort: after 5 s the caller measures anyway.
// The table engine may paint the list again while it measures: a row found a
// moment ago can be gone, so the scroll is retried on the fresh row.
async function scrollToFirstRow(page) {
  await expect(async () => {
    await page.locator("#sec-cola tr[data-cola-row]").first().scrollIntoViewIfNeeded({ timeout: 2000 });
  }).toPass({ timeout: 10000, intervals: [200, 500, 1000] });
}

async function settle(page) {
  let last = "", same = 0;
  await expect.poll(async () => {
    const now = JSON.stringify([await overflow(page), await namesWhole(page), await rowsGeometry(page)]);
    same = now === last ? same + 1 : 0;
    last = now;
    return same >= 2;
  }, { timeout: 5000, intervals: [100] }).toBe(true).catch(() => {});
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

for (const width of WIDTHS) {
  test(`«Cargar en HSI» never hides the patient's name at ${width}px`, async ({ page, app }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await loginAdmin(page, app.baseUrl);
    await page.goto("/admin/cola", { waitUntil: "load", timeout: 15000 });
    await expect(page.locator("#sec-cola .queue-controls")).toBeVisible({ timeout: 12000 });
    // One list (2026-11-08): the unassigned rows are there, nothing to open
    // (a click would save the narrowed list in the account's preferences).
    await expect(page.locator('#sec-cola button.queue-doctor-link[data-cola-doctor="_sin_medico"]').first()).toBeVisible({ timeout: 12000 });
    await expect(page.locator("#sec-cola tr[data-cola-row]").first()).toBeVisible({ timeout: 12000 });
    await settle(page);
    const before = await overflow(page);
    const whole = await namesWhole(page);
    await scrollToFirstRow(page);
    await saveShot(testInfo, page, join(evidence, `cola-${width}-sin-extension.png`));
    await installExtension(page);
    await expect(page.locator("#sec-cola [data-hr-hsi]").first()).toBeVisible({ timeout: 5000 });
    await settle(page);
    const rows = await rowsGeometry(page);
    const after = await overflow(page);
    await scrollToFirstRow(page);
    await saveShot(testInfo, page, join(evidence, `cola-${width}.png`));
    expect(rows.length, "the unassigned group shows its patients: " + JSON.stringify(rows)).toBeGreaterThanOrEqual(3);
    for (const row of rows) {
      expect(row.decorated, "every row gets the button: " + JSON.stringify(row)).toBe(true);
      expect(row.name, "the row has a name to show: " + JSON.stringify(row)).toBeTruthy();
      expect(row.textWidth, "the name is laid out: " + JSON.stringify(row)).toBeGreaterThan(0);
      expect(row.overlaps, "the button never covers the name: " + JSON.stringify(row)).toBe(false);
      if (whole[row.id]) expect(row.cut, "a name the panel shows whole stays whole: " + JSON.stringify(row)).toBe(false);
    }
    const geo = JSON.stringify({ before, after });
    expect(after.table, "the row does not run past the table's edge: " + geo).toBeLessThanOrEqual(before.table + 1);
    expect(after.page, "the page gets no sideways scroll: " + geo).toBeLessThanOrEqual(before.page + 1);
    expect(after.button, "the button is whole inside its cell: " + geo).toBe(0);
    expect(errors).toEqual([]);
  });
}
