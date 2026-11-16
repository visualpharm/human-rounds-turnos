// The browser suite is @playwright/test (playwright.config.mjs, tests/e2e/*.spec.mjs). Three
// rules of that setup are structural and silent when broken, so they are pinned here:
//   1. One app + database per FILE. The app is a worker-scoped fixture keyed by the
//      `appOptions` of the file's `test.use(...)`; two files with equal options would
//      share one running app and its state, so every file names its own `dbName`.
//   2. No fixed sleeps: `waitForTimeout` is a wait-then-look race that passes by luck
//      under a quiet machine; waits are retrying expects, events or `holdsFor` windows.
//   3. The node:test runner is gone from the browser suite: a spec imports the shared
//      fixtures, never `node:test`/`node:assert`, and no `.e2e.mjs` copy survives.
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const E2E = join(import.meta.dirname, "e2e");
const specs = readdirSync(E2E).filter((name) => name.endsWith(".spec.mjs")).sort();
const read = (name) => readFileSync(join(E2E, name), "utf8");
// Code only: a header may name the sleep it replaced.
const code = (name) => read(name).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/.*$/gm, "$1");

test("the browser suite has spec files and no node:test copies", () => {
  assert.ok(specs.length > 0, "no tests/e2e/*.spec.mjs found");
  assert.deepEqual(readdirSync(E2E).filter((name) => name.endsWith(".e2e.mjs")), []);
});

test("every spec names its own throwaway database in test.use({ appOptions })", () => {
  const seen = new Map();
  for (const name of specs) {
    const source = read(name);
    assert.match(source, /test\.use\(\s*\{[\s\S]*?appOptions:/, `${name}: missing test.use({ appOptions })`);
    const top = source.match(/appOptions:\s*\{[\s\S]*?dbName:\s*["'`](sana_turnos_test_[A-Za-z0-9_]+)["'`]/);
    assert.ok(top, `${name}: appOptions needs a literal dbName "sana_turnos_test_..."`);
    assert.ok(!seen.has(top[1]), `${name} and ${seen.get(top[1])} share dbName ${top[1]}`);
    seen.set(top[1], name);
  }
});

test("no spec sleeps for a fixed time before looking", () => {
  const offenders = specs.filter((name) => /\bwaitForTimeout\s*\(/.test(code(name)));
  assert.deepEqual(offenders, []);
});

test("no spec uses the node:test runner or node:assert", () => {
  const offenders = specs.filter((name) => /from\s+["']node:(test|assert)(\/strict)?["']/.test(code(name)));
  assert.deepEqual(offenders, []);
});
