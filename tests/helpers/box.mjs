// box.mjs - a bounding box that has stopped moving; the one function both runners import
// directly (the node:test files and the tests/e2e specs).
//
//   import { stableBox } from "./helpers/box.mjs";
//   const box = await stableBox(page.locator("#iv-form"), "the composer");
//
// `boundingBox()` is null right after a click or a `goto` while the element is still being
// drawn, and mid-layout it can hold a transient size, so a read straight after a visibility
// wait is a race. Poll until two reads in a row are the same non-null box (a condition, not a
// sleep) and return it. It needs no Playwright Test `expect`, only a locator.
import { until } from "./wait.mjs";

export async function stableBox(locator, what, { timeout = 15000, interval = 50 } = {}) {
  let previous = null;
  return until(async () => {
    const box = await locator.boundingBox();
    const same = box !== null && previous !== null && ["x", "y", "width", "height"].every((k) => box[k] === previous[k]);
    previous = box;
    return same ? box : null;
  }, `${what}: a stable box`, { timeout, interval });
}
