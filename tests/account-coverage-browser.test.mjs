import { before, after } from "node:test";
import { test, ceil, hookOptions } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";
import { stableBox } from "./helpers/box.mjs";

// Actual shared component, synthetic fixtures, every request intercepted.
// No server, database, account, registry lookup or message provider is used.
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
after(async () => { await browser?.close(); });

async function fixture(t, { coverage = {}, mode = "editor", width = 390, puco = null, pucoWait = null } = {}) {
  const page = await browser.newPage({ viewport: { width, height: 844 } });
  ceil(page);
  t.after(() => page.close());
  const posts = [], errors = [];
  page.on("pageerror", e => errors.push(e.message));
  const catalog = JSON.parse(readFileSync(new URL("../locales/es/account.form.json", import.meta.url), "utf8"));
  let user = { dni: "05123456", nombre: "Demo", apellido: "Prueba", email: "demo@example.invalid", cobertura: coverage };
  await page.route("**/*", async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (req.method() === "POST") posts.push({ path, body: req.postDataJSON() });
    if (["/static/platform/account-form.mjs", "/static/platform/i18n.mjs"].includes(path)) {
      return route.fulfill({ contentType: "text/javascript", body: readFileSync(new URL(`..${path}`, import.meta.url), "utf8") });
    }
    if (path === "/account") return route.fulfill({ contentType: "text/html", body: `<!doctype html>
      <html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
      <style>body{font-family:Arial,sans-serif;margin:16px;--text:#1f1f1f;--text-2:#4a514c;--line:#ccd2cd;--accent:#176b43}</style>
      <script>window.SANA_I18N=${JSON.stringify({ active: "es", catalog })};
      window.SANA_STATIC={"platform/i18n.mjs":"/static/platform/i18n.mjs"};window.done=[];</script></head>
      <body><main id="host"></main><button id="outside">Otro campo</button><script type="module">
      import {renderAuth,renderEditar} from '/static/platform/account-form.mjs';
      ${mode === "editor" ? `renderEditar(document.getElementById('host'),{usuario:${JSON.stringify(user)},onSave:u=>window.done.push(u)});document.querySelector('[data-panel="cobertura"]').classList.add('on');` :
        "renderAuth(document.getElementById('host'),{onDone:u=>window.done.push(u)});"}
      </script></body></html>` });
    if (path === "/api/account/config") return route.fulfill({ json: {} });
    if (path === "/api/ai-status") return route.fulfill({ json: { disponible: false } });
    if (path === "/api/puco") {
      if (pucoWait) await pucoWait;
      return route.fulfill({ json: puco || { disponible: false } });
    }
    if (path === "/api/account/update") {
      user = { ...user, ...req.postDataJSON() };
      return route.fulfill({ json: { usuario: user } });
    }
    if (path === "/api/account/register") return route.fulfill({ json: { usuario: user } });
    return route.fulfill({ status: 404, body: "Outside synthetic test scope" });
  });
  await page.goto("https://coverage.test/account");
  await page.locator("#sc-os-0").waitFor();
  t.after(() => assert.deepEqual(errors, []));
  return { page, posts };
}

async function pick(page, query, name, row = 0) {
  await page.locator(`#sc-os-${row}`).fill(query);
  await page.locator(`#sc-os-${row}-list`).getByRole("option", { name, exact: true }).click();
}
async function saved(page, posts) {
  await page.waitForFunction(() => document.querySelector(".sc-save-msg.ok")?.textContent || window.done.length);
  return posts.filter(p => p.path === "/api/account/update").at(-1)?.body.cobertura;
}

async function saveMember(page, posts, value, row = 0) {
  // Focusing the member field blurs the previous field, whose own autosave can
  // answer first; wait for the save that carries the typed member number.
  const carries = body => JSON.stringify(body?.cobertura ?? null).includes(JSON.stringify(value));
  const response = page.waitForResponse(r => new URL(r.url()).pathname === "/api/account/update" && carries(r.request().postDataJSON()));
  await page.locator(`#sc-os-af-${row}`).fill(value);
  await page.locator("#outside").click();
  await response;
  return posts.filter(p => p.path === "/api/account/update" && carries(p.body)).at(-1).body.cobertura;
}

