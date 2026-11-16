import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parse } from "acorn";
import { setCatalog, t } from "../static/platform/i18n.mjs";

// Clinical DOM mounts are additionally tested in lab-results and patient-summary.
const surfaces = [
  ["voice composer", "static/platform/chat-ui.mjs", "ai.notice.voice", '<textarea class="'],
  ["web chat", "static/platform/chat-widget.mjs", "ai.notice.chat", '<form class="stzc-form"'],
  ["every booking order upload", "app/modules/turnos/wz-upload.mjs", "ai.notice.order", '<div class="wz-uploadzone"'],
  ["MCP connection", "static/platform/account.mjs", "ai.notice.mcp", 'id="acc-mcp-ver"'],
  ["DNI photo", "static/platform/account-form.mjs", "ai.notice.dni", 'id="sc-scan-f"'],
  ["consultation", "app/modules/intake/admin-consult.mjs", "ai.notice.clinical", "updated + '<p class=\"cst-oneliner\">'"],
];

test("interview identifies AI once in the first message instead of a duplicate heading notice", () => {
  const agent = readFileSync(new URL("../app/modules/intake/interview_agent.py", import.meta.url), "utf8");
  const client = readFileSync(new URL("../app/modules/intake/entrevista.js", import.meta.url), "utf8");
  const opening = agent.match(/_APERTURA = \(([\s\S]*?)\)\n/)[1];
  assert.match(opening, /Soy el asistente de inteligencia artificial/);
  assert.match(opening, /durante la consulta/);
  // The legal scope moved into this message with the heading notice; it must
  // not be lost in the move (entrevista-previa.md, Guardrails).
  assert.match(opening.replace(/"\s+"/g, ""), /no la reemplaza ni da consejos médicos/);
  assert.match(agent, /dialogue\.copy\('opening', _APERTURA, ledger\)/);
  assert.doesNotMatch(client, /ai\.notice\.interview|iv-topbar-sub/);
});

function findNotice(tree, key) {
  if (!tree || typeof tree !== "object") return null;
  if (tree.type === "CallExpression" && tree.callee.name === "T" && tree.arguments[0]?.value === key) return tree;
  for (const value of Object.values(tree)) {
    if (Array.isArray(value)) {
      for (const child of value) { const found = findNotice(child, key); if (found) return found; }
    } else if (value && typeof value === "object") {
      const found = findNotice(value, key); if (found) return found;
    }
  }
  return null;
}

for (const [surface, filename, key, control] of surfaces) {
  test(surface + ": owned AI notice renders in ES/EN before the control", () => {
    const source = readFileSync(new URL("../" + filename, import.meta.url), "utf8");
    const node = findNotice(parse(source, { ecmaVersion: "latest", sourceType: "module" }), key);
    assert.ok(node);
    const fallback = node.arguments[2].value;
    assert.match(fallback, /inteligencia artificial/);
    assert.ok(node.start < source.indexOf(control, node.start));
    for (const language of ["es", "en"]) {
      const catalog = JSON.parse(readFileSync(new URL("../locales/" + language + "/ai.json", import.meta.url), "utf8"));
      setCatalog(catalog);
      assert.equal(t(key, null, fallback), catalog[key]);
      assert.ok(t(key, null, fallback).length > 20);
    }
    setCatalog({});
    assert.equal(t(key, null, fallback), fallback);
  });
}

test("consultation: the one AI notice is concatenated into the AI card, and the summary below is told not to repeat it", () => {
  const source = readFileSync(new URL("../app/modules/intake/admin-consult.mjs", import.meta.url), "utf8");
  // Built once...
  assert.equal(source.split('T("ai.notice.clinical"').length - 1, 1);
  assert.match(source, /var aiNotice = '<p class="adm-ai-notice" data-ai-notice-inline="clinical">'/);
  // ...and actually rendered, first thing inside the card that holds the AI text.
  assert.ok(source.includes("'<div class=\"card cst-brief\">' + aiNotice + updated + '<p class=\"cst-oneliner\">'"));
  assert.equal(source.split("aiNotice +").length - 1, 1);
  // The patient summary would otherwise say it a second time.
  assert.match(source, /PatientSummary\.mount\(summaryHost, \{[^}]*aiNotice: false[^}]*\}\)/);
  // In-card notice: not the list-level marker the disclosure CSS hides.
  assert.doesNotMatch(source, /data-ai-notice="clinical"/);
});
