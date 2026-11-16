import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const JS = readFileSync(join(ROOT, "static/platform/admin-settings.mjs"), "utf8");
const SYSTEM = readFileSync(join(ROOT, "static/platform/admin-system.mjs"), "utf8");
const TEMPLATE = readFileSync(join(ROOT, "templates", "admin", "settings.html"), "utf8");

test("direct state is query-based, canonical, and field-owned", () => {
  assert.match(JS, /new URLSearchParams\(location\.search/);
  assert.match(JS, /params\.getAll\("view"\)/);
  assert.match(JS, /params\.getAll\("field"\)/);
  assert.match(JS, /var owner = fieldView\(requested\.field\)/);
  assert.match(JS, /VISTA = owner \|\| requested\.view/);
  assert.match(JS, /history\[replace \? "replaceState" : "pushState"\]/);
  assert.match(JS, /addEventListener\("popstate"/);
  assert.doesNotMatch(JS, /location\.hash\s*=/);
});

test("field lookup is exact and safe for every form control", () => {
  assert.match(JS, /getAttribute\("data-campo"\) === clave/);
  assert.match(JS, /querySelector\("input, select, textarea"\)/);
  assert.doesNotMatch(JS, /querySelector\('\.set-row\[data-campo="' \+ campoPendiente/);
});

test("direct navigation remains ordinary links with browser history", () => {
  assert.match(TEMPLATE, /<nav class="set-subnav" aria-label="\{\{ t\('admin\.system\.settings\.nav\.aria_label', 'Settings sections'\) \}\}">/);
  assert.match(TEMPLATE, /href="\/admin\/settings" data-settings-view="general"/);
  assert.match(TEMPLATE, /href="\/admin\/settings\?view=connectors" data-settings-view="connectors"/);
  assert.match(TEMPLATE, /href="\/admin\/settings\?view=integrations" data-settings-view="integrations"/);
  assert.match(JS, /history\.pushState\(\{\}, "", canonical\)/);
  assert.match(JS, /data-settings-view/);
  assert.match(JS, /setAttribute\("aria-current", "page"\)/);
  assert.doesNotMatch(JS, /\.set-subnav\{[^}]*border/);
});

test("settings has no retired client hash registration", () => {
  assert.doesNotMatch(JS, /location\.hash|SanaAdminRouter|hashchange|#app/);
  assert.doesNotMatch(JS, /sanaRouteSettings|DOMContentLoaded/);
  assert.match(JS, /export function mount\(host, options = \{\}\)/);
  assert.match(JS, /removeEventListener\("popstate", onPopstate\)/);
  assert.match(JS, /removeEventListener\("click", onClick\)/);
});

test("template uses the shared shell and fingerprinted dependency order", () => {
  assertSharedShellContract(assert, TEMPLATE, ["platform/admin-settings.mjs"]);
  assert.match(TEMPLATE, /extends "admin\/base.html"/);
  assert.match(TEMPLATE, /id="sec-settings"/);
  assert.doesNotMatch(TEMPLATE, /<hr\b|border-top|border-bottom/);
});

// Review 03/11: the «Probar» notice sat above every Settings tab; it starts
// hidden and the module reveals it only on Integraciones, where «Probar» lives.
test("the Probar AI notice starts hidden and no card opens with a gray explanation", () => {
  assert.match(TEMPLATE, /<p data-ai-notice="probe" hidden /);
  assert.match(JS, /probeNotice\.hidden = VISTA !== "integrations"/);
  assert.doesNotMatch(JS, /class="set-lede">/);
  assert.doesNotMatch(JS, /modo_legacy|TURNOS_WEBDATA_DIR/);
});

test("System links use an encoded direct settings URL", () => {
  assert.match(SYSTEM, /"\/admin\/settings\?field=" \+ encodeURIComponent\(it\.campo \|\| ""\)/);
  assert.doesNotMatch(SYSTEM, /location\.hash|SanaAdminRouter|hashchange|#app/);
});
