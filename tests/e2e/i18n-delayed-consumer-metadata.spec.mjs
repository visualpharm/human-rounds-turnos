// Real-script regression for consumers that formerly froze T() fallbacks at
// script evaluation. This runs the production files over HTTP; only the page
// data endpoints and DOM fixture are synthetic.
//
// Port of tests/e2e/i18n-delayed-consumer-metadata.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - No app and no database: the test serves its own synthetic page from the `serve`
//   fixture (closed after the test) and uses the runner's page instead of its own
//   chromium.launch(). The file is `hermetic` (appOptions only names it
//   for tests/e2e-spec-isolation.test.mjs).
// - The withheld i18n module is a `holdGate`: `held` is what the server handler awaits,
//   `release` is also called on cleanup, so a failing test never leaves the response
//   hanging (replaces the try/finally releaseI18n()).
// - «The i18n module was requested within 5 s» was a Promise.race against a timer; it
//   is now `expect.poll` on a counter with the same 5 s limit and message, and the
//   `window.__requests` wait is an `expect.poll` as well (5 s).
// - Waits for the English->Spanish-free title and the simulator row are web-first
//   (`toHaveText`, `toBeVisible`, 5 s). The «still English during the delay» read and the
//   final metadata read stay point-in-time snapshots.
// - Locators: `#tid-*`, `#request-columns [data-sol-sort]` stay CSS - ids and data
//   attributes of the production modules under test are the contract being exercised.
import { readFileSync } from "node:fs";
import path from "node:path";
import { test, expect, holdGate, ROOT } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_i18n_delayed_consumer_metadata", hermetic: true } });

const SOURCES = {
  requests: readFileSync(path.join(ROOT, "static/platform/admin-solicitudes.mjs"), "utf8"),
  preferences: readFileSync(path.join(ROOT, "static/platform/admin-preferences.mjs"), "utf8"),
  simulator: readFileSync(path.join(ROOT, "static/platform/auth-test.mjs"), "utf8"),
  i18n: readFileSync(path.join(ROOT, "static/platform/i18n.mjs"), "utf8"),
  format: readFileSync(path.join(ROOT, "static/platform/format.mjs"), "utf8"),
  dates: readFileSync(path.join(ROOT, "static/platform/dates.mjs"), "utf8"),
};

function fixtureHtml() {
  const catalog = {
    "admin.queue.solicitudes.col_lectura_ia": "AI reading",
    "admin.queue.table.paciente": "Patient",
    "account.mine.auth_test.title": "{provider} authentication simulator",
  };
  const phone = JSON.parse(readFileSync(path.join(ROOT, "packs/ar/country.json"), "utf8")).phone;
  return `<!doctype html><html lang="en"><head><title>Server-provided English title</title>
<script>window.SANA_I18N=${JSON.stringify({ active: "en", catalog })};
window.SANA_COUNTRY_PHONE=${JSON.stringify(phone)};
window.SANA_STATIC={"platform/i18n.mjs":"/static/platform/i18n.1234abcd.mjs","platform/format.mjs":"/static/platform/format.mjs","platform/dates.mjs":"/static/platform/dates.mjs","platform/admin-preferences.mjs":"/static/platform/admin-preferences.mjs","platform/admin-solicitudes.mjs":"/static/platform/admin-solicitudes.1234abcd.mjs"};
window.sanaImportStatic=(name)=>import(window.SANA_STATIC[name]);</script>
<script type="module">window.__requests=import(window.SANA_STATIC["platform/admin-solicitudes.mjs"]);</script><script type="module">import {mount} from '/static/platform/auth-test.mjs'; mount(document);</script>
</head><body><div id="request-columns"></div><h1 id="tid-title">Server-provided English title</h1>
<span id="tid-provider"></span><table><tbody id="tid-rows"></tbody></table><p id="tid-msg"></p>
<dialog id="tid-dialog"></dialog><form id="tid-form"><input id="tid-original"><input id="tid-dni" required>
<input id="tid-name" required><input id="tid-surname" required><input id="tid-birth" required><select id="tid-sex"><option value="F">F</option></select>
<input id="tid-phone"><input id="tid-email"><input id="tid-address"><input id="tid-locality"><p id="tid-form-msg"></p><button id="tid-save"></button></form>
<button id="tid-new"></button><button id="tid-close"></button><button id="tid-random"></button></body></html>`;
}

test("delayed native i18n keeps Requests metadata and simulator title in English", async ({ page, serve, cleanup }) => {
  const i18nGate = holdGate(cleanup);
  let i18nRequests = 0;
  const { url } = await serve(async (request, response) => {
    const js = (body) => { response.writeHead(200, { "Content-Type": "text/javascript" }); response.end(body); };
    const json = (body) => { response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify(body)); };
    if (request.url === "/") {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end(fixtureHtml());
    } else if (request.url === "/static/platform/admin-solicitudes.1234abcd.mjs") {
      js(SOURCES.requests);
    } else if (request.url === "/static/platform/admin-preferences.mjs") {
      js(SOURCES.preferences);
    } else if (request.url === "/static/platform/auth-test.mjs") {
      js(SOURCES.simulator);
    } else if (request.url === "/static/platform/format.mjs") {
      js(SOURCES.format);
    } else if (request.url === "/static/platform/dates.mjs") {
      js(SOURCES.dates);
    } else if (request.url === "/static/platform/i18n.1234abcd.mjs" || request.url === "/static/platform/i18n.mjs") {
      i18nRequests++;
      await i18nGate.held;
      js(SOURCES.i18n);
    } else if (request.url === "/api/account/test-identities") {
      json({ identidades: [{
        dni: "30123456", nombre: "Ada", apellido: "Lovelace", nacimiento: "1815-12-10", sexo: "F",
      }] });
    } else if (request.url === "/api/admin/preferences") {
      json({ items: {} });
    } else {
      response.writeHead(404).end();
    }
  });

  await page.goto(url + "/", { waitUntil: "domcontentloaded", timeout: 10_000 });
  await expect.poll(() => i18nRequests, { message: "Native i18n module was not requested", timeout: 5_000 }).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() => !!window.__requests), { timeout: 5_000 }).toBe(true);

  // The old auth-test.js assigned its Spanish fallback before i18n settled.
  // The server-rendered English title must remain visible during the delay.
  expect(await page.locator("#tid-title").innerText()).toBe("Server-provided English title");

  i18nGate.release();
  await expect(page.locator("#tid-title")).toHaveText("Mi Argentina authentication simulator", { timeout: 5_000 });
  await expect(page.locator("#tid-rows .tid-person")).toBeVisible({ timeout: 5_000 });

  const rendered = await page.evaluate(async () => {
    const Solicitudes = await window.__requests;
    const cols = Solicitudes.columns().filter((column) =>
      ["veredicto", "paciente"].includes(column.key));
    document.getElementById("request-columns").innerHTML = Solicitudes.colsHtml(cols, { key: "paciente", dir: 1 });
    return {
      metadata: Object.fromEntries(cols.map((column) => [column.key, column.label])),
      renderedColumns: [...document.querySelectorAll("#request-columns [data-sol-sort]")]
        .map((button) => button.textContent.trim()),
      title: document.title,
      heading: document.getElementById("tid-title").textContent,
    };
  });
  expect(rendered.metadata).toEqual({ veredicto: "AI reading", paciente: "Patient" });
  expect(rendered.renderedColumns).toEqual(["AI reading", "Patient"]);
  expect(rendered.heading).toBe("Mi Argentina authentication simulator");
  expect(rendered.title).toBe("Mi Argentina authentication simulator");
});
