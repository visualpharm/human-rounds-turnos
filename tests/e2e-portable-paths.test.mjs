// The browser suite must run on any machine (CI included): evidence goes under
// tests/e2e/lib/artifacts.mjs's root, never a literal home directory. Three
// suites once wrote to one developer's home and failed everywhere else.
import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const E2E = join(import.meta.dirname, "e2e");

function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry =>
    entry.isDirectory() ? files(join(dir, entry.name)) : entry.name.endsWith(".mjs") ? [join(dir, entry.name)] : []);
}

test("no browser suite hard-codes a home directory", () => {
  const offenders = files(E2E).filter(file => /["'`]\/(Users|home)\/[^/"'`]+\//.test(readFileSync(file, "utf8")));
  assert.deepEqual(offenders, []);
});
