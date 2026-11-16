import { before, after } from "node:test";
import { test, ceil, hookOptions } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

// Exercise the actual shared component. Every request is fulfilled locally;
// this suite neither starts the application nor connects to a database/provider.
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
after(async () => { await browser?.close(); });

async function fixture(t, { lang = "es", mode = "login", prefill = {}, config = {}, registerStatus = 409, registerWait = null } = {}) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  ceil(page);
  t.after(() => page.close());
  const posts = [];
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  const catalog = JSON.parse(readFileSync(new URL(`../locales/${lang}/account.form.json`, import.meta.url), "utf8"));
  const user = { dni: "05123456", nombre: "Demo", email: "demo8+tag@example.invalid" };
  await page.route("**/*", async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (req.method() === "POST") posts.push({ path, body: req.postDataJSON() });
    if (["/static/platform/account-form.mjs", "/static/platform/i18n.mjs"].includes(path)) {
      return route.fulfill({ contentType: "text/javascript", body: readFileSync(new URL(`..${path}`, import.meta.url), "utf8") });
    }
    if (path === "/account") return route.fulfill({ contentType: "text/html", body: `<!doctype html>
      <html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
      <style>body{font-family:Arial,sans-serif;margin:24px;--text:#1f1f1f;--text-2:#4a514c;--line:#ccd2cd;--accent:#176b43}</style>
      <script>window.SANA_I18N=${JSON.stringify({ active: lang, catalog })};
      window.SANA_STATIC={"platform/i18n.mjs":"/static/platform/i18n.mjs"};window.done=[];</script></head>
      <body><main id="host"></main><script type="module">
      import {renderAuth} from '/static/platform/account-form.mjs';
      renderAuth(document.getElementById('host'),{defaultMode:${JSON.stringify(mode)},prefill:${JSON.stringify(prefill)},onDone:u=>window.done.push(u)});
      </script></body></html>` });
    if (path === "/api/account/config") return route.fulfill({ json: config });
    if (path === "/api/ai-status") return route.fulfill({ json: { disponible: false } });
    if (path === "/api/puco") return route.fulfill({ json: { disponible: false } });
    if (path === "/api/account/register") {
      if (registerWait) await registerWait;
      return route.fulfill({ status: registerStatus,
        json: registerStatus === 200 ? { usuario: user } : { error: lang === "es" ? "dni_registrado" : "email_registrado" } });
    }
    if (path === "/api/account/otp/send") return route.fulfill({ json: { ok: true } });
    if (path === "/api/account/otp/verify") return route.fulfill({ json: { ok: true } });
    if (path === "/api/account/recover") return route.fulfill({ json: { ok: true } });
    return route.fulfill({ status: 404, body: "Outside synthetic test scope" });
  });
  await page.goto("https://registration-recovery.test/account");
  await page.locator("#sc-form").waitFor();
  t.after(() => assert.deepEqual(errors, []));
  return { page, posts, user };
}

async function submit(page) {
  await page.locator("#sc-submit").click();
  await page.waitForFunction(() => document.querySelector("#sc-msg")?.classList.contains("show"));
}

async function enroll(page, lang = "es", phone = false) {
  if (lang === "es") await page.locator("#sc-dni").fill("05123456");
  await page.locator("#sc-nombre").fill("Demo");
  await page.locator("#sc-apellido").fill("Test");
  await page.locator("#sc-email").fill("demo8+tag@example.invalid");
  if (phone) await page.locator("#sc-contacto").fill("+5492254000000");
  await page.locator("#sc-pass").fill("Synthetic +&'<> secret 8");
  await page.locator("#sc-submit").click();
}

test("email with an 8 and plus tag survives login/recovery/edit/login without carrying a password", async t => {
  const { page, posts } = await fixture(t);
  await page.locator("#sc-dni").fill("  demo8+tag@example.invalid  ");
  await page.locator("#sc-pass").fill("Synthetic discarded password");
  await page.locator("#sc-forgot").click();
  assert.equal(await page.locator("#sc-dni").inputValue(), "demo8+tag@example.invalid");
  assert.equal(await page.locator('input[type="password"]').count(), 0);
  await page.locator("#sc-dni").press("Enter");
  await page.waitForFunction(() => document.querySelector("#sc-msg")?.textContent.includes("Si ese email"));
  assert.deepEqual(posts.filter(p => p.path.endsWith("/recover")), [{ path: "/api/account/recover", body: { email: "demo8+tag@example.invalid" } }]);
  assert.equal(await page.locator("#sc-submit").isEnabled(), true);
  await page.locator("#sc-dni").fill("edited8+new@example.invalid");
  await page.locator("#sc-swap").focus();
  await page.keyboard.press("Enter");
  await page.locator("#sc-pass").waitFor();
  assert.equal(await page.locator("#sc-pass").inputValue(), "");
  assert.equal(await page.locator("#sc-dni").inputValue(), "edited8+new@example.invalid");
  await page.locator("#sc-forgot").focus();
  await page.keyboard.press("Enter");
  assert.equal(await page.locator("#sc-dni").inputValue(), "edited8+new@example.invalid");
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  assert.equal(new URL(page.url()).search, "");
  assert.equal(new URL(page.url()).hash, "");
});

