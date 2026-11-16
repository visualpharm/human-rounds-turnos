// The panel's phone gutter, swept screen by screen: at 390px nothing a person
// reads or presses may touch the edge of the screen. Under 720px the shared
// content wrap drops its own side padding (lists and request cards run full
// bleed and inset their own rows), so every other view has to get its 16px
// from somewhere — the patient record (Pacientes → ficha) did not, and its
// headings, fields, links and «Guardar» sat on the left edge with «Archivar»
// on the right one (demo, 25/10).
//
// Every screen of the sidebar, the first record or view each one links to
// (`?item=`, `?patient=`, `?view=`) and the new-patient form. A text line or a
// control closer than 12px to either edge is a finding; each page is also
// saved at 390px (and the patient record at 1280px) for the before/after look.
import os from "node:os";
import path from "node:path";
import { test, expect, loginAdmin, saveShot } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_phone_gutter", requests: true, queue: true } });

const EVIDENCE = path.join(os.homedir(), ".claude/artifacts/pinamar-turnos/ficha-phone-edge");
const MIN = 12;
const PATIENT = { dni: "30111222", nombre: "Alejo", apellido: "Varela", nacimiento: "1978-10-28", sexo: "M",
  contacto: "2254445566", email: "alejo@example.test" };

/** Text lines and controls of the screen body that sit closer than MIN to a side. */
async function edgeFindings(page, min) {
  return page.evaluate((min) => {
    const width = document.documentElement.clientWidth;
    const main = document.querySelector(".adm5-main") || document.body;
    const skip = ".adm-topbar,.adm5-side,dialog,[role=dialog],.toast,#toast,[hidden]";
    const shown = (el) => {
      for (let e = el; e && e !== document.body; e = e.parentElement) {
        const cs = getComputedStyle(e);
        if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) === 0) return false;
        if (cs.position === "fixed") return false;  // toasts, sticky bars: not the page body
      }
      return true;
    };
    // What the person sees: a line cut by an ellipsis or a scroll box ends
    // where its clipping ancestor ends, not where the full text would.
    const clipped = (el, rect) => {
      let l = rect.left, r = rect.right, scrolled = false;
      for (let e = el; e && e !== document.body; e = e.parentElement) {
        const cs = getComputedStyle(e);
        if (cs.overflowX === "visible") continue;
        const box = e.getBoundingClientRect();
        const px = (v) => parseFloat(v) || 0;
        // An ellipsis stops at the content box, inside the padding.
        const inner = cs.textOverflow === "ellipsis";
        const bl = box.left + px(cs.borderLeftWidth) + (inner ? px(cs.paddingLeft) : 0);
        const br = box.right - px(cs.borderRightWidth) - (inner ? px(cs.paddingRight) : 0);
        // A strip that scrolls sideways (the reports' tabs) cuts its next item at its own
        // edge on purpose: the cut says there is more, and a swipe brings the item in whole.
        // A table that outgrew its wrap is not such a strip: its cut cells stay a finding.
        if (!e.querySelector("table") && (cs.overflowX === "auto" || cs.overflowX === "scroll") && e.scrollWidth > e.clientWidth + 1
          && (l < bl - 1 || r > br + 1)) scrolled = true;
        l = Math.max(l, bl);
        r = Math.min(r, br);
      }
      return { left: l, right: r, width: r - l, height: rect.height, bottom: rect.bottom, scrolled };
    };
    const alpha = (color) => {
      if (!color || color === "transparent") return 0;
      const m = color.match(/rgba?\(([^)]+)\)/);
      if (!m) return 1;
      const parts = m[1].split(/[ ,/]+/).filter(Boolean);
      return parts.length > 3 ? parseFloat(parts[3]) : 1;
    };
    // A control with no frame and no fill is seen as what it draws, not as its box: the lists'
    // search is its magnifier and words (the frame it shows on hover opens outside the left
    // edge, 2026-11-08), an icon button is its icon (its touch zone may reach the screen edge).
    const drawn = (el) => {
      const cs = getComputedStyle(el);
      const raw = el.getBoundingClientRect();
      const border = parseFloat(cs.borderLeftWidth) > 0 && cs.borderLeftStyle !== "none" && alpha(cs.borderLeftColor) > 0;
      if (border || alpha(cs.backgroundColor) > 0 || cs.boxShadow !== "none") return raw;
      if (el.tagName === "BUTTON") {
        const range = document.createRange();
        range.selectNodeContents(el);
        const inner = range.getBoundingClientRect();
        return inner.width > 0 ? inner : raw;
      }
      if (el.tagName !== "INPUT") return raw;
      const icon = cs.backgroundImage !== "none" && /px$/.test(cs.backgroundPositionX) ? parseFloat(cs.backgroundPositionX) : null;
      const left = raw.left + parseFloat(cs.borderLeftWidth) + (icon !== null ? icon : parseFloat(cs.paddingLeft));
      return { left, right: raw.right, width: raw.right - left, height: raw.height, bottom: raw.bottom };
    };
    const out = [];
    const note = (el, raw, label) => {
      const rect = clipped(el, raw);
      if (rect.scrolled) return;
      if (rect.width <= 1 || rect.height <= 1) return;  // nothing visible, or a screen-reader-only line
      if (rect.bottom < 0) return;
      const left = Math.round(rect.left);
      const right = Math.round(width - rect.right);
      if (left < min || right < min) out.push(`${label} left ${left}px right ${right}px`);
    };
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement;
      const text = n.textContent.replace(/\s+/g, " ").trim();
      if (!text || !el || ["SCRIPT", "STYLE", "OPTION", "TEMPLATE"].includes(el.tagName)) continue;
      if (el.closest(skip) || !shown(el)) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const rect of range.getClientRects()) note(el, rect, `«${text.slice(0, 40)}»`);
    }
    for (const el of main.querySelectorAll("input:not([type=hidden]),select,textarea,button,.btn")) {
      if (el.closest(skip) || !shown(el)) continue;
      note(el, drawn(el), `<${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}>`);
      // Whatever it draws, a control's box never leaves the screen.
      const box = el.getBoundingClientRect();
      if (!clipped(el, box).scrolled && box.width > 1 && (box.left < -0.5 || box.right > width + 0.5)) {
        out.push(`<${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}> box leaves the screen: left ${Math.round(box.left)}px right ${Math.round(width - box.right)}px`);
      }
    }
    return [...new Set(out)];
  }, min);
}

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  let previous;
  await expect.poll(async () => {
    const current = await page.evaluate(() => document.querySelector(".adm5-main")?.innerHTML.length || 0);
    const stable = current === previous;
    previous = current;
    return stable;
  }, { timeout: 5000, intervals: [150, 300, 500] }).toBe(true).catch(() => {});
}

