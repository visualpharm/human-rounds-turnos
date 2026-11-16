// Physical retirement of the classic account-form.js + window.SanaAccount bridge.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("account-form.js is gone from disk", () => {
  assert.equal(existsSync(join(ROOT, "account-form.js")), false);
  assert.equal(existsSync(join(ROOT, "static/platform/account-form.mjs")), true);
});

test("MIGRATED and CLIENT_LOADED name platform/account-form.mjs, not account-form.js", () => {
  const src = readFileSync(join(ROOT, "app/core/static_fingerprint.py"), "utf8");
  assert.match(src, /"platform\/account-form\.mjs"/);
  assert.doesNotMatch(src, /"account-form\.js"/);
});

test("production sources do not assign window.SanaAccount or load account-form.js as a classic script", () => {
  const skip = new Set(["node_modules", ".git", "tests", "docs", "stories", ".worktrees"]);
  const hits = [];
  function walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const name = entry.name;
      if (skip.has(name)) continue;
      const path = join(dir, name);
      // Directory entries classify ordinary files without stat-ing temporary
      // Python bytecode that another parallel test may already have renamed.
      // Follow links as before; missing source files/links still fail closed.
      const st = entry.isSymbolicLink() ? statSync(path) : entry;
      if (st.isDirectory()) { walk(path); continue; }
      if (!/\.(js|mjs|html|py)$/.test(name)) continue;
      const text = readFileSync(path, "utf8");
      if (/window\.SanaAccount\s*=(?!=)/.test(text)) hits.push(path + ": SanaAccount assignment");
      if (/static\(['"]account-form\.js['"]\)/.test(text)) hits.push(path + ": static account-form.js");
      if (/url\(['"]account-form\.js['"]\)/.test(text)) hits.push(path + ": url account-form.js");
    }
  }
  walk(ROOT);
  assert.deepEqual(hits, []);
});
