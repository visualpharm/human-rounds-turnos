// Source guards for the direct /admin/legal screen (Tier 5 platform-core):
// admin-legal.js's direct-mode branch and templates/admin/legal.html's
// cohort. Same shape as admin-prompts-direct.test.mjs — node --test over the
// file text, no browser (the behaviour gate is the /admin/legal case in
// tests/e2e/admin-screen.spec.mjs).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const legal = readFileSync(join(ROOT, "static", "platform", "admin-legal.mjs"), "utf8");
const template = readFileSync(join(ROOT, "templates", "admin", "legal.html"), "utf8");

test("direct legal state is canonical ?doc= query state with history navigation", () => {
  assert.match(legal, /new URLSearchParams\(location\.search\)\.get\("doc"\)/);
  assert.match(legal, /history\.replaceState\(\{\}, "", canonical\)/);
  assert.match(legal, /history\.pushState\(\{\}, "", legalHref\(doc\)\)/);
  assert.match(legal, /window\.addEventListener\("popstate", onPopstate\);/);
  // the doc id is the wire value, never a translated label
  assert.match(legal, /"\/admin\/legal" \+ \(doc \? "\?doc=" \+ encodeURIComponent\(doc\) : ""\)/);
  assert.doesNotMatch(legal, /location\.hash|SanaAdminRouter|hashchange|#app/);
  // a user-controlled ?doc= never walks the prototype chain
  assert.match(legal, /var ITEMS = Object\.create\(null\)/);
  assert.match(legal, /Object\.prototype\.hasOwnProperty\.call\(ITEMS, id\)/);
});

test("direct in-page navigation is one delegated handler on plain left clicks only", () => {
  assert.match(legal, /sec\.addEventListener\("click", function \(event\) \{/);
  assert.match(legal, /event\.button !== 0/);
  assert.match(legal, /event\.metaKey \|\| event\.ctrlKey \|\| event\.shiftKey \|\| event\.altKey/);
  assert.match(legal, /closest\("a\[data-legal-doc\]"\)/);
  assert.match(legal, /data-legal-doc/);
  // cards, crumb and the error link all carry the marker through one helper
  assert.equal((legal.match(/docAttr\(/g) || []).length, 4, "docAttr: definition + card + crumb + error link");
  assert.match(legal, /esc\(legalHref\(it\.id\)\)/);
  assert.match(legal, /esc\(legalHref\(""\)\)/);
});

test("direct shell delegates only confirmed session transitions and keeps ordinary errors local", () => {
  assert.match(legal, /function directStatus\(msg, isError, linkHref, linkText\)/);
  assert.match(legal, /document\.getElementById\("legal-status"\)/);
  assert.match(legal, /function toast\(msg\) \{\s*directStatus\(msg, false\);\s*\}/);
  assert.match(legal, /\(r\.status === 401 \|\| r\.status === 403\)/);
  assert.match(legal, /sessionReady\.then\(function \(mod\) \{ return mod\.confirmar\(r, undefined, function \(\) \{ return isCurrent\(epoch\); \}\); \}\)/);
  assert.match(legal, /if \(transitioned\) throw new Error\("401"\);/);
  assert.doesNotMatch(legal, /SanaAdminLogin|session_expired|sign_in/);
  assert.doesNotMatch(legal, /location\.assign\(|location\.hash|hashchange|SanaAdminRouter|SanaAdminSections|#app|#legal|showGate/);
  // a11y: the textarea has an accessible name in both modes
  assert.match(legal, /<textarea class="lg-ta" id="lg-cuerpo" spellcheck="true" aria-label="' \+ esc\(it\.titulo\) \+ '">/);
});

test("direct template loads only the legal cohort, in order, and owns a local status boundary", () => {
  assert.match(template, /extends "admin\/base\.html"/);
  assert.match(template, /<div id="sec-legal"><\/div>/);
  assert.match(template, /id="legal-status"[^>]*role="status"[^>]*aria-live="polite"[^>]*hidden/);
  assertSharedShellContract(assert, template, []);
  assert.match(template, /SANA_STATIC\["platform\/admin-legal\.mjs"\]/);
  assert.equal((template.match(/static\('/g) || []).length, 0, "no extra assets");
  for (const forbidden of ["admin-app.js", "admin-router.js", "admin-sections.js", "admin-table.js", "admin-prompts.js"]) {
    assert.doesNotMatch(template, new RegExp(forbidden.replace(".", "\\.")), forbidden);
  }
  assert.doesNotMatch(template, /location\.hash\s*=/);
  assert.doesNotMatch(template, /<style|<hr\b|border-top|border-bottom|role="tab"/);
});

test("editor CSS is mobile-safe: 16px textarea and 44px Save and Restore buttons", () => {
  // iOS Safari zooms the page when a focused field is below 16px.
  const ta = /\.lg-ta\{([^}]*)\}/.exec(legal);
  assert.ok(ta, ".lg-ta rule present");
  const taSize = /font-size:(\d+(?:\.\d+)?)px/.exec(ta[1]);
  assert.ok(taSize && parseFloat(taSize[1]) >= 16, `.lg-ta font-size must be >= 16px, got ${taSize && taSize[1]}`);
  // Save and Restore both reach 44px.
  assert.match(legal, /\.lg-actions \.btn\{min-height:44px;min-width:44px;\}/);
  assert.match(legal, /\.lg-actions-danger button\{display:inline-flex;align-items:center;min-height:44px;min-width:44px;[^}]*\}/);
  assert.match(legal, /<button type="button" id="lg-restaurar">/);
  assert.match(legal, /<button type="button" class="btn btn-accent" id="lg-guardar">/);
});

test("the direct session-expiry strings exist in the es catalog (en mirrors them)", () => {
  const es = JSON.parse(readFileSync(join(ROOT, "locales", "es", "admin.content.json"), "utf8"));
  const en = JSON.parse(readFileSync(join(ROOT, "locales", "en", "admin.content.json"), "utf8"));
  for (const key of ["admin.content.legal.session_expired", "admin.content.legal.sign_in"]) {
    assert.equal(typeof es[key], "string", key + " (es)");
    assert.equal(typeof en[key], "string", key + " (en)");
  }
});
