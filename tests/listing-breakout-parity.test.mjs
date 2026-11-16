/* listing-breakout-parity.test.mjs — a catalog listing stays in the content
   column on desktop (review 2026-11-15, D19/P26). The 2026-07-23 breakout
   stretched the table (and the footer under it) to min(1480px, 100vw - 48px),
   so at 1440px the rows began left of the page title. It lived twice, in
   listing.mjs's injected style and in the shell's inline CSS; neither copy may
   come back, or the block would jump sideways when the other one arrives.

   Run:  node --test tests/listing-breakout-parity.test.mjs */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const listing = readFileSync(new URL("../static/platform/listing.mjs", import.meta.url), "utf8");
const shell = readFileSync(new URL("../app/patient_web/site_shell.py", import.meta.url), "utf8");

for (const [name, source] of [["listing.mjs", listing], ["site_shell.py", shell]]) {
  test(`${name}: no full-viewport breakout of the listing or its footer`, () => {
    assert.doesNotMatch(source, /\[data-listado\][^{]*\{[^}]*width:min\(1480px/);
    assert.doesNotMatch(source, />footer\.site\{[^}]*translateX\(-50%\)/);
    assert.doesNotMatch(source, /\[data-listado\][^{]*\{[^}]*margin-left:50%/);
  });
}
