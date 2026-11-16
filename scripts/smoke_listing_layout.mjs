// smoke_listing_layout.mjs — the appointment cell of the public lists, measured
// in a real browser on the deployed site (ui review 2026-11-15).
//
// Two regressions only a browser sees: «Más horarios» drifting back beside
// the booking button on a phone (its text spilling out of its own outline)
// and the slot date breaking over two lines. For every listed page, at 390
// and at 1440, each appointment cell must hold:
//   - «Más horarios» on its own line under the button, its text inside its frame;
//   - the button and the date each on one line.
//
// Usage: node scripts/smoke_listing_layout.mjs <base> <path> [<path> …]
// Basic auth, when the site asks for it, comes in SMOKE_LAYOUT_AUTH
// ("user:pass", never argv). Playwright resolves from this checkout, then from
// PW_ROOT (as in build_hsi_extension_guide.mjs: a worktree without
// node_modules borrows the main checkout's). Exit 0 = all green, 1 = a cell
// broke, 2 = no browser to measure with.

import { createRequire } from "module";
import fs from "fs";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";

export const WIDTHS = [390, 1440];

// One cell as measured in the page → the problems it shows (empty = fine).
// A cell: {time: rect|null, timeLines, more: rect|null, moreScroll, moreClient,
// notes: [{lines, text}]}. A rect: {top, bottom, left, right}.
export function judge(cell) {
  const out = [];
  if (cell.time && cell.timeLines > 1) out.push("el boton parte su texto en " + cell.timeLines + " lineas");
  for (const n of cell.notes || []) {
    if (n.lines > 1) out.push("la fecha «" + n.text + "» ocupa " + n.lines + " lineas");
  }
  if (cell.more) {
    if (cell.moreScroll > cell.moreClient + 1) out.push("«Mas horarios» se sale de su recuadro");
    if (cell.time && cell.more.top < cell.time.bottom - 1) out.push("«Mas horarios» al lado del boton, no debajo");
  }
  return out;
}

// Runs inside the page: the first cells of every listing table.
function measure() {
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
  };
  const lines = (el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const tops = new Set();
    for (const r of range.getClientRects()) if (r.width > 0) tops.add(Math.round(r.top));
    return tops.size;
  };
  return Array.from(document.querySelectorAll(".lst-timecell")).slice(0, 24).map((tc) => {
    const time = tc.querySelector(":scope>.lst-time");
    const more = tc.querySelector(":scope>.lst-more");
    return {
      time: time ? rect(time) : null,
      timeLines: time ? lines(time) : 0,
      more: more ? rect(more) : null,
      moreScroll: more ? more.scrollWidth : 0,
      moreClient: more ? more.clientWidth : 0,
      notes: Array.from(tc.querySelectorAll(":scope>.lst-time-note--when")).map((n) => ({ lines: lines(n), text: n.innerText.trim() })),
    };
  });
}

function loadPlaywright() {
  const roots = [path.dirname(path.dirname(fileURLToPath(import.meta.url)))];
  if (process.env.PW_ROOT) roots.push(process.env.PW_ROOT);
  for (const root of roots) {
    try {
      return createRequire(path.join(root, "package.json"))("playwright");
    } catch (_) { /* next root */ }
  }
  return null;
}

async function main() {
  const [base, ...paths] = process.argv.slice(2);
  const pw = loadPlaywright();
  if (!pw) {
    console.log("[FALLA] celda de turnos: no hay playwright para medir");
    return 2;
  }
  const auth = process.env.SMOKE_LAYOUT_AUTH || "";
  const cut = auth.indexOf(":");
  const browser = await pw.chromium.launch();
  let fails = 0;
  try {
    for (const width of WIDTHS) {
      const ctx = await browser.newContext({
        viewport: { width, height: 900 },
        // The demo picks its language from Accept-Language: measure the Spanish
        // labels a visitor here reads, whatever the machine's locale.
        locale: "es-AR",
        httpCredentials: cut > 0 ? { username: auth.slice(0, cut), password: auth.slice(cut + 1) } : undefined,
      });
      for (const where of paths) {
        const page = await ctx.newPage();
        let line;
        try {
          await page.goto(base + where, { waitUntil: "load", timeout: 45000 });
          // A cell, not a «Más horarios»: whether a row offers one is data.
          await page.waitForSelector(".lst-timecell", { timeout: 20000 });
          await page.evaluate(() => document.fonts.ready);
          const cells = await page.evaluate(measure);
          const bad = [];
          cells.forEach((c, i) => judge(c).forEach((p) => bad.push("fila " + (i + 1) + ": " + p)));
          line = bad.length
            ? "[FALLA] celda de turnos " + where + " @" + width + ": " + bad.slice(0, 3).join("; ")
            : "[OK] celda de turnos " + where + " @" + width + " (" + cells.length + " filas)";
          if (bad.length) fails += 1;
        } catch (e) {
          fails += 1;
          line = "[FALLA] celda de turnos " + where + " @" + width + ": " + String(e.message || e).split("\n")[0];
        }
        console.log(line);
        await page.close();
      }
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
  return fails ? 1 : 0;
}

if (process.argv[1] && pathToFileURL(fs.realpathSync(process.argv[1])).href === import.meta.url) {
  main().then((code) => process.exit(code), (e) => { console.log("[FALLA] celda de turnos: " + e); process.exit(1); });
}
