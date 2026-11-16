// A page served in the canonical language ships NO catalog (i18n.py's
// `_client_catalog`): the interface strings it needs are the ones the JS
// already had, so re-sending them in the HTML of every page would be pure
// weight. What makes that safe is the third argument of
// `SanaI18n.t(key, vars, fallback)` — the literal the call site replaced.
//
// Miss it on one call and that string renders as a raw dotted key
// ("listing.more_times") on every Spanish page. Nothing else would catch it:
// the developer's own browser, running the language they wrote the catalog
// in, is exactly where the bug does NOT reproduce.
//
// So: every T(...) call in the patient site's JS must pass a fallback, and
// every key it names must exist in the canonical catalog.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

// Every file that calls the shim, found by looking. A hardcoded list would
// mean that translating a file and remembering to register it are two
// separate steps, and the second one is the one that gets skipped — the file
// then renders untested, which is exactly the state this test exists to
// prevent. It also made this line the merge conflict of any parallel
// extraction work.
const CALLS_SHIM = /\bT\(\s*["'][a-z0-9_.]+["']\s*,/;
const platformDir = join(ROOT, 'static', 'platform');
const FILES = [
  ...readdirSync(ROOT)
    .filter((f) => (f.endsWith('.js') || f.endsWith('.mjs') || f.endsWith('.html')) && statSync(join(ROOT, f)).isFile())
    .filter((f) => CALLS_SHIM.test(readFileSync(join(ROOT, f), 'utf8'))),
  ...(existsSync(platformDir) ? readdirSync(platformDir)
    .filter((f) => f.endsWith('.mjs'))
    .map((f) => join('static', 'platform', f))
    .filter((f) => CALLS_SHIM.test(readFileSync(join(ROOT, f), 'utf8'))) : []),
].sort();

// A module can own its own catalog dir (docs/modules.md's `locales`
// manifest field — i18n.py's app_modules.module_locale_roots() merges it
// into the real runtime catalog). This file re-derives the same list
// straight from each modules/<id>/module.json rather than importing Python,
// so a JS-only test suite stays JS-only; keep this in sync with
// app_modules.module_locale_roots()'s own selection rule (any manifest that
// declares a truthy `locales` field, parse errors skipped same as Python's
// discovery does).
function moduleLocaleDirs() {
  const modulesDir = join(ROOT, 'app', 'modules');
  if (!existsSync(modulesDir)) return [];
  const out = [];
  for (const id of readdirSync(modulesDir)) {
    const manifestPath = join(modulesDir, id, 'module.json');
    if (!existsSync(manifestPath)) continue;
    try {
      const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
      // a manifest names its catalog inside the code package, like app_modules does
      if (manifest.locales) out.push(join(ROOT, 'app', manifest.locales));
    } catch { /* a broken manifest contributes nothing, same as app_modules.py */ }
  }
  return out;
}

/** Every namespace-file directory for `lang`: the central locales/<lang>/
 * tree plus every module's own locales/<lang>/ dir, in that order — the
 * exact merge i18n.py's _catalog_files() performs at runtime. */
function catalogDirs(lang) {
  const dirs = [join(ROOT, 'locales', lang)];
  for (const root of moduleLocaleDirs()) {
    const d = join(root, lang);
    if (existsSync(d)) dirs.push(d);
  }
  return dirs.filter((d) => existsSync(d));
}

/** The canonical catalog: locales/es/<namespace>.json + every module's
 * locales/es/<namespace>.json, merged. */
function canonicalCatalog() {
  const out = {};
  for (const dir of catalogDirs('es')) {
    for (const fname of readdirSync(dir).filter((f) => f.endsWith('.json'))) {
      const rows = JSON.parse(readFileSync(join(dir, fname), 'utf8'));
      for (const [k, v] of Object.entries(rows)) if (k !== '_comment') out[k] = v;
    }
  }
  return out;
}

const CANONICAL = canonicalCatalog();

/** Every `T("key", <vars>, ...)` occurrence, with what followed the vars. */
function calls(src) {
  const out = [];
  const re = /\bT\(\s*"([a-z0-9_.]+)"\s*,/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    // walk forward from the key to the call's closing paren, tracking depth,
    // so a fallback containing commas or parens still reads as one argument
    let i = re.lastIndex, depth = 1, args = '';
    for (; i < src.length && depth > 0; i++) {
      const c = src[i];
      if (c === '(') depth++;
      else if (c === ')') { depth--; if (!depth) break; }
      args += c;
    }
    out.push({ key: m[1], rest: args });
  }
  return out;
}

for (const file of FILES) {
  const path = join(ROOT, file);
  if (!existsSync(path)) continue;
  const src = readFileSync(path, 'utf8');
  const found = calls(src);

  test(`${file}: every translated string keeps its canonical fallback`, () => {
    for (const { key, rest } of found) {
      // rest is "<vars>, <fallback>" — a second comma at depth zero
      const hasFallback = /,\s*["']/.test(rest);
      assert.ok(hasFallback,
        `T("${key}", …) in ${file} has no fallback literal — a Spanish page `
        + 'would render the raw key');
    }
  });

  test(`${file}: every key it uses exists in the canonical catalog`, () => {
    for (const { key } of found) {
      const ns = key.split('.')[0];
      assert.ok(Object.prototype.hasOwnProperty.call(CANONICAL, key),
        `${file} uses "${key}", which is not in locales/es/${ns}.json`);
    }
  });
}

// The one resolver every module declares (documented in i18n.js). Repeated
// per file because each module is an IIFE the node suites require() without
// a window; identical everywhere so the fallback rule has one definition.
const RESOLVER = 'function T(key, vars, fallback) { return I18n ? I18n.t(key, vars, fallback) : (fallback || key); }';

/** The whole `function T(...) {...}` declaration, whitespace-normalised. */
function resolverIn(src) {
  const start = src.indexOf('function T(');
  if (start < 0) return null;
  let depth = 0, i = src.indexOf('{', start);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && !--depth) break;
  }
  return src.slice(start, i + 1).replace(/\s+/g, ' ').trim();
}

test('every module declares the SAME T() resolver', () => {
  const want = RESOLVER.replace(/\s+/g, ' ');
  for (const file of FILES.filter((f) => f.endsWith('.js'))) {
    const found = resolverIn(readFileSync(join(ROOT, file), 'utf8'));
    if (found === null) continue;  // reads window.SanaI18n directly, fine
    assert.equal(found, want,
      `${file} declares its own variant of T() — copy the canonical one from platform/i18n.mjs`);
  }
});

test('the shim is discovered, not registered by hand', () => {
  // If the discovery above ever silently matches nothing, every assertion in
  // this file passes vacuously and the whole gate is off.
  assert.ok(FILES.length >= 3, `only ${FILES.length} files found calling T(…)`);
});

test('every catalog key lives under the namespace its file names', () => {
  // The file name is a dotted PREFIX of every key inside it, so
  // `listing.col.proximo` may live in listing.json or in listing.col.json —
  // whichever the surface that owns it split off. Otherwise a key is
  // unfindable, and two people working on the same page collide on one file
  // just because both their strings start with `listing`.
  for (const lang of ['es', 'en']) {
    for (const dir of catalogDirs(lang)) {
      for (const fname of readdirSync(dir).filter((f) => f.endsWith('.json'))) {
        const ns = fname.slice(0, -5);
        for (const key of Object.keys(JSON.parse(readFileSync(join(dir, fname), 'utf8')))) {
          if (key === '_comment') continue;
          assert.ok(key === ns || key.startsWith(ns + '.'),
            `${dir}/${fname} holds "${key}", which is not under "${ns}."`);
        }
      }
    }
  }
});

test('no translation key is defined twice across namespace files', () => {
  for (const lang of ['es', 'en']) {
    const seen = new Map();
    for (const dir of catalogDirs(lang)) {
      for (const fname of readdirSync(dir).filter((f) => f.endsWith('.json'))) {
        for (const key of Object.keys(JSON.parse(readFileSync(join(dir, fname), 'utf8')))) {
          if (key === '_comment') continue;
          assert.ok(!seen.has(key),
            `"${key}" is in both ${lang}/${seen.get(key)} and ${lang}/${dir}/${fname}`);
          seen.set(key, `${dir}/${fname}`);
        }
      }
    }
  }
});

test('the canonical catalog is never shipped to a Spanish page; only runtime overrides may be', () => {
  // The Python side decides this. Canonical pages stay fallback-driven, but
  // an admin-authored chat greeting is deliberately allowed through as the
  // tiny `runtime` overlay so it can differ from the checked-in fallback. The
  // optional deployment-prefix filter may transform that overlay, but the
  // canonical branch must still return before either disk catalog is loaded.
  const py = readFileSync(join(ROOT, 'app', 'core', 'i18n.py'), 'utf8');
  const runtime = py.indexOf('runtime = _runtime_client_overrides(lang, namespaces)');
  const canonical = py.indexOf('if lang == CANONICAL_LANG:', runtime);
  const catalogLoad = py.indexOf('if namespaces is None:', canonical);
  assert.ok(runtime >= 0 && canonical > runtime && catalogLoad > canonical);
  const canonicalBranch = py.slice(canonical, catalogLoad);
  assert.match(canonicalBranch, /return /);
  assert.doesNotMatch(canonicalBranch, /_load_catalog\(/);
});