test("one picker finds Swiss Medical in the same list as IOMA and retains its prepaid payload", async t => {
  const { page, posts } = await fixture(t);
  assert.equal(await page.locator('label[for="sc-os-0"]').textContent(), "Obra social o prepaga");
  assert.equal(await page.locator("#sc-prepaga").count(), 0);
  await pick(page, "swiss", "Swiss Medical");
  await saved(page, posts);
  assert.deepEqual(posts.at(-1).body.cobertura, { obras_sociales: [], prepaga: { nombre: "Swiss Medical" } });
});

// Probador en sitio, 2026-10-21: the list had no "Ninguna". «No tengo cobertura» is a
// choice of the same picker and travels as declarada_sin_cobertura, never as
// a payer name; a saved declaration comes back selected.
test("«No tengo cobertura» is the first choice, saves as a declaration and reloads selected", async t => {
  const { page, posts } = await fixture(t);
  await page.locator("#sc-os-0").focus();
  const first = page.locator("#sc-os-0-list [role=option]").first();
  assert.equal(await first.textContent(), "No tengo cobertura");
  await first.click();
  const cob = await saved(page, posts);
  assert.deepEqual(cob, { obras_sociales: [], declarada_sin_cobertura: true });
  assert.equal(await page.locator("#sc-os-0").inputValue(), "No tengo cobertura");
  assert.equal(await page.locator("#sc-os-af-row-0").isVisible(), false);
  assert.equal(await page.locator("#sc-os-add-row").isVisible(), false);
});

test("a saved «No tengo cobertura» reloads selected; a payer replaces it", async t => {
  const { page, posts } = await fixture(t, { coverage: { declarada_sin_cobertura: true } });
  assert.equal(await page.locator("#sc-os-0").inputValue(), "No tengo cobertura");
  await pick(page, "swiss", "Swiss Medical");
  const cob = await saved(page, posts);
  assert.deepEqual(cob, { obras_sociales: [], prepaga: { nombre: "Swiss Medical" } });
});

test("typing a query never offers remove or stores a coverage; confirmed selection does", async t => {
  const { page, posts } = await fixture(t);
  const remove = page.locator(".sc-os-del");
  assert.equal(await remove.isVisible(), false);
  await page.locator("#sc-os-0").fill("swi");
  assert.equal(await remove.isVisible(), false);
  assert.equal(posts.length, 0);
  await pick(page, "swiss", "Swiss Medical");
  assert.equal(await remove.isVisible(), true);
  assert.equal(await page.locator("#sc-os-0").evaluate(e => e === document.activeElement), true);
});

for (const query of ["swi", "SWISS MEDICAL", "Swíss Médical", "swissmedical", "Swiss-Medical"]) {
  test(`Swiss search ${query} has one canonical result; keyboard selection retains focus and ARIA state`, async t => {
    const { page, posts } = await fixture(t);
    const input = page.locator("#sc-os-0");
    await input.fill(query);
    assert.equal(await page.getByRole("option", { name: "Swiss Medical", exact: true }).count(), 1);
    assert.equal(await input.getAttribute("aria-expanded"), "true");
    await input.press("ArrowDown");
    const active = await input.getAttribute("aria-activedescendant");
    assert.equal(await page.locator(`#${active}`).textContent(), "Swiss Medical");
    await input.press("Enter");
    await saved(page, posts);
    assert.equal(await input.inputValue(), "Swiss Medical");
    assert.equal(await input.getAttribute("aria-expanded"), "false");
    assert.equal(await input.getAttribute("aria-activedescendant"), null);
    assert.equal(await input.evaluate(e => e === document.activeElement), true);
    assert.deepEqual(posts.at(-1).body.cobertura, { obras_sociales: [], prepaga: { nombre: "Swiss Medical" } });
  });
}

