// A 403 {error:"2fa_requerido"} sends the browser to Account's 2FA setup —
// except when it already IS on Account: a doctor tester whose 2FA was
// required at once saw the page reload in a loop and flicker white
// (2026-10-19). The real module, with a fake location.
import test from "node:test";
import assert from "node:assert/strict";

const URL_MOD = new URL("../static/platform/admin-session.mjs", import.meta.url);

function denied() {
  return new Response(JSON.stringify({ error: "2fa_requerido" }),
    { status: 403, headers: { "content-type": "application/json" } });
}

async function at(pathname) {
  const loc = { pathname, search: "", href: pathname };
  globalThis.window = { location: loc };
  const mod = await import(URL_MOD.href + "?t=" + Math.random());
  return { mod, loc };
}

test("from another screen, 2fa_requerido goes to the 2FA setup", async () => {
  const { mod, loc } = await at("/admin/cola");
  assert.equal(await mod.confirmar(denied()), true);
  assert.equal(loc.href, "/admin/account?setup=2fa");
});

test("on Account itself, 2fa_requerido never reloads the page", async () => {
  const { mod, loc } = await at("/admin/account");
  loc.search = "?setup=2fa";
  loc.href = "/admin/account?setup=2fa#keep";
  assert.equal(await mod.confirmar(denied()), false, "no transition: Account keeps its form");
  assert.equal(loc.href, "/admin/account?setup=2fa#keep", "no navigation");
});
