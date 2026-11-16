// «Ver el original de webdata» is a support tool (pinamar-turnos 27.10):
// the raw JSON at the foot of a catalog form is painted for the admin only,
// never for a doctor opening its own tab or for an editor.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = fs.readFileSync(path.join(root, "static/platform/admin-content.mjs"), "utf8");

test("el original de webdata sólo lo ve el admin", () => {
  assert.match(src, /var esAdmin = function \(\) \{ return window\.CURRENT_ROLE === "admin"; \};/);
  const fn = /function renderOriginal\(sec, tipo, item\) \{[\s\S]*?\n  \}\n/.exec(src)[0];
  assert.match(fn, /^function renderOriginal\(sec, tipo, item\) \{\n    if \(!esAdmin\(\) \|\| !item \|\| item\._origen !== "json\+db"\) return;/);
});