for (const lang of ["es", "en", "pt", "fr", "it", "de", "ru", "uk", "ja", "zh"]) {
  test(`${lang}: direct recovery has one username field and accepts email or a leading-zero DNI`, async t => {
    const { page, posts } = await fixture(t, { lang, mode: "recuperar" });
    assert.equal(await page.locator("#sc-form input").count(), 1);
    const input = page.locator("#sc-dni");
    assert.equal(await input.getAttribute("autocomplete"), "username");
    assert.equal(await input.getAttribute("inputmode"), "text");
    assert.equal(await input.getAttribute("autocapitalize"), "off");
    assert.equal(await input.inputValue(), "");
    await input.fill("05.123.456");
    await input.press("Enter");
    await page.waitForFunction(() => !document.querySelector("#sc-submit").disabled && document.querySelector("#sc-msg").classList.contains("show"));
    assert.deepEqual(posts.at(-1), { path: "/api/account/recover", body: { dni: "05123456" } });
    assert.equal(await input.inputValue(), "05.123.456");
    await input.fill("demo8+tag@example.invalid");
    await input.press("Enter");
    await page.waitForFunction(() => !document.querySelector("#sc-submit").disabled);
    assert.deepEqual(posts.at(-1), { path: "/api/account/recover", body: { email: "demo8+tag@example.invalid" } });
    await page.locator("#sc-swap").click();
    assert.equal(await page.locator(lang === "es" ? "#sc-dni" : "#sc-email").inputValue(), "demo8+tag@example.invalid");
    await page.locator("#sc-forgot").click();
    assert.equal(await input.inputValue(), "demo8+tag@example.invalid");
  });
}

test("cleared identifier remains empty across transitions; blank recovery never sends", async t => {
  const { page, posts } = await fixture(t, { prefill: { email: "demo8+old@example.invalid" } });
  await page.locator("#sc-dni").fill("");
  await page.locator("#sc-forgot").click();
  assert.equal(await page.locator("#sc-dni").inputValue(), "");
  await submit(page);
  assert.equal(posts.length, 0);
  assert.equal(await page.locator("#sc-submit").isEnabled(), true);
  assert.equal(await page.locator("#sc-msg").getAttribute("class"), "sc-msg show err");
  await page.locator("#sc-swap").click();
  assert.equal(await page.locator("#sc-dni").inputValue(), "");
});

test("repeated enrollment submit sends once and the refusal keeps the submitted identifier", async t => {
  let release;
  const registerWait = new Promise(resolve => { release = resolve; });
  const { page, posts } = await fixture(t, { mode: "registro", registerWait });
  try {
    const requested = page.waitForRequest("**/api/account/register");
    await enroll(page);
    await requested;
    await page.locator("#sc-dni").fill("06123456");
    await page.locator("#sc-form").evaluate(form => form.requestSubmit());
    release();
    await page.locator("#sc-msg #sc-conflict-forgot").waitFor();
    assert.equal(posts.filter(p => p.path.endsWith("/register")).length, 1);
    assert.equal(await page.locator("#sc-dni").inputValue(), "05.123.456");
    assert.equal(await page.locator("#sc-pass").inputValue(), "");
    assert.deepEqual(await page.evaluate(() => window.done), []);
  } finally { release(); }
});

for (const lang of ["es", "en"]) {
  test(`${lang}: duplicate enrollment is a refusal with login/recovery remedy, empty password and no completion`, async t => {
    const { page, posts } = await fixture(t, { lang, mode: "registro" });
    await enroll(page, lang);
    await page.locator("#sc-msg #sc-conflict-forgot").waitFor();
    assert.equal(await page.locator("#sc-msg").getAttribute("class"), "sc-msg show err");
    assert.match(await page.locator("#sc-msg").textContent(), lang === "es" ? /no se guardó/i : /wasn.t saved/i);
    assert.equal(await page.locator("#sc-pass").inputValue(), "");
    assert.deepEqual(await page.evaluate(() => window.done), []);
    assert.equal(await page.evaluate(() => localStorage.length), 0);
    assert.equal(posts.filter(p => p.path.endsWith("/register")).length, 1);
    assert.equal(posts.filter(p => p.path.endsWith("/login")).length, 0);
    if (lang === "es") assert.equal(await page.locator("#sc-dni").inputValue(), "05.123.456");
    else assert.equal(await page.locator("#sc-email").inputValue(), "demo8+tag@example.invalid");
  });
}

