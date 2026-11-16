// table-family-capture.mjs — captures the render-parity manifest of the
// table-family consolidation (tests/fixtures/table-family-parity.json).
// Run from the repo root:
//
//   node tests/fixtures/table-family-capture.mjs before   # step 1: frozen BEFORE any change
//   node tests/fixtures/table-family-capture.mjs after    # final step: post-migration
//
// It imports the REAL render modules (never a reimplementation) and renders
// each screen's header with its real column shape, so the manifest pins the
// bytes the screens actually ship. The `before` run also freezes, once, the
// source slices of the six ad-hoc sortable-header producers — the record of
// what each ad-hoc copy looked like when the consolidation started. The
// `changes` list, the family map and the exemptions are maintained by hand.
import { writeFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FIXTURE = join(ROOT, "tests", "fixtures", "table-family-parity.json");
const WHEN = process.argv[2] || "";
if (WHEN !== "before" && WHEN !== "after") {
  console.error("usage: node tests/fixtures/table-family-capture.mjs before|after");
  process.exit(1);
}

// The six ad-hoc sortable-header producers, frozen once at the start. Line
// ranges are exact at origin/main (3700e108); freezing only happens while
// `before.source` is missing, so the ranges never re-run against migrated
// source.
const SLICES = [
  ["admin-queue.th", "static/platform/admin-queue.mjs", 1234, 1255],
  ["admin-patients.thead", "static/platform/admin-patients.mjs", 718, 727],
  ["admin-users.theadHtml", "static/platform/admin-users.mjs", 378, 384],
  ["admin-content.catalog-th", "static/platform/admin-content.mjs", 2286, 2310],
  ["admin-content.medcal-th", "static/platform/admin-content.mjs", 3011, 3016],
  ["admin-solicitudes.colsHtml", "static/platform/admin-solicitudes.mjs", 511, 526]
];

// format.mjs needs the phone shape of the active pack (same env the
// Interviews story seeds from packs/ar/country.json before importing).
const require = createRequire(import.meta.url);
globalThis.SANA_COUNTRY_PHONE = require(join(ROOT, "packs/ar/country.json")).phone;

const Solicitudes = await import(join(ROOT, "static/platform/admin-solicitudes.mjs"));

const cols = Solicitudes.columns();
const states = {
  asc: { key: "paciente", dir: 1 },
  desc: { key: "paciente", dir: -1 },
  off: { key: "dni", dir: 1 }
};
const header = {};
for (const [name, sort] of Object.entries(states)) {
  header[name] = Solicitudes.colsHtml(cols, sort);
}

const manifest = JSON.parse(readFileSync(FIXTURE, "utf8"));
manifest[WHEN] = manifest[WHEN] || {};
manifest[WHEN].render = manifest[WHEN].render || {};
manifest[WHEN].render["admin-solicitudes.header"] = header;
if (WHEN === "before" && !manifest.before.source) {
  manifest.before.source = {};
  for (const [name, rel, from, to] of SLICES) {
    const lines = readFileSync(join(ROOT, rel), "utf8").split("\n");
    manifest.before.source[name] = lines.slice(from - 1, to).join("\n");
  }
  manifest.before.frozenAt = "origin/main 3700e108";
}
writeFileSync(FIXTURE, JSON.stringify(manifest, null, 2) + "\n");
console.log("captured " + WHEN + ": admin-solicitudes.header ("
  + Object.keys(states).join(", ") + ")"
  + (manifest.before.source ? " + " + Object.keys(manifest.before.source).length + " source slices" : ""));
