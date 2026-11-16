import { before, after } from "node:test";
import { test, ceil, hookOptions, within } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

// The launch is infrastructure, not the behavior under test: inside the
// test's own 20 s it raced a parallel suite's Chromiums and timed out on a
// loaded machine (23/10) while the save itself takes ~1 s.
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); }, hookOptions());
after(async () => { if (browser) await browser.close(); });

// Every wait below is bounded by the tier's ceiling (helpers/ceiling.mjs) and
// fails with what it waited for; the test's own limit only guards their sum.
test("real Account email save finishes while optional Users import stays pending", async () => {
  let release;
  let observed;
  const held = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { observed = resolve; });
  let moduleCompleted = false;
  const posts = [];
  const errors = [];
  const files = {
    "/static/platform/admin-account.mjs": "static/platform/admin-account.mjs",
    "/static/platform/admin-session.mjs": "static/platform/admin-session.mjs",
    "/static/platform/passkeys.mjs": "static/platform/passkeys.mjs",
    "/static/platform/i18n.mjs": "static/platform/i18n.mjs",
  };
  const manifest = Object.fromEntries(Object.keys(files).filter(p => p.endsWith("mjs"))
    .map(p => [p.replace(/^\/(static\/)?/, ""), p]));
  manifest["platform/admin-users.mjs"] = "/static/platform/admin-users.held.mjs";
  const page = await browser.newPage();
  try {
    ceil(page);
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === manifest["platform/admin-users.mjs"]) {
        observed();
        await held;
        moduleCompleted = true;
        return route.abort();
      }
      if (files[path]) return route.fulfill({ contentType: "text/javascript", body:
        readFileSync(new URL("../" + files[path], import.meta.url), "utf8") });
      if (path === "/admin/account") return route.fulfill({ contentType: "text/html", body:
        `<!doctype html><html lang="es"><head><script>
        window.SANA_STATIC=${JSON.stringify(manifest)};
        window.SANA_I18N={lang:"es",catalog:{}};
        window.SANA_ADMIN_SCREEN="cuenta";
        window.sanaImportStatic=n=>import(window.SANA_STATIC[n]);
        </script></head><body><nav id="adm-nav"></nav><h1 id="adm-h1"></h1>
        <section id="sec-cuenta"></section><script type="module">import {mount} from '/static/platform/admin-account.mjs'; window.accountDispose=mount(document.getElementById('sec-cuenta'));</script></body></html>` });
      if (path === "/api/admin/account") return route.fulfill({ json: {
        usuario: "synthetic.operator", rol: "admin", tiene_2fa: true, email: "old@example.invalid",
      } });
      if (path === "/api/admin/passkeys") return route.fulfill({ json: { passkeys: [] } });
      if (path === "/api/admin/account/profile") {
        assert.equal(route.request().method(), "POST");
        posts.push(route.request().postDataJSON());
        return route.fulfill({ json: { ok: true } });
      }
      return route.fulfill({ status: 404, body: "Outside synthetic test scope" });
    });
    await page.goto("https://account-refresh.test/admin/account");
    // Cuenta is a view: the email form opens in place from its «Editar».
    await page.locator("#acct-email-edit").click();
    await page.locator("#acct-email").fill("new@example.invalid");
    const button = page.locator('#acct-email-form button[type="submit"]');
    await button.click();
    // Without a bound this waited forever whenever the save never reached
    // its success branch, and the failure read as a bare test timeout.
    await within(requested, "request of the optional Users module after the saved email");
    await page.waitForFunction(() => {
      const button = document.querySelector('#acct-email-form button[type="submit"]');
      return button && !button.disabled && document.querySelector("#acct-email-status").textContent === "Email guardado.";
    });
    assert.deepEqual(posts, [{ email: "new@example.invalid" }]);
    assert.equal(moduleCompleted, false, "optional module must still be pending when Save recovers");
    assert.deepEqual(errors, []);
  } finally {
    release();
    await page.close();
  }
});
