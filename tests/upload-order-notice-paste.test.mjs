// /upload-order (estudio de usabilidad 2026-10-16):
//  - el aviso de IA se lee UNA vez (el servidor ya lo trae; la zona no lo repite),
//  - la pista de pegado («Ctrl+V / ⌘V») sólo aparece con puntero fino:
//    en un teléfono (pointer: coarse) no se ve.
import { test, ceil, hookOptions } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { patientSiteSource } from "./helpers/patient-site-source.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MODULE = readFileSync(path.join(ROOT, "app/modules/turnos/wz-upload.mjs"), "utf8");
const NOTICE = "La inteligencia artificial lee tu orden";

async function mount(browser, contextOptions, withServerNotice) {
  const context = await browser.newContext(contextOptions);
  ceil(context);
  const page = await context.newPage();
  await page.route("https://zone.test/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/wz-upload.mjs") return route.fulfill({ contentType: "text/javascript", body: MODULE });
    return route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body>${withServerNotice ? '<p data-ai-notice="order">' + NOTICE + ". x</p>" : ""}
        <div id="host"></div>
        <script type="module">
          import { css, zoneHtml } from "/wz-upload.mjs";
          const style = document.createElement("style"); style.textContent = css; document.head.append(style);
          document.getElementById("host").innerHTML = zoneHtml();
          window.__ready = true;
        </script></body></html>`,
    });
  });
  await page.goto("https://zone.test/");
  await page.waitForFunction(() => window.__ready === true);
  return { context, page };
}

// Un Chromium por archivo, un contexto nuevo por página (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

test("el aviso de IA no se repite cuando el servidor ya lo trae", async () => {
  const withServer = await mount(browser, { viewport: { width: 1440, height: 900 } }, true);
  try {
    assert.equal(await withServer.page.evaluate((n) => document.body.innerText.split(n).length - 1, NOTICE), 1);
  } finally { await withServer.context.close(); }
  const alone = await mount(browser, { viewport: { width: 1440, height: 900 } }, false);
  try {
    assert.equal(await alone.page.evaluate((n) => document.body.innerText.split(n).length - 1, NOTICE), 1,
      "sin aviso del servidor, la zona sigue poniendo el suyo");
  } finally { await alone.context.close(); }
});

test("la pista de pegado se ve con puntero fino y no en un teléfono", async () => {
  const desktop = await mount(browser, { viewport: { width: 1440, height: 900 } }, true);
  try {
    assert.equal(await desktop.page.locator(".wz-up-paste").isVisible(), true);
    assert.match(await desktop.page.locator(".wz-up-paste").innerText(), /(⌘V|Ctrl\+V)/);
    // On a desktop the main button picks a file; «Tomá una foto» is the phone's.
    assert.equal(await desktop.page.locator(".wz-up-desk").isVisible(), true);
    assert.equal(await desktop.page.locator(".wz-up-touch").isVisible(), false);
  } finally { await desktop.context.close(); }
  const phone = await mount(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }, true);
  try {
    assert.equal(await phone.page.locator(".wz-up-paste").isVisible(), false);
    assert.equal(await phone.page.locator(".wz-up-touch").isVisible(), true);
    assert.equal(await phone.page.locator(".wz-up-desk").isVisible(), false);
  } finally { await phone.context.close(); }
});

test("la zona renderizada por el servidor también oculta la pista fuera de punteros finos", () => {
  const css = patientSiteSource();
  assert.match(css, /\.wz-up-paste\{display:none;[^}]*\}\s*\n\s*\.wz-up-desk\{display:none;\}\s*\n\s*@media\(hover:hover\) and \(pointer:fine\)\{\.wz-up-paste\{display:block;\}\.wz-up-desk\{display:inline;\}\.wz-up-touch,\.wz-up-sub\{display:none;\}\}/);
});
