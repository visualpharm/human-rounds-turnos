// scripts/smoke_listing_layout.mjs judges one appointment cell: «Más horarios»
// under the button with its text inside its frame, button and date on one line.
import test from "node:test";
import assert from "node:assert/strict";
import { judge, WIDTHS } from "../scripts/smoke_listing_layout.mjs";

const box = (top, bottom, left, right) => ({ top, bottom, left, right });

test("a cell laid out right passes", () => {
  assert.deepEqual(judge({
    time: box(100, 144, 72, 202), timeLines: 1,
    more: box(152, 196, 72, 197), moreScroll: 123, moreClient: 123,
    notes: [{ lines: 1, text: "Mañana, 08:00" }],
  }), []);
});

test("the review's broken phone frame fails on all three counts", () => {
  // demo /center/sampleton-hospital at 390 as ui-expert captured it: «Más
  // horarios» beside the button, its text past its outline, the date on two lines.
  const bad = judge({
    time: box(354, 398, 72, 196), timeLines: 1,
    more: box(354, 398, 283, 373), moreScroll: 123, moreClient: 88,
    notes: [{ lines: 2, text: "Mañana, 08:00" }],
  });
  assert.equal(bad.length, 3);
  assert.match(bad.join(" "), /fecha/);
  assert.match(bad.join(" "), /se sale/);
  assert.match(bad.join(" "), /al lado/);
});

test("a button that breaks its own label fails", () => {
  assert.equal(judge({ time: box(0, 80, 0, 100), timeLines: 2, more: null, notes: [] }).length, 1);
});

test("it measures the phone and the wide screen", () => {
  assert.deepEqual(WIDTHS, [390, 1440]);
});
