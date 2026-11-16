// Physical retirement of the classic dependents.js + window.SanaDependents bridge.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("dependents.js is gone from disk", () => {
  assert.equal(existsSync(join(ROOT, "dependents.js")), false);
  assert.equal(existsSync(join(ROOT, "static/platform/dependents.mjs")), true);
});

test("MIGRATED and CLIENT_LOADED name platform/dependents.mjs, not dependents.js", () => {
  const src = readFileSync(join(ROOT, "app/core/static_fingerprint.py"), "utf8");
  assert.match(src, /"platform\/dependents\.mjs"/);
  assert.doesNotMatch(src, /"dependents\.js"/);
});

test("production sources do not assign window.SanaDependents or load dependents.js as a classic script", () => {
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
      if (/window\.SanaDependents\s*=(?!=)/.test(text)) hits.push(path + ": SanaDependents assignment");
      if (/static\(['"]dependents\.js['"]\)/.test(text)) hits.push(path + ": static dependents.js");
      if (/url\(['"]dependents\.js['"]\)/.test(text)) hits.push(path + ": url dependents.js");
    }
  }
  walk(ROOT);
  assert.deepEqual(hits, []);
});
