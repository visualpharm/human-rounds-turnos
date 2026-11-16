// Guard for the home page's example search chips ("Probá: Pediatría,
// Ecografía…"). They are picked by scripts/search_examples.py against the
// pack's own catalog, but nothing stops a later commit from renaming the
// service or retiring the doctor a chip depends on — and a chip that lands on
// "no encontramos nada" teaches the opposite of what the row exists for.
//
// So: for every pack that ships a `search_examples` list, every term must
// still return at least one result through the REAL matcher (platform/search.mjs, via
// scripts/search_probe.mjs — never a reimplementation).
//
// Reads the pack's own JSON rather than the enriched /api payloads: the fields
// the search reads (nombre, categoria, palabras, sede, horario, especialidad,
// the center's name) all come straight from the pack, and enrichment only adds
// scheduling data the matcher never looks at.
//
// Per-language chips (2026-08-04): `scripts/search_examples.py` also writes
// `search_examples_<lang>` for every OTHER enabled language, generated from
// and verified against THAT language's own (translated) corpus — but that
// corpus lives in Postgres (pack_l10n.py's translation cache), not in this
// pack's plain JSON files, so THIS file can only check the per-language
// lists are well-formed (non-empty strings, no duplicate), not that they
// still find results — that guarantee lives in
// tests/test_search_examples_i18n.py, which has DB access. Don't add a probe()
// call against a language list here: it would silently probe English terms
// against the Spanish-only corpus these JSON files carry and fail for the
// wrong reason.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { probe } from '../scripts/search_probe.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const readJson = (p) => JSON.parse(readFileSync(p, 'utf8'));

/** Every pack that ships both a sitio.json and a catalog of its own. */
function packsWithExamples() {
  return readdirSync(join(ROOT, 'packs'), { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => join(ROOT, 'packs', d.name))
    .filter((dir) => ['sitio.json', 'servicios.json', 'medicos.json',
      'centros-salud.json'].every((f) => existsSync(join(dir, f))))
    .map((dir) => ({ dir, sitio: readJson(join(dir, 'sitio.json')) }))
    .filter(({ sitio }) => Array.isArray(sitio.search_examples)
      && sitio.search_examples.length > 0);
}

const packs = packsWithExamples();

test('at least one pack ships search_examples', () => {
  assert.ok(packs.length > 0,
    'run scripts/search_examples.py --pack <id> to generate them');
});

for (const { dir, sitio } of packs) {
  const name = dir.split('/').pop();
  const services = readJson(join(dir, 'servicios.json')).servicios || [];
  const doctors = readJson(join(dir, 'medicos.json')).medicos || [];
  const centers = {};
  for (const c of readJson(join(dir, 'centros-salud.json')).centros || [])
    centers[c.slug] = c;

  test(`${name}: every search example returns results`, () => {
    for (const term of sitio.search_examples) {
      const { total } = probe(term, services, doctors, centers);
      assert.ok(total > 0,
        `"${term}" returns nothing in ${name} — re-run `
        + `scripts/search_examples.py --pack ${name}`);
    }
  });

  test(`${name}: the examples are distinct`, () => {
    const seen = new Set(sitio.search_examples.map((t) => t.toLowerCase()));
    assert.equal(seen.size, sitio.search_examples.length);
  });

  // sitio.json keys shaped `search_examples_<lang>` — one per OTHER enabled
  // language scripts/search_examples.py generated a verified set for.
  const langKeys = Object.keys(sitio).filter((k) => /^search_examples_[a-z]+$/.test(k));
  for (const key of langKeys) {
    const lang = key.slice('search_examples_'.length);
    const terms = sitio[key];

    test(`${name}: ${key} is a well-formed, non-empty list`, () => {
      assert.ok(Array.isArray(terms) && terms.length > 0,
        `${key} should be a non-empty array, or absent — never []`);
      for (const term of terms)
        assert.ok(typeof term === 'string' && term.trim(),
          `${key} has a blank/non-string entry`);
    });

    test(`${name}: ${key} entries are distinct`, () => {
      const seen = new Set(terms.map((t) => t.toLowerCase()));
      assert.equal(seen.size, terms.length, `duplicate chip in ${key}`);
    });

    test(`${name}: ${key} is not just the Spanish list under a new key`, () => {
      // catching the exact bug this slice exists to prevent: a language
      // list that's byte-for-byte the canonical one wasn't generated from
      // that language's own corpus, it's the old single-language behavior
      // silently relabeled.
      assert.notDeepEqual(terms, sitio.search_examples,
        `${key} is identical to search_examples — looks unverified for ${lang}`);
    });
  }
}
