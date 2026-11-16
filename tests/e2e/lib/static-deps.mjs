// tests/e2e/lib/static-deps.mjs -- the platform modules a controller really loads.
//
// A synthetic spec (its own HTML, its own window.SANA_STATIC, or a whitelist of served
// paths) used to copy the controller's dependencies by hand. A controller that gained an
// import -- platform/admin-filter-controls.mjs, 2026-10-26 -- then failed closed in every
// such spec at once («[static] platform/… is not in window.SANA_STATIC») while the real
// page, whose map is core/static_fingerprint.py, worked. platformDeps() reads the import
// graph from the source instead, so the spec's map follows the controller.
//
// What counts as a dependency: every "platform/<name>.mjs" literal (importStatic /
// sanaImportStatic / importDep / window.SANA_STATIC[...]) and every relative "./x.mjs"
// import or new URL("./x.mjs", import.meta.url) that resolves under static/, followed
// transitively. A mention that is not a real file is dropped; an extra map entry for a
// module the page never asks for is harmless.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";

const NAMED = /["'`](platform\/[a-z0-9-]+\.mjs)["'`]/g;
const RELATIVE = /(?:\bfrom\s*|\bimport\s*\(\s*|\bnew URL\(\s*)["'](\.{1,2}\/[^"'\s]+\.mjs)["']/g;

/**
 * The sorted "platform/<name>.mjs" keys reachable from `entries` (repo-relative source
 * paths, e.g. "static/platform/admin-content.mjs", "app/modules/intake/admin-entrevistas.mjs").
 * An entry under static/ is itself part of the result.
 */
export function platformDeps(root, entries) {
  const staticDir = resolve(root, "static");
  const keyOf = (file) => {
    const rel = relative(staticDir, file);
    return rel.startsWith("..") ? null : rel.split(sep).join("/");
  };
  const seen = new Set(), keys = new Set();
  const queue = entries.map((entry) => resolve(root, entry));
  // an entry that is not there would give an empty list and a fixture that serves nothing
  for (const file of queue) if (!existsSync(file)) throw new Error("platformDeps: no such entry " + file);
  while (queue.length) {
    const file = queue.pop();
    if (seen.has(file) || !existsSync(file)) continue;
    seen.add(file);
    const key = keyOf(file);
    if (key) keys.add(key);
    const source = readFileSync(file, "utf8");
    for (const [, name] of source.matchAll(NAMED)) queue.push(join(staticDir, name));
    for (const [, rel] of source.matchAll(RELATIVE)) queue.push(resolve(dirname(file), rel));
  }
  return [...keys].sort();
}
