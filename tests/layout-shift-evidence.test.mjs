import test from 'node:test';
import assert from 'node:assert/strict';
import { cumulativeLayoutShift } from './e2e/lib/layout-shift-evidence.mjs';

test('delivered shifts cannot disappear when pending observer queue is empty', () => {
  const delivered = [{ value: 0.12, startTime: 100 }];
  assert.ok(cumulativeLayoutShift([...delivered, ...[]]) > 0.1);
  assert.equal(cumulativeLayoutShift([]), 0); // The rejected clearing mutation.
});
test('CLS uses largest session window and excludes input-associated shifts', () => {
  assert.equal(cumulativeLayoutShift([
    { value: 0.03, startTime: 500 }, { value: 0.02, startTime: 0 },
    { value: 0.9, startTime: 600, hadRecentInput: true },
    { value: 0.04, startTime: 2000 },
  ]), 0.05);
});
test('malformed measurements fail instead of producing a reassuring score', () => {
  assert.throws(() => cumulativeLayoutShift([{ value: NaN, startTime: 0 }]));
  assert.throws(() => cumulativeLayoutShift([{ value: -1, startTime: 0 }]));
});
// GoogleChrome/web-vitals src/lib/LayoutShiftManager.ts uses <1000 and
// <5000 to continue a session. Equality starts a NEW session, not the old one.
test('CLS boundary equality starts a new session, just below it continues', () => {
  assert.equal(cumulativeLayoutShift([{value:0.06,startTime:0},{value:0.06,startTime:1000}]),0.06);
  assert.equal(cumulativeLayoutShift([{value:0.06,startTime:0},{value:0.06,startTime:999}]),0.12);
  const entries = Array.from({length:10}, (_,i) => ({value:0.01,startTime:i*500}));
  assert.ok(cumulativeLayoutShift([...entries,{value:0.06,startTime:5000}]) < 0.101);
  assert.ok(cumulativeLayoutShift([...entries,{value:0.06,startTime:4999}]) > 0.15);
});
test('zero-valued entries do not establish a positive-shift session window', () => {
  const entries = Array.from({length:10}, (_,i) => ({value:0,startTime:i*500}));
  assert.equal(cumulativeLayoutShift([...entries,{value:0.06,startTime:4999},{value:0.06,startTime:5500}]),0.12);
});