const shotName = (href) => href.replace(/^\/admin\/?/, "").replace(/[^a-z0-9]+/gi, "-").replace(/-+$/, "") || "admin";

test("no admin screen touches the phone's edge at 390px", async ({ page, app }, testInfo) => {
  test.setTimeout(300_000);
  const evidence = path.join(EVIDENCE, new Date().toISOString().replace(/[:.]/g, "-"));
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAdmin(page, app.baseUrl);
  await page.waitForLoadState("load");
  const created = await page.request.post("/api/admin/patients", { data: PATIENT });
  expect(created.ok(), `seed the patient record (${created.status()})`).toBe(true);

  const screens = await page.locator("#adm-nav a[href^='/admin/']").evaluateAll(
    (els) => [...new Set(els.map((a) => a.getAttribute("href")))]);
  expect(screens.length, `sidebar lists the screens (${screens.length})`).toBeGreaterThanOrEqual(8);
  const queue = [...screens, "/admin/account", "/admin/pacientes?new=1", `/admin/pacientes?patient=${PATIENT.dni}`];
  const seen = new Set(queue);
  const findings = [];
  while (queue.length) {
    const href = queue.shift();
    await page.goto(href, { waitUntil: "load", timeout: 20000 });
    await settle(page);
    await saveShot(testInfo, page, path.join(evidence, `390-${shotName(href)}.png`), { fullPage: true, attach: false });
    for (const f of await edgeFindings(page, MIN)) findings.push(`${href} ${f}`);
    if (href.includes("?")) continue;
    const deeper = await page.locator("main a[href^='/admin/'][href*='?']").evaluateAll(
      (els) => els.map((a) => a.getAttribute("href")));
    // Two per kind: the first is often `?item=new`, the second a real record.
    const perScreen = new Map();
    for (const link of deeper) {
      const kind = link.split("=")[0];
      const n = perScreen.get(kind) || 0;
      if (seen.has(link) || n >= 2) continue;
      perScreen.set(kind, n + 1);
      seen.add(link);
      queue.push(link);
    }
  }
  const pages = [...seen].join("\n") + "\n";
  await testInfo.attach("gutter-pages.txt", { body: pages, contentType: "text/plain" });
  const report = findings.join("\n") + "\n";
  await testInfo.attach("gutter-findings.txt", { body: report, contentType: "text/plain" });

  // The record the bug was found on, on the desktop too: the fix is phone-only.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/admin/pacientes?patient=${PATIENT.dni}`, { waitUntil: "load" });
  await settle(page);
  await saveShot(testInfo, page, path.join(evidence, "1280-ficha.png"), { fullPage: true });

  expect(findings, `content touching the phone's edge:\n${report}`).toEqual([]);
});