test("multi-coverage keeps registry RNOS, saved category, member and prepaid plan across save/reload", async t => {
  const original = {
    obras_sociales: [
      { nombre: "O.S.P. BUENOS AIRES (I.O.M.A.)", rnos: "000001", origen: "puco", afiliado: "000-123" },
      { nombre: "Swiss Medical", origen: "declarada", afiliado: "legacy-os" }
    ], prepaga: { nombre: "Swiss Medical", afiliado: "000-456", plan: "SMG20" }
  };
  const { page, posts } = await fixture(t, { coverage: original });
  assert.equal(await page.locator(".sc-os-item").count(), 3);
  assert.equal(await page.locator("#sc-os-plan-1").isVisible(), false);
  assert.equal(await page.locator("#sc-os-plan-2").inputValue(), "SMG20");
  const body = await saveMember(page, posts, "000-123");
  assert.deepEqual(body, { obras_sociales: original.obras_sociales.map(({ origen, ...o }) => o), prepaga: original.prepaga });
  await page.reload();
  assert.equal(await page.locator("#sc-os-0").inputValue(), original.obras_sociales[0].nombre);
  assert.equal(await page.locator("#sc-os-af-0").inputValue(), "000-123");
  assert.equal(await page.locator("#sc-os-af-2").inputValue(), "000-456");
  assert.equal(await page.locator("#sc-os-plan-2").inputValue(), "SMG20");
});

test("legacy singular/string coverage loads without recategorizing or requiring re-selection", async t => {
  const { page, posts } = await fixture(t, { coverage: { obra_social: "OSDE", prepaga: "Swiss Medical" } });
  assert.equal(await page.locator("#sc-os-0").inputValue(), "OSDE");
  assert.equal(await page.locator("#sc-os-1").inputValue(), "Swiss Medical");
  assert.equal(await page.locator(".sc-os-del:visible").count(), 2);
  assert.deepEqual(await saveMember(page, posts, "0007"), {
    obras_sociales: [{ nombre: "OSDE", afiliado: "0007" }], prepaga: { nombre: "Swiss Medical" }
  });
});

test("unlisted choice explicitly chooses its type; free query does not invent a payer ID", async t => {
  const { page, posts } = await fixture(t);
  await pick(page, "  Mutual Sintética  ", "Agregar «Mutual Sintética» como obra social");
  await saved(page, posts);
  assert.deepEqual(posts.at(-1).body.cobertura, { obras_sociales: [{ nombre: "Mutual Sintética" }] });
  await page.locator("#sc-os-add").click();
  await pick(page, "Plan Demo", "Agregar «Plan Demo» como prepaga", 1);
  await page.locator("#sc-os-plan-1").fill("P-001");
  const body = await saveMember(page, posts, "00042", 1);
  assert.deepEqual(body, {
    obras_sociales: [{ nombre: "Mutual Sintética" }],
    prepaga: { nombre: "Plan Demo", afiliado: "00042", plan: "P-001" }
  });
});

test("query cancellation and editing cannot delete an existing selection or reuse stale credential fields", async t => {
  const { page, posts } = await fixture(t, { coverage: { obras_sociales: [{ nombre: "IOMA", rnos: "000001", origen: "puco", afiliado: "0007" }] } });
  const input = page.locator("#sc-os-0");
  await input.fill("new query");
  assert.equal(await page.locator(".sc-os-del").isVisible(), false);
  await page.locator("#outside").click();
  assert.equal(await input.inputValue(), "IOMA");
  assert.equal(posts.length, 0);
  await input.fill("swiss");
  await input.press("Escape");
  assert.equal(await input.inputValue(), "IOMA");
  await pick(page, "swiss", "Swiss Medical");
  await saved(page, posts);
  assert.equal(await page.locator("#sc-os-af-0").inputValue(), "");
  assert.equal(await page.locator(".sc-echo.ok").count(), 0);
  assert.deepEqual(posts.at(-1).body.cobertura, { obras_sociales: [], prepaga: { nombre: "Swiss Medical" } });
});

test("clear is an accessible right-side button, restores adjacent focus, persists removal and never reappears on query", async t => {
  const { page, posts } = await fixture(t, { coverage: { obras_sociales: [{ nombre: "IOMA" }], prepaga: "Swiss Medical" } });
  const remove = page.getByRole("button", { name: "Quitar Swiss Medical", exact: true });
  const inputBox = await stableBox(page.locator("#sc-os-1"), "second coverage input"), clearBox = await stableBox(remove, "clear button");
  assert.ok(clearBox.x >= inputBox.x + inputBox.width);
  assert.ok(clearBox.width >= 44 && clearBox.height >= 44);
  await remove.focus();
  await page.keyboard.press("Enter");
  await saved(page, posts);
  assert.equal(await page.locator("#sc-os-0").evaluate(e => e === document.activeElement), true);
  assert.deepEqual(posts.at(-1).body.cobertura, { obras_sociales: [{ nombre: "IOMA" }] });
  await page.getByRole("button", { name: "Quitar IOMA", exact: true }).click();
  assert.equal(await page.locator("#sc-os-0").inputValue(), "");
  assert.equal(await page.locator(".sc-os-del").isVisible(), false);
  await page.locator("#sc-os-0").fill("sw");
  assert.equal(await page.locator(".sc-os-del").isVisible(), false);
  await page.locator("#outside").focus();
  assert.equal(await page.locator("#outside").evaluate(e => e === document.activeElement), true);
  await page.reload();
  assert.equal(await page.locator("#sc-os-0").inputValue(), "");
});

