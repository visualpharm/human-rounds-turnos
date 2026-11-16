import { before, after } from "node:test";
import { test, ceil, hookOptions } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

// Probador en sitio, 2026-10-21: after the photos he read "No pude leer el DNI de esa
// foto…" while the fields HAD been filled. Each side now keeps its own
// line: a back that can't be read never covers the front that was. The real
// registration form, synthetic OCR answers, no server.
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
after(async () => { await browser?.close(); });

const PIXEL = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==", "base64");

async function fixture(t, ocr) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  ceil(page);
  t.after(() => page.close());
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  const catalog = JSON.parse(readFileSync(new URL("../locales/es/account.form.json", import.meta.url), "utf8"));
  await page.route("**/*", route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (["/static/platform/account-form.mjs", "/static/platform/i18n.mjs"].includes(path)) {
      return route.fulfill({ contentType: "text/javascript", body: readFileSync(new URL(`..${path}`, import.meta.url), "utf8") });
    }
    if (path === "/account") return route.fulfill({ contentType: "text/html", body: `<!doctype html>
      <html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
      <script>window.SANA_I18N=${JSON.stringify({ active: "es", catalog })};
      window.SANA_STATIC={"platform/i18n.mjs":"/static/platform/i18n.mjs"};</script></head>
      <body><main id="host"></main><script type="module">
      import {renderAuth} from '/static/platform/account-form.mjs';
      renderAuth(document.getElementById('host'),{defaultMode:'registro'});
      </script></body></html>` });
    if (path === "/api/account/config") return route.fulfill({ json: {} });
    if (path === "/api/ai-status") return route.fulfill({ json: { disponible: true } });
    if (path === "/api/puco") return route.fulfill({ json: { disponible: false } });
    if (path === "/api/ocr-dni") return route.fulfill({ json: ocr[req.postDataJSON().lado] || { ok: false } });
    return route.fulfill({ status: 404, body: "Outside synthetic test scope" });
  });
  await page.goto("https://scan.test/account");
  await page.locator("#sc-scan-f").waitFor();
  t.after(() => assert.deepEqual(errors, []));
  return page;
}

async function upload(page, side) {
  await page.locator(side === "frente" ? "#sc-scan-f-file" : "#sc-scan-d-file")
    .setInputFiles({ name: "dni.png", mimeType: "image/png", buffer: PIXEL });
}

test("a back that can't be read keeps the front's success and names the back", async t => {
  const page = await fixture(t, {
    frente: { ok: true, patient: { dni: "30123456", apellido: "PEREZ", nombres: "JUAN", sexo: "M", nacimiento: "1980-05-09" } },
    foto: { ok: false }, dorso: { ok: false }
  });
  assert.match(await page.locator(".sc-scan-title").textContent(), /^Opcional/);
  await upload(page, "frente");
  await page.locator("#sc-scan-status .ok").waitFor();
  assert.equal(await page.locator("#sc-apellido").inputValue(), "Perez");
  await upload(page, "dorso");
  await page.locator("#sc-scan-status .fail").waitFor();
  const text = await page.locator("#sc-scan-status").innerText();
  assert.match(text, /Frente leído/);
  assert.match(text, /No pude leer el domicilio del dorso/);
  assert.doesNotMatch(text, /No pude leer el DNI/);
});

test("registration email placeholder is Spanish, and the generator reads as optional", async t => {
  const page = await fixture(t, {});
  assert.equal(await page.locator("#sc-email").getAttribute("placeholder"), "vos@ejemplo.com");
  assert.equal(await page.locator("#sc-genbtn").textContent(), "O generá una automáticamente");
});
