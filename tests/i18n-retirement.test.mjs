// Physical retirement of the classic i18n.js + window.SanaI18n bridge.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("i18n.js is gone from disk", () => {
  assert.equal(existsSync(join(ROOT, "i18n.js")), false);
});

test("MIGRATED and CLIENT_LOADED name platform/i18n.mjs, not i18n.js", () => {
  const src = readFileSync(join(ROOT, "app/core/static_fingerprint.py"), "utf8");
  assert.match(src, /"platform\/i18n\.mjs"/);
  assert.doesNotMatch(src, /"i18n\.js"/);
});

test("production sources do not assign window.SanaI18n or load i18n.js as a classic script", () => {
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
      if (/window\.SanaI18n\s*=/.test(text)) hits.push(path + ": SanaI18n assignment");
      if (/static\(['"]i18n\.js['"]\)/.test(text)) hits.push(path + ": static i18n.js");
      if (/url\(['"]i18n\.js['"]\)/.test(text)) hits.push(path + ": url i18n.js");
    }
  }
  walk(ROOT);
  assert.deepEqual(hits, []);
});