test("phone-verified duplicate uses the same refusal and drops the enrollment password", async t => {
  const { page } = await fixture(t, { mode: "registro", config: { otp_registro_requerido: true } });
  await enroll(page, "es", true);
  await page.locator("#sc-otp-code").fill("123456");
  await page.locator("#sc-otp-submit").click();
  await page.locator("#sc-msg #sc-conflict-forgot").waitFor();
  assert.equal(await page.locator("#sc-msg").getAttribute("class"), "sc-msg show err");
  assert.match(await page.locator("#sc-msg").textContent(), /no se guardó/i);
  assert.equal(await page.locator("#sc-pass").inputValue(), "");
  assert.deepEqual(await page.evaluate(() => window.done), []);
});

test("successful enrollment still completes once using the existing server response", async t => {
  const { page, posts, user } = await fixture(t, { lang: "en", mode: "registro", registerStatus: 200 });
  await enroll(page, "en");
  await page.waitForFunction(() => window.done.length === 1);
  assert.deepEqual(await page.evaluate(() => window.done), [user]);
  assert.equal(posts[0].body.email, "demo8+tag@example.invalid");
  assert.equal(posts[0].body.password, "Synthetic +&'<> secret 8");
  assert.equal(posts.filter(p => p.path.endsWith("/login")).length, 0);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("st_identity")).password), undefined);
});

test("recovery preserves a prefilled identifier verbatim and fits phone/desktop widths", async t => {
  const { page } = await fixture(t, { mode: "recuperar", prefill: { dni: "  demo8+tag@example.invalid  " } });
  assert.equal(await page.locator("#sc-dni").inputValue(), "demo8+tag@example.invalid");
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.TEST_AUTH_SCREENSHOT_DIR) {
      mkdirSync(process.env.TEST_AUTH_SCREENSHOT_DIR, { recursive: true });
      await page.locator("#sc-dni").fill("");
      await page.screenshot({ path: join(process.env.TEST_AUTH_SCREENSHOT_DIR, `recovery-${width}.png`), fullPage: true });
    }
  }
});

// TASK-50 (26.10, persona Norma, 74, 390 px): «Crear cuenta» with a DNI that
// already has an account switched to the login screen and erased everything
// she had typed. The sign-up stays on screen as typed (password aside), and
// the refusal itself carries both ways in: sign in, or recover the password.
test("duplicate enrollment keeps what was typed and offers sign-in and recovery in place", async t => {
  const { page } = await fixture(t, { mode: "registro" });
  await enroll(page, "es", true);
  await page.locator("#sc-msg a").first().waitFor();
  assert.equal(await page.locator("#sc-nombre").inputValue(), "Demo");
  assert.equal(await page.locator("#sc-apellido").inputValue(), "Test");
  assert.equal(await page.locator("#sc-email").inputValue(), "demo8+tag@example.invalid");
  assert.equal(await page.locator("#sc-contacto").inputValue(), "+5492254000000");
  assert.equal(await page.locator("#sc-dni").inputValue(), "05.123.456");
  assert.equal(await page.locator("#sc-pass").inputValue(), "");
  assert.match(await page.locator("#sc-msg").textContent(), /ya tenés una cuenta/i);
  await page.locator("#sc-msg #sc-conflict-login").click();
  await page.locator("#sc-forgot").waitFor();
  assert.equal(await page.locator("#sc-dni").inputValue(), "05.123.456");
  await page.locator("#sc-swap").click();
  assert.equal(await page.locator("#sc-nombre").inputValue(), "Demo", "back to sign-up, still filled");
  assert.equal(await page.locator("#sc-contacto").inputValue(), "+5492254000000");
});

// TASK-51: «2254» sat in the phone field as a VALUE, so typing the full
// number after it gave 22542254000000. The area code is the placeholder's
// example only; the field starts empty.
test("the sign-up phone field starts empty; the area code is only the example", async t => {
  const { page } = await fixture(t, { mode: "registro" });
  const tel = page.locator("#sc-contacto");
  assert.equal(await tel.inputValue(), "");
  assert.match(await tel.getAttribute("placeholder"), /^2254 /);
});

test("recovery says what to do without an email: the admission desk adds one", async t => {
  const centro = { nombre: "Hospital Sampleton", url: "/center/sampleton-hospital",
    direccion: "Calle 1 100", telefono: "+54 11 4000-0001", telefono_href: "tel:+541140000001" };
  const { page } = await fixture(t, { mode: "recuperar", config: { centro_principal: centro } });
  const help = page.locator("#sc-sin-email");
  assert.match(await help.textContent(), /email/i);
  assert.equal(await help.locator('a[href="/center/sampleton-hospital"]').textContent(), "Hospital Sampleton");
  assert.equal(await help.locator('a[href="tel:+541140000001"]').count(), 1);
});