test("catalog accents and punctuated aliases resolve to their existing canonical names", async t => {
  const { page, posts } = await fixture(t);
  await pick(page, "I.O.M.A.", "IOMA");
  await page.locator("#sc-os-add").click();
  await pick(page, "medife", "Medifé", 1);
  await saved(page, posts);
  assert.deepEqual(posts.at(-1).body.cobertura, { obras_sociales: [{ nombre: "IOMA" }], prepaga: { nombre: "Medifé" } });
});

test("registry response preserves a selected prepaga and adds the separate canonical registry row", async t => {
  const { page, posts } = await fixture(t, { mode: "register", puco: { disponible: true, obras_sociales: [{ nombre: "O.S.P. BUENOS AIRES (I.O.M.A.)", rnos: "000001" }] } });
  await pick(page, "swiss", "Swiss Medical");
  await page.locator("#sc-os-af-0").fill("00042");
  await page.locator("#sc-os-plan-0").fill("SMG20");
  await page.locator("#sc-dni").fill("05123456");
  await page.waitForFunction(() => document.querySelectorAll(".sc-os-item").length === 2);
  assert.equal(await page.locator("#sc-os-1").inputValue(), "Swiss Medical");
  assert.equal(await page.locator("#sc-os-af-1").inputValue(), "00042");
  await page.locator("#sc-nombre").fill("Demo");
  await page.locator("#sc-apellido").fill("Prueba");
  await page.locator("#sc-email").fill("demo@example.invalid");
  await page.locator("#sc-pass").fill("Synthetic test only");
  await page.locator("#sc-submit").click();
  await page.waitForFunction(() => window.done.length);
  assert.deepEqual(posts.find(p => p.path.endsWith("/register")).body.cobertura, {
    obras_sociales: [{ nombre: "O.S.P. BUENOS AIRES (I.O.M.A.)", rnos: "000001" }], prepaga: { nombre: "Swiss Medical", afiliado: "00042", plan: "SMG20" }
  });
});

test("a late registry response preserves an unfinished search/focus; removed registry coverage stays removed after mode swap", async t => {
  let release;
  const pucoWait = new Promise(resolve => { release = resolve; });
  const { page } = await fixture(t, { mode: "register", pucoWait, puco: { disponible: true, obras_sociales: [{ nombre: "IOMA", rnos: "000001" }] } });
  try {
    await page.locator("#sc-dni").fill("05123456");
    const request = page.waitForRequest("**/api/puco");
    await page.locator("#sc-nombre").focus();
    await request;
    await page.locator("#sc-os-0").fill("swis");
    release();
    await page.waitForFunction(() => document.querySelectorAll(".sc-os-item").length === 2);
    assert.equal(await page.locator("#sc-os-1").inputValue(), "swis");
    assert.equal(await page.locator("#sc-os-1").evaluate(e => e === document.activeElement), true);
    await page.locator("#sc-os-1").press("ArrowDown");
    await page.locator("#sc-os-1").press("Enter");
    await page.getByRole("button", { name: "Quitar IOMA", exact: true }).click();
    await page.locator("#sc-swap").click();
    await page.locator("#sc-swap").click();
    assert.equal(await page.locator(".sc-os-item").count(), 1);
    assert.equal(await page.locator("#sc-os-0").inputValue(), "Swiss Medical");
  } finally { release(); }
});

