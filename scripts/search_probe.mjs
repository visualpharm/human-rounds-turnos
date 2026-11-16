/* scripts/search_probe.mjs — how many results does a search term actually get?

   The home page's search is static/platform/search.mjs running in the
   browser, over the payloads of /api/services and /api/doctors, with the
   field closures index.html builds. Anything that wants to know "does this
   term return results?" — the example chips under the searchbox, a test that
   guards them — has to ask THAT matcher, not a reimplementation of it: the
   stemming and the relevance tiers are the whole point, and a second copy
   would drift (CLAUDE.md: one engine, one place).

   This probe imports the real canonical module and mirrors, byte for byte,
   the two `getCampos` closures of index.html:renderResultados().

   Usage (JSON in on stdin, JSON out on stdout):

     echo '{"services":[…],"doctors":[…],"centers":{…},"terms":["Cardiología"]}' \
       | node scripts/search_probe.mjs

   Out: {"Cardiología": {"services": 7, "doctors": 4, "total": 11}, …}

   Consumers: scripts/search_examples.py (picks the chips) and
   tests/search-examples.test.mjs (fails the build when a chip stops
   returning results). */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import * as Search from "../static/platform/search.mjs";

/** Result counts for one term — the same two searches the home page runs.
    `lang` is optional ("es"/"en"): omitted, Search.buscar autodetects
    from document/window (both absent under Node, so it degrades to Spanish —
    the behavior every existing caller of probe() already relies on). Pass it
    explicitly to prove the English morphology layer (unit 2, 2026-08-04)
    against an English-ish corpus, since Node has no <html lang> to read. */
export function probe(term, services, doctors, centers, lang) {
  const svs = Search.buscar(services, term, function (s) {
    const c = centers[s.centro] || {};
    return {
      principal: [s.nombre],
      resto: [s.categoria, c.name, s.sede, s.horario].concat(s.palabras || []),
      excluidas: s.palabras_excluidas || [],
    };
  }, lang);
  const mds = Search.buscar(doctors, term, function (m) {
    return {
      principal: [m.nombre],
      resto: [m.especialidad, m.centro].concat(m.palabras || []),
      excluidas: m.palabras_excluidas || [],
    };
  }, lang);
  // `keys` names WHICH rows matched, so a caller can tell two terms that are
  // synonyms (the same rows) from two that teach different things.
  const keys = svs.map(function (s) { return "s:" + (s.id || s.nombre); })
    .concat(mds.map(function (m) { return "d:" + (m.id || m.nombre); }));
  return { services: svs.length, doctors: mds.length, total: svs.length + mds.length, keys: keys };
}

if (import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const input = JSON.parse(fs.readFileSync(0, "utf8"));
  const centers = input.centers || {};
  const out = {};
  (input.terms || []).forEach(function (t) {
    out[t] = probe(t, input.services || [], input.doctors || [], centers, input.lang);
  });
  process.stdout.write(JSON.stringify(out));
}
