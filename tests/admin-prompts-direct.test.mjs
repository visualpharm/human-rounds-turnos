import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const prompts = readFileSync(join(ROOT, "static", "platform", "admin-prompts.mjs"), "utf8");
const template = readFileSync(join(ROOT, "templates", "admin", "prompts.html"), "utf8");

test("direct prompt state is canonical query state with history navigation", () => {
  assert.match(prompts, /new URLSearchParams\(location\.search\)\.get\("prompt"\)/);
  assert.match(prompts, /var ITEMS = Object\.create\(null\)/);
  assert.match(prompts, /Object\.prototype\.hasOwnProperty\.call\(ITEMS, clave\)/);
  assert.match(prompts, /history\.replaceState\(\{\}, "", canonical\)/);
  assert.match(prompts, /history\.pushState\(\{\}, "", promptHref\(key\)\)/);
  assert.match(prompts, /export function mount\(\)/);
  assert.match(prompts, /window\.addEventListener\("popstate", onPopstate\);/);
  assert.match(prompts, /export function destroy\(\)/);
  assert.doesNotMatch(prompts, /location\.hash|hashchange|SanaAdminRouter|#app|#prompts/);
});

test("prompt language follows html lang and the canonical i18n module", () => {
  assert.match(prompts, /function idiomaActivo\(\)/);
  assert.match(prompts, /document\.documentElement && document\.documentElement\.lang/);
  assert.doesNotMatch(prompts, /window\.SanaI18n/);
  assert.doesNotMatch(prompts, /SanaAdminLocale/);
});

test("direct links remain inside the server shell and carry the direct query state", () => {
  assert.match(prompts, /"\/admin\/prompts" \+ \(clave \? "\?prompt=" \+ encodeURIComponent\(clave\) : ""\)/);
  assert.match(prompts, /data-prompt-key/);
  assert.match(prompts, /history\.pushState\(\{\}, "", promptHref\(key\)\)/);
});

test("direct shell declares only a title and its own controller, and owns a local status boundary", () => {
  assert.match(template, /extends "admin\/base\.html"/);
  assert.match(template, /id="sec-prompts"/);
  assert.match(template, /id="prompts-status"[^>]*role="status"[^>]*aria-live="polite"/);
  // The shared runtime (i18n/format/dates/toast/bell) is base.html's now;
  // this screen's only script is its controller.
  assertSharedShellContract(assert, template, []);
  assert.match(template, /SANA_STATIC\["platform\/admin-prompts\.mjs"\]/);
  assert.doesNotMatch(template, /admin-app\.js/);
  assert.match(prompts, /function directStatus\(msg, isError\)/);
  assert.match(prompts, /function toast\(msg\) \{\s*directStatus\(msg, false\);\s*\}/);
  assert.doesNotMatch(template, /location\.hash\s*=/);
  assert.doesNotMatch(template, /<hr\b|border-top|border-bottom/);
});