test("existing backend limits are explicit and do not drop a seventh insurer into a saved payload", async t => {
  const obras_sociales = ["IOMA", "PAMI", "OSECAC (Empleados de Comercio)", "Banco Provincia", "OSUTHGRA (Gastronómicos)", "OSPE (Petroleros)"].map(nombre => ({ nombre }));
  const { page, posts } = await fixture(t, { coverage: { obras_sociales } });
  await page.locator("#sc-os-add").click();
  await page.locator("#sc-os-6").fill("OSPRERA");
  assert.equal(await page.getByRole("option", { name: "OSPRERA (Trabajadores Rurales) — hasta 6 obras sociales" }).isDisabled(), true);
  await pick(page, "swiss", "Swiss Medical", 6);
  await saved(page, posts);
  assert.equal(await page.locator("#sc-os-add").isVisible(), false);
  assert.deepEqual(posts.at(-1).body.cobertura, { obras_sociales, prepaga: { nombre: "Swiss Medical" } });
});

test("adding another row preserves the first and cannot silently replace an existing prepaga", async t => {
  const { page, posts } = await fixture(t, { coverage: { prepaga: { nombre: "Swiss Medical", afiliado: "00042", plan: "SMG20" } } });
  await page.locator("#sc-os-add").click();
  await page.locator("#sc-os-1").fill("osde");
  assert.equal(await page.getByRole("option", { name: "OSDE — ya tenés una prepaga" }).isDisabled(), true);
  await pick(page, "ioma", "IOMA", 1);
  await saved(page, posts);
  assert.deepEqual(posts.at(-1).body.cobertura, {
    obras_sociales: [{ nombre: "IOMA" }], prepaga: { nombre: "Swiss Medical", afiliado: "00042", plan: "SMG20" }
  });
});

test("register payload and interrupted navigation keep selected coverage and member details", async t => {
  const { page, posts } = await fixture(t, { mode: "register" });
  await pick(page, "swiss", "Swiss Medical");
  await page.locator("#sc-os-af-0").fill("00042");
  await page.locator("#sc-os-plan-0").fill("SMG20");
  await page.locator("#sc-swap").click();
  await page.locator("#sc-swap").click();
  assert.equal(await page.locator("#sc-os-0").inputValue(), "Swiss Medical");
  assert.equal(await page.locator("#sc-os-af-0").inputValue(), "00042");
  await page.locator("#sc-dni").fill("05123456");
  await page.locator("#sc-nombre").fill("Demo");
  await page.locator("#sc-apellido").fill("Prueba");
  await page.locator("#sc-email").fill("demo@example.invalid");
  await page.locator("#sc-pass").fill("Synthetic test only");
  await page.locator("#sc-submit").click();
  await page.waitForFunction(() => window.done.length);
  assert.deepEqual(posts.find(p => p.path.endsWith("/register")).body.cobertura, {
    obras_sociales: [], prepaga: { nombre: "Swiss Medical", afiliado: "00042", plan: "SMG20" }
  });
});

for (const width of [320, 390]) {
  test(`${width}px: long saved names, dropdown and clear fit without overlap or horizontal scroll`, async t => {
    const { page } = await fixture(t, { width, coverage: { obra_social: { nombre: "Obra Social Sintética de Trabajadores de Servicios Municipales", afiliado: "00000000001234567890" } } });
    const input = page.locator("#sc-os-0"), remove = page.locator(".sc-os-del");
    const i = await stableBox(input, `saved-name input at ${width}`), r = await stableBox(remove, `clear button at ${width}`);
    assert.ok(i.x + i.width <= r.x);
    assert.ok(r.x + r.width <= width);
    await input.fill("trab");
    await page.getByRole("option", { name: "OSPRERA (Trabajadores Rurales)", exact: true }).waitFor();
    const list = await stableBox(page.locator("#sc-os-0-list"), `dropdown at ${width}`);
    assert.ok(list.x >= 0 && list.x + list.width <= width);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.TEST_COVERAGE_SCREENSHOT_DIR) {
      mkdirSync(process.env.TEST_COVERAGE_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: join(process.env.TEST_COVERAGE_SCREENSHOT_DIR, `coverage-search-${width}.png`), fullPage: true });
      await input.press("Escape");
      await page.screenshot({ path: join(process.env.TEST_COVERAGE_SCREENSHOT_DIR, `coverage-selected-${width}.png`), fullPage: true });
    }
  });
}
