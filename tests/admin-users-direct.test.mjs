import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JS = readFileSync(join(ROOT, "static", "platform", "admin-users.mjs"), "utf8");
const TEMPLATE = readFileSync(join(ROOT, "templates", "admin", "users.html"), "utf8");

test("direct Users state uses canonical query URLs and browser history", () => {
  assert.match(JS, /new URLSearchParams\(location\.search\)/);
  assert.match(JS, /params\.getAll\("user"\)/);
  assert.match(JS, /params\.getAll\("new"\)/);
  // El nombre de usuario dejó de tener tope de largo y alfabeto ASCII
  // (2026-09-05): la regla canónica vive en core/usernames.py y el navegador la
  // espeja acá. Lo único que sigue prohibido es un espacio o un carácter de
  // control, porque eso es un login que falla sin que nadie entienda por qué.
  assert.match(JS, /USERNAME_MAL = \/\[\\s\\u0000-\\u001f\\u007f\]\//);
  assert.match(JS, /USERNAME_MIN = 2/);
  assert.doesNotMatch(JS, /\{3,40\}/);
  assert.match(JS, /history\.pushState/);
  assert.match(JS, /window\.addEventListener\("popstate", route\)/);
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
  assert.doesNotMatch(TEMPLATE, /location\.hash\s*=/);
});

test("direct links use canonical paths and history", () => {
  assert.match(JS, /"\/admin\/users\?user=" \+ encodeURIComponent\(usuario\)/);
  assert.match(JS, /"\/admin\/users\?new=1"/);
  assert.match(JS, /function accountHref\(\) \{\s*return "\/admin\/account";/);
  assert.match(JS, /location\.assign\(accountHref\(\)\)/);
  assert.match(JS, /goTo\(link\.getAttribute\("href"\)\)/);
});

test("the direct template reuses the shared shell and exact dependencies", () => {
  assertSharedShellContract(assert, TEMPLATE,
    ["platform/admin-control-fit.mjs"]);
  assert.match(TEMPLATE, /extends "admin\/base.html"/);
  assert.match(TEMPLATE, /id="sec-usuarios"/);
  assert.match(TEMPLATE, /id="users-status"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(TEMPLATE, /SANA_STATIC\[["']platform\/admin-users\.mjs["']\]/);
  assert.match(TEMPLATE, /m\.boot\(\)/);
  assert.doesNotMatch(TEMPLATE, /admin-users\.js|SanaUsers|sanaRouteUsuarios/);
  assert.match(TEMPLATE, /current_user \| tojson/);
  assert.doesNotMatch(TEMPLATE, /current_user_json \| safe/);
  assert.doesNotMatch(TEMPLATE, /<hr\b|border-top|border-bottom/);
  assert.match(JS, /importDep\("platform\/admin-preferences\.mjs"/);
  assert.doesNotMatch(JS, /SanaPreferences|window\.SanaUsers|window\.sanaRouteUsuarios/);
});

test("the shared direct shell owns Users toolbar fit and mobile stacking", () => {
  const CSS = readFileSync(join(ROOT, "static", "root", "admin-direct.css"), "utf8");
  assert.match(CSS, /\.cnt-toolbar-filters\{display:flex;flex-wrap:nowrap/);
  assert.match(CSS, /\.cnt-toolbar-right\{display:flex;flex-wrap:wrap/);
  assert.match(CSS, /\.cnt-toolbar-filters \.adm-sel\{width:var\(--control-fit-width,auto\)/);
  assert.match(CSS, /\.cnt-toolbar-filters \.cnt-search\{[^}]*font-size:16px/);
  assert.match(CSS, /@media\(max-width:760px\)\{[\s\S]*\.cnt-toolbar-filters\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(CSS, /@media\(max-width:520px\)\{[\s\S]*\.cnt-toolbar-filters\{grid-template-columns:minmax\(0,1fr\)/);
  assert.match(CSS, /\.cnt-row \.cnt-in,\.cnt-row \.adm-sel\{max-width:100%;box-sizing:border-box/);
  assert.match(CSS, /\.cnt-modo-op\{[^}]*min-width:0;max-width:100%;box-sizing:border-box/);
  assert.match(CSS, /\.adm-content-wrap>:is\(#sec-servicios,#sec-centros,#sec-medicos,#sec-usuarios,[^)]*\),[^{]*\{padding-left:var\(--adm-gutter\);padding-right:var\(--adm-gutter\);box-sizing:border-box/);
  assert.match(CSS, /\.usr-centros\{grid-template-columns:minmax\(0,1fr\)/);
});

test("doctor names reuse the shared normal-capitalization formatter", () => {
  assert.match(JS, /Format\.nombre/);
  assert.match(JS, /importDep\("platform\/format\.mjs"/);
  assert.doesNotMatch(JS, /SanaFormat|sanaImportStatic/);
  assert.match(JS, /esc\(nombre\(m\.nombre\)\)/);
});
