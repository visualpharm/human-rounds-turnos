import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const consult = readFileSync(join(ROOT, "app/modules/intake/admin-consult.mjs"), "utf8");
const queue = readFileSync(join(ROOT, "static/platform/admin-queue.mjs"), "utf8");
const patients = readFileSync(join(ROOT, "static", "platform", "admin-doctor-patients.mjs"), "utf8");
const template = readFileSync(join(ROOT, "app", "modules", "intake", "admin_consult.html"), "utf8");
const native = readFileSync(join(ROOT, "app", "modules", "intake", "admin-consult.mjs"), "utf8");

test("direct consultation state is canonical query state with history navigation", () => {
  assert.match(consult, /new URLSearchParams\(location\.search \|\| ""\)\.get\("interview"\)/);
  assert.match(consult, /history\.replaceState\(\{\}, "", canonical\)/);
  assert.match(consult, /listen\(win\(\), "popstate", route\)/);
  assert.match(consult, /function route\(\) \{\n    var id = routeId\(\);/);
  assert.match(consult, /return load\(id\);/);
  assert.match(consult, /location\.replace\("\/admin\/cola"\)/);
  assert.doesNotMatch(consult, /directScreen|SanaAdminRouter|location\.hash|hashchange/);
});

test("consultation links use the server-routed URL throughout", () => {
  assert.match(queue, /location\.assign\("\/admin\/consult\?interview=" \+ encodeURIComponent\(interviewId\)\)/);
  assert.match(patients, /"\/admin\/consult\?interview=" \+ encodeURIComponent\(iv\.id\)/);
  assert.match(consult, /href="\/admin\/cola"/);
  assert.doesNotMatch(queue, /location\.hash\s*=/);
  assert.doesNotMatch(patients, /#consulta\//);
});

test("the direct template reuses the shell and exact controller dependencies", () => {
  assert.equal((template.match(/module_static\('modules\/intake\/admin-consult\.mjs'\)/g) || []).length, 1);
  assert.doesNotMatch(template, /static\('admin-consulta\.js'\)/);
  assert.match(template, /mount: \(Consultation\) => Consultation\.mount\(host\)/);
  assert.match(template, /bootScreen\(\{/);
  assert.match(template, /extends "admin\/base.html"/);
  assert.match(template, /block screen_title/);
  assert.match(template, /id="sec-consulta"/);
  assert.match(template, /id="consult-status"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.doesNotMatch(template, /<h1\b|id="adm-h1"/);
  assert.doesNotMatch(template, /location\.hash\s*=/);
  assert.doesNotMatch(template, /<hr\b|border-top|border-bottom/);
  assert.doesNotMatch(template, /admin-patient-summary/);
  assert.match(native, /importStatic\("platform\/admin-patient-summary\.mjs"\)/);
  assert.match(native, /dispose\.ready = Promise\.all\(/);
});

test("native medication source retains mobile labels; rendered geometry has a separate browser gate", () => {
  assert.match(consult, /:is\(p,summary,label,span,th,td,input,textarea,button\)\{font-size:16px\}/);
  assert.match(consult, /\.cst-med-table thead\{display:none\}/);
  assert.match(consult, /td\[data-label\]::before\{content:attr\(data-label\)/);
  assert.match(consult, /function medicationLabels\(\)/);
  assert.match(consult, /data-label="' \+ labels\[0\]/);
});
