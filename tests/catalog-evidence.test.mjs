import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogEvidenceComplete, requiredPhases } from './e2e/lib/catalog-evidence.mjs';

const stories = [['S08', 'servicios', 'hold']];
function fixture(action = 'hold') {
  const view = phase => ({ assets: ['.png', '.html', '.json'].map(ext => ({ path: phase + ext, bytes: 1 })),
    viewportAsset: { path: phase + '-viewport.png', bytes: 1 },
    geometry: { observerSupported: true, observerReady: true, shifts: [], cls: 0, overflow: 0, escaped: [] },
    identity: { status: 200, sourceHash: 'exact-source', urls: ['https://synthetic/module.hash.mjs'] } });
  return [{ id: 'S08', kind: 'servicios', action, assertionComplete: true, observerInitialized: true,
    errors: [], views: { 360: Object.fromEntries(requiredPhases(action).map(phase => [phase, view(phase)])) } }];
}
const complete = rows => catalogEvidenceComplete(rows, stories, [360], 'exact-source');
test('initialized zero-shift observation is valid, not assumed from an empty array alone', () => assert.equal(complete(fixture()), true));
for (const [name, mutate] of [
  ['wrong identity', rows => { rows[0].id = 'S99'; }],
  ['missing held phase', rows => { delete rows[0].views[360].held; }],
  ['reused phase asset', rows => { rows[0].views[360].hold.assets[0] = rows[0].views[360].held.assets[0]; }],
  ['missing readable viewport', rows => { delete rows[0].views[360].hold.viewportAsset; }],
  ['empty readable viewport', rows => { rows[0].views[360].hold.viewportAsset.bytes = 0; }],
  ['uninitialized observer', rows => { rows[0].views[360].hold.geometry.observerReady = false; }],
  ['missing CLS', rows => { delete rows[0].views[360].hold.geometry.cls; }],
  ['stale source', rows => { rows[0].views[360].hold.identity.sourceHash = 'stale'; }],
  ['unexpected error', rows => { rows[0].errors.push('unexpected'); }],
  ['failed action', rows => { rows[0].failure = { message: 'failed' }; }],
  ['duplicate asset', rows => { rows[0].views[360].hold.assets[1] = rows[0].views[360].hold.assets[0]; }],
  ['zero-byte screenshot', rows => { rows[0].views[360].hold.assets[0].bytes = 0; }],
  ['hidden clipping', rows => { rows[0].views[360].hold.geometry.escaped.push({ left: -10 }); }],
]) test(`report rejects ${name}`, () => { const rows = fixture(); mutate(rows); assert.equal(complete(rows), false); });

for (const action of ['nomatch', 'filter', 'tabs']) test(`report requires every ${action} action phase`, () => {
  const rows = fixture(action), expected = [['S08', 'servicios', action]];
  assert.equal(catalogEvidenceComplete(rows, expected, [360], 'exact-source'), true);
  for (const phase of requiredPhases(action)) {
    const missing = structuredClone(rows); delete missing[0].views[360][phase];
    assert.equal(catalogEvidenceComplete(missing, expected, [360], 'exact-source'), false);
  }
});
