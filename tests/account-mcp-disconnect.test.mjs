import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../static/platform/account.mjs", import.meta.url), "utf8");

function body(name) {
  const start = src.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} exists`);
  const next = src.indexOf("\n  function ", start + 10);
  return src.slice(start, next < 0 ? undefined : next);
}

test("after «Desconectar el asistente» the screen says so and issues no token by itself", () => {
  const render = body("renderMcp");
  // Drawing the state never asks the server: GET /api/account/mcp creates a token when there is none.
  assert.doesNotMatch(render, /fetch\(/);
  // The only call that loads the connection data is the person's own click on the link.
  assert.match(render, /ver\.addEventListener\("click",function\(\)\{ cargarMcp\(el,false\); \}\)/);
  // The disconnected state is its own message and an explicit «connect again» label.
  assert.match(render, /account\.dash\.mcp_desconectado/);
  assert.match(render, /account\.dash\.mcp_reconectar/);
});

test("the revoke handler draws the disconnected state, not the plain one", () => {
  const load = body("cargarMcp");
  assert.match(load, /mcp\/revoke[\s\S]*renderMcp\(el,true\)/);
  // Nothing else in the file loads the token on its own.
  const calls = src.match(/cargarMcp\(/g) || [];
  assert.equal(calls.length, 3, "definition, the «Datos de conexión» click, and «Generar uno nuevo»");
});
