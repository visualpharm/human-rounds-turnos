// Source contracts for the platform-core /admin/system direct screen.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JS = readFileSync(join(ROOT, "static/platform/admin-system.mjs"), "utf8");
const TEMPLATE = readFileSync(join(ROOT, "templates", "admin", "system.html"), "utf8");

test("System is a direct screen with no client hash registration", () => {
  assert.match(JS, /export function mount\(host, options = \{\}\)/);
  assert.doesNotMatch(JS, /DOMContentLoaded|SANA_ADMIN_SCREEN/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
  assert.match(JS, /fetch\("\/api\/admin\/system"/);
});

test("direct template uses the shared shell and fingerprinted controller", () => {
  assertSharedShellContract(assert, TEMPLATE, ["platform/admin-system.mjs"]);
  assert.match(TEMPLATE, /extends "admin\/base.html"/);
  assert.match(TEMPLATE, /id="sec-sistema"/);
  assert.doesNotMatch(TEMPLATE, /location\.hash\s*=/);
  assert.doesNotMatch(TEMPLATE, /<hr\b|border-top|border-bottom/);
});

test("direct configuration links return to the settings surface", () => {
  assert.match(JS, /"\/admin\/settings\?field=" \+ encodeURIComponent\(it\.campo \|\| ""\)/);
});

test("responsive key-value rows wrap and keep details readable", () => {
  assert.match(JS, /@media\(max-width:640px\)/);
  assert.match(JS, /sis-td-val,.sis-fix\{overflow-wrap:anywhere/);
  assert.match(JS, /sis-detalle\{[^}]*color:var\(--text,/);
  assert.match(JS, /sis-detalle\{[^}]*font-size:16px/);
});

test("a long status label wraps inside its fixed column instead of being cut", () => {
  const rule = JS.match(/\.sis-td-lab\{[^}]*\}/)[0];
  assert.doesNotMatch(rule, /white-space:nowrap/);
  assert.match(rule, /width:200px/);
  assert.match(rule, /overflow-wrap:break-word/);
});

test("row dividers are the only separators in the touched renderer", () => {
  assert.doesNotMatch(JS, /<hr\b|border-bottom/);
  assert.match(JS, /\.sis-tabla td\{[^}]*border-top/);
  assert.match(JS, /\.sis-tabla tr:first-child td\{border-top:0/);
});
