// Stacked catalog cards (Servicios, Centros, Médicos) on a phone or tablet, measured in a real
// browser with the panel's own CSS and the real contact icons (format.mjs).
// Centros at 390 (ui-expert, 2026-11-08): the card rule made every link inline, so the phone and
// WhatsApp icons — a block glyph inside each link — broke into one icon per line above the
// address, and the separating dot hung beside the first icon with nothing level with it.
// Pinned here: the icons sit on one line with the address; a dot only ever stands between two
// visible parts of the same line — an empty part, a hidden part, or a part opening a line has
// none showing.
import { test, hookOptions, openPage } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ORIGIN = "http://cards.test";

// The card rules are the string pieces of admin-content.mjs inside its <=1024px block, read
// as the module writes them (each source line is one quoted piece followed by `+`).
function cardCss() {
  const lines = readFileSync(path.join(ROOT, "static/platform/admin-content.mjs"), "utf8").split("\n");
  const start = lines.findIndex((l, i) => l.trim() === '"@media(max-width:1024px){" +'
    && /#sec-servicios \.cnt-table-wrap/.test(lines[i + 1] || ""));
  assert.ok(start > 0, "the stacked-card block of admin-content.mjs");
  const pieces = [];
  for (let i = start; i < lines.length; i++) {
    const t = lines[i].trim();
    if (t.startsWith("//")) continue;
    const m = /^("(?:[^"\\]|\\.)*")\s*\+$/.exec(t);
    if (!m) break;
    pieces.push(JSON.parse(m[1]));
  }
  const css = pieces.join("");
  assert.match(css, /#sec-centros \.cnt-table tr,[^{]*\{display:flex;/);
  return css;
}

const CSS = cardCss();
const TOKENS = ":root{--text:#1a1714;--text-2:#6c6862;--hint-ink:#55514b;--accent:#874f2e;--border:#ddd;--hover-bg:#eee}"
  + "body{margin:0;padding:0 16px;font:15px/1.4 system-ui,sans-serif;color:var(--text)}";

const PHONE = "+54 9 2254 12-3456";
const COUNTRY_PHONE = JSON.parse(readFileSync(path.join(ROOT, "packs/ar/country.json"), "utf8")).phone;
const ROWS = {
  centros: [
    // The live demo's shape: a short address and a phone that also has WhatsApp.
    { id: "short", cells: { nombre: "Diagnostic Center", address: "<a href=\"#\">Central Dr. 234</a>", phone: "ICONS" } },
    // An address long enough to wrap: the icons stay one pair, wherever the line breaks.
    { id: "long", cells: { nombre: "Hospital Municipal Dr. Rodolfo Rossi", address: "<a href=\"#\">Avenida Constitución y Avenida Bunge, esquina sur, frente a la plaza principal 1234</a>", phone: "ICONS" } },
    // No phone: the address is the last part, nothing after it.
    { id: "nophone", cells: { nombre: "CAPS Ostende", address: "<a href=\"#\">Laguna 320</a>", phone: "<span class=\"cnt-muted\">—</span>" } },
  ],
  servicios: [
    // A doctor list with nothing to say sits between two parts that have.
    { id: "gap", cells: { nombre: "Audiometría", centro: "<a href=\"#\">Diagnostic Center</a>", modo: "Sin turno", medicos: "<span class=\"cnt-muted\">—</span>", horario: "Lunes a viernes de 7 a 12 h" } },
  ],
  medicos: [
    // Cuenta renders empty before the users load; it is the last part.
    { id: "empty", cells: { nombre: "Adrián Karam", especialidad: "Odontología", centro: "<a href=\"#\">Riverside Clinic</a>", horas_semana: "40 h", proximo_dia: "Mañana", cuenta: "" } },
  ],
};

function pageHtml() {
  const table = (sec, cls, rows) => `<section id="sec-${sec}"><div class="cnt-table-wrap"><table class="${cls}"><tbody>`
    + rows.map((r) => `<tr data-row="${r.id}">` + Object.entries(r.cells)
      .map(([k, v]) => `<td data-col="${k}">${k === "nombre" ? `<a href="#">${v}</a>` : v}</td>`).join("") + "</tr>").join("")
    + "</tbody></table></div></section>";
  return `<!doctype html><html><head><meta charset="utf-8"><script>window.SANA_COUNTRY_PHONE=${JSON.stringify(COUNTRY_PHONE)};window.SANA_I18N={active:"es",catalog:{}};</script><style>${TOKENS}${CSS}</style></head><body>`
    + table("centros", "cnt-table cnt-table--catalog cnt-table--centros", ROWS.centros)
    + table("servicios", "cnt-table cnt-table--catalog cnt-table--servicios", ROWS.servicios)
    + table("medicos", "cnt-table cnt-table--medicos", ROWS.medicos)
    + `<script type="module">
import { contactoIconoHtml } from "${ORIGIN}/static/platform/format.mjs";
for (const td of document.querySelectorAll("td")) if (td.textContent === "ICONS") td.innerHTML = contactoIconoHtml(${JSON.stringify(PHONE)});
document.body.dataset.ready = "1";
</script></body></html>`;
}

// What a person sees on each card: the visible parts in reading order with their boxes, and
// for each part whether its dot (the ::after in the 16px gap to its left) is drawn and unclipped.
function measure() {
  const out = {};
  for (const tr of document.querySelectorAll("tr[data-row]")) {
    const row = tr.getBoundingClientRect();
    const parts = [...tr.children].filter((td) => td.dataset.col && td.dataset.col !== "nombre").map((td) => {
      const r = td.getBoundingClientRect();
      const after = getComputedStyle(td, "::after");
      const range = document.createRange(); range.selectNodeContents(td);
      const ink = [...range.getClientRects()].filter((x) => x.width > 0 && x.height > 0);
      return {
        col: td.dataset.col, shown: getComputedStyle(td).display !== "none" && r.width > 0,
        left: r.left, right: r.right, top: r.top, bottom: r.bottom, inked: ink.length > 0,
        dot: after.content === "\"·\"", dotRight: r.left, dotWidth: parseFloat(after.width) || 0,
      };
    });
    const icons = [...tr.querySelectorAll(".cnt-ico")].map((s) => { const r = s.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; });
    out[tr.dataset.row] = { left: row.left, right: row.right, clipMargin: parseFloat(getComputedStyle(tr).overflowClipMargin) || 0, parts, icons };
  }
  return out;
}

let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

// 360/390: a phone (44px tap boxes); 834: a tablet with a mouse, still stacked (32px boxes).
for (const width of [360, 390, 834]) {
  test(`catalog cards at ${width}px: icons on the address line, a dot only between two visible parts`, async () => {
    const page = await openPage(browser, { viewport: { width, height: 900 } });
    try {
      await page.route(`${ORIGIN}/**`, (route) => {
        const rel = new URL(route.request().url()).pathname.replace(/^\/+/, "");
        if (rel === "") return route.fulfill({ status: 200, contentType: "text/html", body: pageHtml() });
        const file = path.join(ROOT, rel);
        // The i18n catalog (/api/i18n/es.json) is a server route: the Spanish fallbacks are enough.
        if (!file.startsWith(ROOT + path.sep) || !existsSync(file)) return route.fulfill({ status: 404, body: "" });
        const type = file.endsWith(".mjs") ? "text/javascript" : file.endsWith(".svg") ? "image/svg+xml" : "text/plain";
        return route.fulfill({ status: 200, contentType: type, body: readFileSync(file) });
      });
      await page.goto(`${ORIGIN}/`);
      await page.waitForSelector("body[data-ready='1']");
      const cards = await page.evaluate(measure);

      for (const [id, card] of Object.entries(cards)) {
        const shown = card.parts.filter((p) => p.shown);
        // An empty or "—" part leaves the card, so no dot is left in front of nothing.
        for (const p of shown) assert.ok(p.inked, `${id}: ${p.col} is shown but has nothing in it at ${width}px`);
        for (const p of card.parts.filter((x) => !x.shown)) assert.ok(["phone", "medicos", "cuenta"].includes(p.col), `${id}: ${p.col} hidden at ${width}px`);
        for (const p of shown) {
          assert.ok(p.dot, `${id}: ${p.col} carries the separating dot`);
          const sameLine = shown.filter((q) => q !== p && q.right <= p.left + 0.5 && Math.min(q.bottom, p.bottom) - Math.max(q.top, p.top) > 4);
          if (sameLine.length) {
            // The dot sits in the 16px gap, after the previous visible part on the same line.
            const prev = Math.max(...sameLine.map((q) => q.right));
            assert.ok(p.left - prev >= 15.5 && p.left - prev <= 16.5, `${id}: ${p.col} is 16px after the part before it (${(p.left - prev).toFixed(1)}) at ${width}px`);
          } else {
            // A part opening a line has its dot outside the card, past the clip margin.
            const dotInkRight = p.dotRight - p.dotWidth / 2 + 2;
            assert.ok(dotInkRight < card.left - card.clipMargin, `${id}: the dot before ${p.col} opens a line and must be clipped (${dotInkRight.toFixed(1)} vs ${card.left - card.clipMargin}) at ${width}px`);
          }
        }
      }

      for (const id of ["short", "long"]) {
        const card = cards[id];
        assert.equal(card.icons.length, 2, `${id}: phone and WhatsApp icons`);
        const [tel, wa] = card.icons;
        assert.ok(Math.abs(tel.top - wa.top) < 1, `${id}: the two icons share one line at ${width}px (${tel.top} / ${wa.top})`);
        assert.ok(wa.left > tel.right, `${id}: WhatsApp follows the phone on the same line at ${width}px`);
      }
      // The live demo's card: the icons stand on the address line, level with its text.
      const short = cards.short;
      const address = short.parts.find((p) => p.col === "address");
      const mid = (short.icons[0].top + short.icons[0].bottom) / 2;
      assert.ok(mid > address.top && mid < address.bottom, `the icons sit on the address line at ${width}px (icon middle ${mid.toFixed(1)}, address ${address.top.toFixed(1)}–${address.bottom.toFixed(1)})`);
      assert.ok(short.icons[0].left > address.right, `the phone icon follows the address at ${width}px`);
      // The phone glyph stands as far after the dot as the dot after the address (8px each side).
      const glyphGap = short.icons[0].left - address.right;
      assert.ok(glyphGap >= 14 && glyphGap <= 18, `phone glyph ${glyphGap.toFixed(1)}px after the address at ${width}px`);
    } finally { await page.context().close(); }
  });
}
