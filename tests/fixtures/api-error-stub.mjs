// The i18n.mjs apiError export as module source, for tests that stand a
// data: URL in for platform/i18n.mjs (TASK-48). The real function is
// exported next to the stub's own t(); what a staff member reads for a
// failed call is then the shipped translation, never a copy of it.
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../static/platform/i18n.mjs", import.meta.url), "utf8");
const table = src.match(/var API_ERRORS = \{[\s\S]*?\n\};/);
const fn = src.match(/function apiError\(raw, fallback\) \{[\s\S]*?\n\}/);
if (!table || !fn) throw new Error("api-error-stub: apiError not found in i18n.mjs");

// Appended to a stub that already exports t(key, vars, fallback).
export const API_ERROR_STUB = "\n" + table[0] + "\n" + fn[0] + "\nexport { apiError };\n";
