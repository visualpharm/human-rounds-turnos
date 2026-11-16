// Source contracts for the Pinamar-only /admin/plan direct screen.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertSharedShellContract } from "./shared-shell-contract.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const js = readFileSync(join(ROOT, "static/platform/admin-roadmap.mjs"), "utf8");
const template = readFileSync(join(ROOT, "templates", "admin", "plan.html"), "utf8");

test("direct plan state owns the English technical query without hash fallback", () => {
  assert.match(js, /export function mount\(host, options = \{\}\)/);
  assert.match(js, /new URLSearchParams\(location\.search\)\.get\("view"\) === "technical"/);
  assert.match(js, /"\/admin\/plan\?view=technical"/);
  assert.match(js, /history\.pushState\(\{\}, "", link\.getAttribute\("href"\)\)/);
  assert.match(js, /window\.addEventListener\("popstate", onPopstate\)/);
  assert.doesNotMatch(js, /SanaAdminRouter|hashchange|location\.hash/);
  assert.match(js, /@media\(max-width:1100px\)\{\.rm-flow\{flex-direction:column/);
});

test("direct template has only the existing shared shell and roadmap controller", () => {
  assert.match(template, /extends "admin\/base\.html"/);
  assert.match(template, /id="sec-plan"/);
  assertSharedShellContract(assert, template, ["platform/admin-roadmap.mjs"]);
  assert.doesNotMatch(template, /location\.hash\s*=/);
  assert.doesNotMatch(template, /<hr\b|border-top|border-bottom/);
});
