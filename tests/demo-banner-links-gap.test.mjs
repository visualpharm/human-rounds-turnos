// Plaquita de la demo (estudio de usabilidad 2026-10-16): «Consola médica» y «Gestión»
// salían pegadas a 360px. Con el HTML real de la banda, en un navegador real, entre dos
// links vecinos de la misma línea hay siempre hueco legible (≥ 12px) y ningún link se
// sale de la pantalla, en los anchos de teléfono y en escritorio.
import { test, hookOptions, openPage } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BANNER = execFileSync("python3", ["-c", `
import os
os.environ["TURNOS_ENV_FILE"] = "/dev/null"; os.environ["DATABASE_URL"] = ""; os.environ["TURNOS_FAKE_AI"] = "1"
from app.ops import demo_installation
print(demo_installation._banner("public", "es", switchable=True))
`], { cwd: ROOT, encoding: "utf8", env: { ...process.env, TURNOS_ENV_FILE: "/dev/null", DATABASE_URL: "" } });

// Un Chromium por archivo, un contexto nuevo por ancho (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

for (const width of [320, 360, 390, 430, 834, 1440]) {
  test(`links de la banda separados a ${width}px`, async () => {
    const page = await openPage(browser, { viewport: { width, height: 800 } });
    try {
      await page.setContent(`<!doctype html><html><head><style>:root{--accent:#874f2e;--panel:#fffdf8;--text:#1a1714;--text-2:#6c6862}body{margin:0}</style></head><body>${BANNER}</body></html>`);
      // Cajas del TEXTO de cada link (una por línea), no de la celda: lo que ve la persona.
      const links = await page.locator(".demo-installation__links a").evaluateAll((els) => els.map((a) => {
        const range = document.createRange(); range.selectNodeContents(a);
        return { text: a.textContent.trim(), rects: [...range.getClientRects()].map((r) => ({ l: r.left, r: r.right, t: r.top, b: r.bottom })) };
      }));
      assert.equal(links.length, 3);
      for (const link of links) for (const r of link.rects) assert.ok(r.l >= 0 && r.r <= width + 0.5, `${link.text} dentro de la pantalla (${r.l}–${r.r})`);
      for (let i = 0; i < links.length; i++) for (let j = i + 1; j < links.length; j++) {
        for (const a of links[i].rects) for (const b of links[j].rects) {
          const sameLine = Math.min(a.b, b.b) - Math.max(a.t, b.t) > 2;
          if (!sameLine) continue;
          const gap = Math.max(b.l - a.r, a.l - b.r);
          assert.ok(gap >= 12, `${links[i].text} / ${links[j].text}: hueco ${gap.toFixed(1)}px a ${width}px`);
        }
      }
    } finally { await page.context().close(); }
  });
}

// Revisión de la demo 2026-11-15: en un teléfono la banda es UNA línea — «Demo.» y los
// tres links en la misma fila, sin la frase de detalle — desde 360px; cada link ≥ 44px de alto.
for (const width of [360, 390, 430, 834, 1440]) {
  test(`la banda es una sola línea a ${width}px`, async () => {
    const page = await openPage(browser, { viewport: { width, height: 800 } });
    try {
      await page.setContent(`<!doctype html><html><head><style>:root{--accent:#874f2e;--panel:#fffdf8;--text:#1a1714;--text-2:#6c6862}body{margin:0;font-family:system-ui}</style></head><body>${BANNER}</body></html>`);
      const boxes = await page.locator(".demo-installation__lead, .demo-installation__links a").evaluateAll((els) => els.map((el) => {
        const r = el.getBoundingClientRect(); return { text: el.textContent.trim(), mid: (r.top + r.bottom) / 2, h: r.height };
      }));
      assert.equal(boxes.length, 4);
      for (const b of boxes) assert.ok(Math.abs(b.mid - boxes[0].mid) < 4, `${b.text} en la línea de «Demo.» a ${width}px`);
      if (width <= 760) {
        for (const b of boxes.slice(1)) assert.ok(b.h >= 44, `${b.text}: ${b.h}px de alto a ${width}px`);
        assert.equal(await page.locator(".demo-installation__detail").isVisible(), false);
      }
    } finally { await page.context().close(); }
  });
}
