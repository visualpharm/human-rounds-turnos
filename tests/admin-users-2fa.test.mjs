import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const js = fs.readFileSync(new URL("../static/platform/admin-users.mjs", import.meta.url), "utf8");

test("user form shows 2FA status and offers login-time setup", () => {
  assert.match(js, /T\("admin\.system\.users\.estado_label", null, "Estado:"\)/);
  assert.match(js, /id="usr-force-2fa"/);
  assert.match(js, /T\("admin\.system\.users\.force_2fa_new", null, "Exigir que configure 2FA al ingresar"\)/);
  assert.match(js, /T\("admin\.system\.users\.force_2fa_replace", null, "Quitar el código actual y exigir uno nuevo al ingresar"\)/);
  assert.match(js, /id="usr-totp-quitar">.*T\("admin\.system\.users\.remove_tfa", null, "Quitar 2FA"\)/);
});

test("the user configures their own secret and completion clears the force flag", () => {
  assert.match(js, /body\.forzar_2fa = force2fa\.checked/);
  assert.doesNotMatch(js, /id="usr-totp-gen"/);
  assert.doesNotMatch(js, /Código de verificación generado/);
});

test("the user list distinguishes active, pending, and unconfigured 2FA", () => {
  assert.match(js, /if \(item\.tiene_2fa\) return T\("admin\.system\.users\.tfa\.activa", null, "Activa"\)/);
  assert.match(js, /if \(item\.requiere_configurar_2fa\) return T\("admin\.system\.users\.tfa\.pendiente", null, "Pendiente"\)/);
  assert.match(js, /return T\("admin\.system\.users\.tfa\.no_configurada", null, "No configurada"\)/);
  assert.match(js, /class="chip ' \+ clase2fa\(it\)/);
});
