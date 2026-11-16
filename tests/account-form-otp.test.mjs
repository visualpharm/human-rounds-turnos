// Contract tests for the dormant registration OTP client slice. These stay
// DOM-free on purpose: Account has no framework runtime, and this test
// protects the actual browser-facing endpoint/order/accessibility
// contract without inventing a second fake browser implementation or
// extracting nested function bodies to eval.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as I18n from "../static/platform/i18n.mjs";
import * as Account from "../static/platform/account-form.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(ROOT, "static/platform/account-form.mjs"), "utf8");
const catalog = (lang) => JSON.parse(readFileSync(join(ROOT, "locales", lang, "account.form.json"), "utf8"));

test("the native module is the OTP owner and does not export a nested renderer", () => {
  assert.equal(typeof Account.renderAuth, "function");
  assert.equal(typeof Account.otpHtml, "undefined");
  assert.match(src, /function otpHtml\(/);
  assert.match(src, /function startRegistrationOtp\(/);
  assert.match(src, /function wireOtp\(/);
  assert.match(src, /function completeVerifiedRegistration\(/);
});

test("config-gated phone registrations send, verify, then register; legacy and email-only posts stay direct", () => {
  assert.match(src, /state\.otpRequired = !!\(cfg && cfg\.otp_registro_requerido\)/);
  assert.match(src, /state\.mode === "registro" && state\.otpRequired && payload\.contacto/);
  assert.match(src, /fetch\("\/api\/account\/otp\/send"/);
  assert.match(src, /proposito: "registro"/);
  assert.match(src, /state\.mode = "otp"/);
  assert.match(src, /fetch\("\/api\/account\/otp\/verify"/);
  assert.match(src, /completeVerifiedRegistration\(code, submit, say\)/);
  assert.match(src, /fetch\("\/api\/account\/register"/);
  assert.match(src, /state\.otp = \{ payload: null, cooldownUntil: 0, timer: null, registering: false \}/);
});

test("OTP entry is a single accessible, mobile-safe six-digit field with focus and resend cooldown", () => {
  assert.match(src, /id="sc-otp-code"/);
  assert.match(src, /inputmode="numeric"/);
  assert.match(src, /autocomplete="one-time-code"/);
  assert.match(src, /maxlength="6"/);
  assert.match(src, /aria-label="/);
  assert.match(src, /aria-describedby="sc-otp-msg"/);
  assert.match(src, /role="status" aria-live="polite"/);
  assert.match(src, /code\.focus\(\)/);
  assert.match(src, /Date\.now\(\) \+ 45000/);
  assert.match(src, /\.sc-otp-code\{width:10ch;max-width:100%/);
  assert.match(src, /@media\(max-width:480px\).*?\.sc-otp-code\{font-size:22px/s);
  assert.doesNotMatch(src, /text-decoration:underline|text-underline-offset/);
});

test("OTP screen copy is ES, EN, and Spanish no-catalog fallbacks without exposing the raw phone", () => {
  const es = catalog("es");
  const en = catalog("en");
  assert.match(src, /T\("account\.form\.otp\.heading", null, "Confirmá tu teléfono"\)/);
  assert.equal(es["account.form.otp.heading"], "Confirmá tu teléfono");
  assert.match(en["account.form.otp.heading"], /Confirm your phone/i);
  assert.match(es["account.form.otp.sent_to"], /por WhatsApp/);
  assert.match(en["account.form.otp.sent_to"], /by WhatsApp/);
  const otpHtml = /function otpHtml\(\) \{[\s\S]*?\n    \}/.exec(src);
  assert.ok(otpHtml, "otpHtml() must exist");
  assert.doesNotMatch(otpHtml[0], /payload\.contacto|contact\s*=/);
});

test("OTP catalog strings are complete in English and Spanish", () => {
  const en = catalog("en");
  const es = catalog("es");
  const keys = [...new Set([...Object.keys(en), ...Object.keys(es)].filter((key) => key.startsWith("account.form.otp.")))];
  assert.ok(keys.length >= 16, "the OTP flow needs its full visible/error copy in both catalogs");
  for (const key of keys) {
    assert.equal(typeof en[key], "string", `English missing ${key}`);
    assert.equal(typeof es[key], "string", `Spanish missing ${key}`);
    assert.ok(en[key].trim(), `English empty ${key}`);
    assert.ok(es[key].trim(), `Spanish empty ${key}`);
  }
});

test("every OTP no-catalog message fallback matches the Spanish catalog", () => {
  const es = catalog("es");
  const calls = [...src.matchAll(/otpMessage\("([^"]+)", "([^"]*)"\)/g)];
  assert.ok(calls.length >= 12, "expected the interactive OTP status/error messages");
  for (const [, key, fallback] of calls) {
    assert.equal(fallback, es[key], `${key} must fall back to Spanish without a catalog`);
  }
});

test("first paint of renderAuth is registro, not OTP, even with otp config", async () => {
  I18n.setCatalog({});
  const previousFetch = global.fetch;
  const previousWindow = global.window;
  const previousDocument = global.document;
  global.fetch = () => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ otp_registro_requerido: true }),
  });
  global.window = { document: { getElementById: () => null, createElement: () => ({}), head: { appendChild() {} }, documentElement: { lang: "es" } } };
  global.document = global.window.document;
  try {
    const host = {
      _innerHTML: "",
      get innerHTML() { return this._innerHTML; },
      set innerHTML(v) { this._innerHTML = v; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      addEventListener() {},
    };
    await Account.renderAuth(host, { defaultMode: "registro" });
    await new Promise((r) => setImmediate(r));
    assert.match(host.innerHTML, /Crear cuenta|Create account/);
    assert.doesNotMatch(host.innerHTML, /sc-otp-code/);
  } finally {
    I18n.setCatalog({});
    global.fetch = previousFetch;
    global.window = previousWindow;
    global.document = previousDocument;
  }
});
