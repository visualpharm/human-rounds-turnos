/* admin-numbers-font.test.mjs — numbers and labels in the panel use the
   body font (ui-expert review 2026-11-15, variant B). Monospace stays only
   for real code: one-time codes, the TOTP secret, raw JSON, hex colours and
   the AI's technical block. Proportional digits on purpose: Manrope's
   tabular «1» is a «0»-wide glyph, so «157» read «1 57» (same as D26 in
   listing-review-1115.test.mjs), and no tabular-nums anywhere in the panel.

   Run:  node --test tests/admin-numbers-font.test.mjs */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL("../" + p, import.meta.url), "utf8");
const FILES = {
  css: read("static/root/admin-direct.css"),
  content: read("static/platform/admin-content.mjs"),
  summary: read("static/platform/admin-patient-summary.mjs"),
};

const NUMBERS = [
  ["css", ".badge"], ["css", ".rep-kpi-value"], ["css", ".rep-bar-num"],
  ["css", ".rep-sumcard-value"], ["css", ".rep-mini-bar b"], ["css", ".rep-centro-rating"],
  ["content", ".cnt-tab.has-appointments:after"], ["content", ".medcal-sobre"],
  ["summary", ".rp-metric-value"], ["summary", ".rp-item-src"], ["summary", ".rp-timeline-year"],
  ["summary", ".rp-chart-latest"], ["summary", ".rp-fuentes"],
];

function rule(src, sel) {
  const i = src.indexOf(sel + "{");
  assert.ok(i >= 0, "rule not found: " + sel);
  return src.slice(i, src.indexOf("}", i));
}

for (const [file, sel] of NUMBERS) {
  test(sel + " uses the body font, proportional digits", () => {
    const r = rule(FILES[file], sel);
    assert.match(r, /var\(--font\)/);
    assert.doesNotMatch(r, /font-mono|monospace|tabular-nums/);
  });
}

test("the patient's lab chart shows its latest value like the panel's", () => {
  assert.match(rule(read("static/platform/account-labs.mjs"), ".lr-chart-latest"), /var\(--font\)/);
});

// Round 3: Manrope has no alternate «1» (only tnum/pnum, no ss/cv), so the
// panel drops tabular digits everywhere; numeric columns align right instead.
const PANEL = [
  "static/root/admin-direct.css", "static/platform/admin-queue.css",
  "static/platform/doctor-workspace.css", "static/platform/admin-content.mjs",
  "static/platform/admin-hours-editor.mjs", "static/platform/admin-patients.mjs",
  "static/platform/admin-roadmap.mjs", "static/platform/admin-solicitudes.mjs",
  "static/platform/admin-patient-summary.mjs", "static/platform/format.mjs",
];
for (const file of PANEL) {
  test(file + " has no tabular digits", () => {
    assert.doesNotMatch(read(file), /tabular-nums/);
  });
}

test("numeric columns still align right", () => {
  assert.match(rule(FILES.css, ".cnt-table th.cnt-numeric,.cnt-table td.cnt-numeric"), /text-align:right/);
  assert.match(rule(read("static/platform/admin-solicitudes.mjs"), ".sol-hoy-tabla .cnt-numeric"), /text-align:right/);
});

test("code stays monospace", () => {
  const CODE = [
    ["static/root/admin-direct.css", ".usr-totp code"],
    ["static/root/admin-direct.css", ".cnt-pre"],
    ["static/root/admin-gate.css", "#gate-code"],
    ["static/platform/admin-account.mjs", ".acct-field .acct-code"],
    ["static/platform/admin-account.mjs", ".acct-secret code"],
    ["static/platform/admin-appearance.mjs", ".cfg-mk-hex"],
    ["static/platform/admin-prompts.mjs", ".pr-tech pre"],
  ];
  for (const [file, sel] of CODE) assert.match(rule(read(file), sel), /--font-mono|--mono|monospace/, sel);
});
