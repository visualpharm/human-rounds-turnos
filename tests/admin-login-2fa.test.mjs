import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const adminLogin = readFileSync(join(ROOT, "static", "platform", "admin-login.mjs"), "utf8");
const loginTpl = readFileSync(join(ROOT, "templates", "admin", "login.html"), "utf8");

test("MFA stays hidden until the account-specific login response requires it", () => {
  assert.match(loginTpl, /id="gate-2fa" style="display:none;"/);
  assert.match(adminLogin, /function show2fa\(on\)\{\s*NEED_TOTP = on;\s*\$\('#gate-2fa'\)\.style\.display = on \? '' : 'none';/s);
  assert.match(adminLogin, /if\(j\.need_totp\)\{\s*show2fa\(true\);/s);
  assert.doesNotMatch(adminLogin, /preverCodigo/);
  assert.doesNotMatch(adminLogin, /on \|\| CFG\.totp/);
});

test("asking for the 2FA code is not counted as a login error", () => {
  // Every «error» session on the demo was password OK → code → login_ok.
  assert.match(adminLogin, /const pasoCodigo = j\.need_totp && !j\.error;\s*if\(!pasoCodigo\) gpBeacon\('login_error'/s);
  assert.doesNotMatch(adminLogin, /tsReset\(\);\s*gpBeacon\('login_error'/s);
});

test("the MFA step explains where the 6-digit code comes from", () => {
  assert.match(adminLogin, /Ingresá el código de 6 dígitos de tu app de autenticación\./);
  assert.match(loginTpl, /name="otp"/);
  assert.match(loginTpl, /autocomplete="one-time-code"/);
});
