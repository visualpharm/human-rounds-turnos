// tests/listing-multi-facet.test.mjs — a row may carry SEVERAL values for one
// facet in `r[key + "_todas"]` (a doctor with two specialties, hospital
// TASK-35). The facet menu, the filter and the ?specialty= pre-selection all
// read the list; a row without it keeps the scalar r[key]. SanaListing's
// closure needs a <document>, so — same pattern as listing-groups.test.mjs —
// these are guards over the source plus a behavioural copy of the helper.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = readFileSync(join(ROOT, "static/platform/listing.mjs"), "utf8");

function helpers() {
  const m = /function rowFacetVals\(r, key\) \{[\s\S]*?\n    \}\n    function rowHasFacet\(r, key, v\) \{[^\n]*\}/.exec(SRC);
  assert.ok(m, "rowFacetVals + rowHasFacet must exist in listing.mjs");
  return new Function(m[0] + "; return { rowFacetVals, rowHasFacet };")();
}

test("a row with two specialties is found by either, and by neither of the others", () => {
  const { rowFacetVals, rowHasFacet } = helpers();
  const row = { especialidad: "Pediatría · Cardiología", especialidad_todas: ["Pediatría", "Cardiología"] };
  assert.deepEqual(rowFacetVals(row, "especialidad"), ["Pediatría", "Cardiología"]);
  assert.equal(rowHasFacet(row, "especialidad", "Cardiología"), true);
  assert.equal(rowHasFacet(row, "especialidad", "Pediatría"), true);
  assert.equal(rowHasFacet(row, "especialidad", "Nutrición"), false);
});

test("a row without the list keeps the scalar value, an empty one matches nothing", () => {
  const { rowFacetVals, rowHasFacet } = helpers();
  assert.deepEqual(rowFacetVals({ centro: "Hospital" }, "centro"), ["Hospital"]);
  assert.equal(rowHasFacet({ centro: "Hospital" }, "centro", "Hospital"), true);
  assert.deepEqual(rowFacetVals({}, "centro"), []);
  assert.equal(rowHasFacet({ centro: "" }, "centro", ""), false);
});

test("the menu, the filter and the URL pre-selection all go through the helper", () => {
  assert.match(SRC, /rowFacetVals\(r, key\)\.forEach/);
  assert.match(SRC, /!rowHasFacet\(r, k, state\.facet\[k\]\)/);
  assert.match(SRC, /rowHasFacet\(r, f\.key, v\)/);
  assert.doesNotMatch(SRC, /r\[k\] !== state\.facet\[k\]/);
});
