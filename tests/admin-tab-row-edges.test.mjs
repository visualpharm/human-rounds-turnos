/* admin-tab-row-edges.test.mjs — a tab row wider than the screen (a doctor's
   card on a phone: five tabs, ~600px in 358) keeps one row and scrolls, but
   never shows a cut tab as if it were whole (ui-expert 2026-11-15,
   «Informaci»): the row snaps to whole tabs and the side that has more fades.
   The browser half (data-more set on a real 390 screen) is checked live.

   Run:  node --test tests/admin-tab-row-edges.test.mjs */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const SRC = readFileSync(new URL("../static/platform/admin-content.mjs", import.meta.url), "utf8");

test("the tab row snaps to whole tabs", () => {
  assert.match(SRC, /\.cnt-tabs\{[^}]*scroll-snap-type:x mandatory/);
  assert.match(SRC, /\.cnt-tab\{scroll-snap-align:start;/);
});

test("the side with more tabs fades out, on both sides and on each alone", () => {
  for (const sel of [/\.cnt-tabs\[data-more~=end\]\{[^}]*mask-image:linear-gradient\(to right/,
    /\.cnt-tabs\[data-more~=start\]\{[^}]*mask-image:linear-gradient\(to left/,
    /\.cnt-tabs\[data-more~=start\]\[data-more~=end\]\{[^}]*mask-image/]) assert.match(SRC, sel);
});

test("the doctor's card wires the edges and brings a chosen tab in whole", () => {
  assert.match(SRC, /if \(tabs\.length\) wireTabEdges\(tabs\[0\]\.parentNode\);/);
  assert.match(SRC, /tab\.scrollIntoView\(\{ block: "nearest", inline: "nearest" \}\)/);
  const fn = SRC.slice(SRC.indexOf("function wireTabEdges"), SRC.indexOf("function wireMedicoTabs"));
  assert.match(fn, /listen\(row, "scroll", update\)/);
  assert.match(fn, /ResizeObserver/);
  assert.match(fn, /ownRouteCleanup\(function \(\) \{ ro\.disconnect\(\); \}\)/);
});
